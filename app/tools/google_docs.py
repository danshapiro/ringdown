"""Google Docs creation and editing tools.

Available tools:
- CreateGoogleDoc: Create a new Google Doc from markdown content.
- SearchGoogleDrive: Search Google Drive by title or content.
- ReadGoogleDoc: Read the content of a Google Doc or Markdown file.
- AppendGoogleDoc: Append content to a Google Doc in the default folder.

Authentication uses the same delegated service-account credential as the Gmail tool.
The service account impersonates the user
to create and edit documents in their Google Drive.

Required env vars (same as Gmail):
- GMAIL_SA_KEY_PATH: Path to service account JSON key file
- GMAIL_IMPERSONATE_EMAIL: Email address to impersonate (optional)
"""

from __future__ import annotations

import io
import logging
import os
import re
import threading
import time
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from pydantic import BaseModel, Field, field_validator

from ..tool_framework import register_tool
from .email import EmailArgs, send_email

logger = logging.getLogger(__name__)

# Thread-local storage for agent context
_agent_context = threading.local()

# Google API scopes needed for Docs and Drive operations
# - documents: Full access to Google Docs for create/read/edit operations
# - drive.file: Create and manage files created by the app (for CreateGoogleDoc, AppendGoogleDoc)
# - drive.readonly: Read any file in Drive (for ReadGoogleDoc, SearchGoogleDrive)
SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Known MIME types for Markdown files stored in Google Drive
MARKDOWN_MIME_TYPES = {"text/markdown", "text/x-markdown"}


def _iter_text_runs(doc: dict[str, Any]):
    """Yield textRun entries from a Docs API document response."""
    for element in doc.get("body", {}).get("content", []):
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        for elem in paragraph.get("elements", []):
            text_run = elem.get("textRun")
            if text_run:
                yield text_run


def _collect_plain_text(doc: dict[str, Any]) -> str:
    """Return concatenated text content for a Docs API response."""
    return "".join(run.get("content", "") for run in _iter_text_runs(doc))


def _format_run_entry(text_run: dict[str, Any]) -> dict[str, Any]:
    """Build a formatted-content entry (text + optional style) for a text run."""
    entry: dict[str, Any] = {"text": text_run.get("content", "")}
    style = text_run.get("textStyle", {})
    if style:
        entry["style"] = style
    return entry


# Default size of the content window returned by ReadGoogleDoc, in characters.
# Comfortably under the framework's 200k response cap so a window is never
# silently truncated, while staying small enough to be context-friendly.
DEFAULT_READ_WINDOW = 50000

# Characters of surrounding context returned on each side of a `find` match.
FIND_CONTEXT_CHARS = 1000

# Maximum number of `find` matches returned in a single ReadGoogleDoc call.
FIND_MAX_MATCHES = 20


def _normalize_offset(offset: int, total: int) -> int:
    """Clamp an offset into [0, total]. Negative offsets count from the end."""
    if offset < 0:
        return max(0, total + offset)
    return min(offset, total)


def _window_text(text: str, offset: int, max_chars: int) -> dict[str, Any]:
    """Return a character window of ``text`` plus navigation metadata."""
    total = len(text)
    start = _normalize_offset(offset, total)
    end = min(start + max_chars, total)
    chunk = text[start:end]
    has_more = end < total
    return {
        "content": chunk,
        "total_chars": total,
        "offset": start,
        "returned_chars": len(chunk),
        "has_more": has_more,
        "next_offset": end if has_more else None,
    }


def _window_runs(
    runs: list[tuple[str, dict[str, Any]]], start: int, end: int
) -> list[dict[str, Any]]:
    """Return formatted run entries overlapping the character span [start, end)."""
    selected: list[dict[str, Any]] = []
    pos = 0
    for text, entry in runs:
        run_start = pos
        run_end = pos + len(text)
        pos = run_end
        if run_end <= start:
            continue
        if run_start >= end:
            break
        clip_start = max(start, run_start) - run_start
        clip_end = min(end, run_end) - run_start
        clipped = dict(entry)
        clipped["text"] = text[clip_start:clip_end]
        selected.append(clipped)
    return selected


