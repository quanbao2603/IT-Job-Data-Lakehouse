# KIẾN TRÚC TIỀN XỬ LÝ & CHUẨN HÓA DỮ LIỆU TẦNG SILVER
**Hệ thống:** Data Lakehouse Tổng hợp, Phân tích Thị trường Việc làm IT  
**Mục tiêu tài liệu:** Định nghĩa mô hình dữ liệu chuẩn (Canonical Data Model) và quy tắc ánh xạ schema từ 4 nền tảng (ITViec, TopCV, TopDev, VietnamWorks) lên Tầng Silver (Cleaned & Conformed Layer).

---

## 1. Triết lý Kiến trúc Medallion trong Đề tài

* **Tầng Bronze (Raw Layer - Source-aligned):** 
  Lưu trữ nguyên trạng dữ liệu thu thập từ bot cào theo cấu trúc tự nhiên của từng sàn (ITViec: 23 trường, TopCV: 28 trường, TopDev: 22 trường, VietnamWorks: 29 trường) dưới dạng file JSON/Parquet thô. Không thực hiện lọc bỏ trường dữ liệu nhằm bảo toàn tối đa ngữ cảnh ban đầu (Schema-on-read).
* **Tầng Silver (Cleaned & Conformed Layer):** 
  Hợp nhất các nguồn dữ liệu dị thể (Heterogeneous Data) về một thực thể chuẩn hóa duy nhất (`silver_job_postings`). Tiến hành khử trùng lặp (Deduplication), ép kiểu dữ liệu chuẩn, xử lý chuỗi văn bản và gọi LLM bóc tách thông tin phi cấu trúc.
* **Tầng Gold (Aggregated & Data Marts Layer):** 
  Mô hình hóa dữ liệu từ Silver thành các bảng Chiều (Dimension) và Sự kiện (Fact) phục vụ truy vấn OLAP, báo cáo Dashboard BI và cấp công cụ Text-to-SQL cho AI Agent.

---

## 2. Bảng Ánh Xạ Schema Đa Nền Tảng (Cross-Platform Schema Mapping)

Bảng tổng hợp quy tắc chuyển đổi các trường dữ liệu từ 4 nền tảng về bảng thực thể hợp nhất `silver_job_postings`:

