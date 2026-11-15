import re
import os
import time
import json
import random
import hashlib
import argparse
import subprocess
import tempfile
from datetime import datetime
from typing import List, Dict, Any, Optional, Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

try:
    from curl_cffi import requests
    HAVE_CURL_CFFI = True
except ImportError:
    import requests
    HAVE_CURL_CFFI = False

from src.crawlers.base_crawler import (
    BaseCrawler,
    CrawlerSourceError,
    emit_cli_result,
    require_listing_data,
)


class DevworkCrawler(BaseCrawler):
    """
    Bot cào sàn Devwork.vn chuẩn Tầng Bronze 2.0.
    - KHÔNG cần ma trận lọc IT: toàn bộ tin đăng trên devwork.vn đều là việc làm IT/tech.
    - Phân trang server-side: ?page=N (20 tin/trang, tổng ~124 tin, 7 trang).
    - Engine 1 listing: eval window.__NUXT__ qua Node.js → fetch[key].jobs[] với payload giàu:
      id, name, salary{min,max}, province{name}, type{value}, skills[{name}], experience{value},
      logo, service.expired_at, created_at.
    - Engine 1 detail: parse DOM div.content — title (h2), company, location, salary, description,
      requirements, structured fields (kinh nghiệm, trình độ, hình thức, hạn nộp, số lượng).
    - Engine 2 fallback: payload mức thẻ tin (zero data loss) khi trang chi tiết lỗi.
    - Incremental: jobs sắp theo priority/service (tin trả phí lên đầu) nên thứ tự KHÔNG đơn điệu
      theo thời gian → dùng id_cursor (last_record_id) làm chốt chính + datetime guard phụ.
    """

    BASE_URL = "https://devwork.vn"
    LIST_BASE = "https://devwork.vn/viec-lam"
    REQUEST_DELAY_MIN = 1.0
    REQUEST_DELAY_MAX = 1.8

    def __init__(self, **kwargs):
        super().__init__(source_name="devwork", **kwargs)
        self.cookie = self._load_cookie()
        self.session = self._create_session()

    def _load_cookie(self) -> str:
        candidates = [
            os.path.join(os.path.dirname(__file__), "cookies", "devworks_cookie.txt"),
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

    def _create_session(self):
        if HAVE_CURL_CFFI:
            s = requests.Session(impersonate="chrome124")
        else:
            s = requests.Session()

        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
            "Referer": "https://devwork.vn/",
        })

        if self.cookie:
            cookies_dict = {}
            for item in self.cookie.split(";"):
                if "=" in item:
                    k, v = item.strip().split("=", 1)
                    cookies_dict[k] = v
            s.cookies.update(cookies_dict)
        return s

    def _make_request(self, url: str, max_retries: int = 4, base_backoff: float = 3.0) -> Optional[str]:
        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, timeout=25)

                if resp.status_code == 200:
                    return resp.text

                if resp.status_code == 429:
                    sleep_time = (base_backoff * (2 ** attempt)) + random.uniform(1.0, 3.0)
                    print(f"      [!] Bị giới hạn tốc độ (HTTP 429), ngủ {sleep_time:.1f}s trước khi thử lại...")
                    time.sleep(sleep_time)
                    continue

                if resp.status_code == 403:
                    raise CrawlerSourceError(f"{self.source_name} request returned HTTP 403")

                if resp.status_code in [404, 410]:
                    print(f"      [-] Tin không tồn tại hoặc đã đóng (HTTP {resp.status_code})")
                    return None

                if resp.status_code in [500, 502, 503, 504]:
                    time.sleep(2.0 + attempt)
                    continue

                print(f"      [-] HTTP {resp.status_code} khi tải {url}")
                time.sleep(1.5 + attempt)

            except Exception as e:
                if isinstance(e, CrawlerSourceError):
                    raise
                if attempt < max_retries - 1:
                    time.sleep(1.5 + attempt)
                else:
                    raise CrawlerSourceError(
                        f"{self.source_name} request failed after {attempt + 1} attempts: {type(e).__name__}"
                    ) from e
        raise CrawlerSourceError(
            f"{self.source_name} request failed after {max_retries} attempts"
        )

    def _list_url(self, page: int) -> str:
        if page <= 1:
            return self.LIST_BASE
        return f"{self.LIST_BASE}?page={page}"

    @staticmethod
    def _eval_nuxt(html: str) -> Optional[Dict[str, Any]]:
        """Eval window.__NUXT__ qua Node.js subprocess để giải mã variable substitution."""
        soup = BeautifulSoup(html, "html.parser")
        nuxt_script = None
        for sc in soup.find_all("script"):
            content = sc.string or ""
            if "__NUXT__" in content and len(content) > 5000:
                nuxt_script = content
                break

        if not nuxt_script:
            return None

        js_code = f"var window = {{}};\n{nuxt_script}\nprocess.stdout.write(JSON.stringify(window.__NUXT__), () => process.exit(0));"
        tmp = os.path.join(tempfile.gettempdir(), "eval_devwork_nuxt.js")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(js_code)
            result = subprocess.run(
                ["node", tmp], capture_output=True, timeout=15,
                encoding="utf-8", errors="replace"
            )
            if result.returncode != 0:
                return None
            return json.loads(result.stdout.strip())
        except Exception:
            return None

    def _parse_listing_page(self, html: str) -> Dict[str, Any]:
        """Trả về {'items': [...], 'total_items': n} từ NUXT fetch data."""
        data = self._eval_nuxt(html)
        if not data:
            # Fallback: parse total from HTML text
            total_match = re.search(r"(\d+)\s*kết quả", html)
            total = int(total_match.group(1)) if total_match else 0
            return {"items": [], "total_items": total}

        # Find the fetch key containing jobs
        fetch = data.get("fetch", {})
        jobs = []
        for k, v in fetch.items():
            if isinstance(v, dict) and "jobs" in v and isinstance(v["jobs"], list):
                jobs = v["jobs"]
                break

        # Total from HTML text
        total_match = re.search(r"(\d+)\s*kết quả", html)
        total = int(total_match.group(1)) if total_match else len(jobs)

        return {"items": jobs, "total_items": total}

    @staticmethod
    def _format_salary(salary_obj: Any) -> str:
        """salary {min, max} → chuỗi đọc được."""
        if not isinstance(salary_obj, dict):
            return ""
        mn = salary_obj.get("min")
        mx = salary_obj.get("max")
        if not mn and not mx:
            return ""

        def fmt(v):
            if not v:
                return ""
            v = int(v)
            if v >= 1_000_000:
                val = v / 1_000_000
                return f"{val:.0f} triệu" if val == int(val) else f"{val:.1f} triệu"
            return f"{v:,}".replace(",", ".")

        if mn and mx:
            if mn == mx:
                return f"{fmt(mn)} VND"
            return f"{fmt(mn)} - {fmt(mx)} VND"
        if mx:
            return f"Dưới {fmt(mx)} VND"
        return f"Từ {fmt(mn)} VND"

    def _normalize_job_card(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Chuẩn hóa 1 item từ NUXT jobs[] thành card nội bộ."""
        job_id = str(item.get("id") or "")
        name = item.get("name") or ""
        slug = re.sub(r"[^a-z0-9\-]", "-", name.lower().strip())
        slug = re.sub(r"-+", "-", slug).strip("-")
        job_url = f"{self.BASE_URL}/viec-lam/{job_id}/{slug}" if job_id else ""

        province = item.get("province") or {}
        job_location = province.get("name", "") if isinstance(province, dict) else ""

        emp_type = item.get("type") or {}
        employment_type = emp_type.get("value", "") if isinstance(emp_type, dict) else ""

        exp = item.get("experience") or {}
        experience = exp.get("value", "") if isinstance(exp, dict) else ""

        skills_list = item.get("skills") or []
        skills = ", ".join(s.get("name", "") for s in skills_list if isinstance(s, dict) and s.get("name"))

        logo = item.get("logo") or ""
        if logo and not logo.startswith("http"):
            logo = urljoin(self.BASE_URL, logo)

        # Deadline from service.expired_at (format "dd/mm/yyyy")
        service = item.get("service") or {}
        deadline_iso = ""
        if isinstance(service, dict):
            exp_str = service.get("expired_at") or ""
            m = re.match(r"(\d{2})/(\d{2})/(\d{4})", exp_str)
            if m:
                deadline_iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

        # Created date
        created_at = item.get("created_at") or ""
        posted_iso = ""
        if created_at:
            # ISO format "2026-10-27T..." or unix
            if isinstance(created_at, str) and "T" in created_at:
                posted_iso = created_at[:10]
            elif isinstance(created_at, (int, float)):
                try:
                    posted_iso = datetime.fromtimestamp(int(created_at)).strftime("%Y-%m-%d")
                except (ValueError, OSError):
                    pass

        return {
            "job_id": job_id,
            "job_url": job_url,
            "job_title": name,
            "company_name": "",  # Not in listing NUXT data — will be filled from detail
            "company_id": "",
            "company_url": "",
            "company_logo": logo,
            "salary": self._format_salary(item.get("salary")),
            "job_location": job_location,
            "location_structured": json.dumps(province, ensure_ascii=False) if province else "",
            "deadline_iso": deadline_iso,
            "posted_iso": posted_iso,
            "employment_type": employment_type,
            "experience": experience,
            "skills": skills,
            "industries": "",
            "vacancy_quantity": 0,
        }

    @staticmethod
    def _clean_html(raw_html: str) -> str:
        if not raw_html:
            return ""
        soup = BeautifulSoup(raw_html, "html.parser")
        for li in soup.find_all("li"):
            li.insert_before("- ")
            li.insert_after("\n")
        for tag in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "br"]):
            tag.insert_after("\n")
        lines = [re.sub(r"\s+", " ", l).strip() for l in soup.get_text().split("\n")]
        return "\n".join([l for l in lines if l]).strip()

    def _parse_job_detail_raw(self, html: str, card: Dict[str, Any]) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        content_div = soup.find("div", class_="content")
        if not content_div:
            return self._card_fallback_payload(card)

        full_text = content_div.get_text(" ", strip=True)

        # Title
        h2 = content_div.find(["h1", "h2"])
        job_title = h2.get_text(strip=True) if h2 else card["job_title"]

        # Company name
        company_name = card["company_name"]
        company_el = content_div.find(string=re.compile(r"Công ty|CÔNG TY|Company"))
        if company_el:
            parent = company_el.parent
            company_name = parent.get_text(strip=True)[:80] if parent else str(company_el)[:80]

        # Location
        loc_match = re.search(
            r"(Hà Nội|TP\.?\s*HCM|Đà Nẵng|Tokyo|Osaka|Remote|Địa điểm khác|Hồ Chí Minh)",
            full_text
        )
        job_location = loc_match.group(0) if loc_match else card["job_location"]

        # Salary
        salary_match = re.search(
            r"(Dưới\s*\d+\s*triệu|\d+[-–]\d+\s*triệu|\d+[-–]\d+\s*Man|Thỏa thuận)",
            full_text
        )
        salary = salary_match.group(0) if salary_match else card["salary"]

        # Description
        desc_start = full_text.find("Mô tả công việc")
        if desc_start < 0:
            desc_start = full_text.find("Mô tả")
        req_start = full_text.find("Yêu cầu công việc")
        if req_start < 0:
            req_start = full_text.find("Yêu cầu")

        description = ""
        requirements = ""
        if desc_start >= 0:
            end_desc = req_start if req_start > desc_start else len(full_text)
            description = full_text[desc_start:end_desc].strip()
            # Remove the header itself
            description = re.sub(r"^Mô tả công việc\s*", "", description).strip()
        if req_start >= 0:
            # Requirements go until next section or end
            ben_start = full_text.find("Quyền lợi")
            if ben_start < 0:
                ben_start = full_text.find("Benefits")
            end_req = ben_start if ben_start > req_start else len(full_text)
            requirements = full_text[req_start:end_req].strip()
            requirements = re.sub(r"^Yêu cầu công việc\s*", "", requirements).strip()

        # Benefits
        benefits = ""
        ben_start = full_text.find("Quyền lợi")
        if ben_start < 0:
            ben_start = full_text.find("Benefits")
        if ben_start >= 0:
            benefits = full_text[ben_start:].strip()
            benefits = re.sub(r"^Quyền lợi\s*", "", benefits).strip()
            # Trim at next major section if any
            for marker in ("Thông tin", "Hỗ trợ ứng tuyển", "Ứng tuyển"):
                idx = benefits.find(marker)
                if idx > 0:
                    benefits = benefits[:idx].strip()
                    break

        # Structured fields from "Thông tin" section
        info_idx = full_text.find("Thông tin")
        deadline = card["deadline_iso"]
        employment_type = card["employment_type"]
        experience = card["experience"]
        dl_match = None

        if info_idx >= 0:
            info_section = full_text[info_idx:info_idx + 500]
            # Hạn nộp hồ sơ
            dl_match = re.search(r"Hạn nộp hồ sơ\s+(\d{4}-\d{2}-\d{2})", info_section)
            if dl_match:
                deadline = dl_match.group(1)
            # Hình thức
            ft_match = re.search(r"Hình thức\s+(Full-time|Part-time|Internship|Contract)", info_section, re.I)
            if ft_match:
                employment_type = ft_match.group(1)
            # Kinh nghiệm
            exp_match = re.search(r"Kinh nghiệm\s+(.+?)(?:\s+Trình độ|\s+Vị trí|$)", info_section)
            if exp_match:
                experience = exp_match.group(1).strip()
            # Số lượng → prepend vào requirements
            qty_match = re.search(r"Số lượng\s+(\d+)", info_section)
            if qty_match:
                qty_prefix = f"Số lượng: {qty_match.group(1)} người"
                requirements = f"{qty_prefix}\n{requirements}" if requirements else qty_prefix

        content_basis = f"{job_title}|{company_name}|{salary}|{description[:200]}"
        content_hash = hashlib.sha256(content_basis.encode("utf-8")).hexdigest()

        return {
            "platform": "devwork",
            "job_id": card["job_id"],
            "job_url": card["job_url"],
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": content_hash,
            "job_title": job_title,
            "company_name": company_name,
            "company_id": card["company_id"],
            "company_url": card["company_url"],
            "company_logo": card["company_logo"],
            "salary": salary,
            "job_location": job_location,
            "job_location_structured": card.get("location_structured", ""),
            "deadline": deadline,
            "deadline_source": "detail_dom" if info_idx >= 0 and dl_match else ("listed" if card["deadline_iso"] else ""),
            "updated_date": card["posted_iso"],
            "industries": card["industries"],
            "card_meta_raw": "",
            "description": description,
            "requirements": requirements,
            "benefits": benefits,
            "job_info_raw": card["skills"],
            "posted_date_iso": card["posted_iso"],
            "valid_through": "",
            "employment_type": employment_type,
        }

    def fetch_jobs(
        self,
        mode: str = "incremental",
        max_pages: int = 1,
        watermark_ts: Optional[str] = None,
        last_record_id: Optional[str] = None
    ) -> Iterator[Dict[str, Any]]:
        mode_label = "VÉT CẠN TOÀN BỘ (FULL BACKFILL)" if mode == "full" else "TĂNG TRƯỞNG (INCREMENTAL)"
        is_incremental = (mode == "incremental")
        watermark_hit = False
        watermark_date = watermark_ts[:10] if watermark_ts else ""

        print(f"[*] Bắt đầu quét Devwork.vn [toàn bộ tin IT] - Chế độ: {mode_label} (Tối đa {max_pages} trang)...")

        for page in range(1, max_pages + 1):
            if watermark_hit:
                break

            url = self._list_url(page)
            print(f"   -> Đang tải trang tìm kiếm {page}/{max_pages}: {url}")
            html = self._make_request(url)
            if not html:
                raise CrawlerSourceError(
                    f"{self.source_name} listing page {page} returned no response"
                )

            parsed = self._parse_listing_page(html)
            raw_items = parsed["items"]

            if page == 1:
                if parsed["total_items"]:
                    print(f"      📊 DEVWORK XÁC NHẬN: {parsed['total_items']} việc làm IT")
                self.heartbeat()

            cards = [self._normalize_job_card(it) for it in raw_items if it.get("id")]
            print(f"      [✓] Trang {page} phát hiện {len(cards)} tin.")
            if page == 1:
                require_listing_data(
                    getattr(self, "source_name", "devwork"),
                    len(cards),
                    reported_total=parsed["total_items"],
                    total_known=True,
                )
            if not cards:
                self._empty_result_confirmed = True
                print(f"      [!] Đã hết tin phân trang tại trang {page}. Dừng quét.")
                break

            for card in cards:
                if is_incremental:
                    if last_record_id and str(card["job_id"]) == str(last_record_id):
                        print(f"      🎯 [WATERMARK HIT] Chạm ranh giới tin cũ (Job ID: {card['job_id']}). Dừng quét!")
                        watermark_hit = True
                        self._empty_result_confirmed = True
                        break
                    if watermark_date and card["posted_iso"] and card["posted_iso"] < watermark_date:
                        print(f"      🎯 [WATERMARK HIT] Tin đăng {card['posted_iso']} cũ hơn mốc {watermark_date}. Dừng quét!")
                        watermark_hit = True
                        self._empty_result_confirmed = True
                        break

                self.heartbeat()
                detail_html = self._make_request(card["job_url"])
                if detail_html:
                    try:
                        yield self._parse_job_detail_raw(detail_html, card)
                    except Exception as parse_err:
                        print(f"       [!] Lỗi bóc tách chi tiết, giữ payload mức thẻ tin: {parse_err}")
                        yield self._card_fallback_payload(card)
                else:
                    print(f"       [!] Không tải được trang chi tiết {card['job_id']} — dùng Engine fallback.")
                    yield self._card_fallback_payload(card)

                time.sleep(random.uniform(self.REQUEST_DELAY_MIN, self.REQUEST_DELAY_MAX))

            if watermark_hit:
                break
            time.sleep(random.uniform(0.8, 1.2))

    def _card_fallback_payload(self, card: Dict[str, Any]) -> Dict[str, Any]:
        """Zero data loss: trang chi tiết lỗi vẫn giữ trọn thông tin mức thẻ tin."""
        content_basis = f"{card['job_title']}|{card['company_name']}|{card['salary']}|"
        return {
            "platform": "devwork",
            "job_id": card["job_id"],
            "job_url": card["job_url"],
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": hashlib.sha256(content_basis.encode("utf-8")).hexdigest(),
            "job_title": card["job_title"],
            "company_name": card["company_name"],
            "company_id": card["company_id"],
            "company_url": card["company_url"],
            "company_logo": card["company_logo"],
            "salary": card["salary"],
            "job_location": card["job_location"],
            "job_location_structured": card.get("location_structured", ""),
            "deadline": card["deadline_iso"],
            "deadline_source": "listed" if card["deadline_iso"] else "",
            "updated_date": card["posted_iso"],
            "industries": card["industries"],
            "card_meta_raw": "",
            "description": "",
            "requirements": "",
            "benefits": "",
            "job_info_raw": card["skills"],
            "posted_date_iso": card["posted_iso"],
            "valid_through": "",
            "employment_type": card["employment_type"],
        }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Bot cào Devwork.vn chuẩn Tầng Bronze 2.0 (toàn bộ tin IT)")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental", help="Chế độ cào")
    parser.add_argument("--max-pages", type=int, default=1, help="Số trang quét tối đa (20 tin/trang. Mặc định: 1)")
    args = parser.parse_args()

    crawler = DevworkCrawler()
    result = crawler.run(mode=args.mode, max_pages=args.max_pages)
    raise SystemExit(emit_cli_result(result))
