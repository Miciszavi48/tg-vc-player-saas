#!/usr/bin/env python3
"""Audit callback routing risks: cross-panel shortcuts, edit paths, duplicate handlers."""

from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLERS_DIR = ROOT / "app" / "handlers"
UI_PATH = ROOT / "app" / "utils" / "ui.py"
INIT_PATH = HANDLERS_DIR / "__init__.py"
REPORT_PATH = ROOT / "docs" / "reports" / "current" / "callback_risk_audit.md"

# Keyboard factories whose buttons may appear on another module's panel message.
ROOT_PANEL_METHODS = {
    "developer_panel": "dev_panel",
    "dev_sub_broadcast": "dev_panel",
    "owner_panel": "owner_panel",
    "sudo_panel": "sudo_panel",
}

# Registered dev_panel shortcuts that fix cross-module taps from developer keyboards.
KNOWN_DEV_SHORTCUTS = {
    "HLP_HOME",
    "AN_HOME",
    "HELP_HOME",
    "BCW_START",
    "BC_HISTORY",
}

# Owner panel entries intentionally delegated to dedicated private panel modules.
# Their callbacks run in PANEL_CALLBACK_GROUP before the generic callback fallback.
KNOWN_DELEGATED_PANEL_KEYS = {
    "OWN_YOUTUBE_SESSIONS",
    "OWN_FAST_CREAT_TOKENS",
}

IGNORED_CROSS_PANEL_KEYS = {
    "NAV_BACK",
    "NAV_CLOSE",
    # nav:start is generic navigation owned by callbacks.nav_start (identity
    # filter ^nav:start$, group 0) — same class as NAV_BACK/NAV_CLOSE. Dispatch
    # reachability is proven by tests/test_project_callback_routing_dispatch.py.
    "NAV_START",
    "SUDO_MY_STATS",
}

SAFE_EDIT_MARKERS = (
    "panel_callback_edit",
    "deliver_panel_outcome",
    "safe_edit_navigation_message",
    "safe_edit_or_send_callback",
)


@dataclass
class CallbackHandler:
    """Extracted callback handler metadata."""

    module: str
    func_name: str
    line: int
    filter_hint: str
    cb_keys: list[str] = field(default_factory=list)
    uses_raw_edit: bool = False
    uses_safe_edit: bool = False


@dataclass
class AuditReport:
    """Collected audit findings."""

    module_order: list[str] = field(default_factory=list)
    cb_values: dict[str, str] = field(default_factory=dict)
    handlers: list[CallbackHandler] = field(default_factory=list)
    cross_panel_risks: list[dict] = field(default_factory=list)
    duplicate_exact: list[dict] = field(default_factory=list)
    edit_path_gaps: list[dict] = field(default_factory=list)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _module_order() -> list[str]:
    source = _read(INIT_PATH)
    match = re.search(r"_MODULES\s*=\s*\[(.*?)\]", source, re.DOTALL)
    if not match:
        return []
    names: list[str] = []
    for line in match.group(1).splitlines():
        stripped = line.strip().rstrip(",")
        if stripped and not stripped.startswith("#"):
            names.append(stripped)
    return names


def _load_cb_dict() -> dict[str, str]:
    source = _read(UI_PATH)
    tree = ast.parse(source, filename=str(UI_PATH))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CB" and isinstance(node.value, ast.Dict):
                    out: dict[str, str] = {}
                    for key_node, val_node in zip(node.value.keys, node.value.values, strict=False):
                        if isinstance(key_node, ast.Constant) and isinstance(val_node, ast.Constant):
                            if isinstance(key_node.value, str) and isinstance(val_node.value, str):
                                out[key_node.value] = val_node.value
                    return out
    return {}


def _keyboard_cb_keys(source: str) -> dict[str, set[str]]:
    """Map KeyboardFactory method name -> CB keys used in its body."""
    tree = ast.parse(source, filename=str(UI_PATH))
    result: dict[str, set[str]] = {}

    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "KeyboardFactory":
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            keys: set[str] = set()
            segment = ast.get_source_segment(source, item) or ""
            for match in re.finditer(r'CB\[\s*["\']([^"\']+)["\']\s*\]', segment):
                keys.add(match.group(1))
            result[item.name] = keys
    return result


