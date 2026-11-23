# 10 tin TopCV không có tên công ty trong dữ liệu đã cào

Release: `silver_release_manual_facet_fix_20261123_162842` · tổng observation TopCV thiếu company: **1573**

| # | Tiêu đề | URL | company_name_raw | city | level |
|---|---|---|---|---|---|
| 1 | Nhân Viên Dịch VụQuản Trị &Vận Hành Hệ Thống | https://www.topcv.vn/viec-lam/nhan-vien-dich-vu-quan-tri-van-hanh-he-thong/1768102.html | `{{ company?.name }}` | — | Junior |
| 2 | Trưởng Phòng Ứng Dụng Di Động | https://www.topcv.vn/viec-lam/truong-phong-ung-dung-di-dong/2307001.html | `{{ company?.name }}` | — | Lead |
| 3 | Solution Consultant-Tư VấnGiải Pháp (Presales- B2B SaaS) | https://www.topcv.vn/viec-lam/solution-consultant-tu-van-giai-phap-presales-b2b-saas/2315929.html | `{{ company?.name }}` | — | — |
| 4 | Software Support Specialist | https://www.topcv.vn/viec-lam/software-support-specialist/2335659.html | `{{ company?.name }}` | — | Mid |
| 5 | Nhân Viên Kỹ Thuật Lắp ĐặtSửa Chữa Camera - Mạng TạiHồ Chí Minh | https://www.topcv.vn/viec-lam/nhan-vien-ky-thuat-lap-dat-sua-chua-camera-mang-tai-ho-chi-minh/2346501.html | `{{ company?.name }}` | — | Junior |
| 6 | Nhân ViênReactJSFront End Developer(Middle) | https://www.topcv.vn/viec-lam/nhan-vien-reactjs-front-end-developer-middle/2320500.html | `{{ company?.name }}` | — | Junior |
| 7 | JuniorBusiness Analyst(Từ 1 Năm Kinh Nghiệm, Offer 12-18tr/Tháng) | https://www.topcv.vn/viec-lam/junior-business-analyst-tu-1-nam-kinh-nghiem-offer-12-18tr-thang/2340404.html | `{{ company?.name }}` | — | — |
| 8 | Lập Trình ViênAngular- 2 Năm Kinh Nghiệm -Đống Đa,Hà Nội | https://www.topcv.vn/viec-lam/lap-trinh-vien-angular-2-nam-kinh-nghiem-dong-da-ha-noi/2334876.html | `{{ company?.name }}` | — | — |
| 9 | Trưởng Phòng CNTT | https://www.topcv.vn/viec-lam/truong-phong-cntt/2296439.html | `{{ company?.name }}` | — | Lead |
| 10 | Trưởng NhómTester | https://www.topcv.vn/viec-lam/truong-nhom-tester/2336650.html | `{{ company?.name }}` | — | Lead |

Mở URL bằng trình duyệt sẽ thấy TopCV hiển thị tên công ty. Lỗi nằm ở parser: nó đọc
`div.company-name` (node template Angular) thay vì
`.box-company-info-detail .company-name-label a.name`, nên Bronze lưu `{{ company?.name }}`.
Đã sửa ở crawler (`1a190d6`); các bản ghi cũ cần crawl lại vì Bronze không lưu HTML gốc.
