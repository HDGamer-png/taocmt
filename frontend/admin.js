const adminToken = localStorage.getItem('cmt_token');

function adminHeaders(options = {}) {
    return {
        'Authorization': `Bearer ${adminToken}`,
        'Content-Type': 'application/json',
        ...(options.headers || {}),
    };
}

async function adminFetch(url, options = {}) {
    const response = await fetch(url, {...options, headers: adminHeaders(options) });
    if (response.status === 401 || response.status === 403) {
        window.location.href = '/auth';
        return null;
    }
    return response;
}

function escapeAdminHtml(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

function showAdminToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
}

function formatAdminDate(value) {
    if (!value) return '-';
    return new Date(value).toLocaleString('vi-VN');
}

async function loadAdminData() {
    const [summaryResponse, usersResponse] = await Promise.all([
        adminFetch('/api/admin/summary'),
        adminFetch('/api/admin/users'),
    ]);
    if (!summaryResponse || !usersResponse || !summaryResponse.ok || !usersResponse.ok) {
        showAdminToast('Không thể tải dữ liệu quản trị.', 'error');
        return;
    }

    const summary = await summaryResponse.json();
    const users = await usersResponse.json();
    document.getElementById('admin-stats').innerHTML = `
        <div class="admin-stat"><span>Tổng user</span><strong>${summary.total_users}</strong></div>
        <div class="admin-stat"><span>User hoạt động</span><strong>${summary.active_users}</strong></div>
        <div class="admin-stat"><span>Tổng comment</span><strong>${summary.generated_count}</strong></div>
        <div class="admin-stat"><span>Task hoàn thành</span><strong>${summary.completed_tasks}</strong></div>
    `;

    document.getElementById('admin-users').innerHTML = users.map(user => `
        <tr>
            <td><strong>${escapeAdminHtml(user.display_name)}</strong><small>@${escapeAdminHtml(user.username)}</small></td>
            <td>${escapeAdminHtml(user.email)}<small>Đăng ký: ${formatAdminDate(user.created_at)}</small></td>
            <td><span class="admin-role ${user.role}">${escapeAdminHtml(user.role)}</span></td>
            <td>${user.task_count || 0}</td>
            <td>${user.generated_count || 0}</td>
            <td><span class="admin-status ${user.is_active ? 'active' : 'blocked'}">${user.is_active ? 'Hoạt động' : 'Đã khóa'}</span></td>
            <td>${user.role === 'admin' ? '<span class="text-muted">Admin</span>' : `<button class="btn btn-secondary btn-sm" onclick="toggleUser('${user.user_id}', ${!user.is_active})">${user.is_active ? 'Khóa' : 'Mở khóa'}</button>`}</td>
        </tr>
    `).join('');
}

async function toggleUser(userId, isActive) {
    const response = await adminFetch(`/api/admin/users/${userId}/status?is_active=${isActive}`, { method: 'PUT' });
    if (response && response.ok) {
        showAdminToast(isActive ? 'Đã mở khóa tài khoản.' : 'Đã khóa tài khoản.', 'success');
        await loadAdminData();
    } else {
        showAdminToast('Không thể cập nhật tài khoản.', 'error');
    }
}

function logoutAdmin() {
    localStorage.removeItem('cmt_token');
    localStorage.removeItem('cmt_user');
    window.location.href = '/auth';
}

if (!adminToken) {
    window.location.href = '/auth';
} else {
    loadAdminData();
}