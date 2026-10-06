"""
================================================================================
RÀ SOÁT CUỐI TUẦN & THEO DÕI BIẾN ĐỘNG TIN (WEEKLY AUDIT & CDC) - TOPCV
================================================================================
Mục đích:
  - Chạy định kỳ vào mỗi Chủ Nhật hàng tuần trên nền tảng TopCV.
  - Sử dụng curl_cffi kiểm tra lại toàn bộ tin đang mở (status != 'CLOSED') trong
    `topcv_crawled_history.json`:
      1. Nếu phát hiện tin ĐÃ ĐÓNG / HẾT HẠN (HTTP 404/410, "Hết hạn ứng tuyển"):
         -> CẬP NHẬT NGẦM `"status": "CLOSED"` vào `topcv_crawled_history.json`.
         -> KHÔNG sinh file thừa.
      2. Nếu phát hiện tin CÓ BIẾN ĐỘNG (content_hash mới != content_hash cũ):
         -> Cào lại toàn bộ thông tin mới (lương, JD, yêu cầu vừa được điều chỉnh).
         -> Ghi vào ĐÚNG 1 FILE DUY NHẤT: `topcv_updated_jobs_{YYYYMMDD}.jsonl`.
         -> Cập nhật mã hash mới và `"status": "UPDATED"` vào `topcv_crawled_history.json`.
      3. Báo cáo tổng kết: In trực tiếp bảng thống kê ra màn hình Terminal (không sinh file thừa).
  - Hoàn toàn KHÔNG sử dụng CSV (100% chuẩn JSON/JSONL).
================================================================================
"""

import os
import re
import sys
import json
import time
import random
import hashlib
from datetime import datetime
from bs4 import BeautifulSoup

# Cấu hình UTF-8 cho console Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

try:
    from curl_cffi import requests
    HAVE_CURL_CFFI = True
except ImportError:
    import requests
    HAVE_CURL_CFFI = False

# ==============================================================================
# CẤU HÌNH ĐƯỜNG DẪN & THAM SỐ
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(BASE_DIR, "topcv_cookie.txt")
HISTORY_FILE = os.path.join(BASE_DIR, "topcv_crawled_history.json")

REQUEST_DELAY = 1.3  # Nghỉ an toàn giữa mỗi request

def load_cookie():
    """Nạp cookie từ file topcv_cookie.txt"""
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""