def _find_in_text(text: str, needle: str, *, max_chars: int) -> dict[str, Any]:
    """Return context windows around each (case-insensitive) match of ``needle``."""
    total = len(text)
    haystack = text.lower()
    target = needle.lower()
    matches: list[dict[str, Any]] = []
    search_from = 0
    while len(matches) < FIND_MAX_MATCHES:
        idx = haystack.find(target, search_from)
        if idx == -1:
            break
        win_start = max(0, idx - FIND_CONTEXT_CHARS)
        win_end = min(total, idx + len(needle) + FIND_CONTEXT_CHARS)
        snippet = text[win_start:win_end]
        if max_chars and len(snippet) > max_chars:
            snippet = snippet[:max_chars]
            win_end = win_start + max_chars
        matches.append(
            {
                "match_offset": idx,
                "window_start": win_start,
                "window_end": win_end,
                "snippet": snippet,
            }
        )
        search_from = idx + len(needle)

    more = haystack.find(target, search_from) != -1 if matches else False
    return {
        "total_chars": total,
        "find": needle,
        "match_count": len(matches),
        "matches": matches,
        "more_matches": more,
    }


def set_agent_context(agent_config: dict[str, Any] | None) -> None:
    """Set the current agent configuration in thread-local storage."""
    logger.debug(f"Google Docs: set_agent_context called with config: {agent_config}")
    _agent_context.config = agent_config


def get_agent_context() -> dict[str, Any] | None:
    """Get the current agent configuration from thread-local storage."""
    ctx = getattr(_agent_context, "config", None)
    logger.debug(f"Google Docs: get_agent_context returning: {ctx}")
    return ctx


def _get_allowed_folders() -> list[str]:
    """Get allowed folders for the current agent."""
    agent_config = get_agent_context()
    logger.debug(f"Google Docs: _get_allowed_folders called, agent_config: {agent_config}")

    if not agent_config:
        logger.error("Google Docs: No agent context available in _get_allowed_folders")
        raise ValueError("Agent context is required for Google Docs folder access")

    # Check for docs_folder_greenlist in agent config
    folder_list = agent_config.get("docs_folder_greenlist")
    if folder_list:
        logger.debug(f"Google Docs: Using custom folder greenlist: {folder_list}")
        return folder_list

    # Generate default folder list based on agent's bot_name
    bot_name = agent_config.get("bot_name")
    if not bot_name:
        logger.error("Google Docs: No bot_name in agent config")
        raise ValueError("Agent configuration must include 'bot_name' for Google Docs access")

    default_folders = [
        f"{bot_name}-default",  # Dynamic folder name based on bot_name
    ]
    logger.debug(f"Google Docs: Using default folders for bot_name '{bot_name}': {default_folders}")
    return default_folders


def _is_folder_allowed(folder_name: str) -> bool:
    """Check if folder name matches any greenlist pattern for the current agent."""
    allowed = _get_allowed_folders()

    for candidate in allowed:
        if _is_regex_pattern(candidate):
            try:
                if re.fullmatch(candidate, folder_name, re.IGNORECASE):
                    return True
            except re.error as exc:  # pragma: no cover - configuration error surfaced at runtime
                logger.error("Google Docs: Invalid folder regex '%s': %s", candidate, exc)
                continue
        elif candidate.lower() == folder_name.lower():  # Exact match
            return True
    return False


def _get_services() -> tuple[Any, Any]:
    """Get authenticated Google Docs and Drive services.

    Returns:
        Tuple of (docs_service, drive_service)
    """
    key_path = os.getenv("GMAIL_SA_KEY_PATH")
    from app.settings import get_default_email as _get_default_email

    impersonate = os.getenv("GMAIL_IMPERSONATE_EMAIL", _get_default_email())

    if not key_path:
        raise ValueError("GMAIL_SA_KEY_PATH environment variable is required")

    if not os.path.exists(key_path):
        raise FileNotFoundError(f"GMAIL_SA_KEY_PATH points to missing file: {key_path}")

    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=SCOPES
    ).with_subject(impersonate)

    docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    return docs_service, drive_service


