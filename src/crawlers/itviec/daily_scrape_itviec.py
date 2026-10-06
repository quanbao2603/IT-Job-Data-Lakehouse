"""
================================================================================
CÀO DỮ LIỆU TĂNG TRƯỞNG HÀNG NGÀY (DAILY INCREMENTAL SCRAPER) - ITVIEC
================================================================================
Mục đích:
  - Chạy tự động hàng ngày (từ Thứ 2 đến Chủ Nhật) để cào các tin việc làm IT mới nhất.
  - Áp dụng cơ chế Dừng Sớm (Early Stopping): Quét từ trang 1, nếu gặp liên tiếp
    các tin đã có trong `itviec_crawled_history.json`, bot tự động dừng quét.
  - Xử lý thông minh tin RE-OPEN:
    + Nếu tin chưa từng có trong lịch sử -> Cào mới tinh!
    + Nếu tin đã có trong lịch sử nhưng trước đó mang trạng thái "CLOSED" -> Phát hiện
      tin được mở lại (RE-OPEN) -> Cào lại và cập nhật lại trạng thái "ACTIVE"!
  - Toàn bộ dữ liệu mới/re-open được ghi vào 1 file duy nhất của ngày:
    `itviec_daily_jobs_{YYYYMMDD}.jsonl`
  - Cập nhật ngầm vào `itviec_crawled_history.json`.
  - KHÔNG sử dụng CSV (100% chuẩn JSON/JSONL).
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

# Ngưỡng dừng sớm: Gặp liên tiếp 15 tin cũ đang ACTIVE thì dừng quét
CONSECUTIVE_OLD_THRESHOLD = 15
MAX_SEARCH_PAGES = 5  # Quét tối đa 5 trang đầu (khoảng 100 tin mới nhất mỗi ngày)
REQUEST_DELAY = 1.3   # Nghỉ giữa mỗi tin chi tiết (giây)

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9,vi;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Referer': 'https://itviec.com/'
}

def load_cookie():
    """Nạp cookie từ itviec_cookie.txt hoặc fallback cookie.txt"""
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
    """Nạp lịch sử từ itviec_crawled_history.json hoặc fallback crawled_history.json"""
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

# ==============================================================================
# HÀM BÓC TÁCH CHI TIẾT TIN TUYỂN DỤNG ITVIEC
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
# QUÉT TĂNG TRƯỞNG & XỬ LÝ RE-OPEN (EARLY STOPPING)
# ==============================================================================
def discover_new_and_reopened_jobs(headers, history):
    """
    Quét từ trang 1 của ITViec:
      - jid chưa có trong history: Tin mới tinh -> Cào!
      - jid đã có nhưng status == 'CLOSED': Tin Re-open -> Cào lại và cập nhật ACTIVE!
      - jid đã có và status == 'ACTIVE': Tin cũ bình thường -> Tăng biến đếm dừng sớm.
    """
    candidates = []
    seen_urls = set()
    consecutive_active_old = 0
    stopped_early = False

    print("\n" + "=" * 75)
    print("⚡ BẮT ĐẦU QUÉT TĂNG TRƯỞNG TIN MỚI & RE-OPEN (ITVIEC)")
    print(f"   - Lịch chạy: Hàng ngày (Thứ 2 đến Chủ Nhật)")
    print(f"   - Ngưỡng dừng sớm: {CONSECUTIVE_OLD_THRESHOLD} tin cũ đang active liên tiếp")
    print(f"   - Giới hạn quét tối đa: {MAX_SEARCH_PAGES} trang")
    print("=" * 75)

    for page in range(1, MAX_SEARCH_PAGES + 1):
        page_url = f"https://itviec.com/it-jobs?page={page}"
        print(f"  [+] Đang quét Trang {page}...", end=" ", flush=True)

        try:
            resp = requests.get(page_url, headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"[HTTP {resp.status_code} - Dừng phân trang]")
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            links = soup.find_all('a', href=re.compile(r'/it-jobs/[a-zA-Z0-9\-]+-\d+'))

            page_new_count = 0
            page_reopen_count = 0
            page_old_count = 0

            for a in links:
                href = a.get('href', '')
                full_url = urljoin('https://itviec.com', href.split('?')[0])
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                jid = extract_job_id(full_url)

                # Trường hợp 1: Tin mới tinh
                if jid not in history:
                    consecutive_active_old = 0
                    page_new_count += 1
                    candidates.append({"url": full_url, "type": "NEW"})

                # Trường hợp 2: Tin từng bị đóng, nay xuất hiện lại -> RE-OPEN!
                elif history[jid].get('status') == 'CLOSED':
                    consecutive_active_old = 0
                    page_reopen_count += 1
                    candidates.append({"url": full_url, "type": "REOPEN"})

                # Trường hợp 3: Tin cũ đang active bình thường
                else:
                    consecutive_active_old += 1
                    page_old_count += 1
                    if consecutive_active_old >= CONSECUTIVE_OLD_THRESHOLD:
                        stopped_early = True
                        break

            msg_parts = []
            if page_new_count > 0: msg_parts.append(f"+{page_new_count} tin mới")
            if page_reopen_count > 0: msg_parts.append(f"+{page_reopen_count} tin re-open")
            msg_parts.append(f"{page_old_count} tin cũ")
            print(" -> " + " | ".join(msg_parts))

            if stopped_early:
                print(f"\n[🛑 DỪNG SỚM] Đã gặp {CONSECUTIVE_OLD_THRESHOLD} tin cũ active liên tiếp! Dừng quét các trang sau.")
                break

            time.sleep(1.0)

        except Exception as e:
            print(f"[Lỗi quét: {e}]")
            break

    new_total = sum(1 for c in candidates if c['type'] == 'NEW')
    reopen_total = sum(1 for c in candidates if c['type'] == 'REOPEN')
    print(f"\n[KẾT QUẢ QUÉT] Phát hiện: {len(candidates)} tin cần cào ({new_total} tin mới tinh, {reopen_total} tin re-open).")
    return candidates

# ==============================================================================
# HÀM CHÍNH: CÀO TĂNG TRƯỞNG VÀ LƯU 1 FILE DUY NHẤT
# ==============================================================================
def main():
    start_time = time.time()
    today_str = datetime.now().strftime("%Y%m%d")
    daily_jsonl_file = os.path.join(BASE_DIR, f"itviec_daily_jobs_{today_str}.jsonl")

    cookie = load_cookie()
    headers = DEFAULT_HEADERS.copy()
    if cookie:
        headers['Cookie'] = cookie

    history = load_history()
    print(f"[*] Đã tải lịch sử ITViec: {len(history):,} tin đã lưu.")

    candidates = discover_new_and_reopened_jobs(headers, history)
    if not candidates:
        print("[🎉] Không có tin việc làm IT mới hoặc re-open nào hôm nay! Dữ liệu đã đồng bộ 100%.")
        return

    print(f"\n[🚀] BẮT ĐẦU CÀO CHI TIẾT {len(candidates)} TIN...")
    success_records = []

    for idx, item_info in enumerate(candidates, 1):
        url = item_info['url']
        job_type = item_info['type']
        jid = extract_job_id(url)
        tag = "[MỚI]" if job_type == 'NEW' else "[♻️ RE-OPEN]"
        print(f"  [{idx:2d}/{len(candidates):2d}] {tag} Đang cào Job[{jid}]: {url}")

        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"      [-] HTTP {resp.status_code}, bỏ qua.")
                continue

            item = parse_job_detail_raw(resp.text, url)
            success_records.append(item)

            # Cập nhật ngầm vào history
            history[jid] = {
                "url": url,
                "crawled_date": item['crawled_date'],
                "content_hash": item['content_hash'],
                "job_title": item['job_title'],
                "company_name": item['company_name'],
                "status": "ACTIVE"
            }

            print(f"      [✓] {item['job_title']} | {item['company_name']} | Lương: {item['salary']}")
            time.sleep(REQUEST_DELAY)

        except Exception as e:
            print(f"      [!] Lỗi cào tin {url}: {e}")

    if not success_records:
        print("[-] Không cào được tin nào thành công.")
        return

    # Ghi toàn bộ dữ liệu cào được vào ĐÚNG 1 FILE DUY NHẤT theo ngày
    with open(daily_jsonl_file, 'a', encoding='utf-8') as f:
        for r in success_records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    # Lưu lại lịch sử cập nhật ngầm
    save_history(history)

    elapsed = time.time() - start_time
    print("\n" + "=" * 75)
    print("✅ HOÀN TẤT CÀO TĂNG TRƯỞNG ITVIEC HÔM NAY:")
    print(f"   - Số tin cào thành công      : {len(success_records)} tin")
    print(f"   - File dữ liệu tăng trưởng ngày: {os.path.basename(daily_jsonl_file)}")
    print(f"   - Tổng kho tích lũy trong history: {len(history):,} tin")
    print(f"   - Thời gian thực hiện          : {elapsed:.2f} giây")
    print("=" * 75)

if __name__ == '__main__':
    main()
