"""
================================================================================
CÀO DỮ LIỆU TĂNG TRƯỞNG HÀNG NGÀY (DAILY INCREMENTAL SCRAPER) - VIETNAMWORKS
================================================================================
Mục đích:
  - Chạy tự động hàng ngày (từ Thứ 2 đến Chủ Nhật) để cào các tin việc làm IT mới nhất.
  - Sử dụng Search API của VietnamWorks với bộ lọc Job Function = 5 (IT - Phần mềm).
  - Áp dụng cơ chế Dừng Sớm (Early Stopping): Khi gặp liên tiếp các tin đã có trong
    `vietnamworks_crawled_history.json`, bot tự động dừng gọi Search API để tiết kiệm băng thông.
  - Xử lý thông minh tin RE-OPEN:
    + Nếu tin chưa từng có trong lịch sử -> Cào mới tinh!
    + Nếu tin đã có trong lịch sử nhưng trước đó mang trạng thái "CLOSED" -> Phát hiện
      tin được mở lại (RE-OPEN) -> Cào lại và cập nhật lại trạng thái "ACTIVE"!
  - Giải mã Next.js Flight RSC stream bóc tách đầy đủ 28 trường dữ liệu chuẩn Bronze.
  - Toàn bộ dữ liệu mới/re-open được ghi vào 1 file duy nhất của ngày:
    `vietnamworks_daily_jobs_{YYYYMMDD}.jsonl`
  - Cập nhật ngầm vào `vietnamworks_crawled_history.json`.
  - Hoàn toàn KHÔNG sử dụng CSV (100% chuẩn JSON/JSONL).
================================================================================
"""

import os
import sys
import json
import re
import time
import random
import hashlib
from datetime import datetime
import requests
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
COOKIE_FILE = os.path.join(BASE_DIR, "vietnamworks_cookie.txt")
HISTORY_FILE = os.path.join(BASE_DIR, "vietnamworks_crawled_history.json")

CONSECUTIVE_OLD_THRESHOLD = 20  # Gặp 20 tin cũ đang active liên tiếp -> Dừng sớm
REQUEST_DELAY = 1.0             # Nghỉ giữa mỗi tin chi tiết

def compute_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def clean_html(raw_html):
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("- ")
        li.insert_after("\n")
    for tag in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6"]):
        tag.insert_after("\n")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    text = soup.get_text()
    cleaned_lines = [line.strip() for line in text.splitlines()]
    result = []
    last_empty = False
    for line in cleaned_lines:
        if line:
            result.append(line)
            last_empty = False
        elif not last_empty:
            result.append("")
            last_empty = True
    return "\n".join(result).strip()

def decode_flight_push(p):
    try:
        return json.loads(f'"{p}"')
    except Exception:
        try:
            return bytes(p, "utf-8").decode("unicode_escape").encode("latin1").decode("utf-8")
        except Exception:
            return p.replace('\\"', '"').replace('\\\\', '\\')

def load_cookies():
    """Nạp cookie từ file vietnamworks_cookie.txt"""
    cookies = {}
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                for item in f.read().strip().split(";"):
                    if "=" in item:
                        k, v = item.strip().split("=", 1)
                        cookies[k] = v
        except Exception:
            pass
    return cookies

def create_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "vi,en;q=0.9",
        "Origin": "https://www.vietnamworks.com",
        "Referer": "https://www.vietnamworks.com/",
    })
    s.cookies.update(load_cookies())
    return s

