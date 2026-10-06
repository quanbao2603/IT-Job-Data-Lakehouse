"""
================================================================================
CÀO DỮ LIỆU TĂNG TRƯỞNG HÀNG NGÀY (DAILY INCREMENTAL SCRAPER) - TOPDEV
================================================================================
Mục đích:
  - Chạy tự động hàng ngày (từ Thứ 2 đến Chủ Nhật) để cào các tin việc làm IT mới nhất.
  - Quét trang tìm kiếm của TopDev (`https://topdev.vn/jobs/search?page={page}`).
  - Áp dụng cơ chế Dừng Sớm (Early Stopping): Khi gặp liên tiếp các tin đã có trong
    `topdev_crawled_history.json`, bot tự động dừng quét trang để tiết kiệm thời gian.
  - Xử lý thông minh tin RE-OPEN:
    + Nếu tin chưa từng có trong lịch sử -> Cào mới tinh!
    + Nếu tin đã có trong lịch sử nhưng trước đó mang trạng thái "CLOSED" -> Phát hiện
      tin được mở lại (RE-OPEN) -> Cào lại và cập nhật lại trạng thái "ACTIVE"!
  - Giải mã cấu trúc Next.js RSC Stream bóc tách trọn vẹn 21 trường dữ liệu.
  - Toàn bộ dữ liệu mới/re-open được ghi vào 1 file duy nhất của ngày:
    `topdev_daily_jobs_{YYYYMMDD}.jsonl`
  - Cập nhật ngầm vào `topdev_crawled_history.json`.
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
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup

# Cấu hình UTF-8 cho console Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ==============================================================================
# CẤU HÌNH ĐƯỜNG DẪN & THAM SỐ
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(BASE_DIR, 'topdev_cookie.txt')
HISTORY_FILE = os.path.join(BASE_DIR, 'topdev_crawled_history.json')

CONSECUTIVE_OLD_THRESHOLD = 20  # Gặp 20 tin cũ đang active liên tiếp -> Dừng sớm
MAX_SEARCH_PAGES = 5            # Quét tối đa 5 trang đầu
REQUEST_DELAY = 1.0             # Nghỉ an toàn giữa mỗi request

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept-Language': 'vi,en-US;q=0.9,en;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Referer': 'https://topdev.vn/',
}

def create_robust_session():
    session = requests.Session()
    retries = Retry(total=4, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504], raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retries)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session

def load_cookies():
    """Nạp cookie từ file topdev_cookie.txt"""
    cookies = {}
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                for item in f.read().strip().split(';'):
                    if '=' in item:
                        k, v = item.strip().split('=', 1)
                        cookies[k] = v
        except Exception:
            pass
    return cookies

def load_history():
    """Nạp lịch sử từ file topdev_crawled_history.json"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_history(history):
    """Cập nhật ngầm trạng thái vào file topdev_crawled_history.json"""
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def compute_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def extract_job_id(url):
    m = re.search(r'-(\d+)(?:\?.*)?$', url)
    return m.group(1) if m else url.split('/')[-1].split('?')[0]

