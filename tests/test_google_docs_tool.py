#!/usr/bin/env python3
"""Tests for Google Docs tools."""

import os
from unittest.mock import patch, MagicMock, Mock
import pytest

from app.tools import google_docs
from app import tool_framework as tf


def test_docs_tools_registered():
    """Test that all Google Docs tools are registered."""
    assert "CreateGoogleDoc" in tf.TOOL_REGISTRY
    assert "ReadGoogleDoc" in tf.TOOL_REGISTRY
    assert "AppendGoogleDoc" in tf.TOOL_REGISTRY
    assert "SearchGoogleDrive" in tf.TOOL_REGISTRY


def test_folder_validation_default():
    """_is_folder_allowed should reflect default greenlist based on bot_name."""
    # Set agent context with bot_name
    google_docs.set_agent_context({"bot_name": "testbot"})

    # Allowed folder should return True
    assert google_docs._is_folder_allowed("testbot-default") is True

    # Disallowed folder should return False
    assert google_docs._is_folder_allowed("Random Folder") is False

    # Clear agent context after test
    google_docs.set_agent_context(None)

    # Set agent context with bot_name
    google_docs.set_agent_context({"bot_name": "testbot"})
    
    # Valid folders (default greenlist based on bot_name)
    valid_folders = [
        "testbot-default",  # Should be dynamically generated
    ]
    
    # Invalid folders
    invalid_folders = [
        "Random Folder",
        "Personal Documents",
    ]
    
    # Test valid folders
    for folder in valid_folders:
        assert google_docs._is_folder_allowed(folder) is True

    # Test invalid folders
    for folder in invalid_folders:
        assert google_docs._is_folder_allowed(folder) is False
    
    # Clear agent context after test
    google_docs.set_agent_context(None)


def test_folder_validation_dynamic_bot_name():
    """_is_folder_allowed should change default folder name based on bot_name."""
    test_cases = [
        ("testbot", "testbot-default"),
        ("myagent", "myagent-default"),
        ("AssistantBot", "AssistantBot-default"),
    ]

    for bot_name, expected_folder in test_cases:
        google_docs.set_agent_context({"bot_name": bot_name})
        assert google_docs._is_folder_allowed(expected_folder) is True
        # Folders for other bots should be disallowed
        for other_bot, other_folder in test_cases:
            if other_bot != bot_name:
                assert google_docs._is_folder_allowed(other_folder) is False

    google_docs.set_agent_context(None)


def test_folder_validation_missing_agent_context():
    """_is_folder_allowed should error when agent context is missing."""
    google_docs.set_agent_context(None)
    with pytest.raises(ValueError, match="Agent context is required"):
        google_docs._is_folder_allowed("test-folder")




def test_folder_validation_missing_bot_name():
    """_is_folder_allowed should error when bot_name missing in context."""
    google_docs.set_agent_context({"some_other_field": "value"})
    with pytest.raises(ValueError, match="bot_name"):
        google_docs._is_folder_allowed("test-folder")
    google_docs.set_agent_context(None)



    
    # Clear agent context after test
    google_docs.set_agent_context(None)


def test_folder_validation_with_agent_context():
    """_is_folder_allowed should respect custom agent greenlist."""
    test_agent_config = {

        "docs_folder_greenlist": [
            "Test Folder",
            "^Project .*$"
        ]
    }
    google_docs.set_agent_context(test_agent_config)
    assert google_docs._is_folder_allowed("Test Folder") is True
    assert google_docs._is_folder_allowed("Project Alpha") is True
    assert google_docs._is_folder_allowed("ringdown-default") is False
    google_docs.set_agent_context(None)







def test_extract_doc_id():
    """Test document ID extraction from various formats."""
    test_cases = [
        ("1234567890abcdef", "1234567890abcdef"),  # Direct ID
        ("https://docs.google.com/document/d/1234567890abcdef/edit", "1234567890abcdef"),
        ("https://docs.google.com/document/d/1234567890abcdef/edit?usp=sharing", "1234567890abcdef"),
        ("docs.google.com/document/d/1234567890abcdef", "1234567890abcdef"),
    ]
    
    for input_val, expected in test_cases:
        assert google_docs._extract_doc_id(input_val) == expected
    
    # Test invalid input
    with pytest.raises(ValueError):
        google_docs._extract_doc_id("not a valid url or id!")


