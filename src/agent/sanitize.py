"""Last line of defence for the public chat answer.

The prompt asks the model not to talk about tables, SQL or the model itself, but a prompt
is a request, not a guarantee. This module strips anything technical that still leaks
through, so no schema name, query text or provider string can reach the browser — including
answers replayed from browser history written by an older build.

It only ever *removes* technical fragments. Business lines (numbers, labels, units) are
kept verbatim; a filter that silently shortened an answer would be worse than the leak.
"""
from __future__ import annotations

import re

# Whole lines that are pure provenance / footer noise.
TECH_LINE_PATTERNS = (
    re.compile(r"^\s*nguồn\s*:", re.I),
    re.compile(r"^\s*source\s*:", re.I),
    re.compile(r"^\s*(?:truy vấn|query|câu lệnh)\s*(?:đã chạy|đã dùng|sql)?\s*:", re.I),
    re.compile(r"^\s*sql\s*(?:đã chạy|đã dùng)?\s*[:]?\s*$", re.I),
    re.compile(r"^\s*(?:model|provider|mô hình)\s*[:：]", re.I),
)

# Fragments inside a line: schema-qualified relations, bare gold.* names, fenced code.
TECH_FRAGMENT_PATTERNS = (
    (re.compile(r"```[a-zA-Z]*"), ""),
    (re.compile(r"`(?:select|with)\b[^`]*`", re.I), ""),
    (re.compile(r"\bgold\.(?:v_|dim_|fact_)[a-z0-9_]+", re.I), ""),
    (re.compile(r"\b(?:v_job_snapshot|v_dim_job_current|v_job_skill|v_job_source_url|"
                r"dim_company|dim_skill|dim_job|fact_job_[a-z_]+)\b", re.I), ""),
    (re.compile(r"\b(?:openai|anthropic|gemini|google|deepseek|chib)\b\s*[·:/]\s*[a-z0-9.\-]+", re.I), ""),
    (re.compile(r"\b(?:openai|anthropic|gemini|deepseek|chib)\b", re.I), ""),
    (re.compile(r"\bsalary_sample_flag\b", re.I), "dữ liệu lương hợp lệ"),
    (re.compile(r"\blast_seen_at\b", re.I), "lần ghi nhận gần nhất"),
    (re.compile(r"\bsnapshot_cutoff_at\b", re.I), "thời điểm chốt dữ liệu"),
    (re.compile(r"\b(?:gold|lakehouse|bronze|silver)\b", re.I), ""),
)

MULTI_SPACE = re.compile(r"[ \t]{2,}")
DOUBLE_PUNCT = re.compile(r"\s+([,.;:])")


def scrub_answer(text: str) -> str:
    """Remove technical leakage from an answer while keeping every business figure."""
    if not text:
        return ""
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        # A fenced block is code, never business content: drop it whole.
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if any(pattern.match(stripped) for pattern in TECH_LINE_PATTERNS):
            continue
        for pattern, replacement in TECH_FRAGMENT_PATTERNS:
            stripped = pattern.sub(replacement, stripped)
        if not stripped.strip(" -•*\t"):
            continue
        cleaned = MULTI_SPACE.sub(" ", stripped)
        cleaned = DOUBLE_PUNCT.sub(r"\1", cleaned).strip()
        kept.append(cleaned)
    # Collapse the blank lines left behind by removed footer blocks.
    out: list[str] = []
    for line in kept:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).strip()


def has_technical_leak(text: str) -> bool:
    """True when a payload still contains SQL, schema or provider markers."""
    if not text:
        return False
    lowered = text.lower()
    markers = ("gold.", "v_job_snapshot", "dim_company", "select ", "```sql", "openai", "deepseek",
               "chib", "salary_sample_flag", "nguồn:")
    return any(marker in lowered for marker in markers)
