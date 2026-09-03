# 🗨️ Social Media Comment Generator

Công cụ sinh bình luận giả lập phong cách mạng xã hội bằng AI API (OpenAI / Anthropic).

## Cài đặt

```bash
pip install -r requirements.txt
```

## Cấu hình API Key

```bash
# OpenAI (Windows PowerShell)
$env:OPENAI_API_KEY = "sk-your-key-here"

# Hoặc Anthropic
$env:ANTHROPIC_API_KEY = "sk-ant-your-key-here"
```

## Cách chạy

### 1. Dùng file config (đơn giản nhất)
Sửa `config.json` rồi chạy:
```bash
python generate_comments.py
```

### 2. Dùng CLI arguments
```bash
# Sinh 200 comment về chủ đề cụ thể
python generate_comments.py --topic "AI thay thế con người" --count 200

# Dùng Anthropic Claude
python generate_comments.py --topic "Giáo dục" --provider anthropic --model claude-sonnet-4-20250514

# Chế độ nhập chủ đề từ bàn phím
python generate_comments.py --interactive

# Chạy tiếp nếu lần trước chưa đủ
python generate_comments.py --append
```

### 3. Các tham số CLI đầy đủ

| Tham số | Mô tả | Mặc định |
|---------|--------|----------|
| `--topic, -t` | Chủ đề cần sinh | Từ config.json |
| `--count, -n` | Số lượng comment | 200 |
| `--language, -l` | Ngôn ngữ đầu ra (`Tiếng Việt`, `GenZ`, `English`...) | Tiếng Việt |
| `--provider, -p` | API provider (openai/anthropic) | openai |
| `--model, -m` | Tên model | gpt-4o-mini |
| `--batch-size, -b` | Số comment mỗi batch | 15 |
| `--output-dir, -o` | Thư mục output | output/ |
| `--output-format` | csv, json, hoặc both | both |
| `--config, -c` | Đường dẫn config | config.json |
| `--interactive, -i` | Nhập chủ đề từ bàn phím | false |
| `--append, -a` | Load kết quả cũ và sinh thêm | false |
| `--similarity, -s` | Ngưỡng tương đồng (0-1) | 0.75 |

## Output

Kết quả được lưu trong thư mục `output/` gồm:
- **JSON**: Dữ liệu đầy đủ, dễ xử lý tiếp
- **CSV**: Mở được bằng Excel, có encoding UTF-8-BOM

Mỗi comment gồm các trường:
| Trường | Mô tả |
|--------|--------|
| `id` | UUID ngắn 8 ký tự |
| `content` | Nội dung comment |
| `length_category` | short / medium / long |
| `tone` | agreeing, disagreeing, neutral, humorous, sarcastic, questioning, storytelling, informative |
| `style` | formal, casual, teencode, emoji_heavy, emphatic, minimal |
| `word_count` | Số từ |
