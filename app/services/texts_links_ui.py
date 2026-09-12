from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.repositories import settings_repo
from app.services import start_customization_service as start_custom
from app.utils.ask_result import AskResult  # noqa: F401 - compatibility re-export for tests/callers
from app.utils.button_style import mark_toggle_state
from app.utils.i18n import t
from app.utils.ui import CB, _chunk_buttons


@dataclass(frozen=True)
class TextFieldSpec:
    key: str
    label_key: str
    kind: str
    runtime_status: str
    location_keys: tuple[str, ...]
    global_effect_keys: tuple[str, ...]
    owner_effect_keys: tuple[str, ...]
    media_note_key: str | None = None
    note_keys: tuple[str, ...] = ()
    owner_editable: bool = True


_LINK_VALUE_RE = re.compile(r"^(https?://|t\.me/|tg://)", re.IGNORECASE)


FIELD_SPECS: tuple[TextFieldSpec, ...] = (
    TextFieldSpec(
        "start_text",
        "texts_links.start_text",
        "text",
        "active_global",
        ("private_start_message",),
        ("global_private_start_message",),
        ("owner_private_start_message",),
        "caption_only",
        (),
    ),
    TextFieldSpec(
        "helper_text",
        "texts_links.helper_text",
        "text",
        "storage_only",
        ("storage_only",),
        ("storage_only_no_runtime",),
        ("storage_only_no_runtime",),
        "media_storage_only",
    ),
    TextFieldSpec(
        "about_text",
        "texts_links.about_text",
        "text",
        "active_global",
        ("about_callback",),
        ("global_about_callback",),
        ("owner_saved_about_unused",),
        "caption_only",
    ),
    TextFieldSpec(
        "tariff_text",
        "texts_links.tariff_text",
        "text",
        "storage_only",
        ("storage_only",),
        ("storage_only_no_runtime",),
        ("owner_saved_tariff_unused",),
        "media_storage_only",
    ),
    TextFieldSpec(
        "helper_start_text",
        "texts_links.helper_start_text",
        "text",
        "storage_only",
        ("storage_only",),
        ("storage_only_no_runtime",),
        ("storage_only_no_runtime",),
        "media_storage_only",
    ),
    TextFieldSpec(
        "developer_link",
        "texts_links.developer_link",
        "link",
        "active_owner_override",
        ("start_buttons", "about_callback", "group_support"),
        ("global_creator_link_fallback",),
        ("owner_group_support_creator", "owner_private_start_unused"),
        None,
        ("developer_link_priority_note",),
    ),
    TextFieldSpec(
        "bot_channel_link",
        "texts_links.bot_channel_link",
        "link",
        "active_owner_override",
        ("start_buttons", "install_post_install"),
        ("global_start_button_install_fallback",),
        ("owner_install_bot_channel", "owner_private_start_unused"),
    ),
    TextFieldSpec(
        "support_group_link",
        "texts_links.support_group_link",
        "link",
        "active_owner_override",
        ("start_buttons", "group_support"),
        ("global_start_button_group_fallback",),
        ("owner_group_support", "owner_private_start_unused"),
    ),
    TextFieldSpec(
        "guide_channel_link",
        "texts_links.guide_channel_link",
        "link",
        "active_owner_override",
        ("start_buttons", "group_support", "install_post_install"),
        ("global_start_button_group_install_fallback",),
        ("owner_group_support", "owner_install_guide", "owner_private_start_unused"),
    ),
    TextFieldSpec(
        "developer_pv_link",
        "texts_links.developer_pv_link",
        "link",
        "active_owner_override",
        ("start_buttons", "about_callback", "group_support"),
        ("global_primary_creator_link",),
        ("owner_group_support_creator", "owner_private_start_unused"),
        None,
        ("developer_pv_priority_note",),
    ),
    TextFieldSpec(
        "broadcast_channel_link",
        "texts_links.broadcast_channel_link",
        "link",
        "storage_only",
        ("storage_only",),
        ("storage_only_no_runtime",),
        ("storage_only_no_runtime",),
    ),
    TextFieldSpec(
        "custom_link",
        "texts_links.custom_link",
        "link",
        "active_global",
        ("start_buttons",),
        ("global_custom_start_button",),
        ("owner_saved_start_button_unused",),
    ),
    TextFieldSpec(
        "sudo_link_1",
        "texts_links.sudo_link_1",
        "link",
        "active_global",
        ("start_buttons",),
        ("global_sudo_buy_link",),
        ("owner_saved_sudo_unused",),
    ),
    TextFieldSpec(
        "sudo_link_2",
        "texts_links.sudo_link_2",
        "link",
        "active_global",
        ("start_buttons",),
        ("global_sudo_buy_link",),
        ("owner_saved_sudo_unused",),
    ),
)

