import tempfile
import unittest
from pathlib import Path

from src.etl.silver_writer import SilverWriter


class TestSilverWriter(unittest.TestCase):
    def test_bounded_parquet_write_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SilverWriter(tmp, max_records_per_batch=2)
            rows = [{
                "source_job_id": str(i), "observation_id": f"obs-{i}", "platform": "itviec",
                "source_url": None, "canonical_url": None, "content_hash": None,
                "crawled_at": "2026-11-16T02:00:00Z", "observed_at": "2026-11-16T02:00:00Z",
                "ingested_at": "2026-11-16T02:00:00Z", "silver_updated_at": "2026-11-16T02:00:00Z",
                "is_active": True, "job_title": "Backend", "job_title_normalized": "backend",
                "job_level": None, "job_expertise": None, "job_domain": None, "work_mode": None,
                "working_time": None, "overtime_policy": None, "company_name": "Acme",
                "company_name_raw": "Acme", "company_name_normalized": "acme", "company_size": None,
                "company_industry": None, "company_type": None, "company_country": None, "city": None,
                "city_normalized": None, "full_address": None, "salary_raw": None,
                "salary_min_vnd": None, "salary_max_vnd": None, "is_negotiable": False,
                "salary_currency": None, "exp_years_min": None, "exp_years_max": None,
                "vacancies_count": 1, "posted_date": None, "expired_date": None,
                "skills_raw": None, "skills_standardized": None, "job_description_clean": None,
                "description_normalized": None, "requirements_clean": None, "benefits_clean": None,
                "platform_specific_attributes": "{}",
            } for i in range(3)]
            result = writer.write("observations", rows, "platform=itviec/year=2026/month=11/day=16")
            self.assertEqual(len(result), 2)
            self.assertTrue(all(Path(item["path"]).exists() for item in result))
            audit = writer.write_audit_log({"status": "SUCCESS"}, "test")
            self.assertTrue(Path(audit).exists())


if __name__ == "__main__":
    unittest.main()