def load_history():
    """Nạp lịch sử từ file vietnamworks_crawled_history.json"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_history(history):
    """Cập nhật ngầm trạng thái vào file vietnamworks_crawled_history.json"""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ==============================================================================
# BÓC TÁCH CHI TIẾT TIN TUYỂN DỤNG TỪ LUỒNG FLIGHT RSC
# ==============================================================================
def scrape_job_detail(session, job_url):
    for attempt in range(1, 4):
        try:
            res = session.get(job_url, timeout=15)
            if res.status_code == 404:
                return None
            if res.status_code != 200:
                time.sleep(attempt * 1.5)
                continue

            html = res.text
            pushes = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)
            full_stream = "".join([decode_flight_push(p) for p in pushes])

            idx = full_stream.find('"jobDetail":')
            if idx == -1:
                return None

            sub = full_stream[idx + len('"jobDetail":'):]
            brace_count = 0
            end_pos = 0
            for i, ch in enumerate(sub):
                if ch == '{': brace_count += 1
                elif ch == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = i + 1
                        break

            raw_job = json.loads(sub[:end_pos])

            for field in ["jobDescription", "jobRequirement"]:
                val = raw_job.get(field, "")
                if isinstance(val, str) and val.startswith("$") and val[1:].isalnum():
                    ref_id = val[1:]
                    pattern = re.compile(rf'(?:^|[^\d]){re.escape(ref_id)}:T([0-9a-fA-F]+),')
                    match = pattern.search(full_stream)
                    if match:
                        hex_len = int(match.group(1), 16)
                        start_idx = match.end()
                        raw_job[field] = full_stream[start_idx:start_idx + hex_len]

            job_id = str(raw_job.get("jobId") or "")
            job_title = raw_job.get("jobTitle") or ""
            company_name = raw_job.get("companyName") or ""
            company_profile = clean_html(raw_job.get("companyProfile") or "")
            company_logo = raw_job.get("companyLogo") or ""
            salary = raw_job.get("prettySalary") or raw_job.get("salary") or "Thương lượng"
            expired_on = raw_job.get("expiredOn") or ""

            expires_in_days = ""
            if expired_on:
                try:
                    exp_dt = datetime.fromisoformat(expired_on.replace('Z', '+00:00'))
                    diff = (exp_dt.date() - datetime.now().date()).days
                    expires_in_days = f"Expires in {diff} days" if diff > 0 else "Expired"
                except Exception:
                    pass

            views_count = f"{raw_job.get('views', 0)} views"
            working_locs = raw_job.get("workingLocations") or []
            company_location = working_locs[0].get("cityName", "") if working_locs else ""

            posted_time_ago = ""
            created_on = raw_job.get("createdOn") or ""
            if created_on:
                try:
                    c_dt = datetime.fromisoformat(created_on.replace('Z', '+00:00'))
                    d_diff = (datetime.now().date() - c_dt.date()).days
                    posted_time_ago = f"Đăng {d_diff} ngày trước" if d_diff > 0 else "Đăng hôm nay"
                except Exception:
                    pass

            num_apps = raw_job.get("numberOfApplications")
            applicant_count = "Be one of the first 20 applicants" if (num_apps is None or num_apps < 20) else f"{num_apps} applicants"

            job_description = clean_html(raw_job.get("jobDescription") or "")
            job_requirements = clean_html(raw_job.get("jobRequirement") or "")

            raw_benefits = raw_job.get("benefits") or []
            ben_list = []
            for b in raw_benefits:
                n = b.get("benefitName") or b.get("benefitNameVI") or ""
                v = b.get("benefitValue") or ""
                ben_list.append(f"{n}: {v}" if v else n)
            what_we_offer = " | ".join(ben_list)

            approved_on = raw_job.get("approvedOn") or ""
            posted_date = ""
            if approved_on:
                try:
                    posted_date = datetime.fromisoformat(approved_on.replace('Z', '+00:00')).strftime("%d/%m/%Y")
                except Exception:
                    posted_date = approved_on

            job_level = raw_job.get("jobLevel") or raw_job.get("jobLevelVI") or ""

            jf_data = raw_job.get("jobFunction") or {}
            p_name = jf_data.get("parentName") or ""
            c_names = [c.get("name") for c in (jf_data.get("children") or []) if c.get("name")]
            job_function = f"{p_name} > {', '.join(c_names)}" if c_names else p_name

            raw_skills = raw_job.get("skills") or []
            skills = ", ".join([s.get("skillName") for s in raw_skills if s.get("skillName")])

            raw_ind = raw_job.get("industriesV3") or []
            job_industry = ", ".join([ind.get("industryV3Name") for ind in raw_ind if ind.get("industryV3Name")])

            preferred_language = raw_job.get("languageSelected") or raw_job.get("languageSelectedVI") or "Any"
            years_of_experience = str(raw_job.get("yearsOfExperience", ""))
            nationality = "Not shown" if raw_job.get("isShowNationality", 0) == 0 else "Shown"
            job_locations = raw_job.get("address") or (working_locs[0].get("address") if working_locs else "")

            tags_set = [p_name] + c_names + [i.get("industryV3Name") for i in raw_ind if i.get("industryV3Name")] + [s.get("skillName") for s in raw_skills if s.get("skillName")]
            if company_name: tags_set.append(company_name)
            if company_location: tags_set.append(company_location)
            user_tags = ", ".join(dict.fromkeys([t for t in tags_set if t]))

            content_hash = compute_hash(f"{job_title}|{salary}|{job_description}|{job_requirements}|{skills}")

            return {
                "platform": "VietnamWorks",
                "job_id": job_id,
                "job_url": job_url,
                "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "content_hash": content_hash,
                "company_name": company_name,
                "company_profile": company_profile,
                "company_logo": company_logo,
                "job_title": job_title,
                "salary": salary,
                "expired_on": expired_on,
                "expires_in_days": expires_in_days,
                "views_count": views_count,
                "company_location": company_location,
                "posted_time_ago": posted_time_ago,
                "applicant_count": applicant_count,
                "job_description": job_description,
                "job_requirements": job_requirements,
                "what_we_offer": what_we_offer,
                "posted_date": posted_date,
                "job_level": job_level,
                "job_function": job_function,
                "skills": skills,
                "job_industry": job_industry,
                "preferred_language": preferred_language,
                "years_of_experience": years_of_experience,
                "nationality": nationality,
                "job_locations": job_locations,
                "user_tags": user_tags
            }
        except Exception:
            time.sleep(attempt * 1.5)
    return None

# ==============================================================================
# QUÉT TĂNG TRƯỞNG & XỬ LÝ RE-OPEN (EARLY STOPPING)
# ==============================================================================
def discover_new_and_reopened_jobs(session, history):
    search_url = "https://ms.vietnamworks.com/job-search/v1.0/search"
    it_filter = [{"parentId": 5, "childrenIds": [-1]}]
    candidates = []
    consecutive_active_old = 0
    stopped_early = False

    print("\n" + "=" * 75)
    print("⚡ BẮT ĐẦU QUÉT TĂNG TRƯỞNG TIN MỚI & RE-OPEN (VIETNAMWORKS IT)")
    print(f"   - Lịch chạy: Hàng ngày (Thứ 2 đến Chủ Nhật)")
    print(f"   - Ngưỡng dừng sớm: {CONSECUTIVE_OLD_THRESHOLD} tin cũ đang active liên tiếp")
    print("=" * 75)

    for page in range(0, 5):  # Quét tối đa 5 trang đầu (250 tin IT mới nhất)
        payload = {
            "userId": 0, "query": "",
            "filter": [{"field": "jobFunction", "value": json.dumps(it_filter)}],
            "ranges": [], "order": [],
            "hitsPerPage": 50,
            "page": page
        }

        try:
            res = session.post(search_url, json=payload, timeout=20)
            if res.status_code != 200:
                print(f"[Lỗi Search API HTTP {res.status_code} - Dừng phân trang]")
                break

            data = res.json()
            jobs = data.get("data", [])
            if not jobs:
                break

            page_new = 0
            page_reopen = 0
            page_old = 0

            for job in jobs:
                jid = str(job.get("jobId") or "")
                jurl = job.get("jobUrl") or f"https://www.vietnamworks.com/{jid}-jv"
                
                # Trường hợp 1: Tin mới tinh
                if jid not in history:
                    consecutive_active_old = 0
                    page_new += 1
                    candidates.append({"url": jurl, "type": "NEW", "id": jid})

                # Trường hợp 2: Tin từng bị đóng, nay xuất hiện lại -> RE-OPEN!
                elif history[jid].get('status') == 'CLOSED':
                    consecutive_active_old = 0
                    page_reopen += 1
                    candidates.append({"url": jurl, "type": "REOPEN", "id": jid})

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
            print(f"  [+] Trang {page + 1}: " + " | ".join(msg_parts))

            if stopped_early:
                print(f"\n[🛑 DỪNG SỚM] Đã gặp {CONSECUTIVE_OLD_THRESHOLD} tin cũ active liên tiếp! Dừng quét API.")
                break

            time.sleep(0.5)

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
    daily_jsonl_file = os.path.join(BASE_DIR, f"vietnamworks_daily_jobs_{today_str}.jsonl")

    session = create_session()
    history = load_history()
    print(f"[*] Đã tải lịch sử VietnamWorks: {len(history):,} tin đã lưu.")

    candidates = discover_new_and_reopened_jobs(session, history)
    if not candidates:
        print("[🎉] Không có tin việc làm IT mới hoặc re-open nào hôm nay trên VietnamWorks! Dữ liệu đã đồng bộ 100%.")
        return

    print(f"\n[🚀] BẮT ĐẦU CÀO CHI TIẾT {len(candidates)} TIN...")
    success_records = []

    for idx, item_info in enumerate(candidates, 1):
        url = item_info['url']
        job_type = item_info['type']
        jid = item_info['id']
        tag = "[MỚI]" if job_type == 'NEW' else "[♻️ RE-OPEN]"
        print(f"  [{idx:2d}/{len(candidates):2d}] {tag} Đang cào Job[{jid}]: {url}")

        try:
            item = scrape_job_detail(session, url)
            if not item or not item.get("job_title") or not item.get("company_name"):
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
    print("✅ HOÀN TẤT CÀO TĂNG TRƯỞNG VIETNAMWORKS HÔM NAY:")
    print(f"   - Số tin cào thành công      : {len(success_records)} tin")
    print(f"   - File dữ liệu tăng trưởng ngày: {os.path.basename(daily_jsonl_file)}")
    print(f"   - Tổng kho tích lũy trong history: {len(history):,} tin")
    print(f"   - Thời gian thực hiện          : {elapsed:.2f} giây")
    print("=" * 75)

if __name__ == '__main__':
    main()
