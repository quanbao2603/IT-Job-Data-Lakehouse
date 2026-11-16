"""Compatibility export for the Silver quarantine contract."""
from .silver_schema import QUARANTINE_SCHEMA, SILVER_OBSERVATIONS_SCHEMA

SILVER_QUARANTINE_SCHEMA = QUARANTINE_SCHEMA

__all__ = ["SILVER_OBSERVATIONS_SCHEMA", "SILVER_QUARANTINE_SCHEMA"]
