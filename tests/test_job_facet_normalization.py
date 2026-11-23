"""Normalization contracts behind the dashboard facets (city, level, domain, company)."""
import unittest

from src.etl.transformers import (
    classify_job_domain,
    classify_job_level,
    canonicalize_bronze_record,
    is_placeholder_company,
    normalize_city,
    parse_salary,
)
from src.storage.metadata_envelope import MetadataEnvelope


def _envelope(platform, job_id, **extra):
    payload = {
        "platform": platform,
        "job_id": job_id,
        "job_url": f"https://jobs.example.test/{platform}/{job_id}",
        "scraped_at": "2026-11-16T01:00:00Z",
        "content_hash": f"hash-{platform}-{job_id}",
        "job_title": "Backend Engineer",
        "company_name": "Acme",
        "salary": "20 - 30 triệu",
        "job_description": "Python backend service",
    }
    payload.update(extra)
    envelope = MetadataEnvelope.wrap(platform, payload, run_id="run-test")
    envelope["_metadata"]["ingested_at"] = "2026-11-16T02:00:00Z"
    return envelope


class TestCityNormalization(unittest.TestCase):
    def test_district_address_maps_to_province(self):
        self.assertEqual(normalize_city("Quận Cầu Giấy, Hà Nội"), "Hà Nội")
        self.assertEqual(normalize_city("Tòa nhà QTSC 9, Công viên Phần mềm Quang Trung, TP.HCM"), "Hồ Chí Minh")

    def test_english_and_abbreviated_forms(self):
        self.assertEqual(normalize_city("Ho Chi Minh City"), "Hồ Chí Minh")
        self.assertEqual(normalize_city("Ha Noi"), "Hà Nội")
        self.assertEqual(normalize_city("Da Nang"), "Đà Nẵng")

    def test_multi_city_picks_the_first_listed(self):
        self.assertEqual(normalize_city("Hanoi / HCMC"), "Hà Nội")
        self.assertEqual(normalize_city("Hà Nội, Hồ Chí Minh"), "Hà Nội")

    def test_unknown_and_foreign_locations(self):
        self.assertEqual(normalize_city("Tokyo"), "Khác")
        self.assertEqual(normalize_city("Bình Dương"), "Khác")
        self.assertIsNone(normalize_city(""))
        self.assertIsNone(normalize_city(None))


class TestLevelClassification(unittest.TestCase):
    def test_source_specific_labels(self):
        self.assertEqual(classify_job_level("Experienced (non-manager)"), "Mid")
        self.assertEqual(classify_job_level("Manager"), "Lead")
        self.assertEqual(classify_job_level("Director and above"), "Director")
        self.assertEqual(classify_job_level("Fresher/Entry level"), "Fresher")
        self.assertEqual(classify_job_level("Chuyên viên"), "Mid")
        self.assertEqual(classify_job_level("Chuyên viên cấp cao"), "Senior")
        self.assertEqual(classify_job_level("Nhân viên"), "Junior")
        self.assertEqual(classify_job_level("Thực tập sinh"), "Intern")

    def test_title_fallback(self):
        self.assertEqual(classify_job_level(None, title="Senior Backend Engineer"), "Senior")
        self.assertEqual(classify_job_level(None, title="Thực tập sinh Java"), "Intern")
        self.assertEqual(classify_job_level(None, title="Trưởng nhóm Kỹ thuật"), "Lead")

    def test_experience_years_fallback(self):
        self.assertEqual(classify_job_level(None, title="Kỹ sư phần mềm", experience_years=0), "Fresher")
        self.assertEqual(classify_job_level(None, title="Kỹ sư phần mềm", experience_years=3), "Mid")
        self.assertEqual(classify_job_level(None, title="Kỹ sư phần mềm", experience_years=6), "Senior")
        self.assertIsNone(classify_job_level(None, title="Kỹ sư phần mềm"))


class TestDomainClassification(unittest.TestCase):
    def test_title_drives_the_dashboard_taxonomy(self):
        self.assertEqual(classify_job_domain(None, "Senior Java Backend Engineer"), "Phát triển phần mềm")
        self.assertEqual(classify_job_domain(None, "Kỹ sư An toàn thông tin"), "An ninh mạng")
        self.assertEqual(classify_job_domain(None, "Data Engineer"), "Dữ liệu & AI")
        self.assertEqual(classify_job_domain(None, "DevOps Engineer"), "Hạ tầng & Cloud")
        self.assertEqual(classify_job_domain(None, "QA Engineer"), "QA & Kiểm thử")
        self.assertEqual(classify_job_domain(None, "Business Analyst"), "Phân tích & Quản lý")
        self.assertEqual(classify_job_domain(None, "Android Developer"), "Di động & Nhúng")

    def test_raw_industry_alone_still_classifies(self):
        self.assertEqual(classify_job_domain("IT Software/SaaS"), "Phát triển phần mềm")
        self.assertIsNone(classify_job_domain(None, None))


class TestPlaceholderCompany(unittest.TestCase):
    def test_template_and_generic_labels_are_placeholders(self):
        for value in ("{{ company?.name }}", "${company.name}", "Công ty đang tuyển dụng", "UNKNOWN", ""):
            self.assertTrue(is_placeholder_company(value), value)
        self.assertFalse(is_placeholder_company("Bosch Global Software Technologies"))

    def test_template_company_becomes_unknown_without_losing_raw(self):
        record = canonicalize_bronze_record(
            _envelope("topcv", "9", company_name="{{ company?.name }}", job_location="")
        )
        self.assertEqual(record["company_name"], "UNKNOWN")
        self.assertEqual(record["company_name_normalized"], "")
        self.assertEqual(record["company_name_raw"], "{{ company?.name }}")
        self.assertIsNone(record["city_normalized"])


class TestVietnamWorksFields(unittest.TestCase):
    def test_job_locations_is_read_as_city(self):
        record = canonicalize_bronze_record(_envelope("vietnamworks", "77", job_locations="Hanoi / HCMC"))
        self.assertEqual(record["city_normalized"], "Hà Nội")

    def test_level_from_native_field_and_years(self):
        record = canonicalize_bronze_record(
            _envelope("vietnamworks", "78", job_level="Experienced (non-manager)", years_of_experience=3)
        )
        self.assertEqual(record["job_level"], "Mid")


class TestSalaryText(unittest.TestCase):
    def test_lookup_widget_suffix_is_ignored(self):
        low, high, negotiable, currency = parse_salary("25 - 33 triệuXem mức lương thị trường cho vị trí này")
        self.assertEqual((low, high), (25_000_000, 33_000_000))
        self.assertFalse(negotiable)
        self.assertEqual(currency, "VND")

    def test_tra_cuu_suffix_is_ignored(self):
        low, high, _, _ = parse_salary("12 - 16 triệuTra cứu lương thị trường việc làm")
        self.assertEqual((low, high), (12_000_000, 16_000_000))


if __name__ == "__main__":
    unittest.main()
