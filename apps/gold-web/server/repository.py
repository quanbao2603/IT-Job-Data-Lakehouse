from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

import psycopg2

from .config import Settings, get_settings
from .schemas import FilterParams


class GoldWebError(RuntimeError):
    code = "GOLD_UNAVAILABLE"
    public_message = "Chưa thể đọc dữ liệu Gold."


class GoldNotConfigured(GoldWebError):
    code = "DB_NOT_CONFIGURED"
    public_message = "Chưa cấu hình kết nối Gold trên máy chủ."


class GoldNotReady(GoldWebError):
    code = "GOLD_RELEASE_MISSING"
    public_message = "Chưa có release Gold hợp lệ để phục vụ dữ liệu."


class GoldReleaseChanged(GoldWebError):
    code = "RELEASE_CHANGED"
    public_message = "Release dữ liệu đã thay đổi; hãy tải lại trang."


@dataclass(frozen=True)
class ReleaseContext:
    gold_release_id: str
    silver_release_id: str | None
    snapshot_date: str
    snapshot_cutoff_at: str
    switched_at: str | None
    status: str
    dq_total: int
    dq_failed: int


class GoldRepository:
    """Read-only repository for the currently active Gold release."""

    def __init__(
        self,
        settings: Settings | None = None,
        connection_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._connection_factory = connection_factory or self._connect

    def close(self) -> None:
        """Compatibility hook for request-scoped repository instances."""

    def _connect(self) -> Any:
        if not self.settings.reader_password:
            raise GoldNotConfigured()
        try:
            return psycopg2.connect(
                host=self.settings.db_host,
                port=self.settings.db_port,
                dbname=self.settings.db_name,
                user=self.settings.reader_user,
                password=self.settings.reader_password,
                connect_timeout=self.settings.connect_timeout,
                application_name="it-insight-web",
            )
        except Exception as exc:  # noqa: BLE001 - public error is intentionally generic
            raise GoldWebError("Gold PostgreSQL không khả dụng.") from exc

    @contextmanager
    def _read_transaction(self) -> Iterator[tuple[Any, Any]]:
        connection = self._connection_factory()
        cursor = None
        try:
            # Enforce read-only at the session level before the first statement so a
            # routing mistake cannot become a write, then pin one snapshot for every
            # query that builds a single response.
            connection.set_session(
                isolation_level="REPEATABLE READ", readonly=True, autocommit=False
            )
            cursor = connection.cursor()
            cursor.execute("SET LOCAL statement_timeout = %s", (self.settings.query_timeout_ms,))
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = %s", (self.settings.query_timeout_ms * 2,))
            yield connection, cursor
            connection.rollback()
        except GoldWebError:
            connection.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 - never expose driver details
            connection.rollback()
            raise GoldWebError("Không thể truy vấn dữ liệu Gold.") from exc
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def release_context(self, cursor: Any | None = None) -> ReleaseContext:
        if cursor is not None:
            return self._release_context_from_cursor(cursor)
        with self._read_transaction() as (_, current):
            return self._release_context_from_cursor(current)

    def _release_context_from_cursor(self, cursor: Any) -> ReleaseContext:
        cursor.execute(
            """
            SELECT a.gold_release_id,
                   a.snapshot_date,
                   a.switched_at,
                   l.silver_release_id,
                   l.snapshot_cutoff_at,
                   COALESCE(l.status, 'UNKNOWN') AS load_status,
                   COALESCE(d.dq_total, 0) AS dq_total,
                   COALESCE(d.dq_failed, 0) AS dq_failed
            FROM gold_control.active_release a
            LEFT JOIN LATERAL (
                SELECT silver_release_id, snapshot_cutoff_at, status
                FROM gold_control.load_run
                WHERE gold_release_id = a.gold_release_id
                ORDER BY started_at DESC NULLS LAST, finished_at DESC NULLS LAST
                LIMIT 1
            ) l ON TRUE
            LEFT JOIN (
                SELECT gold_release_id,
                       COUNT(*) AS dq_total,
                       COUNT(*) FILTER (WHERE NOT passed) AS dq_failed
                FROM gold_control.dq_result
                GROUP BY gold_release_id
            ) d ON d.gold_release_id = a.gold_release_id
            WHERE a.id = 1
            """
        )
        row = cursor.fetchone()
        if row is None:
            raise GoldNotReady()
        columns = [item[0] for item in cursor.description]
        data = dict(zip(columns, row))
        if not data.get("gold_release_id") or not data.get("snapshot_date"):
            raise GoldNotReady()
        return ReleaseContext(
            gold_release_id=str(data["gold_release_id"]),
            silver_release_id=self._text(data.get("silver_release_id")),
            snapshot_date=self._text(data["snapshot_date"]) or "",
            snapshot_cutoff_at=self._text(data.get("snapshot_cutoff_at")) or "",
            switched_at=self._text(data.get("switched_at")),
            status=str(data.get("load_status") or "UNKNOWN"),
            dq_total=int(data.get("dq_total") or 0),
            dq_failed=int(data.get("dq_failed") or 0),
        )

    def ready(self) -> ReleaseContext:
        with self._read_transaction() as (_, cursor):
            context = self._release_context_from_cursor(cursor)
            if context.status != "SUCCESS" or context.dq_failed:
                raise GoldNotReady()
            return context

    @staticmethod
    def _where(params: FilterParams, snapshot_alias: str = "s", job_alias: str = "j", company_alias: str = "c") -> tuple[str, list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if params.q:
            pattern = f"%{GoldRepository._escape_like(params.q)}%"
            clauses.append(
                f"({job_alias}.canonical_title ILIKE %s ESCAPE '\\' "
                f"OR {company_alias}.company_display_name ILIKE %s ESCAPE '\\')"
            )
            values.extend([pattern, pattern])
        filter_columns = (
            (f"{job_alias}.city_bucket", params.city),
            (f"COALESCE({job_alias}.job_domain, 'UNKNOWN')", params.domain),
            (f"COALESCE({job_alias}.job_level, 'UNKNOWN')", params.level),
            (f"{company_alias}.company_key", params.company_key),
        )
        for column, value in filter_columns:
            if value:
                clauses.append(f"{column} = %s")
                values.append(value)
        if params.skill:
            clauses.append(
                f"EXISTS (SELECT 1 FROM gold.v_job_skill sf "
                f"JOIN gold.dim_skill sd ON sd.skill_key = sf.skill_key "
                f"WHERE sf.unified_job_id = {snapshot_alias}.unified_job_id "
                f"AND sf.snapshot_date = {snapshot_alias}.snapshot_date "
                f"AND sd.skill_code = %s AND NOT sd.is_unknown)"
            )
            values.append(params.skill)
        return (" AND ".join(clauses) if clauses else "TRUE"), values

    def _assert_expected_release(self, context: ReleaseContext, params: FilterParams) -> None:
        if params.expected_release_id and params.expected_release_id != context.gold_release_id:
            raise GoldReleaseChanged()

    @staticmethod
    def _with_context(context: ReleaseContext, payload: dict[str, Any]) -> dict[str, Any]:
        """Carry the transaction's release snapshot to the API layer."""
        return {"_context": context, **payload}

    @staticmethod
    def _age_days(snapshot_date: str) -> int:
        try:
            today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
            return max(0, (today - date.fromisoformat(snapshot_date[:10])).days)
        except (TypeError, ValueError):
            return 0

    def filters(self) -> dict[str, list[dict[str, Any]]]:
        with self._read_transaction() as (_, cursor):
            context = self._release_context_from_cursor(cursor)
            result: dict[str, list[dict[str, Any]]] = {}
            queries = {
                "cities": """
                    SELECT j.city_bucket AS value, j.city_bucket AS label, COUNT(*)::int AS count
                    FROM gold.v_job_snapshot s JOIN gold.dim_job j ON j.job_key=s.job_key
                    GROUP BY j.city_bucket ORDER BY count DESC, value
                """,
                "domains": """
                    SELECT COALESCE(j.job_domain, 'UNKNOWN') AS value,
                           COALESCE(j.job_domain, 'UNKNOWN') AS label, COUNT(*)::int AS count
                    FROM gold.v_job_snapshot s JOIN gold.dim_job j ON j.job_key=s.job_key
                    GROUP BY COALESCE(j.job_domain, 'UNKNOWN') ORDER BY count DESC, value
                """,
                "levels": """
                    SELECT COALESCE(j.job_level, 'UNKNOWN') AS value,
                           COALESCE(j.job_level, 'UNKNOWN') AS label, COUNT(*)::int AS count
                    FROM gold.v_job_snapshot s JOIN gold.dim_job j ON j.job_key=s.job_key
                    GROUP BY COALESCE(j.job_level, 'UNKNOWN') ORDER BY count DESC, value
                """,
                "skills": """
                    SELECT d.skill_code AS value, d.skill_name AS label,
                           COUNT(DISTINCT f.unified_job_id)::int AS count
                    FROM gold.v_job_skill f JOIN gold.dim_skill d ON d.skill_key=f.skill_key
                    WHERE NOT d.is_unknown
                    GROUP BY d.skill_code, d.skill_name ORDER BY count DESC, value
                """,
            }
            for key, query in queries.items():
                cursor.execute(query)
                result[key] = [dict(zip((item[0] for item in cursor.description), row)) for row in cursor.fetchall()]
            return self._with_context(context, result)

    def dashboard(self, params: FilterParams) -> dict[str, Any]:
        where, values = self._where(params)
        with self._read_transaction() as (_, cursor):
            context = self._release_context_from_cursor(cursor)
            self._assert_expected_release(context, params)
            base = f"""
                FROM gold.v_job_snapshot s
                JOIN gold.dim_job j ON j.job_key = s.job_key
                JOIN gold.dim_company c ON c.company_key = s.company_key
                WHERE {where}
            """
            cursor.execute(
                f"""
                SELECT COUNT(*)::int AS jobs,
                       COUNT(DISTINCT c.company_key) FILTER (WHERE NOT c.is_unknown)::int AS companies,
                       COUNT(*) FILTER (WHERE s.salary_sample_flag)::int AS salary_samples,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY s.salary_midpoint_vnd)
                           FILTER (WHERE s.salary_sample_flag) AS salary_median,
                       COUNT(*) FILTER (WHERE j.city_bucket = 'UNKNOWN')::int AS missing_city,
                       MAX(s.last_seen_at) AS last_seen_at
                {base}
                """,
                values,
            )
            summary = dict(zip((item[0] for item in cursor.description), cursor.fetchone()))
            summary["salary_median"] = float(summary["salary_median"]) if summary["salary_median"] is not None else None
            summary["last_seen_at"] = self._text(summary.get("last_seen_at"))
            cursor.execute(
                f"""
                SELECT COUNT(DISTINCT sf.skill_key)::int
                FROM gold.v_job_skill sf
                JOIN gold.dim_skill sd ON sd.skill_key = sf.skill_key
                JOIN (SELECT DISTINCT s.unified_job_id {base}) cohort
                  ON cohort.unified_job_id = sf.unified_job_id
                WHERE NOT sd.is_unknown
                """,
                values,
            )
            summary["skills"] = int(cursor.fetchone()[0])
            groups: dict[str, list[dict[str, Any]]] = {}
            group_queries = {
                "domains": f"SELECT COALESCE(j.job_domain, 'UNKNOWN') AS value, COUNT(*)::int AS count {base} GROUP BY 1 ORDER BY count DESC, value LIMIT 20",
                "cities": f"SELECT j.city_bucket AS value, COUNT(*)::int AS count {base} GROUP BY 1 ORDER BY count DESC, value",
                "levels": f"SELECT COALESCE(j.job_level, 'UNKNOWN') AS value, COUNT(*)::int AS count {base} GROUP BY 1 ORDER BY count DESC, value",
                "companies": f"SELECT c.company_key AS company_key, c.company_display_name AS label, COUNT(*)::int AS count {base} AND NOT c.is_unknown GROUP BY c.company_key, c.company_display_name ORDER BY count DESC, label LIMIT 10",
            }
            for key, query in group_queries.items():
                cursor.execute(query, values)
                groups[key] = [dict(zip((item[0] for item in cursor.description), row)) for row in cursor.fetchall()]
            cursor.execute(
                f"""
                SELECT dp.platform_code AS value, COUNT(DISTINCT f.unified_job_id)::int AS count
                FROM gold.v_job_platform f
                JOIN gold.dim_platform dp ON dp.platform_key=f.platform_key
                JOIN (SELECT DISTINCT s.unified_job_id {base}) cohort
                  ON cohort.unified_job_id=f.unified_job_id
                GROUP BY dp.platform_code ORDER BY count DESC, value
                """,
                values,
            )
            groups["platforms"] = [dict(zip((item[0] for item in cursor.description), row)) for row in cursor.fetchall()]
            cursor.execute(
                f"""
                SELECT d.skill_code AS value, d.skill_name AS label,
                       COUNT(DISTINCT f.unified_job_id)::int AS count
                FROM gold.v_job_skill f JOIN gold.dim_skill d ON d.skill_key=f.skill_key
                JOIN (SELECT DISTINCT s.unified_job_id {base}) cohort
                  ON cohort.unified_job_id=f.unified_job_id
                WHERE NOT d.is_unknown
                GROUP BY d.skill_code, d.skill_name ORDER BY count DESC, value LIMIT 20
                """,
                values,
            )
            groups["skills"] = [dict(zip((item[0] for item in cursor.description), row)) for row in cursor.fetchall()]
            return self._with_context(context, {"summary": summary, **groups})

    def jobs(self, params: FilterParams) -> dict[str, Any]:
        where, values = self._where(params)
        order_by = {
            "recent": "s.last_seen_at DESC NULLS LAST, s.unified_job_id",
            "salary": "s.salary_midpoint_vnd DESC NULLS LAST, s.unified_job_id",
            "title": "j.canonical_title ASC, s.unified_job_id",
        }[params.sort]
        with self._read_transaction() as (_, cursor):
            context = self._release_context_from_cursor(cursor)
            self._assert_expected_release(context, params)
            from_sql = f"""
                FROM gold.v_job_snapshot s
                JOIN gold.dim_job j ON j.job_key=s.job_key
                JOIN gold.dim_company c ON c.company_key=s.company_key
                WHERE {where}
            """
            cursor.execute(f"SELECT COUNT(*)::int {from_sql}", values)
            total = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT s.unified_job_id, j.canonical_title, c.company_key,
                       c.company_display_name, j.city_bucket, j.job_domain,
                       j.job_level, j.work_mode, s.entity_status,
                       s.canonical_salary_min_vnd, s.canonical_salary_max_vnd,
                       s.salary_midpoint_vnd, s.salary_sample_flag,
                       s.source_count, s.last_seen_at, s.job_key
                {from_sql}
                ORDER BY {order_by}
                LIMIT %s OFFSET %s
                """,
                [*values, params.page_size, (params.page - 1) * params.page_size],
            )
            columns = [item[0] for item in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]
            self._enrich_job_rows(cursor, items)
            return self._with_context(
                context,
                {"items": items, "total": total, "page": params.page, "page_size": params.page_size},
            )

    def _enrich_job_rows(self, cursor: Any, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        ids = [item["unified_job_id"] for item in items]
        cursor.execute(
            """
            SELECT f.unified_job_id, d.skill_code, d.skill_name
            FROM gold.v_job_skill f JOIN gold.dim_skill d ON d.skill_key=f.skill_key
            WHERE f.unified_job_id = ANY(%s)
            GROUP BY f.unified_job_id, d.skill_code, d.skill_name
            ORDER BY d.skill_name
            """,
            (ids,),
        )
        skills: dict[str, list[dict[str, str]]] = {}
        for job_id, code, name in cursor.fetchall():
            skills.setdefault(job_id, []).append({"skill_code": code, "skill_name": name})
        cursor.execute(
            """
            SELECT f.unified_job_id, d.platform_code
            FROM gold.v_job_platform f JOIN gold.dim_platform d ON d.platform_key=f.platform_key
            WHERE f.unified_job_id = ANY(%s)
            GROUP BY f.unified_job_id, d.platform_code ORDER BY d.platform_code
            """,
            (ids,),
        )
        platforms: dict[str, list[str]] = {}
        for job_id, code in cursor.fetchall():
            platforms.setdefault(job_id, []).append(code)
        for item in items:
            item["skills"] = skills.get(item["unified_job_id"], [])
            item["platforms"] = platforms.get(item["unified_job_id"], [])
            for key in ("last_seen_at",):
                item[key] = self._text(item.get(key))

    def job_detail(self, job_id: str) -> dict[str, Any] | None:
        with self._read_transaction() as (_, cursor):
            context = self._release_context_from_cursor(cursor)
            cursor.execute(
                """
                SELECT s.unified_job_id, j.canonical_title, c.company_key,
                       c.company_display_name, j.city_bucket, j.job_domain,
                       j.job_level, j.work_mode, s.entity_status,
                       s.canonical_salary_min_vnd, s.canonical_salary_max_vnd,
                       s.salary_midpoint_vnd, s.salary_sample_flag,
                       s.source_count, s.observation_count, s.last_seen_at,
                       s.snapshot_cutoff_at, s.job_key
                FROM gold.v_job_snapshot s
                JOIN gold.dim_job j ON j.job_key=s.job_key
                JOIN gold.dim_company c ON c.company_key=s.company_key
                WHERE s.unified_job_id=%s
                """,
                (job_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            item = dict(zip((item[0] for item in cursor.description), row))
            self._enrich_job_rows(cursor, [item])
            return self._with_context(context, {"job": item})

    def skills(self, params: FilterParams) -> dict[str, Any]:
        where, values = self._where(params)
        with self._read_transaction() as (_, cursor):
            context = self._release_context_from_cursor(cursor)
            self._assert_expected_release(context, params)
            cohort = f"SELECT DISTINCT s.unified_job_id {self._base_from(where)}"
            cursor.execute(
                f"""
                SELECT d.skill_code, d.skill_name, COUNT(DISTINCT f.unified_job_id)::int AS job_count
                FROM gold.v_job_skill f JOIN gold.dim_skill d ON d.skill_key=f.skill_key
                JOIN ({cohort}) cohort ON cohort.unified_job_id=f.unified_job_id
                WHERE NOT d.is_unknown
                GROUP BY d.skill_code, d.skill_name ORDER BY job_count DESC, d.skill_name
                """,
                values,
            )
            items = [dict(zip((item[0] for item in cursor.description), row)) for row in cursor.fetchall()]
            cursor.execute(
                f"""
                WITH cohort AS ({cohort}), job_skills AS (
                    SELECT DISTINCT f.unified_job_id, f.skill_key
                    FROM gold.v_job_skill f JOIN cohort ON cohort.unified_job_id=f.unified_job_id
                )
                SELECT da.skill_code AS skill_a, da.skill_name AS skill_a_name,
                       db.skill_code AS skill_b, db.skill_name AS skill_b_name,
                       COUNT(DISTINCT a.unified_job_id)::int AS job_count
                FROM job_skills a
                JOIN job_skills b ON a.unified_job_id=b.unified_job_id AND a.skill_key < b.skill_key
                JOIN gold.dim_skill da ON da.skill_key=a.skill_key AND NOT da.is_unknown
                JOIN gold.dim_skill db ON db.skill_key=b.skill_key AND NOT db.is_unknown
                GROUP BY da.skill_code, da.skill_name, db.skill_code, db.skill_name
                ORDER BY job_count DESC, skill_a, skill_b LIMIT 100
                """,
                values,
            )
            pairs = [dict(zip((item[0] for item in cursor.description), row)) for row in cursor.fetchall()]
            return self._with_context(context, {"items": items, "pairs": pairs})

    @staticmethod
    def _base_from(where: str) -> str:
        return f"""
            FROM gold.v_job_snapshot s
            JOIN gold.dim_job j ON j.job_key=s.job_key
            JOIN gold.dim_company c ON c.company_key=s.company_key
            WHERE {where}
        """

    def companies(self, params: FilterParams) -> dict[str, Any]:
        where, values = self._where(params)
        with self._read_transaction() as (_, cursor):
            context = self._release_context_from_cursor(cursor)
            self._assert_expected_release(context, params)
            base = self._base_from(where)
            # Company KPI/drill-through excludes the technical UNKNOWN member.
            company_base = f"{base} AND NOT c.is_unknown"
            cursor.execute(
                f"SELECT COUNT(*)::int FROM (SELECT c.company_key {company_base} GROUP BY c.company_key) x",
                values,
            )
            total = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT c.company_key, c.company_display_name AS company_name,
                       COUNT(*)::int AS job_count,
                       COUNT(DISTINCT j.city_bucket)::int AS city_count,
                       COUNT(DISTINCT COALESCE(j.job_domain, 'UNKNOWN'))::int AS domain_count,
                       COUNT(*) FILTER (WHERE s.salary_sample_flag)::int AS salary_samples
                {company_base}
                GROUP BY c.company_key, c.company_display_name
                ORDER BY job_count DESC, company_name
                LIMIT %s OFFSET %s
                """,
                [*values, params.page_size, (params.page - 1) * params.page_size],
            )
            items = [dict(zip((item[0] for item in cursor.description), row)) for row in cursor.fetchall()]
            return self._with_context(
                context,
                {"items": items, "total": total, "page": params.page, "page_size": params.page_size},
            )

    def quality(self, params: FilterParams | None = None) -> dict[str, Any]:
        with self._read_transaction() as (_, cursor):
            context = self._release_context_from_cursor(cursor)
            if params is not None:
                self._assert_expected_release(context, params)
            cursor.execute(
                """
                SELECT check_code, check_name, severity, passed, expected, actual, diff, checked_at
                FROM gold_control.dq_result
                WHERE gold_release_id=%s ORDER BY check_code
                """,
                (context.gold_release_id,),
            )
            checks = [dict(zip((item[0] for item in cursor.description), row)) for row in cursor.fetchall()]
            for row in checks:
                row["checked_at"] = self._text(row.get("checked_at"))
            cursor.execute(
                """
                SELECT snapshot_date, status, gold_release_id, silver_release_id,
                       silver_ready_at, gold_published_at, bi_refreshed_at, sla_breached
                FROM gold.v_daily_coverage ORDER BY snapshot_date DESC LIMIT 30
                """
            )
            coverage = [dict(zip((item[0] for item in cursor.description), row)) for row in cursor.fetchall()]
            for row in coverage:
                for key in ("snapshot_date", "silver_ready_at", "gold_published_at", "bi_refreshed_at"):
                    row[key] = self._text(row.get(key))
            return self._with_context(context, {"checks": checks, "coverage": coverage})
