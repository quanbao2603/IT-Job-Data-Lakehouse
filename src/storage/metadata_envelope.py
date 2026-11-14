import json
import hashlib
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set


class MetadataEnvelope:
    """
    Bộ đóng gói siêu dữ liệu kỹ thuật và bảo toàn dữ liệu cho Tầng Bronze (Bronze Immutable Extracted Payload).
    - Đính kèm Audit Metadata (ingested_at UTC, source, run_id).
    - Tính mã băm SHA-256 đối soát toàn vẹn dữ liệu.
    - Kích hoạt cơ chế Schema Drift Protection: Tự động phát hiện trường lạ
      và đưa vào cột _rescued_data dựa trên Hợp đồng Schema (Schema Contracts).
    """

    FORMAT_VERSION = "1.0"

    SCHEMA_CONTRACTS: Dict[str, Set[str]] = {
        "itviec": {
            "platform", "job_id", "job_url", "scraped_at", "content_hash",
            "job_title", "company_name", "company_logo", "salary", "address",
            "work_mode", "posted_date", "posted_date_iso", "valid_through",
            "employment_type", "experience_requirements", "skills", "job_expertise",
            "job_domain", "job_description", "requirements", "benefits",
            "company_overview", "company_type", "company_industry", "company_size",
            "company_country", "working_days", "overtime_policy"
        },
        "topcv": {
            "platform", "job_id", "job_url", "scraped_at", "content_hash",
            "company_name", "job_title", "salary", "job_location", "location",
            "experience", "deadline", "job_level", "education", "quantity",
            "work_mode", "contract_type", "overview_requirements", "overview_skills",
            "job_description_raw", "requirements_raw", "industry_knowledge",
            "required_skills", "benefits_raw", "work_address", "work_time",
            "company_size", "company_address", "company_field", "company_industry"
        },
        "topdev": {
            "platform", "job_id", "job_url", "job_title", "company_name",
            "salary", "job_location", "job_level", "experience", "skills",
            "expires_in", "num_candidates", "general_description", "responsibilities",
            "requirements", "benefits", "company_industry", "company_size",
            "company_country", "company_job_openings", "content_hash", "scraped_at"
        },
        "vietnamworks": {
            "platform", "job_id", "job_url", "scraped_at", "content_hash",
            "company_name", "company_profile", "company_logo", "job_title",
            "salary", "expired_on", "expires_in_days", "views_count",
            "company_location", "posted_time_ago", "applicant_count",
            "job_description", "job_requirements", "what_we_offer", "posted_date",
            "job_level", "job_function", "skills", "job_industry",
            "preferred_language", "years_of_experience", "nationality",
            "job_locations", "user_tags"
        },
        "careerviet": {
            "platform", "job_id", "job_url", "scraped_at", "content_hash",
            "job_title", "company_name", "company_id", "company_url", "company_logo",
            "salary", "job_location", "job_location_structured", "deadline",
            "deadline_source", "updated_date", "industries", "card_meta_raw",
            "description", "requirements", "benefits", "job_info_raw",
            "posted_date_iso", "valid_through", "employment_type"
        },
        "upzi": {
            "platform", "job_id", "job_url", "scraped_at", "content_hash",
            "job_title", "company_name", "company_id", "company_url", "company_logo",
            "salary", "job_location", "job_location_structured", "deadline",
            "deadline_source", "updated_date", "industries", "card_meta_raw",
            "description", "requirements", "benefits", "job_info_raw",
            "posted_date_iso", "valid_through", "employment_type"
        },
        "vietnamdevs": {
            "platform", "job_id", "job_url", "scraped_at", "content_hash",
            "job_title", "company_name", "company_id", "company_url", "company_logo",
            "salary", "job_location", "job_location_structured", "deadline",
            "deadline_source", "updated_date", "industries", "card_meta_raw",
            "description", "requirements", "benefits", "job_info_raw",
            "posted_date_iso", "valid_through", "employment_type"
        }
    }

    @staticmethod
    def _compute_sha256(data: Any) -> str:
        if isinstance(data, (dict, list)):
            canonical_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
        else:
            canonical_str = str(data)
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    @classmethod
    def wrap(
        cls,
        source: str,
        raw_payload: Any,
        run_id: Optional[str] = None,
        expected_keys: Optional[Set[str]] = None
    ) -> Dict[str, Any]:
        source_clean = source.lower().strip()
        now_utc = datetime.utcnow().isoformat() + "Z"
        current_run_id = run_id or f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        checksum = cls._compute_sha256(raw_payload)

        active_expected_keys = expected_keys
        if active_expected_keys is None:
            active_expected_keys = cls.SCHEMA_CONTRACTS.get(source_clean)

        rescued_data = {}
        cleaned_payload = raw_payload

        if isinstance(raw_payload, dict) and active_expected_keys is not None:
            cleaned_payload = {}
            for k, v in raw_payload.items():
                if k in active_expected_keys:
                    cleaned_payload[k] = v
                else:
                    rescued_data[k] = v

        return {
            "_metadata": {
                "ingested_at": now_utc,
                "source": source_clean,
                "payload_checksum": checksum,
                "pipeline_run_id": current_run_id,
                "format_version": cls.FORMAT_VERSION
            },
            "raw_payload": cleaned_payload,
            "_rescued_data": rescued_data
        }

    @classmethod
    def verify_integrity(cls, enveloped_record: Dict[str, Any]) -> bool:
        try:
            stored_checksum = enveloped_record["_metadata"]["payload_checksum"]
            payload = enveloped_record["raw_payload"]
            if enveloped_record.get("_rescued_data"):
                merged = {**payload, **enveloped_record["_rescued_data"]}
                computed = cls._compute_sha256(merged)
            else:
                computed = cls._compute_sha256(payload)
            return stored_checksum == computed
        except Exception:
            return False

    @staticmethod
    def to_jsonl(records: List[Dict[str, Any]]) -> str:
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