| Nhóm thông tin | Trường chuẩn hóa (Silver Schema) | Kiểu dữ liệu | ITViec (23 trường) | TopCV (28 trường) | TopDev (22 trường) | VietnamWorks (29 trường) | Ghi chú xử lý / Logic |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Định danh & Audit** | `unified_job_id` | STRING (PK) | `itviec_{job_id}` | `topcv_{job_id}` | `topdev_{job_id}` | `vnw_{job_id}` | Khóa chính toàn cục tránh trùng lặp giữa các sàn |
| | `platform` | STRING | `platform` | `platform` | `platform` | `platform` | Nguồn thu thập (ITViec, TopCV, TopDev, VietnamWorks) |
| | `original_job_id` | STRING | `job_id` | `job_id` | `job_id` | `job_id` | ID nguyên bản của tin trên nền tảng nguồn |
| | `job_url` | STRING | `job_url` | `job_url` | `job_url` | `job_url` | Đường dẫn URL bài viết gốc |
| | `content_hash` | STRING | `content_hash` | `content_hash` | `content_hash` | `content_hash` | Hash MD5 dùng để kiểm tra biến động nội dung (CDC) |
| | `crawled_at` | TIMESTAMP | `crawled_date` | `scraped_at` | `scraped_at` | `scraped_at` | Thời điểm bot thu thập bản ghi |
| **Thông tin Việc làm** | `job_title` | STRING | `job_title` | `job_title` | `job_title` | `job_title` | Chuẩn hóa khoảng trắng, loại bỏ ký tự rác |
| | `job_level` | STRING | *Bóc từ JD* | `job_level` | `job_level` | `job_level` | Chuẩn hóa: Intern, Fresher, Junior, Mid, Senior, Lead, Manager |
| | `job_expertise` | STRING | `job_expertise` | *Bóc từ JD* | *Bóc từ JD* | `job_function` | Phân loại chuyên môn (Backend, Frontend, Fullstack, DevOps, Data...) |
| | `job_domain` | STRING | `job_domain` | *Bóc từ JD* | *Bóc từ JD* | *Bóc từ JD* | Lĩnh vực ứng dụng (Fintech, E-commerce, EdTech, Healthcare...) |
| | `work_mode` | STRING | `work_mode` | `work_mode` | *Bóc từ JD* | *Bóc từ JD* | Chuẩn hóa: On-site, Hybrid, Remote |
| | `working_time` | STRING | `working_days` | `work_time` | N/A | `working_days` | Thời gian, giờ giấc làm việc |
| | `overtime_policy` | STRING | `overtime_policy` | *Bóc từ JD* | *Bóc từ JD* | *Bóc từ JD* | Chính sách làm thêm giờ |
| **Thông tin Doanh nghiệp** | `company_name` | STRING | `company_name` | `company_name` | `company_name` | `company_name` | Tên pháp nhân tuyển dụng |
| | `company_size` | STRING | `company_size` | `company_size` | `company_size` | N/A | Quy mô nhân sự (chuẩn hóa về khoảng số) |
| | `company_industry` | STRING | `company_industry` | `company_industry` | `company_industry` | `job_industry` | Ngành nghề kinh doanh của công ty |
| | `company_type` | STRING | `company_type` | *Bóc từ JD* | *Bóc từ JD* | *Bóc từ JD* | Phân loại: Product, Outsourcing, System Integrator |
| | `company_country` | STRING | `country` | N/A | `company_country` | `nationality` (Ref) | Quốc gia trụ sở chính |
| **Địa điểm & Không gian** | `city` | STRING | Bóc từ `address` | `job_location` | `job_location` | `company_location` | Chuẩn hóa danh mục hành chính: Hà Nội, TP.HCM, Đà Nẵng... |
| | `full_address` | STRING | `address` | `work_address` | N/A | `job_locations` | Địa chỉ cụ thể phục vụ phân tích bán kính địa lý |
| **Lương & Đãi ngộ** | `salary_raw` | STRING | `salary` | `salary` | `salary` | `salary` | Chuỗi ký tự mức lương nguyên bản |
| | `salary_min_vnd` | BIGINT | *LLM/Regex* | *LLM/Regex* | *LLM/Regex* | *LLM/Regex* | Cận dưới mức lương quy đổi về VNĐ |
| | `salary_max_vnd` | BIGINT | *LLM/Regex* | *LLM/Regex* | *LLM/Regex* | *LLM/Regex* | Cận trên mức lương quy đổi về VNĐ |
| | `is_negotiable` | BOOLEAN | *LLM/Regex* | *LLM/Regex* | *LLM/Regex* | *LLM/Regex* | Đánh cờ TRUE nếu lương là "Thương lượng / Negotiable" |
| **Định lượng & Tương tác**| `exp_years_min` | DOUBLE | Bóc từ `skills_and_exp` | Bóc từ `experience`| Bóc từ `experience`| `years_of_experience`| Số năm kinh nghiệm tối thiểu yêu cầu |
| | `exp_years_max` | DOUBLE | Bóc từ `skills_and_exp` | Bóc từ `experience`| Bóc từ `experience`| Bóc từ `experience`| Số năm kinh nghiệm tối đa (nếu có) |
| | `vacancies_count` | INT | N/A (Mặc định 1) | `quantity` | N/A (Mặc định 1) | N/A (Mặc định 1) | Số lượng vị trí cần tuyển |
| | `views_count` | INT | N/A | N/A | N/A | `views_count` | Lượt xem bài tuyển dụng |
| | `applications_count`| INT | N/A | N/A | `num_candidates` | `applicant_count` | Số lượng hồ sơ đã nộp |
| **Thời gian Tuyển dụng** | `posted_date` | DATE | `posted_date` | N/A | N/A | `posted_date` / `posted_time_ago` | Ngày tin tuyển dụng bắt đầu hiệu lực |
| | `expired_date` | DATE | N/A | `deadline` | `expires_in` (Quy đổi) | `expired_on` | Ngày hết hạn nộp hồ sơ |
| **Nội dung Chi tiết & Kỹ năng** | `skills_raw` | ARRAY\<STRING\> | `skills` | `overview_skills` + `required_skills` | `skills` | `skills` + `user_tags` | Mảng các tag kỹ năng thô từ web |
| | `skills_standardized` | ARRAY\<STRING\> | *LLM Entity Parser* | *LLM Entity Parser* | *LLM Entity Parser* | *LLM Entity Parser* | Mảng Tech Stack chuẩn hóa (VD: `["Python", "AWS", "Spark"]`) |
| | `job_description_clean` | STRING | `job_description` | `job_description` | `general_description` + `responsibilities` | `job_description` | Mô tả trách nhiệm công việc sau khi dọn HTML tags |
| | `requirements_clean` | STRING | `skills_and_experience` | `candidate_requirements` + `industry_knowledge` | `requirements` | `job_requirements` | Yêu cầu năng lực ứng viên đã làm sạch |
| | `benefits_clean` | STRING | `why_love_working_here` | `benefits` | `benefits` | `what_we_offer` | Quyền lợi, chế độ đãi ngộ đã làm sạch |
| **Mở rộng Đa nguồn** | `platform_specific_attributes` | STRING (JSON) | Các trường đặc thù còn lại | Các trường đặc thù còn lại | Các trường đặc thù còn lại | Các trường đặc thù còn lại | Đóng gói JSON các trường riêng biệt để tránh thất thoát dữ liệu thô |

