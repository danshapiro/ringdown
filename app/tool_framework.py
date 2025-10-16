"""Universal tool framework for Ringdown.

This module offers:
1.  `register_tool` decorator – declare a function as an LLM tool.
2.  `TOOL_REGISTRY` – runtime registry of all tools.
3.  Helpers to build the OpenAI/LiteLLM *tools* list and execute
    model-requested tool calls.

Design goals
• *Opinionated but flexible:* Each tool provides a Pydantic model
  describing its parameters – strong runtime validation & JSON-Schema
  generation for OpenAI.
• *No hidden globals:* Registry is a thin dict, easy to introspect and
  test.
• *Agent-aware:* `get_tools_for_agent(agent_cfg)` resolves the tool list
  specified in `agent_cfg["tools"]` (falling back to defaults).
• *Cross-provider compatible:* Schemas work with OpenAI, Anthropic, and Gemini
  via LiteLLM by using JSON Schema draft 2020-12 compatible formats.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import threading
import traceback
from functools import wraps
from typing import Any, Callable, Dict, List, Sequence, Type

from pydantic import BaseModel, ValidationError
from pydantic.json_schema import models_json_schema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry internals
# ---------------------------------------------------------------------------

class _ToolSpec(BaseModel):
    """Metadata for a registered tool."""

    name: str
    description: str
    param_model: Type[BaseModel]
    func: Callable[[BaseModel], Any]
    prompt: str | None = None
    async_execution: bool = False
    category: str = "input"

    model_config = {
        "arbitrary_types_allowed": True,
    }

    def openai_schema(self) -> Dict[str, Any]:
        """Return OpenAI-compatible JSON schema dict for this tool.
        
        Uses JSON Schema draft 2020-12 compatible format with $defs instead of
        the legacy $components/schemas format. This ensures compatibility with:
        - OpenAI (supports both formats)
        - Anthropic (requires draft 2020-12 format)
        - Gemini (via LiteLLM translation)
        """
        # Use JSON Schema draft 2020-12 compatible $defs format for maximum compatibility
        schema = self.param_model.model_json_schema(ref_template="#/$defs/{model}")
        
        # Add JSON Schema draft identifier for maximum compatibility
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        
        # Ensure basic required fields
        if "type" not in schema:
            schema["type"] = "object"
        if "properties" not in schema:
            schema["properties"] = {}
        
        # Set strict validation for better provider compatibility
        if "additionalProperties" not in schema:
            schema["additionalProperties"] = False
        
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


# Global mapping name -> ToolSpec
TOOL_REGISTRY: Dict[str, _ToolSpec] = {}

# Global storage for current agent context to support async execution
_current_agent_context: Dict[str, Any] | None = None

# Global registry for async tool results and callbacks
_async_tool_registry: Dict[str, Dict[str, Any]] = {}

# Function to register a callback for async tool completion
def register_async_callback(async_id: str, callback: Any) -> None:
    """Register a callback to be called when an async tool completes."""
    if async_id not in _async_tool_registry:
        _async_tool_registry[async_id] = {}
    _async_tool_registry[async_id]['callback'] = callback

# Function to get the result of an async tool
def get_async_result(async_id: str) -> Dict[str, Any] | None:
    """Get the result of an async tool execution if available."""
    return _async_tool_registry.get(async_id, {}).get('result')

# ---------------------------------------------------------------------------
# Public decorator
# ---------------------------------------------------------------------------

def register_tool(
    *,
    name: str | None = None,
    description: str | None = None,
    param_model: Type[BaseModel],
    prompt: str | None = None,
    async_execution: bool = False,
    category: str = "input",
) -> Callable[[Callable[[BaseModel], Any]], Callable[[BaseModel], Any]]:
    """Decorator to register a callable as an LLM tool.

    Example::

        class SearchArgs(BaseModel):
            query: str
            max_results: int | None = 5

        @register_tool(name="search", description="Web search", param_model=SearchArgs)
        def search_tool(args: SearchArgs) -> dict:
            ...
    """

    def decorator(func: Callable[[BaseModel], Any]):
        nonlocal name, description

        if name is None:
            name = func.__name__
        if description is None:
            description = inspect.getdoc(func) or "No description supplied."

        if name in TOOL_REGISTRY:
            raise ValueError(f"Tool '{name}' already registered")

        cat_value = category.lower()
        if cat_value not in {"input", "output"}:
            raise ValueError(f"Tool '{name}' category must be 'input' or 'output', got '{category}'")

        TOOL_REGISTRY[name] = _ToolSpec(
            name=name,
            description=description,
            param_model=param_model,
            func=func,
            prompt=prompt,
            async_execution=async_execution,
            category=cat_value,
        )
        logger.debug("Registered tool '%s'", name)

        @wraps(func)
        def wrapper(args: BaseModel):  # type: ignore[override]
            return func(args)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Helper APIs
# ---------------------------------------------------------------------------

def list_tools() -> List[str]:
    """Return available tool names."""

    return list(TOOL_REGISTRY.keys())


def get_tool_schema(name: str) -> Dict[str, Any]:
    """Return OpenAI-compatible JSON schema for a tool by name."""
    if name not in TOOL_REGISTRY:
        raise ValueError(f"Tool '{name}' not found")
    return TOOL_REGISTRY[name].openai_schema()


def get_tool_prompt(name: str) -> str:
    """Return the prompt documentation for a tool by name."""
    if name not in TOOL_REGISTRY:
        raise ValueError(f"Tool '{name}' not found")
    spec = TOOL_REGISTRY[name]
    return spec.prompt or f"Tool '{name}' has no prompt documentation."


def get_tools_for_agent(agent_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return list of OpenAI tool schemas enabled for this agent.

    `agent_cfg` should contain an optional `tools` key: list[str].  If
    absent or empty, no tools are enabled.
    """

    names: Sequence[str] = agent_cfg.get("tools", [])
    schemas: List[Dict[str, Any]] = []

    for n in names:
        if n not in TOOL_REGISTRY:
            raise KeyError(f"Tool '{n}' referenced by agent but not registered")
        schemas.append(TOOL_REGISTRY[n].openai_schema())

    return schemas