def test_create_document_mock():
    """Test document creation with mocked Google API - now async execution."""
    # Set agent context for dynamic folder name
    google_docs.set_agent_context({"bot_name": "testbot"})
    
    # Mock the services
    mock_docs_service = MagicMock()
    mock_drive_service = MagicMock()
    
    # Mock document creation
    mock_docs_service.documents().create().execute.return_value = {
        'documentId': 'test_doc_123',
        'title': 'Test Document'
    }
    
    # Mock batch update for content
    mock_docs_service.documents().batchUpdate().execute.return_value = {}
    
    # Mock folder operations
    mock_drive_service.files().list().execute.return_value = {
        'files': [{'id': 'folder_123', 'name': 'testbot-default'}]
    }
    mock_drive_service.files().get().execute.return_value = {
        'parents': ['root']
    }
    mock_drive_service.files().update().execute.return_value = {'id': 'test_doc_123'}
    
    with patch('app.tools.google_docs._get_services', return_value=(mock_docs_service, mock_drive_service)):
        result = tf.execute_tool("CreateGoogleDoc", {
            "title": "Test Document",
            "content": "This is test content.",
        })
        
        # Since CreateGoogleDoc now runs asynchronously, we get an immediate response
        assert result["success"] is True
        assert result["async_execution"] is True
        assert "started asynchronously" in result["message"]
    
    # Clear agent context after test
    google_docs.set_agent_context(None)


def test_read_document_mock():
    """Test document reading with mocked Google API."""
    mock_docs_service = MagicMock()
    mock_drive_service = MagicMock()
    
    # Mock document content
    mock_docs_service.documents().get().execute.return_value = {
        'documentId': 'test_doc_123',
        'title': 'Test Document',
        'body': {
            'content': [
                {
                    'paragraph': {
                        'elements': [
                            {
                                'textRun': {
                                    'content': 'This is test content.'
                                }
                            }
                        ]
                    }
                }
            ]
        }
    }
    
    with patch('app.tools.google_docs._get_services', return_value=(mock_docs_service, mock_drive_service)):
        result = tf.execute_tool("ReadGoogleDoc", {
            "document_id_or_url": "test_doc_123",
            "include_formatting": False
        })
        
        assert result["success"] is True
        assert result["document_id"] == "test_doc_123"
        assert result["content"] == "This is test content."


def test_update_document_mock():
    """Test document updating with mocked Google API - now async execution."""
    # Set agent context
    google_docs.set_agent_context({"bot_name": "testbot"})
    
    mock_docs_service = MagicMock()
    mock_drive_service = MagicMock()
    
    # Mock getting current document
    mock_docs_service.documents().get().execute.return_value = {
        'documentId': 'test_doc_123',
        'body': {
            'content': [
                {'endIndex': 1},
                {'endIndex': 100}
            ]
        }
    }
    
    # Mock folder validation - document is in default folder
    mock_get_calls = [
        MagicMock(),  # First call for document parents
        MagicMock()   # Second call for folder name
    ]
    mock_get_calls[0].execute.return_value = {'parents': ['folder_123']}
    mock_get_calls[1].execute.return_value = {'name': 'testbot-default'}
    mock_drive_service.files().get.side_effect = mock_get_calls
    
    # Mock batch update
    mock_docs_service.documents().batchUpdate().execute.return_value = {}
    
    with patch('app.tools.google_docs._get_services', return_value=(mock_docs_service, mock_drive_service)):
        result = tf.execute_tool("AppendGoogleDoc", {
            "document_id_or_url": "test_doc_123",
            "content": "New content to append."
        })
        
        # Since AppendGoogleDoc now runs asynchronously, we get an immediate response
        assert result["success"] is True
        assert result["async_execution"] is True
        assert "started asynchronously" in result["message"]
    
    # Clear agent context after test
    google_docs.set_agent_context(None)


