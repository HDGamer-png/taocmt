/**
 * =============================================================================
 * APP.JS — Dashboard Logic
 * =============================================================================
 * Manages: tasks CRUD, WebSocket real-time progress, comment viewer,
 *          filtering, export, user menu, profile
 * =============================================================================
 */

const API_BASE = '';

// ============================================================================
// STATE
// ============================================================================
let currentUser = null;
let currentTaskId = null;
let tasksList = [];
let wsConnection = null;
let providers = {};
let refreshInterval = null;

// ============================================================================
// AUTH HELPERS
// ============================================================================

function getToken() {
    return localStorage.getItem('cmt_token');
}

function getUser() {
    try { return JSON.parse(localStorage.getItem('cmt_user')); } catch { return null; }
}

function saveUser(user) {
    localStorage.setItem('cmt_user', JSON.stringify(user));
    currentUser = user;
}

function clearAuth() {
    localStorage.removeItem('cmt_token');
    localStorage.removeItem('cmt_user');
}

async function apiFetch(url, options = {}) {
    const token = getToken();
    if (!token) {
        window.location.href = '/auth';
        return null;
    }
    const headers = {
        'Authorization': `Bearer ${token}`,
        ...(options.headers || {}),
    };
    if (!(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
    }

    try {
        const res = await fetch(`${API_BASE}${url}`, {...options, headers });
        if (res.status === 401) {
            clearAuth();
            window.location.href = '/auth';
            return null;
        }
        return res;
    } catch (err) {
        showToast('Lỗi kết nối server.', 'error');
        return null;
    }
}

// ============================================================================
// TOAST
// ============================================================================

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(50px)';
        toast.style.transition = '0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// ============================================================================
// USER MENU
// ============================================================================

function toggleUserMenu() {
    document.getElementById('user-menu').classList.toggle('open');
}

function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

// Close user menu when clicking outside
document.addEventListener('click', (e) => {
    const menu = document.getElementById('user-menu');
    if (menu && !menu.contains(e.target)) {
        menu.classList.remove('open');
    }
});

function updateUserUI() {
    if (!currentUser) return;
    document.getElementById('header-username').textContent = currentUser.display_name || currentUser.username;

    const avatarEl = document.getElementById('header-avatar');
    if (currentUser.avatar_path) {
        avatarEl.innerHTML = `<img src="/${currentUser.avatar_path}" alt="Avatar">`;
    } else {
        const letter = (currentUser.display_name || currentUser.username || 'U')[0].toUpperCase();
        document.getElementById('header-avatar-letter').textContent = letter;
    }
}

function logout() {
    clearAuth();
    window.location.href = '/auth';
}

// ============================================================================
// PROFILE MODAL
// ============================================================================

function showProfileModal() {
    toggleUserMenu();
    const modal = document.getElementById('profile-modal');
    modal.classList.add('show');

    document.getElementById('profile-displayname').value = currentUser.display_name || '';
    document.getElementById('profile-email').value = currentUser.email || '';
    document.getElementById('profile-username').value = currentUser.username || '';

    const preview = document.getElementById('profile-avatar-preview');
    if (currentUser.avatar_path) {
        preview.innerHTML = `<img src="/${currentUser.avatar_path}" alt="Avatar"><div class="overlay">Đổi ảnh</div>`;
    }

    // Avatar change handler
    const input = document.getElementById('profile-avatar-input');
    input.onchange = (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (ev) => {
            preview.innerHTML = `<img src="${ev.target.result}" alt="Avatar"><div class="overlay">Đổi ảnh</div>`;
        };
        reader.readAsDataURL(file);
    };
}

async function saveProfile() {
    const displayName = document.getElementById('profile-displayname').value.trim();
    const email = document.getElementById('profile-email').value.trim();

    const res = await apiFetch('/api/auth/me', {
        method: 'PUT',
        body: JSON.stringify({ display_name: displayName, email }),
    });

    if (res && res.ok) {
        const updated = await res.json();
        saveUser(updated);
        updateUserUI();

        // Upload avatar if changed
        const avatarInput = document.getElementById('profile-avatar-input');
        if (avatarInput.files.length > 0) {
            const formData = new FormData();
            formData.append('file', avatarInput.files[0]);
            const avatarRes = await apiFetch('/api/auth/me/avatar', {
                method: 'POST',
                body: formData,
            });
            if (avatarRes && avatarRes.ok) {
                const avatarData = await avatarRes.json();
                currentUser.avatar_path = avatarData.avatar_path;
                saveUser(currentUser);
                updateUserUI();
            }
        }

        showToast('Cập nhật hồ sơ thành công!', 'success');
        closeModal('profile-modal');
    } else if (res) {
        const err = await res.json();
        showToast(err.detail || 'Lỗi cập nhật.', 'error');
    }
}

function showPasswordModal() {
    toggleUserMenu();
    document.getElementById('password-modal').classList.add('show');
    document.getElementById('current-password').value = '';
    document.getElementById('new-password').value = '';
    document.getElementById('confirm-new-password').value = '';
}

async function changePassword() {
    const oldPwd = document.getElementById('current-password').value;
    const newPwd = document.getElementById('new-password').value;
    const confirmPwd = document.getElementById('confirm-new-password').value;

    if (newPwd.length < 6) {
        showToast('Mật khẩu mới phải từ 6 ký tự.', 'error');
        return;
    }
    if (newPwd !== confirmPwd) {
        showToast('Mật khẩu xác nhận không khớp.', 'error');
        return;
    }

    const res = await apiFetch('/api/auth/me/password', {
        method: 'PUT',
        body: JSON.stringify({ old_password: oldPwd, new_password: newPwd }),
    });

    if (res && res.ok) {
        showToast('Đổi mật khẩu thành công!', 'success');
        closeModal('password-modal');
    } else if (res) {
        const err = await res.json();
        showToast(err.detail || 'Đổi mật khẩu thất bại.', 'error');
    }
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('show');
}

// Close modal on overlay click
document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.classList.remove('show');
    });
});