def _truncate_tool_response(result: Any) -> Any:
    """Truncate tool response to 200k characters if needed."""
    try:
        # Convert result to JSON string to check length
        result_json = json.dumps(result, ensure_ascii=False)
        
        # Check if truncation is needed
        if len(result_json) <= 200000:
            return result
        
        # Calculate how many characters were removed
        chars_removed = len(result_json) - 200000
        
        # Create truncation notice
        truncation_notice = f"[TRUNCATED: {chars_removed} more characters removed; be sure to mention this to the user.]"
        
        # Try to be smarter about truncation for different data types
        if isinstance(result, str):
            # For strings, truncate the original string and add notice
            # Account for JSON quotes and escape sequences
            max_content_length = 200000 - len(json.dumps("")) - len(truncation_notice) - 10  # safety buffer
            if max_content_length > 0:
                truncated_str = result[:max_content_length]
                return truncated_str + "\n\n" + truncation_notice
            else:
                return truncation_notice
        
        elif isinstance(result, dict):
            # For dicts, try to preserve structure by truncating string values
            truncated_dict = {}
            remaining_chars = 200000 - len(truncation_notice) - 100  # safety buffer
            
            for key, value in result.items():
                key_json = json.dumps({key: value})
                if len(json.dumps(truncated_dict)) + len(key_json) < remaining_chars:
                    truncated_dict[key] = value
                else:
                    # Try to add a truncated version of this key-value pair
                    if isinstance(value, str) and len(value) > 100:
                        truncated_value = value[:100] + "..."
                        truncated_dict[key] = truncated_value
                    elif not truncated_dict:  # Ensure we have at least one key
                        truncated_dict[key] = str(value)[:100] + "..." if len(str(value)) > 100 else value
                    break
            
            truncated_dict["_truncation_notice"] = truncation_notice
            return truncated_dict
        
        elif isinstance(result, list):
            # For lists, include as many complete items as possible
            truncated_list = []
            remaining_chars = 200000 - len(truncation_notice) - 100  # safety buffer
            
            for item in result:
                item_json = json.dumps(item)
                if len(json.dumps(truncated_list)) + len(item_json) < remaining_chars:
                    truncated_list.append(item)
                else:
                    # Try to add a truncated version of this item
                    if isinstance(item, str) and len(item) > 100:
                        truncated_list.append(item[:100] + "...")
                    elif not truncated_list:  # Ensure we have at least one item
                        truncated_list.append(str(item)[:100] + "..." if len(str(item)) > 100 else item)
                    break
            
            truncated_list.append(truncation_notice)
            return truncated_list
        
        else:
            # For other types, convert to string and truncate
            str_result = str(result)
            max_content_length = 200000 - len(truncation_notice) - 10
            if max_content_length > 0:
                return str_result[:max_content_length] + "\n\n" + truncation_notice
            else:
                return truncation_notice
            
    except Exception as exc:
        # If anything goes wrong with truncation, log and return original
        logger.error(f"Failed to truncate tool response: {exc}")
        return result


