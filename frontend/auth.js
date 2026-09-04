/**
 * =============================================================================
 * AUTH.JS — Xử lý đăng nhập / đăng ký
 * =============================================================================
 */

const API_BASE = '';

// ============================================================================
// HELPERS
// ============================================================================

function showAlert(message, type = 'error') {
    const alert = document.getElementById('auth-alert');
    alert.className = `alert show alert-${type}`;
    alert.querySelector('.alert-icon').textContent = type === 'error' ? '⚠️' : '✅';
    alert.querySelector('.alert-text').textContent = message;

    // Auto-hide after 5s
    setTimeout(() => {
        alert.classList.remove('show');
    }, 5000);
}

function setLoading(btn, loading) {
    if (loading) {
        btn.classList.add('loading');
        btn.disabled = true;
    } else {
        btn.classList.remove('loading');
        btn.disabled = false;
    }
}

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
    }, 3000);
}

// ============================================================================
// TOKEN MANAGEMENT
// ============================================================================

function saveToken(token) {
    localStorage.setItem('cmt_token', token);
}

function getToken() {
    return localStorage.getItem('cmt_token');
}

function saveUser(user) {
    localStorage.setItem('cmt_user', JSON.stringify(user));
}

function getUser() {
    try {
        return JSON.parse(localStorage.getItem('cmt_user'));
    } catch {
        return null;
    }
}

function clearAuth() {
    localStorage.removeItem('cmt_token');
    localStorage.removeItem('cmt_user');
}

// Check if already logged in
function checkAuth() {
    const token = getToken();
    if (token) {
        // Verify token is still valid
        fetch(`${API_BASE}/api/auth/me`, {
                headers: { 'Authorization': `Bearer ${token}` }
            })
            .then(async res => {
                if (res.ok) {
                    const user = await res.json();
                    saveUser(user);
                    window.location.href = user.role === 'admin' ? '/admin' : '/dashboard';
                } else {
                    clearAuth();
                }
            })
            .catch(() => {
                // Token invalid, stay on auth page
                clearAuth();
            });
    }
}

// ============================================================================
// TAB SWITCHING
// ============================================================================

function switchTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.auth-tab[data-tab="${tabName}"]`).classList.add('active');

    // Update forms
    document.querySelectorAll('.auth-form').forEach(f => f.classList.remove('active'));
    document.getElementById(`${tabName}-form`).classList.add('active');

    // Clear alert
    document.getElementById('auth-alert').classList.remove('show');

    // Clear errors
    document.querySelectorAll('.form-group.error').forEach(g => g.classList.remove('error'));

    // Update page title
    document.title = tabName === 'login' ?
        'Comment Generator — Đăng nhập' :
        'Comment Generator — Đăng ký';
}

// ============================================================================
// AVATAR PREVIEW
// ============================================================================

function setupAvatarPreview() {
    const input = document.getElementById('avatar-input');
    const preview = document.getElementById('avatar-preview');

    input.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;

        if (!file.type.startsWith('image/')) {
            showAlert('Vui lòng chọn file ảnh.', 'error');
            return;
        }

        if (file.size > 5 * 1024 * 1024) {
            showAlert('Ảnh quá lớn (tối đa 5MB).', 'error');
            return;
        }

        const reader = new FileReader();
        reader.onload = (ev) => {
            preview.innerHTML = `<img src="${ev.target.result}" alt="Avatar"><div class="overlay">Đổi ảnh</div>`;
        };
        reader.readAsDataURL(file);
    });
}

// ============================================================================
// LOGIN
// ============================================================================

async function handleLogin(e) {
    e.preventDefault();

    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value;
    const btn = document.getElementById('login-btn');

    if (!username || !password) {
        showAlert('Vui lòng nhập đầy đủ thông tin.', 'error');
        return;
    }

    setLoading(btn, true);

    try {
        const res = await fetch(`${API_BASE}/api/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username_or_email: username,
                password: password,
            }),
        });

        const data = await res.json();

        if (res.ok) {
            saveToken(data.token);
            saveUser(data.user);
            showToast('Đăng nhập thành công! Đang chuyển hướng...', 'success');
            setTimeout(() => {
                window.location.href = data.user.role === 'admin' ? '/admin' : '/dashboard';
            }, 500);
        } else {
            showAlert(data.detail || 'Đăng nhập thất bại.', 'error');
        }
    } catch (err) {
        showAlert('Lỗi kết nối server. Hãy kiểm tra lại.', 'error');
    } finally {
        setLoading(btn, false);
    }
}

