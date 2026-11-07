import os
import sys
import re
import time
import random
import argparse
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional, Iterator

from bs4 import BeautifulSoup

try:
    from curl_cffi import requests
    HAVE_CURL_CFFI = True
except ImportError:
    import requests
    HAVE_CURL_CFFI = False

from src.crawlers.base_crawler import BaseCrawler


class TopCVCrawler(BaseCrawler):
    BASE_URL = "https://www.topcv.vn"
    SEARCH_BASE = "https://www.topcv.vn/tim-kiem-viec-lam-it?sort=top_related&category_family=cr257&page={page}"
    REQUEST_DELAY_MIN = 1.0
    REQUEST_DELAY_MAX = 1.8

    def __init__(self, **kwargs):
        super().__init__(source_name="topcv", **kwargs)
        self.cookie = self._load_cookie()
        self.session = self._create_session()

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
        if HAVE_CURL_CFFI:
            s = requests.Session(impersonate="chrome124")
        else:
            s = requests.Session()

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'vi,en-US;q=0.9,en;q=0.8',
            'Referer': 'https://www.topcv.vn/',
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

    def _extract_job_id(self, url: str) -> str:
        m = re.search(r'/([^/]+)\.html', url)
        if m:
            slug = m.group(1)
            id_m = re.search(r'(\d+)$', slug)
            return id_m.group(1) if id_m else slug
        return url.split('/')[-1]

    def _parse_job_detail_raw(self, html_content: str, job_url: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html_content, 'html.parser')

        title_elem = soup.find('h1', class_=re.compile(r'job-title|title', re.I)) or soup.find('h1')
        job_title = title_elem.get_text(strip=True) if title_elem else ""

        company_elem = soup.find('div', class_=re.compile(r'company-name', re.I)) or soup.find('a', class_=re.compile(r'company-name', re.I))
        company_name = company_elem.get_text(strip=True) if company_elem else ""

        salary_elem = soup.find(class_=re.compile(r'salary|salary-range', re.I))
        salary = salary_elem.get_text(strip=True) if salary_elem else "Thỏa thuận"

        general_items = {}
        for box in soup.find_all('div', class_=re.compile(r'general-item|box-item|item', re.I)):
            label = box.find(class_=re.compile(r'title|label', re.I))
            val = box.find(class_=re.compile(r'value|text', re.I))
            if label and val:
                general_items[label.get_text(strip=True).lower()] = val.get_text(strip=True)

        job_location = general_items.get('địa điểm tuyển dụng', general_items.get('địa điểm', ''))
        experience = general_items.get('kinh nghiệm', '')
        deadline = general_items.get('hạn nộp hồ sơ', general_items.get('hạn nộp', ''))
        job_level = general_items.get('cấp bậc', '')
        education = general_items.get('bằng cấp', '')
        quantity = general_items.get('số lượng tuyển', '')
        work_mode = general_items.get('hình thức làm việc', '')
        contract_type = general_items.get('loại hợp đồng', '')

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

        company_size = ""
        company_address = ""
        company_industry = ""
        comp_info = soup.find('div', class_=re.compile(r'company-info|company-detail', re.I))
        if comp_info:
            for item in comp_info.find_all(class_=re.compile(r'item|line', re.I)):
                t = item.get_text(strip=True)
                if 'quy mô' in t.lower():
                    company_size = t.split(':')[-1].strip()
                elif 'địa điểm' in t.lower() or 'địa chỉ' in t.lower():
                    company_address = t.split(':')[-1].strip()
                elif 'lĩnh vực' in t.lower() or 'ngành nghề' in t.lower():
                    company_industry = t.split(':')[-1].strip()

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
        seen = set()
        watermark_hit = False

        for page in range(1, max_pages + 1):
            if watermark_hit:
                break

            url = self.SEARCH_BASE.format(page=page)
            print(f"   -> Đang tải trang tìm kiếm {page}/{max_pages}: {url}")
            html = self._make_request(url)
            if not html:
                print(f"      [!] Không tải được trang {page}, kết thúc quét.")
                break

            soup = BeautifulSoup(html, 'html.parser')

            if page == 1:
                header_count = soup.find(string=re.compile(r'Tuyển dụng\s+([\d\.\,]+)\s+việc làm', re.I))
                if header_count:
                    print(f"      📊 TOPCV XÁC NHẬN: {header_count.strip()}")

            job_links = []
            for a in soup.find_all('a', href=re.compile(r'/viec-lam/[^/]+/\d+\.html')):
                href = a.get('href', '').split('?')[0]
                if href and href not in seen:
                    seen.add(href)
                    jid = self._extract_job_id(href)

                    if mode == "incremental" and last_record_id and str(jid) == str(last_record_id):
                        print(f"      🎯 [WATERMARK HIT] Chạm ranh giới tin cũ đã cào (Job ID: {jid}). Dừng quét!")
                        watermark_hit = True
                        break

                    job_links.append(href)

            print(f"      [✓] Trang {page} phát hiện {len(job_links)} tin tuyển dụng mới.")

            if not job_links and not watermark_hit:
                print(f"      [!] Đã hết tin phân trang tại trang {page}. Dừng quét.")
                break

            candidates_urls.extend(job_links)

            if watermark_hit:
                break

            time.sleep(random.uniform(1.0, 1.5))

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
    crawler.run(mode=args.mode, max_pages=args.max_pages)
