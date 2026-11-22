"""Explicit development-only demo dataset for the IT Insight web app.

The fixture exists so the interface can be exercised without a Gold connection.
It is never a fallback: it is returned only when ``WEB_DEMO_MODE=true`` in a
non-production environment, and every response carries ``mode=demo`` plus the
demo release identifier so a viewer cannot mistake it for live data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .repository import GoldReleaseChanged, ReleaseContext
from .schemas import FilterParams

DEMO_GOLD_RELEASE = "gold_demo_fixture_2026-11-22"
DEMO_SILVER_RELEASE = "silver_demo_fixture_2026-11-22"
DEMO_SNAPSHOT_DATE = "2026-11-22"


@dataclass(frozen=True)
class DemoJob:
    unified_job_id: str
    canonical_title: str
    company_key: str
    company_display_name: str
    city_bucket: str
    job_domain: str
    job_level: str
    work_mode: str
    entity_status: str
    salary_min: int | None
    salary_max: int | None
    salary_midpoint: int | None
    salary_sample: bool
    source_count: int
    last_seen_at: str
    skills: tuple[tuple[str, str], ...]
    platforms: tuple[str, ...]


_RAW: tuple[dict[str, Any], ...] = (
    {
        "id": "demo-001", "title": "Senior Backend Engineer", "company": "Acme Software",
        "city": "Hà Nội", "domain": "Backend", "level": "Senior", "mode": "Hybrid",
        "salary": (35_000_000, 50_000_000), "skills": (("python", "Python"), ("postgresql", "PostgreSQL"), ("docker", "Docker")),
        "platforms": ("itviec", "topcv"),
    },
    {
        "id": "demo-002", "title": "Frontend Engineer (React)", "company": "Beta Digital",
        "city": "Hồ Chí Minh", "domain": "Frontend", "level": "Mid", "mode": "Onsite",
        "salary": (22_000_000, 32_000_000), "skills": (("react", "React"), ("typescript", "TypeScript"), ("css", "CSS")),
        "platforms": ("topcv", "vietnamworks"),
    },
    {
        "id": "demo-003", "title": "Data Engineer", "company": "Gamma Data",
        "city": "Hà Nội", "domain": "Data", "level": "Senior", "mode": "Remote",
        "salary": (40_000_000, 60_000_000), "skills": (("python", "Python"), ("spark", "Apache Spark"), ("sql", "SQL")),
        "platforms": ("itviec",),
    },
    {
        "id": "demo-004", "title": "DevOps Engineer", "company": "Delta Cloud",
        "city": "Đà Nẵng", "domain": "DevOps", "level": "Mid", "mode": "Hybrid",
        "salary": (25_000_000, 38_000_000), "skills": (("kubernetes", "Kubernetes"), ("docker", "Docker"), ("terraform", "Terraform")),
        "platforms": ("topdev",),
    },
    {
        "id": "demo-005", "title": "Machine Learning Engineer", "company": "Epsilon AI",
        "city": "Hồ Chí Minh", "domain": "Data", "level": "Senior", "mode": "Hybrid",
        "salary": (45_000_000, 70_000_000), "skills": (("python", "Python"), ("pytorch", "PyTorch"), ("mlops", "MLOps")),
        "platforms": ("itviec", "topcv"),
    },
    {
        "id": "demo-006", "title": "Junior Backend Developer", "company": "Zeta Startup",
        "city": "Hà Nội", "domain": "Backend", "level": "Junior", "mode": "Onsite",
        "salary": (12_000_000, 18_000_000), "skills": (("java", "Java"), ("spring", "Spring Boot"), ("sql", "SQL")),
        "platforms": ("vieclam24h",),
    },
    {
        "id": "demo-007", "title": "Mobile Engineer (Flutter)", "company": "Eta Mobile",
        "city": "Hồ Chí Minh", "domain": "Mobile", "level": "Mid", "mode": "Remote",
        "salary": (24_000_000, 34_000_000), "skills": (("flutter", "Flutter"), ("dart", "Dart")),
        "platforms": ("topcv",),
    },
    {
        "id": "demo-008", "title": "Business Analyst (IT)", "company": "Theta Consult",
        "city": "Hà Nội", "domain": "Business Analysis", "level": "Mid", "mode": "Onsite",
        "salary": None, "skills": (("sql", "SQL"), ("jira", "Jira")),
        "platforms": ("vietnamdevs",),
    },
    {
        "id": "demo-009", "title": "QA Automation Engineer", "company": "Iota Quality",
        "city": "Đà Nẵng", "domain": "QA", "level": "Mid", "mode": "Hybrid",
        "salary": (20_000_000, 28_000_000), "skills": (("playwright", "Playwright"), ("typescript", "TypeScript")),
        "platforms": ("itviec",),
    },
    {
        "id": "demo-010", "title": "Cloud Architect", "company": "Kappa Systems",
        "city": "Hồ Chí Minh", "domain": "DevOps", "level": "Lead", "mode": "Hybrid",
        "salary": (60_000_000, 90_000_000), "skills": (("aws", "AWS"), ("kubernetes", "Kubernetes"), ("terraform", "Terraform")),
        "platforms": ("topdev", "vietnamworks"),
    },
    {
        "id": "demo-011", "title": "Backend Engineer (Go)", "company": "Lambda Fintech",
        "city": "Hà Nội", "domain": "Backend", "level": "Mid", "mode": "Remote",
        "salary": (30_000_000, 45_000_000), "skills": (("go", "Go"), ("postgresql", "PostgreSQL"), ("docker", "Docker")),
        "platforms": ("itviec", "topcv"),
    },
    {
        "id": "demo-012", "title": "IT Support Specialist", "company": "Unknown",
        "city": "UNKNOWN", "domain": "UNKNOWN", "level": "Junior", "mode": "Onsite",
        "salary": None, "skills": (), "platforms": ("vieclam24h",),
    },
)


def _demo_jobs() -> list[DemoJob]:
    jobs: list[DemoJob] = []
    for index, row in enumerate(_RAW):
        salary = row["salary"]
        jobs.append(
            DemoJob(
                unified_job_id=row["id"],
                canonical_title=row["title"],
                company_key="demo-company-" + row["company"].lower().replace(" ", "-"),
                company_display_name=row["company"],
                city_bucket=row["city"],
                job_domain=row["domain"],
                job_level=row["level"],
                work_mode=row["mode"],
                entity_status="ACTIVE",
                salary_min=salary[0] if salary else None,
                salary_max=salary[1] if salary else None,
                salary_midpoint=(salary[0] + salary[1]) // 2 if salary else None,
                salary_sample=bool(salary),
                source_count=1 + index % 2,
                last_seen_at=f"2026-11-2{1 + index % 2}T0{index % 9}:00:00+00:00",
                skills=tuple(row["skills"]),
                platforms=tuple(row["platforms"]),
            )
        )
    return jobs


_JOBS = _demo_jobs()


class DemoRepository:
    """In-memory implementation of the read-only repository contract."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def close(self) -> None:
        """Compatibility hook; the fixture holds no connection."""

    def release_context(self) -> ReleaseContext:
        return ReleaseContext(
            gold_release_id=DEMO_GOLD_RELEASE,
            silver_release_id=DEMO_SILVER_RELEASE,
            snapshot_date=DEMO_SNAPSHOT_DATE,
            snapshot_cutoff_at=f"{DEMO_SNAPSHOT_DATE}T03:00:00+00:00",
            switched_at=f"{DEMO_SNAPSHOT_DATE}T03:10:00+00:00",
            status="SUCCESS",
            dq_total=32,
            dq_failed=0,
        )

    def ready(self) -> ReleaseContext:
        return self.release_context()

    def _assert_expected_release(self, context: ReleaseContext, params: FilterParams) -> None:
        if params.expected_release_id and params.expected_release_id != context.gold_release_id:
            raise GoldReleaseChanged()

    @staticmethod
    def _with_context(context: ReleaseContext, payload: dict[str, Any]) -> dict[str, Any]:
        return {"_context": context, **payload}

    @staticmethod
    def _matches(job: DemoJob, params: FilterParams) -> bool:
        if params.q:
            needle = params.q.casefold()
            if needle not in job.canonical_title.casefold() and needle not in job.company_display_name.casefold():
                return False
        if params.city and job.city_bucket != params.city:
            return False
        if params.domain and job.job_domain != params.domain:
            return False
        if params.level and job.job_level != params.level:
            return False
        if params.company_key and job.company_key != params.company_key:
            return False
        if params.skill and params.skill not in {code for code, _ in job.skills}:
            return False
        return True

    def _cohort(self, params: FilterParams) -> list[DemoJob]:
        return [job for job in _JOBS if self._matches(job, params)]

    @staticmethod
    def _job_payload(job: DemoJob) -> dict[str, Any]:
        return {
            "unified_job_id": job.unified_job_id,
            "canonical_title": job.canonical_title,
            "company_key": job.company_key,
            "company_display_name": job.company_display_name,
            "city_bucket": job.city_bucket,
            "job_domain": job.job_domain,
            "job_level": job.job_level,
            "work_mode": job.work_mode,
            "entity_status": job.entity_status,
            "canonical_salary_min_vnd": job.salary_min,
            "canonical_salary_max_vnd": job.salary_max,
            "salary_midpoint_vnd": job.salary_midpoint,
            "salary_sample_flag": job.salary_sample,
            "source_count": job.source_count,
            "observation_count": job.source_count,
            "last_seen_at": job.last_seen_at,
            "snapshot_cutoff_at": f"{DEMO_SNAPSHOT_DATE}T03:00:00+00:00",
            "skills": [{"skill_code": code, "skill_name": name} for code, name in job.skills],
            "platforms": list(job.platforms),
        }

    def filters(self) -> dict[str, list[dict[str, Any]]]:
        context = self.release_context()

        def counts(values: list[str]) -> list[dict[str, Any]]:
            tally: dict[str, int] = {}
            for value in values:
                tally[value] = tally.get(value, 0) + 1
            return [
                {"value": value, "label": value, "count": count}
                for value, count in sorted(tally.items(), key=lambda item: (-item[1], item[0]))
            ]

        skills: dict[str, tuple[str, int]] = {}
        for job in _JOBS:
            for code, name in job.skills:
                current = skills.get(code, (name, 0))
                skills[code] = (current[0], current[1] + 1)
        return self._with_context(
            context,
            {
                "cities": counts([job.city_bucket for job in _JOBS]),
                "domains": counts([job.job_domain for job in _JOBS]),
                "levels": counts([job.job_level for job in _JOBS]),
                "skills": [
                    {"value": code, "label": name, "count": count}
                    for code, (name, count) in sorted(skills.items(), key=lambda item: (-item[1][1], item[0]))
                ],
            },
        )

    def dashboard(self, params: FilterParams) -> dict[str, Any]:
        context = self.release_context()
        self._assert_expected_release(context, params)
        cohort = self._cohort(params)
        samples = sorted(job.salary_midpoint for job in cohort if job.salary_sample and job.salary_midpoint)
        median = None
        if samples:
            middle = len(samples) // 2
            median = (
                samples[middle]
                if len(samples) % 2
                else (samples[middle - 1] + samples[middle]) // 2
            )

        def group(key) -> list[dict[str, Any]]:
            tally: dict[str, int] = {}
            for job in cohort:
                tally[key(job)] = tally.get(key(job), 0) + 1
            return [
                {"value": value, "count": count}
                for value, count in sorted(tally.items(), key=lambda item: (-item[1], item[0]))
            ]

        companies: dict[str, dict[str, Any]] = {}
        for job in cohort:
            if job.company_display_name == "Unknown":
                continue
            entry = companies.setdefault(
                job.company_key,
                {"company_key": job.company_key, "label": job.company_display_name, "count": 0},
            )
            entry["count"] += 1
        platforms: dict[str, int] = {}
        skills: dict[str, dict[str, Any]] = {}
        for job in cohort:
            for platform in job.platforms:
                platforms[platform] = platforms.get(platform, 0) + 1
            for code, name in job.skills:
                entry = skills.setdefault(code, {"value": code, "label": name, "count": 0})
                entry["count"] += 1

        return self._with_context(
            context,
            {
                "summary": {
                    "jobs": len(cohort),
                    "companies": len(companies),
                    "skills": len(skills),
                    "salary_samples": len(samples),
                    "salary_median": median,
                    "missing_city": sum(1 for job in cohort if job.city_bucket == "UNKNOWN"),
                    "last_seen_at": max((job.last_seen_at for job in cohort), default=None),
                },
                "domains": group(lambda job: job.job_domain),
                "cities": group(lambda job: job.city_bucket),
                "levels": group(lambda job: job.job_level),
                "companies": sorted(companies.values(), key=lambda row: (-row["count"], row["label"]))[:10],
                "platforms": sorted(
                    ({"value": value, "count": count} for value, count in platforms.items()),
                    key=lambda row: (-row["count"], row["value"]),
                ),
                "skills": sorted(skills.values(), key=lambda row: (-row["count"], row["value"]))[:20],
            },
        )

    def jobs(self, params: FilterParams) -> dict[str, Any]:
        context = self.release_context()
        self._assert_expected_release(context, params)
        cohort = self._cohort(params)
        if params.sort == "salary":
            cohort.sort(key=lambda job: (-(job.salary_midpoint or -1), job.unified_job_id))
        elif params.sort == "title":
            cohort.sort(key=lambda job: (job.canonical_title.casefold(), job.unified_job_id))
        else:
            cohort.sort(key=lambda job: (job.last_seen_at, job.unified_job_id), reverse=True)
        start = (params.page - 1) * params.page_size
        page_rows = cohort[start : start + params.page_size]
        return self._with_context(
            context,
            {
                "items": [self._job_payload(job) for job in page_rows],
                "total": len(cohort),
                "page": params.page,
                "page_size": params.page_size,
            },
        )

    def job_detail(self, job_id: str) -> dict[str, Any] | None:
        context = self.release_context()
        for job in _JOBS:
            if job.unified_job_id == job_id:
                return self._with_context(context, {"job": self._job_payload(job)})
        return None

    def skills(self, params: FilterParams) -> dict[str, Any]:
        context = self.release_context()
        self._assert_expected_release(context, params)
        cohort = self._cohort(params)
        counts: dict[str, dict[str, Any]] = {}
        pair_counts: dict[tuple[str, str], int] = {}
        names: dict[str, str] = {}
        for job in cohort:
            codes = sorted({code for code, _ in job.skills})
            for code in codes:
                names[code] = dict(job.skills)[code]
                counts.setdefault(code, {"skill_code": code, "skill_name": names[code], "job_count": 0})
                counts[code]["job_count"] += 1
            for first in range(len(codes)):
                for second in range(first + 1, len(codes)):
                    key = (codes[first], codes[second])
                    pair_counts[key] = pair_counts.get(key, 0) + 1
        items = sorted(counts.values(), key=lambda row: (-row["job_count"], row["skill_code"]))
        pairs = [
            {
                "skill_a": a,
                "skill_a_name": names[a],
                "skill_b": b,
                "skill_b_name": names[b],
                "job_count": count,
            }
            for (a, b), count in sorted(pair_counts.items(), key=lambda item: (-item[1], item[0]))
        ][:100]
        return self._with_context(context, {"items": items, "pairs": pairs})

    def companies(self, params: FilterParams) -> dict[str, Any]:
        context = self.release_context()
        self._assert_expected_release(context, params)
        cohort = [job for job in self._cohort(params) if job.company_display_name != "Unknown"]
        grouped: dict[str, dict[str, Any]] = {}
        for job in cohort:
            entry = grouped.setdefault(
                job.company_key,
                {
                    "company_key": job.company_key,
                    "company_name": job.company_display_name,
                    "job_count": 0,
                    "cities": set(),
                    "domains": set(),
                    "salary_samples": 0,
                },
            )
            entry["job_count"] += 1
            entry["cities"].add(job.city_bucket)
            entry["domains"].add(job.job_domain)
            entry["salary_samples"] += 1 if job.salary_sample else 0
        rows = [
            {
                "company_key": entry["company_key"],
                "company_name": entry["company_name"],
                "job_count": entry["job_count"],
                "city_count": len(entry["cities"]),
                "domain_count": len(entry["domains"]),
                "salary_samples": entry["salary_samples"],
            }
            for entry in grouped.values()
        ]
        rows.sort(key=lambda row: (-row["job_count"], row["company_name"]))
        start = (params.page - 1) * params.page_size
        return self._with_context(
            context,
            {
                "items": rows[start : start + params.page_size],
                "total": len(rows),
                "page": params.page,
                "page_size": params.page_size,
            },
        )

    def quality(self, params: FilterParams | None = None) -> dict[str, Any]:
        context = self.release_context()
        if params is not None:
            self._assert_expected_release(context, params)
        checks = [
            {
                "check_code": f"DEMO-{index:02d}",
                "check_name": f"Kiểm tra minh họa {index}",
                "severity": "BLOCK",
                "passed": True,
                "expected": "demo",
                "actual": "demo",
                "diff": None,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            for index in range(1, 4)
        ]
        coverage = [
            {
                "snapshot_date": DEMO_SNAPSHOT_DATE,
                "status": "DEMO",
                "gold_release_id": DEMO_GOLD_RELEASE,
                "silver_release_id": DEMO_SILVER_RELEASE,
                "silver_ready_at": None,
                "gold_published_at": None,
                "bi_refreshed_at": None,
                "sla_breached": False,
            }
        ]
        return self._with_context(context, {"checks": checks, "coverage": coverage})
