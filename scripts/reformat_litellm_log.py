#!/usr/bin/env python3
"""
Parse a litellm Cloud Run log dump and emit a text file that retains every
field while presenting prompts, tool calls, and responses in a readable order.
"""

from __future__ import annotations

import argparse
import ast
import json
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reformat litellm call logs to highlight prompts and replies."
    )
    parser.add_argument(
        "input_path",
        type=Path,
        help="Path to the JSON log file (e.g. logs/litellm_call_*.json).",
    )
    parser.add_argument(
        "-o",
        "--output-path",
        type=Path,
        help="Where to write the formatted transcript. Defaults to <input>.prompts.txt",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=100,
        help="Wrap width for message bodies (0 disables wrapping).",
    )
    return parser.parse_args()


def read_log(path: Path) -> Sequence[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array in {path}")
    return data


def _slice_list_literal(text: str) -> Optional[str]:
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    in_string: Optional[str] = None
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_string:
                in_string = None
            continue
        if ch in ("'", '"'):
            in_string = ch
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


def _extract_partial_messages(text: str) -> Tuple[List[dict[str, Any]], bool]:
    start = text.find("[")
    if start == -1:
        return [], False
    parsed_messages: List[dict[str, Any]] = []
    in_string: Optional[str] = None
    escape = False
    brace_depth = 0
    dict_start: Optional[int] = None
    truncated = False
    idx = start
    while idx < len(text):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == chr(92):
                escape = True
            elif ch == in_string:
                in_string = None
            idx += 1
            continue
        if ch in ("'", '"'):
            in_string = ch
            idx += 1
            continue
        if ch == "{":
            if brace_depth == 0:
                dict_start = idx
            brace_depth += 1
        elif ch == "}":
            if brace_depth == 0:
                idx += 1
                continue
            brace_depth -= 1
            if brace_depth == 0 and dict_start is not None:
                literal = text[dict_start : idx + 1]
                try:
                    parsed = ast.literal_eval(literal)
                except (ValueError, SyntaxError):
                    truncated = True
                else:
                    if isinstance(parsed, dict):
                        parsed_messages.append(parsed)
                dict_start = None
        idx += 1
    if brace_depth != 0 or dict_start is not None:
        truncated = True
    return parsed_messages, truncated


def extract_messages(text_payload: str) -> Optional[Tuple[List[dict[str, Any]], bool]]:
    marker = "Message array content:"
    marker_index = text_payload.find(marker)
    if marker_index == -1:
        return None
    candidate = text_payload[marker_index + len(marker) :]
    literal = _slice_list_literal(candidate)
    if literal:
        try:
            parsed = ast.literal_eval(literal)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, list):
            return parsed, False
    partial_messages, truncated = _extract_partial_messages(candidate)
    if partial_messages:
        return partial_messages, True
    return None


