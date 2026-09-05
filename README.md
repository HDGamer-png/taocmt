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
	cấp tài khoản Admin ban đầu trên Replit.

Khi khởi động lần đầu, ứng dụng tự động nhập dữ liệu cũ từ `data/users.json`,
`data/{user_id}/tasks.json` và `BE/history/{user_id}.json`. Database được đánh dấu
đã migrate để không nhập trùng ở các lần khởi động sau.

### Cấu hình Neon PostgreSQL và Replit

Tạo database PostgreSQL miễn phí trên Neon, sao chép connection string dạng
`postgresql://...` và đặt vào Replit Secrets với tên `DATABASE_URL`. Không dùng
SQLite trên Replit cho dữ liệu production vì filesystem của deployment không
được coi là nơi lưu trữ lâu dài.

Trong Replit, import repository này và tạo các Secrets: `DATABASE_URL`,
`CMT_SECRET_KEY`, `GEMINI_API_KEY_1` đến `GEMINI_API_KEY_5`, `GROQ_API_KEY`, `ADMIN_USERNAME`,
`ADMIN_EMAIL` và `ADMIN_PASSWORD`. File `.replit` đã chứa lệnh chạy web và
deployment.

Gemini là provider chính. Hãy thêm đủ năm key bằng `GEMINI_API_KEY_1` đến
`GEMINI_API_KEY_5`. Backend sẽ thử từng key Gemini; khi một key hết quota, timeout
hoặc trả lỗi, key đó được bỏ qua và chuyển sang key kế tiếp, sau đó mới fallback
sang Groq. Người dùng không nhìn thấy hay lựa chọn provider/model.

Model mặc định là `gemini-2.5-flash-lite`, phù hợp cho việc sinh nhiều comment
nhanh. Có thể ghi đè bằng Secret hoặc biến môi trường `GEMINI_MODEL`.

Để tạo Admin, thêm ba biến `ADMIN_USERNAME`, `ADMIN_EMAIL` và `ADMIN_PASSWORD`
trên Replit trước lần deploy đầu tiên. Khi ứng dụng khởi động, tài khoản đó sẽ
được tạo với quyền Admin. Không đưa mật khẩu vào GitHub. Sau khi đăng nhập,
truy cập `/admin` để quản lý.

Khi khởi động lần đầu, ứng dụng tự tạo các bảng PostgreSQL. Nếu các file JSON cũ
vẫn có trong workspace lúc khởi động, ứng dụng sẽ tự nhập một lần. Dữ liệu đã
từng nằm trên instance cũ không tự chuyển sang database mới; cần xuất
hoặc nhập chúng trước khi deploy phiên bản này.

Avatar hiện vẫn là file local. Với deployment miễn phí, avatar có thể mất khi instance
thay đổi; tài khoản, task, comment và history vẫn được bảo toàn trong PostgreSQL.