def clean_html(raw_html):
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, 'html.parser')
    for li in soup.find_all('li'):
        li.insert_before('• ')
        li.insert_after('\n')
    for br in soup.find_all(['br', 'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        br.insert_after('\n')
    text = soup.get_text()
    lines = [re.sub(r'\s+', ' ', l).strip() for l in text.split('\n')]
    return '\n'.join([l for l in lines if l]).strip()

def parse_rsc_bytes(b):
    chunks = {}
    i = 0
    n = len(b)
    decoder = json.JSONDecoder()
    pattern = re.compile(rb'([0-9a-fA-F]+):')
    
    while i < n:
        while i < n and b[i:i+1] in b'\r\n \t':
            i += 1
        if i >= n:
            break
        m = pattern.match(b, i)
        if not m:
            search_m = pattern.search(b, i)
            if search_m:
                i = search_m.start()
                continue
            else:
                break
        cid = m.group(1).decode('ascii')
        i = m.end()
        if i < n and b[i:i+1] == b'T':
            comma = b.find(b',', i)
            if comma != -1:
                hex_str = b[i+1:comma].decode('ascii', errors='ignore')
                try:
                    text_len = int(hex_str, 16)
                    chunks[cid] = b[comma+1:comma+1+text_len].decode('utf-8', errors='replace')
                    i = comma + 1 + text_len
                    continue
                except ValueError:
                    pass
        if i < n and b[i:i+1] in b'{[':
            text_from_i = b[i:].decode('utf-8', errors='ignore')
            try:
                obj, end_idx = decoder.raw_decode(text_from_i)
                chunks[cid] = obj
                i += len(text_from_i[:end_idx].encode('utf-8'))
                continue
            except json.JSONDecodeError:
                pass
        next_nl = b.find(b'\n', i)
        if next_nl != -1:
            chunks[cid] = b[i:next_nl].strip().decode('utf-8', errors='replace')
            i = next_nl + 1
        else:
            chunks[cid] = b[i:].strip().decode('utf-8', errors='replace')
            break
    return chunks

def extract_job_from_html(html_content, job_url=""):
    soup = BeautifulSoup(html_content, 'html.parser')
    scripts = soup.find_all('script')
    pushes = []
    for s in scripts:
        text = s.string or ""
        if "self.__next_f.push" in text:
            for m in re.finditer(r'self\.__next_f\.push\(\[\d+,\s*("(?:[^"\\]|\\.)*")\]\)', text):
                try:
                    pushes.append(json.loads(m.group(1)))
                except Exception:
                    pass

    full_stream = "".join(pushes)
    chunks = parse_rsc_bytes(full_stream.encode('utf-8'))

    def resolve_ref(val):
        if isinstance(val, str) and val.startswith('$') and len(val) > 1 and val[1:] in chunks:
            return resolve_tree(chunks[val[1:]])
        return val

    def resolve_tree(obj):
        if isinstance(obj, dict):
            return {k: resolve_ref(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [resolve_ref(item) for item in obj]
        elif isinstance(obj, str) and obj.startswith('$') and len(obj) > 1 and obj[1:] in chunks:
            return resolve_tree(chunks[obj[1:]])
        return obj

    job = None
    for k, v in chunks.items():
        if isinstance(v, dict) and 'title' in v and 'company' in v and 'job_levels_str' in v:
            job = resolve_tree(v)
            break

    if not job:
        return None

    job_id = str(job.get('id') or extract_job_id(job_url))
    job_title = job.get('title', '')
    
    salary_obj = job.get('salary', {})
    salary = ""
    if isinstance(salary_obj, dict):
        salary = salary_obj.get('value', '')
        if not salary and (salary_obj.get('min') or salary_obj.get('max')):
            min_val = f"{int(salary_obj.get('min')):,}" if str(salary_obj.get('min', '')).isdigit() else str(salary_obj.get('min', ''))
            max_val = f"{int(salary_obj.get('max')):,}" if str(salary_obj.get('max', '')).isdigit() else str(salary_obj.get('max', ''))
            salary = f"{min_val} - {max_val} {salary_obj.get('currency', 'VND')}".strip(" -")
    else:
        salary = str(salary_obj or '')

    addr_obj = job.get('addresses', {})
    job_location = ""
    if isinstance(addr_obj, dict):
        job_location = addr_obj.get('sort_addresses', '')
        if not job_location and addr_obj.get('full_addresses'):
            job_location = addr_obj.get('full_addresses')[0]

    job_level = job.get('job_levels_str', '')
    experience = job.get('experiences_str', '')
    
    skills_arr = job.get('skills_arr', []) or []
    skills = ", ".join([s.get('value', '') for s in skills_arr if isinstance(s, dict) and s.get('value')])

    expires_in = job.get('job_durations_str', '')
    num_candidates = job.get('num_candidates_str', '')

    desc_raw = job.get('description', '')
    resp_raw = job.get('responsibilities', '')
    req_raw = job.get('requirements', '')
    ben_raw = job.get('benefits', '')

    general_description = clean_html(desc_raw)
    responsibilities = clean_html(resp_raw)
    requirements = clean_html(req_raw)
    benefits = clean_html(ben_raw)

    comp = job.get('company', {}) or {}
    company_name = comp.get('display_name', '')
    company_industry = comp.get('industries_str', '')
    company_size = comp.get('company_size', '')
    company_country = comp.get('country_name', '')
    company_job_openings = comp.get('job_openings_str', '')

    hash_source = f"{job_title}|{salary}|{responsibilities}|{requirements}|{benefits}"
    content_hash = compute_hash(hash_source)

    return {
        'platform': 'TopDev',
        'job_id': job_id,
        'job_title': job_title,
        'company_name': company_name,
        'salary': salary,
        'job_location': job_location,
        'job_level': job_level,
        'experience': experience,
        'skills': skills,
        'expires_in': expires_in,
        'num_candidates': f"{num_candidates} Applicants" if num_candidates is not None else "0 Applicants",
        'general_description': general_description,
        'responsibilities': responsibilities,
        'requirements': requirements,
        'benefits': benefits,
        'company_industry': company_industry,
        'company_size': company_size,
        'company_country': company_country,
        'company_job_openings': company_job_openings,
        'job_url': job_url or job.get('detail_url', ''),
        'content_hash': content_hash,
        'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

# ==============================================================================
# QUÉT TĂNG TRƯỞNG & XỬ LÝ RE-OPEN (EARLY STOPPING)
# ==============================================================================
def discover_new_and_reopened_jobs(session, headers, cookies, history):
    candidates = []
    seen = set()
    consecutive_active_old = 0
    stopped_early = False

    print("\n" + "=" * 75)
    print("⚡ BẮT ĐẦU QUÉT TĂNG TRƯỞNG TIN MỚI & RE-OPEN (TOPDEV IT)")
    print(f"   - Lịch chạy: Hàng ngày (Thứ 2 đến Chủ Nhật)")
    print(f"   - Ngưỡng dừng sớm: {CONSECUTIVE_OLD_THRESHOLD} tin cũ đang active liên tiếp")
    print(f"   - Giới hạn quét tối đa: {MAX_SEARCH_PAGES} trang")
    print("=" * 75)

    for page in range(1, MAX_SEARCH_PAGES + 1):
        url = f"https://topdev.vn/jobs/search?page={page}"
        print(f"  [+] Đang quét Trang {page}...", end=" ", flush=True)

        try:
            r = session.get(url, headers=headers, cookies=cookies, timeout=20)
            if r.status_code != 200:
                print(f"[HTTP {r.status_code} - Dừng phân trang]")
                break

            text = r.text
            found_urls = []
            for m in re.finditer(r'https://topdev\.vn/detail-jobs/[a-zA-Z0-9\-]+-(\d+)', text):
                u = m.group(0)
                if u not in seen:
                    seen.add(u)
                    found_urls.append(u)

            page_new = 0
            page_reopen = 0
            page_old = 0

            for full_url in found_urls:
                jid = extract_job_id(full_url)

                # Trường hợp 1: Tin mới tinh
                if jid not in history:
                    consecutive_active_old = 0
                    page_new += 1
                    candidates.append({"url": full_url, "type": "NEW"})

                # Trường hợp 2: Tin từng bị đóng, nay xuất hiện lại -> RE-OPEN!
                elif history[jid].get('status') == 'CLOSED':
                    consecutive_active_old = 0
                    page_reopen += 1
                    candidates.append({"url": full_url, "type": "REOPEN"})

                # Trường hợp 3: Tin cũ đang active bình thường
                else:
                    consecutive_active_old += 1
                    page_old += 1
                    if consecutive_active_old >= CONSECUTIVE_OLD_THRESHOLD:
                        stopped_early = True
                        break

            msg_parts = []
            if page_new > 0: msg_parts.append(f"+{page_new} tin mới")
            if page_reopen > 0: msg_parts.append(f"+{page_reopen} tin re-open")
            msg_parts.append(f"{page_old} tin cũ")
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
    daily_jsonl_file = os.path.join(BASE_DIR, f"topdev_daily_jobs_{today_str}.jsonl")

    session = create_robust_session()
    cookies = load_cookies()
    history = load_history()
    print(f"[*] Đã tải lịch sử TopDev: {len(history):,} tin đã lưu.")

    candidates = discover_new_and_reopened_jobs(session, DEFAULT_HEADERS, cookies, history)
    if not candidates:
        print("[🎉] Không có tin việc làm IT mới hoặc re-open nào hôm nay trên TopDev! Dữ liệu đã đồng bộ 100%.")
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
            r = session.get(url, headers=DEFAULT_HEADERS, cookies=cookies, timeout=20)
            if r.status_code != 200:
                print(f"      [-] HTTP {r.status_code}, bỏ qua.")
                continue

            item = extract_job_from_html(r.text, job_url=url)
            if not item or not item.get('job_title') or not item.get('company_name'):
                print("      [-] Không bóc tách được dữ liệu hợp lệ, bỏ qua.")
                continue

            success_records.append(item)
            history[jid] = {
                "url": url,
                "crawled_date": item['scraped_at'].split()[0],
                "content_hash": item['content_hash'],
                "job_title": item['job_title'],
                "company_name": item['company_name'],
                "status": "ACTIVE"
            }

            print(f"      [✓] {item['job_title'][:35]} | {item['company_name'][:25]} | Lương: {item['salary']}")
            time.sleep(REQUEST_DELAY)

        except Exception as e:
            print(f"      [!] Lỗi bóc tách tin {url}: {e}")

    if not success_records:
        print("[-] Không cào được tin nào thành công.")
        return

    # Ghi vào ĐÚNG 1 FILE DUY NHẤT theo ngày
    with open(daily_jsonl_file, 'a', encoding='utf-8') as f:
        for r in success_records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    # Lưu lại lịch sử cập nhật ngầm
    save_history(history)

    elapsed = time.time() - start_time
    print("\n" + "=" * 75)
    print("✅ HOÀN TẤT CÀO TĂNG TRƯỞNG TOPDEV HÔM NAY:")
    print(f"   - Số tin cào thành công      : {len(success_records)} tin")
    print(f"   - File dữ liệu tăng trưởng ngày: {os.path.basename(daily_jsonl_file)}")
    print(f"   - Tổng kho tích lũy trong history: {len(history):,} tin")
    print(f"   - Thời gian thực hiện          : {elapsed:.2f} giây")
    print("=" * 75)

if __name__ == '__main__':
    main()
