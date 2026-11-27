from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


SortValue = Literal["recent", "salary", "title"]


class FilterParams(BaseModel):
    q: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    domain: str | None = Field(default=None, max_length=200)
    level: str | None = Field(default=None, max_length=100)
    skill: str | None = Field(default=None, max_length=120)
    # Multi-select arrives as one comma-separated value (`?skills=python,aws`) because a Pydantic
    # model read through Depends() turns `list[str]` into a single "<factory>" placeholder.
    skills: str | None = Field(default=None, max_length=600)
    company_key: str | None = Field(default=None, max_length=200)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=12, ge=1, le=50)
    sort: SortValue = "recent"
    scope: Literal["all", "recent"] = "all"
    expected_release_id: str | None = Field(default=None, max_length=300)


    @field_validator("q", "city", "domain", "level", "skill", "company_key", mode="before")
    @classmethod
    def blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def skill_codes(self) -> list[str]:
        """Selected skills, deduplicated, in the order the caller sent them."""
        raw = self.skills or self.skill or ""
        seen: list[str] = []
        for part in str(raw).split(","):
            code = part.strip()
            if code and code not in seen:
                seen.append(code)
        return seen


class ReleaseMeta(BaseModel):
    gold_release_id: str
    silver_release_id: str | None
    snapshot_date: str
    snapshot_cutoff_at: str
    switched_at: str | None
    served_at: str
    mode: Literal["live", "demo"]
    age_days: int
    stale_after_days: int
    stale: bool
    scope: str = "nguồn cung tin hệ thống quan sát được"
    salary_definition: str = "Trung điểm khoảng lương VND trên các tin có đủ min/max hợp lệ"


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody

class AskBody(BaseModel):
    """One question for the Text-to-SQL agent, with the last few chat turns."""

    question: str = Field(min_length=1, max_length=500)
    history: list[dict[str, str]] = Field(default_factory=list)

    @field_validator("question", mode="before")
    @classmethod
    def strip_question(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

