"""
================================================================================
CÀO DỮ LIỆU TĂNG TRƯỞNG HÀNG NGÀY (DAILY INCREMENTAL SCRAPER) - TOPCV
================================================================================
Mục đích:
  - Chạy tự động hàng ngày (từ Thứ 2 đến Chủ Nhật) để cào các tin việc làm IT mới nhất.
  - Sử dụng curl_cffi (Chrome 124 impersonate) vượt Cloudflare an toàn với Cookie.
  - Bộ lọc: Chuyên ngành Công nghệ Thông tin (danh mục cr257).
  - Áp dụng cơ chế Dừng Sớm (Early Stopping): Quét từ trang 1, nếu gặp liên tiếp
    các tin đã có trong `topcv_crawled_history.json`, bot tự động dừng quét trang.
  - Xử lý thông minh tin RE-OPEN:
    + Nếu tin chưa từng có trong lịch sử -> Cào mới tinh!
    + Nếu tin đã có trong lịch sử nhưng trước đó mang trạng thái "CLOSED" -> Phát hiện
      tin được mở lại (RE-OPEN) -> Cào lại và cập nhật lại trạng thái "ACTIVE"!
  - Toàn bộ dữ liệu mới/re-open được ghi vào 1 file duy nhất của ngày:
    `topcv_daily_jobs_{YYYYMMDD}.jsonl`
  - Cập nhật ngầm vào `topcv_crawled_history.json`.
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
    print("[CẢNH BÁO] Chưa cài curl_cffi, có thể gặp lỗi 403 Cloudflare. Chạy: pip install curl_cffi")

# ==============================================================================
# CẤU HÌNH ĐƯỜNG DẪN & THAM SỐ
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(BASE_DIR, "topcv_cookie.txt")
HISTORY_FILE = os.path.join(BASE_DIR, "topcv_crawled_history.json")

IT_SEARCH_BASE = "https://www.topcv.vn/tim-viec-lam-cong-nghe-thong-tin-cr257"
CONSECUTIVE_OLD_THRESHOLD = 20  # Gặp 20 tin cũ đang active liên tiếp -> Dừng sớm
MAX_SEARCH_PAGES = 5            # Quét tối đa 5 trang đầu (khoảng 250 tin IT mới nhất)
REQUEST_DELAY = 1.5             # Nghỉ an toàn giữa mỗi request

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
    """Nạp lịch sử cào từ file topcv_crawled_history.json"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_history(history):
    """Cập nhật ngầm trạng thái cào vào file topcv_crawled_history.json"""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

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

def make_request(url, headers, retries=3):
    for attempt in range(1, retries + 1):
        try:
            if HAVE_CURL_CFFI:
                resp = requests.get(url, headers=headers, impersonate="chrome124", timeout=20)
            else:
                resp = requests.get(url, headers=headers, timeout=20)

            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 429:
                wait_time = attempt * 5 + random.uniform(2, 5)
                time.sleep(wait_time)
            elif resp.status_code in [404, 410]:
                return None
            else:
                time.sleep(attempt * 2)
        except Exception:
            time.sleep(attempt * 2)
    return None

def compute_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def extract_job_id(url):
    m = re.search(r'/(\d+)\.html', url)
    return m.group(1) if m else url.split('/')[-1]

# ==============================================================================
# HÀM BÓC TÁCH CHI TIẾT TIN TOPCV (HỖ TRỢ SONG NGỮ ANH / VIỆT)
# ==============================================================================
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
    BENEFIT_KWS = ['quyền lợi', 'benefits', 'benefit']
    ben_box = soup.find('div', class_='box-job-information-benefit')
    if not ben_box:
        for item in soup.find_all('div', class_='box-job-information-detail-item'):
            h = item.find(['h2', 'h3'])
            if h and any(kw in h.text.strip().lower() for kw in BENEFIT_KWS):
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

    jid = extract_job_id(source_url)
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
# QUÉT TĂNG TRƯỞNG & XỬ LÝ RE-OPEN (EARLY STOPPING)
# ==============================================================================
def discover_new_and_reopened_jobs(headers, history):
    candidates = []
    seen = set()
    consecutive_active_old = 0
    stopped_early = False

    print("\n" + "=" * 75)
    print("⚡ BẮT ĐẦU QUÉT TĂNG TRƯỞNG TIN MỚI & RE-OPEN (TOPCV IT)")
    print(f"   - Lịch chạy: Hàng ngày (Thứ 2 đến Chủ Nhật)")
    print(f"   - Ngưỡng dừng sớm: {CONSECUTIVE_OLD_THRESHOLD} tin cũ đang active liên tiếp")
    print(f"   - Giới hạn quét tối đa: {MAX_SEARCH_PAGES} trang")
    print("=" * 75)

    for page in range(1, MAX_SEARCH_PAGES + 1):
        url = f"{IT_SEARCH_BASE}?page={page}"
        print(f"  [+] Đang quét Trang {page}...", end=" ", flush=True)

        html = make_request(url, headers)
        if not html:
            print("[Không tải được trang - Dừng phân trang]")
            break

        soup = BeautifulSoup(html, 'html.parser')
        job_links = []
        for a in soup.find_all('a', href=re.compile(r'/viec-lam/[^/]+/\d+\.html')):
            href = a.get('href', '').split('?')[0]
            if href and href not in seen:
                seen.add(href)
                job_links.append(href)

        page_new = 0
        page_reopen = 0
        page_old = 0

        for full_url in job_links:
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

        time.sleep(2.0)

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
    daily_jsonl_file = os.path.join(BASE_DIR, f"topcv_daily_jobs_{today_str}.jsonl")

    headers = get_headers()
    history = load_history()
    print(f"[*] Đã tải lịch sử TopCV: {len(history):,} tin đã lưu.")

    candidates = discover_new_and_reopened_jobs(headers, history)
    if not candidates:
        print("[🎉] Không có tin việc làm IT mới hoặc re-open nào hôm nay trên TopCV! Dữ liệu đã đồng bộ 100%.")
        return

    print(f"\n[🚀] BẮT ĐẦU CÀO CHI TIẾT {len(candidates)} TIN...")
    success_records = []

    for idx, item_info in enumerate(candidates, 1):
        url = item_info['url']
        job_type = item_info['type']
        jid = extract_job_id(url)
        tag = "[MỚI]" if job_type == 'NEW' else "[♻️ RE-OPEN]"
        print(f"  [{idx:2d}/{len(candidates):2d}] {tag} Đang cào Job[{jid}]: {url}")

        html = make_request(url, headers)
        if not html:
            print(f"      [-] Không tải được HTML, bỏ qua.")
            continue

        try:
            item = parse_topcv_job(html, source_url=url)
            if not item.get('job_title') or not item.get('company_name'):
                print("      [-] Thiếu trường title/company, bỏ qua.")
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
            time.sleep(REQUEST_DELAY + random.uniform(0.3, 0.7))

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
    print("✅ HOÀN TẤT CÀO TĂNG TRƯỞNG TOPCV HÔM NAY:")
    print(f"   - Số tin cào thành công      : {len(success_records)} tin")
    print(f"   - File dữ liệu tăng trưởng ngày: {os.path.basename(daily_jsonl_file)}")
    print(f"   - Tổng kho tích lũy trong history: {len(history):,} tin")
    print(f"   - Thời gian thực hiện          : {elapsed:.2f} giây")
    print("=" * 75)

if __name__ == '__main__':
    main()