def _get_default_error_email() -> str:
    """Get the default email address for error reporting."""
    from app.settings import get_default_email as _get_email
    return _get_email()


def _send_error_email(tool_name: str, raw_args: Dict[str, Any], error: Exception) -> None:
    """Send an email about a tool execution error."""
    try:
        # Import here to avoid circular imports
        from .tools.email import send_email, EmailArgs, _is_recipient_allowed
        
        error_email = _get_default_error_email()
        
        # Check if we can send to this email address
        if not _is_recipient_allowed(error_email):
            logger.error(f"Cannot send error email to {error_email} - not in greenlist")
            return
        
        # Format the error message
        error_msg = (
            f"Tool Execution Error\n\n"
            f"Tool: {tool_name}\n"
            f"Arguments: {json.dumps(raw_args, indent=2)}\n\n"
            f"Error: {str(error)}\n\n"
            f"Full traceback:\n{traceback.format_exc()}"
        )
        
        # Send the error email
        from app.settings import get_default_bot_name as _bot_default
        email_args = EmailArgs(
            to=error_email,
            subject=f"[{_bot_default()}] Async Tool Error: {tool_name}",
            body=error_msg
        )
        
        send_email(email_args)
        logger.info(f"Sent error email for tool {tool_name} to {error_email}")
        
    except Exception as e:
        logger.error(f"Failed to send error email for tool {tool_name}: {e}")


