"""
=============================================================================
SOCIAL MEDIA COMMENT GENERATOR
=============================================================================
Script sinh bình luận giả lập phong cách mạng xã hội bằng AI API.

Hỗ trợ: OpenAI API & Anthropic Claude API
Output:  CSV + JSON với các trường: id, content, length_category, tone, style

Cách chạy:
    python generate_comments.py                     # Dùng config.json
    python generate_comments.py --topic "Chủ đề"    # Override topic
    python generate_comments.py --count 500          # Override số lượng
    python generate_comments.py --interactive        # Nhập từ bàn phím

Yêu cầu:
    pip install -r requirements.txt
    Đặt biến môi trường OPENAI_API_KEY hoặc ANTHROPIC_API_KEY
=============================================================================
"""

import os
import sys
import json
import csv
import time
import uuid
import hashlib
import argparse
import logging
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from difflib import SequenceMatcher
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ============================================================================
# LOGGING SETUP
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================================
# CẤU HÌNH MẶC ĐỊNH
# ============================================================================
DEFAULT_CONFIG = {
    "topic": "",
    "num_comments": 200,
    "language": "Tiếng Việt",
    "batch_size": 5,                # Số comment mỗi lần gọi API
    "word_count": 10,               # Số từ mong muốn trong mỗi comment
    "similarity_threshold": 0.75,   # Ngưỡng loại bỏ trùng lặp (0-1)
    "output_format": "both",        # "csv", "json", hoặc "both"
    "api_provider": "openai",       # "openai" hoặc "anthropic"
    "api_model": "gpt-4o-mini",     # Model cụ thể
    "max_retries": 5,               # Số lần retry khi lỗi API
    "retry_delay_base": 2,          # Base delay (giây) cho exponential backoff
}


# ============================================================================
# PROMPT TEMPLATE
# ============================================================================
SYSTEM_PROMPT = """Bạn là một engine sinh bình luận mạng xã hội cực kỳ tự nhiên.

QUY TẮC TUYỆT ĐỐI - KHÔNG ĐƯỢC VI PHẠM:
    - Mỗi trường content PHẢI CÓ ĐÚNG 10 TỪ TIẾNG VIỆT. Không hơn, không kém.
    - Chỉ đếm các từ nằm bên trong content; không đếm content, tone, style, JSON hay giải thích.
    - Emoji và dấu câu KHÔNG tính là từ nhưng cũng KHÔNG được dùng quá 2 emoji mỗi comment.
- Mỗi comment PHẢI khác biệt về cấu trúc câu, cách mở đầu, và nội dung
- KHÔNG bao giờ bắt đầu 2 comment liên tiếp bằng cùng một từ/cụm từ
- TUYỆT ĐỐI KHÔNG sinh nội dung thù ghét, phân biệt, thông tin sai lệch nguy hiểm
- Giữ nội dung an toàn nhưng vẫn tự nhiên, có quan điểm rõ ràng

VÍ DỤ COMMENT ĐÚNG 10 TỪ:
- "Xinh quá trời ơi nhìn hoài không chán luôn á" (10 từ ✓)
- "Ai cho phép đẹp vậy trời ơi cứu tui với" (10 từ ✓)
- "Lướt feed gặp em là quên hết mọi thứ luôn" (10 từ ✓)

PHONG CÁCH ĐA DẠNG (trộn đều trong batch):
- Khen ngợi nhiệt tình, reaction nhanh
- Hài hước, châm biếm nhẹ
- Teencode nhẹ (ko, dc, j, r...)
- Dùng emoji tự nhiên 😂🤔👍🔥
- Viết hoa NHẤN MẠNH 1-2 từ
- Câu hỏi tu từ, thắc mắc vui

FORMAT OUTPUT:
Trả về ĐÚNG một JSON object, không markdown, không giải thích:
{
    "comments": [
    {"content": "nội dung comment đúng 10 từ tiếng Việt", "tone": "giọng_điệu", "style": "văn_phong"},
  ...
    ]
}

Các giá trị tone: agreeing, disagreeing, neutral, humorous, sarcastic, questioning, storytelling, informative
Các giá trị style: formal, casual, teencode, emoji_heavy, emphatic, minimal

NHẮC LẠI: CHỈ TRƯỜNG content PHẢI CÓ CHÍNH XÁC 10 TỪ TIẾNG VIỆT. ĐẾM KỸ TRƯỚC KHI TRẢ VỀ.
"""