// ============================================================================
// REGISTER
// ============================================================================

async function handleRegister(e) {
    e.preventDefault();

    const displayName = document.getElementById('reg-displayname').value.trim();
    const username = document.getElementById('reg-username').value.trim();
    const email = document.getElementById('reg-email').value.trim();
    const password = document.getElementById('reg-password').value;
    const passwordConfirm = document.getElementById('reg-password-confirm').value;
    const btn = document.getElementById('register-btn');

    // Clear errors
    document.querySelectorAll('.form-group.error').forEach(g => g.classList.remove('error'));

    // Validate
    let hasError = false;

    if (displayName.length < 2 || displayName.length > 30) {
        document.getElementById('reg-displayname').closest('.form-group').classList.add('error');
        hasError = true;
    }

    if (!/^[a-zA-Z0-9_]{3,20}$/.test(username)) {
        document.getElementById('reg-username').closest('.form-group').classList.add('error');
        hasError = true;
    }

    if (!email || !email.includes('@') || !email.includes('.')) {
        document.getElementById('reg-email').closest('.form-group').classList.add('error');
        hasError = true;
    }

    if (password.length < 6) {
        document.getElementById('reg-password').closest('.form-group').classList.add('error');
        hasError = true;
    }

    if (password !== passwordConfirm) {
        document.getElementById('reg-password-confirm').closest('.form-group').classList.add('error');
        hasError = true;
    }

    if (hasError) {
        showAlert('Vui lòng kiểm tra lại thông tin.', 'error');
        return;
    }

    setLoading(btn, true);

    try {
        // Step 1: Register
        const res = await fetch(`${API_BASE}/api/auth/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username,
                email,
                password,
                display_name: displayName,
            }),
        });

        const data = await res.json();

        if (!res.ok) {
            showAlert(data.detail || 'Đăng ký thất bại.', 'error');
            return;
        }

        // Step 2: Upload avatar if selected
        const avatarInput = document.getElementById('avatar-input');
        if (avatarInput.files.length > 0) {
            const formData = new FormData();
            formData.append('file', avatarInput.files[0]);

            await fetch(`${API_BASE}/api/auth/me/avatar`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${data.token}` },
                body: formData,
            });
        }

        // Save and redirect
        saveToken(data.token);
        saveUser(data.user);
        showToast('Tạo tài khoản thành công!', 'success');
        setTimeout(() => {
            window.location.href = '/dashboard';
        }, 500);
    } catch (err) {
        showAlert('Lỗi kết nối server. Hãy kiểm tra lại.', 'error');
    } finally {
        setLoading(btn, false);
    }
}

// ============================================================================
// INIT
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
    // Check existing auth
    checkAuth();

    // Tab switching
    document.querySelectorAll('.auth-tab').forEach(tab => {
        tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });

    document.getElementById('switch-to-register').addEventListener('click', (e) => {
        e.preventDefault();
        switchTab('register');
    });

    document.getElementById('switch-to-login').addEventListener('click', (e) => {
        e.preventDefault();
        switchTab('login');
    });

    // Avatar preview
    setupAvatarPreview();

    // Form submissions
    document.getElementById('login-form').addEventListener('submit', handleLogin);
    document.getElementById('register-form').addEventListener('submit', handleRegister);
});