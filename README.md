# taocmt

## Lưu trữ dữ liệu

Ứng dụng ưu tiên lưu tài khoản, task/comment và lịch sử trong PostgreSQL. Khi
chạy local mà chưa có `DATABASE_URL`, ứng dụng tự động dùng SQLite tại
`data/app.db` để phát triển và kiểm thử.

Các biến môi trường:

- `DATABASE_URL`: chuỗi kết nối PostgreSQL từ Supabase hoặc Neon.
- `CMT_DATA_DIR`: thư mục chứa avatar và SQLite fallback.
- `CMT_DB_PATH`: đường dẫn đầy đủ tới file SQLite fallback.
- `ADMIN_USERNAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`: thông tin tạo hoặc nâng
	cấp tài khoản Admin ban đầu trên Render.

Khi khởi động lần đầu, ứng dụng tự động nhập dữ liệu cũ từ `data/users.json`,
`data/{user_id}/tasks.json` và `BE/history/{user_id}.json`. Database được đánh dấu
đã migrate để không nhập trùng ở các lần khởi động sau.

### Cấu hình Render và PostgreSQL miễn phí

`render.yaml` yêu cầu biến môi trường `DATABASE_URL`. Hãy tạo database miễn phí
trên Supabase hoặc Neon, lấy connection string và nhập vào Render tại
**Dashboard → Service → Environment → Add Environment Variable**.

Để tạo Admin, thêm ba biến `ADMIN_USERNAME`, `ADMIN_EMAIL` và `ADMIN_PASSWORD`
trên Render trước lần deploy đầu tiên. Khi ứng dụng khởi động, tài khoản đó sẽ
được tạo với quyền Admin. Không đưa mật khẩu vào GitHub. Sau khi đăng nhập,
truy cập `/admin` để quản lý.

Khi khởi động lần đầu, ứng dụng tự tạo các bảng PostgreSQL. Nếu các file JSON cũ
vẫn có trong workspace lúc khởi động, ứng dụng sẽ tự nhập một lần. Dữ liệu đã
từng nằm trên instance Render cũ không tự chuyển sang database mới; cần xuất
hoặc nhập chúng trước khi deploy phiên bản này.

Avatar hiện vẫn là file local. Với Render Free, avatar có thể mất khi instance
thay đổi; tài khoản, task, comment và history vẫn được bảo toàn trong PostgreSQL.