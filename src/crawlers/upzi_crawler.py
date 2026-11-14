import re
import os
import time
import json
import random
import hashlib
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

try:
    from curl_cffi import requests
    HAVE_CURL_CFFI = True
except ImportError:
    import requests
    HAVE_CURL_CFFI = False

from src.crawlers.base_crawler import BaseCrawler


class UpziCrawler(BaseCrawler):
    """
    Bot cào sàn Upzi.vn (Powered by VietnamWorks / Navigos) chuẩn Tầng Bronze 2.0.
    - Ma trận lọc IT cố định: ngành "Phần Mềm CNTT/Dịch vụ Phần mềm" UUID khóa cứng trong URL
      để KHÔNG bao giờ cào tin ngoài IT.
    - Phân trang server-side qua query param: ?industry=<uuid>&page=N (trang 1 SSR lazy ~16 tin,
      trang 2+ trả 50 tin/trang).
    - Engine 1 listing: bóc JSON từ RSC flight stream (initialResponse.data[]) — đầy đủ UUID id,
      alias (slug), company, salary, location, createdAt/approvedAt ISO.
    - Engine 1 detail: JSON-LD JobPosting nhúng trong HTML trang chi tiết (/viec-lam/{alias})
      cho description HTML, datePosted, validThrough, employmentType, hiringOrganization, jobLocation,
      jobBenefits, skills, industry; bổ sung parsedShouldHaveRequirement[]/parsedMandatoryRequirement[]
      và benefits[] từ flight stream chi tiết.
    - Engine 2 fallback: payload mức thẻ tin (zero data loss) khi trang chi tiết lỗi.
    - Incremental: list sắp theo approvedAt giảm dần → dừng khi chạm last_record_id (id_cursor)
      hoặc approvedAt cũ hơn mốc watermark (datetime guard nghiêm ngặt <).
    """

    BASE_URL = "https://www.upzi.vn"
    INDUSTRY_UUID = "4ba29694-240d-11f0-a329-022cb52f85bb"  # Phần Mềm CNTT/Dịch vụ Phần mềm
    LIST_BASE = f"https://www.upzi.vn/viec-lam?industry={INDUSTRY_UUID}"
    REQUEST_DELAY_MIN = 1.0
    REQUEST_DELAY_MAX = 1.8

    # Regex patterns
    FLIGHT_PUSH_RE = re.compile(
        r'self\.__next_f\.push\(\s*\[\s*1\s*,\s*("(?:[^"\\]|\\.)*")\s*\]\s*\)', re.S
    )

    def __init__(self, **kwargs):
        super().__init__(source_name="upzi", **kwargs)
        self.cookie = self._load_cookie()
        self.session = self._create_session()

    def _load_cookie(self) -> str:
        candidates = [
            os.path.join(os.path.dirname(__file__), "cookies", "upzi_cookie.txt"),
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

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
            "Referer": "https://www.upzi.vn/",
        }
        s.headers.update(headers)

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

                if resp.status_code in [404, 410]:
                    print(f"      [-] Tin không tồn tại hoặc đã đóng (HTTP {resp.status_code})")
                    return None

                if resp.status_code in [500, 502, 503, 504]:
                    time.sleep(2.0 + attempt)
                    continue

                print(f"      [-] HTTP {resp.status_code} khi tải {url}")
                time.sleep(1.5 + attempt)

            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1.5 + attempt)
                else:
                    print(f"      [!] Lỗi kết nối {url}: {e}")
                    return None
        return None

    def _list_url(self, page: int) -> str:
        return f"{self.LIST_BASE}&page={page}"

    @staticmethod
    def _decode_flight_stream(html: str) -> str:
        """Giải mã các chunk self.__next_f.push([1,"..."]) thành blob text liên tục."""
        chunks = []
        for m in UpziCrawler.FLIGHT_PUSH_RE.finditer(html):
            try:
                chunks.append(json.loads(m.group(1)))
            except Exception:
                pass
        return "".join(chunks)

    # Anchor pattern: flexible whitespace after colons for both minified and pretty-printed JSON
    INITIAL_RESPONSE_ANCHOR_RE = re.compile(
        r'"initialResponse"\s*:\s*\{\s*"status"\s*:\s*"success"'
    )

    @staticmethod
    def _extract_json_object(blob: str, anchor_re: 're.Pattern') -> Optional[Dict[str, Any]]:
        """Bóc object JSON đầu tiên sau anchor regex bằng brace matching (xử lý string escape)."""
        m = anchor_re.search(blob)
        if not m:
            return None
        i = m.start()
        start = blob.find('{', i + len('"initialResponse"'))
        if start == -1:
            return None
        depth = 0
        in_str = False
        esc = False
        for p in range(start, len(blob)):
            ch = blob[p]
            if in_str:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(blob[start:p + 1])
                    except Exception:
                        return None
        return None

    def _parse_listing_page(self, html: str) -> Dict[str, Any]:
        """Trả về {'jobs': [...], 'pagination': {...}, 'total': int}."""
        blob = self._decode_flight_stream(html)
        obj = self._extract_json_object(blob, self.INITIAL_RESPONSE_ANCHOR_RE)
        if not obj:
            return {"jobs": [], "pagination": {}, "total": 0}

        jobs = obj.get("data", [])
        meta = obj.get("meta", {})
        pagination = meta.get("pagination", {})
        total = pagination.get("total", 0)

        return {"jobs": jobs, "pagination": pagination, "total": total}

    def _normalize_job_card(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Chuẩn hóa một job object từ listing API thành card dict nội bộ."""
        job_id = raw.get("id", "")
        alias = raw.get("alias", "")
        job_url = f"{self.BASE_URL}/viec-lam/{alias}" if alias else ""

        company = raw.get("company") or {}
        company_id = company.get("id", "")
        company_name = company.get("name", "")
        company_logo = company.get("logo", "")
        company_url = f"{self.BASE_URL}/cong-ty/{company.get('alias', '')}" if company.get("alias") else ""

        locations = raw.get("workingLocations") or []
        job_location = ", ".join(locations) if isinstance(locations, list) else str(locations)

        salary = raw.get("prettySalary", "")
        if not salary or salary == "Lương không công khai":
            sal_min = raw.get("salaryMin", 0)
            sal_max = raw.get("salaryMax", 0)
            currency = raw.get("salaryCurrency", "VND")
            if sal_min or sal_max:
                salary = f"{sal_min:,} - {sal_max:,} {currency}".replace(",000", "K") if sal_max else f"Tới {sal_max:,} {currency}"

        # approvedAt là thời điểm tin được duyệt (ISO 8601 với timezone)
        approved_at = raw.get("approvedAt", "") or raw.get("createdAt", "")
        # Chuẩn hóa về yyyy-mm-dd cho watermark comparison
        approved_iso = ""
        if approved_at:
            try:
                dt = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
                approved_iso = dt.strftime("%Y-%m-%d")
            except (ValueError, AttributeError):
                approved_iso = approved_at[:10] if len(approved_at) >= 10 else ""

        deadline_raw = raw.get("applicationDeadline", "")
        deadline_iso = ""
        if deadline_raw:
            try:
                dt = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
                deadline_iso = dt.strftime("%Y-%m-%d")
            except (ValueError, AttributeError):
                deadline_iso = deadline_raw[:10] if len(deadline_raw) >= 10 else ""

        employment_type = raw.get("employmentType", "")
        working_arrangement = raw.get("workingArrangement", "")
        weekend_text = raw.get("workingOnWeekendText", "")

        return {
            "job_id": job_id,
            "job_url": job_url,
            "job_title": raw.get("jobTitle", ""),
            "company_name": company_name,
            "company_id": company_id,
            "company_url": company_url,
            "company_logo": company_logo,
            "salary": salary,
            "job_location": job_location,
            "deadline_raw": deadline_raw,
            "deadline_iso": deadline_iso,
            "approved_at_raw": approved_at,
            "approved_iso": approved_iso,
            "employment_type": employment_type,
            "working_arrangement": working_arrangement,
            "weekend_text": weekend_text,
            "job_level": raw.get("jobLevel", ""),
            "alias": alias,
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

    def _extract_json_ld_job(self, html: str) -> Dict[str, Any]:
        """JobPosting JSON-LD của Upzi nằm trong HTML trang chi tiết — bóc bằng brace matching."""
        idx = html.find('"@type":"JobPosting"')
        if idx == -1:
            return {}
        start = html.rfind("{", 0, idx)
        if start == -1:
            return {}
        depth = 0
        end = 0
        for i in range(start, min(len(html), start + 500000)):
            ch = html[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end == 0:
            return {}
        try:
            return json.loads(html[start:end])
        except Exception:
            return {}

    def _parse_detail_flight_extras(self, html: str) -> Dict[str, Any]:
        """Bóc parsedShouldHaveRequirement[], parsedMandatoryRequirement[], benefits[] từ flight stream chi tiết."""
        blob = self._decode_flight_stream(html)
        result = {"requirements_parsed": [], "benefits_parsed": []}

        # parsedShouldHaveRequirement
        m = re.search(r'"parsedShouldHaveRequirement"\s*:\s*(\[.*?\])', blob)
        if m:
            try:
                result["requirements_parsed"] = json.loads(m.group(1))
            except Exception:
                pass

        # parsedMandatoryRequirement
        m = re.search(r'"parsedMandatoryRequirement"\s*:\s*(\[.*?\])', blob)
        if m:
            try:
                reqs = json.loads(m.group(1))
                result["requirements_parsed"] = reqs + result["requirements_parsed"]
            except Exception:
                pass

        # benefits array from flight
        m = re.search(r'"benefits"\s*:\s*(\[.*?\])', blob)
        if m:
            try:
                raw_benefits = json.loads(m.group(1))
                for b in raw_benefits:
                    name = b.get("name", "")
                    desc = self._clean_html(b.get("description", ""))
                    if name or desc:
                        result["benefits_parsed"].append(f"{name}: {desc}" if name and desc else (name or desc))
            except Exception:
                pass

        return result

    def _parse_job_detail_raw(self, html: str, card: Dict[str, Any]) -> Dict[str, Any]:
        ld = self._extract_json_ld_job(html)
        extras = self._parse_detail_flight_extras(html)

        job_title = ld.get("title", "") or card["job_title"]

        org = ld.get("hiringOrganization") or {}
        company_name = org.get("name", "") or card["company_name"]
        company_logo = card["company_logo"]

        loc = ld.get("jobLocation") or {}
        addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
        job_location_structured = json.dumps(loc, ensure_ascii=False) if loc else ""
        job_location = ""
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality", ""), addr.get("addressRegion", "")]
            job_location = ", ".join(p for p in parts if p)
        if not job_location:
            job_location = card["job_location"]

        salary = card["salary"]

        description = self._clean_html(ld.get("description", ""))

        # Requirements from JSON-LD or flight extras
        requirements = ""
        if extras["requirements_parsed"]:
            requirements = "\n".join(f"- {r}" for r in extras["requirements_parsed"] if r)
        if not requirements:
            # Try to extract from description sections
            soup = BeautifulSoup(html, "html.parser")
            for h_tag in soup.find_all(["h2", "h3", "h4"]):
                htext = h_tag.get_text(strip=True).lower()
                if any(kw in htext for kw in ["yêu cầu", "requirement", "qualification"]):
                    nxt = h_tag.find_next_sibling()
                    if nxt:
                        requirements = nxt.get_text("\n", strip=True)
                    break

        # Benefits from JSON-LD jobBenefits or flight extras
        benefits = ""
        if extras["benefits_parsed"]:
            benefits = "\n".join(f"- {b}" for b in extras["benefits_parsed"])
        if not benefits:
            jb = ld.get("jobBenefits")
            if isinstance(jb, list):
                benefits = "\n".join(f"- {b}" for b in jb if b)
            elif isinstance(jb, str) and jb:
                benefits = jb

        # Skills from JSON-LD
        skills = ld.get("skills", "")
        if isinstance(skills, list):
            skills = ", ".join(str(s) for s in skills)

        # Industry from JSON-LD
        industry = ld.get("industry", "")
        if isinstance(industry, list):
            industry = ", ".join(str(i) for i in industry)

        employment_type = ld.get("employmentType", "") or card["employment_type"]
        if isinstance(employment_type, list):
            employment_type = ", ".join(employment_type)

        posted_date_iso = ld.get("datePosted", "")
        valid_through = ld.get("validThrough", "")

        # Deadline: prefer JSON-LD validThrough, fallback to card
        deadline = card["deadline_iso"]
        if valid_through:
            try:
                dt = datetime.fromisoformat(valid_through.replace("Z", "+00:00"))
                deadline = dt.strftime("%Y-%m-%d")
            except (ValueError, AttributeError):
                deadline = valid_through[:10] if len(valid_through) >= 10 else valid_through

        content_for_hash = f"{job_title}|{company_name}|{salary}|{description[:200]}"
        content_hash = hashlib.sha256(content_for_hash.encode("utf-8")).hexdigest()

        return {
            "platform": "upzi",
            "job_id": card["job_id"],
            "job_url": card["job_url"],
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": content_hash,
            "job_title": job_title,
            "company_name": company_name,
            "company_id": card["company_id"],
            "company_url": card["company_url"],
            "company_logo": company_logo,
            "salary": salary,
            "job_location": job_location,
            "job_location_structured": job_location_structured,
            "deadline": deadline,
            "deadline_source": "json_ld" if valid_through else ("listed" if card["deadline_iso"] else ""),
            "updated_date": card["approved_iso"],
            "industries": industry,
            "card_meta_raw": "",
            "description": description,
            "requirements": requirements,
            "benefits": benefits,
            "job_info_raw": f"Skills: {skills}" if skills else "",
            "posted_date_iso": posted_date_iso,
            "valid_through": valid_through,
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

        print(f"[*] Bắt đầu quét Upzi [ma trận IT: {self.INDUSTRY_UUID[:8]}...] - Chế độ: {mode_label} (Tối đa {max_pages} trang)...")

        for page in range(1, max_pages + 1):
            if watermark_hit:
                break

            url = self._list_url(page)
            print(f"   -> Đang tải trang tìm kiếm {page}/{max_pages}: {url}")
            html = self._make_request(url)
            if not html:
                print(f"      [!] Không tải được trang {page}, kết thúc quét.")
                break

            parsed = self._parse_listing_page(html)
            raw_jobs = parsed["jobs"]
            pagination = parsed["pagination"]
            total = parsed["total"]

            if page == 1:
                if total:
                    print(f"      📊 UPZI XÁC NHẬN: {total} việc làm trong ma trận IT ({self.INDUSTRY_UUID[:8]}...)")
                    print(f"         Phân trang: {pagination.get('totalPages', '?')} trang, {pagination.get('pageSize', '?')} tin/trang")
                self.heartbeat()

            cards = [self._normalize_job_card(j) for j in raw_jobs if j.get("id")]
            print(f"      [✓] Trang {page} phát hiện {len(cards)} tin trong ma trận IT.")
            if not cards:
                print(f"      [!] Đã hết tin phân trang tại trang {page}. Dừng quét.")
                break

            for card in cards:
                if is_incremental:
                    if last_record_id and str(card["job_id"]) == str(last_record_id):
                        print(f"      🎯 [WATERMARK HIT] Chạm ranh giới tin cũ Upzi (Job ID: {card['job_id']}). Dừng quét!")
                        watermark_hit = True
                        break
                    # Datetime guard: chỉ dừng khi approvedAt CỦ HƠN hẳn mốc (so sánh nghiêm ngặt <)
                    if watermark_date and card["approved_iso"] and card["approved_iso"] < watermark_date:
                        print(f"      🎯 [WATERMARK HIT] Tin approved {card['approved_iso']} cũ hơn mốc {watermark_date}. Dừng quét!")
                        watermark_hit = True
                        break

                self.heartbeat()  # fetch-phase heartbeat trước mỗi lần tải trang chi tiết
                detail_html = self._make_request(card["job_url"])
                if detail_html:
                    try:
                        yield self._parse_job_detail_raw(detail_html, card)
                    except Exception as parse_err:
                        print(f"       [!] Lỗi bóc tách chi tiết, giữ nguyên payload mức thẻ tin: {parse_err}")
                        yield self._card_fallback_payload(card)
                else:
                    print(f"       [!] Không tải được trang chi tiết {card['job_id']} — dùng Engine fallback mức thẻ tin.")
                    yield self._card_fallback_payload(card)

                time.sleep(random.uniform(self.REQUEST_DELAY_MIN, self.REQUEST_DELAY_MAX))

            if watermark_hit:
                break
            time.sleep(random.uniform(0.8, 1.2))

    def _card_fallback_payload(self, card: Dict[str, Any]) -> Dict[str, Any]:
        """Zero data loss: trang chi tiết lỗi vẫn giữ trọn thông tin mức thẻ tin."""
        content_for_hash = f"{card['job_title']}|{card['company_name']}|{card['salary']}|"
        return {
            "platform": "upzi",
            "job_id": card["job_id"],
            "job_url": card["job_url"],
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": hashlib.sha256(content_for_hash.encode("utf-8")).hexdigest(),
            "job_title": card["job_title"],
            "company_name": card["company_name"],
            "company_id": card["company_id"],
            "company_url": card["company_url"],
            "company_logo": card["company_logo"],
            "salary": card["salary"],
            "job_location": card["job_location"],
            "job_location_structured": "",
            "deadline": card["deadline_iso"],
            "deadline_source": "listed" if card["deadline_iso"] else "",
            "updated_date": card["approved_iso"],
            "industries": "",
            "card_meta_raw": "",
            "description": "",
            "requirements": "",
            "benefits": "",
            "job_info_raw": "",
            "posted_date_iso": "",
            "valid_through": "",
            "employment_type": card["employment_type"],
        }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Bot cào Upzi.vn chuẩn Tầng Bronze 2.0 (ma trận IT Phần Mềm CNTT)")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental", help="Chế độ cào")
    parser.add_argument("--max-pages", type=int, default=1, help="Số trang quét tối đa (50 tin/trang. Mặc định: 1)")
    args = parser.parse_args()

    crawler = UpziCrawler()
    crawler.run(mode=args.mode, max_pages=args.max_pages)
