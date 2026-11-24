"""Small, deterministic Bronze-to-Silver normalization helpers."""
from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"ref", "source", "fbclid", "search_id"}
LEGAL_PREFIXES = ("công ty tnhh", "ctcp", "công ty cổ phần", "cong ty tnhh", "cong ty co phan")
LEGAL_SUFFIXES = ("co., ltd", "co ltd", "jsc", "inc.", "inc")
TITLE_STOP_WORDS = {"tuyển", "dụng", "job", "jobs", "hiring", "urgent", "việc", "làm"}
CITY_ALIASES = {
    "ha noi": "Hà Nội", "hanoi": "Hà Nội", "hà nội": "Hà Nội",
    "ho chi minh": "Hồ Chí Minh", "ho chi minh city": "Hồ Chí Minh",
    "hochiminh": "Hồ Chí Minh", "hồ chí minh": "Hồ Chí Minh", "sai gon": "Hồ Chí Minh",
    "da nang": "Đà Nẵng", "đà nẵng": "Đà Nẵng", "danang": "Đà Nẵng",
}
JOB_LEVELS = ("Intern", "Fresher", "Junior", "Mid", "Senior", "Lead", "Director")
SKILL_TAXONOMY = (
    "python", "java", "javascript", "typescript", "golang", "rust", "php", "c#", "c++", "kotlin", "swift",
    "ruby", "scala", "dart", "sql", "nosql", "html", "css", "react", "angular", "vue", "next.js", "nuxt.js",
    "node.js", "express", "nestjs", "django", "flask", "fastapi", "spring", "spring boot", ".net", "laravel",
    "rails", "graphql", "rest api", "grpc", "microservices", "docker", "kubernetes", "helm", "terraform",
    "ansible", "jenkins", "github actions", "gitlab ci", "azure devops", "aws", "gcp", "azure", "ec2", "s3",
    "lambda", "cloudfront", "vpc", "rds", "dynamodb", "bigquery", "redshift", "snowflake", "databricks",
    "spark", "pyspark", "hadoop", "kafka", "rabbitmq", "redis", "elasticsearch", "mongodb", "postgresql",
    "mysql", "mariadb", "oracle", "sql server", "sqlite", "firebase", "supabase", "airflow", "dbt", "etl",
    "elt", "data engineering", "data science", "machine learning", "deep learning", "tensorflow", "pytorch",
    "scikit-learn", "pandas", "numpy", "opencv", "nlp", "llm", "generative ai", "computer vision", "mlops",
    "devops", "sre", "linux", "bash", "networking", "security", "cybersecurity", "penetration testing", "iam",
    "oauth", "jwt", "qa", "testing", "unit testing", "selenium", "cypress", "playwright", "postman", "jira",
    "confluence", "git", "github", "gitlab", "bitbucket", "figma", "ui/ux", "product management", "agile",
    "scrum", "kanban", "system design", "solid", "design patterns", "technical writing", "wordpress", "shopify",
)

# Versioned normalization contracts. Gold records these in gold_control.load_run so a
# taxonomy or naming-rule change can never silently rewrite historical reports.
SKILL_TAXONOMY_VERSION = "skill-taxonomy-v1"