def build_user_prompt(topic: str, count: int, language: str, existing_starts: list[str], word_count: int) -> str:
    """Xây dựng prompt cho mỗi batch, bao gồm danh sách từ mở đầu cần tránh."""
    avoid_section = ""
    language_instruction = ""
    if language.lower() == "genz":
        language_instruction = """
YÊU CẦU NGÔN NGỮ GENZ:
- Mỗi comment phải ưu tiên dùng tiếng Việt tự nhiên và dễ hiểu.
- Có thể dùng teencode, từ lóng và cách nói mạng xã hội vừa phải như "slay", "flex", "xịn", "đỉnh", "cháy".
- Không viết cả câu bằng tiếng Anh; từ mượn chỉ dùng khi phù hợp ngữ cảnh.
- Giữ đúng ý chính của chủ đề, không lạm dụng emoji hoặc teencode làm câu khó đọc.
"""
    if existing_starts:
        # Lấy tối đa 30 từ mở đầu gần nhất để tránh lặp
        recent = existing_starts[-30:]
        avoid_section = f"""
TRÁNH MỞ ĐẦU BẰNG CÁC TỪ/CỤM SAU (đã dùng rồi):
{', '.join(f'"{s}"' for s in recent)}
"""

    return f"""Chủ đề: "{topic}"
Ngôn ngữ: {language}
Số lượng: {count} comment
Số từ tiếng Việt trong trường content: chính xác {word_count} từ (bắt buộc)
{language_instruction}
{avoid_section}
Hãy sinh ĐÚNG {count} comment đa dạng. Chỉ đếm từ trong content, không đếm metadata. Trả về JSON object có khóa comments, không thêm text nào khác."""


# ============================================================================
# API CLIENTS
# ============================================================================

