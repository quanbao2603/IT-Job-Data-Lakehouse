"""
================================================================================
RÀ SOÁT CUỐI TUẦN & THEO DÕI BIẾN ĐỘNG TIN (WEEKLY AUDIT & CDC) - TOPDEV
================================================================================
Mục đích:
  - Chạy định kỳ vào mỗi Chủ Nhật hàng tuần trên nền tảng TopDev.
  - Rà soát lại toàn bộ tin đang mở (status != 'CLOSED') trong
    `topdev_crawled_history.json`:
      1. Nếu phát hiện tin ĐÃ ĐÓNG / HẾT HẠN (HTTP 404/410, "Tin tuyển dụng đã hết hạn"):
         -> CẬP NHẬT NGẦM `"status": "CLOSED"` vào `topdev_crawled_history.json`.
         -> KHÔNG sinh file thừa.
      2. Nếu phát hiện tin CÓ BIẾN ĐỘNG (content_hash mới != content_hash cũ):
         -> Cào lại toàn bộ thông tin mới (lương, JD, yêu cầu vừa được điều chỉnh).
         -> Ghi vào ĐÚNG 1 FILE DUY NHẤT: `topdev_updated_jobs_{YYYYMMDD}.jsonl`.
         -> Cập nhật mã hash mới và `"status": "UPDATED"` vào `topdev_crawled_history.json`.
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

REQUEST_DELAY = 1.0  # Nghỉ giữa mỗi lần request (giây)

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept-Language': 'vi,en-US;q=0.9,en;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Referer': 'https://topdev.vn/',
}

def create_robust_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1.2, status_forcelist=[429, 500, 502, 503, 504], raise_on_status=False)
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

def is_job_closed(status_code, html_text):
    if status_code in [404, 410]:
        return True
    if not html_text:
        return True
    closed_keywords = [
        "việc làm này đã hết hạn",
        "tin tuyển dụng này đã đóng",
        "job is no longer available",
        "this job has expired",
        "công việc đã dừng tuyển",
        "tin tuyển dụng không tồn tại"
    ]
    lower = html_text.lower()
    return any(kw in lower for kw in closed_keywords)

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
# HÀM CHÍNH: RÀ SOÁT CUỐI TUẦN TOPDEV
# ==============================================================================
def main(limit=None):
    start_time = time.time()
    today_str = datetime.now().strftime("%Y%m%d")
    updated_file = os.path.join(BASE_DIR, f"topdev_updated_jobs_{today_str}.jsonl")

    session = create_robust_session()
    cookies = load_cookies()
    history = load_history()

    if not history:
        print("[-] Không tìm thấy lịch sử trong topdev_crawled_history.json để rà soát.")
        return

    active_jobs = [
        (jid, data) for jid, data in history.items() 
        if data.get('status', 'ACTIVE') != 'CLOSED'
    ]

    if limit and isinstance(limit, int):
        active_jobs = active_jobs[:limit]

    total = len(active_jobs)
    print("=" * 80)
    print("🔎 CHƯƠNG TRÌNH RÀ SOÁT CUỐI TUẦN & THEO DÕI BIẾN ĐỘNG TIN (TOPDEV)")
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
        url = info.get('url', f"https://topdev.vn/detail-jobs/{jid}")
        old_hash = info.get('content_hash', '')
        title = info.get('job_title', 'Unknown Title')

        percent = (idx / total) * 100
        print(f"[{idx:4d}/{total:4d}] ({percent:5.1f}%) Kiểm tra Job[{jid}]: {title[:35]}...", end=" ", flush=True)

        try:
            r = session.get(url, headers=DEFAULT_HEADERS, cookies=cookies, timeout=15)
            
            # 1. KIỂM TRA TIN ĐÃ ĐÓNG / HẾT HẠN
            if is_job_closed(r.status_code, r.text):
                print("[🔴 ĐÃ ĐÓNG / HẾT HẠN]")
                count_closed += 1
                history[jid]['status'] = 'CLOSED'
                history[jid]['closed_detected_at'] = datetime.now().strftime("%Y-%m-%d")
                history[jid]['last_audited_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 2. TIN VẪN HOẠT ĐỘNG -> KIỂM TRA BIẾN ĐỘNG NỘI DUNG (CDC)
            elif r.status_code == 200:
                item = extract_job_from_html(r.text, job_url=url)
                if not item:
                    print("[⚠️ Không parse được RSC]")
                    count_error += 1
                    continue

                new_hash = item['content_hash']
                history[jid]['last_audited_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if old_hash and new_hash != old_hash:
                    print("[🟡 CÓ BIẾN ĐỘNG / UPDATED!]")
                    count_updated += 1
                    history[jid]['status'] = 'UPDATED'
                    history[jid]['content_hash'] = new_hash
                    history[jid]['updated_at'] = datetime.now().strftime("%Y-%m-%d")

                    updated_records.append(item)
                else:
                    print("[🟢 VẪN HOẠT ĐỘNG / KHÔNG ĐỔI]")
                    count_active_unchanged += 1
                    history[jid]['status'] = 'ACTIVE'
            else:
                print(f"[⚠️ HTTP {r.status_code}]")
                count_error += 1

            time.sleep(REQUEST_DELAY)

        except Exception as e:
            print(f"[Lỗi: {e}]")
            count_error += 1

    save_history(history)

    if updated_records:
        with open(updated_file, 'a', encoding='utf-8') as f:
            for r in updated_records:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')

    elapsed = time.time() - start_time
    print("\n" + "=" * 80)
    print("📊 BÁO CÁO TỔNG KẾT RÀ SOÁT CUỐI TUẦN (TOPDEV):")
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