def get_headers():
    return {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
        'Cookie': load_cookie(),
        'Referer': 'https://www.topcv.vn/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    }

def load_history():
    """Nạp lịch sử từ file topcv_crawled_history.json"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_history(history):
    """Cập nhật ngầm trạng thái vào file topcv_crawled_history.json"""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def make_request(url, headers, retries=3):
    for attempt in range(1, retries + 1):
        try:
            if HAVE_CURL_CFFI:
                resp = requests.get(url, headers=headers, impersonate="chrome124", timeout=18)
            else:
                resp = requests.get(url, headers=headers, timeout=18)

            if resp.status_code == 200:
                return resp.status_code, resp.text
            elif resp.status_code == 429:
                time.sleep(attempt * 5 + random.uniform(2, 4))
            elif resp.status_code in [404, 410]:
                return resp.status_code, ""
            else:
                time.sleep(attempt * 2)
        except Exception:
            time.sleep(attempt * 2)
    return 0, ""

def compute_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def is_job_closed(status_code, html_text):
    if status_code in [404, 410]:
        return True
    if not html_text:
        return True
    closed_keywords = [
        "hết hạn ứng tuyển",
        "tin tuyển dụng này đã hết hạn",
        "việc làm này đã dừng nhận hồ sơ",
        "công việc đã đóng",
        "job has expired",
        "tin tuyển dụng không tồn tại"
    ]
    lower = html_text.lower()
    return any(kw in lower for kw in closed_keywords)

def clean_single_line(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', str(text)).strip()

def clean_html_content(tag):
    if not tag:
        return ""
    for elem in tag.find_all(['li']):
        elem.insert_before('\n* ')
    for elem in tag.find_all(['br', 'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        elem.insert_before('\n')
    text = tag.get_text(separator=' ')
    raw_lines = text.split('\n')
    formatted = []
    for line in raw_lines:
        c = clean_single_line(line)
        if c:
            if c.startswith('*'):
                formatted.append(re.sub(r'^\*\s*', '- ', c))
            else:
                formatted.append(c)
    return '\n'.join(formatted).strip()

def parse_topcv_job(html, source_url):
    soup = BeautifulSoup(html, 'html.parser')
    title_elem = soup.find('h1', class_='job-detail__info--title') or soup.find('h1')
    job_title = clean_single_line(title_elem.text) if title_elem else ""

    salary = ""
    job_location = ""
    experience = ""
    deadline = ""
    for sec in soup.find_all('div', class_='job-detail__info--section-content-item'):
        lbl_elem = sec.find('div', class_='job-detail__info--section-content-item-label')
        val_elem = sec.find('div', class_='job-detail__info--section-content-item-value')
        if not lbl_elem or not val_elem:
            continue
        lbl = lbl_elem.text.strip().lower()
        val = clean_single_line(val_elem.text)
        if any(kw in lbl for kw in ['mức lương', 'salary']):
            salary = val
        elif any(kw in lbl for kw in ['địa điểm', 'location']):
            job_location = val
        elif any(kw in lbl for kw in ['kinh nghiệm', 'experience']):
            experience = val
        elif any(kw in lbl for kw in ['hạn nộp', 'deadline']):
            deadline = val

    overview_requirements = []
    overview_skills = []
    tags_container = soup.find('div', class_='job-tags')
    if tags_container:
        for tag in tags_container.find_all('a', class_='item'):
            tag_name = clean_single_line(tag.text)
            if not tag_name:
                continue
            href = tag.get('href', '')
            if 'keyword=' in href:
                overview_skills.append(tag_name)
            else:
                overview_requirements.append(tag_name)

    job_description = ""
    DESC_KWS = ['mô tả công việc', 'job description', 'mô tả', 'description']
    for item in soup.find_all('div', class_='box-job-information-detail-item'):
        h = item.find(['h2', 'h3'])
        if not h:
            continue
        if any(kw in h.text.strip().lower() for kw in DESC_KWS) and not item.find('div', class_='job-tags'):
            text_elem = item.find('div', class_='box-job-information-detail-item__text') or item
            job_description = clean_html_content(text_elem)
            break

    candidate_requirements = ""
    industry_knowledge = []
    required_skills = []
    CAND_KWS = ['yêu cầu ứng viên', 'candidate requirements', 'candidate requirement', 'yêu cầu']
    req_box = soup.find('div', class_='box-job-information-required-candidate')
    if not req_box:
        for item in soup.find_all('div', class_='box-job-information-detail-item'):
            h = item.find(['h2', 'h3'])
            if h and any(kw in h.text.strip().lower() for kw in CAND_KWS) and not item.find('div', class_='job-tags'):
                req_box = item
                break
    if req_box:
        text_elem = req_box.find('div', class_='box-job-information-detail-item__text')
        candidate_requirements = clean_html_content(text_elem) if text_elem else ""
        for tag_block in req_box.find_all('div', class_='required-tag__content'):
            t_title = tag_block.find(['h3', 'div'], class_='required-tag__content--title')
            t_text = t_title.text.strip().lower() if t_title else ""
            raw_val = clean_single_line(tag_block.get_text(separator=' ', strip=True).replace(t_title.text if t_title else '', ''))
            if 'kiến thức ngành' in t_text or 'industry' in t_text:
                industry_knowledge.append(raw_val)
            elif 'kỹ năng cần có' in t_text or 'skill' in t_text:
                required_skills.append(raw_val)

    benefits = ""
    ben_box = soup.find('div', class_='box-job-information-benefit')
    if not ben_box:
        for item in soup.find_all('div', class_='box-job-information-detail-item'):
            h = item.find(['h2', 'h3'])
            if h and any(kw in h.text.strip().lower() for kw in ['quyền lợi', 'benefits', 'benefit']):
                ben_box = item
                break
    if ben_box:
        text_elem = ben_box.find('div', class_='box-job-information-detail-item__text')
        benefits = clean_html_content(text_elem) if text_elem else ""

    work_address = ""
    work_time = ""
    addr_box = soup.find('div', class_='box-job-information-address-and-time')
    if addr_box:
        for it in addr_box.find_all('div', class_='box-job-information-address-and-time-list__item'):
            h3 = it.find('h3')
            if not h3:
                continue
            h3_title = h3.text.strip().lower()
            val_div = it.find('div', class_='box-job-information-address-and-time-list__item--content')
            val_text = clean_html_content(val_div) if val_div else clean_html_content(it).replace(h3.text.strip(), '').strip()
            if any(kw in h3_title for kw in ['địa điểm làm việc', 'location', 'work location', 'địa điểm']):
                work_address = val_text
            elif any(kw in h3_title for kw in ['thời gian làm việc', 'work schedule', 'working hours', 'schedule', 'thời gian']):
                work_time = val_text

    company_name = ""
    company_size = ""
    company_address = ""
    company_industry = ""
    c_box = soup.find('div', class_='box-company-info-detail')
    if c_box:
        name_elem = (c_box.find('div', class_='company-name-label')
                     or c_box.find('a', class_='name')
                     or c_box.find('h2', class_=re.compile(r'company'))
                     or c_box.find('p', class_=re.compile(r'name')))
        company_name = clean_single_line(name_elem.text) if name_elem else ""
        for it in c_box.find_all('div', class_='box-company-info-detail__list--item'):
            full_text = clean_single_line(it.text)
            lt = full_text.lower()
            if any(kw in lt for kw in ['quy mô', 'company size', 'size']):
                company_size = clean_single_line(re.sub(r'(?:quy mô|company size|size):\s*', '', full_text, flags=re.I))
            elif any(kw in lt for kw in ['địa điểm', 'location', 'address']):
                company_address = clean_single_line(re.sub(r'(?:địa điểm|location|address):\s*', '', full_text, flags=re.I))
            elif any(kw in lt for kw in ['ngành', 'industry', 'field']):
                company_industry = clean_single_line(re.sub(r'(?:ngành nghề|industry|field):\s*', '', full_text, flags=re.I))

    job_level = ""
    education = ""
    quantity = ""
    work_mode = ""
    contract_type = ""
    gen_box = soup.find('div', class_='box-job-information-general-info')
    if gen_box:
        for it in gen_box.find_all('div', class_='box-job-information-general-info-list__item'):
            title_div = it.find('div', class_='box-job-information-general-info-list__item--content-title')
            desc_div = it.find('div', class_='box-job-information-general-info-list__item--content-desc')
            if title_div and desc_div:
                t = title_div.text.strip().lower()
                d = clean_single_line(desc_div.text)
                if any(kw in t for kw in ['cấp bậc', 'level', 'position']):
                    job_level = d
                elif any(kw in t for kw in ['học vấn', 'education', 'degree']):
                    education = d
                elif any(kw in t for kw in ['số lượng', 'quantity', 'headcount']):
                    quantity = d
                elif any(kw in t for kw in ['hình thức', 'work type', 'working form']):
                    work_mode = d
                elif any(kw in t for kw in ['loại hợp đồng', 'contract type', 'contract']):
                    contract_type = d

    jid_m = re.search(r'/(\d+)\.html', source_url)
    jid = jid_m.group(1) if jid_m else source_url.split('/')[-1]
    hash_str = f"{job_title}|{company_name}|{salary}|{job_description}|{candidate_requirements}|{benefits}"
    content_hash = compute_hash(hash_str)

    return {
        "platform": "TopCV",
        "job_id": jid,
        "job_title": job_title,
        "company_name": company_name,
        "salary": salary,
        "job_location": job_location,
        "experience": experience,
        "deadline": deadline,
        "job_level": job_level,
        "education": education,
        "quantity": quantity,
        "work_mode": work_mode,
        "contract_type": contract_type,
        "overview_requirements": " | ".join(overview_requirements),
        "overview_skills": " | ".join(overview_skills),
        "job_description": job_description,
        "candidate_requirements": candidate_requirements,
        "industry_knowledge": " | ".join(industry_knowledge),
        "required_skills": " | ".join(required_skills),
        "benefits": benefits,
        "work_address": work_address,
        "work_time": work_time,
        "company_size": company_size,
        "company_address": company_address,
        "company_industry": company_industry,
        "job_url": source_url,
        "content_hash": content_hash,
        "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

# ==============================================================================
# HÀM CHÍNH: RÀ SOÁT CUỐI TUẦN TOPCV
# ==============================================================================
def main(limit=None):
    start_time = time.time()
    today_str = datetime.now().strftime("%Y%m%d")
    updated_file = os.path.join(BASE_DIR, f"topcv_updated_jobs_{today_str}.jsonl")

    headers = get_headers()
    history = load_history()
    if not history:
        print("[-] Không tìm thấy lịch sử trong topcv_crawled_history.json để rà soát.")
        return

    active_jobs = [
        (jid, data) for jid, data in history.items() 
        if data.get('status', 'ACTIVE') != 'CLOSED'
    ]

    if limit and isinstance(limit, int):
        active_jobs = active_jobs[:limit]

    total = len(active_jobs)
    print("=" * 80)
    print("🔎 CHƯƠNG TRÌNH RÀ SOÁT CUỐI TUẦN & THEO DÕI BIẾN ĐỘNG TIN (TOPCV)")
    print(f"   - Lịch chạy                 : Mỗi Chủ Nhật hàng tuần")
    print(f"   - Tổng số tin trong lịch sử : {len(history):,} tin")
    print(f"   - Số tin active cần rà soát : {total:,} tin")
    print(f"   - Thời điểm thực hiện       : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80 + "\n")

    count_active_unchanged = 0
    count_updated = 0
    count_closed = 0
    count_error = 0

    updated_records = []

    for idx, (jid, info) in enumerate(active_jobs, 1):
        url = info.get('url', f"https://www.topcv.vn/viec-lam/{jid}.html")
        old_hash = info.get('content_hash', '')
        title = info.get('job_title', 'Unknown Title')

        percent = (idx / total) * 100
        print(f"[{idx:4d}/{total:4d}] ({percent:5.1f}%) Kiểm tra Job[{jid}]: {title[:35]}...", end=" ", flush=True)

        status_code, html = make_request(url, headers)

        # 1. KIỂM TRA TIN ĐÃ ĐÓNG / HẾT HẠN
        if is_job_closed(status_code, html):
            print("[🔴 ĐÃ ĐÓNG / HẾT HẠN]")
            count_closed += 1
            # Cập nhật ngầm vào history (không xuất file thừa)
            history[jid]['status'] = 'CLOSED'
            history[jid]['closed_detected_at'] = datetime.now().strftime("%Y-%m-%d")
            history[jid]['last_audited_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 2. TIN VẪN HOẠT ĐỘNG -> KIỂM TRA BIẾN ĐỘNG NỘI DUNG (CDC)
        elif status_code == 200:
            item = parse_topcv_job(html, source_url=url)
            new_hash = item['content_hash']
            history[jid]['last_audited_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if old_hash and new_hash != old_hash:
                print("[🟡 CÓ BIẾN ĐỘNG / UPDATED!]")
                count_updated += 1
                # Cập nhật ngầm vào history
                history[jid]['status'] = 'UPDATED'
                history[jid]['content_hash'] = new_hash
                history[jid]['updated_at'] = datetime.now().strftime("%Y-%m-%d")

                # Ghi nhận bản ghi chi tiết mới
                updated_records.append(item)
            else:
                print("[🟢 VẪN HOẠT ĐỘNG / KHÔNG ĐỔI]")
                count_active_unchanged += 1
                history[jid]['status'] = 'ACTIVE'
        else:
            print(f"[⚠️ HTTP {status_code}]")
            count_error += 1

        time.sleep(REQUEST_DELAY + random.uniform(0.2, 0.5))

    # Lưu lại lịch sử cập nhật ngầm
    save_history(history)

    # Nếu có tin thay đổi: Ghi vào ĐÚNG 1 FILE DUY NHẤT
    if updated_records:
        with open(updated_file, 'a', encoding='utf-8') as f:
            for r in updated_records:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')

    elapsed = time.time() - start_time
    print("\n" + "=" * 80)
    print("📊 BÁO CÁO TỔNG KẾT RÀ SOÁT CUỐI TUẦN (TOPCV):")
    print(f"   - Tổng số tin đã rà soát     : {total:,} tin")
    print(f"   - Tin vẫn mở & không đổi (🟢): {count_active_unchanged:,} tin")
    print(f"   - Tin có sửa JD/Lương    (🟡): {count_updated:,} tin")
    print(f"   - Tin đã đóng tuyển dụng (🔴): {count_closed:,} tin (Đã cập nhật ngầm vào history)")
    print(f"   - Tin gặp lỗi kết nối        : {count_error:,} tin")
    print(f"   - Tổng thời gian thực hiện   : {elapsed:.2f} giây")
    if updated_records:
        print(f"📁 1 File duy nhất cào về các tin sửa: {os.path.basename(updated_file)}")
    else:
        print("🎉 Không có tin nào bị sửa nội dung trong tuần qua!")
    print("=" * 80 + "\n")

if __name__ == '__main__':
    # Mặc định quét toàn bộ. Để test nhanh 5 tin: main(limit=5)
    main()
