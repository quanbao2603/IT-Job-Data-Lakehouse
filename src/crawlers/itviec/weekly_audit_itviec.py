"""
================================================================================
RÀ SOÁT CUỐI TUẦN & THEO DÕI BIẾN ĐỘNG TIN (WEEKLY AUDIT & CDC) - ITVIEC
================================================================================
Mục đích:
  - Chạy định kỳ vào mỗi Chủ Nhật hàng tuần.
  - Rà soát lại toàn bộ các tin tuyển dụng đang mở (status != 'CLOSED') trong
    `itviec_crawled_history.json`:
      1. Nếu phát hiện tin ĐÃ ĐÓNG / HẾT HẠN (HTTP 404/410 hoặc thông báo hết hạn):
         -> CẬP NHẬT NGẦM `"status": "CLOSED"` vào `itviec_crawled_history.json`.
         -> KHÔNG sinh file thừa.
      2. Nếu phát hiện tin CÓ BIẾN ĐỘNG (content_hash mới != content_hash cũ):
         -> Cào lại toàn bộ thông tin mới (lương, JD, yêu cầu vừa được sửa).
         -> Ghi vào ĐÚNG 1 FILE DUY NHẤT: `itviec_updated_jobs_{YYYYMMDD}.jsonl`.
         -> Cập nhật mã hash mới và `"status": "UPDATED"` vào `itviec_crawled_history.json`.
      3. Báo cáo tổng kết: In trực tiếp bảng thống kê ra màn hình Terminal (không sinh file thừa).
  - Hoàn toàn KHÔNG sử dụng CSV (100% chuẩn JSON/JSONL).
================================================================================
"""

import os
import sys
import re
import json
import time
import hashlib
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# Cấu hình UTF-8 cho terminal Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ==============================================================================
# CẤU HÌNH ĐƯỜNG DẪN & THAM SỐ
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(BASE_DIR, "itviec_cookie.txt")
HISTORY_FILE = os.path.join(BASE_DIR, "itviec_crawled_history.json")

REQUEST_DELAY = 1.0  # Nghỉ giữa mỗi lần kiểm tra tin (giây)

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9,vi;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Referer': 'https://itviec.com/'
}

def load_cookie():
    target_files = [COOKIE_FILE, os.path.join(BASE_DIR, "cookie.txt")]
    for cf in target_files:
        if os.path.exists(cf):
            try:
                with open(cf, 'r', encoding='utf-8') as f:
                    lines = [l.strip() for l in f.readlines() if l.strip() and not l.startswith('#')]
                    if lines:
                        return '; '.join(lines)
            except Exception:
                pass
    return ""

def compute_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def extract_job_id(url):
    m = re.search(r'-(\d+)(?:\?.*)?$', url)
    if m:
        return m.group(1)
    return url.split('/')[-1].split('?')[0]

