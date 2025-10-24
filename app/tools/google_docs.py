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
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.oauth2 import service_account

from ..tool_framework import register_tool

logger = logging.getLogger(__name__)

# Thread-local storage for agent context
_agent_context = threading.local()

# Google API scopes needed for Docs and Drive operations
SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

# Known MIME types for Markdown files stored in Google Drive
MARKDOWN_MIME_TYPES = {"text/markdown", "text/x-markdown"}

def set_agent_context(agent_config: Dict[str, Any] | None) -> None:
    """Set the current agent configuration in thread-local storage."""
    logger.debug(f"Google Docs: set_agent_context called with config: {agent_config}")
    _agent_context.config = agent_config


def get_agent_context() -> Dict[str, Any] | None:
    """Get the current agent configuration from thread-local storage."""
    ctx = getattr(_agent_context, 'config', None)
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
    folder_list = agent_config.get('docs_folder_greenlist')
    if folder_list:
        logger.debug(f"Google Docs: Using custom folder greenlist: {folder_list}")
        return folder_list
    
    # Generate default folder list based on agent's bot_name
    bot_name = agent_config.get('bot_name')
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
        
    for pattern in allowed:
        if pattern.startswith("^"):  # It's a regex pattern
            if re.fullmatch(pattern, folder_name, re.IGNORECASE):
                return True
        elif pattern.lower() == folder_name.lower():  # Exact match
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

    creds = (
        service_account.Credentials.from_service_account_file(key_path, scopes=SCOPES)
        .with_subject(impersonate)
    )

    docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    
    return docs_service, drive_service


def _find_or_create_folder(folder_name: str, drive_service: Any | None = None) -> str:
    """Find a folder by name or create it if it doesn't exist.
    
    Returns:
        Folder ID
    """
    if drive_service is None:
        _, drive_service = _get_services()
    
    # Search for existing folder
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = drive_service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name)',
        pageSize=1
    ).execute()
    
    files = results.get('files', [])
    if files:
        return files[0]['id']
    
    # Create new folder
    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    folder = drive_service.files().create(body=file_metadata, fields='id').execute()
    logger.info(f"Created new folder '{folder_name}' with ID: {folder['id']}")
    return folder['id']


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
    if re.match(r'^[a-zA-Z0-9_-]+$', doc_input):
        return doc_input
    
    # Extract from various URL formats
    patterns = [
        r'docs\.google\.com/document/d/([a-zA-Z0-9_-]+)',
        r'docs\.google\.com/.*[?&]id=([a-zA-Z0-9_-]+)',
        r'drive\.google\.com/file/d/([a-zA-Z0-9_-]+)',
        r'drive\.google\.com/.*[?&]id=([a-zA-Z0-9_-]+)',
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
    """Check if a document is in the agent's default folder.
    
    Args:
        doc_id: Document ID
        
    Returns:
        True if document is in default folder, False otherwise
    """
    _, drive_service = _get_services()
    
    # Get the agent's default folder name
    agent_config = get_agent_context()
    if not agent_config:
        logger.warning("Agent context not available for folder validation, allowing operation")
        # If agent context isn't available, we should allow the operation
        # This happens during synchronous execution before context is restored
        return True
    
    bot_name = agent_config.get('bot_name')
    if not bot_name:
        logger.warning("No bot_name in agent context for folder validation, allowing operation")
        return True
    
    default_folder_name = f"{bot_name}-default"
    
    # Get the document's parent folders
    doc_info = drive_service.files().get(
        fileId=doc_id,
        fields='parents'
    ).execute()
    
    parent_ids = doc_info.get('parents', [])
    
    # Check each parent folder
    for parent_id in parent_ids:
        folder_info = drive_service.files().get(
            fileId=parent_id,
            fields='name'
        ).execute()
        
        folder_name = folder_info.get('name', '')
        if folder_name == default_folder_name:
            return True
    
    return False


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
def create_google_doc(args: CreateDocArgs) -> Dict[str, Any]:
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
                "error": "No allowed folders configured for document creation"
            }

        docs_service, drive_service = _get_services()

        # Resolve the first usable folder from the greenlist
        target_folder_id: Optional[str] = None
        target_folder_name: Optional[str] = None
        for candidate in allowed_folders:
            try:
                target_folder_id = _find_or_create_folder(candidate, drive_service=drive_service)
                target_folder_name = candidate
                break
            except Exception as folder_error:
                logger.error("Failed to resolve folder '%s': %s", candidate, folder_error)

        if target_folder_id is None or target_folder_name is None:
            logger.error("Unable to resolve any allowed folder for CreateGoogleDoc")
            return {
                "success": False,
                "error": "Unable to resolve an allowed folder for document creation"
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
            created_file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, parents",
            ).execute()
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

        return {
            "success": True,
            "document_id": doc_id,
            "title": args.title,
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
        }
        
    except Exception as e:
        logger.error(f"Failed to create document: {e}")
        return {
            "success": False,
            "error": str(e)
        }


class SearchDriveArgs(BaseModel):
    query: str = Field(..., description="Text to match when searching Google Drive. Use defaults unless specified otherwise.")
    titles_only: bool = Field(True, description="Default true to only search file titles; false searches titles and content")
    docs_only: bool = Field(True, description="Default true to restrict results to Google Docs files; false includes sheets, pdfs, etc.")

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Search query cannot be empty.")
        return trimmed


