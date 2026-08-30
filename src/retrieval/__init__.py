"""Stable retrieval interfaces; callers do not need to know the DB schema."""

from .default_sections import DEFAULT_RETRIEVAL_V1, RetrievalV1Config, search_default_sections

__all__ = ["DEFAULT_RETRIEVAL_V1", "RetrievalV1Config", "search_default_sections"]