def _is_regex_pattern(candidate: str) -> bool:
    """Return True when the folder entry should be treated as a regex pattern."""
    if candidate.startswith("^") or candidate.endswith("$"):
        return True
    return any(char in candidate for char in "*?[]{}()|\\")


def _find_folder_by_pattern(pattern: str, drive_service: Any) -> tuple[str | None, str | None]:
    """Locate an existing folder whose name matches the supplied regex pattern."""
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:  # pragma: no cover - configuration error surfaced at runtime
        raise ValueError(f"Invalid folder regex '{pattern}': {exc}") from exc

    page_token: str | None = None
    while True:
        response = (
            drive_service.files()
            .list(
                q="mimeType='application/vnd.google-apps.folder' and trashed=false",
                spaces="drive",
                fields="nextPageToken, files(id, name)",
                pageToken=page_token,
                pageSize=100,
            )
            .execute()
        )

        for folder in response.get("files", []):
            name = folder.get("name", "")
            if compiled.fullmatch(name):
                return folder.get("id"), name

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return None, None


def _find_or_create_folder(folder_name: str, drive_service: Any | None = None) -> str:
    """Find a folder by name or create it if it doesn't exist.

    Returns:
        Folder ID
    """
    if drive_service is None:
        _, drive_service = _get_services()

    # Search for existing folder
    escaped_name = _escape_drive_query_term(folder_name)
    query = (
        f"name='{escaped_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    results = (
        drive_service.files()
        .list(q=query, spaces="drive", fields="files(id, name)", pageSize=1)
        .execute()
    )

    files = results.get("files", [])
    if files:
        return files[0]["id"]

    # Create new folder
    file_metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    folder = drive_service.files().create(body=file_metadata, fields="id").execute()
    logger.info(f"Created new folder '{folder_name}' with ID: {folder['id']}")
    return folder["id"]


def _resolve_allowed_folder(candidate: str, drive_service: Any) -> tuple[str | None, str | None]:
    """Resolve a greenlisted entry to a specific Drive folder."""
    if _is_regex_pattern(candidate):
        folder_id, resolved_name = _find_folder_by_pattern(candidate, drive_service)
        if folder_id and resolved_name:
            logger.debug(
                "Google Docs: Resolved regex folder '%s' to existing folder '%s'",
                candidate,
                resolved_name,
            )
            return folder_id, resolved_name
        raise ValueError(f"No folder found matching regex pattern '{candidate}'")

    folder_id = _find_or_create_folder(candidate, drive_service=drive_service)
    return folder_id, candidate


