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
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests
    HAVE_CURL_CFFI = True
except ImportError:
    import requests
    HAVE_CURL_CFFI = False

from src.crawlers.base_crawler import BaseCrawler


class TopDevCrawler(BaseCrawler):
    """
    Bot cào dữ liệu sàn TopDev chuẩn Tầng Bronze 2.0 (Dual-Engine RSC + DOM).
    - Bộ lọc chuẩn 13 nhóm ngành IT chuyên sâu:
      job_categories_ids=2,3,4,5,6,7,8,9,10,11,12,13,67
    - Giải mã trực tiếp luồng Next.js RSC Flight Push chứa full 15 jobs/trang.
    - Hỗ trợ cào vét cạn toàn sàn (Full Backfill) và cào tăng trưởng (Incremental).
    - Quản lý phiên Session keep-alive với Chrome 124 TLS chống Cloudflare block.
    """

    TOPDEV_IT_CATEGORY_IDS = "2,3,4,5,6,7,8,9,10,11,12,13,67"
    SEARCH_BASE = f"https://topdev.vn/viec-lam/tim-kiem?job_categories_ids={TOPDEV_IT_CATEGORY_IDS}&page={{page}}"
    REQUEST_DELAY_MIN = 1.0
    REQUEST_DELAY_MAX = 1.8

    def __init__(self, **kwargs):
        super().__init__(source_name="topdev", **kwargs)
        self.cookie = self._load_cookie()
        self._init_session()

    def _init_session(self):
        """Khởi tạo phiên Session tái sử dụng kết nối HTTP/2 keep-alive."""
        if HAVE_CURL_CFFI:
            self.session = requests.Session(impersonate="chrome124")
        else:
            self.session = requests.Session()

    def _load_cookie(self) -> str:
        candidates = [
            os.path.join(os.path.dirname(__file__), "cookies", "topdev_cookie.txt"),
            os.path.join(os.path.dirname(__file__), "topdev", "topdev_cookie.txt"),
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

    def _make_request(self, url: str, max_retries: int = 4, base_backoff: float = 3.0) -> Optional[str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
            "Referer": "https://topdev.vn/viec-lam/tim-kiem",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie

        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, headers=headers, timeout=25)

                if resp.status_code == 200:
                    return resp.text

                if resp.status_code == 429:
                    sleep_time = (base_backoff * (2 ** attempt)) + random.uniform(1.0, 2.5)
                    print(f"      [!] Bị giới hạn tốc độ (HTTP 429), ngủ {sleep_time:.1f}s trước khi thử lại...")
                    time.sleep(sleep_time)
                    continue

                if resp.status_code in [500, 502, 503, 504]:
                    time.sleep(2.0 + attempt)
                    continue

                print(f"      [-] HTTP {resp.status_code} khi tải {url}")
                return None

            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2.0 + attempt)
                else:
                    print(f"      [!] Lỗi kết nối {url}: {e}")
                    return None
        return None

    def _clean_html(self, raw_html: str) -> str:
        if not raw_html:
            return ""
        soup = BeautifulSoup(raw_html, "html.parser")
        for li in soup.find_all("li"):
            li.insert_before("• ")
            li.insert_after("\n")
        for br in soup.find_all(["br", "p", "div", "h1", "h2", "h3", "h4", "h5", "h6"]):
            br.insert_after("\n")
        text = soup.get_text()
        lines = [re.sub(r"\s+", " ", l).strip() for l in text.split("\n")]
        return "\n".join([l for l in lines if l]).strip()

    def _parse_rsc_bytes(self, b: bytes) -> Dict[str, Any]:
        """
        Giải mã luồng RSC Byte Stream của Next.js App Router.
        """
        chunks = {}
        i = 0
        n = len(b)
        decoder = json.JSONDecoder()
        pattern = re.compile(rb"([0-9a-fA-F]+):")

        while i < n:
            while i < n and b[i : i + 1] in b"\r\n \t":
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
            cid = m.group(1).decode("ascii")
            i = m.end()

            if i < n and b[i : i + 1] == b"T":
                comma = b.find(b",", i)
                if comma != -1:
                    hex_str = b[i + 1 : comma].decode("ascii", errors="ignore")
                    try:
                        text_len = int(hex_str, 16)
                        chunks[cid] = b[comma + 1 : comma + 1 + text_len].decode("utf-8", errors="replace")
                        i = comma + 1 + text_len
                        continue
                    except ValueError:
                        pass

            if i < n and b[i : i + 1] in b"{[":
                text_from_i = b[i:].decode("utf-8", errors="ignore")
                try:
                    obj, end_idx = decoder.raw_decode(text_from_i)
                    chunks[cid] = obj
                    i += len(text_from_i[:end_idx].encode("utf-8"))
                    continue
                except json.JSONDecodeError:
                    pass

            next_nl = b.find(b"\n", i)
            if next_nl != -1:
                chunks[cid] = b[i:next_nl].strip().decode("utf-8", errors="replace")
                i = next_nl + 1
            else:
                chunks[cid] = b[i:].strip().decode("utf-8", errors="replace")
                break

        return chunks

    def _extract_job_id(self, url: str) -> str:
        m = re.search(r"-(\d+)(?:\?.*)?$", url)
        return m.group(1) if m else url.split("/")[-1].split("?")[0]

    def _extract_jobs_from_rsc(self, html_content: str) -> List[Dict[str, Any]]:
        """
        Bóc tách toàn bộ danh sách việc làm trực tiếp từ luồng RSC Flight Push.
        """
        soup = BeautifulSoup(html_content, "html.parser")
        scripts = soup.find_all("script")
        pushes = []
        for s in scripts:
            text = s.string or ""
            if "self.__next_f.push" in text:
                for m in re.finditer(r'self\.__next_f\.push\(\[\d+,\s*("(?:[^"\\]|\\.)*")\]\)', text):
                    try:
                        pushes.append(json.loads(m.group(1)))
                    except Exception:
                        pass

        if not pushes:
            return []

        full_stream = "".join(pushes)
        chunks = self._parse_rsc_bytes(full_stream.encode("utf-8")) if full_stream else {}

        # 1. Thu thập bản đồ công ty theo job id
        companies_by_job_id: Dict[int, Dict[str, Any]] = {}
        def extract_companies(obj):
            if isinstance(obj, dict):
                if "jobs" in obj and isinstance(obj["jobs"], list):
                    for item in obj["jobs"]:
                        if isinstance(item, dict) and "id" in item and "company" in item and isinstance(item["company"], dict):
                            companies_by_job_id[item["id"]] = item["company"]
                for v in obj.values():
                    extract_companies(v)
            elif isinstance(obj, list):
                for item in obj:
                    extract_companies(item)

        extract_companies(chunks)

        # 2. Thu thập danh sách jobs từ RSC
        raw_jobs = []
        def extract_raw_jobs(obj):
            if isinstance(obj, dict):
                if "id" in obj and "title" in obj and ("salary" in obj or "detail_url" in obj or "skills_str" in obj):
                    raw_jobs.append(obj)
                for v in obj.values():
                    extract_raw_jobs(v)
            elif isinstance(obj, list):
                for item in obj:
                    extract_raw_jobs(item)

        # Ưu tiên quét trong chunk 3a (chứa danh sách kết quả tìm kiếm chính)
        if "3a" in chunks:
            extract_raw_jobs(chunks["3a"])
        if not raw_jobs:
            extract_raw_jobs(chunks)

        # 3. Hàm giải mã tham chiếu HTML trong RSC ($5a, $5b, ...)
        def resolve_text_ref(val: Any) -> str:
            if not val:
                return ""
            if isinstance(val, str) and val.startswith("$") and len(val) > 1 and val[1:] in chunks:
                ref_val = chunks[val[1:]]
                return self._clean_html(str(ref_val))
            return self._clean_html(str(val))

        # 4. Chuẩn hóa sang 21 trường Medallion Bronze
        results = []
        seen_ids = set()

        for j in raw_jobs:
            jid = str(j.get("id"))
            if not jid or jid in seen_ids:
                continue
            seen_ids.add(jid)

            title = j.get("title", "")
            comp = companies_by_job_id.get(j.get("id")) or {}

            # Mức lương
            sal_obj = j.get("salary", {})
            if isinstance(sal_obj, dict):
                sal = sal_obj.get("value", "")
                if not sal or sal == "0":
                    min_v = sal_obj.get("min", 0)
                    max_v = sal_obj.get("max", 0)
                    curr = sal_obj.get("currency", "VND")
                    if min_v or max_v:
                        sal = f"{min_v:,} - {max_v:,} {curr}"
                    elif sal_obj.get("is_negotiable") == "1":
                        sal = "Thương lượng"
            else:
                sal = str(sal_obj or "")

            # Địa điểm làm việc
            addr_obj = j.get("addresses", {})
            loc = ""
            if isinstance(addr_obj, dict):
                loc = addr_obj.get("sort_addresses", "")
                if not loc and addr_obj.get("full_addresses"):
                    loc = addr_obj.get("full_addresses")[0]

            level = j.get("job_levels_str", "")
            exp = j.get("experiences_str", "")

            # Kỹ năng
            skills = j.get("skills_arr", []) or []
            if not skills and j.get("skills_str"):
                skills = [s.strip() for s in j.get("skills_str").split(",") if s.strip()]

            # Nội dung chi tiết
            resp = resolve_text_ref(j.get("responsibilities_original") or j.get("responsibilities"))
            req = resolve_text_ref(j.get("requirements_original") or j.get("requirements"))

            ben_raw = j.get("benefits_original") or j.get("benefits") or []
            ben_texts = []
            if isinstance(ben_raw, list):
                for b in ben_raw:
                    if isinstance(b, dict) and b.get("value"):
                        ben_texts.append(resolve_text_ref(b.get("value")))
                    elif isinstance(b, str):
                        ben_texts.append(resolve_text_ref(b))
            elif isinstance(ben_raw, str):
                ben_texts.append(resolve_text_ref(ben_raw))
            ben = "\n".join([b for b in ben_texts if b]).strip()

            # Thông tin doanh nghiệp
            c_name = comp.get("display_name", "")
            c_size = str(comp.get("company_size") or "")
            c_ind = comp.get("industries_str", "")
            c_country = comp.get("nationalities_str", "")
            c_openings = str(comp.get("num_job_openings", ""))
            c_tagline = comp.get("tagline", "")

            # Thời hạn nộp
            exp_obj = j.get("expires", {})
            deadline = exp_obj.get("date", "") if isinstance(exp_obj, dict) else str(exp_obj or "")

            detail_url = j.get("detail_url") or f"https://topdev.vn/viec-lam/{j.get('slug')}-{jid}"

            hash_source = f"{title}|{c_name}|{sal}|{resp[:200]}"
            content_hash = hashlib.sha256(hash_source.encode("utf-8")).hexdigest()

            results.append({
                "platform": "topdev",
                "job_id": jid,
                "job_url": detail_url,
                "job_title": title,
                "company_name": c_name,
                "salary": sal,
                "job_location": loc,
                "job_level": level,
                "experience": exp,
                "skills": sorted(list(set(skills))),
                "expires_in": deadline,
                "num_candidates": "0 Applicants",
                "general_description": c_tagline,
                "responsibilities": resp,
                "requirements": req,
                "benefits": ben,
                "company_industry": c_ind,
                "company_size": c_size,
                "company_country": c_country,
                "company_job_openings": c_openings,
                "content_hash": content_hash,
                "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

        return results

    def _extract_job_detail_dom(self, html_content: str, job_url: str = "") -> Optional[Dict[str, Any]]:
        """
        Fallback bóc tách DOM nếu RSC không khả dụng.
        """
        soup = BeautifulSoup(html_content, "html.parser")
        job_id = self._extract_job_id(job_url)

        h1 = soup.find("h1")
        job_title = h1.get_text(strip=True) if h1 else ""

        comp_elem = soup.find(class_=re.compile(r"company-name|company__name"))
        company_name = comp_elem.get_text(strip=True) if comp_elem else ""

        content = self._clean_html(str(soup.find("div", class_=re.compile(r"job-detail|content|detail-content")) or ""))
        hash_src = f"{job_title}|{company_name}|{content[:200]}"
        content_hash = hashlib.sha256(hash_src.encode("utf-8")).hexdigest()

        return {
            "platform": "topdev",
            "job_id": job_id,
            "job_url": job_url,
            "job_title": job_title,
            "company_name": company_name,
            "salary": "",
            "job_location": "",
            "job_level": "",
            "experience": "",
            "skills": [],
            "expires_in": "",
            "num_candidates": "0 Applicants",
            "general_description": content,
            "responsibilities": "",
            "requirements": "",
            "benefits": "",
            "company_industry": "",
            "company_size": "",
            "company_country": "",
            "company_job_openings": "",
            "content_hash": content_hash,
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def fetch_jobs(
        self,
        mode: str = "incremental",
        max_pages: int = 5,
        watermark_ts: Optional[str] = None,
        last_record_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Thực thi quét danh sách việc làm TopDev chuẩn 13 nhóm IT và cào dữ liệu.
        - Tự động bóc tách full payload từ RSC Flight Push 15 tin/trang.
        - Incremental: Dừng quét ngay khi chạm Watermark (last_record_id).
        - Full: Quét vét cạn liên tục đến khi hết trang hoặc đạt max_pages.
        """
        mode_label = "VÉT CẠN TOÀN BỘ (FULL BACKFILL)" if mode == "full" else "TĂNG TRƯỞNG (INCREMENTAL)"
        print(f"[*] Bắt đầu quét TopDev qua 13 nhóm ngành IT - Chế độ: {mode_label} (Tối đa {max_pages} trang)...")

        seen_job_ids = set()
        scraped_jobs = []
        watermark_hit = False

        for page in range(1, max_pages + 1):
            if watermark_hit:
                break

            search_url = self.SEARCH_BASE.format(page=page)
            print(f"   -> Đang tải trang tìm kiếm {page}/{max_pages}: {search_url}")

            html_page = self._make_request(search_url)
            if not html_page:
                print(f"      [-] Không thể tải trang tìm kiếm {page}, tạm dừng.")
                break

            # Đo đạc và thông báo tổng số tin ở trang đầu tiên
            if page == 1:
                total_match = re.search(r'\"meta\":\{[^\}]*\"total\":\s*(\d+)', html_page)
                if not total_match:
                    total_match = re.search(r"(\d+)\s*kết quả", html_page)
                if total_match:
                    print(f"      📊 TOPDEV XÁC NHẬN: Tuyển dụng {total_match.group(1)} việc làm ngành IT")

            # Engine 1: Giải mã RSC Flight Push
            page_jobs = self._extract_jobs_from_rsc(html_page)

            # Engine 2: Fallback DOM nếu RSC không bóc tách được tin nào
            if not page_jobs:
                soup = BeautifulSoup(html_page, "html.parser")
                for a in soup.find_all("a", href=True):
                    m = re.match(r"^/viec-lam/([a-zA-Z0-9\-]+-(\d+))", a["href"])
                    if m:
                        jid = m.group(2)
                        if jid not in seen_job_ids:
                            job_url = f"https://topdev.vn/viec-lam/{m.group(1)}"
                            dom_item = self._extract_job_detail_dom(html_page, job_url)
                            if dom_item:
                                page_jobs.append(dom_item)

            if not page_jobs:
                print(f"      [!] Trang {page} không phát hiện tin tuyển dụng nào. Đã đến trang cuối cùng của TopDev!")
                break

            new_in_page = 0
            for item in page_jobs:
                jid = item["job_id"]

                # Kiểm tra Watermark: Gặp lại tin đã cào ở mốc trước
                if mode == "incremental" and last_record_id and str(jid) == str(last_record_id):
                    print(f"      🎯 [WATERMARK HIT] Chạm ranh giới tin cũ TopDev (Job ID: {jid}). Dừng quét!")
                    watermark_hit = True
                    break

                if jid not in seen_job_ids:
                    seen_job_ids.add(jid)
                    scraped_jobs.append(item)
                    new_in_page += 1
                    skills_preview = ", ".join(item["skills"][:3]) if item["skills"] else "N/A"
                    print(f"       [✓] Job[{jid}] {item['job_title'][:35]} | {item['company_name'][:20]} | Lương: {item['salary'] or 'Thoả thuận'} | Skills: [{skills_preview}]")

            print(f"      [✓] Trang {page}: Thu thập thành công {new_in_page} tin tuyển dụng IT mới.")
            self.heartbeat()

            if watermark_hit:
                break
            if new_in_page == 0:
                print("      [!] Không có tin mới ở trang này; dừng phân trang để tránh vòng lặp.")
                break

            time.sleep(random.uniform(self.REQUEST_DELAY_MIN, self.REQUEST_DELAY_MAX))

        print(f"\n[*] TỔNG CỘNG THU THẬP: {len(scraped_jobs)} tin tuyển dụng TopDev IT chuẩn.")
        return scraped_jobs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Bot cào TopDev chuẩn Tầng Bronze 2.0 (Dual-Engine RSC + DOM)")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental", help="Chế độ cào")
    parser.add_argument("--max-pages", type=int, default=1, help="Số trang quét tối đa (Mặc định: 1)")
    args = parser.parse_args()

    crawler = TopDevCrawler()
    crawler.run(mode=args.mode, max_pages=args.max_pages)
