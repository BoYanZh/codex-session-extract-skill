#!/usr/bin/env python3
"""Extract Codex CLI sessions from local rollout.jsonl files only."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PATH_SEPARATORS = re.compile(r"[\\/]+")


class ExtractError(RuntimeError):
    pass


def default_root() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured) if configured else Path.home() / ".codex"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List or extract Codex CLI sessions from local rollout files."
    )
    parser.add_argument("--root", type=Path, default=default_root(), help="Codex home directory")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="Project directory")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="List sessions matching the project")
    action.add_argument("--session", help="Extract an exact or uniquely prefixed session ID")
    action.add_argument("--latest", action="store_true", help="Extract the latest matching session")
    parser.add_argument("--keyword", action="append", default=[], help="Keep matching transcript parts")
    parser.add_argument("--tail", type=int, default=0, help="Keep only the last N matching parts")
    parser.add_argument(
        "--max-part-chars",
        type=int,
        default=8000,
        help="Maximum characters per content field; 0 disables truncation",
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", type=Path, help="Write output atomically instead of printing it")
    return parser.parse_args(argv)


def normalized_path(value: str | Path) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return PATH_SEPARATORS.sub("/", os.path.normcase(str(path))).rstrip("/").casefold()


def normalized_text(value: str) -> str:
    return PATH_SEPARATORS.sub("/", value).casefold()


def nested_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from nested_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from nested_strings(item)


def rollout_files(root: Path) -> list[Path]:
    sessions = root / "sessions"
    if not sessions.is_dir():
        raise ExtractError(f"Codex sessions directory not found: {sessions}")
    return sorted(sessions.rglob("rollout-*.jsonl"))


def read_index(root: Path) -> dict[str, dict[str, str]]:
    path = root / "session_index.jsonl"
    index: dict[str, dict[str, str]] = {}
    if not path.is_file():
        return index
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("id"):
                    index[str(record["id"])] = {
                        "thread_name": str(record.get("thread_name", "")),
                        "updated_at": str(record.get("updated_at", "")),
                    }
    except OSError as error:
        raise ExtractError(f"Cannot read session index: {path}: {error}") from error
    return index


def read_rollout(path: Path, *, require_content: bool) -> list[dict[str, Any]]:
    if not path.is_file():
        if require_content:
            raise ExtractError(
                f"Session rollout is missing: {path}; raw database recovery is unsupported."
            )
        return []
    if path.stat().st_size == 0:
        if require_content:
            raise ExtractError(
                f"Session rollout is empty: {path}; raw database recovery is unsupported."
            )
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ExtractError(
                        f"Invalid rollout JSON at {path}:{line_number}: {error}"
                    ) from error
                if not isinstance(record, dict):
                    raise ExtractError(f"Rollout record must be an object at {path}:{line_number}")
                records.append(record)
    except OSError as error:
        raise ExtractError(f"Cannot read rollout: {path}: {error}") from error
    if require_content and not records:
        raise ExtractError(
            f"Session rollout is empty: {path}; raw database recovery is unsupported."
        )
    return records


def session_meta(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in records:
        if record.get("type") == "session_meta" and isinstance(record.get("payload"), dict):
            return record["payload"]
    return {}


def message_text(payload: dict[str, Any]) -> str:
    content = payload.get("content", [])
    texts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") in ("input_text", "output_text", "text"):
                text = item.get("text", "")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
    return "\n".join(texts).strip()


def session_id_from_path(path: Path, meta: dict[str, Any]) -> str:
    for key in ("session_id", "id"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value
    match = re.match(
        r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(.+)\.jsonl$", path.name
    )
    if match:
        return match.group(1)
    return path.stem.replace("rollout-", "", 1)


def transcript_status(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return "empty" if path.stat().st_size == 0 else "nonempty"
    if not path.is_file():
        return "missing"
    return "empty" if path.stat().st_size == 0 else "nonempty"


def record_mentions_project(record: dict[str, Any], project_key: str) -> bool:
    return any(project_key in normalized_text(value) for value in nested_strings(record))


def tool_project_state(record: dict[str, Any], project_key: str) -> bool | None:
    location_keys = {
        "cwd",
        "directory",
        "file",
        "filepath",
        "path",
        "searchpath",
        "targetfile",
        "workdir",
        "workingdirectory",
    }

    def locations(value: Any) -> Iterable[str]:
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if key.casefold() in location_keys and isinstance(item, str):
                yield item
            if isinstance(item, dict):
                yield from locations(item)
            elif isinstance(item, list):
                for child in item:
                    yield from locations(child)

    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    values = list(locations(payload))
    if not values:
        return None
    for value in values:
        candidate = normalized_text(value.strip().strip("\"'"))
        if candidate == project_key or candidate.startswith(project_key + "/"):
            return True
    return False


def meta_project_state(meta: dict[str, Any], project_key: str) -> bool:
    cwd = meta.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        return False
    candidate = normalized_text(cwd.strip().strip("\"'"))
    return candidate == project_key or candidate.startswith(project_key + "/")


def is_boilerplate_prompt(text: str) -> bool:
    head = text.lstrip()[:64].casefold()
    return head.startswith("# agents.md") or head.startswith("<instructions>")


def summarize_session(
    path: Path,
    records: list[dict[str, Any]],
    index: dict[str, dict[str, str]],
    project_key: str | None,
) -> dict[str, Any] | None:
    status = transcript_status(path)
    meta = session_meta(records)
    session_id = session_id_from_path(path, meta)
    mapped = bool(project_key) and meta_project_state(meta, project_key)
    mentioned = bool(project_key) and any(
        record_mentions_project(record, project_key) for record in records
    )
    if project_key and not mapped and not mentioned:
        return None
    prompts = [
        message_text(record["payload"])
        for record in records
        if record.get("type") == "response_item"
        and isinstance(record.get("payload"), dict)
        and record["payload"].get("type") == "message"
        and record["payload"].get("role") == "user"
    ]
    prompts = [prompt for prompt in prompts if prompt]
    indexed = index.get(session_id, {})
    timestamps = [str(record["timestamp"]) for record in records if record.get("timestamp")]
    modified_ns = path.stat().st_mtime_ns if path.is_file() else 0
    content_prompts = [prompt for prompt in prompts if not is_boilerplate_prompt(prompt)]
    title_source = content_prompts[0] if content_prompts else None
    title = (
        title_source
        or indexed.get("thread_name")
        or (prompts[0][:160] if prompts else "")
        or f"({status} transcript)"
    )
    return {
        "id": session_id,
        "title": title,
        "transcript_status": status,
        "record_count": len(records),
        "first_created_at": timestamps[0] if timestamps else None,
        "last_created_at": timestamps[-1] if timestamps else None,
        "updated_at": indexed.get("updated_at") or None,
        "mapped_to_project": mapped,
        "mentions_project": mentioned,
        "rollout_path": str(path),
        "cwd": meta.get("cwd"),
        "_sort_key": (1 if mapped else 0, timestamps[-1] if timestamps else "", modified_ns),
    }


def list_sessions(root: Path, project: Path | None) -> list[dict[str, Any]]:
    index = read_index(root)
    project_key = normalized_path(project) if project else None
    by_id: dict[str, dict[str, Any]] = {}
    for path in rollout_files(root):
        records = read_rollout(path, require_content=False)
        summary = summarize_session(path, records, index, project_key)
        if summary is not None:
            # Forked continuations share one session ID across several rollout
            # files; keep only the latest file per session.
            current = by_id.get(summary["id"])
            if current is None or summary["_sort_key"] > current["_sort_key"]:
                by_id[summary["id"]] = summary
    sessions = list(by_id.values())
    sessions.sort(key=lambda session: session["_sort_key"], reverse=True)
    for session in sessions:
        session.pop("_sort_key", None)
    return sessions


def select_session(root: Path, project: Path, session_id: str | None) -> dict[str, Any]:
    if session_id:
        sessions = list_sessions(root, None)
        matches = [session for session in sessions if session["id"] == session_id]
        if not matches:
            matches = [session for session in sessions if session["id"].startswith(session_id)]
        if not matches:
            raise ExtractError(f"Codex session not found: {session_id}")
        if len(matches) > 1:
            ids = ", ".join(session["id"] for session in matches)
            raise ExtractError(f"Ambiguous session prefix {session_id}: {ids}")
        return matches[0]
    sessions = list_sessions(root, project)
    if not sessions:
        raise ExtractError(f"No Codex sessions found for project: {project.resolve(strict=False)}")
    return sessions[0]


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0 or len(text) <= limit:
        return text, False
    omitted = len(text) - limit
    return f"{text[:limit]}\n… [truncated {omitted} characters]", True


def project_records(
    records: list[dict[str, Any]], project: Path, session: dict[str, Any]
) -> list[dict[str, Any]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for record in records:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        is_user = (
            record.get("type") == "response_item"
            and payload.get("type") == "message"
            and payload.get("role") == "user"
        )
        if is_user and current:
            turns.append(current)
            current = []
        current.append(record)
    if current:
        turns.append(current)

    project_key = normalized_path(project)
    relevant: list[dict[str, Any]] = []
    for turn in turns:
        prompt = next(
            (
                record
                for record in turn
                if record.get("type") == "response_item"
                and isinstance(record.get("payload"), dict)
                and record["payload"].get("type") == "message"
                and record["payload"].get("role") == "user"
            ),
            None,
        )
        active = bool(prompt and record_mentions_project(prompt, project_key))
        selected: list[dict[str, Any]] = [prompt] if active and prompt else []
        for record in turn:
            if record is prompt or record.get("type") == "session_meta":
                continue
            state = tool_project_state(record, project_key)
            if state is not None:
                if state and not selected and prompt:
                    selected.append(prompt)
                active = state
            if active:
                selected.append(record)
        relevant.extend(selected)
    if relevant:
        return relevant
    if session.get("mapped_to_project") and not any(
        tool_project_state(record, project_key) is not None for record in records
    ):
        return records
    raise ExtractError(
        f"Session {session['id']} has no transcript turns matching project: "
        f"{project.resolve(strict=False)}"
    )


def extract_session(
    root: Path,
    session: dict[str, Any],
    project: Path,
    keywords: list[str],
    tail: int,
    max_part_chars: int,
) -> dict[str, Any]:
    records = read_rollout(Path(session["rollout_path"]), require_content=True)
    records = project_records(records, project, session)
    parts: list[dict[str, Any]] = []
    previous_prompt: str | None = None

    for record in records:
        record_type = str(record.get("type", ""))
        payload = record.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        text = ""
        tool_calls: Any = None
        if record_type == "session_meta":
            text = f"cwd: {payload.get('cwd', '')} | cli: {payload.get('cli_version', '')}"
        elif record_type == "response_item":
            kind = str(payload.get("type", ""))
            if kind == "message":
                text = message_text(payload)
                if payload.get("role") == "user" and (not text or text == previous_prompt):
                    continue
                if payload.get("role") == "user":
                    previous_prompt = text
            elif kind in ("custom_tool_call", "function_call"):
                tool_calls = payload
            elif kind in ("custom_tool_call_output", "function_call_output"):
                tool_calls = payload
            elif kind == "reasoning":
                continue
            else:
                text = json.dumps(payload, ensure_ascii=False)[:2000]
        elif record_type == "event_msg":
            event_type = str(payload.get("type", ""))
            if event_type in ("task_started", "task_complete"):
                text = f"{event_type}: {json.dumps(payload, ensure_ascii=False)[:1000]}"
            else:
                continue
        else:
            continue

        if not text and not tool_calls:
            continue
        part: dict[str, Any] = {
            "timestamp": record.get("timestamp"),
            "ordinal": record.get("ordinal"),
            "source": record_type,
            "type": payload.get("type", record_type),
        }
        if payload.get("role"):
            part["role"] = payload["role"]
        if text:
            part["text"], truncated = truncate_text(text, max_part_chars)
            if truncated:
                part["truncated"] = True
        if tool_calls:
            part["tool_calls"] = tool_calls
        parts.append(part)

    if keywords:
        lowered = [keyword.casefold() for keyword in keywords]
        parts = [
            part
            for part in parts
            if any(keyword in json.dumps(part, ensure_ascii=False).casefold() for keyword in lowered)
        ]
    if tail > 0:
        parts = parts[-tail:]

    prompts = [part["text"] for part in parts if part.get("role") == "user" and part.get("text")]
    return {"session": session, "prompts": prompts, "parts": parts}


def render_markdown(payload: dict[str, Any]) -> str:
    session = payload["session"]
    lines = [
        f"# Codex session: {session['id']}",
        "",
        f"- Title: {session['title']}",
        f"- Last activity: {session.get('updated_at') or session.get('last_created_at') or 'unknown'}",
        f"- Transcript records: {session['record_count']}",
        f"- Workdir: {session.get('cwd') or 'unknown'}",
        "",
        "## Timeline",
        "",
    ]
    for part in payload["parts"]:
        label = f"{part.get('source') or 'UNKNOWN'} / {part.get('type') or 'UNKNOWN'}"
        if part.get("role"):
            label += f" / {part['role']}"
        lines.extend([f"### {label}", ""])
        if part.get("text"):
            lines.extend([part["text"], ""])
        if part.get("tool_calls"):
            lines.extend(
                ["```json", json.dumps(part["tool_calls"], ensure_ascii=False, indent=2), "```", ""]
            )
    return "\n".join(lines).rstrip() + "\n"


def serialize(value: Any, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    return render_markdown(value)


def write_output(path: Path, content: str) -> None:
    path = path.expanduser().resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = args.root.expanduser().resolve(strict=False)
        project = args.project.expanduser()
        if not project.is_absolute():
            project = Path.cwd() / project
        if args.list:
            result: Any = list_sessions(root, project)
            content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        else:
            session = select_session(root, project, args.session)
            result = extract_session(root, session, project, args.keyword, args.tail, args.max_part_chars)
            content = serialize(result, args.format)
        if args.output:
            write_output(args.output, content)
        else:
            print(content, end="")
        return 0
    except ExtractError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