def _extract_doc_id(doc_input: str) -> str:
    """Extract document ID from various input formats.

    Accepts:
    - Direct document ID
    - Full Google Docs URL
    - Docs sharing URL

    Returns:
        Document ID
    """
    # If it's already just an ID (alphanumeric and underscore/hyphen)
    if re.match(r"^[a-zA-Z0-9_-]+$", doc_input):
        return doc_input

    # Extract from various URL formats
    patterns = [
        r"docs\.google\.com/document/d/([a-zA-Z0-9_-]+)",
        r"docs\.google\.com/.*[?&]id=([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/.*[?&]id=([a-zA-Z0-9_-]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, doc_input)
        if match:
            return match.group(1)

    raise ValueError(f"Could not extract document ID from: {doc_input}")


def _escape_drive_query_term(term: str) -> str:
    """Escape characters for inclusion in Drive query strings."""
    return term.replace("\\", "\\\\").replace("'", "\\'")


def _is_document_in_default_folder(doc_id: str) -> bool:
    """Check if a document lives in a greenlisted folder for the current agent."""
    _, drive_service = _get_services()

    allowed_folders = _get_allowed_folders()

    doc_info = drive_service.files().get(fileId=doc_id, fields="parents").execute()

    parent_ids = doc_info.get("parents", [])

    for parent_id in parent_ids:
        folder_info = drive_service.files().get(fileId=parent_id, fields="name").execute()

        folder_name = folder_info.get("name", "")
        if _is_folder_allowed(folder_name):
            return True

    logger.debug(
        "Google Docs: Document '%s' is not in any allowed folder; parents=%s allowed=%s",
        doc_id,
        parent_ids,
        allowed_folders,
    )
    return False


def _notify_doc_created(
    *,
    doc_id: str,
    title: str,
    docs_service: Any,
    fallback_content: str,
) -> None:
    """Email the account owner with the document link and content."""

    try:
        from app.settings import get_default_email as _get_default_email_for_notify

        recipient = _get_default_email_for_notify()
    except Exception as exc:  # pragma: no cover - configuration error surfaced at runtime
        logger.error(
            "Google Docs: Unable to resolve notification recipient for document %s: %s",
            doc_id,
            exc,
        )
        return

    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

    doc_text = fallback_content or ""
    try:
        fetched = docs_service.documents().get(documentId=doc_id).execute()
        resolved = _collect_plain_text(fetched)
        if resolved:
            doc_text = resolved
    except Exception as exc:  # pragma: no cover - API availability issues handled gracefully
        logger.warning(
            "Google Docs: Failed to retrieve document %s content for notification: %s",
            doc_id,
            exc,
        )

    safe_title = title.replace("\r", " ").replace("\n", " ").strip()
    body = doc_url if not doc_text else f"{doc_url}\n\n{doc_text}"

    try:
        email_args = EmailArgs(
            to=recipient,
            subject=f'Created gdoc: "{safe_title}"',
            body=body,
        )
    except Exception as exc:
        logger.error(
            "Google Docs: Failed to build notification email payload for document %s: %s",
            doc_id,
            exc,
        )
        return

    try:
        result = send_email(email_args)
        logger.info(
            "Google Docs: Notification dispatched for document %s (success=%s)",
            doc_id,
            result.get("success"),
        )
    except Exception as exc:
        logger.error(
            "Google Docs: Notification email failed for document %s: %s",
            doc_id,
            exc,
        )


# ============================================================================
# Tool Definitions
# ============================================================================


class CreateDocArgs(BaseModel):
    title: str = Field(..., description="Document title")
    content: str = Field("", description="Document content formatted nicely in markdown")


@register_tool(
    name="CreateGoogleDoc",
    description="Create a new Google Doc from markdown content",
    param_model=CreateDocArgs,
    async_execution=True,  # Changed back to async
    category="output",
)
def create_google_doc(args: CreateDocArgs) -> dict[str, Any]:
    """Create a new Google Doc."""
    logger.info(f"CreateGoogleDoc called with args: {args}")
    logger.debug(f"Current thread ID: {threading.get_ident()}")

    # Check agent context right at the start
    ctx = get_agent_context()
    logger.info(f"CreateGoogleDoc: Agent context at start: {ctx}")

    try:
        allowed_folders = _get_allowed_folders()
        if not allowed_folders:
            logger.error("No allowed folders configured for CreateGoogleDoc")
            return {
                "success": False,
                "error": "No allowed folders configured for document creation",
            }

        docs_service, drive_service = _get_services()

        # Resolve the first usable folder from the greenlist
        target_folder_id: str | None = None
        target_folder_name: str | None = None
        for candidate in allowed_folders:
            try:
                resolved_id, resolved_name = _resolve_allowed_folder(candidate, drive_service)
                target_folder_id = resolved_id
                target_folder_name = resolved_name
                if target_folder_id and target_folder_name:
                    break
            except Exception as folder_error:
                logger.error("Failed to resolve folder '%s': %s", candidate, folder_error)

        if target_folder_id is None or target_folder_name is None:
            logger.error("Unable to resolve any allowed folder for CreateGoogleDoc")
            return {
                "success": False,
                "error": "Unable to resolve an allowed folder for document creation",
            }

        markdown_content = args.content or ""

        if markdown_content:
            file_metadata = {
                "name": args.title,
                "mimeType": "application/vnd.google-apps.document",
                "parents": [target_folder_id],
            }
            media = MediaIoBaseUpload(
                io.BytesIO(markdown_content.encode("utf-8")),
                mimetype="text/markdown",
                resumable=False,
            )
            created_file = (
                drive_service.files()
                .create(
                    body=file_metadata,
                    media_body=media,
                    fields="id, parents",
                )
                .execute()
            )
            doc_id = created_file["id"]
            logger.info("Uploaded markdown document '%s' with ID: %s", args.title, doc_id)
        else:
            doc = docs_service.documents().create(body={"title": args.title}).execute()
            doc_id = doc["documentId"]
            logger.info("Created empty document '%s' with ID: %s", args.title, doc_id)

            file_meta = drive_service.files().get(fileId=doc_id, fields="parents").execute()
            prev_parents = ",".join(file_meta.get("parents", []))
            drive_service.files().update(
                fileId=doc_id,
                addParents=target_folder_id,
                removeParents=prev_parents if prev_parents else None,
                fields="id, parents",
            ).execute()
            logger.info("Moved empty document '%s' to folder '%s'", doc_id, target_folder_name)

        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

        _notify_doc_created(
            doc_id=doc_id,
            title=args.title,
            docs_service=docs_service,
            fallback_content=markdown_content,
        )

        return {
            "success": True,
            "document_id": doc_id,
            "title": args.title,
            "url": doc_url,
        }

    except Exception as e:
        logger.error(f"Failed to create document: {e}")
        return {"success": False, "error": str(e)}


class SearchDriveArgs(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Text to match when searching Google Drive. Use defaults unless "
            "specified otherwise."
        ),
    )
    titles_only: bool = Field(
        True,
        description="Default true to only search file titles; false searches titles and content",
    )
    docs_only: bool = Field(
        True,
        description=(
            "Default true to restrict results to Google Docs files; false "
            "includes sheets, pdfs, etc."
        ),
    )
    max_results: int = Field(50, description="Maximum number of results to return.")

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Search query cannot be empty.")
        return trimmed

    @field_validator("max_results")
    @classmethod
    def validate_max_results(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_results must be greater than zero.")
        return value


@register_tool(
    name="SearchGoogleDrive",
    description="Search Google Drive by title or content",
    param_model=SearchDriveArgs,
)
def search_google_drive(args: SearchDriveArgs) -> dict[str, Any]:
    """Search Google Drive and return matching file names and IDs."""
    try:
        _, drive_service = _get_services()

        runtime_limit_seconds = 30.0
        start_time = time.monotonic()
        max_results = args.max_results
        page_count = 0
        truncated = False
        truncation_reason: str | None = None

        escaped_term = _escape_drive_query_term(args.query)

        if args.titles_only:
            search_clause = f"name contains '{escaped_term}'"
        else:
            search_clause = (
                f"(name contains '{escaped_term}' or fullText contains '{escaped_term}')"
            )

        query_parts = ["trashed=false", search_clause]

        if args.docs_only:
            query_parts.insert(1, "mimeType='application/vnd.google-apps.document'")
        else:
            # Exclude folders from search results
            query_parts.insert(1, "mimeType!='application/vnd.google-apps.folder'")

        query = " and ".join(query_parts)

        results: list[dict[str, str]] = []
        page_token: str | None = None
        elapsed = 0.0

        while True:
            remaining_budget = max_results - len(results)
            if remaining_budget <= 0:
                truncated = True
                truncation_reason = "max_results"
                break

            page_size = min(100, remaining_budget)
            if page_size <= 0:
                page_size = 1

            response = (
                drive_service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType)",
                    pageToken=page_token,
                    pageSize=page_size,
                    orderBy="modifiedTime desc",
                )
                .execute()
            )
            page_count += 1

            for file in response.get("files", []):
                results.append(
                    {
                        "id": file.get("id", ""),
                        "name": file.get("name", ""),
                        "mimeType": file.get("mimeType", ""),
                    }
                )
                if len(results) >= max_results:
                    break

            if len(results) >= max_results:
                truncated = True
                truncation_reason = "max_results"
                break

            page_token = response.get("nextPageToken")
            elapsed = time.monotonic() - start_time

            if elapsed >= runtime_limit_seconds:
                truncated = True
                truncation_reason = "runtime_limit"
                break

            if not page_token:
                break

        elapsed = time.monotonic() - start_time

        if page_token and not truncated:
            truncated = True
            truncation_reason = "unconsumed_page"

        logger.debug(
            "SearchGoogleDrive telemetry query=%s titles_only=%s docs_only=%s "
            "results=%d pages=%d elapsed=%.2fs truncated=%s reason=%s",
            args.query,
            args.titles_only,
            args.docs_only,
            len(results),
            page_count,
            elapsed,
            truncated,
            truncation_reason,
        )

        return {
            "success": True,
            "results": results[:max_results],
            "count": len(results),
            "query": args.query,
            "titles_only": args.titles_only,
            "docs_only": args.docs_only,
            "max_results": max_results,
            "truncated": truncated,
            "truncation_reason": truncation_reason,
            "runtime_seconds": round(elapsed, 2),
            "pages_fetched": page_count,
        }

    except Exception as exc:
        logger.error(f"Failed to search Google Drive: {exc}")
        return {"success": False, "error": str(exc)}