---

## 3. Bốn Nhiệm Vụ Kỹ Thuật Trọng Tâm Cho Pipeline Tầng Silver

### Nhiệm vụ 1: Tạo Khóa Chính Hợp Nhất (Global Deduplication & Identification)
Do các nền tảng có cơ chế phát sinh ID độc lập, nguy cơ xung đột khóa (Key Collision) là hoàn toàn có thể xảy ra:
* **Quy tắc tạo khóa:** Ghép định danh sàn với ID nguyên bản:  
  `unified_job_id = CONCAT(LOWER(platform), '_', TRIM(original_job_id))`
* **Kiểm tra trùng lặp đa sàn:** Cùng một công việc có thể được công ty đăng tải đồng thời trên cả ITViec và TopCV. PySpark áp dụng thuật toán so khớp dựa trên bộ ba thuộc tính `(company_name, job_title, city)` kết hợp với độ tương đồng nội dung văn bản (Cosine Similarity trên Vector embeddings của JD) để đánh dấu các bản ghi trùng lặp liên sàn.

### Nhiệm vụ 2: Chuẩn Hóa Thời Gian Tuyển Dụng (Temporal Normalization)
Đưa toàn bộ biểu diễn thời gian phân mảnh về chuẩn ISO `YYYY-MM-DD`:
* **Định dạng chuẩn:** Chuyển đổi các chuỗi ngày tháng dạng `DD/MM/YYYY`, `YYYY-MM-DD` trực tiếp sang kiểu `DATE`.
* **Định dạng thời gian đếm lùi:** TopDev hiển thị `expires_in: "15 ngày nữa"` $\rightarrow$ `expired_date = crawled_at + INTERVAL 15 DAYS`.
* **Định dạng thời gian tương đối:** VietnamWorks hiển thị `posted_time_ago: "Đăng 3 ngày trước"` $\rightarrow$ `posted_date = crawled_at - INTERVAL 3 DAYS`.