# Display names for acronyms and product spellings that `str.title()` gets wrong
# ("Sql", "Aws", "Javascript"). The taxonomy key stays lowercase; only the label
# rendered on the dashboard is corrected.
SKILL_DISPLAY_NAMES = {
    "sql": "SQL", "nosql": "NoSQL", "html": "HTML", "css": "CSS", "aws": "AWS",
    "gcp": "GCP", "ec2": "EC2", "s3": "S3", "rds": "RDS", "vpc": "VPC",
    "iam": "IAM", "jwt": "JWT", "oauth": "OAuth", "api": "API",
    "rest api": "REST API", "graphql": "GraphQL", "grpc": "gRPC",
    "javascript": "JavaScript", "typescript": "TypeScript",
    "postgresql": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB",
    "mongodb": "MongoDB", "sqlite": "SQLite", "sql server": "SQL Server",
    "dynamodb": "DynamoDB", "bigquery": "BigQuery", "snowflake": "Snowflake",
    "databricks": "Databricks", "pyspark": "PySpark", "hadoop": "Hadoop",
    "kafka": "Kafka", "rabbitmq": "RabbitMQ", "redis": "Redis",
    "elasticsearch": "Elasticsearch", "firebase": "Firebase", "supabase": "Supabase",
    "airflow": "Airflow", "dbt": "dbt", "etl": "ETL", "elt": "ELT",
    "next.js": "Next.js", "nuxt.js": "Nuxt.js", "node.js": "Node.js",
    "express": "Express", "nestjs": "NestJS", "django": "Django", "flask": "Flask",
    "fastapi": "FastAPI", "spring": "Spring", "spring boot": "Spring Boot",
    ".net": ".NET", "laravel": "Laravel", "rails": "Rails",
    "docker": "Docker", "kubernetes": "Kubernetes", "helm": "Helm",
    "terraform": "Terraform", "ansible": "Ansible", "jenkins": "Jenkins",
    "github actions": "GitHub Actions", "gitlab ci": "GitLab CI",
    "azure devops": "Azure DevOps", "azure": "Azure", "cloudfront": "CloudFront",
    "redshift": "Redshift", "spark": "Spark", "tensorflow": "TensorFlow",
    "pytorch": "PyTorch", "scikit-learn": "scikit-learn", "pandas": "pandas",
    "numpy": "NumPy", "opencv": "OpenCV", "nlp": "NLP", "llm": "LLM",
    "generative ai": "Generative AI", "computer vision": "Computer Vision",
    "mlops": "MLOps", "devops": "DevOps", "sre": "SRE", "linux": "Linux",
    "bash": "Bash", "qa": "QA", "ui/ux": "UI/UX", "jira": "Jira",
    "confluence": "Confluence", "git": "Git", "github": "GitHub",
    "gitlab": "GitLab", "bitbucket": "Bitbucket", "figma": "Figma",
    "wordpress": "WordPress", "shopify": "Shopify", "php": "PHP",
    "kotlin": "Kotlin", "swift": "Swift", "rust": "Rust", "golang": "Golang",
    "scala": "Scala", "dart": "Dart", "ruby": "Ruby", "react": "React",
    "angular": "Angular", "vue": "Vue", "selenium": "Selenium",
    "cypress": "Cypress", "playwright": "Playwright", "postman": "Postman",
    "agile": "Agile", "scrum": "Scrum", "kanban": "Kanban",
    "penetration testing": "Penetration Testing", "cybersecurity": "Cybersecurity",
    "unit testing": "Unit Testing", "testing": "Testing", "security": "Security",
}


def skill_display_name(code: str) -> str:
    """Human label for a taxonomy code, keeping acronyms and product casing intact."""
    key = str(code).strip()
    if key.casefold() in SKILL_DISPLAY_NAMES:
        return SKILL_DISPLAY_NAMES[key.casefold()]
    return key.title()
COMPANY_NORMALIZATION_VERSION = "company-normalization-v2"
CITY_NORMALIZATION_VERSION = "city-normalization-v2"
TRANSFORM_VERSION = "bronze-to-silver-transform-v2"


def normalization_versions() -> dict[str, str]:
    """Expose every versioned normalization contract for release manifests."""
    return {
        "skill_taxonomy_version": SKILL_TAXONOMY_VERSION,
        "company_normalization_version": COMPANY_NORMALIZATION_VERSION,
        "city_normalization_version": CITY_NORMALIZATION_VERSION,
        "transform_version": TRANSFORM_VERSION,
    }


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).casefold()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_title(value: Any) -> str:
    words = normalize_text(value).split()
    return " ".join(word for word in words if word not in TITLE_STOP_WORDS)


# Platform decorations pasted in front of the real job title ("[ Hot Job ] Cloud
# Solution Architect", "Phản hồi vượt trội Lập Trình Java"). Stripping them keeps the
# card readable without inventing a new title.
_TITLE_PREFIXES = (
    r"^\[\s*hot(\s*job)?\s*\]\s*", r"^\[\s*new\s*\]\s*", r"^\[\s*gấp\s*\]\s*",
    r"^\[\s*vip\s*\]\s*", r"^\[\s*tuyển\s*gấp\s*\]\s*",
    r"^phản\s+hồi\s+(vượt\s+trội|thường\s+xuyên|nhanh)\s+",
    r"^ứng\s+tuyển\s+nhanh\s+", r"^hot\s+job\s*[-:|]?\s*",
)