@register_tool(
    name="SearchGoogleDrive",
    description="Search Google Drive by title or content",
    param_model=SearchDriveArgs,
)
def search_google_drive(args: SearchDriveArgs) -> Dict[str, Any]:
    """Search Google Drive and return matching file names and IDs."""
    try:
        _, drive_service = _get_services()

        escaped_term = _escape_drive_query_term(args.query)

        if args.titles_only:
            search_clause = f"name contains '{escaped_term}'"
        else:
            search_clause = f"(name contains '{escaped_term}' or fullText contains '{escaped_term}')"

        query_parts = ["trashed=false", search_clause]

        if args.docs_only:
            query_parts.insert(1, "mimeType='application/vnd.google-apps.document'")

        query = " and ".join(query_parts)

        results: List[Dict[str, str]] = []
        page_token: Optional[str] = None

        while True:
            response = drive_service.files().list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name)",
                pageToken=page_token,
                pageSize=100,
            ).execute()

            for file in response.get("files", []):
                results.append({
                    "id": file.get("id", ""),
                    "name": file.get("name", ""),
                })

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return {
            "success": True,
            "results": results,
            "count": len(results),
            "query": args.query,
            "titles_only": args.titles_only,
            "docs_only": args.docs_only,
        }

    except Exception as exc:
        logger.error(f"Failed to search Google Drive: {exc}")
        return {
            "success": False,
            "error": str(exc)
        }


class ReadDocArgs(BaseModel):
    document_id_or_url: str = Field(..., description="Document ID or Google Docs URL")
    include_formatting: bool = Field(False, description="Include formatting information")


@register_tool(
    name="ReadGoogleDoc",
    description="Read the content of a Google Doc or Markdown file",
    param_model=ReadDocArgs
)
def read_google_doc(args: ReadDocArgs) -> Dict[str, Any]:
    """Read document content."""
    try:
        docs_service, drive_service = _get_services()
        doc_id = _extract_doc_id(args.document_id_or_url)

        try:
            doc = docs_service.documents().get(documentId=doc_id).execute()
        except Exception as doc_exc:
            metadata: Dict[str, Any] = {}
            try:
                raw_metadata = drive_service.files().get(
                    fileId=doc_id,
                    fields="id, name, mimeType"
                ).execute()
                if isinstance(raw_metadata, dict):
                    metadata = raw_metadata
            except Exception as meta_exc:  # pragma: no cover - metadata fetch is best effort
                logger.debug(f"Failed to retrieve metadata for {doc_id}: {meta_exc}")

            mime_type = metadata.get("mimeType")
            title = metadata.get("name", "Untitled")
            is_markdown = (
                (mime_type in MARKDOWN_MIME_TYPES) or title.lower().endswith(".md")
            )
            if is_markdown:
                request = drive_service.files().get_media(fileId=doc_id)
                buffer = io.BytesIO()
                downloader = MediaIoBaseDownload(buffer, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                buffer.seek(0)
                content = buffer.read().decode("utf-8")

                return {
                    "success": True,
                    "document_id": doc_id,
                    "title": title,
                    "content": content,
                    "url": f"https://drive.google.com/file/d/{doc_id}/view",
                }

            raise doc_exc

        content_parts = []
        for element in doc.get('body', {}).get('content', []):
            if 'paragraph' not in element:
                continue
            for elem in element['paragraph'].get('elements', []):
                text_run = elem.get('textRun')
                if not text_run:
                    continue
                text = text_run.get('content', '')
                if args.include_formatting:
                    style = text_run.get('textStyle', {})
                    if style:
                        content_parts.append({"text": text, "style": style})
                    else:
                        content_parts.append({"text": text})
                else:
                    content_parts.append(text)

        content = content_parts if args.include_formatting else ''.join(content_parts)
        title = doc.get('title', 'Untitled')
        url = f"https://docs.google.com/document/d/{doc_id}/edit"

        return {
            "success": True,
            "document_id": doc_id,
            "title": title,
            "content": content,
            "url": url,
        }

    except Exception as e:
        logger.error(f"Failed to read document: {e}")
        return {
            "success": False,
            "error": str(e)
        }


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
def append_google_doc(args: AppendDocArgs) -> Dict[str, Any]:
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
            bot_name = agent_config.get('bot_name', 'unknown') if agent_config else 'unknown'
            default_folder = f"{bot_name}-default"
            return {
                "success": False,
                "error": f"Document updates are only allowed for documents in the '{default_folder}' folder"
            }
        
        # Get current document to find content boundaries
        doc = docs_service.documents().get(documentId=doc_id).execute()
        
        # Only append operation - find the end of the document
        end_index = doc['body']['content'][-1].get('endIndex', 1) - 1
        requests = [{
            'insertText': {
                'location': {'index': end_index},
                'text': '\n\n' + args.content
            }
        }]
        
        # Execute the update
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={'requests': requests}
        ).execute()
        
        logger.info(f"Appended content to document {doc_id}")
        
        return {
            "success": True,
            "document_id": doc_id,
            "action": "append",
            "url": f"https://docs.google.com/document/d/{doc_id}/edit"
        }
        
    except Exception as e:
        logger.error(f"Failed to update document: {e}")
        return {
            "success": False,
            "error": str(e)
        }


