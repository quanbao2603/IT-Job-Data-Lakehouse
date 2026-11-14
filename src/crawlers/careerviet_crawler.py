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


class CareerVietCrawler(BaseCrawler):
    """
    Bot cào sàn CareerViet (tiền thân CareerBuilder.vn) chuẩn Tầng Bronze 2.0.
    - Ma trận lọc IT cố định: ngành nghề c1 (CNTT - Phần mềm) + c63 (CNTT - Phần cứng / Mạng),
      khóa cứng trong LIST URL để KHÔNG bao giờ cào tin ngoài IT.
    - Phân trang server-side theo segment đường dẫn: `-trang-{n}-` (trang 1 không có segment).
    - Engine 1: thẻ tin `article.campaign-job-card` (SSR) + JSON-LD JobPosting nhúng trong
      luồng RSC ở trang chi tiết; Engine 2 fallback: payload mức thẻ tin (zero data loss).
    - Incremental: list mặc định sắp theo ngày cập nhật giảm dần → dừng khi chạm
      last_record_id (id_cursor) hoặc ngày cập nhật của thẻ cũ hơn mốc watermark (datetime guard).
    """

    BASE_URL = "https://careerviet.vn"
    LIST_PAGE1 = "https://careerviet.vn/viec-lam/cntt-phan-mem-cntt-phan-cung-mang-c1,63-vi.html"
    LIST_PAGE_N = "https://careerviet.vn/viec-lam/cntt-phan-mem-cntt-phan-cung-mang-c1,63-trang-{page}-vi.html"
    IT_CATEGORY_IDS = "1,63"  # 1 = CNTT - Phần mềm | 63 = CNTT - Phần cứng / Mạng
    REQUEST_DELAY_MIN = 1.0
    REQUEST_DELAY_MAX = 1.8

    JOB_URL_RE = re.compile(r"/vi/tim-viec-lam/[a-z0-9\-]+\.([0-9A-F]{8})\.html", re.I)
    EMPLOYER_URL_RE = re.compile(r"/vi/nha-tuyen-dung/[a-z0-9\-]+\.([0-9A-F]{8})\.html", re.I)
    DATE_RE = re.compile(r"\b(\d{2}-\d{2}-\d{4})\b")
    RELATIVE_RE = re.compile(r"\b(\d+)\s+(ngày|tuần|tháng)\b")
    SALARY_RE = re.compile(r"(Lương\s*:[^|]+?(?:VND|USD)|Cạnh tranh|Thương lượng|Thỏa thuận)")
    DEADLINE_RE = re.compile(r"Hạn nộp\s*:\s*([0-9\-]{8,10}|\d+\s+(?:ngày|tuần|tháng))")
    COUNTDOWN_RE = re.compile(r"Cơ hội ứng tuyển chỉ còn\s*:\s*(\d+\s*(?:ngày|tuần|tháng))")
    UPDATED_RE = re.compile(r"Cập nhật\s*:\s*([0-9\-]{8,10}|\d+\s+(?:ngày|tuần|tháng))")

    def __init__(self, **kwargs):
        super().__init__(source_name="careerviet", **kwargs)
        self.cookie = self._load_cookie()
        self.session = self._create_session()

    def _load_cookie(self) -> str:
        candidates = [
            os.path.join(os.path.dirname(__file__), "cookies", "careerviet_cookie.txt"),
            os.path.join(os.path.dirname(__file__), "careerviet", "careerviet_cookie.txt"),
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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
            "Referer": "https://careerviet.vn/",
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
                resp = self.session.get(url, timeout=20)

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
        if page <= 1:
            return self.LIST_PAGE1
        return self.LIST_PAGE_N.format(page=page)

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

    def _parse_total_jobs(self, html: str) -> Optional[int]:
        m = re.search(r"([\d\.]+)\s+việc làm", html)
        if not m:
            return None
        try:
            return int(m.group(1).replace(".", ""))
        except ValueError:
            return None

    def _date_to_iso(self, value: str) -> str:
        """dd-mm-yyyy → yyyy-mm-dd; chuỗi tương đối 'N ngày/tuần/tháng' → hôm nay - N."""
        if not value:
            return ""
        m = self.DATE_RE.search(value)
        if m:
            d, mo, y = m.group(1).split("-")
            return f"{y}-{mo}-{d}"
        rel = self.RELATIVE_RE.search(value)
        if rel:
            n = int(rel.group(1))
            unit = rel.group(2)
            days = n if unit == "ngày" else (n * 7 if unit == "tuần" else n * 30)
            return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return ""

    @staticmethod
    def _normalize_meta_label(text: str) -> str:
        """'Hạn nộp<!-- -->: 13-12-2026' → 'Hạn nộp: 13-12-2026'.
        Comment HTML của React nằm giữa nhãn và dấu ':' nên phải ghép lại trước khi regex."""
        text = re.sub(r"<!--.*?-->", "", text)
        text = re.sub(r"(Hạn nộp|Cập nhật)\s*:", r"\1: ", text)
        return text

    def _parse_cards(self, html: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        cards: List[Dict[str, Any]] = []

        containers = soup.find_all("div", class_="job-item")
        containers += soup.find_all("article", class_="campaign-job-card")

        for card_el in containers:
            title_link = card_el.find("a", class_="job_link") or card_el.find("a", href=self.JOB_URL_RE)
            if not title_link:
                continue
            href = title_link.get("href", "")
            m = self.JOB_URL_RE.search(href)
            job_id = title_link.get("data-id") or (m.group(1) if m else "")
            if not job_id:
                continue
            job_url = urljoin(self.BASE_URL, href.split("?")[0])
            job_title = title_link.get("title") or title_link.get_text(strip=True)

            company_link = card_el.find("a", href=self.EMPLOYER_URL_RE)
            company_name = company_link.get("title") or company_link.get_text(strip=True) if company_link else ""
            company_id = ""
            company_url = ""
            if company_link:
                em = self.EMPLOYER_URL_RE.search(company_link.get("href", ""))
                company_id = em.group(1) if em else ""
                company_url = urljoin(self.BASE_URL, company_link["href"].split("?")[0])

            # Dòng meta có nhãn tường minh: "Lương: ... <loc> Hạn nộp: dd-mm-yyyy | Cập nhật: dd-mm-yyyy <ngành...>"
            # Campaign card không có các nhãn này → fallback: không có meta thời gian/địa điểm.
            meta_raw = self._normalize_meta_label(card_el.get_text(" ", strip=True))
            if not self.DEADLINE_RE.search(meta_raw) and not self.UPDATED_RE.search(meta_raw) \
                    and not self.COUNTDOWN_RE.search(meta_raw) and not re.search(r"VND|USD", meta_raw):
                meta_raw = ""

            dl_m = self.DEADLINE_RE.search(meta_raw)
            up_m = self.UPDATED_RE.search(meta_raw)
            deadline_raw = dl_m.group(1) if dl_m else ""
            deadline_source = "listed" if deadline_raw else ""
            if not deadline_raw:
                cd_m = self.COUNTDOWN_RE.search(meta_raw)
                if cd_m:
                    deadline_raw = cd_m.group(1)
                    deadline_source = "countdown"
            updated_raw = up_m.group(1) if up_m else ""
            if not updated_raw:
                rel = self.RELATIVE_RE.search(meta_raw)
                updated_raw = f"{rel.group(1)} {rel.group(2)}" if rel else ""

            sal_m = self.SALARY_RE.search(meta_raw)
            salary = sal_m.group(1).strip() if sal_m else ""

            job_location = ""
            if sal_m:
                end_idx = dl_m.start() if dl_m else len(meta_raw)
                for marker in ("Hạn nộp", "Cập nhật", "Cơ hội", "MỚI", "URGENT", "Hot", "Ứng", "Lưu"):
                    mi = meta_raw.find(marker, sal_m.end())
                    if mi != -1 and mi < end_idx:
                        end_idx = mi
                job_location = re.sub(r"\s+", " ", meta_raw[sal_m.end():end_idx].replace("|", " ")).strip()

            industries = ""
            if up_m:
                tail = meta_raw[up_m.end():]
                industries = re.sub(r"[\|\+]\s*\d*", " ", tail)
                industries = re.sub(r"\s+", " ", industries).strip()

            cards.append({
                "job_id": job_id,
                "job_url": job_url,
                "job_title": job_title,
                "company_name": company_name,
                "company_id": company_id,
                "company_url": company_url,
                "salary": salary,
                "job_location": job_location,
                "deadline_raw": deadline_raw,
                "deadline_source": deadline_source,
                "updated_raw": updated_raw,
                "updated_iso": self._date_to_iso(updated_raw),
                "deadline_iso": self._date_to_iso(deadline_raw),
                "industries": industries,
                "card_meta_raw": meta_raw,
            })
        return cards

    def _extract_json_ld_job(self, html: str) -> Dict[str, Any]:
        """JobPosting JSON-LD của CareerViet nằm thô bên trong luồng RSC — bóc bằng brace matching."""
        idx = html.find('"@type":"JobPosting"')
        if idx == -1:
            return {}
        start = html.rfind("{", 0, idx)
        if start == -1:
            return {}
        depth = 0
        end = 0
        for i in range(start, min(len(html), start + 400000)):
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

    @staticmethod
    def _unq(value: Any) -> Any:
        """Gỡ một lớp escape quote còn sót khi JSON-LD nằm trong chuỗi flight stream."""
        if isinstance(value, list):
            return [CareerVietCrawler._unq(v) for v in value]
        if isinstance(value, str) and len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            return value[1:-1]
        return value

    def _parse_job_detail_raw(self, html: str, card: Dict[str, Any]) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        ld = self._extract_json_ld_job(html)

        h1 = soup.find("h1")
        job_title = (self._unq(ld.get("title")) or (h1.get_text(strip=True) if h1 else "")) or card["job_title"]

        org = ld.get("hiringOrganization") or {}
        company_name = self._unq(org.get("name")) or card["company_name"]
        company_logo = self._unq(org.get("logo")) or ""

        loc = ld.get("jobLocation") or {}
        addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
        job_location_structured = json.dumps(loc, ensure_ascii=False) if loc else ""
        job_location = addr.get("addressLocality") or addr.get("addressRegion") or card["job_location"]

        salary = card["salary"]
        base_salary = ld.get("baseSalary")
        if base_salary and not salary:
            salary = json.dumps(base_salary, ensure_ascii=False)

        description = self._clean_html(self._unq(ld.get("description")) or "")
        if not description:
            for h_tag in soup.find_all(["h2", "h3"]):
                if "mô tả công việc" in h_tag.get_text(strip=True).lower():
                    nxt = h_tag.find_next_sibling()
                    description = nxt.get_text("\n", strip=True) if nxt else ""
                    break

        def extract_section(keywords):
            for h_tag in soup.find_all(["h2", "h3"]):
                htext = h_tag.get_text(strip=True).lower()
                if any(kw in htext for kw in keywords):
                    c = h_tag.find_next_sibling()
                    if not c:
                        c = h_tag.parent.find_next_sibling()
                    if c:
                        return c.get_text("\n", strip=True)
            return ""

        requirements = extract_section(["yêu cầu"])
        benefits = extract_section(["phúc lợi", "quyền lợi"])
        job_info_raw = extract_section(["thông tin công việc", "thông tin chung"])

        content_for_hash = f"{job_title}|{company_name}|{salary}|{description[:200]}"
        content_hash = hashlib.sha256(content_for_hash.encode("utf-8")).hexdigest()

        return {
            "platform": "careerviet",
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
            "deadline": card["deadline_iso"] or self._unq(ld.get("validThrough", "")),
            "deadline_source": card["deadline_source"],
            "updated_date": card["updated_iso"],
            "industries": card["industries"],
            "card_meta_raw": card["card_meta_raw"],
            "description": description,
            "requirements": requirements,
            "benefits": benefits,
            "job_info_raw": job_info_raw,
            "posted_date_iso": self._unq(ld.get("datePosted", "")),
            "valid_through": self._unq(ld.get("validThrough", "")),
            "employment_type": self._unq(ld.get("employmentType", "")),
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

        print(f"[*] Bắt đầu quét CareerViet [ma trận IT: c{self.IT_CATEGORY_IDS}] - Chế độ: {mode_label} (Tối đa {max_pages} trang)...")

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
                    print(f"      📊 CAREERVIET XÁC NHẬN: {total} việc làm trong ma trận IT (c{self.IT_CATEGORY_IDS})")
                self.heartbeat()

            cards = self._parse_cards(html)
            print(f"      [✓] Trang {page} phát hiện {len(cards)} tin trong ma trận IT.")
            if not cards:
                print(f"      [!] Đã hết tin phân trang tại trang {page}. Dừng quét.")
                break

            for card in cards:
                if is_incremental:
                    if last_record_id and str(card["job_id"]) == str(last_record_id):
                        print(f"      🎯 [WATERMARK HIT] Chạm ranh giới tin cũ CareerViet (Job ID: {card['job_id']}). Dừng quét!")
                        watermark_hit = True
                        break
                    # Datetime guard: chỉ dừng khi ngày cập nhật CỦ HƠN hẳn mốc (so sánh nghiêm ngặt
                    # theo ngày); trùng ngày vẫn quét tiếp và nhường quyết định cho id_cursor.
                    if watermark_date and card["updated_iso"] and card["updated_iso"] < watermark_date:
                        print(f"      🎯 [WATERMARK HIT] Tin cập nhật {card['updated_iso']} cũ hơn mốc {watermark_date}. Dừng quét!")
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
            "platform": "careerviet",
            "job_id": card["job_id"],
            "job_url": card["job_url"],
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": hashlib.sha256(content_for_hash.encode("utf-8")).hexdigest(),
            "job_title": card["job_title"],
            "company_name": card["company_name"],
            "company_id": card["company_id"],
            "company_url": card["company_url"],
            "company_logo": "",
            "salary": card["salary"],
            "job_location": card["job_location"],
            "job_location_structured": "",
            "deadline": card["deadline_iso"],
            "deadline_source": card["deadline_source"],
            "updated_date": card["updated_iso"],
            "industries": card["industries"],
            "card_meta_raw": card["card_meta_raw"],
            "description": "",
            "requirements": "",
            "benefits": "",
            "job_info_raw": "",
            "posted_date_iso": "",
            "valid_through": "",
            "employment_type": "",
        }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Bot cào CareerViet chuẩn Tầng Bronze 2.0 (ma trận IT c1,63)")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental", help="Chế độ cào")
    parser.add_argument("--max-pages", type=int, default=1, help="Số trang quét tối đa (50 tin/trang. Mặc định: 1)")
    args = parser.parse_args()

    crawler = CareerVietCrawler()
    crawler.run(mode=args.mode, max_pages=args.max_pages)
