#!/usr/bin/env python3
"""Extract @bot.on_message / @bot.on_callback_query registrations from app/handlers."""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLERS_DIR = ROOT / "app" / "handlers"


@dataclass
class HandlerRow:
    """One registered handler extracted from source."""

    module: str
    kind: str
    func_name: str
    group: str
    filter_hint: str
    line: int
    notes: str = ""


@dataclass
class AuditResult:
    """Collected handler rows."""

    messages: list[HandlerRow] = field(default_factory=list)
    callbacks: list[HandlerRow] = field(default_factory=list)


def _expr_source(source: str, node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.get_source_segment(source, node) or ""
    except Exception:
        return ""


def _filter_hint(source: str, decorator: ast.Call) -> str:
    parts: list[str] = []
    for arg in decorator.args:
        text = _expr_source(source, arg).strip()
        if text:
            parts.append(text[:120])
    for kw in decorator.keywords:
        if kw.arg == "group":
            parts.append(f"group={_expr_source(source, kw.value).strip()}")
    return " & ".join(parts) if parts else "(no filter)"


def _group_from_decorator(source: str, decorator: ast.Call) -> str:
    for kw in decorator.keywords:
        if kw.arg == "group":
            return _expr_source(source, kw.value).strip() or "0"
    return "0"


def _scan_file(path: Path) -> AuditResult:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    module = path.relative_to(ROOT).as_posix().replace("/", ".")
    result = AuditResult()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not isinstance(func, ast.Attribute):
                continue
            if not (
                isinstance(func.value, ast.Name)
                and func.value.id == "bot"
                and func.attr in ("on_message", "on_callback_query")
            ):
                continue
            kind = "message" if func.attr == "on_message" else "callback"
            row = HandlerRow(
                module=module,
                kind=kind,
                func_name=node.name,
                group=_group_from_decorator(source, dec),
                filter_hint=_filter_hint(source, dec),
                line=node.lineno,
            )
            if kind == "message":
                result.messages.append(row)
            else:
                result.callbacks.append(row)
    return result


def collect_handlers() -> AuditResult:
    """Walk all handler modules and merge rows."""
    merged = AuditResult()
    for path in sorted(HANDLERS_DIR.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        partial = _scan_file(path)
        merged.messages.extend(partial.messages)
        merged.callbacks.extend(partial.callbacks)
    merged.messages.sort(key=lambda r: (int(r.group) if r.group.lstrip("-").isdigit() else 0, r.module, r.line))
    merged.callbacks.sort(key=lambda r: (int(r.group) if r.group.lstrip("-").isdigit() else 0, r.module, r.line))
    return merged


def _md_table(rows: list[HandlerRow], kind: str) -> str:
    lines = [
        f"## {kind.title()} handlers",
        "",
        "| Module | Function | Group | Filter | Line |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        filt = row.filter_hint.replace("|", "\\|")
        lines.append(
            f"| `{row.module}` | `{row.func_name}` | {row.group} | `{filt}` | {row.line} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_markdown(result: AuditResult) -> str:
    """Render audit tables as markdown."""
    parts = [
        "# Handler map (auto-generated)",
        "",
        f"Total message handlers: **{len(result.messages)}**",
        f"Total callback handlers: **{len(result.callbacks)}**",
        "",
        _md_table(result.messages, "message"),
        _md_table(result.callbacks, "callback"),
    ]
    return "\n".join(parts)


def main() -> int:
    result = collect_handlers()
    out_path = ROOT / "docs" / "reports" / "current" / "handler_command_callback_playback_fix_report.md"
    existing = ""
    if out_path.exists():
        existing = out_path.read_text(encoding="utf-8")
        marker = "## Handler map (auto-generated)"
        if marker in existing:
            existing = existing.split(marker)[0].rstrip() + "\n\n"

    body = existing + render_markdown(result)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    print(f"Wrote {out_path} ({len(result.messages)} msg, {len(result.callbacks)} cb)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
