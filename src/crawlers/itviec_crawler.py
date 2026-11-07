import re
import os
import sys
import time
import json
import random
import hashlib
import argparse
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

from src.crawlers.base_crawler import BaseCrawler


class ITViecCrawler(BaseCrawler):
    BASE_URL = "https://itviec.com"
    SEARCH_URL = "https://itviec.com/it-jobs?page={page}"
    REQUEST_DELAY_MIN = 1.0
    REQUEST_DELAY_MAX = 1.8

    def __init__(self, **kwargs):
        super().__init__(source_name="itviec", **kwargs)
        self.cookie = self._load_cookie()
        self.session = self._create_session()

    def _load_cookie(self) -> str:
        candidates = [
            os.path.join(os.path.dirname(__file__), "cookies", "itviec_cookie.txt"),
            os.path.join(os.path.dirname(__file__), "itviec", "itviec_cookie.txt"),
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
            'Referer': 'https://itviec.com/',
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
        m = re.search(r'-(\d+)(?:\?.*)?$', url)
        return m.group(1) if m else url.split('/')[-1]

    def _parse_job_detail_raw(self, html_content: str, job_url: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html_content, 'html.parser')

        json_ld = {}
        for s in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(s.text.strip())
                if data.get('@type') == 'JobPosting':
                    json_ld = data
                    break
            except Exception:
                pass

        posted_date_iso = json_ld.get('datePosted', '')
        valid_through = json_ld.get('validThrough', '')
        employment_type = json_ld.get('employmentType', '')
        company_logo = json_ld.get('hiringOrganization', {}).get('logo', '')
        experience_requirements = json_ld.get('experienceRequirements', '')

        header = soup.find('div', class_='job-header-info') or soup
        h1 = header.find('h1')
        job_title = h1.get_text().strip() if h1 else (json_ld.get('title') or "")

        emp_elem = header.find('div', class_='employer-name') or header.find('a', class_='employer-name')
        company_name = emp_elem.get_text().strip() if emp_elem else (json_ld.get('hiringOrganization', {}).get('name') or "")

        salary_elem = header.find('div', class_=lambda c: c and ('salary' in str(c).lower() or 'text-success' in str(c).lower())) or header.find(class_=lambda c: c and 'salary' in str(c).lower())
        salary = salary_elem.get_text(' ', strip=True) if salary_elem else "Thỏa thuận"

        job_info = soup.find('div', class_='job-show-info')
        address, work_mode, posted_date = "", "", ""
        skills = []
        job_expertise, job_domain = "", ""

        if job_info:
            info_text = job_info.get_text('\n', strip=True)

            map_link = job_info.find('a', href=re.compile(r'google\.com/maps|maps', re.I))
            if map_link and map_link.parent:
                address = map_link.parent.get_text().strip()
            else:
                addr_elem = job_info.find(class_=lambda c: c and 'address' in c)
                if addr_elem:
                    address = addr_elem.get_text().strip()

            mode_match = re.search(r'\b(At office|Remote|Hybrid)\b', info_text, re.I)
            if mode_match:
                work_mode = mode_match.group(1)

            posted_match = re.search(r'Posted\s+([^\n\r]+)', info_text, re.I)
            if posted_match:
                posted_date = 'Posted ' + posted_match.group(1).strip()

            for a in job_info.find_all('a', href=re.compile(r'/it-jobs/')):
                st = a.get_text().strip()
                if st and st not in skills and len(st) < 40:
                    skills.append(st)

            lines = [l.strip() for l in info_text.split('\n') if l.strip()]
            for i, l in enumerate(lines):
                if any(k in l.lower() for k in ['expertise', 'chuyên môn']) and i + 1 < len(lines):
                    job_expertise = lines[i+1]
                if any(k in l.lower() for k in ['domain', 'lĩnh vực']) and i + 1 < len(lines):
                    job_domain = lines[i+1]

        def extract_section(title_keywords):
            for h_tag in soup.find_all(['h2', 'h3']):
                htext = h_tag.get_text().strip().lower()
                if any(kw in htext for kw in title_keywords):
                    c = h_tag.find_next_sibling()
                    if not c:
                        c = h_tag.parent.find_next_sibling()
                    if c:
                        return c.get_text('\n', strip=True)
            return ""

        job_desc = extract_section(['mô tả công việc', 'job description', 'responsibilities'])
        requirements = extract_section(['your skills and experience', 'yêu cầu', 'requirements'])
        benefits = extract_section(['why you\'ll love working here', 'lý do bạn sẽ thích', 'quyền lợi', 'benefits'])

        company_overview = ""
        comp_elem = soup.find('div', class_='employer-overview') or soup.find('div', class_='company-info')
        if comp_elem:
            company_overview = comp_elem.get_text('\n', strip=True)

        comp_dict = {}
        for div in soup.find_all('div'):
            text = div.get_text(' ', strip=True)
            if any(k in text for k in ['Company type', 'Company industry', 'Company size', 'Country', 'Working days', 'Overtime policy']):
                children = [c.get_text(' ', strip=True) for c in div.find_all('div', recursive=False) if c.get_text(strip=True)]
                if len(children) == 2 and len(text) < 150:
                    label, val = children[0].strip(), children[1].strip()
                    comp_dict[label] = re.sub(r'\s+', ' ', val)

        company_type = comp_dict.get('Company type', '')
        company_industry = comp_dict.get('Company industry', '')
        company_size = comp_dict.get('Company size', '')
        company_country = comp_dict.get('Country', '')
        working_days = comp_dict.get('Working days', '')
        overtime_policy = comp_dict.get('Overtime policy', '')

        if not skills and json_ld.get('skills'):
            raw_s = json_ld['skills']
            skills = [s.strip() for s in raw_s.split(',') if s.strip()]

        content_for_hash = f"{job_title}|{company_name}|{salary}|{job_desc[:200]}"
        content_hash = hashlib.sha256(content_for_hash.encode('utf-8')).hexdigest()

        return {
            "platform": "itviec",
            "job_id": self._extract_job_id(job_url),
            "job_url": job_url,
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": content_hash,
            "job_title": job_title,
            "company_name": company_name,
            "company_logo": company_logo,
            "salary": salary,
            "address": address,
            "work_mode": work_mode,
            "posted_date": posted_date,
            "posted_date_iso": posted_date_iso,
            "valid_through": valid_through,
            "employment_type": employment_type,
            "experience_requirements": experience_requirements,
            "skills": ", ".join(skills),
            "job_expertise": job_expertise,
            "job_domain": job_domain,
            "job_description": job_desc,
            "requirements": requirements,
            "benefits": benefits,
            "company_overview": company_overview,
            "company_type": company_type,
            "company_industry": company_industry,
            "company_size": company_size,
            "company_country": company_country,
            "working_days": working_days,
            "overtime_policy": overtime_policy,
        }

    def fetch_jobs(
        self,
        mode: str = "incremental",
        max_pages: int = 1,
        watermark_ts: Optional[str] = None,
        last_record_id: Optional[str] = None
    ) -> Iterator[Dict[str, Any]]:
        mode_label = "VÉT CẠN TOÀN BỘ (FULL BACKFILL)" if mode == "full" else "TĂNG TRƯỞNG (INCREMENTAL)"
        candidates_urls = []
        is_incremental = (mode == "incremental")
        watermark_hit = False

        print(f"[*] Bắt đầu quét ITViec - Chế độ: {mode_label} (Tối đa {max_pages} trang)...")
        for page in range(1, max_pages + 1):
            if watermark_hit:
                break

            url = self.SEARCH_URL.format(page=page)
            print(f"   -> Đang tải trang tìm kiếm {page}/{max_pages}: {url}")
            html = self._make_request(url)
            if not html:
                print(f"      [!] Không tải được trang {page}, kết thúc quét.")
                break

            soup = BeautifulSoup(html, 'html.parser')

            if page == 1:
                h1_elem = soup.find('h1')
                if h1_elem:
                    cnt_m = re.search(r'([\d\.\,]+)\s+IT jobs', h1_elem.get_text())
                    if cnt_m:
                        total_jobs = cnt_m.group(1)
                        print(f"      📊 ITVIEC XÁC NHẬN: Tuyển dụng {total_jobs} việc làm IT tại Việt Nam")

            job_links = soup.find_all('a', href=re.compile(r'/it-jobs/[a-zA-Z0-9\-]+-\d+'))
            page_urls = []
            for a in job_links:
                href = a.get('href', '')
                if href:
                    full_u = urljoin(self.BASE_URL, href.split('?')[0])
                    jid = self._extract_job_id(full_u)

                    if is_incremental and last_record_id and str(jid) == str(last_record_id):
                        print(f"      🎯 [WATERMARK HIT] Chạm ranh giới tin cũ ITViec (Job ID: {jid}). Dừng quét!")
                        watermark_hit = True
                        break

                    if full_u not in candidates_urls and full_u not in page_urls:
                        page_urls.append(full_u)

            print(f"      [✓] Trang {page} phát hiện {len(page_urls)} tin thực tế mới.")

            if not page_urls and not watermark_hit:
                print(f"      [!] Đã hết tin phân trang tại trang {page}. Dừng quét.")
                break

            candidates_urls.extend(page_urls)

            if watermark_hit:
                break

            time.sleep(random.uniform(0.8, 1.2))

        print(f"\n[*] TỔNG CỘNG PHÁT HIỆN: {len(candidates_urls)} tin tuyển dụng ITViec. BẮT ĐẦU CÀO STREAMING...")
        for idx, j_url in enumerate(candidates_urls, 1):
            jid = self._extract_job_id(j_url)
            print(f"  [{idx:3d}/{len(candidates_urls):3d}] Đang cào Job[{jid}]: {j_url}")

            detail_html = self._make_request(j_url)
            if detail_html:
                try:
                    item = self._parse_job_detail_raw(detail_html, j_url)
                    print(f"       [✓] {item['job_title'][:40]} | Cty: {item['company_name'][:25]} | Lương: {item['salary']}")
                    yield item
                except Exception as parse_err:
                    print(f"       [!] Lỗi bóc tách: {parse_err}")
            else:
                print(f"       [-] Không tải được nội dung tin {jid}")

            time.sleep(random.uniform(self.REQUEST_DELAY_MIN, self.REQUEST_DELAY_MAX))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Bot cào ITViec chuẩn Tầng Bronze 2.0 (Zero Data Loss)")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental", help="Chế độ cào")
    parser.add_argument("--max-pages", type=int, default=1, help="Số trang quét tối đa (Mặc định: 1)")
    args = parser.parse_args()

    crawler = ITViecCrawler()
    crawler.run(mode=args.mode, max_pages=args.max_pages)
