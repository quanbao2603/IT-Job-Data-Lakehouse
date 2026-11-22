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
    company_key: str | None = Field(default=None, max_length=200)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=12, ge=1, le=50)
    sort: SortValue = "recent"
    expected_release_id: str | None = Field(default=None, max_length=300)

    @field_validator("q", "city", "domain", "level", "skill", "company_key", mode="before")
    @classmethod
    def blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


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
