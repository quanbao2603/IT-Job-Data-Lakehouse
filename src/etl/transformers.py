"""Small, deterministic Bronze-to-Silver normalization helpers."""
from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"ref", "source", "fbclid", "search_id"}
LEGAL_PREFIXES = ("công ty tnhh", "ctcp", "công ty cổ phần", "cong ty tnhh", "cong ty co phan")
LEGAL_SUFFIXES = ("co., ltd", "co ltd", "jsc", "inc.", "inc")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).casefold()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


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
        return raw
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_PARAMS]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, urlencode(query), ""))


def generate_observation_id(platform: str, source_job_id: str, observed_at: str, content_hash: str) -> str:
    raw_key = f"{platform}|{source_job_id}|{observed_at}|{content_hash}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_unified_job_id(identity_seed: str) -> str:
    return "uj_" + hashlib.sha256(identity_seed.encode("utf-8")).hexdigest()


def parse_salary(value: Any, usd_to_vnd: int = 25_400) -> tuple[Optional[int], Optional[int], bool, Optional[str]]:
    raw = normalize_text(value)
    if not raw or any(word in raw for word in ("thỏa thuận", "thoả thuận", "negotiable", "competitive")):
        return None, None, bool(raw), None
    currency = "USD" if "$" in raw or "usd" in raw else "VND"
    numbers = [float(n.replace(",", "").replace(".", "")) for n in re.findall(r"\d+(?:[.,]\d+)*", raw)]
    if not numbers:
        return None, None, False, currency
    multiplier = 1
    if "triệu" in raw or "trieu" in raw or "million" in raw:
        multiplier = 1_000_000
    elif "k" in raw:
        multiplier = 1_000
    values = [int(n * multiplier * (usd_to_vnd if currency == "USD" else 1)) for n in numbers[:2]]
    return values[0], values[-1], False, currency


def standardize_skills(values: Iterable[Any] | Any) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,;/|]", values)
    result = []
    for value in values or []:
        skill = normalize_text(value)
        if skill and skill not in result:
            result.append(skill)
    return result


def build_platform_specific_attributes(payload: dict[str, Any], known_keys: set[str]) -> str:
    return json.dumps({k: v for k, v in payload.items() if k not in known_keys}, ensure_ascii=False, sort_keys=True)