class APIClient:
    """Base class cho các API client."""

    def __init__(self, model: str, max_retries: int, retry_delay_base: float):
        self.model = model
        self.max_retries = max_retries
        self.retry_delay_base = retry_delay_base

    def call(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError

    def _retry_with_backoff(self, func, *args, **kwargs):
        """Gọi func với exponential backoff khi gặp lỗi."""
        for attempt in range(1, self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_str = str(e).lower()
                # Phân loại lỗi
                is_rate_limit = any(kw in error_str for kw in ["rate_limit", "rate limit", "429", "too many"])
                is_timeout = any(kw in error_str for kw in ["timeout", "timed out", "connection"])
                is_server = any(kw in error_str for kw in ["500", "502", "503", "server"])

                if is_rate_limit or is_timeout or is_server:
                    delay = self.retry_delay_base * (2 ** (attempt - 1))
                    if is_rate_limit:
                        delay = max(delay, 10)  # Rate limit cần chờ lâu hơn
                    logger.warning(
                        f"  ⚠ Lỗi API (lần {attempt}/{self.max_retries}): {type(e).__name__}. "
                        f"Retry sau {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    # Lỗi không thể retry (ví dụ: auth, invalid request)
                    logger.error(f"  ✗ Lỗi không thể retry: {e}")
                    raise
        raise RuntimeError(f"Đã thử {self.max_retries} lần nhưng vẫn thất bại.")


class OpenAIClient(APIClient):
    """Client cho OpenAI API."""

    def __init__(self, model: str, max_retries: int, retry_delay_base: float):
        super().__init__(model, max_retries, retry_delay_base)
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Cần cài đặt: pip install openai")

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("Chưa đặt biến môi trường OPENAI_API_KEY")

        self.client = OpenAI(api_key=api_key)

    def call(self, system_prompt: str, user_prompt: str) -> str:
        def _do_call():
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=1.0,      # Tăng tính sáng tạo
                top_p=0.95,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content
        return self._retry_with_backoff(_do_call)


class AnthropicClient(APIClient):
    """Client cho Anthropic Claude API."""

    def __init__(self, model: str, max_retries: int, retry_delay_base: float):
        super().__init__(model, max_retries, retry_delay_base)
        try:
            import anthropic
        except ImportError:
            raise ImportError("Cần cài đặt: pip install anthropic")

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("Chưa đặt biến môi trường ANTHROPIC_API_KEY")

        self.client = anthropic.Anthropic(api_key=api_key)

    def call(self, system_prompt: str, user_prompt: str) -> str:
        def _do_call():
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                temperature=1.0,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.content[0].text
        return self._retry_with_backoff(_do_call)


class GroqClient(APIClient):
    """Client cho Groq API (miễn phí, inference cực nhanh)."""

    def __init__(self, model: str, max_retries: int, retry_delay_base: float):
        super().__init__(model, max_retries, retry_delay_base)
        try:
            from groq import Groq
        except ImportError:
            raise ImportError("Cần cài đặt: pip install groq")

        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "Chưa đặt biến môi trường GROQ_API_KEY.\n"
                "Lấy key miễn phí tại: https://console.groq.com/keys"
            )

        self.client = Groq(api_key=api_key)

    def call(self, system_prompt: str, user_prompt: str) -> str:
        def _do_call():
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            if self.model.startswith("qwen/"):
                messages[1]["content"] = "/no_think\n" + messages[1]["content"]
            request_options = {
                "model": self.model,
                "messages": messages,
                "temperature": 1.0,
                "top_p": 0.95,
                "max_tokens": 512,
            }
            if self.model.startswith("qwen/"):
                request_options["reasoning_effort"] = "none"
            elif self.model.startswith("openai/gpt-oss"):
                request_options["reasoning_effort"] = "low"
            try:
                response = self.client.chat.completions.create(**request_options)
            except Exception as error:
                error_text = str(error).lower()
                if "401" in error_text or "invalid api key" in error_text or "authentication" in error_text:
                    raise ValueError(
                        "GROQ_API_KEY không hợp lệ hoặc đã hết hạn. "
                        "Hãy cập nhật lại biến môi trường GROQ_API_KEY trên máy chủ rồi deploy lại."
                    ) from error
                elif "model" in error_text and ("not found" in error_text or "decommissioned" in error_text):
                    raise ValueError(
                        f"Model Groq '{self.model}' không còn khả dụng. "
                        "Hãy chọn một model đang hoạt động trong danh sách Provider."
                    ) from error
                else:
                    raise
            return response.choices[0].message.content
        return self._retry_with_backoff(_do_call)

    def validate(self) -> str:
        """Validate credentials and return an available text model."""
        try:
            models = self.client.models.list()
        except Exception as error:
            error_text = str(error).lower()
            if "401" in error_text or "invalid api key" in error_text or "authentication" in error_text:
                raise ValueError(
                    "GROQ_API_KEY không hợp lệ hoặc đã hết hạn. "
                    "Hãy cập nhật lại biến môi trường trên máy chủ rồi deploy lại."
                ) from error
            raise ValueError(f"Không thể kết nối Groq: {error}") from error

        available_models = [model.id for model in models.data]
        if self.model in available_models:
            return self.model

        preferred_models = [
            "qwen/qwen3.6-27b",
            "openai/gpt-oss-20b",
        ]
        for model in preferred_models:
            if model in available_models:
                return model

        excluded_prefixes = ("whisper", "distil-whisper", "playai-tts", "meta-llama/llama-guard")
        text_models = [model for model in available_models if not model.startswith(excluded_prefixes)]
        if text_models:
            return text_models[0]

        raise ValueError("Tài khoản Groq không có model sinh văn bản khả dụng.")


class GeminiClient(APIClient):
    """Gemini client using the public generateContent REST endpoint."""

    def __init__(self, model: str, max_retries: int, retry_delay_base: float,
                 api_key: str = None):
        super().__init__(model, max_retries, retry_delay_base)
        self.api_key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
        if not self.api_key:
            raise ValueError("Chưa đặt biến môi trường GEMINI_API_KEY")

    def call(self, system_prompt: str, user_prompt: str) -> str:
        def _do_call():
            payload = json.dumps({
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": 1.0,
                    "topP": 0.95,
                    "maxOutputTokens": 2048,
                    "responseMimeType": "application/json",
                },
            }).encode("utf-8")
            request = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Gemini HTTP {error.code}: {detail[:300]}") from error
            candidates = data.get("candidates", [])
            if not candidates:
                raise RuntimeError("Gemini không trả về candidate hợp lệ.")
            return "".join(part.get("text", "") for part in candidates[0].get("content", {}).get("parts", []))

        return self._retry_with_backoff(_do_call)


def get_gemini_api_keys() -> list[str]:
    """Read Gemini keys in deterministic order without logging their values."""
    keys = []
    keys.extend(value.strip() for value in os.environ.get("GEMINI_API_KEYS", "").split(","))
    single_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if single_key:
        keys.append(single_key)
    for index in range(1, 6):
        key = os.environ.get(f"GEMINI_API_KEY_{index}", "").strip()
        if key:
            keys.append(key)
    return list(dict.fromkeys(key for key in keys if key))


class FallbackClient(APIClient):
    """Try configured hosted providers in order, without exposing them to users."""

    def __init__(self, model: str, max_retries: int, retry_delay_base: float):
        super().__init__(model, max_retries, retry_delay_base)
        self.clients = []
        gemini_model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        candidates = [
            (f"gemini-{index}", gemini_model, GeminiClient, key)
            for index, key in enumerate(get_gemini_api_keys(), start=1)
        ]
        gemini_only = os.environ.get("GEMINI_ONLY", "true").strip().lower() == "true"
        if not gemini_only and os.environ.get("GROQ_API_KEY", "").strip():
            candidates.append(("groq", os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"), GroqClient, None))
        errors = []
        for name, provider_model, client_type, api_key in candidates:
            try:
                client = client_type(provider_model, max_retries, retry_delay_base, api_key) if api_key else client_type(provider_model, max_retries, retry_delay_base)
                self.clients.append((name, provider_model, client))
            except (ImportError, ValueError) as error:
                errors.append(f"{name}: {error}")
        if not self.clients:
            raise ValueError("Không có API sinh comment khả dụng. " + " | ".join(errors))

    def call(self, system_prompt: str, user_prompt: str) -> str:
        errors = []
        for name, model, client in self.clients:
            try:
                result = client.call(system_prompt, user_prompt)
                self.model = model
                return result
            except Exception as error:
                errors.append(f"{name}: {error}")
                self.clients.remove((name, model, client))
                logger.warning("Provider %s lỗi, chuyển provider tiếp theo: %s", name, error)
            raise RuntimeError("Tất cả Gemini API đều lỗi: " + " | ".join(errors))


class OllamaClient(APIClient):
    """Client cho Ollama (chạy local, không cần API key)."""

    def __init__(self, model: str, max_retries: int, retry_delay_base: float,
                 base_url: str = "http://localhost:11434"):
        super().__init__(model, max_retries, retry_delay_base)
        self.base_url = base_url.rstrip("/")
        # Kiểm tra Ollama có đang chạy không
        try:
            import urllib.request
            urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=5)
        except Exception:
            raise ConnectionError(
                f"Không kết nối được Ollama tại {self.base_url}.\n"
                "Hãy chắc chắn:\n"
                "  1. Đã cài Ollama: https://ollama.com/download\n"
                "  2. Ollama đang chạy: ollama serve\n"
                f"  3. Đã pull model: ollama pull {model}"
            )

    def call(self, system_prompt: str, user_prompt: str) -> str:
        import urllib.request
        import urllib.error

        def _do_call():
            payload = json.dumps({
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "options": {
                    "temperature": 1.0,
                    "top_p": 0.95,
                    "num_predict": 4096,
                },
                "format": "json",
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            # Timeout cao hơn vì local model có thể chậm
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["message"]["content"]

        return self._retry_with_backoff(_do_call)


def create_client(provider: str, model: str, max_retries: int, retry_delay_base: float) -> APIClient:
    """Factory function tạo API client phù hợp."""
    providers = {
        "auto": FallbackClient,
        "gemini": GeminiClient,
        "openai": OpenAIClient,
        "anthropic": AnthropicClient,
        "groq": GroqClient,
        "ollama": OllamaClient,
    }
    if provider not in providers:
        raise ValueError(f"Provider không hỗ trợ: {provider}. Chọn: {list(providers.keys())}")
    return providers[provider](model, max_retries, retry_delay_base)


# ============================================================================
# XỬ LÝ & LỌC TRÙNG LẶP
# ============================================================================

def count_content_words(content: str) -> int:
    """Count words in comment content, excluding punctuation and emoji."""
    return len(re.findall(r"[\wÀ-ỹ]+(?:['’-][\wÀ-ỹ]+)*", content, re.UNICODE))


def fit_content_word_count(content: str, target: int) -> str:
    """Adjust short model output so content has exactly the requested words."""
    words = content.split()
    current = count_content_words(content)
    if current < target:
        fillers = ["thật", "sự", "rất", "đáng", "xem", "lại", "nhiều", "lần"]
        missing = target - current
        suffix = " ".join(fillers[:missing])
        return f"{content.rstrip(' .!?')} {suffix}."
    if current > target:
        while words and count_content_words(" ".join(words)) > target:
            words.pop()
        return " ".join(words).rstrip(" ,;:") + "."
    return content


def parse_api_response(raw_text: str, target_word_count: int = 10) -> list[dict]:
    """Parse JSON response từ API, xử lý các trường hợp format khác nhau."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return []
    text = raw_text.strip()

    # Qwen đôi khi trả phần suy luận dù đã yêu cầu JSON.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1].strip()
    elif "<think>" in text:
        text = text.split("<think>", 1)[0].strip()

    # Loại bỏ markdown code block nếu có
    if text.startswith("```"):
        lines = text.split("\n")
        # Bỏ dòng đầu (```json) và dòng cuối (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Tìm object hoặc array JSON nằm trong phần trả lời thừa.
        candidates = [(text.find("{"), text.rfind("}") + 1),
                      (text.find("["), text.rfind("]") + 1)]
        data = None
        decoder = json.JSONDecoder()
        try:
            data, _ = decoder.raw_decode(text[text.find("{"):] if "{" in text else text[text.find("["):])
        except (json.JSONDecodeError, ValueError):
            pass
        for start, end in candidates:
            if data is not None:
                break
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end])
                    break
                except json.JSONDecodeError:
                    continue
        if data is None:
            logger.error(f"  ✗ Không parse được JSON. Raw (300 ký tự đầu): {text[:300]}")
            data = []

    # Nếu API trả về object chứa array (ví dụ: {"comments": [...]})
    if isinstance(data, dict):
        for key in ["comments", "data", "results", "items", "responses"]:
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
            if key in data and isinstance(data[key], str):
                data = data[key].splitlines()
                break
        else:
            # Một số model bọc danh sách trong nhiều lớp object.
            nested = next((value for value in data.values() if isinstance(value, dict)), None)
            if nested:
                return parse_api_response(json.dumps(nested, ensure_ascii=False), target_word_count)
            data = [data]

    if not isinstance(data, list):
        logger.error(f"  ✗ Kết quả không phải list: {type(data)}")
        return []

    valid = []
    for item in data:
        if isinstance(item, str):
            content = item.strip()
            tone = "neutral"
            style = "casual"
        elif isinstance(item, dict):
            content = str(item.get("content", item.get("comment", item.get("text", "")))).strip()
            tone = item.get("tone", "neutral")
            style = item.get("style", "casual")
        else:
            continue
        if content:
            # Bỏ dấu ngoặc kép bọc ngoài nếu có
            if (content.startswith('"') and content.endswith('"')) or (content.startswith("'") and content.endswith("'")):
                content = content[1:-1].strip()
            word_count = count_content_words(content)
            if 1 <= word_count <= target_word_count + 3:
                content = fit_content_word_count(content, target_word_count)
                valid.append({
                    "content": content,
                    "tone": tone,
                    "style": style,
                })
    if valid:
        return valid

    # Fallback cho model trả mỗi comment một dòng thay vì JSON.
    for line in text.splitlines():
        content = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip(" `\"'")
        word_count = count_content_words(content)
        if 1 <= word_count <= target_word_count + 3:
            content = fit_content_word_count(content, target_word_count)
            valid.append({"content": content, "tone": "neutral", "style": "casual"})
    return valid


def calculate_similarity(text1: str, text2: str) -> float:
    """Tính độ tương đồng giữa 2 chuỗi bằng SequenceMatcher (nhanh, đủ tốt)."""
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()


def get_opening_words(text: str, n_words: int = 3) -> str:
    """Lấy N từ đầu tiên của comment để kiểm tra lặp mở đầu."""
    words = text.split()[:n_words]
    return " ".join(words).lower().strip()


def deduplicate_comments(
    new_comments: list[dict],
    existing_comments: list[dict],
    threshold: float = 0.75,
) -> list[dict]:
    """
    Loại bỏ comment trùng lặp bằng 2 cơ chế:
    1. Hash exact match (nhanh)
    2. Similarity ratio (bắt paraphrase gần giống)
    """
    # Tập hash của comment đã có
    existing_hashes = set()
    existing_texts = []
    for c in existing_comments:
        text = c["content"].strip().lower()
        existing_hashes.add(hashlib.md5(text.encode()).hexdigest())
        existing_texts.append(text)

    unique = []
    for comment in new_comments:
        text = comment["content"].strip().lower()
        text_hash = hashlib.md5(text.encode()).hexdigest()

        # Check 1: Exact duplicate
        if text_hash in existing_hashes:
            continue

        # Check 2: Compare with the complete topic history to preserve uniqueness.
        is_similar = False
        for existing_text in existing_texts:
            if calculate_similarity(text, existing_text) > threshold:
                is_similar = True
                break

        if not is_similar:
            existing_hashes.add(text_hash)
            existing_texts.append(text)
            unique.append(comment)

    return unique


def classify_length(content: str) -> str:
    """Phân loại độ dài comment."""
    word_count = len(content.split())
    if word_count <= 15:
        return "short"
    elif word_count <= 50:
        return "medium"
    else:
        return "long"


# ============================================================================
# LƯU KẾT QUẢ
# ============================================================================

def save_to_json(comments: list[dict], filepath: str):
    """Lưu danh sách comment ra file JSON."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(comments, f, ensure_ascii=False, indent=2)
    logger.info(f"  💾 Đã lưu JSON: {filepath}")


def save_to_csv(comments: list[dict], filepath: str):
    """Lưu danh sách comment ra file CSV."""
    fieldnames = ["id", "content", "length_category", "tone", "style", "word_count"]
    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(comments)
    logger.info(f"  💾 Đã lưu CSV: {filepath}")


# ============================================================================
# ENGINE CHÍNH
# ============================================================================

class CommentGenerator:
    """Engine sinh comment chính."""

    def __init__(self, config: dict, on_progress=None, cancel_flag: threading.Event = None,
                 existing_comments: list[dict] = None):
        self.config = config
        self.topic = config["topic"]
        self.target_count = config["num_comments"]
        self.language = config["language"]
        self.batch_size = config["batch_size"]
        self.word_count = config.get("word_count", 10)
        self.similarity_threshold = config["similarity_threshold"]
        self.output_format = config["output_format"]

        # Callback cho web server: on_progress(batch_num, total, target, new_comments, log_message)
        self.on_progress = on_progress
        # Flag để huỷ task từ bên ngoài (threading.Event)
        self.cancel_flag = cancel_flag or threading.Event()
        self.existing_comments = [c.copy() for c in (existing_comments or [])]

        # Khởi tạo API client
        self.client = create_client(
            provider=config["api_provider"],
            model=config["api_model"],
            max_retries=config["max_retries"],
            retry_delay_base=config["retry_delay_base"],
        )

        # Lưu trữ kết quả
        self.comments: list[dict] = []
        self.last_error = None
        self.opening_words: list[str] = []  # Track từ mở đầu để tránh lặp
        for comment in self.existing_comments:
            opening = get_opening_words(comment.get("content", ""))
            if opening:
                self.opening_words.append(opening)

    def generate_batch(self, count: int) -> list[dict]:
        """Sinh một batch comment từ API."""
        user_prompt = build_user_prompt(
            topic=self.topic,
            count=count,
            language=self.language,
            existing_starts=self.opening_words,
            word_count=self.word_count,
        )

        system_prompt = SYSTEM_PROMPT.replace("10 TỪ", f"{self.word_count} TỪ")
        raw_response = self.client.call(system_prompt, user_prompt)
        comments = parse_api_response(raw_response, self.word_count)

        return comments

    def _notify_progress(self, batch_num, new_comments, log_message):
        """Gửi thông báo tiến trình qua callback (nếu có)."""
        if self.on_progress:
            try:
                self.on_progress(
                    batch_num=batch_num,
                    total=len(self.comments),
                    target=self.target_count,
                    new_comments=new_comments,
                    log_message=log_message,
                )
            except Exception:
                pass  # Không để callback lỗi ảnh hưởng engine

    def run(self) -> list[dict]:
        """
        Vòng lặp chính: sinh comment theo batch cho đến khi đủ số lượng.
        Tự động chạy lại nếu bị loại nhiều do trùng lặp.
        Hỗ trợ cancel_flag để huỷ giữa chừng và on_progress callback.
        """
        logger.info("=" * 60)
        logger.info(f"🚀 BẮT ĐẦU SINH COMMENT")
        logger.info(f"   Chủ đề:    {self.topic}")
        logger.info(f"   Số lượng:  {self.target_count}")
        logger.info(f"   Ngôn ngữ:  {self.language}")
        logger.info(f"   Provider:  {self.config['api_provider']} / {self.config['api_model']}")
        logger.info(f"   Batch:     {self.batch_size} comment/lần")
        logger.info("=" * 60)

        self._notify_progress(0, [], f"🚀 Bắt đầu sinh {self.target_count} comment về: {self.topic}")

        batch_num = 0
        max_rounds = 100  # Cho phép bù batch bị trùng hoặc bị rate limit
        consecutive_failures = 0

        while len(self.comments) < self.target_count and batch_num < max_rounds:
            # Kiểm tra cancel flag
            if self.cancel_flag.is_set():
                msg = f"⛔ Task bị huỷ sau {batch_num} batch ({len(self.comments)} comment)."
                logger.info(msg)
                self._notify_progress(batch_num, [], msg)
                break

            batch_num += 1
            remaining = self.target_count - len(self.comments)

            # Tính batch size: sinh thêm 20% buffer để bù trùng lặp
            current_batch = min(self.batch_size, remaining + max(3, remaining // 5))
            current_batch = max(current_batch, 5)  # Tối thiểu 5

            log_msg = (
                f"📦 Batch {batch_num}: Sinh {current_batch} comment "
                f"(đã có {len(self.comments)}/{self.target_count})..."
            )
            logger.info(f"\n{log_msg}")
            self._notify_progress(batch_num, [], log_msg)

            try:
                # Gọi API sinh comment
                new_comments = self.generate_batch(current_batch)

                if not new_comments:
                    consecutive_failures += 1
                    self.last_error = "AI provider trả về kết quả rỗng hoặc không đúng JSON/số từ yêu cầu."
                    msg = f"⚠ Batch rỗng! ({consecutive_failures} lần liên tiếp)"
                    logger.warning(f"  {msg}")
                    self._notify_progress(batch_num, [], msg)
                    if consecutive_failures >= 10:
                        msg = "✗ 10 batch rỗng liên tiếp. Dừng lại."
                        logger.error(f"  {msg}")
                        self._notify_progress(batch_num, [], msg)
                        break
                    continue

                consecutive_failures = 0  # Reset khi thành công

                # Loại bỏ trùng lặp
                before_dedup = len(new_comments)
                unique_comments = deduplicate_comments(
                    new_comments,
                    self.existing_comments + self.comments,
                    self.similarity_threshold,
                )
                duplicates = before_dedup - len(unique_comments)

                # Bổ sung metadata và thêm vào kết quả
                batch_added = []
                for comment in unique_comments:
                    if len(self.comments) >= self.target_count:
                        break
                    comment["id"] = str(uuid.uuid4())[:8]
                    comment["word_count"] = count_content_words(comment["content"])
                    comment["length_category"] = classify_length(comment["content"])
                    self.comments.append(comment)
                    batch_added.append(comment)

                    # Track từ mở đầu
                    opening = get_opening_words(comment["content"])
                    if opening:
                        self.opening_words.append(opening)

                log_msg = (
                    f"✓ Nhận: {before_dedup} | Trùng: {duplicates} | "
                    f"Thêm: {len(batch_added)} | Tổng: {len(self.comments)}/{self.target_count}"
                )
                logger.info(f"  {log_msg}")
                self._notify_progress(batch_num, batch_added, log_msg)

                # Delay nhẹ giữa các batch để tránh rate limit
                if len(self.comments) < self.target_count:
                    time.sleep(0.15)

            except Exception as e:
                self.last_error = str(e)
                msg = f"✗ Lỗi batch {batch_num}: {e}"
                logger.error(f"  {msg}")
                self._notify_progress(batch_num, [], msg)
                consecutive_failures += 1
                if consecutive_failures >= 10:
                    msg = "✗ 10 lỗi liên tiếp. Dừng lại."
                    logger.error(f"  {msg}")
                    self._notify_progress(batch_num, [], msg)
                    break
                time.sleep(3)

        # Kết quả
        logger.info("\n" + "=" * 60)
        if self.cancel_flag.is_set():
            msg = f"⛔ Đã huỷ. Sinh được {len(self.comments)} comment."
            logger.info(msg)
            self._notify_progress(batch_num, [], msg)
        elif len(self.comments) >= self.target_count:
            msg = f"🎉 HOÀN THÀNH! Đã sinh {len(self.comments)} comment trong {batch_num} batch."
            logger.info(msg)
            self._notify_progress(batch_num, [], msg)
        else:
            msg = (
                f"⚠ Chưa đủ! Chỉ sinh được {len(self.comments)}/{self.target_count} comment "
                f"sau {batch_num} batch."
            )
            if self.last_error:
                msg = f"{msg} Lỗi cuối: {self.last_error}"
            logger.warning(msg)
            self._notify_progress(batch_num, [], msg)
        logger.info("=" * 60)

        return self.comments

    def save(self, output_dir: str = "."):
        """Lưu kết quả ra file."""
        if not self.comments:
            logger.warning("Không có comment nào để lưu!")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_topic = "".join(c if c.isalnum() or c in " _-" else "_" for c in self.topic)[:40]
        base_name = f"comments_{safe_topic}_{timestamp}"

        os.makedirs(output_dir, exist_ok=True)

        if self.output_format in ("json", "both"):
            json_path = os.path.join(output_dir, f"{base_name}.json")
            save_to_json(self.comments, json_path)

        if self.output_format in ("csv", "both"):
            csv_path = os.path.join(output_dir, f"{base_name}.csv")
            save_to_csv(self.comments, csv_path)

        # In thống kê
        self._print_stats()

    def _print_stats(self):
        """In thống kê phân bố comment."""
        total = len(self.comments)
        logger.info(f"\n📊 THỐNG KÊ ({total} comment):")

        # Phân bố độ dài
        lengths = {}
        for c in self.comments:
            cat = c["length_category"]
            lengths[cat] = lengths.get(cat, 0) + 1
        logger.info("  Độ dài:")
        for cat, count in sorted(lengths.items()):
            pct = count / total * 100
            bar = "█" * int(pct / 2)
            logger.info(f"    {cat:8s}: {count:4d} ({pct:5.1f}%) {bar}")

        # Phân bố giọng điệu
        tones = {}
        for c in self.comments:
            t = c.get("tone", "unknown")
            tones[t] = tones.get(t, 0) + 1
        logger.info("  Giọng điệu:")
        for tone, count in sorted(tones.items(), key=lambda x: -x[1]):
            pct = count / total * 100
            logger.info(f"    {tone:15s}: {count:4d} ({pct:5.1f}%)")

        # Phân bố văn phong
        styles = {}
        for c in self.comments:
            s = c.get("style", "unknown")
            styles[s] = styles.get(s, 0) + 1
        logger.info("  Văn phong:")
        for style, count in sorted(styles.items(), key=lambda x: -x[1]):
            pct = count / total * 100
            logger.info(f"    {style:15s}: {count:4d} ({pct:5.1f}%)")


# ============================================================================
# LOAD / MERGE CONFIG
# ============================================================================

def load_config(config_path: str = "config.json") -> dict:
    """Load config từ file JSON, merge với default."""
    config = DEFAULT_CONFIG.copy()

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_config = json.load(f)
            config.update(file_config)
            logger.info(f"📄 Đã load config từ: {config_path}")
        except Exception as e:
            logger.warning(f"⚠ Không đọc được config: {e}. Dùng config mặc định.")
    else:
        logger.info("📄 Không tìm thấy config.json, dùng config mặc định.")

    return config


def append_existing(output_dir: str, topic: str) -> list[dict]:
    """Tìm và load file kết quả cũ nhất của cùng chủ đề để append thêm."""
    safe_topic = "".join(c if c.isalnum() or c in " _-" else "_" for c in topic)[:40]
    pattern = f"comments_{safe_topic}_"

    existing = []
    for f in sorted(Path(output_dir).glob("*.json"), reverse=True):
        if pattern in f.name:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    existing = data
                    logger.info(f"📂 Đã load {len(existing)} comment từ: {f.name}")
                    break
            except Exception:
                continue
    return existing


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="🗨️ Social Media Comment Generator - Sinh bình luận giả lập bằng AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
  python generate_comments.py
  python generate_comments.py --topic "AI thay thế con người" --count 300
  python generate_comments.py --provider anthropic --model claude-sonnet-4-20250514
  python generate_comments.py --interactive
  python generate_comments.py --append  # Chạy tiếp nếu chưa đủ
        """,
    )
    parser.add_argument("--topic", "-t", type=str, help="Chủ đề cần sinh comment")
    parser.add_argument("--count", "-n", type=int, help="Số lượng comment cần sinh")
    parser.add_argument("--language", "-l", type=str, help="Ngôn ngữ đầu ra")
    parser.add_argument("--provider", "-p", type=str, choices=["openai", "anthropic", "groq", "ollama"],
                        help="API provider")
    parser.add_argument("--model", "-m", type=str, help="Tên model")
    parser.add_argument("--batch-size", "-b", type=int, help="Số comment mỗi batch")
    parser.add_argument("--output-dir", "-o", type=str, default="output", help="Thư mục output")
    parser.add_argument("--output-format", type=str, choices=["csv", "json", "both"],
                        help="Định dạng output")
    parser.add_argument("--config", "-c", type=str, default="config.json",
                        help="Đường dẫn file config")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Chế độ nhập chủ đề từ bàn phím")
    parser.add_argument("--append", "-a", action="store_true",
                        help="Load kết quả cũ và sinh thêm cho đủ")
    parser.add_argument("--similarity", "-s", type=float,
                        help="Ngưỡng tương đồng (0-1) để loại trùng lặp")

    args = parser.parse_args()

    # Load config file
    config = load_config(args.config)

    # Override bằng CLI args
    if args.topic:
        config["topic"] = args.topic
    if args.count:
        config["num_comments"] = args.count
    if args.language:
        config["language"] = args.language
    if args.provider:
        config["api_provider"] = args.provider
    if args.model:
        config["api_model"] = args.model
    if args.batch_size:
        config["batch_size"] = args.batch_size
    if args.output_format:
        config["output_format"] = args.output_format
    if args.similarity:
        config["similarity_threshold"] = args.similarity

    # Chế độ interactive: hỏi topic từ bàn phím
    if args.interactive or not config["topic"]:
        config["topic"] = input("📝 Nhập chủ đề cần sinh comment: ").strip()
        if not config["topic"]:
            print("❌ Chủ đề không được để trống!")
            sys.exit(1)

    # Chế độ append: load comment cũ
    existing = []
    if args.append:
        existing = append_existing(args.output_dir, config["topic"])
        if existing:
            config["num_comments"] = max(0, config["num_comments"] - len(existing))
            if config["num_comments"] <= 0:
                logger.info(f"✅ Đã đủ {len(existing)} comment. Không cần sinh thêm.")
                return

    # Khởi tạo và chạy generator
    generator = CommentGenerator(config)

    # Nếu append, nạp comment cũ vào để kiểm tra trùng lặp
    if existing:
        generator.comments = existing
        generator.target_count = len(existing) + config["num_comments"]
        for c in existing:
            opening = get_opening_words(c.get("content", ""))
            if opening:
                generator.opening_words.append(opening)

    generator.run()
    generator.save(output_dir=args.output_dir)


if __name__ == "__main__":
    main()