def clean_display_title(value: Any) -> str:
    """Strip platform badges from a title for display; never touches job_title_normalized."""
    text = normalize_text(value)
    if not text:
        return text
    previous = None
    while previous != text:
        previous = text
        for pattern in _TITLE_PREFIXES:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
    return text or previous or ""


def _fold(value: Any) -> str:
    """Casefold and strip Vietnamese diacritics for tolerant matching."""
    text = unicodedata.normalize("NFD", normalize_text(value))
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


# Ordered city needles; the earliest occurrence in the folded string wins, so
# "Hanoi / HCMC" resolves to the first city the source listed.
CITY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Hồ Chí Minh", ("ho chi minh", "tp.hcm", "tphcm", "hcm", "sai gon", "saigon", "tp hcm")),
    ("Hà Nội", ("ha noi", "hanoi")),
    ("Đà Nẵng", ("da nang", "danang")),
)


def normalize_city(value: Any) -> Optional[str]:
    """Map free-form locations and addresses onto the five-group Gold axis."""
    text = normalize_text(value)
    if not text:
        return None
    folded = f" {_fold(text)} "
    best: Optional[tuple[int, str]] = None
    for label, needles in CITY_PATTERNS:
        for needle in needles:
            index = folded.find(needle)
            if index >= 0 and (best is None or index < best[0]):
                best = (index, label)
    if best is not None:
        return best[1]
    return "Khác"


LEVEL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Director", (r"\bdirector\b", r"giam doc", r"\bcto\b", r"\bcio\b", r"head of")),
    ("Intern", (r"\bintern(ship)?\b", r"thuc tap", r"\btts\b")),
    ("Fresher", (r"\bfresher\b", r"entry level", r"moi tot nghiep")),
    ("Lead", (r"\bteam lead\b", r"\btech lead\b", r"truong nhom", r"truong phong", r"\bmanager\b",
              r"quan ly", r"\bprincipal\b", r"^lead\b", r"\blead engineer\b", r"\bengineering lead\b")),
    ("Senior", (r"\bsenior\b", r"\bsr\.?\b", r"cap cao")),
    ("Junior", (r"\bjunior\b", r"\bjr\.?\b", r"nhan vien", r"\bentry\b")),
    ("Mid", (r"\bmid\b", r"\bmiddle\b", r"\bexperienced\b", r"chuyen vien", r"specialist")),
)


def _match_level(folded: str) -> Optional[str]:
    for level, patterns in LEVEL_RULES:
        for pattern in patterns:
            if re.search(pattern, folded):
                return level
    return None


# Titles are noisier than a dedicated seniority field: "Chuyên viên Quản lý chất
# lượng dữ liệu" is a mid-level role, not a manager. Title matching therefore uses
# word-boundary patterns and drops the bare manager/quản lý needles.
TITLE_LEVEL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Director", (r"\bdirector\b", r"giam doc", r"\bcto\b", r"\bcio\b", r"head of")),
    ("Intern", (r"\bintern\b", r"thuc tap", r"\btts\b")),
    ("Fresher", (r"\bfresher\b", r"entry level", r"moi tot nghiep")),
    ("Lead", (r"\bteam lead\b", r"\btech lead\b", r"truong nhom", r"truong phong", r"\bprincipal\b",
              r"^lead\b", r"\blead engineer\b", r"\bengineering lead\b", r"\bmanager\b")),
    ("Senior", (r"\bsenior\b", r"\bsr\.?\b", r"cap cao")),
    ("Junior", (r"\bjunior\b", r"\bjr\.?\b", r"nhan vien")),
    ("Mid", (r"\bmid\b", r"\bmiddle\b", r"chuyen vien", r"specialist")),
)


def _match_title_level(folded: str) -> Optional[str]:
    for level, patterns in TITLE_LEVEL_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, folded):
                return level
    return None


