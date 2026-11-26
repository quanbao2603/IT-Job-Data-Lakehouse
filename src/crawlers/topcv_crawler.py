import os
import sys
import re
import time
import random
import argparse
import hashlib
import json
import tempfile
from datetime import datetime
from typing import List, Dict, Any, Optional, Iterator

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
    incremental_scan_pages,
    require_listing_data,
)


class TopCVCrawler(BaseCrawler):
    BASE_URL = "https://www.topcv.vn"
    SEARCH_BASE = "https://www.topcv.vn/tim-viec-lam-it?page={page}"
    # Tunable without editing code: TopCV starts returning HTTP 429 when the crawl
    # exceeds roughly 15-20 requests/minute from one IP.
    # Measured against TopCV: 2-2.5s trips 429 after ~20 requests, 6-8s after ~6, while 15-19s
    # ran 425 requests without a single block. The default is the only pace that survives a
    # daily incremental, so it is the default and callers do not have to remember it.
    REQUEST_DELAY_MIN = float(os.getenv("TOPCV_DELAY_MIN", "15.0"))
    REQUEST_DELAY_MAX = float(os.getenv("TOPCV_DELAY_MAX", "19.0"))
    # curl_cffi impersonates a real Chrome TLS fingerprint, so Cloudflare no longer
    # answers 403 for every request. A faster burst (20-30) drew HTTP 429 from TopCV,
    # so the crawl runs at ~20 requests/minute: 10-15 requests, then a 20-30s pause,
    # plus a 2-3s delay per request. A real 403 still trips the 15 minute breaker.
    REQUESTS_BEFORE_COOLDOWN_MIN = int(os.getenv("TOPCV_BURST_MIN", "8"))
    REQUESTS_BEFORE_COOLDOWN_MAX = int(os.getenv("TOPCV_BURST_MAX", "10"))
    COOLDOWN_MIN = float(os.getenv("TOPCV_COOLDOWN_MIN", "15"))
    COOLDOWN_MAX = float(os.getenv("TOPCV_COOLDOWN_MAX", "20"))
    BLOCKED_COOLDOWN_SECONDS = 15 * 60

    def __init__(self, **kwargs):
        super().__init__(source_name="topcv", **kwargs)
        self.cookie = self._load_cookie()
        self.session = self._create_session()
        self._requests_since_cooldown = 0
        self._next_cooldown_after = self._choose_next_cooldown()
        self.cooldown_state_path = os.environ.get(
            "LAKEHOUSE_TOPCV_COOLDOWN_STATE",
            os.path.join("data", "operational", "topcv_cooldown.json"),
        )

    def _choose_next_cooldown(self) -> int:
        return random.randint(self.REQUESTS_BEFORE_COOLDOWN_MIN, self.REQUESTS_BEFORE_COOLDOWN_MAX)

    def _pace_after_request(self) -> None:
        self._requests_since_cooldown = getattr(self, "_requests_since_cooldown", 0) + 1
        next_cooldown_after = getattr(
            self,
            "_next_cooldown_after",
            self.REQUESTS_BEFORE_COOLDOWN_MAX,
        )
        if self._requests_since_cooldown >= next_cooldown_after:
            pause = random.uniform(self.COOLDOWN_MIN, self.COOLDOWN_MAX)
            print(f"      [~] TopCV nghỉ chống dồn request {pause:.1f}s sau {self._requests_since_cooldown} request.")
            time.sleep(pause)
            self._requests_since_cooldown = 0
            self._next_cooldown_after = self._choose_next_cooldown()

    def _read_cooldown_state(self) -> Dict[str, Any]:
        path = getattr(
            self,
            "cooldown_state_path",
            os.environ.get(
                "LAKEHOUSE_TOPCV_COOLDOWN_STATE",
                os.path.join("data", "operational", "topcv_cooldown.json"),
            ),
        )
        try:
            with open(path, encoding="utf-8") as stream:
                payload = json.load(stream)
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _write_cooldown_state(self, payload: Dict[str, Any]) -> None:
        path = getattr(
            self,
            "cooldown_state_path",
            os.environ.get(
                "LAKEHOUSE_TOPCV_COOLDOWN_STATE",
                os.path.join("data", "operational", "topcv_cooldown.json"),
            ),
        )
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".topcv-cooldown-", dir=directory, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _assert_not_blocked(self) -> None:
        state = self._read_cooldown_state()
        blocked_until = float(state.get("blocked_until", 0) or 0)
        if blocked_until > time.time():
            remaining = int(blocked_until - time.time())
            raise CrawlerSourceError(
                f"{self.source_name} cooldown active for HTTP 403 ({remaining}s remaining); no request sent"
            )

    def _activate_block_cooldown(self) -> None:
        now = time.time()
        self._write_cooldown_state(
            {
                "source": self.source_name,
                "reason": "HTTP_403",
                "blocked_at": now,
                "blocked_until": now + self.BLOCKED_COOLDOWN_SECONDS,
            }
        )
        self._requests_since_cooldown = 0
        raise CrawlerSourceError(
            f"{self.source_name} request returned HTTP 403; "
            f"circuit breaker active for {self.BLOCKED_COOLDOWN_SECONDS}s"
        )

    def _clear_block_cooldown(self) -> None:
        state = self._read_cooldown_state()
        if state.get("blocked_until"):
            self._write_cooldown_state({})

    def _load_cookie(self) -> str:
        candidates = [
            os.path.join(os.path.dirname(__file__), "cookies", "topcv_cookie.txt"),
            os.path.join(os.path.dirname(__file__), "topcv", "topcv_cookie.txt"),
        ]
        for c_path in candidates:
            if os.path.exists(c_path):
                try:
                    with open(c_path, 'r', encoding='utf-8') as f:
                        lines = [l.strip() for l in f.readlines() if l.strip() and not l.startswith('#')]
                        if lines:
                            return "; ".join(lines)
                except Exception:
                    pass
        return ""

    def _create_session(self):
        """curl_cffi owns the whole browser profile.

        Adding even "harmless" headers (Referer/Accept-Language) on top of the
        impersonated profile made Cloudflare answer 403 with a cookie that otherwise
        returns 200, so the session is left untouched apart from the cookie dict.
        """
        if HAVE_CURL_CFFI:
            s = requests.Session(impersonate="chrome")
        else:
            s = requests.Session()
            s.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
            })
        self._cookies = {}
        if self.cookie:
            for item in self.cookie.split(";"):
                if "=" in item:
                    k, v = item.strip().split("=", 1)
                    self._cookies[k] = v
        return s

    def _make_request(self, url: str, max_retries: int = 4, base_backoff: float = 3.0) -> Optional[str]:
        for attempt in range(max_retries):
            try:
                # The burst limiter runs before the next request. A 403 also
                # persists a circuit breaker so Airflow retries cannot hammer
                # TopCV again from a fresh Python process.
                self._assert_not_blocked()
                if getattr(self, "_requests_since_cooldown", 0) >= getattr(
                    self, "_next_cooldown_after", self.REQUESTS_BEFORE_COOLDOWN_MAX
                ):
                    pause = random.uniform(self.COOLDOWN_MIN, self.COOLDOWN_MAX)
                    print(f"      [~] TopCV nghỉ trước burst tiếp theo {pause:.1f}s.")
                    time.sleep(pause)
                    self._requests_since_cooldown = 0
                    self._next_cooldown_after = self._choose_next_cooldown()
                resp = self.session.get(url, timeout=20, cookies=getattr(self, "_cookies", None) or None)

                if resp.status_code == 200:
                    self._requests_since_cooldown = getattr(self, "_requests_since_cooldown", 0) + 1
                    self._clear_block_cooldown()
                    return resp.text

                if resp.status_code == 429:
                    self._requests_since_cooldown = getattr(self, "_requests_since_cooldown", 0) + 1
                    sleep_time = (base_backoff * (2 ** attempt)) + random.uniform(1.0, 3.0)
                    print(f"      [!] Bị giới hạn tốc độ (HTTP 429), ngủ {sleep_time:.1f}s trước khi thử lại...")
                    time.sleep(sleep_time)
                    continue

                if resp.status_code == 403:
                    self._activate_block_cooldown()

                if resp.status_code in [404, 410]:
                    print(f"      [-] Tin không tồn tại hoặc đã đóng (HTTP {resp.status_code})")
                    return None

                if resp.status_code in [500, 502, 503, 504]:
                    self._requests_since_cooldown = getattr(self, "_requests_since_cooldown", 0) + 1
                    time.sleep(2.0 + attempt)
                    continue

                print(f"      [-] HTTP {resp.status_code} khi tải {url}")
                self._requests_since_cooldown = getattr(self, "_requests_since_cooldown", 0) + 1
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

    def _extract_job_id(self, url: str) -> str:
        m = re.search(r'/([^/]+)\.html', url)
        if m:
            slug = m.group(1)
            id_m = re.search(r'(\d+)$', slug)
            return id_m.group(1) if id_m else slug
        return url.split('/')[-1]

    @staticmethod
    def _clean(value: str) -> str:
        """Collapse whitespace and drop unrendered template fragments."""
        text = re.sub(r"\s+", " ", value or "").strip()
        return "" if any(marker in text for marker in ("{{", "}}", "${")) else text

    def _extract_company_name(self, soup) -> str:
        """Company name lives in `.box-company-info-detail .company-name-label a.name`.

        The old selector (`div.company-name`) matched the Angular template node first, so
        every TopCV record stored `{{ company?.name }}` even on fully rendered pages.
        """
        candidates = []
        node = (
            soup.select_one(".box-company-info-detail .company-name-label a.name")
            or soup.select_one(".company-name-label a.name")
            or soup.select_one(".company-name-label a")
        )
        if node:
            candidates.append(node.get_text(" ", strip=True))
            candidates.append(node.get("title") or "")
        logo = soup.select_one("a.company-logo")
        if logo:
            candidates.append(logo.get("title") or "")
            image = logo.select_one("img")
            if image:
                candidates.append(image.get("alt") or "")
        for candidate in candidates:
            text = self._clean(candidate)
            if text:
                return text
        return ""

    def _label_values(self, soup, root: str = "") -> Dict[str, str]:
        """Collect `label: value` pairs from the job and company info blocks."""
        containers = (
            (
                ".job-detail__info--section-content",
                ".job-detail__info--section-content-title",
                ".job-detail__info--section-content-value",
            ),
            (
                ".box-company-info-detail__list--item",
                ".box-company-info-detail__list--item-label",
                ".box-company-info-detail__list--item-value",
            ),
            (".general-item", ".title", ".value"),
            (".box-item", ".title", ".value"),
        )
        values: Dict[str, str] = {}
        for container, label_selector, value_selector in containers:
            scope = soup.select_one(root) if root else soup
            if scope is None:
                return {}
            for item in scope.select(container):
                label = item.select_one(label_selector)
                value = item.select_one(value_selector)
                if not label or not value:
                    continue
                key = label.get_text(" ", strip=True).rstrip(":").strip().lower()
                text = self._clean(value.get_text(" ", strip=True))
                if key and text and key not in values:
                    values[key] = text
        return values

    @staticmethod
    def _first(values: Dict[str, str], *keys: str) -> str:
        for key in keys:
            if values.get(key):
                return values[key]
        return ""

    def _parse_listing_card(self, card) -> Optional[Dict[str, Any]]:
        """One search-result card: title, company, city, experience, salary.

        The listing page is server-rendered and already contains the employer, so a
        25-request listing crawl can replace a 1.100-request detail crawl when TopCV
        rate-limits the detail endpoint.
        """
        link = card.select_one("a[href*='/viec-lam/']")
        href = (link.get("href") or "").split("?")[0] if link else ""
        if not re.search(r"/viec-lam/[^/]+/\d+\.html$", href):
            return None
        if href.startswith("/"):
            href = self.BASE_URL + href

        def text(selector: str) -> str:
            node = card.select_one(selector)
            return self._clean(node.get_text(" ", strip=True)) if node else ""

        job_title = text("h3") or text("a.title")
        company_name = text(".company-name") or text(".company a") or text(".company")
        city = text(".city-text") or text(".address")
        experience = text(".exp")
        salary = text(".salary") or text(".box-right .salary")
        if not salary:
            match = re.search(r"(\d[\d.,]*\s*-\s*\d[\d.,]*\s*triệu|Tới\s+\d[\d.,]*\s*triệu|Tho[ảa]\s*thuận)", card.get_text(" ", strip=True))
            salary = match.group(1) if match else "Thỏa thuận"
        if not job_title or not company_name:
            return None
        return {
            "platform": "topcv",
            "job_id": self._extract_job_id(href),
            "job_url": href,
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": hashlib.sha256(f"{job_title}|{company_name}|{salary}|{city}".encode()).hexdigest(),
            "company_name": company_name,
            "job_title": job_title,
            "salary": salary,
            "job_location": city,
            "location": city,
            "experience": experience,
            "job_level": "",
            "company_size": "",
            "company_address": "",
            "company_industry": "",
            "work_mode": "",
            "contract_type": "",
            "deadline": "",
            "education": "",
            "quantity": "",
            "overview_requirements": "",
            "overview_skills": "",
            "job_description_raw": "",
            "requirements_raw": "",
            "industry_knowledge": "",
            "required_skills": "",
            "benefits_raw": "",
            "work_address": city,
            "work_time": "",
        }

    def _parse_job_detail_raw(self, html_content: str, job_url: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html_content, 'html.parser')

        title_elem = soup.find('h1', class_=re.compile(r'job-title|title', re.I)) or soup.find('h1')
        job_title = title_elem.get_text(strip=True) if title_elem else ""

        company_name = self._extract_company_name(soup)

        salary_elem = soup.find(class_=re.compile(r'salary|salary-range', re.I))
        salary = salary_elem.get_text(strip=True) if salary_elem else "Thỏa thuận"
        # A rendered page appends a lookup widget to the salary text.
        salary = re.split(r'Tra cứu lương|Xem mức lương', salary)[0].strip() or "Thỏa thuận"

        general_items = self._label_values(soup)
        job_location = self._first(general_items, 'địa điểm tuyển dụng', 'địa điểm làm việc', 'địa điểm', 'nơi làm việc')
        experience = self._first(general_items, 'kinh nghiệm', 'kinh nghiệm làm việc')
        deadline = self._first(general_items, 'hạn nộp hồ sơ', 'hạn nộp')
        job_level = self._first(general_items, 'cấp bậc', 'chức vụ')
        education = self._first(general_items, 'bằng cấp', 'học vấn')
        quantity = self._first(general_items, 'số lượng tuyển', 'số lượng')
        work_mode = self._first(general_items, 'hình thức làm việc', 'hình thức')
        contract_type = self._first(general_items, 'loại hợp đồng')

        # TopCV serves an Angular shell until the client hydrates it. When the page is
        # captured pre-render there is no company name and no location/level at all.
        # Fail closed instead: a skipped page surfaces as PARSER_FAILURE, not as data.
        if not company_name and not any(
            value.strip() for value in (job_location, experience, job_level, education, contract_type)
        ):
            raise ValueError("TopCV trả về trang template chưa render (company/location/level rỗng)")

        overview_requirements = ""
        overview_skills = ""
        for sec in soup.find_all('div', class_=re.compile(r'job-overview', re.I)):
            sec_text = sec.get_text('\n', strip=True)
            if 'yêu cầu' in sec_text.lower():
                overview_requirements = sec_text
            if 'kỹ năng' in sec_text.lower():
                overview_skills = sec_text

        def extract_section(keywords):
            for h_tag in soup.find_all(['h2', 'h3', 'h4']):
                h_text = h_tag.get_text(strip=True).lower()
                if any(kw in h_text for kw in keywords):
                    content = h_tag.find_next_sibling()
                    if not content:
                        content = h_tag.parent.find_next_sibling()
                    if content:
                        return content.get_text('\n', strip=True)
            return ""

        job_description = extract_section(['mô tả công việc', 'mô tả', 'job description'])
        candidate_requirements = extract_section(['yêu cầu ứng viên', 'yêu cầu công việc', 'requirements'])
        benefits = extract_section(['quyền lợi', 'quyền lợi được hưởng', 'benefits'])

        tag_skills = []
        for tag in soup.find_all(class_=re.compile(r'tag-item|badge-item|skill-item', re.I)):
            st = tag.get_text(strip=True)
            if st and st not in tag_skills and len(st) < 40:
                tag_skills.append(st)
        required_skills = ", ".join(tag_skills)

        industry_knowledge = ""
        for div in soup.find_all('div', class_=re.compile(r'tag-group|tag-container', re.I)):
            label = div.find(class_=re.compile(r'label|title', re.I))
            if label and 'ngành nghề' in label.get_text(strip=True).lower():
                tags = [t.get_text(strip=True) for t in div.find_all('a')]
                industry_knowledge = ", ".join(tags)

        work_address = ""
        addr_sec = soup.find(class_=re.compile(r'work-address|address-content', re.I))
        if addr_sec:
            work_address = addr_sec.get_text(strip=True)

        work_time = ""
        time_sec = soup.find(class_=re.compile(r'work-time|time-content', re.I))
        if time_sec:
            work_time = time_sec.get_text(strip=True)

        company_items = self._label_values(soup, root=".box-company-info-detail")
        company_size = self._first(company_items, 'quy mô', 'quy mô công ty')
        company_address = self._first(company_items, 'địa chỉ', 'địa điểm')
        company_industry = self._first(company_items, 'ngành nghề', 'lĩnh vực')

        content_for_hash = f"{job_title}|{company_name}|{salary}|{job_description[:200]}"
        content_hash = hashlib.sha256(content_for_hash.encode('utf-8')).hexdigest()

        return {
            "platform": "topcv",
            "job_id": self._extract_job_id(job_url),
            "job_url": job_url,
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": content_hash,
            "company_name": company_name,
            "job_title": job_title,
            "salary": salary or "Thỏa thuận",
            "job_location": job_location,
            "location": job_location,
            "experience": experience,
            "deadline": deadline,
            "job_level": job_level,
            "education": education,
            "quantity": quantity,
            "work_mode": work_mode,
            "contract_type": contract_type,
            "overview_requirements": overview_requirements,
            "overview_skills": overview_skills,
            "job_description_raw": job_description,
            "requirements_raw": candidate_requirements,
            "industry_knowledge": industry_knowledge,
            "required_skills": required_skills,
            "benefits_raw": benefits,
            "work_address": work_address,
            "work_time": work_time,
            "company_size": company_size,
            "company_address": company_address,
            "company_industry": company_industry,
        }

    def fetch_jobs(
        self,
        mode: str = "incremental",
        max_pages: int = 1,
        watermark_ts: Optional[str] = None,
        last_record_id: Optional[str] = None
    ) -> Iterator[Dict[str, Any]]:
        mode_label = "VÉT CẠN TOÀN BỘ (FULL BACKFILL)" if mode == "full" else "TĂNG TRƯỞNG (INCREMENTAL)"
        print(f"[*] Bắt đầu quét TopCV CNTT (cr257) - Chế độ: {mode_label} (Tối đa {max_pages} trang)...")

        candidates_urls = []
        listing_records: list[Dict[str, Any]] = []
        listing_only = os.getenv("TOPCV_LISTING_ONLY", "0").strip().lower() in {"1", "true", "yes"}
        seen = set()
        reported_total = None

        scan_pages = incremental_scan_pages(mode, max_pages, getattr(self, "source_name", "topcv"))
        for page in range(1, scan_pages + 1):

            url = self.SEARCH_BASE.format(page=page)
            print(f"   -> Đang tải trang tìm kiếm {page}/{scan_pages}: {url}")
            html = self._make_request(url)
            if not html:
                raise CrawlerSourceError(
                    f"{self.source_name} listing page {page} returned no response"
                )

            soup = BeautifulSoup(html, 'html.parser')

            if page == 1:
                header_count = soup.find(string=re.compile(r'Tuyển dụng\s+([\d\.\,]+)\s+việc làm', re.I))
                if header_count:
                    count_match = re.search(r'([\d\.\,]+)', header_count)
                    reported_total = int(count_match.group(1).replace('.', '').replace(',', '')) if count_match else None
                    print(f"      📊 TOPCV XÁC NHẬN: {header_count.strip()}")

            job_links = []
            for a in soup.find_all('a', href=re.compile(r'/viec-lam/[^/]+/\d+\.html')):
                href = a.get('href', '').split('?')[0]
                if href and href not in seen:
                    seen.add(href)
                    jid = self._extract_job_id(href)

                    if mode == "incremental" and last_record_id and str(jid) == str(last_record_id):
                        # TopCV promotes paid listings, so the boundary card can sit at the top of
                        # page 1. Skipping it (not stopping) is what keeps the day's new jobs.
                        print(f"      ↷ [MỐC] Bỏ qua tin đã cào {jid}, tiếp tục quét.")
                        continue

                    job_links.append(href)

            print(f"      [✓] Trang {page} phát hiện {len(job_links)} tin tuyển dụng mới.")
            self.heartbeat()

            if not job_links:
                if page == 1:
                    require_listing_data(
                        getattr(self, "source_name", "topcv"),
                        0,
                        reported_total=reported_total,
                        total_known=reported_total is not None,
                    )
                self._empty_result_confirmed = True
                print(f"      [!] Đã hết tin phân trang tại trang {page}. Dừng quét.")
                break

            candidates_urls.extend(job_links)

            if listing_only:
                for card in soup.select(".job-item-search-result"):
                    record = self._parse_listing_card(card)
                    if record:
                        listing_records.append(record)

            if page < scan_pages:
                time.sleep(random.uniform(1.0, 1.5))

        if listing_only:
            print(f"\n[*] LISTING-ONLY: {len(listing_records)} tin lấy trực tiếp từ trang danh sách (không gọi trang chi tiết)")
            for record in listing_records:
                yield record
            return

        print(f"\n[*] TỔNG CỘNG PHÁT HIỆN: {len(candidates_urls)} tin tuyển dụng TopCV. BẮT ĐẦU CÀO STREAMING...")
        for idx, j_url in enumerate(candidates_urls, 1):
            jid = self._extract_job_id(j_url)
            print(f"  [{idx:3d}/{len(candidates_urls):3d}] Đang cào Job[{jid}]: {j_url}")
            detail_html = self._make_request(j_url)
            if detail_html:
                try:
                    item = self._parse_job_detail_raw(detail_html, j_url)
                    print(f"       [✓] {item['job_title'][:40]} | Lương: {item['salary']} | Kinh nghiệm: {item['experience'] or 'N/A'}")
                    yield item
                except Exception as parse_err:
                    print(f"       [!] Lỗi bóc tách: {parse_err}")
            else:
                print(f"       [-] Không tải được nội dung tin {jid}")

            time.sleep(random.uniform(self.REQUEST_DELAY_MIN, self.REQUEST_DELAY_MAX))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Bot cào TopCV chuẩn Tầng Bronze 2.0 (Zero Data Loss)")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental", help="Chế độ cào")
    parser.add_argument("--max-pages", type=int, default=1, help="Số trang quét tối đa (Mặc định: 1)")
    args = parser.parse_args()

    crawler = TopCVCrawler()
    result = crawler.run(mode=args.mode, max_pages=args.max_pages)
    raise SystemExit(emit_cli_result(result))
