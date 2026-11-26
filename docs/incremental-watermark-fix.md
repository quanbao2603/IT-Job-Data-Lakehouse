# Vì sao dashboard thiếu tin mới dù crawler báo SUCCESS

Ngày 26/11/2026, sau khi kiểm tra toàn diện, phát hiện 4 nguồn chạy "thành công" nhưng ghi 0–1 bản ghi
mỗi ngày: itviec, careerviet, topdev, vietnamdevs. Devwork thì dừng hẳn từ 14/11.

## Nguyên nhân

Mọi crawler danh sách đều dừng **toàn bộ** vòng quét ngay khi gặp thẻ tin đầu tiên trùng
`last_record_id` (mốc đã cào lần trước) hoặc có ngày cũ hơn watermark:

```python
if last_record_id and str(jid) == str(last_record_id):
    watermark_hit = True
    break            # ← dừng cả crawl
```

Cách này chỉ đúng khi sàn sắp xếp **mới → cũ**. Thực tế cả 7 sàn đều đẩy tin trả phí / tin nổi bật
lên đầu trang 1, nên thẻ mốc nằm ngay đầu danh sách:

- vòng quét dừng ở trang 1, sau 1–2 thẻ
- toàn bộ tin đăng trong ngày bị bỏ qua
- run vẫn kết thúc `SUCCESS` với `records_ingested = 0` → không có cảnh báo nào

Bằng chứng rõ nhất là itviec: run 25/11 ghi **0 bản ghi**, trong khi cào full trang 1 thấy **21 tin**,
trong đó **3 tin đăng 26/11** và **6 tin đăng 25/11**.

## Cách sửa

1. Thẻ mốc chỉ bị **bỏ qua riêng nó** (`continue`), không kết thúc vòng quét.
2. Mỗi run incremental chỉ quét một cửa sổ trang giới hạn — `incremental_scan_pages()` trong
   `src/crawlers/base_crawler.py`, mặc định **2 trang**, đổi bằng `LAKEHOUSE_INCREMENTAL_SCAN_PAGES`.
3. Dừng khi **cả một trang không có tin mới** (`collected_this_page == 0`), tức đã ra khỏi vùng tin mới.
4. Thẻ không có ngày vẫn được giữ: có thể là tin mới nhất trong ngày, và bản ghi trùng rẻ hơn tin bị mất
   (Silver ghi đè observation theo `content_hash`).

Áp dụng cho: `itviec`, `careerviet`, `topdev`, `vietnamdevs`, `devwork`, `upzi`, `topcv`, `vieclam24h`.
`vietnamworks` giữ nguyên vì API của họ sắp xếp tường minh `approvedOn desc` nên dừng sớm là đúng.

## Nhịp TopCV

Đo lại trên production: 2–2,5s/tin bị 429 sau ~20 request; 6–8s sau ~6 request; **15–19s chạy 425
request liên tiếp không bị chặn**. Nhịp an toàn này trở thành mặc định của crawler
(`TOPCV_DELAY_MIN/MAX`), không còn phụ thuộc người chạy nhớ đặt biến môi trường.

## Kết quả đo sau khi sửa (26/11)

| Nguồn | Trước | Sau |
|---|---|---|
| itviec | 0 | 43 |
| careerviet | 1 | 33 |
| topdev | 0 | 29 |
| vietnamdevs | 0 | 79 |
| devwork | 0 (12 ngày) | 120 |
| vietnamworks | — | 20 |
| upzi | — | 10 |

## Test

- `tests/test_incremental_scan_window.py` — 5 test cho cửa sổ quét (mặc định 2, không vượt ngân sách
  của caller, đổi được bằng env, env sai không crash).
- `tests/test_{itviec,careerviet,topdev,vietnamdevs,devwork,upzi,vieclam24h}_parser.py` — khẳng định
  thẻ mốc bị bỏ qua nhưng các thẻ sau nó vẫn được cào.