def _execute_tool_async(
    name: str,
    raw_args: Dict[str, Any],
    spec: _ToolSpec,
    async_id: str,
    preflight_payload: Any | None,
) -> None:
    """Execute a tool asynchronously in a background thread."""
    # Capture the current agent context from module-level storage
    current_agent_context = _current_agent_context
    
    if current_agent_context is None:
        logger.warning(f"No agent context available for async tool {name}")
    else:
        logger.debug(f"Captured agent context for async tool {name}: {current_agent_context}")
    
    def async_execution():
        try:
            # Restore agent context in the new thread by calling set_agent_context
            # This will propagate to all tools that need it
            if current_agent_context is not None:
                logger.debug(f"Restoring agent context in async thread for tool {name}")
                # Import here to avoid circular imports
                set_agent_context(current_agent_context)
            else:
                logger.warning(f"No agent context to restore for async tool {name}")
            
            # Validate and execute the tool
            args_obj = spec.param_model(**raw_args)
            if preflight_payload is not None:
                try:
                    object.__setattr__(args_obj, "_preflight_payload", preflight_payload)
                except Exception:
                    setattr(args_obj, "_preflight_payload", preflight_payload)
            logger.info(f"Executing tool {name} asynchronously with args={args_obj}")
            result = spec.func(args_obj)
            logger.info(f"Async tool {name} completed successfully")
            
            # Log result preview for debugging
            try:
                preview = json.dumps(result)[:500]
            except Exception:
                preview = str(result)[:500]
            logger.debug(f"Async tool {name} result preview: {preview}")
            
            # Store the result in the registry
            if async_id not in _async_tool_registry:
                _async_tool_registry[async_id] = {}
            _async_tool_registry[async_id]['result'] = result
            _async_tool_registry[async_id]['status'] = 'completed'
            
            # Call the callback if registered
            callback = _async_tool_registry.get(async_id, {}).get('callback')
            if callback:
                try:
                    callback(async_id, result)
                except Exception as e:
                    logger.error(f"Error calling async callback for {name}: {e}")
            
        except Exception as e:
            logger.error(f"Async tool {name} failed: {e}")
            _send_error_email(name, raw_args, e)
            
            # Store the error in the registry
            if async_id not in _async_tool_registry:
                _async_tool_registry[async_id] = {}
            _async_tool_registry[async_id]['result'] = {
                "success": False,
                "error": str(e)
            }
            _async_tool_registry[async_id]['status'] = 'failed'
            
            # Call the callback with error
            callback = _async_tool_registry.get(async_id, {}).get('callback')
            if callback:
                try:
                    callback(async_id, {"success": False, "error": str(e)})
                except Exception as cb_e:
                    logger.error(f"Error calling async error callback for {name}: {cb_e}")
    
    # Start the async execution in a background thread
    thread = threading.Thread(target=async_execution, daemon=True)
    thread.start()

    # Allow async workers a brief head-start so tests observing side effects
    # immediately after `execute_tool` return see the expected behaviour.
    try:
        wait_hint = float(os.getenv("RINGDOWN_ASYNC_START_WAIT", "0"))
    except ValueError:
        wait_hint = 0.0
    if wait_hint > 0:
        thread.join(wait_hint)


def execute_tool(name: str, raw_args: Dict[str, Any]) -> Any:
    """Validate *raw_args* against tool schema and execute the tool."""

    if name not in TOOL_REGISTRY:
        raise KeyError(f"Tool '{name}' not registered")

    # Propagate current agent context to thread-local storage so that any
    # Pydantic validators executed in this thread have access to it.
    if _current_agent_context is not None:
        try:
            set_agent_context(_current_agent_context)
        except Exception as exc:
            logger.error(f"Failed to propagate agent context in execute_tool: {exc}")

    spec = TOOL_REGISTRY[name]
    
    # Handle async execution
    if spec.async_execution:
        try:
            # Validate args first to catch errors early
            args_obj = spec.param_model(**raw_args)
        except ValidationError as exc:
            # Validation failure for async tool – treat as a *graceful refusal* not a crash.
            # This is an expected outcome (e.g. recipient not on the green-list), so we
            # log only at DEBUG level to avoid cluttering production logs with warnings.
            # Synchronous tools continue to raise so existing unit tests relying on that
            # behaviour still pass.
            logger.debug("Async tool %s refused with validation error: %s", name, exc)
            return {
                "success": False,
                "async_execution": False,
                "validation_error": True,
                "error": str(exc),
                "tool_name": name,
            }

        preflight_payload = None
        preflight = getattr(spec.func, "preflight_check", None)
        if callable(preflight):
            ready, message, *extra = preflight()
            if extra:
                preflight_payload = extra[0]
            if not ready:
                return {
                    "success": False,
                    "async_execution": False,
                    "disabled": True,
                    "reason": "integration_disabled",
                    "message": message,
                    "tool_name": name,
                }

        # Generate a unique ID for this async execution
        import uuid
        async_id = str(uuid.uuid4())

        # Start async execution with the ID
        _execute_tool_async(name, raw_args, spec, async_id, preflight_payload)
        
        # Return immediately with a pending status that includes the ID
        return {
            "success": True,  # Immediate acknowledgement for caller
            "async_execution": True,
            "async_id": async_id,
            "status": "pending",
            "tool_name": name,
            # Tests expect this exact phrase for quick validation
            "message": f"Tool '{name}' started asynchronously (id={async_id})."
        }
    
    # Handle synchronous execution (existing behavior)
    try:
        args_obj = spec.param_model(**raw_args)
    except ValidationError as exc:
        logger.error("Invalid args for tool %s: %s", name, exc)
        raise

    logger.info(f"Executing tool {name} with args={args_obj}")
    result = spec.func(args_obj)

    # Build a concise preview for logs without assuming the result is
    # iterable.  Fallback to ``str`` when ``len()`` is unsupported.
    try:
        _len = len(result)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 – object has no length
        _len = None

    if _len is not None:
        logger.info(f"Result received. Length: {_len}")
    else:
        logger.info("Result received (scalar value)")

    # Truncate verbose output for hygiene
    try:
        preview = json.dumps(result)[:500]
    except Exception:  # noqa: BLE001
        preview = str(result)[:500]
    logger.debug(preview)

    # Universal truncation for all tool responses
    result = _truncate_tool_response(result)

    return result