def classify_job_level(
    value: Any = None,
    *,
    title: Any = None,
    experience_years: Optional[int] = None,
) -> Optional[str]:
    """Classify seniority from the source field, then the title, then years.

    Sources label seniority inconsistently ("Experienced (non-manager)",
    "Chuyên viên cấp cao", "Fresher/Entry level"), so an exact enum lookup
    silently produced NULL for most platforms and the dashboard reported
    "chưa xác định" for 98% of jobs.
    """
    text = normalize_text(value)
    # A long field is a requirements paragraph, not a seniority label; letting it
    # through made "lead the team" in the JD mark hundreds of engineers as Lead.
    if text and len(text) <= 60:
        folded = _fold(text)
        if "non-manager" in folded or "non manager" in folded:
            return "Mid"
        level = _match_level(folded)
        if level:
            return level
    title_text = normalize_text(title)
    if title_text:
        level = _match_title_level(_fold(title_text))
        if level:
            return level
    if isinstance(experience_years, (int, float)) and experience_years >= 0:
        if experience_years < 1:
            return "Fresher"
        if experience_years < 2:
            return "Junior"
        if experience_years < 5:
            return "Mid"
        if experience_years < 8:
            return "Senior"
        return "Lead"
    return None


DOMAIN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("An ninh mạng", ("security", "an ninh", "an toan thong tin", "bao mat", "pentest", "soc analyst")),
    ("Dữ liệu & AI", ("data", "du lieu", "machine learning", "artificial intelligence", " ai ", "analytics",
                      "business intelligence", "etl", "warehouse", "nlp", "computer vision", "big data", "mlops")),
    ("Hạ tầng & Cloud", ("devops", "cloud", "aws", "azure", "gcp", "kubernetes", "docker", "infrastructure",
                         "ha tang", "system admin", "sysadmin", "network", "sre", "platform engineer")),
    ("QA & Kiểm thử", ("qa ", "qc ", "tester", "test engineer", "kiem thu", "automation test", "selenium")),
    ("Phân tích & Quản lý", ("business analyst", "phan tich nghiep vu", "product owner", "product manager",
                             "project manager", "quan ly du an", "scrum master")),
    ("Di động & Nhúng", ("android", "ios", "mobile", "flutter", "react native", "embedded", "nhung", "firmware", "iot")),
    ("Phát triển phần mềm", ("developer", "engineer", "lap trinh", "phan mem", "backend", "frontend", "fullstack",
                             "full stack", "software", "web", "java", "python", "php", ".net", "node")),
)
DOMAIN_LABELS = tuple(label for label, _ in DOMAIN_RULES)


def classify_job_domain(*values: Any) -> Optional[str]:
    """Fold industry/function/title text into the eight dashboard domains."""
    folded = _fold(" ".join(str(value) for value in values if value))
    if not folded.strip():
        return None
    padded = f" {folded} "
    for label, needles in DOMAIN_RULES:
        for needle in needles:
            if needle in padded:
                return label
    return None


PLACEHOLDER_COMPANY_PATTERNS = (
    re.compile(r"\{\{.*?\}\}", re.S),
    re.compile(r"\$\{.*?\}", re.S),
    re.compile(r"^\s*(unknown|n/?a|company name|update later|dang cap nhat)\s*$", re.I),
    re.compile(r"công ty đang tuyển dụng|cong ty dang tuyen dung", re.I),
)


def is_placeholder_company(value: Any) -> bool:
    """True when the crawler captured a template or a generic 'hiring' label."""
    text = _text(value)
    if not text:
        return True
    return any(pattern.search(text) for pattern in PLACEHOLDER_COMPANY_PATTERNS)


def clean_company_name(value: Any) -> str:
    raw = normalize_text(value)
    changed = True
    while changed and raw:
        changed = False
        for prefix in LEGAL_PREFIXES:
            if raw.startswith(prefix + " "):
                raw = raw[len(prefix):].strip(" ,.-")
                changed = True
                break
        for suffix in LEGAL_SUFFIXES:
            if raw.endswith(" " + suffix):
                raw = raw[: -len(suffix)].strip(" ,.-")
                changed = True
                break
    return raw


def strip_html(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(_text(value)))
    return re.sub(r"\s+", " ", text).strip()


