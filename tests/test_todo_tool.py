"""Tests for the Todo tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.tools import todo
from app import tool_framework as tf


def _make_document(text: str, end_index: int | None = None) -> dict[str, object]:
    if end_index is None:
        end_index = len(text) + 1
    return {
        "documentId": "doc123",
        "title": todo.TODO_TITLE,
        "body": {
            "content": [
                {
                    "endIndex": end_index,
                    "paragraph": {
                        "elements": [
                            {
                                "textRun": {
                                    "content": text,
                                }
                            }
                        ]
                    },
                }
            ]
        },
    }


def test_todo_tools_registered():
    assert "TodoRead" in tf.TOOL_REGISTRY
    assert "TodoAdd" in tf.TOOL_REGISTRY


@patch("app.tools.todo._get_services")
def test_todo_read_existing_document(mock_get_services: MagicMock):
    mock_docs = MagicMock()
    mock_drive = MagicMock()
    documents_resource = mock_docs.documents.return_value

    mock_get_services.return_value = (mock_docs, mock_drive)

    mock_drive.files().list().execute.return_value = {"files": [{"id": "doc123"}]}
    documents_resource.get.return_value.execute.return_value = _make_document("# First\n\nItem")

    result = todo.todo_read(todo.TodoReadArgs())

    assert result["success"] is True
    assert result["document_id"] == "doc123"
    assert "First" in result["todos"]
    documents_resource.create.assert_not_called()


@patch("app.tools.todo._get_services")
def test_todo_add_appends_with_blank_line(mock_get_services: MagicMock):
    mock_docs = MagicMock()
    mock_drive = MagicMock()
    documents_resource = mock_docs.documents.return_value
    mock_get_services.return_value = (mock_docs, mock_drive)

    mock_drive.files().list().execute.return_value = {"files": [{"id": "doc123"}]}
    documents_resource.get.return_value.execute.return_value = _make_document("Existing todo", end_index=15)

    result = todo.todo_add(todo.TodoAddArgs(text="# Todo\n\nDetails"))

    assert result["success"] is True
    batch_args = documents_resource.batchUpdate.call_args.kwargs
    requests = batch_args["body"]["requests"]
    assert requests[0]["insertText"]["text"].startswith("\n\n")
    assert "# Todo" in requests[0]["insertText"]["text"]


@patch("app.tools.todo._get_services")
def test_todo_add_creates_document_when_missing(mock_get_services: MagicMock):
    mock_docs = MagicMock()
    mock_drive = MagicMock()
    documents_resource = mock_docs.documents.return_value
    mock_get_services.return_value = (mock_docs, mock_drive)

    mock_drive.files().list().execute.return_value = {"files": []}
    documents_resource.create.return_value.execute.return_value = {"documentId": "doc456"}
    mock_drive.files().get().execute.return_value = {"parents": ["root"]}
    documents_resource.get.return_value.execute.return_value = _make_document("", end_index=1)

    result = todo.todo_add(todo.TodoAddArgs(text="# Todo\n\nDescription"))

    assert result["success"] is True
    documents_resource.create.assert_called_once_with(body={"title": todo.TODO_TITLE})
    assert result["created_document"] is True
    inserted_text = documents_resource.batchUpdate.call_args.kwargs["body"]["requests"][0]["insertText"]["text"]
    assert inserted_text == "# Todo\n\nDescription"


def test_todo_add_validation():
    with pytest.raises(ValueError):
        todo.TodoAddArgs(text="   ")