def _auto_import_app_tools() -> None:
    """Eagerly import every submodule in *app.tools* so any call to
    :pyfunc:`register_tool` executes and populates ``TOOL_REGISTRY``.

    This avoids having to remember explicit imports elsewhere in the codebase
    (production or tests).  It runs only once at module-import time.
    """

    import importlib
    import pkgutil
    import sys

    try:
        import app.tools as _tools_pkg  # pylint: disable=import-error
    except ModuleNotFoundError:  # pragma: no cover – package missing in unusual envs
        logger.debug("No 'app.tools' package found for auto-import")
        return

    for module_info in pkgutil.walk_packages(_tools_pkg.__path__, prefix=f"{_tools_pkg.__name__}."):
        name = module_info.name
        if name in sys.modules:
            continue  # Already imported, likely via tests
        try:
            importlib.import_module(name)
            logger.debug("Auto-imported tool module '%s'", name)
        except Exception as exc:  # noqa: BLE001 – want to log and continue, not crash app
            logger.exception("Failed to auto-import %s: %s", name, exc)


# Perform discovery immediately so registry is complete for first caller.
_auto_import_app_tools()

# ---------------------------------------------------------------------------
# Agent context propagation                                                     
# ---------------------------------------------------------------------------


def set_agent_context(agent_cfg: Dict[str, Any] | None) -> None:
    """Propagate *agent_cfg* to all registered tools that expose a
    ``set_agent_context`` callable.

    Tools can opt-in by defining a module-level ``set_agent_context`` function
    that accepts a single argument (the agent configuration dict or ``None``).

    This helper lets the chat loop set and later clear context for **all** such
    tools without importing them individually.  New tools therefore get the
    context automatically – no core-loop changes required.
    """
    
    # Store the context at module level for async tools
    global _current_agent_context
    _current_agent_context = agent_cfg
    
    # Log agent context details
    if agent_cfg:
        logger.info(f"Setting agent context with bot_name: {agent_cfg.get('bot_name', 'NOT SET')}")
        logger.debug(f"Full agent context: {agent_cfg}")
    else:
        logger.info("Clearing agent context (set to None)")

    for spec in TOOL_REGISTRY.values():
        mod = inspect.getmodule(spec.func)
        if mod is None:  # pragma: no cover – should not occur
            continue
        setter = getattr(mod, "set_agent_context", None)
        if callable(setter):
            try:
                setter(agent_cfg)
                logger.debug(f"Set agent context on module {mod.__name__}")
            except Exception as exc:  # noqa: BLE001 – do not fail app for one tool
                logger.exception("%s.set_agent_context failed: %s", mod.__name__, exc) 
