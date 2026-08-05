"""Pydantic response and request models for the Search Service API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SearchHitModel(BaseModel):
    """A single search result."""

    id: str
    title: str
    content_snippet: str
    type: str
    owner_id: str
    tags: list[str] = []
    score: float = 0.0
    highlights: dict[str, list[str]] = {}
    created_at: str | None = None
    updated_at: str | None = None
    mime_type: str | None = None
    folder_id: str | None = None
    size: int | None = None


class SearchResponseModel(BaseModel):
    """Paginated search response."""

    results: list[SearchHitModel]
    total: int
    page: int
    page_size: int
    query: str


class SuggestResponseModel(BaseModel):
    """Autocomplete suggestion response."""

    suggestions: list[Any]
    query: str


class AnalyticsResponseModel(BaseModel):
    """Search analytics response."""

    popular_queries: list[dict[str, Any]]
    zero_result_queries: list[dict[str, Any]]
    total_searches: int
    avg_results_per_query: float


class IndexResponseModel(BaseModel):
    """Index operation response."""

    status: str
    id: str
    type: str


class HealthResponseModel(BaseModel):
    """Liveness response."""

    status: str
    service: str


class ReadinessResponseModel(BaseModel):
    """Readiness response."""

    ready: bool
