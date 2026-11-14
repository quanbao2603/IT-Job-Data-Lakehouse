import re
import os
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


class Vieclam24hCrawler(BaseCrawler):
    """
    Bot cào sàn Vieclam24h.vn chuẩn Tầng Bronze 2.0.
    - Ma trận lọc IT cố định trong URL: occupation_ids[]=7 (IT Phần cứng - Mạng)
      và occupation_ids[]=8 (IT Phần mềm) → chống thất thoát & chống nhiễm tin ngoài IT.
    - Phân trang server-side: &page=N (30 tin/trang, ~536 tin, 18 trang).
    - Engine 1 listing: bóc script#__NEXT_DATA__ → props.initialProps.pageProps.jobsResponse
      (items[], total_pages, total_items) — payload giàu: id, title, employer_info, salary_min/max,
      created_at/approved_at (unix), places (JSON), vacancy_quantity, experience_range, smart_tags.
    - Engine 1 detail: JSON-LD JobPosting trong trang chi tiết
      (/{category}/{slug}-c{occ}p{prov}p{...}id{job_id}.html) cho description, jobBenefits,
      qualifications, industry, experienceRequirements, validThrough, baseSalary, totalJobOpenings.
    - Engine 2 fallback: payload mức thẻ tin (zero data loss) khi trang chi tiết lỗi.
    - Incremental: kết quả tìm kiếm sắp theo priority (tin trả phí lên đầu) nên thứ tự KHÔNG
      đơn điệu theo thời gian → dùng id_cursor (last_record_id) làm chốt chính và datetime guard
      chỉ dừng khi cả trang đều quá cũ (an toàn hơn so với dừng theo từng tin).
    """

    BASE_URL = "https://vieclam24h.vn"
    OCCUPATION_IDS = (7, 8)  # 7 = IT Phần cứng - Mạng, 8 = IT Phần mềm
    LIST_BASE = "https://vieclam24h.vn/tim-kiem-viec-lam-nhanh?occupation_ids[]=7&occupation_ids[]=8"
    REQUEST_DELAY_MIN = 1.0
    REQUEST_DELAY_MAX = 1.8

    EXPERIENCE_MAP = {
        0: "Không yêu cầu",
        1: "Dưới 1 năm",
        2: "1 năm",
        3: "2 năm",
        4: "3 năm",
        5: "4 năm",
        6: "5 năm",
        7: "Trên 5 năm",
    }
    WORKING_METHOD_MAP = {1: "Toàn thời gian", 2: "Bán thời gian", 3: "Thực tập", 4: "Thời vụ"}

    def __init__(self, **kwargs):
        super().__init__(source_name="vieclam24h", **kwargs)
        self.cookie = self._load_cookie()
        self.session = self._create_session()

    def _load_cookie(self) -> str:
        candidates = [
            os.path.join(os.path.dirname(__file__), "cookies", "vieclam24h_cookie.txt"),
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
            "Referer": "https://vieclam24h.vn/",
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
            return self.LIST_BASE
        return f"{self.LIST_BASE}&page={page}"

    @staticmethod
    def _extract_next_data(html: str) -> Optional[Dict[str, Any]]:
        """Bóc toàn bộ JSON của thẻ <script id="__NEXT_DATA__">."""
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag:
            return None
        try:
            return json.loads(tag.string or tag.get_text())
        except Exception:
            return None

    def _parse_listing_page(self, html: str) -> Dict[str, Any]:
        """Trả về {'items': [...], 'total_pages': n, 'total_items': n, 'current': n}."""
        data = self._extract_next_data(html)
        if not data:
            return {"items": [], "total_pages": 0, "total_items": 0, "current": 1}

        page_props = (
            data.get("props", {})
                .get("initialProps", {})
                .get("pageProps", {})
        )
        jr = page_props.get("jobsResponse") or {}
        return {
            "items": jr.get("items") or [],
            "total_pages": jr.get("total_pages") or 0,
            "total_items": jr.get("total_items") or 0,
            "current": jr.get("current") or 1,
        }

    @staticmethod
    def _ts_to_iso(ts: Any) -> str:
        """Unix timestamp (int/str) → 'yyyy-mm-dd'."""
        if not ts:
            return ""
        try:
            return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            return ""

    @staticmethod
    def _format_salary(item: Dict[str, Any]) -> str:
        """salary_min/salary_max (VND) → chuỗi đọc được. Rỗng nếu không công khai."""
        try:
            smin = int(item.get("salary_min") or 0)
            smax = int(item.get("salary_max") or 0)
        except (ValueError, TypeError):
            return ""
        if smin <= 0 and smax <= 0:
            return ""

        def fmt(v: int) -> str:
            if v >= 1_000_000_000:
                return f"{v / 1_000_000_000:.0f} tỷ"
            if v >= 1_000_000:
                val = v / 1_000_000
                return f"{val:.0f} triệu" if val == int(val) else f"{val:.1f} triệu"
            return f"{v:,}".replace(",", ".")

        if smin > 0 and smax > 0:
            if smin == smax:
                return f"{fmt(smin)} VND"
            return f"{fmt(smin)} - {fmt(smax)} VND"
        if smax > 0:
            return f"Tới {fmt(smax)} VND"
        return f"Từ {fmt(smin)} VND"

    @staticmethod
    def _parse_places(raw: Any) -> Dict[str, Any]:
        """decode chuỗi JSON 'places' → {address_list: [...], structured: [...]}."""
        if not raw:
            return {"address_list": [], "structured": ""}
        try:
            places = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return {"address_list": [], "structured": ""}
        if not isinstance(places, list):
            return {"address_list": [], "structured": ""}

        addrs: List[str] = []
        for p in places:
            if not isinstance(p, dict):
                continue
            addr = (p.get("address") or "").strip()
            if addr and addr not in addrs:
                addrs.append(addr)
        return {
            "address_list": addrs,
            "structured": json.dumps(places, ensure_ascii=False),
        }

    @staticmethod
    def _clean_html(raw_html: str) -> str:
        if not raw_html:
            return ""
        raw_html = re.sub(r"<!--.*?-->", "", raw_html, flags=re.S)
        soup = BeautifulSoup(raw_html, "html.parser")
        for li in soup.find_all("li"):
            li.insert_before("- ")
            li.insert_after("\n")
        for tag in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "br"]):
            tag.insert_after("\n")
        lines = [re.sub(r"\s+", " ", l).strip() for l in soup.get_text().split("\n")]
        return "\n".join([l for l in lines if l]).strip()

    def _normalize_job_card(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Chuẩn hóa 1 item của jobsResponse thành card nội bộ."""
        job_id = str(item.get("id") or "")
        title_slug = item.get("title_slug") or ""
        # URL: /{category-slug}/{title_slug}-c{occ}p{prov}id{job_id}.html
        # category slug lấy từ occupation_ids_main → map, hoặc dùng slug chung /it-phan-mem/
        occ_main = item.get("occupation_ids_main") or []
        category = "it-phan-mem"
        if 7 in occ_main and 8 not in occ_main:
            category = "it-phan-cung-mang"
        elif 8 in occ_main:
            category = "it-phan-mem"

        province_ids = item.get("province_ids") or []
        prov = province_ids[0] if province_ids else ""
        if title_slug and job_id:
            job_url = f"{self.BASE_URL}/{category}/{title_slug}-c{occ_main[0] if occ_main else ''}p{prov}id{job_id}.html"
        else:
            job_url = ""

        emp = item.get("employer_info") or {}
        places = self._parse_places(item.get("places"))

        exp_code = item.get("experience_range")
        experience = self.EXPERIENCE_MAP.get(exp_code, "") if isinstance(exp_code, int) else ""

        working_method = item.get("working_method")
        employment_type = self.WORKING_METHOD_MAP.get(working_method, "") if isinstance(working_method, int) else ""

        # Hạn nộp: ưu tiên job_box[].expired_at, fallback resume_apply_expired
        deadline_ts = 0
        for box in (item.get("job_box") or []):
            if isinstance(box, dict) and box.get("expired_at"):
                deadline_ts = box["expired_at"]
                break
        if not deadline_ts:
            deadline_ts = item.get("resume_apply_expired") or 0

        smart_tags = item.get("smart_tags") or []
        skills = ", ".join(
            t.get("name", "") for t in smart_tags
            if isinstance(t, dict) and t.get("category") in ("Skill", "Knowledge") and t.get("name")
        )
        industries = ", ".join(
            t.get("name", "") for t in smart_tags
            if isinstance(t, dict) and t.get("category") == "Industry" and t.get("name")
        )

        return {
            "job_id": job_id,
            "job_url": job_url,
            "job_title": item.get("title") or "",
            "company_name": emp.get("name") or "",
            "company_id": str(emp.get("id") or ""),
            "company_url": (
                f"{self.BASE_URL}/danh-sach-tin-tuyen-dung-{emp.get('slug')}-ntd{emp.get('id')}p122.html"
                if emp.get("slug") and emp.get("id") else ""
            ),
            "company_logo": emp.get("logo") or "",
            "salary": self._format_salary(item),
            "job_location": ", ".join(places["address_list"]),
            "location_structured": places["structured"],
            "deadline_iso": self._ts_to_iso(deadline_ts),
            "posted_iso": self._ts_to_iso(item.get("approved_at") or item.get("created_at")),
            "created_iso": self._ts_to_iso(item.get("created_at")),
            "employment_type": employment_type,
            "experience": experience,
            "skills": skills,
            "industries": industries,
            "vacancy_quantity": item.get("vacancy_quantity") or 0,
            "level_requirement": item.get("level_requirement") or 0,
            "occupation_ids_main": occ_main,
            "other_requirement_raw": item.get("other_requirement_html") or "",
            "job_requirement_raw": item.get("job_requirement_html") or "",
        }

    def _extract_json_ld_job(self, html: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        for sc in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(sc.string or sc.get_text())
            except Exception:
                continue
            candidates = data if isinstance(data, list) else [data]
            for cand in candidates:
                if isinstance(cand, dict) and cand.get("@type") == "JobPosting":
                    return cand
        return {}

    def _parse_job_detail_raw(self, html: str, card: Dict[str, Any]) -> Dict[str, Any]:
        ld = self._extract_json_ld_job(html)

        job_title = ld.get("title") or card["job_title"]

        org = ld.get("hiringOrganization") or {}
        company_name = org.get("name") or card["company_name"]
        company_logo = org.get("logo") or card["company_logo"]
        company_url = org.get("sameAs") or card["company_url"]

        job_location_struct = ""
        job_location = card["job_location"]
        locs = ld.get("jobLocation")
        if isinstance(locs, dict):
            locs = [locs]
        if isinstance(locs, list) and locs:
            job_location_struct = json.dumps(locs, ensure_ascii=False)
            parts = []
            for loc in locs:
                if not isinstance(loc, dict):
                    continue
                addr = loc.get("address") or {}
                if not isinstance(addr, dict):
                    continue
                seg = ", ".join(
                    x for x in (
                        addr.get("streetAddress", ""),
                        addr.get("addressLocality", ""),
                        addr.get("addressRegion", ""),
                    ) if x
                )
                if seg and seg not in parts:
                    parts.append(seg)
            if parts:
                job_location = " | ".join(parts)

        # Salary: JSON-LD baseSalary chuẩn xác nhất
        salary = card["salary"]
        base = ld.get("baseSalary") or {}
        if isinstance(base, dict):
            val = base.get("value") or {}
            if isinstance(val, dict):
                mn = val.get("minValue")
                mx = val.get("maxValue")
                if mn or mx:
                    salary = self._format_salary({"salary_min": mn or 0, "salary_max": mx or 0})

        description = self._clean_html(ld.get("description") or "")
        benefits = self._clean_html(ld.get("jobBenefits") or "")

        # Requirements: qualifications → other_requirement_html từ card
        requirements = self._clean_html(ld.get("qualifications") or "")
        if not requirements:
            requirements = self._clean_html(card.get("other_requirement_raw") or "")

        industry = ld.get("industry") or card["industries"]
        if isinstance(industry, list):
            industry = ", ".join(str(i) for i in industry)

        exp_obj = ld.get("experienceRequirements") or {}
        exp_months = ""
        if isinstance(exp_obj, dict) and exp_obj.get("monthsOfExperience") is not None:
            try:
                months = int(exp_obj["monthsOfExperience"])
                exp_months = f"{months // 12} năm" if months >= 12 else f"{months} tháng"
            except (ValueError, TypeError):
                exp_months = ""
        experience = exp_months or card["experience"]
        # Gộp kinh nghiệm vào đầu requirements để không thất thoát (contract không có field riêng)
        if experience:
            prefix = f"Kinh nghiệm: {experience}"
            requirements = f"{prefix}\n{requirements}" if requirements else prefix

        employment_type = ld.get("employmentType") or card["employment_type"]
        if isinstance(employment_type, list):
            employment_type = ", ".join(employment_type)

        posted_date_iso = ld.get("datePosted") or card["posted_iso"]
        valid_through = ld.get("validThrough") or ""

        deadline = card["deadline_iso"]
        if valid_through:
            deadline = valid_through[:10]

        quantity = ld.get("totalJobOpenings")
        vacancy_quantity = int(quantity) if isinstance(quantity, (int, float)) else card["vacancy_quantity"]

        content_basis = f"{job_title}|{company_name}|{salary}|{description[:200]}"
        content_hash = hashlib.sha256(content_basis.encode("utf-8")).hexdigest()

        return {
            "platform": "vieclam24h",
            "job_id": card["job_id"],
            "job_url": card["job_url"],
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": content_hash,
            "job_title": job_title,
            "company_name": company_name,
            "company_id": card["company_id"],
            "company_url": company_url,
            "company_logo": company_logo,
            "salary": salary,
            "job_location": job_location,
            "job_location_structured": job_location_struct or card["location_structured"],
            "deadline": deadline,
            "deadline_source": "json_ld" if valid_through else ("listed" if card["deadline_iso"] else ""),
            "updated_date": card["posted_iso"],
            "industries": industry,
            "card_meta_raw": "",
            "description": description,
            "requirements": requirements,
            "benefits": benefits,
            "job_info_raw": card["skills"],
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

        print(f"[*] Bắt đầu quét Vieclam24h [ma trận IT: occupation_ids={'+'.join(map(str, self.OCCUPATION_IDS))}] "
              f"- Chế độ: {mode_label} (Tối đa {max_pages} trang)...")

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
            raw_items = parsed["items"]

            if page == 1:
                if parsed["total_items"]:
                    print(f"      📊 VIECLAM24H XÁC NHẬN: {parsed['total_items']} việc làm trong ma trận IT "
                          f"({parsed['total_pages']} trang × 30 tin)")
                self.heartbeat()

            cards = [self._normalize_job_card(it) for it in raw_items if it.get("id")]
            print(f"      [✓] Trang {page} phát hiện {len(cards)} tin trong ma trận IT.")
            if not cards:
                print(f"      [!] Đã hết tin phân trang tại trang {page}. Dừng quét.")
                break

            # Id-cursor là chốt chính (thứ tự listing không đơn điệu theo thời gian do ưu tiên tin trả phí)
            for card in cards:
                if is_incremental:
                    if last_record_id and str(card["job_id"]) == str(last_record_id):
                        print(f"      🎯 [WATERMARK HIT] Chạm ranh giới tin cũ (Job ID: {card['job_id']}). Dừng quét!")
                        watermark_hit = True
                        break
                    # Datetime guard: chỉ dừng khi tin CỦ HƠN hẳn mốc (strict < → giữ tin cùng ngày)
                    if self._yield_wrapper_guard(card, watermark_date):
                        print(f"      🎯 [WATERMARK HIT] Tin đăng {card['posted_iso']} cũ hơn mốc {watermark_date}. Dừng quét!")
                        watermark_hit = True
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

    @staticmethod
    def _yield_wrapper_guard(card: Dict[str, Any], watermark_date: str) -> bool:
        """Trả True khi tin đã quá cũ so với mốc (strict <  → giữ tin cùng ngày)."""
        if not watermark_date:
            return False
        posted = card.get("posted_iso") or ""
        return bool(posted) and posted < watermark_date

    def _card_fallback_payload(self, card: Dict[str, Any]) -> Dict[str, Any]:
        """Zero data loss: trang chi tiết lỗi vẫn giữ trọn thông tin mức thẻ tin."""
        content_basis = f"{card['job_title']}|{card['company_name']}|{card['salary']}|"
        return {
            "platform": "vieclam24h",
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
            "job_location_structured": card["location_structured"],
            "deadline": card["deadline_iso"],
            "deadline_source": "listed" if card["deadline_iso"] else "",
            "updated_date": card["posted_iso"],
            "industries": card["industries"],
            "card_meta_raw": "",
            "description": "",
            "requirements": self._clean_html(card.get("other_requirement_raw") or ""),
            "benefits": "",
            "job_info_raw": card["skills"],
            "posted_date_iso": card["posted_iso"],
            "valid_through": "",
            "employment_type": card["employment_type"],
        }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Bot cào Vieclam24h.vn chuẩn Tầng Bronze 2.0 (ma trận IT: Phần cứng-Mạng + Phần mềm)")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental", help="Chế độ cào")
    parser.add_argument("--max-pages", type=int, default=1, help="Số trang quét tối đa (30 tin/trang. Mặc định: 1)")
    args = parser.parse_args()

    crawler = Vieclam24hCrawler()
    crawler.run(mode=args.mode, max_pages=args.max_pages)