def test_update_document_not_in_default_folder():
    """Test async execution for AppendGoogleDoc - folder validation will happen in background."""
    # Set agent context
    google_docs.set_agent_context({"bot_name": "testbot"})
    
    result = tf.execute_tool("AppendGoogleDoc", {
        "document_id_or_url": "test_doc_123",
        "content": "New content to append."
    })
    
    # Since AppendGoogleDoc now runs asynchronously, we always get immediate success
    # Validation errors (like folder restrictions) will be emailed as errors
    assert result["success"] is True
    assert result["async_execution"] is True
    assert "started asynchronously" in result["message"]
    
    # Clear agent context after test
    google_docs.set_agent_context(None)


def test_search_drive_default_filters():
    """SearchGoogleDrive should restrict to Docs titles by default."""
    mock_docs_service = MagicMock()
    mock_drive_service = MagicMock()

    mock_drive_service.files.return_value.list.return_value.execute.return_value = {
        "files": [
            {"id": "doc1", "name": "Meeting Notes"},
            {"id": "doc2", "name": "Meeting Agenda"},
        ],
        "nextPageToken": None,
    }

    with patch("app.tools.google_docs._get_services", return_value=(mock_docs_service, mock_drive_service)):
        result = tf.execute_tool("SearchGoogleDrive", {"query": "Meeting"})

    assert result["success"] is True
    assert result["count"] == 2
    assert [entry["id"] for entry in result["results"]] == ["doc1", "doc2"]

    call_kwargs = mock_drive_service.files.return_value.list.call_args.kwargs
    assert call_kwargs["q"] == (
        "trashed=false and mimeType='application/vnd.google-apps.document' and name contains 'Meeting'"
    )
    assert call_kwargs["pageToken"] is None


def test_search_drive_full_text_and_all_types():
    """SearchGoogleDrive should search contents when configured and handle pagination."""
    mock_docs_service = MagicMock()
    mock_drive_service = MagicMock()

    list_mock = mock_drive_service.files.return_value.list
    list_mock.return_value.execute.side_effect = [
        {
            "files": [{"id": "doc3", "name": "Project Plan"}],
            "nextPageToken": "token123",
        },
        {
            "files": [{"id": "sheet1", "name": "Project Spreadsheet"}],
            "nextPageToken": None,
        },
    ]

    with patch("app.tools.google_docs._get_services", return_value=(mock_docs_service, mock_drive_service)):
        result = tf.execute_tool(
            "SearchGoogleDrive",
            {"query": "Project", "titles_only": False, "docs_only": False},
        )

    assert result["success"] is True
    assert result["count"] == 2
    assert [entry["id"] for entry in result["results"]] == ["doc3", "sheet1"]

    first_call_kwargs = list_mock.call_args_list[0].kwargs
    second_call_kwargs = list_mock.call_args_list[1].kwargs

    assert first_call_kwargs["q"] == (
        "trashed=false and (name contains 'Project' or fullText contains 'Project')"
    )
    assert "mimeType" not in first_call_kwargs["q"]
    assert first_call_kwargs["pageToken"] is None
    assert second_call_kwargs["pageToken"] == "token123"


def test_search_drive_escapes_quotes():
    """SearchGoogleDrive should escape single quotes in the query."""
    mock_docs_service = MagicMock()
    mock_drive_service = MagicMock()

    mock_drive_service.files.return_value.list.return_value.execute.return_value = {
        "files": [],
        "nextPageToken": None,
    }

    with patch("app.tools.google_docs._get_services", return_value=(mock_docs_service, mock_drive_service)):
        result = tf.execute_tool("SearchGoogleDrive", {"query": "Bob's Plan"})

    assert result["success"] is True

    query_string = mock_drive_service.files.return_value.list.call_args.kwargs["q"]
    assert "Bob\\'s Plan" in query_string


def test_error_handling():
    """Test error handling when API calls fail - now async execution."""
    result = tf.execute_tool("CreateGoogleDoc", {
        "title": "Test",
        "content": "Test"
    })
    
    # Since CreateGoogleDoc now runs asynchronously, we always get immediate success
    # API errors will be handled in the background and emailed
    assert result["success"] is True
    assert result["async_execution"] is True
    assert "started asynchronously" in result["message"] 
