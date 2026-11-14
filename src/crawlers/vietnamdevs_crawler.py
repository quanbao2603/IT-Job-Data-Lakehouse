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


class VietnamDevsCrawler(BaseCrawler):
    """
    Bot cào sàn VietnamDevs.com chuẩn Tầng Bronze 2.0.
    - KHÔNG cần ma trận lọc IT: toàn bộ tin đăng trên vietnamdevs.com đều là việc làm IT/tech
      cho English speakers tại Việt Nam.
    - Phân trang server-side qua query param: ?page=N (60 tin/trang, tổng ~84 tin, 2 trang).
    - Engine 1 listing: parse DOM SSR — div.card-hoverable chứa company name (p.font-extrabold > span),
      job title (h2 > a), location/type/skills/time posted trong body card.
    - Engine 1 detail: JSON-LD JobPosting nhúng trong HTML trang chi tiết (/jobs/{id}/{slug})
      cho description HTML, datePosted, validThrough, employmentType, hiringOrganization, jobLocation.
    - Engine 2 fallback: payload mức thẻ tin (zero data loss) khi trang chi tiết lỗi.
    - Incremental: list sắp theo thời gian đăng giảm dần → dừng khi chạm last_record_id (id_cursor)
      hoặc ngày đăng cũ hơn mốc watermark (datetime guard nghiêm ngặt <).
    """

    BASE_URL = "https://vietnamdevs.com"
    LIST_BASE = "https://vietnamdevs.com/jobs"
    REQUEST_DELAY_MIN = 1.0
    REQUEST_DELAY_MAX = 1.8

    # Regex patterns
    JOB_URL_RE = re.compile(r"/jobs/(\d+)/[a-z0-9\-]+")
    TOTAL_JOBS_RE = re.compile(r"(\d+)\s*jobs?", re.I)
    TIME_AGO_RE = re.compile(r"(\d+[dhm]|yesterday|today|\d+\s+(?:day|hour|minute|week|month)s?\s+ago)", re.I)
    LOCATION_TYPE_RE = re.compile(
        r"(Ho Chi Minh City|Hanoi|Da Nang|Remote|Hybrid|On-site|Singapore|Bangkok|Other)"
        r"[\s·]+(Full-time|Part-time|Contract|Internship)",
        re.I
    )

    def __init__(self, **kwargs):
        super().__init__(source_name="vietnamdevs", **kwargs)
        self.cookie = self._load_cookie()
        self.session = self._create_session()

    def _load_cookie(self) -> str:
        candidates = [
            os.path.join(os.path.dirname(__file__), "cookies", "vietnamdevs_cookie.txt"),
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
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
            "Referer": "https://vietnamdevs.com/",
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
        return f"{self.LIST_BASE}?page={page}"

    def _parse_total_jobs(self, html: str) -> Optional[int]:
        m = self.TOTAL_JOBS_RE.search(html)
        if not m:
            return None
        try:
            return int(m.group(1))
        except ValueError:
            return None

    def _time_ago_to_iso(self, value: str) -> str:
        """'1d', '2h', '3 days ago', 'yesterday' → yyyy-mm-dd."""
        if not value:
            return ""
        value = value.lower().strip()
        now = datetime.now()

        if value == "today":
            return now.strftime("%Y-%m-%d")
        if value == "yesterday":
            return (now - timedelta(days=1)).strftime("%Y-%m-%d")

        # Pattern: Nd, Nh, Nm
        m = re.match(r"^(\d+)([dhm])$", value)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            if unit == "d":
                return (now - timedelta(days=n)).strftime("%Y-%m-%d")
            elif unit == "h":
                return (now - timedelta(hours=n)).strftime("%Y-%m-%d")
            elif unit == "m":
                return (now - timedelta(minutes=n)).strftime("%Y-%m-%d")

        # Pattern: N days/hours/minutes/weeks/months ago
        m = re.search(r"(\d+)\s+(day|hour|minute|week|month)s?\s+ago", value)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            if unit == "day":
                return (now - timedelta(days=n)).strftime("%Y-%m-%d")
            elif unit == "hour":
                return (now - timedelta(hours=n)).strftime("%Y-%m-%d")
            elif unit == "minute":
                return (now - timedelta(minutes=n)).strftime("%Y-%m-%d")
            elif unit == "week":
                return (now - timedelta(weeks=n)).strftime("%Y-%m-%d")
            elif unit == "month":
                return (now - timedelta(days=n * 30)).strftime("%Y-%m-%d")

        return ""

    def _parse_cards(self, html: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        cards: List[Dict[str, Any]] = []

        containers = soup.find_all("div", class_="card-hoverable")

        for card_el in containers:
            # Job link and ID
            link = card_el.find("a", href=self.JOB_URL_RE)
            if not link:
                continue
            href = link.get("href", "")
            m = self.JOB_URL_RE.search(href)
            if not m:
                continue
            job_id = m.group(1)
            job_url = urljoin(self.BASE_URL, href.split("?")[0])

            # Job title from h2
            h2 = card_el.find("h2")
            job_title = h2.get_text(strip=True) if h2 else link.get_text(strip=True)

            # Company name: p.font-extrabold > span.truncate
            company_name = ""
            company_p = card_el.find("p", class_=re.compile(r"font-extrabold"))
            if company_p:
                span = company_p.find("span", class_="truncate")
                company_name = span.get_text(strip=True) if span else company_p.get_text(strip=True)

            # Company meta: p.text-sm.text-gray-500 → "Consultancy · 51-150 employees · Singapore"
            company_meta = ""
            meta_p = card_el.find("p", class_=re.compile(r"text-sm.*text-gray-500"))
            if meta_p:
                company_meta = meta_p.get_text(strip=True)

            # Company logo
            company_logo = ""
            img = card_el.find("img", alt=re.compile(r"logo", re.I))
            if img:
                company_logo = img.get("src", "")

            # Location and employment type from card body text
            card_text = card_el.get_text(" ", strip=True)
            loc_match = self.LOCATION_TYPE_RE.search(card_text)
            job_location = loc_match.group(1) if loc_match else ""
            employment_type = loc_match.group(2) if loc_match else ""

            # Skills/tags
            skills = []
            tag_els = card_el.find_all(["span", "a"], class_=re.compile(r"tag|skill|badge|chip|bg-gray-100", re.I))
            for t in tag_els:
                txt = t.get_text(strip=True)
                if txt and len(txt) < 30 and txt not in skills:
                    skills.append(txt)
            # Also extract from text after location/type
            if loc_match:
                after_loc = card_text[loc_match.end():]
                # Skills are usually short words separated by spaces or dots
                potential_skills = re.findall(r"\b([A-Z][a-zA-Z0-9+#\.]{1,20})\b", after_loc[:200])
                for ps in potential_skills[:5]:
                    if ps not in skills and ps not in ("Ho", "Chi", "Minh", "City", "Full", "Part", "time"):
                        skills.append(ps)

            # Time posted
            time_match = self.TIME_AGO_RE.search(card_text)
            posted_raw = time_match.group(0) if time_match else ""
            posted_iso = self._time_ago_to_iso(posted_raw)

            cards.append({
                "job_id": job_id,
                "job_url": job_url,
                "job_title": job_title,
                "company_name": company_name,
                "company_meta": company_meta,
                "company_logo": company_logo,
                "salary": "",  # Not shown on listing
                "job_location": job_location,
                "employment_type": employment_type,
                "skills": ", ".join(skills[:8]),
                "posted_raw": posted_raw,
                "posted_iso": posted_iso,
            })
        return cards

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
        """JobPosting JSON-LD của VietnamDevs nằm trong <script type='application/ld+json'>."""
        soup = BeautifulSoup(html, "html.parser")
        scripts = soup.find_all("script", type="application/ld+json")
        for sc in scripts:
            try:
                data = json.loads(sc.string or sc.get_text())
                if isinstance(data, dict) and data.get("@type") == "JobPosting":
                    return data
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") == "JobPosting":
                            return item
            except Exception:
                continue
        return {}

    def _parse_job_detail_raw(self, html: str, card: Dict[str, Any]) -> Dict[str, Any]:
        ld = self._extract_json_ld_job(html)

        job_title = ld.get("title", "") or card["job_title"]

        org = ld.get("hiringOrganization") or {}
        company_name = org.get("name", "") or card["company_name"]
        company_logo = org.get("logo", "") or card["company_logo"]

        loc = ld.get("jobLocation") or {}
        addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
        job_location_structured = json.dumps(loc, ensure_ascii=False) if loc else ""
        job_location = ""
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality", ""), addr.get("addressRegion", "")]
            job_location = ", ".join(p for p in parts if p)
        if not job_location:
            job_location = card["job_location"]

        salary = card["salary"]  # Not available on this platform

        description = self._clean_html(ld.get("description", ""))

        # Extract requirements and benefits from description HTML
        requirements = ""
        benefits = ""
        soup = BeautifulSoup(html, "html.parser")
        for h_tag in soup.find_all(["h2", "h3"]):
            htext = h_tag.get_text(strip=True).lower()
            if "requirement" in htext or "qualification" in htext:
                nxt = h_tag.find_next_sibling()
                if nxt:
                    requirements = nxt.get_text("\n", strip=True)
            elif "benefit" in htext or "offer" in htext or "perk" in htext:
                nxt = h_tag.find_next_sibling()
                if nxt:
                    benefits = nxt.get_text("\n", strip=True)

        employment_type = ld.get("employmentType", "") or card["employment_type"]
        if isinstance(employment_type, list):
            employment_type = ", ".join(employment_type)

        posted_date_iso = ld.get("datePosted", "")
        valid_through = ld.get("validThrough", "")

        # Deadline from JSON-LD validThrough
        deadline = ""
        if valid_through:
            try:
                dt = datetime.fromisoformat(valid_through.replace("Z", "+00:00"))
                deadline = dt.strftime("%Y-%m-%d")
            except (ValueError, AttributeError):
                deadline = valid_through[:10] if len(valid_through) >= 10 else valid_through

        content_for_hash = f"{job_title}|{company_name}|{salary}|{description[:200]}"
        content_hash = hashlib.sha256(content_for_hash.encode("utf-8")).hexdigest()

        return {
            "platform": "vietnamdevs",
            "job_id": card["job_id"],
            "job_url": card["job_url"],
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": content_hash,
            "job_title": job_title,
            "company_name": company_name,
            "company_id": "",  # No separate company ID on this platform
            "company_url": "",
            "company_logo": company_logo,
            "salary": salary,
            "job_location": job_location,
            "job_location_structured": job_location_structured,
            "deadline": deadline,
            "deadline_source": "json_ld" if valid_through else "",
            "updated_date": card["posted_iso"],
            "industries": "",  # All IT, no specific industry tags
            "card_meta_raw": card.get("company_meta", ""),
            "description": description,
            "requirements": requirements,
            "benefits": benefits,
            "job_info_raw": card.get("skills", ""),
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

        print(f"[*] Bắt đầu quét VietnamDevs [toàn bộ tin IT] - Chế độ: {mode_label} (Tối đa {max_pages} trang)...")

        for page in range(1, max_pages + 1):
            if watermark_hit:
                break

            url = self._list_url(page)
            print(f"   -> Đang tải trang tìm kiếm {page}/{max_pages}: {url}")
            html = self._make_request(url)
            if not html:
                print(f"      [!] Không tải được trang {page}, kết thúc quét.")
                break

            if page == 1:
                total = self._parse_total_jobs(html)
                if total:
                    print(f"      📊 VIETNAMDEVS XÁC NHẬN: {total} việc làm IT")
                self.heartbeat()

            cards = self._parse_cards(html)
            print(f"      [✓] Trang {page} phát hiện {len(cards)} tin.")
            if not cards:
                print(f"      [!] Đã hết tin phân trang tại trang {page}. Dừng quét.")
                break

            for card in cards:
                if is_incremental:
                    if last_record_id and str(card["job_id"]) == str(last_record_id):
                        print(f"      🎯 [WATERMARK HIT] Chạm ranh giới tin cũ VietnamDevs (Job ID: {card['job_id']}). Dừng quét!")
                        watermark_hit = True
                        break
                    # Datetime guard: chỉ dừng khi ngày đăng CỦ HƠN hẳn mốc (so sánh nghiêm ngặt <)
                    if watermark_date and card["posted_iso"] and card["posted_iso"] < watermark_date:
                        print(f"      🎯 [WATERMARK HIT] Tin đăng {card['posted_iso']} cũ hơn mốc {watermark_date}. Dừng quét!")
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
            "platform": "vietnamdevs",
            "job_id": card["job_id"],
            "job_url": card["job_url"],
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": hashlib.sha256(content_for_hash.encode("utf-8")).hexdigest(),
            "job_title": card["job_title"],
            "company_name": card["company_name"],
            "company_id": "",
            "company_url": "",
            "company_logo": card["company_logo"],
            "salary": card["salary"],
            "job_location": card["job_location"],
            "job_location_structured": "",
            "deadline": "",
            "deadline_source": "",
            "updated_date": card["posted_iso"],
            "industries": "",
            "card_meta_raw": card.get("company_meta", ""),
            "description": "",
            "requirements": "",
            "benefits": "",
            "job_info_raw": card.get("skills", ""),
            "posted_date_iso": "",
            "valid_through": "",
            "employment_type": card["employment_type"],
        }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Bot cào VietnamDevs.com chuẩn Tầng Bronze 2.0 (toàn bộ tin IT)")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental", help="Chế độ cào")
    parser.add_argument("--max-pages", type=int, default=1, help="Số trang quét tối đa (60 tin/trang. Mặc định: 1)")
    args = parser.parse_args()

    crawler = VietnamDevsCrawler()
    crawler.run(mode=args.mode, max_pages=args.max_pages)
