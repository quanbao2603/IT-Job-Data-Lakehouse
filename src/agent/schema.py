"""Gold schema description handed to the LLM, plus a check that it still matches.

The prompt needs a compact, opinionated schema: only the objects an analyst may query,
with the grain and the business caveats. It is curated rather than fully introspected so
the model sees columns that matter, but ``verify_schema`` fails loudly if PostgreSQL has
drifted away from it, so the prompt can never silently describe the wrong database.
"""
from __future__ import annotations

from typing import Any

# Views the agent may read. Everything else is rejected by the guard.
ALLOWED_RELATIONS: tuple[str, ...] = (
    "gold.v_job_snapshot",
    "gold.v_job_source_url",
    "gold.v_job_skill",
    "gold.v_job_platform",
    "gold.v_market_daily",
    "gold.v_company_hiring",
    "gold.v_skill_pair",
    "gold.v_dim_job_current",
    "gold.dim_job",
    "gold.dim_company",
    "gold.dim_skill",
    "gold.dim_platform",
    "gold.dim_date",
    "gold.fact_job_daily_snapshot",
    "gold.fact_job_market_daily",
    "gold.fact_company_hiring_daily",
    "gold.fact_job_skill_daily",
    "gold.fact_job_platform_daily",
    "gold.fact_skill_pair_daily",
)

SCHEMA_DOC = """
Bạn truy vấn PostgreSQL, schema `gold`, của một data lakehouse tin tuyển dụng IT Việt Nam.
Tất cả view `v_*` đã gắn với release Gold đang công bố, KHÔNG cần tự lọc gold_release_id.

## Bảng/view chính

gold.v_job_snapshot  -- 1 dòng = 1 tin đã hợp nhất trong release hiện tại
  unified_job_id TEXT, job_key TEXT, company_key TEXT, date_key INT,
  posted_date_key INT, job_count INT, newly_seen_job_count INT,
  entity_status TEXT, canonical_salary_min_vnd BIGINT, canonical_salary_max_vnd BIGINT,
  salary_midpoint_vnd NUMERIC, salary_sample_flag BOOLEAN, source_count INT,
  observation_count INT, last_seen_at TIMESTAMPTZ, snapshot_cutoff_at TIMESTAMPTZ,
  snapshot_date DATE, gold_release_id TEXT, silver_release_id TEXT

gold.v_dim_job_current  -- thuộc tính mô tả của từng tin (join qua job_key)
  job_key TEXT, unified_job_id TEXT, canonical_title TEXT, company_key TEXT,
  canonical_location TEXT, city_bucket TEXT, job_domain TEXT, job_level TEXT,
  work_mode TEXT, canonical_posted_date DATE, entity_status TEXT

gold.dim_company
  company_key TEXT, company_display_name TEXT, company_name_normalized TEXT,
  is_unknown BOOLEAN

gold.dim_skill
  skill_key TEXT, skill_code TEXT, skill_name TEXT, is_unknown BOOLEAN

gold.dim_platform
  platform_key SMALLINT, platform_code TEXT, platform_name TEXT

gold.v_job_skill  -- tin nào có kỹ năng nào
  unified_job_id TEXT, skill_key TEXT

gold.v_job_platform  -- tin được đăng trên nền tảng nào
  unified_job_id TEXT, platform_key SMALLINT, source_url TEXT,
  observation_count_in_window INT, last_observed_at TIMESTAMPTZ, freshness_days SMALLINT

gold.v_job_source_url  -- URL tin gốc theo nền tảng
  unified_job_id TEXT, platform_code TEXT, source_url TEXT, last_observed_at TIMESTAMPTZ

gold.v_market_daily  -- tổng hợp sẵn theo chuyên môn × địa điểm × cấp bậc
  snapshot_date DATE, job_domain TEXT, city_bucket TEXT, job_level TEXT,
  observed_job_count INT, open_job_count INT, newly_seen_job_count INT,
  salary_sample_count INT, salary_p25_vnd NUMERIC, salary_p50_vnd NUMERIC,
  salary_p75_vnd NUMERIC, salary_avg_vnd NUMERIC

gold.v_company_hiring
  snapshot_date DATE, company_key TEXT, observed_job_count INT, open_job_count INT,
  newly_seen_job_count INT, distinct_city_count INT, distinct_domain_count INT,
  salary_sample_count INT

gold.v_skill_pair  -- cặp kỹ năng xuất hiện cùng tin
  snapshot_date DATE, skill_a_key TEXT, skill_b_key TEXT, co_mentioned_job_count INT

gold.dim_date  -- date_key INT, full_date DATE, year, quarter, month, week_of_year, day_of_week

## Quy tắc nghiệp vụ bắt buộc

1. `city_bucket` chỉ có 5 giá trị: 'Hà Nội', 'Hồ Chí Minh', 'Đà Nẵng', 'Khác', 'UNKNOWN'.
2. `job_domain` có 8 nhóm tiếng Việt: 'Phát triển phần mềm', 'Dữ liệu & AI',
   'Hạ tầng & Cloud', 'QA & Kiểm thử', 'An ninh mạng', 'Phân tích & Quản lý',
   'Di động & Nhúng', 'UNKNOWN'.
3. `job_level`: 'Intern', 'Fresher', 'Junior', 'Mid', 'Senior', 'Lead', 'Director', 'UNKNOWN'.
4. `UNKNOWN` phải được diễn giải là "chưa xác định" trong câu trả lời, không bao giờ đọc nguyên chữ UNKNOWN.
5. Lương: chỉ dùng `salary_midpoint_vnd` khi `salary_sample_flag = TRUE`. Trung vị =
   percentile_cont(0.5). Luôn nêu số mẫu (COUNT) đi kèm.
6. Khi đếm công ty, loại nhóm kỹ thuật: `AND NOT c.is_unknown` (join gold.dim_company).
7. Khi đếm kỹ năng, loại `d.is_unknown`.
8. Dữ liệu là "nguồn cung tin hệ thống quan sát được", KHÔNG phải tổng số việc làm thị trường.
9. `entity_status` là giá trị vòng đời đã chuẩn hoá, KHÔNG phải bằng chứng tin còn tuyển. Không
   dùng nó, và không dùng bất kỳ cột đếm trạng thái nào, để nói "đang mở"/"đang tuyển".
"""


def verify_schema(cursor: Any) -> list[str]:
    """Return the curated relations/columns missing from the live database."""
    problems: list[str] = []
    for relation in ALLOWED_RELATIONS:
        schema, name = relation.split(".", 1)
        cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, name),
        )
        columns = {row[0] for row in cursor.fetchall()}
        if not columns:
            problems.append(f"missing relation {relation}")
    # Spot-check the columns the prompt mentions by name in its examples.
    required = {
        "gold.v_job_snapshot": {"unified_job_id", "salary_midpoint_vnd", "salary_sample_flag"},
        "gold.v_dim_job_current": {"job_domain", "city_bucket", "job_level"},
        "gold.dim_company": {"company_display_name", "is_unknown"},
        "gold.v_market_daily": {"observed_job_count", "salary_p50_vnd"},
    }
    for relation, wanted in required.items():
        schema, name = relation.split(".", 1)
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
            (schema, name),
        )
        columns = {row[0] for row in cursor.fetchall()}
        for column in sorted(wanted - columns):
            problems.append(f"missing column {relation}.{column}")
    return problems