### Nhiệm vụ 3: Phân Rã & Quy Đổi Tỷ Giá Mức Lương (Salary Normalization & FX Conversion)
Cột `salary_raw` có cấu trúc văn bản không đồng nhất. Quy trình xử lý chia làm hai giai đoạn:
1. **Phân tích cú pháp (Regex/LLM Parsing):** 
   Nhận diện đơn vị tiền tệ (VNĐ, USD), khoảng giá trị (Min, Max) hoặc trạng thái "Thương lượng".
2. **Quy đổi ngoại tệ:**
   * Nếu mức lương sử dụng USD, áp dụng tỷ giá tham chiếu thị trường để quy đổi trực tiếp về đơn vị VNĐ:
     $$\text{salary\_min\_vnd} = \text{salary\_min\_usd} \times \text{FX\_USD\_VND}$$
   * Lưu trữ song song cận dưới (`salary_min_vnd`) và cận trên (`salary_max_vnd`) dưới dạng số nguyên (`BIGINT`) để phục vụ tính toán các chỉ số trung vị (Median) và trung bình (Average) ở tầng Gold.

### Nhiệm vụ 4: Chuẩn Hóa Thực Thể Công Nghệ (Tech Stack Canonicalization)
Các tin tuyển dụng thường ghi nhận kỹ năng dưới nhiều biến thể (Synonyms):
* **Vấn đề phân mảnh:** `React`, `ReactJS`, `React.js` đều đại diện cho cùng một thư viện frontend.
* **Giải pháp Tầng Silver:**
  * Sử dụng API LLM kết hợp từ điển ánh xạ (Skill Taxonomy Dictionary) để quy nạp các biến thể về một danh pháp chuẩn duy nhất.
  * Xuất kết quả dưới dạng mảng chuỗi `ARRAY<STRING>` (`skills_standardized`), loại bỏ các kỹ năng mềm chung chung (ví dụ: "giao tiếp tốt", "chịu áp lực") để giữ lại thuần túy Tech Stack kỹ thuật phục vụ bảng cầu kỹ năng (`mart_skill_demands`).

---

## 4. Cấu Trúc Bảng Delta Tầng Silver (DDL Định Danh Cho PySpark)

```sql
CREATE TABLE IF NOT EXISTS silver.job_postings (
    unified_job_id STRING NOT NULL,
    platform STRING NOT NULL,
    original_job_id STRING NOT NULL,
    job_url STRING,
    content_hash STRING,
    crawled_at TIMESTAMP,
    
    -- Thông tin công việc & Vị trí
    job_title STRING NOT NULL,
    job_level STRING,
    job_expertise STRING,
    job_domain STRING,
    work_mode STRING,
    working_time STRING,
    overtime_policy STRING,
    
    -- Thông tin doanh nghiệp
    company_name STRING NOT NULL,
    company_size STRING,
    company_industry STRING,
    company_type STRING,
    company_country STRING,
    
    -- Địa bàn hoạt động
    city STRING,
    full_address STRING,
    
    -- Đãi ngộ & Định lượng
    salary_raw STRING,
    salary_min_vnd BIGINT,
    salary_max_vnd BIGINT,
    is_negotiable BOOLEAN,
    exp_years_min DOUBLE,
    exp_years_max DOUBLE,
    vacancies_count INT,
    
    -- Tương tác thị trường
    views_count INT,
    applications_count INT,
    
    -- Chu kỳ tin tuyển dụng
    posted_date DATE,
    expired_date DATE,
    
    -- Nội dung văn bản & Kỹ năng đã xử lý
    skills_raw ARRAY<STRING>,
    skills_standardized ARRAY<STRING>,
    job_description_clean STRING,
    requirements_clean STRING,
    benefits_clean STRING,
    
    -- Lưu trữ mở rộng
    platform_specific_attributes STRING
)
USING DELTA
LOCATION 's3a://silver/job_postings/'
PARTITIONED BY (platform, posted_date);