def make_canonical_url(value: Any) -> Optional[str]:
    raw = _text(value)
    if not raw:
        return None
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        return None
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_PARAMS]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, urlencode(query), ""))


def generate_observation_id(platform: str, source_job_id: str, observed_at: str, content_hash: str) -> str:
    if not all(_text(value) for value in (platform, source_job_id, observed_at, content_hash)):
        raise ValueError("platform, source_job_id, observed_at and content_hash are required")
    raw_key = f"{platform}|{source_job_id}|{observed_at}|{content_hash}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_unified_job_id(identity_seed: str) -> str:
    return "uj_" + hashlib.sha256(identity_seed.encode("utf-8")).hexdigest()


def parse_salary(value: Any, usd_to_vnd: int = 25_400) -> tuple[Optional[int], Optional[int], bool, Optional[str]]:
    raw = normalize_text(value)
    # Sources append a salary-lookup widget to the real text
    # ("25 - 33 triệuXem mức lương thị trường cho vị trí này").
    for marker in ("tra cứu lương", "xem mức lương", "xem chi tiết"):
        raw = raw.split(marker)[0].strip()
    if not raw or any(word in raw for word in ("thỏa thuận", "thoả thuận", "negotiable", "competitive")):
        return None, None, bool(raw), None
    currency = "USD" if "$" in raw or "usd" in raw else "VND"
    numbers = []
    for token in re.findall(r"\d+(?:[.,]\d+)*", raw):
        if "." in token and "," in token:
            token = token.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token) or re.fullmatch(r"\d{1,3}(?:,\d{3})+", token):
            token = token.replace(".", "").replace(",", "")
        else:
            token = token.replace(",", ".")
        numbers.append(float(token))
    if not numbers:
        return None, None, False, currency
    multiplier = 1
    if "triệu" in raw or "trieu" in raw or "million" in raw:
        multiplier = 1_000_000
    elif re.search(r"\d\s*tr\b", raw):
        # VietnamWorks writes the unit as "tr" ("12tr-15tr ₫/tháng", "Tới 5tr").
        # Without this branch 12 means twelve dong, which then poisons every median
        # that includes the row.
        multiplier = 1_000_000
        if "usd" not in raw:
            # The same source masks the currency as "$$ 7tr-9tr"; "tr" already means
            # triệu đồng, so the placeholder must not switch the row to USD.
            currency = "VND"
    elif re.search(r"\d\s*k\b", raw):
        multiplier = 1_000
    values = [int(n * multiplier * (usd_to_vnd if currency == "USD" else 1)) for n in numbers[:2]]
    if any(marker in raw for marker in ("/năm", "mỗi năm", "per year", "annual", "yearly")):
        values = [int(value / 12) for value in values]
    return values[0], values[-1], False, currency


def standardize_skills(values: Iterable[Any] | Any) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,;/|]", values)
    text = " ".join(normalize_text(value) for value in (values or []))
    result = []
    for skill in sorted(SKILL_TAXONOMY, key=len, reverse=True):
        pattern = rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])"
        if skill == "java":
            pattern = r"(?<![a-z0-9])java(?!\s*script)(?![a-z0-9])"
        if re.search(pattern, text, flags=re.IGNORECASE) and skill not in result:
            result.append(skill)
    return result


def _iso_or_none(value: Any) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,;/|]", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def _as_string_scalar(value: Any) -> Optional[str]:
    """Coerce source scalar-or-list fields into the Silver string contract."""
    values = _as_string_list(value)
    return "; ".join(values) if values else None


