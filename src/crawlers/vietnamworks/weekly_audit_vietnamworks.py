"""
================================================================================
RÀ SOÁT CUỐI TUẦN & THEO DÕI BIẾN ĐỘNG TIN (WEEKLY AUDIT & CDC) - VIETNAMWORKS
================================================================================
Mục đích:
  - Chạy định kỳ vào mỗi Chủ Nhật hàng tuần trên nền tảng VietnamWorks.
  - Rà soát lại toàn bộ tin đang mở (status != 'CLOSED') trong
    `vietnamworks_crawled_history.json`:
      1. Nếu phát hiện tin ĐÃ ĐÓNG / HẾT HẠN (HTTP 404/410, expiredOn quá hạn):
         -> CẬP NHẬT NGẦM `"status": "CLOSED"` vào `vietnamworks_crawled_history.json`.
         -> KHÔNG sinh file thừa.
      2. Nếu phát hiện tin CÓ BIẾN ĐỘNG (content_hash mới != content_hash cũ):
         -> Cào lại toàn bộ thông tin mới (lương, JD, yêu cầu vừa được điều chỉnh).
         -> Ghi vào ĐÚNG 1 FILE DUY NHẤT: `vietnamworks_updated_jobs_{YYYYMMDD}.jsonl`.
         -> Cập nhật mã hash mới và `"status": "UPDATED"` vào `vietnamworks_crawled_history.json`.
      3. Báo cáo tổng kết: In trực tiếp bảng thống kê ra màn hình Terminal (không sinh file thừa).
  - Hoàn toàn KHÔNG sử dụng CSV (100% chuẩn JSON/JSONL).
================================================================================
"""

import os
import sys
import json
import re
import time
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

REQUEST_DELAY = 1.0  # Nghỉ an toàn giữa mỗi request

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
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join([l for l in lines if l]).strip()

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

def scrape_full_vietnamworks_job(session, job_url):
    """Bóc tách toàn bộ chi tiết tin tuyển dụng khi phát hiện thay đổi nội dung"""
    for attempt in range(1, 3):
        try:
            res = session.get(job_url, timeout=15)
            if res.status_code in [404, 410]:
                return 'CLOSED', None
            if res.status_code != 200:
                time.sleep(1.0)
                continue

            html = res.text
            pushes = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)
            full_stream = "".join([decode_flight_push(p) for p in pushes])

            idx = full_stream.find('"jobDetail":')
            if idx == -1:
                if any(kw in html.lower() for kw in ["hết hạn", "no longer available", "expired"]):
                    return 'CLOSED', None
                return 'ERROR', None

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

            # Kiểm tra thời hạn
            expired_on = raw_job.get("expiredOn") or ""
            if expired_on:
                try:
                    exp_dt = datetime.fromisoformat(expired_on.replace('Z', '+00:00'))
                    if exp_dt.date() < datetime.now().date():
                        return 'CLOSED', None
                except Exception:
                    pass

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

            record = {
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
            return 'ACTIVE', record

        except Exception:
            time.sleep(1.0)

    return 'ERROR', None

# ==============================================================================
# HÀM CHÍNH: RÀ SOÁT CUỐI TUẦN VIETNAMWORKS
# ==============================================================================
def main(limit=None):
    start_time = time.time()
    today_str = datetime.now().strftime("%Y%m%d")
    updated_file = os.path.join(BASE_DIR, f"vietnamworks_updated_jobs_{today_str}.jsonl")

    session = create_session()
    history = load_history()

    if not history:
        print("[-] Không tìm thấy lịch sử trong vietnamworks_crawled_history.json để rà soát.")
        return

    active_jobs = [
        (jid, data) for jid, data in history.items() 
        if data.get('status', 'ACTIVE') != 'CLOSED'
    ]

    if limit and isinstance(limit, int):
        active_jobs = active_jobs[:limit]

    total = len(active_jobs)
    print("=" * 80)
    print("🔎 CHƯƠNG TRÌNH RÀ SOÁT CUỐI TUẦN & THEO DÕI BIẾN ĐỘNG TIN (VIETNAMWORKS)")
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
        url = info.get('url', f"https://www.vietnamworks.com/{jid}-jv")
        old_hash = info.get('content_hash', '')
        title = info.get('job_title', 'Unknown Title')

        percent = (idx / total) * 100
        print(f"[{idx:4d}/{total:4d}] ({percent:5.1f}%) Kiểm tra Job[{jid}]: {title[:35]}...", end=" ", flush=True)

        job_status, item = scrape_full_vietnamworks_job(session, url)

        # 1. KIỂM TRA TIN ĐÃ ĐÓNG / HẾT HẠN
        if job_status == 'CLOSED':
            print("[🔴 ĐÃ ĐÓNG / HẾT HẠN]")
            count_closed += 1
            # Cập nhật ngầm vào history
            history[jid]['status'] = 'CLOSED'
            history[jid]['closed_detected_at'] = datetime.now().strftime("%Y-%m-%d")
            history[jid]['last_audited_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 2. TIN VẪN HOẠT ĐỘNG -> KIỂM TRA BIẾN ĐỘNG NỘI DUNG (CDC)
        elif job_status == 'ACTIVE' and item:
            new_hash = item['content_hash']
            history[jid]['last_audited_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if old_hash and new_hash and new_hash != old_hash:
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
            print("[⚠️ Lỗi kiểm tra]")
            count_error += 1

        time.sleep(REQUEST_DELAY)

    save_history(history)

    if updated_records:
        with open(updated_file, 'a', encoding='utf-8') as f:
            for r in updated_records:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')

    elapsed = time.time() - start_time
    print("\n" + "=" * 80)
    print("📊 BÁO CÁO TỔNG KẾT RÀ SOÁT CUỐI TUẦN (VIETNAMWORKS):")
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