def load_history():
    target_files = [HISTORY_FILE, os.path.join(BASE_DIR, "crawled_history.json")]
    for hf in target_files:
        if os.path.exists(hf):
            try:
                with open(hf, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
    return {}

def save_history(history):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def is_job_closed(response_status, html_text):
    """Xác định xem tin tuyển dụng đã đóng/hết hạn hay chưa"""
    if response_status in [404, 410]:
        return True
    
    closed_keywords = [
        "việc làm này đã hết hạn",
        "tin tuyển dụng này đã đóng",
        "job is no longer available",
        "this job has expired",
        "công việc này đã dừng tuyển dụng"
    ]
    lower_text = html_text.lower()
    return any(kw in lower_text for kw in closed_keywords)

# ==============================================================================
# HÀM BÓC TÁCH CHI TIẾT KHI CẦN CẬP NHẬT DỮ LIỆU MỚI
# ==============================================================================
def parse_job_detail_raw(html_content, job_url):
    soup = BeautifulSoup(html_content, 'html.parser')
    
    header = soup.find('div', class_='job-show-header') or soup
    h1 = header.find('h1')
    job_title = h1.get_text().strip() if h1 else ""
    
    company_name = ""
    emp_elem = header.find('div', class_='employer-name') or header.find('a', class_='employer-name')
    if emp_elem:
        company_name = emp_elem.get_text().strip()
    elif h1:
        next_elem = h1.find_next_sibling()
        if next_elem and 'salary' not in (next_elem.get('class') or []):
            company_name = next_elem.get_text().strip()

    salary = ""
    salary_elem = header.find('div', class_=lambda c: c and ('salary' in str(c).lower() or 'text-success' in str(c).lower())) or header.find(class_=lambda c: c and 'salary' in str(c).lower())
    if salary_elem:
        salary = salary_elem.get_text(' ', strip=True)
    else:
        for tag in header.find_all(['div', 'span', 'p', 'a']):
            t = tag.get_text(' ', strip=True)
            if any(k in t.lower() for k in ['sign in to view salary', 'usd', 'vnd', 'thương lượng', 'negotiable', '$']) and len(t) < 60:
                salary = t
                break

    job_info = soup.find('div', class_='job-show-info')
    address = ""
    work_mode = ""
    posted_date = ""
    skills = []
    job_expertise = ""
    job_domain = ""

    if job_info:
        info_text = job_info.get_text('\n', strip=True)

        map_link = job_info.find('a', href=re.compile(r'google\.com/maps|maps', re.I))
        if map_link and map_link.parent:
            address = map_link.parent.get_text().strip()
        else:
            addr_elem = job_info.find(class_=lambda c: c and 'address' in c)
            if addr_elem:
                address = addr_elem.get_text().strip()

        mode_match = re.search(r'\b(At office|Remote|Hybrid)\b', info_text, re.I)
        if mode_match:
            work_mode = mode_match.group(1)

        posted_match = re.search(r'Posted\s+([^\n\r]+)', info_text, re.I)
        if posted_match:
            posted_date = 'Posted ' + posted_match.group(1).strip()

        for a in job_info.find_all('a', href=re.compile(r'/it-jobs/')):
            skill_text = a.get_text().strip()
            if skill_text and skill_text not in skills and len(skill_text) < 40:
                skills.append(skill_text)

        lines = [l.strip() for l in info_text.split('\n') if l.strip()]
        for i, l in enumerate(lines):
            if any(k in l.lower() for k in ['expertise', 'chuyên môn']) and i + 1 < len(lines):
                job_expertise = lines[i+1]
            if any(k in l.lower() for k in ['domain', 'lĩnh vực']) and i + 1 < len(lines):
                job_domain = lines[i+1]

    def extract_section_text(title_keywords):
        for header_tag in soup.find_all(['h2', 'h3', 'h4', 'div']):
            htext = header_tag.get_text().strip().lower()
            if any(kw in htext for kw in title_keywords):
                container = header_tag.find_next_sibling()
                if not container:
                    container = header_tag.parent.find_next_sibling()
                if container:
                    return container.get_text('\n', strip=True)
        return ""

    job_description = extract_section_text(['top 3 reasons', 'mô tả công việc', 'job description', 'responsibilities', 'trách nhiệm'])
    skills_and_experience = extract_section_text(['your skills and experience', 'yêu cầu', 'skills and experience', 'requirements'])
    why_love = extract_section_text(['why you\'ll love working here', 'lý do bạn sẽ thích', 'quyền lợi', 'benefits'])

    sidebar = soup.find('div', class_='employer-overview') or soup.find('div', class_='job-show-sidebar') or soup
    company_type = ""
    company_industry = ""
    company_size = ""
    country = ""
    working_days = ""
    overtime_policy = ""

    if sidebar:
        sb_text = sidebar.get_text('\n', strip=True)
        sb_lines = [l.strip() for l in sb_text.split('\n') if l.strip()]
        for i, l in enumerate(sb_lines):
            llow = l.lower()
            if 'company type' in llow and i + 1 < len(sb_lines):
                company_type = sb_lines[i+1]
            elif 'company industry' in llow and i + 1 < len(sb_lines):
                company_industry = sb_lines[i+1]
            elif 'company size' in llow and i + 1 < len(sb_lines):
                company_size = sb_lines[i+1]
            elif 'country' in llow and i + 1 < len(sb_lines):
                country = sb_lines[i+1]
            elif 'working days' in llow and i + 1 < len(sb_lines):
                working_days = sb_lines[i+1]
            elif 'overtime policy' in llow and i + 1 < len(sb_lines):
                overtime_policy = sb_lines[i+1]

    content_for_hash = f"{job_title}|{salary}|{job_description}|{skills_and_experience}"
    content_hash = compute_hash(content_for_hash)

    return {
        "platform": "ITViec",
        "job_id": extract_job_id(job_url),
        "crawled_date": datetime.now().strftime("%Y-%m-%d"),
        "content_hash": content_hash,
        "job_title": job_title,
        "company_name": company_name,
        "salary": salary,
        "address": address,
        "work_mode": work_mode,
        "posted_date": posted_date,
        "skills": ", ".join(skills),
        "job_expertise": job_expertise,
        "job_domain": job_domain,
        "job_description": job_description,
        "skills_and_experience": skills_and_experience,
        "why_love_working_here": why_love,
        "company_type": company_type,
        "company_industry": company_industry,
        "company_size": company_size,
        "country": country,
        "working_days": working_days,
        "overtime_policy": overtime_policy,
        "job_url": job_url
    }

# ==============================================================================
# HÀM CHÍNH: RÀ SOÁT HỆ THỐNG VÀO CHỦ NHẬT
# ==============================================================================
def main(limit=None):
    """
    Rà soát toàn bộ hoặc N tin cũ.
    limit: Số tin tối đa cần rà soát (dùng để test nhanh, None = rà soát toàn bộ).
    """
    start_time = time.time()
    today_str = datetime.now().strftime("%Y%m%d")
    updated_file = os.path.join(BASE_DIR, f"itviec_updated_jobs_{today_str}.jsonl")

    cookie = load_cookie()
    headers = DEFAULT_HEADERS.copy()
    if cookie:
        headers['Cookie'] = cookie

    history = load_history()
    if not history:
        print("[-] Không tìm thấy lịch sử trong itviec_crawled_history.json để rà soát.")
        return

    # Lọc danh sách các tin còn đang ACTIVE (chưa từng bị đóng)
    active_jobs = [
        (jid, data) for jid, data in history.items() 
        if data.get('status', 'ACTIVE') != 'CLOSED'
    ]

    if limit and isinstance(limit, int):
        active_jobs = active_jobs[:limit]

    total = len(active_jobs)
    print("=" * 80)
    print("🔎 CHƯƠNG TRÌNH RÀ SOÁT CUỐI TUẦN & THEO DÕI BIẾN ĐỘNG TIN (ITVIEC)")
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
        url = info.get('url', f"https://itviec.com/it-jobs/{jid}")
        old_hash = info.get('content_hash', '')
        title = info.get('job_title', 'Unknown Title')

        percent = (idx / total) * 100
        print(f"[{idx:4d}/{total:4d}] ({percent:5.1f}%) Kiểm tra Job[{jid}]: {title[:35]}...", end=" ", flush=True)

        try:
            resp = requests.get(url, headers=headers, timeout=12, allow_redirects=True)
            
            # 1. KIỂM TRA TIN ĐÃ ĐÓNG / HẾT HẠN
            if is_job_closed(resp.status_code, resp.text):
                print("[🔴 ĐÃ ĐÓNG / HẾT HẠN]")
                count_closed += 1
                # Cập nhật ngầm vào history (không xuất file)
                history[jid]['status'] = 'CLOSED'
                history[jid]['closed_detected_at'] = datetime.now().strftime("%Y-%m-%d")
                history[jid]['last_audited_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 2. TIN VẪN HOẠT ĐỘNG -> KIỂM TRA BIẾN ĐỘNG NỘI DUNG (CDC)
            elif resp.status_code == 200:
                item = parse_job_detail_raw(resp.text, url)
                new_hash = item['content_hash']
                history[jid]['last_audited_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if old_hash and new_hash != old_hash:
                    print("[🟡 CÓ BIẾN ĐỘNG / UPDATED!]")
                    count_updated += 1
                    # Cập nhật ngầm vào history
                    history[jid]['status'] = 'UPDATED'
                    history[jid]['content_hash'] = new_hash
                    history[jid]['updated_at'] = datetime.now().strftime("%Y-%m-%d")

                    # Ghi nhận bản ghi chi tiết mới vào danh sách cập nhật
                    updated_records.append(item)
                else:
                    print("[🟢 VẪN HOẠT ĐỘNG / KHÔNG ĐỔI]")
                    count_active_unchanged += 1
                    history[jid]['status'] = 'ACTIVE'

            else:
                print(f"[⚠️ HTTP {resp.status_code}]")
                count_error += 1

            time.sleep(REQUEST_DELAY)

        except Exception as e:
            print(f"[Lỗi: {e}]")
            count_error += 1

    # Lưu lại lịch sử cập nhật ngầm
    save_history(history)

    # Nếu có tin thay đổi: Ghi vào ĐÚNG 1 FILE DUY NHẤT
    if updated_records:
        with open(updated_file, 'a', encoding='utf-8') as f:
            for r in updated_records:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')

    elapsed = time.time() - start_time
    print("\n" + "=" * 80)
    print("📊 BÁO CÁO TỔNG KẾT RÀ SOÁT CUỐI TUẦN (ITVIEC):")
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
    # Mặc định rà soát toàn bộ tin active. Nếu muốn test nhanh 5 tin: main(limit=5)
    main()