_FIELD_MAP: dict[str, TextFieldSpec] = {f.key: f for f in FIELD_SPECS}

_OWNER_LABEL_OVERRIDES: dict[str, str] = {
    "start_text": "texts_links.owner_labels.start_text",
    "developer_link": "texts_links.owner_labels.developer_link",
    "developer_pv_link": "texts_links.owner_labels.developer_pv_link",
    "bot_channel_link": "texts_links.owner_labels.bot_channel_link",
    "custom_link": "texts_links.owner_labels.custom_link",
}

_SOURCE_LABEL_KEYS: dict[str, str] = {
    "owner": "texts_links.source_owner",
    "global": "texts_links.source_global",
    "empty": "texts_links.source_empty",
}


def _btn(label: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=callback_data)


def _role_cfg(role: str) -> dict[str, str]:
    if role == "owner":
        return {
            "home": CB["OWN_TEXTS_HOME"],
            "field_prefix": CB["OWN_TEXT_FIELD_PREFIX"],
            "set_text_prefix": CB["OWN_TEXT_SET_TEXT_PREFIX"],
            "set_media_prefix": CB["OWN_TEXT_SET_MEDIA_PREFIX"],
            "clear_prefix": CB["OWN_TEXT_CLEAR_PREFIX"],
            "preview_prefix": CB["OWN_TEXT_PREVIEW_PREFIX"],
            "style_toggle": CB["OWN_START_STYLE_TOGGLE"],
            "style_toggle_label": "texts_links.start_style.toggle_button_owner",
            "style_scope_note": "texts_links.start_style.owner_scope_note",
            "back": CB["OWN_TEXTS_BACK"],
        }
    return {
        "home": CB["DEV_TEXTS_HOME"],
        "field_prefix": CB["DEV_TEXT_FIELD_PREFIX"],
        "set_text_prefix": CB["DEV_TEXT_SET_TEXT_PREFIX"],
        "set_media_prefix": CB["DEV_TEXT_SET_MEDIA_PREFIX"],
        "clear_prefix": CB["DEV_TEXT_CLEAR_PREFIX"],
        "preview_prefix": CB["DEV_TEXT_PREVIEW_PREFIX"],
        "style_toggle": CB["DEV_START_STYLE_TOGGLE"],
        "style_toggle_label": "texts_links.start_style.toggle_button_developer",
        "style_scope_note": "texts_links.start_style.developer_scope_note",
        "back": CB["DEV_TEXTS_BACK"],
    }


def get_field_spec(field: str) -> TextFieldSpec | None:
    return _FIELD_MAP.get(field)


def field_label_key(spec: TextFieldSpec, role: str) -> str:
    """Return the i18n label key for a field in global or owner scope."""
    if role == "owner" and spec.key in _OWNER_LABEL_OVERRIDES:
        return _OWNER_LABEL_OVERRIDES[spec.key]
    return spec.label_key


def is_storage_only_field(spec: TextFieldSpec) -> bool:
    return spec.runtime_status == "storage_only"


def is_owner_editable_field(spec: TextFieldSpec) -> bool:
    return spec.owner_editable


def field_runtime_status_key(spec: TextFieldSpec) -> str:
    return f"texts_links.runtime_status.{spec.runtime_status}"


def _field_kind_key(spec: TextFieldSpec) -> str:
    return f"texts_links.kind_{spec.kind}"


def _runtime_location_lines(lang: str, spec: TextFieldSpec) -> list[str]:
    lines = [t(lang, "texts_links.runtime_locations_title")]
    for key in spec.location_keys:
        lines.append(t(lang, f"texts_links.runtime_locations.{key}"))
    return lines


def _runtime_effect_lines(lang: str, spec: TextFieldSpec, role: str) -> list[str]:
    effect_keys = spec.owner_effect_keys if role == "owner" else spec.global_effect_keys
    lines = [t(lang, "texts_links.runtime_effect_title")]
    for key in effect_keys:
        lines.append(t(lang, f"texts_links.runtime_effects.{key}"))
    return lines