def build_bronze_metadata(envelope: dict[str, Any]) -> str:
    """Serialize Bronze envelope metadata for row-level Silver lineage.

    Payload values are intentionally excluded.  Silver keeps the normalized
    fields separately and carries only the audit metadata plus the names of
    rescued fields so schema drift remains traceable without duplicating raw
    Bronze data.
    """
    envelope_metadata = envelope.get("_metadata") or {}
    metadata = {
        "bronze_source": envelope_metadata.get("source"),
        "bronze_ingested_at": envelope_metadata.get("ingested_at"),
        "bronze_observed_at": envelope_metadata.get("observed_at"),
        "bronze_payload_checksum": envelope_metadata.get("payload_checksum"),
        "bronze_pipeline_run_id": envelope_metadata.get("pipeline_run_id"),
        "bronze_format_version": envelope_metadata.get("format_version"),
        "bronze_schema_drift_fields": sorted(
            str(key) for key in (envelope.get("_rescued_data") or {})
        ),
    }
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_experience(value: Any) -> tuple[Optional[float], Optional[float]]:
    if isinstance(value, dict):
        # itviec emits schema.org-style {"monthsOfExperience": 37}; reading the raw
        # number as years turned 10 months into "Lead" for ~1.200 engineers.
        months = value.get("monthsOfExperience") or value.get("months")
        years = value.get("yearsOfExperience") or value.get("years")
        if isinstance(months, (int, float)):
            return months / 12.0, months / 12.0
        if isinstance(years, (int, float)):
            return float(years), float(years)
        return None, None
    numbers = [float(token) for token in re.findall(r"\d+(?:\.\d+)?", normalize_text(value))]
    numbers = [number for number in numbers if number <= 50]  # drop calendar years
    if not numbers:
        return None, None
    return numbers[0], numbers[-1]


