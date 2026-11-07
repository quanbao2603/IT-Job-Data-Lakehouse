import abc
import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
import requests
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as cffi_requests
    HAVE_CURL_CFFI = True
except ImportError:
    import requests as cffi_requests
    HAVE_CURL_CFFI = False

from src.crawlers.base_crawler import BaseCrawler


class VietnamWorksCrawler(BaseCrawler):
    """
    Bot cào dữ liệu sàn VietnamWorks chuẩn Tầng Bronze 2.0 (Search API + RSC Dual-Engine).
    - Bộ lọc chuẩn 14 nhóm ngành IT chuyên môn (parentId: 5, 762 việc làm IT).
    - Giải mã chi tiết bằng Next.js Flight RSC Stream.
    - Dự phòng tự động (Fallback) bằng payload Search API nếu trang chi tiết lỗi.
    - Quản lý phiên Session keep-alive với Chrome 124 TLS chống Cloudflare block.
    """

    SEARCH_API = "https://ms.vietnamworks.com/job-search/v1.0/search"
    IT_PARENT_ID = 5
    IT_CATEGORIES = {
        25: "Business/System Analysis",
        26: "Database Administration",
        27: "Data Engineer/Data Analyst/AI",
        28: "Digital Transformation",
        29: "Hardware Developer",
        30: "IT Management",
        31: "IT Support/Help Desk",
        32: "System/Cloud/DevOps Engineer",
        33: "IT Product Management/Project Management",
        34: "QA/QC/Software Testing",
        35: "Security",
        36: "Software Developer",
        37: "Telecommunications",
        38: "UX/UI Design",
    }
    IT_FILTER = [{"parentId": IT_PARENT_ID, "childrenIds": [-1]}]
    REQUEST_DELAY_MIN = 0.8
    REQUEST_DELAY_MAX = 1.5

    def __init__(self, **kwargs):
        super().__init__(source_name="vietnamworks", **kwargs)
        self.cookie = self._load_cookie()
        self._init_sessions()

    def _init_sessions(self):
        """Khởi tạo phiên Session tái sử dụng kết nối HTTP/2 keep-alive."""
        if HAVE_CURL_CFFI:
            self.cffi_session = cffi_requests.Session(impersonate="chrome124")
        else:
            self.cffi_session = requests.Session()

        self.api_session = requests.Session()
        self.api_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://www.vietnamworks.com",
            "Referer": "https://www.vietnamworks.com/",
        })

    def _load_cookie(self) -> str:
        candidates = [
            os.path.join(os.path.dirname(__file__), "cookies", "vietnamworks_cookie.txt"),
            os.path.join(os.path.dirname(__file__), "vietnamworks", "vietnamworks_cookie.txt"),
        ]
        for c_path in candidates:
            if os.path.exists(c_path):
                try:
                    with open(c_path, "r", encoding="utf-8") as f:
                        lines = [l.strip() for l in f.readlines() if l.strip() and not l.startswith("#")]
                        if lines:
                            return "; ".join(lines)
                except Exception:
                    pass
        return ""

    def _clean_html(self, raw_html: str) -> str:
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
        cleaned_lines = []
        last_empty = False
        for line in text.splitlines():
            l = line.strip()
            l = re.sub(r"^[0-9a-fA-F]+:T[0-9a-fA-F]+,", "", l).strip()
            if l:
                cleaned_lines.append(l)
                last_empty = False
            elif not last_empty:
                cleaned_lines.append("")
                last_empty = True
        return "\n".join(cleaned_lines).strip()

    def _decode_flight_push(self, p: str) -> str:
        try:
            return json.loads(f'"{p}"')
        except Exception:
            try:
                return bytes(p, "utf-8").decode("unicode_escape").encode("latin1").decode("utf-8")
            except Exception:
                return p.replace('\\"', '"').replace("\\\\", "\\")

    def _make_request(self, url: str, max_retries: int = 4, base_backoff: float = 3.0) -> Optional[str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi,en;q=0.9",
            "Referer": "https://www.vietnamworks.com/",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie

        for attempt in range(max_retries):
            try:
                resp = self.cffi_session.get(url, headers=headers, timeout=25)

                if resp.status_code == 200:
                    return resp.text

                if resp.status_code == 429:
                    sleep_time = (base_backoff * (2 ** attempt)) + random.uniform(1.0, 2.5)
                    print(f"      [!] Bị giới hạn tốc độ (HTTP 429), ngủ {sleep_time:.1f}s trước khi thử lại...")
                    time.sleep(sleep_time)
                    continue

                if resp.status_code == 404:
                    return None

                time.sleep(1.5 + attempt)

            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1.5 + attempt)
                else:
                    print(f"      [!] Lỗi kết nối {url}: {e}")
                    return None
        return None

    def _extract_job_detail_raw(self, html_content: str, job_url: str) -> Optional[Dict[str, Any]]:
        """
        Bóc tách 28 trường thô từ luồng Next.js Flight RSC của VietnamWorks.
        """
        try:
            pushes = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html_content, re.DOTALL)
            full_stream = "".join([self._decode_flight_push(p) for p in pushes])

            idx = full_stream.find('"jobDetail":')
            if idx == -1:
                return None

            sub = full_stream[idx + len('"jobDetail":'):]
            brace_count = 0
            end_pos = 0
            for i, ch in enumerate(sub):
                if ch == "{":
                    brace_count += 1
                elif ch == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = i + 1
                        break

            if end_pos == 0:
                return None

            raw_job = json.loads(sub[:end_pos])

            # Giải mã các trường tham chiếu chuỗi dài (Text Reference: $id)
            for field in ["jobDescription", "jobRequirement"]:
                val = raw_job.get(field, "")
                if isinstance(val, str) and val.startswith("$") and val[1:].isalnum():
                    ref_id = val[1:]
                    pattern = re.compile(rf'(?:^|[^\d]){re.escape(ref_id)}:T([0-9a-fA-F]+),')
                    match = pattern.search(full_stream)
                    if match:
                        hex_len = int(match.group(1), 16)
                        start_idx = match.end()
                        raw_job[field] = full_stream[start_idx : start_idx + hex_len]

            job_id = str(raw_job.get("jobId") or "")
            job_title = raw_job.get("jobTitle") or ""
            company_name = raw_job.get("companyName") or ""
            company_profile = self._clean_html(raw_job.get("companyProfile") or "")
            company_logo = raw_job.get("companyLogo") or ""
            salary = raw_job.get("prettySalary") or raw_job.get("salary") or "Thương lượng"
            expired_on = raw_job.get("expiredOn") or ""

            expires_in_days = ""
            if expired_on:
                try:
                    exp_dt = datetime.fromisoformat(expired_on.replace("Z", "+00:00"))
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
                    c_dt = datetime.fromisoformat(created_on.replace("Z", "+00:00"))
                    d_diff = (datetime.now().date() - c_dt.date()).days
                    posted_time_ago = f"Đăng {d_diff} ngày trước" if d_diff > 0 else "Đăng hôm nay"
                except Exception:
                    pass

            num_apps = raw_job.get("numberOfApplications")
            applicant_count = "Be one of the first 20 applicants" if (num_apps is None or num_apps < 20) else f"{num_apps} applicants"

            job_description = self._clean_html(raw_job.get("jobDescription") or "")
            job_requirements = self._clean_html(raw_job.get("jobRequirement") or "")

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
                    posted_date = datetime.fromisoformat(approved_on.replace("Z", "+00:00")).strftime("%d/%m/%Y")
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
            if company_name:
                tags_set.append(company_name)
            if company_location:
                tags_set.append(company_location)
            user_tags = ", ".join(dict.fromkeys([t for t in tags_set if t]))

            content_hash = hashlib.md5(f"{job_title}|{salary}|{job_description}|{job_requirements}|{skills}".encode("utf-8")).hexdigest()

            return {
                "platform": "vietnamworks",
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
                "user_tags": user_tags,
            }
        except Exception:
            return None

    def _extract_job_from_api_payload(self, job: Dict[str, Any], job_url: str) -> Dict[str, Any]:
        """
        Dự phòng (Fallback) bóc tách trực tiếp từ payload của Search API nếu trang chi tiết bị lỗi.
        """
        job_id = str(job.get("jobId") or "")
        job_title = job.get("jobTitle") or ""
        company_name = job.get("companyName") or ""
        company_profile = self._clean_html(job.get("companyProfile") or "")
        company_logo = job.get("companyLogo") or ""
        salary = job.get("prettySalary") or "Thương lượng"
        expired_on = job.get("expiredOn") or ""

        working_locs = job.get("workingLocations") or []
        company_location = working_locs[0].get("cityName", "") if working_locs else ""

        raw_skills = job.get("skills") or []
        skills = ", ".join([s.get("skillName") for s in raw_skills if isinstance(s, dict) and s.get("skillName")])

        job_desc = self._clean_html(job.get("jobDescription") or "")
        job_req = self._clean_html(job.get("jobRequirement") or "")

        jf_data = job.get("jobFunction") or {}
        p_name = jf_data.get("parentName") or ""
        c_names = [c.get("name") for c in (jf_data.get("children") or []) if isinstance(c, dict) and c.get("name")]
        job_function = f"{p_name} > {', '.join(c_names)}" if c_names else p_name

        content_hash = hashlib.md5(f"{job_title}|{salary}|{job_desc}|{job_req}|{skills}".encode("utf-8")).hexdigest()

        return {
            "platform": "vietnamworks",
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
            "expires_in_days": "",
            "views_count": "0 views",
            "company_location": company_location,
            "posted_time_ago": "",
            "applicant_count": "Be one of the first 20 applicants",
            "job_description": job_desc,
            "job_requirements": job_req,
            "what_we_offer": "",
            "posted_date": "",
            "job_level": job.get("jobLevel") or job.get("jobLevelVI") or "",
            "job_function": job_function,
            "skills": skills,
            "job_industry": "",
            "preferred_language": "Any",
            "years_of_experience": str(job.get("yearsOfExperience", "")),
            "nationality": "Not shown",
            "job_locations": working_locs[0].get("address", "") if working_locs else "",
            "user_tags": f"{p_name}, {skills}",
        }

    def fetch_jobs(
        self,
        mode: str = "incremental",
        max_pages: int = 1,
        watermark_ts: Optional[str] = None,
        last_record_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Tìm kiếm việc làm IT qua VietnamWorks Search API (chuẩn 14 chuyên ngành IT) và cào chi tiết.
        """
        mode_label = "VÉT CẠN TOÀN BỘ (FULL BACKFILL)" if mode == "full" else "TĂNG TRƯỞNG (INCREMENTAL)"
        print(f"[*] Bắt đầu quét VietnamWorks qua 14 chuyên ngành IT - Chế độ: {mode_label} (Tối đa {max_pages} trang API)...")

        candidates = []
        seen_ids = set()
        watermark_hit = False

        # 1. Gọi trang 0 để lấy tổng số trang khả dụng
        try:
            init_payload = {
                "userId": 0, "query": "",
                "filter": [{"field": "jobFunction", "value": json.dumps(self.IT_FILTER)}],
                "ranges": [], "order": [{"field": "approvedOn", "value": "desc"}],
                "hitsPerPage": 50,
                "page": 0,
            }
            init_res = self.api_session.post(self.SEARCH_API, json=init_payload, timeout=20)
            if init_res.status_code == 200:
                init_data = init_res.json()
                meta = init_data.get("meta", {})
                total_hits = meta.get("nbHits", 0)
                available_pages = meta.get("nbPages", 1)
                print(f"      📊 VIETNAMWORKS XÁC NHẬN: Tuyển dụng {total_hits:,} việc làm ngành IT ({available_pages} trang API)")
            else:
                available_pages = max_pages
        except Exception as e:
            print(f"      [!] Cảnh báo lấy metadata API: {e}")
            available_pages = max_pages

        actual_max_pages = min(max_pages, available_pages) if mode == "full" else max_pages

        # 2. Quét danh sách tin qua Search API
        for page in range(0, actual_max_pages):
            if watermark_hit:
                break

            print(f"   -> Đang tải trang tìm kiếm {page + 1}/{actual_max_pages}...", end=" ", flush=True)

            payload = {
                "userId": 0, "query": "",
                "filter": [{"field": "jobFunction", "value": json.dumps(self.IT_FILTER)}],
                "ranges": [], "order": [{"field": "approvedOn", "value": "desc"}],
                "hitsPerPage": 50,
                "page": page,
            }

            try:
                res = self.api_session.post(self.SEARCH_API, json=payload, timeout=20)
                if res.status_code != 200:
                    print(f"[HTTP {res.status_code} - Dừng quét]")
                    break

                jobs = res.json().get("data", [])
                if not jobs:
                    print("[Hết tin]")
                    break

                page_candidates = []
                for job in jobs:
                    jid = str(job.get("jobId") or "")
                    jurl = job.get("jobUrl") or f"https://www.vietnamworks.com/{jid}-jv"
                    approved_on = job.get("approvedOn")

                    if not jurl.startswith("http"):
                        jurl = f"https://www.vietnamworks.com{jurl}"

                    if jid in seen_ids:
                        continue

                    # Kiểm tra Watermark: Gặp lại tin đã cào ở mốc trước
                    if mode == "incremental":
                        if last_record_id and str(jid) == str(last_record_id):
                            print(f"\n      🎯 [WATERMARK HIT] Chạm ranh giới Job ID cũ ({jid}). Dừng quét API!")
                            watermark_hit = True
                            break
                        if watermark_ts and approved_on and str(approved_on) <= str(watermark_ts):
                            print(f"\n      🎯 [WATERMARK HIT] Tin đăng {approved_on} <= Watermark {watermark_ts}. Dừng quét API!")
                            watermark_hit = True
                            break

                    seen_ids.add(jid)
                    page_candidates.append({
                        "id": jid,
                        "url": jurl,
                        "title": job.get("jobTitle", ""),
                        "raw_api_job": job,
                    })

                candidates.extend(page_candidates)
                print(f"[✓ Phát hiện +{len(page_candidates)} tin mới]")
                self.heartbeat()

                if watermark_hit:
                    break

                time.sleep(0.5)

            except Exception as e:
                print(f"[Lỗi API: {e}]")
                break

        print(f"\n[*] TỔNG CỘNG PHÁT HIỆN: {len(candidates)} tin tuyển dụng VietnamWorks IT. BẮT ĐẦU CÀO...")
        scraped_jobs = []
        for idx, item in enumerate(candidates, 1):
            jid = item["id"]
            jurl = item["url"]
            print(f"  [{idx:4d}/{len(candidates):4d}] Đang cào Job[{jid}]: {jurl}")

            # Engine 1: Cào chi tiết Next.js Flight RSC
            detail_html = self._make_request(jurl)
            job_data = None
            if detail_html:
                job_data = self._extract_job_detail_raw(detail_html, jurl)

            # Engine 2: Fallback an toàn từ API Search nếu không tải được trang chi tiết
            if not job_data and item.get("raw_api_job"):
                print(f"       [!] Dùng Engine Fallback (Search API) cho Job[{jid}]")
                job_data = self._extract_job_from_api_payload(item["raw_api_job"], jurl)

            if job_data:
                scraped_jobs.append(job_data)
                skills_preview = job_data["skills"][:30] + "..." if len(job_data["skills"]) > 30 else job_data["skills"]
                print(f"       [✓] {job_data['job_title'][:35]} | {job_data['company_name'][:20]} | Lương: {job_data['salary']} | Skills: [{skills_preview or 'N/A'}]")
            else:
                print(f"       [-] Thất bại cào nội dung Job[{jid}]")

            time.sleep(random.uniform(self.REQUEST_DELAY_MIN, self.REQUEST_DELAY_MAX))

        print(f"\n[*] TỔNG CỘNG THU THẬP: {len(scraped_jobs)} tin tuyển dụng VietnamWorks IT chuẩn.")
        return scraped_jobs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Bot cào VietnamWorks chuẩn Tầng Bronze 2.0 (Search API + RSC Stream)")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental", help="Chế độ cào")
    parser.add_argument("--max-pages", type=int, default=1, help="Số trang API quét tối đa (50 tin/trang. Mặc định: 1)")
    args = parser.parse_args()

    crawler = VietnamWorksCrawler()
    crawler.run(mode=args.mode, max_pages=args.max_pages)