def normalize_content(value: Any) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return stripped
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        return stripped
    if isinstance(value, (list, dict)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    if value is None:
        return ""
    return str(value)


def wrap_content(content: str, width: int) -> str:
    if width <= 0 or not content:
        return content
    wrapper = textwrap.TextWrapper(
        width=width,
        replace_whitespace=False,
        drop_whitespace=False,
    )
    lines: list[str] = []
    for line in content.splitlines():
        if not line:
            lines.append("")
            continue
        lines.append(wrapper.fill(line))
    return "\n".join(lines)


def shorten_text(text: str, limit: int = 120) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    if limit <= 3:
        return collapsed[:limit]
    return collapsed[: limit - 3] + "..."


MARKER_PREFIX = "RD_MARKER "


def extract_markers(text_payload: str) -> List[dict[str, Any]]:
    markers: List[dict[str, Any]] = []
    for line in text_payload.splitlines():
        pos = line.find(MARKER_PREFIX)
        if pos == -1:
            continue
        candidate = line[pos + len(MARKER_PREFIX) :].strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            markers.append(parsed)
    return markers


def summarize_marker(marker: Dict[str, Any]) -> str:
    event = str(marker.get("event", "UNKNOWN"))
    role = marker.get("role")
    tool = marker.get("tool")
    call_id = marker.get("call_id")
    async_id = marker.get("async_id")
    preview = marker.get("preview") or ""
    preview_text = shorten_text(str(preview)) if preview else ""
    detail = marker.get("detail")
    reason = marker.get("reason")
    classification = marker.get("classification")
    tokens = marker.get("tokens")
    tool_iterations = marker.get("tool_iterations")
    elapsed = marker.get("elapsed_seconds")
    message_index = marker.get("message_index")
    message_count = marker.get("message_count")

    def _append_preview(text: str) -> str:
        if not preview_text:
            return text
        return f"{text} :: {preview_text}"

    if event == "TURN_START":
        base = f"{role or 'user'} input started"
        if message_count is not None:
            base += f" (messages={message_count})"
        return _append_preview(base)
    if event == "TURN_CONTEXT":
        return _append_preview("system prompt refreshed")
    if event == "TURN_END":
        parts: list[str] = []
        if reason:
            parts.append(str(reason))
        if tokens is not None:
            parts.append(f"tokens={tokens}")
        if tool_iterations is not None:
            parts.append(f"tools={tool_iterations}")
        label = "/".join(parts) if parts else "completed"
        return _append_preview(f"{role or 'assistant'} turn {label}")
    if event == "TOOL_QUEUED":
        base = f"{tool or 'tool'} queued"
        if classification:
            base += f" [{classification}]"
        if call_id:
            base += f" (call={call_id})"
        return _append_preview(base)
    if event == "TOOL_EXEC_START":
        text = f"{tool or 'tool'} executing"
        if call_id:
            text += f" (call={call_id})"
        return text
    if event == "TOOL_RESULT":
        label = f"{tool or 'tool'} result"
        if call_id:
            label += f" (call={call_id})"
        if elapsed is not None:
            label += f" in {elapsed:.2f}s"
        return _append_preview(label)
    if event == "ASYNC_PENDING":
        text = f"{tool or 'tool'} async pending"
        if async_id:
            text += f" (async={async_id})"
        if call_id:
            text += f" call={call_id}"
        if message_index is not None:
            text += f" msg_idx={message_index}"
        return _append_preview(text)
    if event == "ASYNC_REGISTERED":
        text = f"async callback registered"
        if async_id:
            text += f" (async={async_id})"
        if call_id:
            text += f" call={call_id}"
        if message_index is not None:
            text += f" msg_idx={message_index}"
        return text
    if event == "ASYNC_COMPLETE":
        text = f"async complete"
        if async_id:
            text += f" (async={async_id})"
        if call_id:
            text += f" call={call_id}"
        if message_index is not None:
            text += f" msg_idx={message_index}"
        return _append_preview(text)
    if event == "ASYNC_RESULT_POLLED":
        text = "async result polled"
        if async_id:
            text += f" (async={async_id})"
        if call_id:
            text += f" call={call_id}"
        if message_index is not None:
            text += f" msg_idx={message_index}"
        return _append_preview(text)
    return _append_preview(f"{event} {detail or ''}".strip())


def format_markers_block(markers: Sequence[dict[str, Any]], width: int) -> str:
    if not markers:
        return ""
    lines: list[str] = []
    last_turn: Any = object()
    for marker in markers:
        turn = marker.get("turn")
        context_id = marker.get("context_id")
        if turn != last_turn:
            label = f"Turn {turn}" if turn is not None else "Turn ?"
            if context_id:
                label += f" (context={context_id})"
            lines.append(f"{label}:")
            last_turn = turn
        summary = wrap_content(summarize_marker(marker), width)
        summary_lines = summary.splitlines() or [""]
        lines.append(f"    - {summary_lines[0]}")
        for extra in summary_lines[1:]:
            lines.append(f"      {extra}")
    return "\n".join(lines)


def format_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def indent_block(text: str, prefix: str = "    ") -> str:
    if not text:
        return prefix
    lines = text.splitlines()
    return "\n".join((prefix + line) if line else prefix for line in lines)


def format_messages_block(
    messages: Sequence[dict[str, Any]],
    width: int,
    truncated: bool,
) -> str:
    lines: list[str] = []
    if truncated:
        lines.append("[Cloud Logging truncated this message array; showing surviving items]")
        lines.append("")
    for message_index, message in enumerate(messages, start=1):
        role = str(message.get("role", "unknown")).upper()
        qualifiers: list[str] = []
        if message.get("tool_calls"):
            qualifiers.append("tool_call")
        if message.get("tool_call_id"):
            qualifiers.append(f"id={message['tool_call_id']}")
        qualifier_text = f" ({', '.join(qualifiers)})" if qualifiers else ""
        lines.append(f"{message_index:02d}. {role}{qualifier_text}")
        content = wrap_content(normalize_content(message.get("content", "")), width)
        lines.append(content or "(no content)")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_log_entry(
    entry_index: int,
    entry: Dict[str, Any],
    width: int,
) -> str:
    timestamp = (
        entry.get("timestamp")
        or entry.get("receiveTimestamp")
        or entry.get("insertId")
        or "unknown timestamp"
    )
    lines: list[str] = [f"=== Entry {entry_index} | {timestamp} ==="]
    handled_keys = {"timestamp"}
    metadata_parts: list[str] = []
    for key in (
        "insertId",
        "severity",
        "logName",
        "receiveTimestamp",
        "trace",
        "spanId",
        "traceSampled",
    ):
        value = entry.get(key)
        if value is not None:
            metadata_parts.append(f"{key}={value}")
            handled_keys.add(key)
    if metadata_parts:
        lines.append("Metadata: " + ", ".join(metadata_parts))
    resource = entry.get("resource")
    if resource is not None:
        lines.append("Resource:")
        lines.append(indent_block(format_json(resource)))
        handled_keys.add("resource")
    http_request = entry.get("httpRequest")
    if http_request is not None:
        lines.append("HTTP Request:")
        lines.append(indent_block(format_json(http_request)))
        handled_keys.add("httpRequest")
    labels = entry.get("labels")
    if labels:
        lines.append("Labels:")
        lines.append(indent_block(format_json(labels)))
        handled_keys.add("labels")
    text_payload = entry.get("textPayload")
    if isinstance(text_payload, str):
        handled_keys.add("textPayload")
        markers = extract_markers(text_payload)
        if markers:
            lines.append("Markers:")
            lines.append(indent_block(format_markers_block(markers, width)))
        extracted = extract_messages(text_payload)
        if extracted:
            messages, truncated = extracted
            lines.append("Parsed Messages:")
            lines.append(indent_block(format_messages_block(messages, width, truncated)))
        lines.append("textPayload (raw):")
        lines.append(indent_block(text_payload.rstrip("\n")))
    json_payload = entry.get("jsonPayload")
    if json_payload is not None:
        lines.append("jsonPayload:")
        lines.append(indent_block(format_json(json_payload)))
        handled_keys.add("jsonPayload")
    proto_payload = entry.get("protoPayload")
    if proto_payload is not None:
        lines.append("protoPayload:")
        lines.append(indent_block(format_json(proto_payload)))
        handled_keys.add("protoPayload")
    other_fields = {
        key: value for key, value in entry.items() if key not in handled_keys
    }
    if other_fields:
        lines.append("Other fields:")
        lines.append(indent_block(format_json(other_fields)))
    return "\n".join(lines).rstrip()


def default_output_path(input_path: Path) -> Path:
    return Path(f"{input_path}.prompts.txt")


def main() -> None:
    args = parse_args()
    entries = read_log(args.input_path)
    indexed_entries = list(enumerate(entries))

    def sort_key(item: Tuple[int, Dict[str, Any]]) -> Tuple[str, int]:
        idx, entry = item
        timestamp = (
            entry.get("timestamp")
            or entry.get("receiveTimestamp")
            or entry.get("insertId")
            or ""
        )
        return timestamp, idx

    indexed_entries.sort(key=sort_key)
    formatted_entries: list[str] = []
    for display_idx, (_, entry) in enumerate(indexed_entries, start=1):
        formatted_entries.append(format_log_entry(display_idx, entry, args.width))
    output_path = args.output_path or default_output_path(args.input_path)
    output_text = "\n\n".join(formatted_entries) + "\n"
    output_path.write_text(output_text, encoding="utf-8")
    print(f"Wrote {len(formatted_entries)} entries to {output_path}")


if __name__ == "__main__":
    main()