class ReadDocArgs(BaseModel):
    document_id_or_url: str = Field(..., description="Document ID or Google Docs URL")
    include_formatting: bool = Field(False, description="Include formatting information")
    offset: int = Field(
        0,
        description=(
            "Character offset to start reading from. Negative values read from the "
            "end of the document (e.g. -50000 returns the final 50000 characters). "
            "Use with the 'next_offset'/'total_chars' fields in the response to page "
            "through long documents."
        ),
    )
    max_chars: int = Field(
        DEFAULT_READ_WINDOW,
        gt=0,
        description=(
            "Maximum number of characters to return in this call. Large documents are "
            "returned one window at a time; check 'has_more' and 'next_offset' to continue."
        ),
    )
    find: str | None = Field(
        None,
        description=(
            "Optional text to locate within the document. When set, returns context "
            "windows (with character offsets) around each match instead of a contiguous "
            "slice, so you can jump straight to the relevant section of a long document."
        ),
    )


@register_tool(
    name="ReadGoogleDoc",
    description=(
        "Read the content of a Google Doc or Markdown file. Supports windowed reads of "
        "long documents via 'offset'/'max_chars' (negative offset reads from the end) and "
        "locating text with 'find'."
    ),
    param_model=ReadDocArgs,
)
def read_google_doc(args: ReadDocArgs) -> dict[str, Any]:
    """Read document content, with windowing and search for long documents."""
    try:
        docs_service, drive_service = _get_services()
        doc_id = _extract_doc_id(args.document_id_or_url)

        try:
            doc = docs_service.documents().get(documentId=doc_id).execute()
        except Exception as doc_exc:
            metadata: dict[str, Any] = {}
            try:
                raw_metadata = (
                    drive_service.files().get(fileId=doc_id, fields="id, name, mimeType").execute()
                )
                if isinstance(raw_metadata, dict):
                    metadata = raw_metadata
            except Exception as meta_exc:  # pragma: no cover - metadata fetch is best effort
                logger.debug(f"Failed to retrieve metadata for {doc_id}: {meta_exc}")

            mime_type = metadata.get("mimeType")
            title = metadata.get("name", "Untitled")
            is_markdown = (mime_type in MARKDOWN_MIME_TYPES) or title.lower().endswith(".md")
            if is_markdown:
                request = drive_service.files().get_media(fileId=doc_id)
                buffer = io.BytesIO()
                downloader = MediaIoBaseDownload(buffer, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                buffer.seek(0)
                full_text = buffer.read().decode("utf-8")

                base = {
                    "success": True,
                    "document_id": doc_id,
                    "title": title,
                    "url": f"https://drive.google.com/file/d/{doc_id}/view",
                }
                if args.find is not None:
                    return {**base, **_find_in_text(full_text, args.find, max_chars=args.max_chars)}
                return {**base, **_window_text(full_text, args.offset, args.max_chars)}

            raise doc_exc

        title = doc.get("title", "Untitled")
        url = f"https://docs.google.com/document/d/{doc_id}/edit"
        base = {
            "success": True,
            "document_id": doc_id,
            "title": title,
            "url": url,
        }

        full_text = _collect_plain_text(doc)

        if args.find is not None:
            return {**base, **_find_in_text(full_text, args.find, max_chars=args.max_chars)}

        window = _window_text(full_text, args.offset, args.max_chars)

        if args.include_formatting:
            runs = [
                (run.get("content", ""), _format_run_entry(run)) for run in _iter_text_runs(doc)
            ]
            win_start = window["offset"]
            win_end = win_start + len(window["content"])
            content: Any = _window_runs(runs, win_start, win_end)
        else:
            content = window["content"]

        return {
            **base,
            "content": content,
            "total_chars": window["total_chars"],
            "offset": window["offset"],
            "returned_chars": window["returned_chars"],
            "has_more": window["has_more"],
            "next_offset": window["next_offset"],
        }

    except Exception as e:
        logger.error(f"Failed to read document: {e}")
        return {"success": False, "error": str(e)}


class AppendDocArgs(BaseModel):
    document_id_or_url: str = Field(..., description="Document ID or Google Docs URL")
    content: str = Field(..., description="Content to append to the document")

    # No action field needed since only 'append' is allowed


@register_tool(
    name="AppendGoogleDoc",
    description="Append content to a Google Doc (only works with documents in the default folder)",
    param_model=AppendDocArgs,
    async_execution=True,  # Changed back to async
    category="output",
)
def append_google_doc(args: AppendDocArgs) -> dict[str, Any]:
    """Append content to a document (only in default folder for security)."""
    logger.info(f"AppendGoogleDoc called with args: {args}")
    logger.debug(f"Current thread ID: {threading.get_ident()}")

    # Check agent context right at the start
    ctx = get_agent_context()
    logger.info(f"AppendGoogleDoc: Agent context at start: {ctx}")

    try:
        docs_service, _ = _get_services()
        doc_id = _extract_doc_id(args.document_id_or_url)

        # Security check: only allow updates to documents in the default folder
        if not _is_document_in_default_folder(doc_id):
            agent_config = get_agent_context()
            bot_name = agent_config.get("bot_name", "unknown") if agent_config else "unknown"
            default_folder = f"{bot_name}-default"
            return {
                "success": False,
                "error": (
                    "Document updates are only allowed for documents in the "
                    f"'{default_folder}' folder"
                ),
            }

        # Get current document to find content boundaries
        doc = docs_service.documents().get(documentId=doc_id).execute()

        # Only append operation - find the end of the document
        end_index = doc["body"]["content"][-1].get("endIndex", 1) - 1
        requests = [
            {"insertText": {"location": {"index": end_index}, "text": "\n\n" + args.content}}
        ]

        # Execute the update
        docs_service.documents().batchUpdate(
            documentId=doc_id, body={"requests": requests}
        ).execute()

        logger.info(f"Appended content to document {doc_id}")

        return {
            "success": True,
            "document_id": doc_id,
            "action": "append",
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
        }

    except Exception as e:
        logger.error(f"Failed to update document: {e}")
        return {"success": False, "error": str(e)}
