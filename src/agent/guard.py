"""SQL guard: the only thing standing between an LLM and the Gold database.

An LLM wrote the SQL, so it is treated as hostile input. The guard is deliberately
paranoid and purely syntactic (no network, no database): it strips literals and comments,
requires a single read-only statement, refuses anything that could write or escalate,
checks every referenced relation against a whitelist, and injects a row cap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "merge", "upsert", "truncate", "drop", "alter",
    "create", "grant", "revoke", "copy", "vacuum", "analyze", "reindex", "cluster",
    "refresh", "comment", "call", "do", "set", "reset", "show", "listen", "notify",
    "unlisten", "lock", "prepare", "execute", "deallocate", "into", "commit",
    "rollback", "savepoint", "begin", "start", "explain", "pg_sleep", "pg_read_file",
    "pg_ls_dir", "dblink", "lo_import", "lo_export", "current_setting", "set_config",
)

# Objects the agent may never touch even if they existed in the reader's search path.
FORBIDDEN_SCHEMAS = ("pg_catalog", "information_schema", "gold_control", "gold_staging", "public")

_IDENT = r"[a-zA-Z_][a-zA-Z0-9_]*"


class GuardError(ValueError):
    """Raised when SQL is not a safe single read-only query."""


@dataclass
class GuardResult:
    sql: str
    relations: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)


def _strip_literals_and_comments(sql: str) -> str:
    """Blank out strings, dollar-quoted bodies and comments so keywords cannot hide."""
    output: list[str] = []
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char == "'":
            index += 1
            while index < length:
                if sql[index] == "'" and (index + 1 >= length or sql[index + 1] != "'"):
                    index += 1
                    break
                index += 1
            output.append(" '' ")
            continue
        if char == '"':
            index += 1
            while index < length and sql[index] != '"':
                index += 1
            index += 1
            output.append(" ident ")
            continue
        if sql.startswith("--", index):
            while index < length and sql[index] != "\n":
                index += 1
            output.append(" ")
            continue
        if sql.startswith("/*", index):
            index += 2
            while index < length and not sql.startswith("*/", index):
                index += 1
            index += 2
            output.append(" ")
            continue
        if sql.startswith("$$", index):
            closing = sql.find("$$", index + 2)
            index = length if closing == -1 else closing + 2
            output.append(" '' ")
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _cte_names(stripped: str) -> set[str]:
    return {match.group(1).lower() for match in re.finditer(rf"(?:\bwith|,)\s*({_IDENT})\s+as\s*\(", stripped, re.I)}


def _relations(stripped: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(rf"\b(?:from|join)\s+({_IDENT}(?:\.{_IDENT})?)", stripped, re.I):
        found.append(match.group(1))
    return found


def validate(sql: str, *, allowed_relations: tuple[str, ...], max_rows: int) -> GuardResult:
    """Return a safe, row-capped statement or raise GuardError."""
    if not sql or not sql.strip():
        raise GuardError("Câu lệnh SQL rỗng.")
    cleaned = sql.strip()
    stripped = _strip_literals_and_comments(cleaned)

    statements = [part.strip() for part in stripped.split(";")]
    non_empty = [part for part in statements if part]
    if len(non_empty) != 1:
        raise GuardError("Chỉ được chạy một câu lệnh duy nhất.")

    lowered = stripped.lower().strip()
    if not re.match(r"^(select|with)\b", lowered):
        raise GuardError("Chỉ cho phép câu lệnh SELECT.")

    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"(?<![a-z0-9_]){keyword}(?![a-z0-9_])", lowered):
            raise GuardError(f"Từ khoá bị chặn: {keyword}.")

    for schema in FORBIDDEN_SCHEMAS:
        if re.search(rf"(?<![a-z0-9_]){schema}\s*\.", lowered):
            raise GuardError(f"Không được truy cập schema {schema}.")

    ctes = _cte_names(stripped)
    relations = _relations(stripped)
    allowed = {relation.lower() for relation in allowed_relations}
    for relation in relations:
        name = relation.lower()
        if name in ctes:
            continue
        # A bare table name without schema resolves through search_path; require schema.
        if "." not in name:
            raise GuardError(f"Phải ghi rõ schema cho bảng: {relation}.")
        if name not in allowed:
            raise GuardError(f"Bảng/view không được phép: {relation}.")

    warnings: list[str] = []
    limit_match = re.search(r"\blimit\s+(\d+)", lowered)
    if limit_match:
        requested = int(limit_match.group(1))
        if requested > max_rows:
            cleaned = re.sub(
                r"\blimit\s+\d+", f"LIMIT {max_rows}", cleaned, count=1, flags=re.I
            )
            warnings.append(f"LIMIT {requested} vượt giới hạn, đã hạ xuống {max_rows}.")
    else:
        cleaned = f"{cleaned.rstrip().rstrip(';')}\nLIMIT {max_rows}"
        warnings.append(f"Đã thêm LIMIT {max_rows}.")

    return GuardResult(sql=cleaned, relations=tuple(relations), warnings=warnings)