// ============================================================================
// PROVIDERS
// ============================================================================

async function loadProviders() {
    const res = await apiFetch('/api/providers');
    if (res && res.ok) {
        providers = await res.json();
    }
}

// ============================================================================
// TASK LIST (SIDEBAR)
// ============================================================================

async function loadTasks() {
    const res = await apiFetch('/api/tasks');
    if (!res || !res.ok) return;

    tasksList = await res.json();
    renderTaskList();
}

function renderTaskList() {
    const container = document.getElementById('task-list');

    if (tasksList.length === 0) {
        container.innerHTML = `
            <div class="empty-state" style="padding:2rem 1rem">
                <div class="empty-icon">📝</div>
                <p class="text-secondary" style="font-size:0.85rem">Chưa có task nào.<br>Bấm "＋ Mới" để bắt đầu.</p>
            </div>
        `;
        return;
    }

    container.innerHTML = tasksList.map(task => {
        const isActive = task.task_id === currentTaskId ? 'active' : '';
        const statusClass = task.status || 'pending';
        const statusLabels = {
            pending: '⏳ Chờ',
            running: '🔄 Đang chạy',
            completed: '✅ Xong',
            failed: '❌ Lỗi',
            cancelled: '⛔ Đã huỷ',
        };

        const timeStr = task.created_at ? new Date(task.created_at).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' }) : '';
        const countStr = `${task.current_count || task.comment_count || 0}/${task.num_comments}`;

        return `
            <div class="task-card ${isActive}" onclick="selectTask('${task.task_id}')" data-task-id="${task.task_id}">
                <div class="task-topic" title="${escapeHtml(task.topic)}">${escapeHtml(task.topic)}</div>
                <div class="task-meta">
                    <span class="task-status ${statusClass}">${statusLabels[statusClass] || statusClass}</span>
                    <span>${countStr} · ${timeStr}</span>
                </div>
            </div>
        `;
    }).join('');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================================
// SELECT / VIEW TASK
// ============================================================================

async function selectTask(taskId) {
    currentTaskId = taskId;
    renderTaskList(); // Update active state

    // Close sidebar on mobile
    document.getElementById('sidebar').classList.remove('open');

    // Load task details
    const res = await apiFetch(`/api/tasks/${taskId}`);
    if (!res || !res.ok) return;

    const task = await res.json();
    renderTaskDetail(task);

    // Connect WebSocket if running
    if (task.status === 'running' || task.status === 'pending') {
        connectWebSocket(taskId);
    } else {
        disconnectWebSocket();
    }
}

function renderTaskDetail(task) {
    const main = document.getElementById('main-content');
    const statusLabels = {
        pending: '⏳ Chờ xử lý',
        running: '🔄 Đang chạy',
        completed: '✅ Hoàn thành',
        failed: '❌ Thất bại',
        cancelled: '⛔ Đã huỷ',
    };

    const isRunning = task.status === 'running' || task.status === 'pending';
    const progressPct = task.progress_pct || 0;
    const comments = task.comments || [];

    main.innerHTML = `
        <div class="task-detail">
            <!-- Header -->
            <div class="task-detail-header">
                <div>
                    <h2>${escapeHtml(task.topic)}</h2>
                    <div class="task-info">
                        <span class="info-tag">${task.api_provider} / ${task.api_model}</span>
                        <span class="info-tag">${task.language}</span>
                        <span class="info-tag">Batch: ${task.batch_size}</span>
                        <span class="task-status ${task.status}">${statusLabels[task.status] || task.status}</span>
                    </div>
                </div>
                <div class="task-actions">
                    ${isRunning ? `<button class="btn btn-danger btn-sm" onclick="cancelTask('${task.task_id}')">⛔ Huỷ task</button>` : ''}
                    ${!isRunning && comments.length > 0 ? `
                        <button class="btn btn-secondary btn-sm" onclick="downloadTask('${task.task_id}', 'json')">📥 JSON</button>
                        <button class="btn btn-secondary btn-sm" onclick="downloadTask('${task.task_id}', 'csv')">📥 CSV</button>
                    ` : ''}
                    <button class="btn btn-ghost btn-sm" onclick="deleteTask('${task.task_id}')" title="Xoá task">🗑️</button>
                </div>
            </div>

            <!-- Progress -->
            <div class="progress-section" id="progress-section">
                <div class="progress-bar-container">
                    <div class="progress-bar-fill ${isRunning ? 'animated' : ''}" id="progress-bar" style="width: ${progressPct}%"></div>
                </div>
                <div class="progress-info">
                    <span id="progress-text">${task.current_count || 0} / ${task.num_comments} comment</span>
                    <span class="progress-pct" id="progress-pct">${progressPct}%</span>
                </div>
            </div>

            <!-- Log panel (visible when running) -->
            <div class="log-panel ${isRunning ? '' : 'hidden'}" id="log-panel">
                <div id="log-entries">
                    ${isRunning ? '<div class="log-entry">⏳ Đang kết nối...</div>' : ''}
                </div>
            </div>

            <!-- Stats -->
            ${!isRunning && comments.length > 0 ? renderStats(comments, task.num_comments) : ''}

            <!-- Comments section -->
            <div class="comments-section" id="comments-section">
                <h3>📋 Danh sách comment <span class="count" id="comment-count">${comments.length}</span></h3>

                <!-- Filters -->
                <div class="filters-bar">
                    <div class="search-input-wrapper">
                        <span class="search-icon">🔍</span>
                        <input type="text" class="form-input" id="search-input" placeholder="Tìm kiếm comment..." oninput="filterComments()">
                    </div>
                </div>

                <div class="filters-bar">
                    <div class="chip-filters" id="tone-filters">
                        <span class="chip active" data-tone="" onclick="setToneFilter(this)">Tất cả</span>
                    </div>
                </div>

                <!-- Table -->
                <div class="comments-table-wrapper">
                    <table class="comments-table">
                        <thead>
                            <tr>
                                <th style="width:40px">#</th>
                                <th>Nội dung</th>
                                <th style="width:100px">Tone</th>
                                <th style="width:90px">Style</th>
                                <th style="width:60px">Từ</th>
                            </tr>
                        </thead>
                        <tbody id="comments-tbody">
                        </tbody>
                    </table>
                </div>

                <!-- Pagination -->
                <div class="pagination" id="pagination"></div>
            </div>
        </div>
    `;

    // Build tone filter chips from data
    if (comments.length > 0) {
        const tones = [...new Set(comments.map(c => c.tone).filter(Boolean))];
        const toneContainer = document.getElementById('tone-filters');
        tones.forEach(tone => {
            const chip = document.createElement('span');
            chip.className = 'chip';
            chip.dataset.tone = tone;
            chip.textContent = tone;
            chip.onclick = () => setToneFilter(chip);
            toneContainer.appendChild(chip);
        });
    }

    // Render initial comments
    renderComments(comments);
}

function renderStats(comments, target) {
    const total = comments.length;

    // Count tones
    const tones = {};
    const styles = {};
    comments.forEach(c => {
        tones[c.tone] = (tones[c.tone] || 0) + 1;
        styles[c.style] = (styles[c.style] || 0) + 1;
    });

    const avgWords = total > 0 ? Math.round(comments.reduce((s, c) => s + (c.word_count || 0), 0) / total) : 0;

    return `
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">${total}</div>
                <div class="stat-label">Tổng comment</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${Object.keys(tones).length}</div>
                <div class="stat-label">Loại tone</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${Object.keys(styles).length}</div>
                <div class="stat-label">Loại style</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${avgWords}</div>
                <div class="stat-label">Trung bình từ/comment</div>
            </div>
        </div>
    `;
}

// ============================================================================
// COMMENTS TABLE + FILTER + PAGINATION
// ============================================================================

let allComments = [];
let filteredComments = [];
let currentPage = 1;
const PAGE_SIZE = 30;
let currentToneFilter = '';

function renderComments(comments) {
    allComments = comments;
    filteredComments = [...comments];
    currentPage = 1;
    currentToneFilter = '';
    applyFilters();
}

function setToneFilter(chip) {
    document.querySelectorAll('#tone-filters .chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    currentToneFilter = chip.dataset.tone || '';
    currentPage = 1;
    applyFilters();
}

function filterComments() {
    currentPage = 1;
    applyFilters();
}

function applyFilters() {
    const searchTerm = (document.getElementById('search-input')?.value || '').toLowerCase();

    filteredComments = allComments.filter(c => {
        if (currentToneFilter && c.tone !== currentToneFilter) return false;
        if (searchTerm && !c.content.toLowerCase().includes(searchTerm)) return false;
        return true;
    });

    const countEl = document.getElementById('comment-count');
    if (countEl) countEl.textContent = filteredComments.length;

    renderCommentsPage();
    renderPagination();
}

function renderCommentsPage() {
    const tbody = document.getElementById('comments-tbody');
    if (!tbody) return;

    const start = (currentPage - 1) * PAGE_SIZE;
    const pageItems = filteredComments.slice(start, start + PAGE_SIZE);

    if (pageItems.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted" style="padding:2rem">Không có comment nào</td></tr>`;
        return;
    }

    tbody.innerHTML = pageItems.map((c, i) => `
        <tr>
            <td class="text-muted">${start + i + 1}</td>
            <td class="comment-content" onclick="copyComment(this, '${escapeHtml(c.content).replace(/'/g, "\\'")}')">
                ${escapeHtml(c.content)}
                <span class="copy-hint">📋 Copy</span>
            </td>
            <td><span class="badge badge-tone">${c.tone || '-'}</span></td>
            <td><span class="badge badge-style">${c.style || '-'}</span></td>
            <td class="text-muted">${c.word_count || '-'}</td>
        </tr>
    `).join('');
}

function renderPagination() {
    const container = document.getElementById('pagination');
    if (!container) return;

    const totalPages = Math.ceil(filteredComments.length / PAGE_SIZE);
    if (totalPages <= 1) {
        container.innerHTML = '';
        return;
    }

    let html = `<button class="page-btn" ${currentPage <= 1 ? 'disabled' : ''} onclick="goToPage(${currentPage - 1})">◀</button>`;

    const maxVisible = 5;
    let startPage = Math.max(1, currentPage - Math.floor(maxVisible / 2));
    let endPage = Math.min(totalPages, startPage + maxVisible - 1);
    startPage = Math.max(1, endPage - maxVisible + 1);

    for (let p = startPage; p <= endPage; p++) {
        html += `<button class="page-btn ${p === currentPage ? 'active' : ''}" onclick="goToPage(${p})">${p}</button>`;
    }

    html += `<button class="page-btn" ${currentPage >= totalPages ? 'disabled' : ''} onclick="goToPage(${currentPage + 1})">▶</button>`;
    container.innerHTML = html;
}

function goToPage(page) {
    currentPage = page;
    renderCommentsPage();
    renderPagination();
}

function copyComment(el, text) {
    navigator.clipboard.writeText(text).then(() => {
        showToast('Đã copy comment!', 'success');
    }).catch(() => {
        // Fallback
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        showToast('Đã copy comment!', 'success');
    });
}

// ============================================================================
// NEW TASK FORM
// ============================================================================

function showNewTaskView() {
    currentTaskId = null;
    renderTaskList();

    document.getElementById('sidebar').classList.remove('open');

    const providerOptions = Object.keys(providers).map(p =>
        `<option value="${p}" ${p === 'groq' ? 'selected' : ''}>${p.charAt(0).toUpperCase() + p.slice(1)}</option>`
    ).join('');

    const main = document.getElementById('main-content');
    main.innerHTML = `
        <div class="new-task-form">
            <h2>✨ Tạo task mới</h2>
            <p class="subtitle">Cấu hình và bắt đầu sinh bình luận bằng AI</p>

            <div class="form-card">
                <h3>📝 Nội dung</h3>
                <div class="form-group">
                    <label for="task-topic">Chủ đề *</label>
                    <input type="text" class="form-input" id="task-topic" placeholder="VD: Lời khen gái xinh trên TikTok kiểu GenZ" required>
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label for="task-count">Số lượng comment</label>
                        <div class="range-group">
                            <input type="range" id="task-count" min="5" max="1000" value="200" step="5" oninput="document.getElementById('count-val').textContent=this.value">
                            <span class="range-value" id="count-val">200</span>
                        </div>
                    </div>
                    <div class="form-group">
                        <label for="task-language">Ngôn ngữ</label>
                        <select class="form-select" id="task-language">
                            <option value="Tiếng Việt" selected>🇻🇳 Tiếng Việt</option>
                            <option value="GenZ">🔥 GenZ (ưu tiên tiếng Việt)</option>
                            <option value="English">🇺🇸 English</option>
                            <option value="日本語">🇯🇵 日本語</option>
                            <option value="한국어">🇰🇷 한국어</option>
                        </select>
                    </div>
                </div>
            </div>

            <div class="form-card">
                <h3>🤖 AI Provider</h3>
                <div class="form-row">
                    <div class="form-group">
                        <label for="task-provider">Provider</label>
                        <select class="form-select" id="task-provider" onchange="updateModelOptions()">
                            ${providerOptions}
                        </select>
                    </div>
                    <div class="form-group">
                        <label for="task-model">Model</label>
                        <select class="form-select" id="task-model">
                        </select>
                    </div>
                </div>
            </div>

            <div class="form-card">
                <h3>⚙️ Nâng cao</h3>
                <div class="form-row">
                    <div class="form-group">
                        <label for="task-batch">Batch size</label>
                        <input type="number" class="form-input" id="task-batch" value="15" min="5" max="50">
                    </div>
                    <div class="form-group">
                        <label for="task-similarity">Ngưỡng trùng lặp</label>
                        <div class="range-group">
                            <input type="range" id="task-similarity" min="0" max="100" value="75" oninput="document.getElementById('sim-val').textContent=(this.value/100).toFixed(2)">
                            <span class="range-value" id="sim-val">0.75</span>
                        </div>
                    </div>
                </div>
            </div>

            <button class="btn btn-primary btn-full" onclick="createTask()" id="create-task-btn" style="padding: 0.9rem; font-size: 1rem;">
                <span class="spinner"></span>
                <span class="btn-text">🚀 Bắt đầu sinh comment</span>
            </button>
        </div>
    `;

    // Populate model options
    updateModelOptions();
}

function updateModelOptions() {
    const provider = document.getElementById('task-provider')?.value;
    const modelSelect = document.getElementById('task-model');
    if (!provider || !modelSelect) return;

    const models = providers[provider] || [];
    modelSelect.innerHTML = models.map((m, i) =>
        `<option value="${m}" ${i === 0 ? 'selected' : ''}>${m}</option>`
    ).join('');
}

async function createTask() {
    const topic = document.getElementById('task-topic')?.value.trim();
    if (!topic) {
        showToast('Vui lòng nhập chủ đề!', 'error');
        return;
    }

    const btn = document.getElementById('create-task-btn');
    btn.classList.add('loading');
    btn.disabled = true;

    const payload = {
        topic,
        num_comments: parseInt(document.getElementById('task-count').value),
        language: document.getElementById('task-language').value,
        api_provider: document.getElementById('task-provider').value,
        api_model: document.getElementById('task-model').value,
        batch_size: parseInt(document.getElementById('task-batch').value),
        similarity_threshold: parseInt(document.getElementById('task-similarity').value) / 100,
    };

    const res = await apiFetch('/api/tasks', {
        method: 'POST',
        body: JSON.stringify(payload),
    });

    btn.classList.remove('loading');
    btn.disabled = false;

    if (res && res.ok) {
        const data = await res.json();
        showToast('Task đã được tạo! Đang sinh comment...', 'success');
        await loadTasks();
        selectTask(data.task_id);
    } else if (res) {
        const err = await res.json();
        showToast(err.detail || 'Tạo task thất bại.', 'error');
    }
}

// ============================================================================
// TASK ACTIONS
// ============================================================================

async function cancelTask(taskId) {
    const res = await apiFetch(`/api/tasks/${taskId}/cancel`, { method: 'POST' });
    if (res && res.ok) {
        showToast('Đã gửi lệnh huỷ task.', 'info');
    }
}

async function deleteTask(taskId) {
    if (!confirm('Bạn có chắc muốn xoá task này?')) return;

    const res = await apiFetch(`/api/tasks/${taskId}`, { method: 'DELETE' });
    if (res && res.ok) {
        showToast('Đã xoá task.', 'success');
        if (currentTaskId === taskId) {
            currentTaskId = null;
            showEmptyState();
        }
        await loadTasks();
    }
}

function downloadTask(taskId, format) {
    const token = getToken();
    window.open(`${API_BASE}/api/tasks/${taskId}/download?format=${format}&authorization=Bearer ${token}`, '_blank');
}

// ============================================================================
// WEBSOCKET — Real-time Progress
// ============================================================================

function connectWebSocket(taskId) {
    disconnectWebSocket();

    const token = getToken();
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/tasks/${taskId}?token=${token}`;

    wsConnection = new WebSocket(wsUrl);

    wsConnection.onopen = () => {
        addLogEntry('🔗 Kết nối WebSocket thành công.');
        // Keep-alive ping
        wsConnection._pingInterval = setInterval(() => {
            if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
                wsConnection.send('ping');
            }
        }, 25000);
    };

    wsConnection.onmessage = (event) => {
        const data = event.data;
        if (data === 'ping' || data === 'pong') return;

        try {
            const msg = JSON.parse(data);
            handleProgressUpdate(msg, taskId);
        } catch {
            // Not JSON
        }
    };

    wsConnection.onclose = () => {
        if (wsConnection?._pingInterval) clearInterval(wsConnection._pingInterval);
    };

    wsConnection.onerror = () => {
        addLogEntry('⚠️ Lỗi kết nối WebSocket.');
    };
}

function disconnectWebSocket() {
    if (wsConnection) {
        if (wsConnection._pingInterval) clearInterval(wsConnection._pingInterval);
        wsConnection.close();
        wsConnection = null;
    }
}

function handleProgressUpdate(msg, taskId) {
    // Update progress bar
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const progressPct = document.getElementById('progress-pct');

    if (progressBar && msg.target > 0) {
        const pct = Math.min(100, Math.round(msg.total / msg.target * 100));
        progressBar.style.width = `${pct}%`;
        if (progressText) progressText.textContent = `${msg.total} / ${msg.target} comment`;
        if (progressPct) progressPct.textContent = `${pct}%`;
    }

    // Add log entry
    if (msg.log_message) {
        addLogEntry(msg.log_message);
    }

    // Add new comments to table
    if (msg.new_comments && msg.new_comments.length > 0) {
        msg.new_comments.forEach(c => {
            allComments.push(c);
        });
        filteredComments = [...allComments];
        const countEl = document.getElementById('comment-count');
        if (countEl) countEl.textContent = allComments.length;
        renderCommentsPage();
        renderPagination();
    }

    // Task completed/failed/cancelled
    if (msg.status) {
        disconnectWebSocket();

        // Remove animated class
        if (progressBar) progressBar.classList.remove('animated');

        // Reload full task detail
        setTimeout(() => {
            selectTask(taskId);
            loadTasks();
        }, 500);
    }
}

function addLogEntry(message) {
    const logEntries = document.getElementById('log-entries');
    if (!logEntries) return;

    const time = new Date().toLocaleTimeString('vi-VN');
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = `<span class="timestamp">[${time}]</span> ${escapeHtml(message)}`;
    logEntries.appendChild(entry);

    // Auto scroll
    const logPanel = document.getElementById('log-panel');
    if (logPanel) logPanel.scrollTop = logPanel.scrollHeight;
}

// ============================================================================
// EMPTY STATE
// ============================================================================

function showEmptyState() {
    document.getElementById('main-content').innerHTML = `
        <div class="empty-state">
            <div class="empty-icon">🗨️</div>
            <h3>Chào mừng đến Comment Generator!</h3>
            <p>Chọn một task từ sidebar hoặc tạo task mới để bắt đầu sinh bình luận.</p>
            <button class="btn btn-primary" onclick="showNewTaskView()">✨ Tạo task mới</button>
        </div>
    `;
}

// ============================================================================
// AUTO REFRESH for running tasks
// ============================================================================

function startAutoRefresh() {
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(async () => {
        const hasRunning = tasksList.some(t => t.status === 'running' || t.status === 'pending');
        if (hasRunning) {
            await loadTasks();
        }
    }, 10000); // Refresh task list every 10s if any are running
}

// ============================================================================
// INIT
// ============================================================================

async function init() {
    // Check auth
    const token = getToken();
    if (!token) {
        window.location.href = '/auth';
        return;
    }

    // Load user info
    const res = await apiFetch('/api/auth/me');
    if (!res || !res.ok) {
        clearAuth();
        window.location.href = '/auth';
        return;
    }

    currentUser = await res.json();
    saveUser(currentUser);
    updateUserUI();

    // Load providers and tasks
    await Promise.all([loadProviders(), loadTasks()]);

    // Show empty state or new task form
    showEmptyState();

    // Start auto-refresh
    startAutoRefresh();
}

document.addEventListener('DOMContentLoaded', init);