def _expr_source(source: str, node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.get_source_segment(source, node) or ""
    except Exception:
        return ""


def _scan_handlers() -> list[CallbackHandler]:
    rows: list[CallbackHandler] = []
    cb_key_re = re.compile(r"""CB\[\s*['"]([^'"]+)['"]\s*\]""")
    for path in sorted(HANDLERS_DIR.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        source = _read(path)
        module = path.stem
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                if not (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "bot"
                    and func.attr == "on_callback_query"
                ):
                    continue
                body_src = ast.get_source_segment(source, node) or ""
                filter_hint = _expr_source(source, dec)
                cb_keys = cb_key_re.findall(filter_hint)
                rows.append(
                    CallbackHandler(
                        module=module,
                        func_name=node.name,
                        line=node.lineno,
                        filter_hint=filter_hint[:160],
                        cb_keys=cb_keys,
                        uses_raw_edit=".edit_text(" in body_src,
                        uses_safe_edit=any(m in body_src for m in SAFE_EDIT_MARKERS),
                    )
                )
    return rows


def _prefix_of(cb_value: str) -> str:
    if ":" in cb_value:
        return cb_value.split(":", 1)[0] + ":"
    return cb_value


def _module_for_cb(cb_key: str, cb_value: str, handlers: list[CallbackHandler]) -> str | None:
    exact = [h for h in handlers if cb_key in h.cb_keys]
    if exact:
        return exact[0].module
    exact_value = [h for h in handlers if f"^{cb_value}$" in h.filter_hint]
    if exact_value:
        return exact_value[0].module
    prefix = _prefix_of(cb_value)
    for h in handlers:
        if prefix.rstrip(":") in h.filter_hint or f"^{re.escape(prefix)}" in h.filter_hint:
            return h.module
    return None


def run_audit() -> AuditReport:
    report = AuditReport()
    report.module_order = _module_order()
    report.cb_values = _load_cb_dict()
    report.handlers = _scan_handlers()
    kb_keys = _keyboard_cb_keys(_read(UI_PATH))
    order_index = {name: i for i, name in enumerate(report.module_order)}

    for kb_method, owner_module in ROOT_PANEL_METHODS.items():
        for cb_key in sorted(kb_keys.get(kb_method, set())):
            if cb_key in IGNORED_CROSS_PANEL_KEYS:
                continue
            cb_value = report.cb_values.get(cb_key)
            if not cb_value:
                continue
            if cb_value.startswith("dev:"):
                continue
            handler_module = _module_for_cb(cb_key, cb_value, report.handlers)
            if handler_module is None:
                report.cross_panel_risks.append(
                    {
                        "keyboard": kb_method,
                        "owner_module": owner_module,
                        "cb_key": cb_key,
                        "cb_value": cb_value,
                        "handler_module": None,
                        "risk": "no_handler",
                    }
                )
                continue
            owner_idx = order_index.get(owner_module, 999)
            handler_idx = order_index.get(handler_module, 999)
            has_dev_shortcut = (
                owner_module == "dev_panel"
                and cb_key in KNOWN_DEV_SHORTCUTS
                and any(
                    h.module == "dev_panel" and h.func_name.startswith("dev_shortcut_")
                    for h in report.handlers
                    if cb_key in h.cb_keys
                )
            )
            is_known_delegation = (
                owner_module == "owner_panel"
                and cb_key in KNOWN_DELEGATED_PANEL_KEYS
            )
            if handler_idx > owner_idx and not has_dev_shortcut and not is_known_delegation:
                report.cross_panel_risks.append(
                    {
                        "keyboard": kb_method,
                        "owner_module": owner_module,
                        "cb_key": cb_key,
                        "cb_value": cb_value,
                        "handler_module": handler_module,
                        "risk": "late_registration",
                    }
                )

    by_cb: dict[str, list[CallbackHandler]] = {}
    for h in report.handlers:
        for key in h.cb_keys:
            by_cb.setdefault(key, []).append(h)
    for key, group in sorted(by_cb.items()):
        modules = {h.module for h in group}
        if len(modules) > 1:
            report.duplicate_exact.append(
                {
                    "cb_key": key,
                    "cb_value": report.cb_values.get(key, ""),
                    "handlers": [f"{h.module}.{h.func_name}" for h in group],
                }
            )

    for h in report.handlers:
        if h.uses_raw_edit and not h.uses_safe_edit:
            report.edit_path_gaps.append(
                {
                    "module": h.module,
                    "handler": h.func_name,
                    "line": h.line,
                }
            )

    return report


def render_markdown(report: AuditReport) -> str:
    lines = [
        "# Callback risk audit (auto-generated)",
        "",
        f"Modules in registration order: **{len(report.module_order)}**",
        f"Callback handlers scanned: **{len(report.handlers)}**",
        "",
        "## Cross-panel shortcut risks",
        "",
    ]
    if not report.cross_panel_risks:
        lines.append("No unresolved cross-panel shortcut risks detected.")
    else:
        lines.extend(
            [
                "| Keyboard | Owner module | Callback | Handler module | Risk |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in report.cross_panel_risks:
            lines.append(
                f"| `{row['keyboard']}` | `{row['owner_module']}` | "
                f"`{row['cb_value']}` | `{row['handler_module']}` | {row['risk']} |"
            )
    lines.extend(["", "## Duplicate exact-match handlers", ""])
    if not report.duplicate_exact:
        lines.append("None.")
    else:
        for row in report.duplicate_exact:
            lines.append(f"- `{row['cb_key']}` (`{row['cb_value']}`): {', '.join(row['handlers'])}")
    lines.extend(["", "## Edit-path gaps (raw edit_text, no safe helper)", ""])
    lines.append(f"Total: **{len(report.edit_path_gaps)}** (informational; not all are bugs)")
    for row in report.edit_path_gaps[:40]:
        lines.append(f"- `{row['module']}.{row['handler']}` (line {row['line']})")
    if len(report.edit_path_gaps) > 40:
        lines.append(f"- … and {len(report.edit_path_gaps) - 40} more")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit callback routing risks")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when unresolved cross-panel risks remain",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        default=True,
        help="Write markdown report (default: on)",
    )
    args = parser.parse_args()

    report = run_audit()
    if args.write_report:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(render_markdown(report), encoding="utf-8")
        print(f"Wrote {REPORT_PATH}")

    unresolved = [r for r in report.cross_panel_risks if r["risk"] != "resolved"]
    print(
        f"cross_panel_risks={len(unresolved)} "
        f"duplicate_exact={len(report.duplicate_exact)} "
        f"edit_path_gaps={len(report.edit_path_gaps)}"
    )
    if args.check and unresolved:
        for row in unresolved:
            print(f"  RISK {row['cb_value']}: {row['owner_module']} -> {row['handler_module']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
