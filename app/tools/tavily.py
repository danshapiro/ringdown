"""Tavily Search & Extract tools.

Makes live HTTP calls to Tavily APIs using the API key from environment
variable `TAVILY_API_KEY` (loaded via `EnvSettings`).

Both tools are registered with ``app.tool_framework`` at import time.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import requests
from pydantic import BaseModel, Field, field_validator

from ..tool_framework import register_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BASE_URL = "https://api.tavily.com"


def _auth_headers() -> Dict[str, str]:
    """Return Tavily auth header (uses x-api-key)."""

    from ..settings import get_env

    key = get_env().tavily_api_key  # type: ignore[attr-defined]
    if not key:
        raise RuntimeError("TAVILY_API_KEY missing in environment")
    # Tavily switched to bearer-token authentication in March 2025. Older
    # `x-api-key` headers now return HTTP 401. Use the current scheme so our
    # tools continue to work without requiring config changes.
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


class _TimeoutModel(BaseModel):
    timeout: int | None = Field(60, description="Request timeout seconds")


# ---------------------------------------------------------------------------
# Search
# We're using a subset of all arguments because we want simpler models to succeed.
# ---------------------------------------------------------------------------

class SearchArgs(_TimeoutModel):
    query: str
    # topic: str | None = Field(None, description="'general' or 'news'")
    # search_depth: str | None = Field(None, description="'basic' or 'advanced'")
    max_results: int | None = Field(None, ge=0, le=20)
    # chunks_per_source: int | None = Field(None, ge=1, le=3)
    days: int | None = Field(None, gt=0)
    # time_range: str | None = None
    # include_answer: bool | None = None
    # include_raw_content: bool | None = None
    # include_images: bool | None = None
    # include_image_descriptions: bool | None = None
    include_domains: List[str] | None = None
    # exclude_domains: List[str] | None = None
    # country: str | None = None


@register_tool(
    name="TavilySearch", 
    description="Search the web via Tavily", 
    param_model=SearchArgs,
    prompt="""## tavily_search
Search the web. Always cite results as "from [website]:" or "[website] says".

**After use:** If you've thoroughly answered the question, reply. If you think the pages have additional information, use tavily_extract. If you think there are other pages with more information, use tavily_search again.

**Available parameters:**
- `query` (required): Search query string
- `max_results` (optional): Number of results to return (0-20)
- `days` (optional): Limit results to past N days. Perfect when the user wants recent content
- `include_domains` (optional): Search only these domains. Use this generously! If you know what domain has the answer, or if you're refining your search, this is invaluable."""
)
def tavily_search(args: SearchArgs) -> Dict[str, Any]:
    url = f"{_BASE_URL}/search"
    payload = args.model_dump(exclude_none=True)
    # Always set include_answer=False (not exposed to LLM)
    payload["include_answer"] = False
    # Hardcoded values - only set non-None values
    payload["topic"] = "general"
    payload["search_depth"] = "advanced"
    payload["chunks_per_source"] = 1
    logger.debug("TavilySearch payload=%s", payload)
    resp = requests.post(url, headers=_auth_headers(), json=payload, timeout=args.timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"TavilySearch HTTP {resp.status_code}: {resp.text}")
    return resp.json()


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

class ExtractArgs(_TimeoutModel):
    urls: List[str]
    # extract_depth: str | None = Field(None, description="'basic' or 'advanced'")
    # include_images: bool | None = None
    # format: str | None = Field(None, description="'markdown' or 'text'")

    @field_validator("urls", mode="before")
    @classmethod
    def _normalize_and_limit(cls, v):  # noqa: D401
        # Accept a single string or list; always return list[str]
        if isinstance(v, str):
            v = [v]
        if len(v) > 20:
            raise ValueError("Maximum 20 URLs allowed")
        return v


@register_tool(
    name="TavilyExtract", 
    description="Extract content from URLs via Tavily", 
    param_model=ExtractArgs,
    prompt="""## tavily_extract
Extract full content from specific URLs using Tavily's extraction API.

**After use:** If you've thoroughly answered the question, reply. If you think there are other pages with more information, use tavily_search.

**Available parameters:**
- `urls` (required): Single URL string or list of URLs (max 20)

**Use cases:** Reading articles verbatim, extracting structured content, getting full page text."""
)
def tavily_extract(args: ExtractArgs) -> Dict[str, Any]:
    url = f"{_BASE_URL}/extract"
    payload = args.model_dump(exclude_none=True)
    # Hardcoded values - only set non-None values
    payload["extract_depth"] = "advanced"
    payload["format"] = "markdown"
    logger.debug("TavilyExtract payload=%s", payload)
    resp = requests.post(url, headers=_auth_headers(), json=payload, timeout=args.timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"TavilyExtract HTTP {resp.status_code}: {resp.text}")
    return resp.json() 