def canonicalize_bronze_record(envelope: dict[str, Any], *, silver_updated_at: Optional[str] = None) -> dict[str, Any]:
    """Map one verified Bronze envelope into the 46-field Silver observation contract."""
    metadata = envelope.get("_metadata") or {}
    payload = envelope.get("raw_payload") or {}
    platform = _text(payload.get("platform") or metadata.get("source")).casefold()
    source_job_id = _text(payload.get("job_id") or payload.get("source_job_id"))
    # Processing/ingestion time is not a stable observation timestamp.  Falling
    # back to it would make a replay of the same Bronze object produce a new
    # observation_id, so missing source observation time must fail closed.
    observed_at = _text(metadata.get("observed_at") or payload.get("observed_at") or payload.get("scraped_at"))
    content_hash = _text(payload.get("content_hash"))
    if not platform or not source_job_id or not observed_at or not content_hash:
        raise ValueError("Bronze envelope lacks stable platform/source_job_id/observed_at/content_hash")
    metadata_ingested_at = _text(metadata.get("ingested_at") or observed_at)
    all_payload = {**payload, **(envelope.get("_rescued_data") or {})}
    source_url = payload.get("job_url") or payload.get("source_url")
    title = _text(payload.get("job_title"))
    company = _text(payload.get("company_name"))
    if not title or not company:
        raise ValueError("Bronze payload lacks mandatory job_title/company_name")
    salary_min, salary_max, negotiable, currency = parse_salary(payload.get("salary"))
    # vietnamworks ships `job_locations` ("Hanoi / HCMC") and itviec ships `address`;
    # reading only job_location/location/address left 506 VNW jobs with no city.
    city_raw = (
        payload.get("job_location")
        or payload.get("job_locations")
        or payload.get("location")
        or payload.get("address")
        or payload.get("company_location")
    )
    description = strip_html(payload.get("job_description") or payload.get("description") or payload.get("general_description"))
    requirements = strip_html(payload.get("requirements") or payload.get("job_requirements") or payload.get("requirements_raw"))
    benefits = strip_html(payload.get("benefits") or payload.get("what_we_offer") or payload.get("benefits_raw"))
    exp_min, exp_max = _parse_experience(payload.get("experience_requirements") or payload.get("experience") or payload.get("years_of_experience"))
    raw_skills = _as_string_list(payload.get("skills") or payload.get("required_skills") or payload.get("overview_skills"))
    placeholder_company = is_placeholder_company(company)
    company_name = "UNKNOWN" if placeholder_company else clean_company_name(company)
    company_normalized = "" if placeholder_company else clean_company_name(company)
    raw_domain = (
        payload.get("job_domain")
        or payload.get("job_industry")
        or payload.get("industries")
        or payload.get("company_industry")
        or payload.get("company_field")
        or payload.get("job_function")
        or payload.get("industry_knowledge")
    )
    known = {"platform", "job_id", "source_job_id", "job_url", "source_url", "content_hash", "scraped_at", "observed_at", "job_title", "company_name", "salary", "job_location", "location", "address", "job_description", "description", "general_description", "requirements", "job_requirements", "requirements_raw", "benefits", "what_we_offer", "benefits_raw", "skills", "required_skills", "overview_skills", "experience_requirements", "experience", "years_of_experience", "posted_date_iso", "posted_date", "valid_through", "deadline", "expired_on", "quantity", "job_level", "work_mode", "employment_type"}
    posted_date = _iso_or_none(payload.get("posted_date_iso") or payload.get("posted_date"))
    expired_date = _iso_or_none(payload.get("valid_through") or payload.get("deadline") or payload.get("expired_on"))
    is_active = not expired_date or expired_date >= observed_at[:10]
    record = {
        "source_job_id": source_job_id, "observation_id": generate_observation_id(platform, source_job_id, observed_at, content_hash), "platform": platform,
        "source_url": source_url, "canonical_url": make_canonical_url(source_url), "content_hash": content_hash,
        "crawled_at": _text(payload.get("scraped_at") or observed_at), "observed_at": observed_at,
        "ingested_at": metadata_ingested_at, "silver_updated_at": silver_updated_at or observed_at, "is_active": is_active,
        "job_title": clean_display_title(title), "job_title_normalized": normalize_title(title),
        "job_level": classify_job_level(
            payload.get("job_level") or payload.get("experience") or payload.get("experience_requirements"),
            title=title,
            experience_years=exp_min,
        ),
        "job_expertise": _as_string_scalar(payload.get("job_expertise")),
        # job_domain must cover all nine platforms. Only itviec emits `job_domain` and only
        # vietnamworks emits `job_industry`; the five card-style platforms expose `industries`,
        # and the remaining ones only carry `company_industry`. Without this fallback chain the
        # Gold market aggregate would group ~7/9 platforms into UNKNOWN.
        # One dashboard taxonomy instead of the raw LinkedIn-style industry strings,
        # which left 8 of 9 platforms in NULL or in 20 competing English buckets.
        "job_domain": classify_job_domain(raw_domain, title, payload.get("job_expertise"), " ".join(raw_skills)),
        "work_mode": _as_string_scalar(payload.get("work_mode")), "working_time": _as_string_scalar(payload.get("working_time") or payload.get("employment_type")), "overtime_policy": _as_string_scalar(payload.get("overtime_policy")),
        "company_name": company_name, "company_name_raw": company, "company_name_normalized": company_normalized,
        "company_size": _as_string_scalar(payload.get("company_size")),
        "company_industry": _as_string_scalar(payload.get("company_industry")), "company_type": _as_string_scalar(payload.get("company_type") or payload.get("contract_type")), "company_country": _as_string_scalar(payload.get("company_country")),
        "city": _as_string_scalar(city_raw), "city_normalized": normalize_city(city_raw), "full_address": _as_string_scalar(payload.get("work_address") or payload.get("full_address") or city_raw),
        "salary_raw": payload.get("salary"), "salary_min_vnd": salary_min, "salary_max_vnd": salary_max, "is_negotiable": negotiable, "salary_currency": currency,
        "exp_years_min": exp_min, "exp_years_max": exp_max, "vacancies_count": max(1, int(payload.get("quantity") or 1)),
        "posted_date": posted_date, "expired_date": expired_date,
        "skills_raw": raw_skills,
        "skills_standardized": standardize_skills(description + " " + requirements + " " + benefits),
        "job_description_clean": description or None, "description_normalized": normalize_text(description) or None,
        "requirements_clean": requirements or None, "benefits_clean": benefits or None,
        "platform_specific_attributes": build_platform_specific_attributes(
            all_payload, known, bronze_metadata=build_bronze_metadata(envelope)
        ),
    }
    return record


def build_platform_specific_attributes(
    payload: dict[str, Any],
    known_keys: set[str],
    *,
    bronze_metadata: Optional[str] = None,
) -> str:
    attributes: dict[str, Any] = {
        k: v for k, v in payload.items() if k not in known_keys
    }
    if bronze_metadata is not None:
        # Keep the historical platform-specific shape intact and reserve one
        # namespaced key for Bronze lineage metadata.
        attributes["_bronze_metadata"] = json.loads(bronze_metadata)
    return json.dumps(attributes, ensure_ascii=False, sort_keys=True)