def _runtime_note_lines(lang: str, spec: TextFieldSpec) -> list[str]:
    lines: list[str] = []
    for key in spec.note_keys:
        lines.append(t(lang, f"texts_links.runtime_notes.{key}"))
    if spec.media_note_key:
        lines.append(t(lang, f"texts_links.media_runtime.{spec.media_note_key}"))
    return lines


def confirmation_key_for_save(
    spec: TextFieldSpec,
    *,
    media: bool = False,
    scope: str = "global",
) -> str:
    """Return i18n key for a successful save confirmation."""
    if scope == "owner":
        if media:
            return "texts_links.owner_saved_media"
        if spec.kind == "link":
            return "texts_links.owner_saved_link"
        return "texts_links.owner_saved_text"
    if media:
        return "texts_links.saved_media"
    if spec.kind == "link":
        return "texts_links.saved_link"
    return "texts_links.saved_text"


def confirmation_key_for_clear(*, scope: str = "global") -> str:
    """Return i18n key for a successful clear confirmation."""
    if scope == "owner":
        return "texts_links.cleared_override"
    return "texts_links.cleared"


def normalize_link_value(value: str) -> str:
    """Trim whitespace from a link field value."""
    return (value or "").strip()


def is_valid_link_value(value: str) -> bool:
    """Return True when value looks like a usable Telegram URL button target."""
    normalized = normalize_link_value(value)
    return bool(normalized) and bool(_LINK_VALUE_RE.match(normalized))


def all_field_keys() -> tuple[str, ...]:
    return tuple(f.key for f in FIELD_SPECS)


def decode_setting_value(raw: str | None) -> dict[str, Any]:
    if raw is None or str(raw).strip() == "":
        return {"mode": "empty"}

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("__mode") == "media" and data.get("file_id"):
            return {
                "mode": "media",
                "file_id": str(data.get("file_id")),
                "caption": str(data.get("caption") or ""),
            }
    except Exception:
        pass

    return {"mode": "text", "value": str(raw)}


def encode_media_value(file_id: str, caption: str | None) -> str:
    return json.dumps(
        {
            "__mode": "media",
            "file_id": file_id,
            "caption": (caption or "").strip(),
        },
        ensure_ascii=False,
    )


def extract_media_payload(message: Message) -> tuple[str | None, str]:
    file_id: str | None = None

    photo = getattr(message, "photo", None)
    if photo is not None:
        if isinstance(photo, list) and photo:
            file_id = getattr(photo[-1], "file_id", None)
        else:
            file_id = getattr(photo, "file_id", None)

    if file_id is None:
        document = getattr(message, "document", None)
        if document is not None:
            file_id = getattr(document, "file_id", None)

    caption = (getattr(message, "caption", None) or "").strip()
    return file_id, caption


def _shorten(text: str, limit: int = 200) -> str:
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _state_key(parsed: dict[str, Any]) -> str:
    mode = parsed.get("mode")
    if mode == "media":
        return "texts_links.state_media_set"
    if mode == "text":
        return "texts_links.state_set"
    return "texts_links.state_empty"


def _parsed_from_effective(effective: object) -> dict[str, Any]:
    """Convert an effective scoped value into decode_setting_value shape."""
    if effective.mode == "empty":
        return {"mode": "empty"}
    if effective.mode == "media":
        return decode_setting_value(effective.raw)
    if effective.mode == "link":
        return {"mode": "text", "value": effective.raw or ""}
    return {"mode": "text", "value": effective.raw or ""}


