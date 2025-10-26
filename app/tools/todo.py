"""Google Docs-backed Todo tools for Ringdown."""

from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from pydantic import BaseModel, Field, field_validator

from ..tool_framework import register_tool
from .google_docs import _collect_plain_text, _escape_drive_query_term, _get_services

logger = logging.getLogger(__name__)

TODO_TITLE = "Ringdown Todo"
TODO_DOCUMENT_URL_TEMPLATE = "https://docs.google.com/document/d/{doc_id}/edit"


class TodoReadArgs(BaseModel):
    """Arguments for the TodoRead tool. No parameters are required."""


class TodoAddArgs(BaseModel):
    """Arguments for the TodoAdd tool."""

    text: str = Field(
        ...,
        description=(
            "Markdown todo entry formatted exactly as:\n\n"
            "# Todo name\n\n"
            "description"
        ),
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Todo text must not be empty")
        return value


def _find_existing_todo_document(drive_service: Any) -> str | None:
    """Return the document ID for the todo list if it already exists."""

    escaped_title = _escape_drive_query_term(TODO_TITLE)
    query = (
        "mimeType='application/vnd.google-apps.document' and "
        "trashed=false and "
        f"name='{escaped_title}' and "
        "'root' in parents"
    )

    response = drive_service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name)",
        pageSize=1,
    ).execute()

    files = response.get("files", []) if isinstance(response, dict) else []
    if not files:
        return None

    return files[0].get("id")


def _ensure_document_in_root(doc_id: str, drive_service: Any) -> None:
    """Ensure the given document resides in the Drive root folder."""

    metadata = drive_service.files().get(fileId=doc_id, fields="parents").execute()
    parents = metadata.get("parents", []) if isinstance(metadata, dict) else []

    if "root" in parents:
        return

    remove_parents = ",".join(parents) if parents else None
    update_kwargs: Dict[str, Any] = {
        "fileId": doc_id,
        "addParents": "root",
        "fields": "id, parents",
    }
    if remove_parents:
        update_kwargs["removeParents"] = remove_parents

    drive_service.files().update(**update_kwargs).execute()


def _create_todo_document(docs_service: Any, drive_service: Any) -> str:
    """Create the todo document and ensure it is available at the root."""

    creation = docs_service.documents().create(body={"title": TODO_TITLE}).execute()
    doc_id = creation.get("documentId")
    if not doc_id:
        raise RuntimeError("Failed to create Ringdown Todo document")

    _ensure_document_in_root(doc_id, drive_service)
    return doc_id


def _ensure_todo_document(docs_service: Any, drive_service: Any) -> Tuple[str, bool]:
    """Return the todo document ID, creating the document if necessary."""

    existing = _find_existing_todo_document(drive_service)
    if existing:
        return existing, False

    logger.info("Ringdown Todo document not found; creating a new one.")
    doc_id = _create_todo_document(docs_service, drive_service)
    return doc_id, True


def _todo_document_url(doc_id: str) -> str:
    return TODO_DOCUMENT_URL_TEMPLATE.format(doc_id=doc_id)


@register_tool(
    name="TodoRead",
    description="Retrieve the full contents of the Ringdown Todo document.",
    param_model=TodoReadArgs,
    prompt="""
## TodoRead
Use this tool to read the shared Ringdown todo list stored in Google Docs.
Call this when you need the latest todos before making updates or summaries.
""".strip(),
)
def todo_read(_: TodoReadArgs) -> Dict[str, Any]:
    """Read the Ringdown todo document and return its text content."""

    try:
        docs_service, drive_service = _get_services()
        doc_id, _ = _ensure_todo_document(docs_service, drive_service)
        document = docs_service.documents().get(documentId=doc_id).execute()
        content = _collect_plain_text(document).strip()

        return {
            "success": True,
            "document_id": doc_id,
            "title": document.get("title", TODO_TITLE),
            "url": _todo_document_url(doc_id),
            "todos": content,
        }
    except Exception as exc:  # noqa: BLE001 - propagate error via response payload
        logger.exception("Failed to read Ringdown Todo document: %s", exc)
        return {
            "success": False,
            "error": str(exc),
        }


@register_tool(
    name="TodoAdd",
    description="Append a new entry to the Ringdown Todo document.",
    param_model=TodoAddArgs,
    prompt="""
## TodoAdd
Use this tool to append a new todo to the shared Ringdown list.
Provide markdown in the form:

# Todo name

description
""".strip(),
)
def todo_add(args: TodoAddArgs) -> Dict[str, Any]:
    """Append a todo entry to the Ringdown todo document."""

    try:
        docs_service, drive_service = _get_services()
        doc_id, created = _ensure_todo_document(docs_service, drive_service)

        document = docs_service.documents().get(documentId=doc_id).execute()
        body = document.get("body", {})
        content = body.get("content", [])
        if not content:
            end_index = 0
            existing_text = ""
        else:
            end_index = content[-1].get("endIndex", 1) - 1
            existing_text = _collect_plain_text(document).strip()

        insertion_text = args.text if not existing_text else f"\n\n{args.text}"

        requests = [
            {
                "insertText": {
                    "location": {"index": max(end_index, 0)},
                    "text": insertion_text,
                }
            }
        ]

        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests},
        ).execute()

        return {
            "success": True,
            "document_id": doc_id,
            "created_document": created,
            "appended_text": args.text,
            "url": _todo_document_url(doc_id),
        }
    except Exception as exc:  # noqa: BLE001 - propagate error via response payload
        logger.exception("Failed to append to Ringdown Todo document: %s", exc)
        return {
            "success": False,
            "error": str(exc),
        }
