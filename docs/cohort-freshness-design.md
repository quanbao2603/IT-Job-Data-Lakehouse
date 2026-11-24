# Thiết kế phạm vi dữ liệu: giữ tối đa, nhưng luôn phân biệt được độ mới

## Vấn đề

Cohort Gold giữ mọi tin từng được hợp nhất. Ở release `gold_20261124_2024_salary`:

- **4.526 tin** tổng cộng
- **3.533 tin** có lần ghi nhận cuối trong 14 ngày (cửa sổ bằng chứng mà Gold contract khai báo)
- **993 tin** cũ hơn, cá biệt có tin từ 07/11

Nếu chỉ hiển thị 3.533 tin thì mất dữ liệu lịch sử. Nếu chỉ hiển thị 4.526 tin mà không nói gì thì người đọc hiểu sai là "đang tuyển".

## Quyết định

Giữ **toàn bộ** dữ liệu, và biến độ mới thành một **thuộc tính truy vấn được** thay vì một lần cắt dữ liệu.

### 1. Ở tầng Gold

Không đổi schema, không xoá dòng nào. `last_seen_at` đã có sẵn trong `gold.v_job_snapshot`, nên độ mới suy ra được bằng biểu thức:

```sql
last_seen_at >= NOW() - INTERVAL '14 days'   -- còn bằng chứng trong cửa sổ
```

Ngưỡng 14 ngày lấy đúng hằng số `EVIDENCE_WINDOW_DAYS` của Gold contract, không tự đặt lại.

### 2. Ở tầng API

Mọi endpoint nhận thêm `scope`:

| Giá trị | Ý nghĩa | Số tin |
| --- | --- | --- |
| `all` (mặc định) | Toàn bộ dữ liệu hệ thống biết | 4.526 |
| `recent` | Chỉ tin có bằng chứng trong 14 ngày | 3.533 |

`recent` là bộ lọc thật ở tầng SQL (`_where()`), không phải tham số trang trí: mọi biểu đồ, bảng, phân trang và xuất CSV đều tính lại theo scope. Giá trị lạ trả 422.

### 3. Ở tầng giao diện

- Ô chọn **"Mọi thời điểm / Cập nhật 14 ngày"** nằm cùng hàng với các bộ lọc khác.
- Dưới cụm KPI của Tổng quan luôn in: *"3.533 tin được ghi nhận trong 14 ngày gần nhất; 993 tin cũ hơn (lần ghi nhận sớm nhất 07/11) vẫn nằm trong tập dữ liệu."*
- Thẻ tin nào quá 14 ngày có nhãn **"cũ N ngày"** ngay cạnh chuyên môn, để không ai đọc nhầm một tin 17 ngày tuổi là tin mới.

### 4. Ở trợ lý AI

Prompt buộc:

- không dùng `open_job_count`/`entity_status` như bằng chứng tin còn đăng;
- khi câu hỏi ngụ ý hiện tại ("đang tuyển", "còn mở"), phải chạy với `last_seen_at >= NOW() - INTERVAL '14 days'` và **nói rõ đã giới hạn 14 ngày**;
- khi là thống kê chung, dùng toàn bộ tập và nêu tổng số để người đọc biết quy mô.

Ví dụ câu trả lời hiện tại cho "Hiện có bao nhiêu tin còn đang tuyển?":

> Trong release hiện tại có **4.526 tin** đã hợp nhất (tổng `open_job_count` = 4.526), trong đó **3.533 tin** có lần ghi nhận cuối trong 14 ngày gần nhất; 993 tin còn lại là tin cũ hơn.
> Lưu ý: đây là số tin hệ thống quan sát được, không phải kết luận "đang tuyển" — `open_job_count` là trạng thái vòng đời đã hợp nhất, không phải bằng chứng tin còn đăng.

## Vì sao không cắt dữ liệu

| Cách làm | Dữ liệu | Rủi ro |
| --- | --- | --- |
| Chỉ giữ tin ≤14 ngày | Mất 993 tin lịch sử, không truy vấn lại được | Xu hướng nhiều kỳ không dựng được; mất bằng chứng cũ |
| Giữ tất cả, không nêu độ mới | Đủ dữ liệu | Người đọc hiểu sai là tin đang tuyển |
| **Giữ tất cả + `scope` + nhãn độ mới** | **Đủ dữ liệu, truy vấn được cả hai góc nhìn** | Không có, miễn là giao diện và AI luôn nêu scope |

## Việc còn lại (không chặn)

Nguyên nhân sâu của 993 tin cũ là vòng đời thực thể chưa đóng tin: đóng tin dựa trên "nguồn không còn liệt kê trong lần cào hôm nay", nhưng từ 15/11 các lần cào đều là incremental nên tin cũ không được kiểm tra lại. Muốn giảm con số này một cách trung thực thì phải cào lại toàn bộ danh sách của từng nguồn (full mode) rồi để `close_stale_entities()` đóng những tin thực sự biến mất — không được suy diễn từ việc tin vắng mặt trong một batch tăng trưởng.