async def _resolve_field_value(
    role: str,
    spec: TextFieldSpec,
    *,
    owner_user_id: int | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Return parsed value and optional source label key for scoped reads."""
    if role == "owner" and owner_user_id is not None:
        if not is_owner_editable_field(spec):
            current = await settings_repo.get_bot_setting(spec.key)
            parsed = decode_setting_value(current)
            source = "empty" if parsed.get("mode") == "empty" else "global"
            return parsed, source

        from app.services import owner_text_link_service

        effective = await owner_text_link_service.get_effective_text_link(
            spec.key,
            owner_user_id=owner_user_id,
        )
        return _parsed_from_effective(effective), effective.source

    current = await settings_repo.get_bot_setting(spec.key)
    return decode_setting_value(current), None


def _source_label(lang: str, source: str | None) -> str:
    if not source:
        return ""
    key = _SOURCE_LABEL_KEYS.get(source)
    return t(lang, key) if key else ""


def _style_mode_label(lang: str, mode: str) -> str:
    if mode == start_custom.STYLE_ADVANCED:
        return t(lang, "texts_links.start_style.mode_advanced")
    return t(lang, "texts_links.start_style.mode_simple")


def _style_source_label(lang: str, source_scope: str) -> str:
    key = {
        "owner": "texts_links.start_style.scope_owner",
        "global": "texts_links.start_style.scope_global",
        "default": "texts_links.start_style.scope_default",
    }.get(source_scope, "texts_links.start_style.scope_default")
    return t(lang, key)


async def build_texts_hub_payload(
    lang: str,
    role: str,
    *,
    owner_user_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    cfg = _role_cfg(role)
    active_text_specs = [f for f in FIELD_SPECS if f.kind == "text" and not is_storage_only_field(f)]
    active_link_specs = [f for f in FIELD_SPECS if f.kind == "link" and not is_storage_only_field(f)]
    storage_specs = [f for f in FIELD_SPECS if is_storage_only_field(f)]

    hub_title = (
        t(lang, "texts_links.owner_hub_title")
        if role == "owner"
        else t(lang, "texts_links.hub_title")
    )
    lines = [hub_title]
    style = await start_custom.get_effective_style_mode(
        owner_user_id=owner_user_id if role == "owner" else None,
    )
    next_mode = start_custom.next_style_mode(style.mode)
    if role == "owner":
        lines.extend(["", t(lang, "texts_links.owner_scope_note")])
    else:
        lines.append("")

    lines.extend([
        t(lang, "texts_links.start_style.title"),
        t(
            lang,
            "texts_links.start_style.line",
            mode=_style_mode_label(lang, style.mode),
            source=_style_source_label(lang, style.source_scope),
        ),
        t(lang, cfg["style_scope_note"]),
        t(
            lang,
            "texts_links.start_style.color_guide",
            palette="".join(start_custom.DEFAULT_ADVANCED_COLOR_SEQUENCE),
        ),
        t(lang, "texts_links.start_style.clear_guide"),
        "",
        t(lang, "texts_links.group_text"),
    ])

    text_buttons: list[InlineKeyboardButton] = []
    for spec in active_text_specs:
        parsed, source = await _resolve_field_value(role, spec, owner_user_id=owner_user_id)
        label = field_label_key(spec, role)
        if role == "owner" and source:
            lines.append(
                t(
                    lang,
                    "texts_links.hub_item_scoped_runtime",
                    field=t(lang, label),
                    state=t(lang, _state_key(parsed)),
                    source=_source_label(lang, source),
                    status=t(lang, field_runtime_status_key(spec)),
                )
            )
        else:
            lines.append(
                t(
                    lang,
                    "texts_links.hub_item_runtime",
                    field=t(lang, label),
                    state=t(lang, _state_key(parsed)),
                    status=t(lang, field_runtime_status_key(spec)),
                )
            )
        text_buttons.append(_btn(t(lang, label), f"{cfg['field_prefix']}{spec.key}"))

    lines.extend(["", t(lang, "texts_links.group_link")])
    link_buttons: list[InlineKeyboardButton] = []
    for spec in active_link_specs:
        parsed, source = await _resolve_field_value(role, spec, owner_user_id=owner_user_id)
        label = field_label_key(spec, role)
        if role == "owner" and source:
            lines.append(
                t(
                    lang,
                    "texts_links.hub_item_scoped_runtime",
                    field=t(lang, label),
                    state=t(lang, _state_key(parsed)),
                    source=_source_label(lang, source),
                    status=t(lang, field_runtime_status_key(spec)),
                )
            )
        else:
            lines.append(
                t(
                    lang,
                    "texts_links.hub_item_runtime",
                    field=t(lang, label),
                    state=t(lang, _state_key(parsed)),
                    status=t(lang, field_runtime_status_key(spec)),
                )
            )
        link_buttons.append(_btn(t(lang, label), f"{cfg['field_prefix']}{spec.key}"))

    lines.extend(["", t(lang, "texts_links.group_storage_only"), t(lang, "texts_links.storage_only_section_note")])
    storage_buttons: list[InlineKeyboardButton] = []
    for spec in storage_specs:
        parsed, source = await _resolve_field_value(role, spec, owner_user_id=owner_user_id)
        label = field_label_key(spec, role)
        if role == "owner" and source:
            lines.append(
                t(
                    lang,
                    "texts_links.hub_item_scoped_runtime",
                    field=t(lang, label),
                    state=t(lang, _state_key(parsed)),
                    source=_source_label(lang, source),
                    status=t(lang, field_runtime_status_key(spec)),
                )
            )
        else:
            lines.append(
                t(
                    lang,
                    "texts_links.hub_item_runtime",
                    field=t(lang, label),
                    state=t(lang, _state_key(parsed)),
                    status=t(lang, field_runtime_status_key(spec)),
                )
            )
        storage_buttons.append(_btn(t(lang, label), f"{cfg['field_prefix']}{spec.key}"))

    style_toggle_button = _btn(
        t(
            lang,
            cfg["style_toggle_label"],
            mode=_style_mode_label(lang, next_mode),
        ),
        cfg["style_toggle"],
    )
    mark_toggle_state(
        style_toggle_button,
        style.mode == start_custom.STYLE_ADVANCED,
    )
    rows = [[style_toggle_button]]
    rows.extend(_chunk_buttons(text_buttons, size=2))
    rows.extend(_chunk_buttons(link_buttons, size=2))
    rows.extend(_chunk_buttons(storage_buttons, size=2))
    rows.append([_btn(t(lang, "common.buttons.back"), cfg["back"])])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def build_text_field_payload(
    lang: str,
    role: str,
    field: str,
    *,
    owner_user_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    spec = get_field_spec(field)
    if spec is None:
        return await build_texts_hub_payload(lang, role, owner_user_id=owner_user_id)

    cfg = _role_cfg(role)
    parsed, source = await _resolve_field_value(role, spec, owner_user_id=owner_user_id)
    label = field_label_key(spec, role)

    lines = [
        t(lang, "texts_links.field_title", field=t(lang, label)),
        "",
        t(lang, "texts_links.kind_line", kind=t(lang, _field_kind_key(spec))),
        t(lang, field_runtime_status_key(spec)),
    ]

    if role == "owner" and source == "global":
        lines.append("")
        lines.append(t(lang, "texts_links.using_global_fallback"))
    elif role == "owner" and source == "owner":
        lines.append("")
        lines.append(t(lang, "texts_links.source_owner"))
    elif role == "owner" and source == "empty":
        lines.append("")
        lines.append(t(lang, "texts_links.source_empty"))

    lines.append("")
    lines.extend(_runtime_location_lines(lang, spec))
    lines.append("")
    lines.extend(_runtime_effect_lines(lang, spec, role))

    note_lines = _runtime_note_lines(lang, spec)
    if note_lines:
        lines.append("")
        lines.extend(note_lines)

    owner_read_only = role == "owner" and not is_owner_editable_field(spec)
    if owner_read_only:
        lines.append("")
        lines.append(t(lang, "texts_links.owner_read_only_global"))

    mode = parsed.get("mode")
    lines.append("")
    if mode == "empty":
        lines.append(t(lang, "texts_links.preview_empty"))
    elif mode == "media":
        cap = _shorten(str(parsed.get("caption") or ""), 200)
        if cap:
            lines.append(t(lang, "texts_links.preview_media_with_caption", caption=cap))
        else:
            lines.append(t(lang, "texts_links.preview_media_no_caption"))
    else:
        value = _shorten(str(parsed.get("value") or ""), 200)
        if spec.kind == "link":
            lines.append(t(lang, "texts_links.preview_link", value=value))
        else:
            lines.append(t(lang, "texts_links.preview_text", value=value))

    if owner_read_only:
        rows = []
    elif spec.kind == "link":
        rows = [
            [
                _btn(t(lang, "texts_links.buttons.set_link"), f"{cfg['set_text_prefix']}{field}"),
                _btn(t(lang, "texts_links.buttons.clear"), f"{cfg['clear_prefix']}{field}"),
            ],
        ]
    else:
        rows = [
            [
                _btn(t(lang, "texts_links.buttons.set_text"), f"{cfg['set_text_prefix']}{field}"),
                _btn(t(lang, "texts_links.buttons.set_media"), f"{cfg['set_media_prefix']}{field}"),
            ],
            [_btn(t(lang, "texts_links.buttons.clear"), f"{cfg['clear_prefix']}{field}")],
        ]
        if mode == "media":
            rows.append([_btn(t(lang, "texts_links.buttons.preview_photo"), f"{cfg['preview_prefix']}{field}")])

    rows.append([_btn(t(lang, "common.buttons.back"), cfg["home"])])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


def role_return_token(role: str) -> str:
    if role == "owner":
        return "owner_texts"
    return "dev_texts"


def field_from_callback(data: str, prefix: str) -> str | None:
    if not data.startswith(prefix):
        return None
    field = data[len(prefix) :]
    if get_field_spec(field) is None:
        return None
    return field
