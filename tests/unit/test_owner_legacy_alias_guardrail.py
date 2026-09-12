"""Legacy Owner callback aliases must stay route-compatible but unemitted.

The ``own:cat:*`` category aliases predate the current Owner root layout. They
must remain wired to handlers (so old inline keyboards / deep links keep
working) yet must never be produced by any current Owner keyboard builder.
"""
from __future__ import annotations

import sys
from types import ModuleType

import pytest

if "pyromod" not in sys.modules:  # pragma: no cover - test shim
    _pyromod = ModuleType("pyromod")
    _pyromod_exc = ModuleType("pyromod.exceptions")

    class _ListenerStopped(Exception):
        pass

    _pyromod_exc.ListenerStopped = _ListenerStopped
    _pyromod.exceptions = _pyromod_exc
    sys.modules["pyromod"] = _pyromod
    sys.modules["pyromod.exceptions"] = _pyromod_exc

from app.handlers import owner_panel  # noqa: E402
from app.utils.ui import CB, KeyboardFactory  # noqa: E402

# Legacy alias -> the handler expected to keep serving it.
_LEGACY_ALIASES = {
    "OWN_CAT_INSTALLS": "own_cat_installs",
    "OWN_CAT_BROADCAST": "own_cat_broadcast",
    "OWN_CAT_SETTINGS": "own_cat_settings",
    "OWN_CAT_USERS": "own_cat_users",
    "OWN_CAT_REPORTS": "own_cat_reports",
    "OWN_CAT_BILLING": "own_cat_billing",
    "OWN_CAT_TEXTS": "own_cat_texts",
}

_OWNER_KEYBOARDS = (
    lambda: KeyboardFactory.owner_panel("fa"),
    lambda: KeyboardFactory.owner_sub_groups("fa"),
    lambda: KeyboardFactory.owner_sub_credit("fa"),
    lambda: KeyboardFactory.owner_sub_lists("fa"),
    lambda: KeyboardFactory.owner_sub_moderation("fa"),
    lambda: KeyboardFactory.owner_sub_media("fa"),
    lambda: KeyboardFactory.owner_sub_sudo_titles("fa"),
    lambda: KeyboardFactory.owner_sub_installs("fa"),
    lambda: KeyboardFactory.owner_sub_broadcast("fa"),
    lambda: KeyboardFactory.owner_sub_settings("fa"),
    lambda: KeyboardFactory.owner_sub_users("fa"),
    lambda: KeyboardFactory.owner_sub_reports("fa"),
    lambda: KeyboardFactory.owner_sub_billing("fa"),
    lambda: KeyboardFactory.owner_sub_texts("fa"),
)


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []
        self.other_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):
            def _register(*args, **kwargs):  # noqa: ANN002, ANN003
                def _decorator(fn):
                    self.other_handlers.append(fn)
                    return fn

                return _decorator

            return _register
        raise AttributeError(name)


def _all_owner_callbacks() -> set[str]:
    cbs: set[str] = set()
    for factory in _OWNER_KEYBOARDS:
        kb = factory()
        cbs.update(
            btn.callback_data
            for row in kb.inline_keyboard
            for btn in row
            if getattr(btn, "callback_data", None)
        )
    return cbs


def test_legacy_aliases_are_not_emitted_by_any_owner_keyboard():
    emitted = _all_owner_callbacks()
    for const in _LEGACY_ALIASES:
        assert CB[const] not in emitted, f"{const} should not be emitted anymore"


def test_legacy_aliases_remain_routed():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler_names = {fn.__name__ for fn in bot.callback_handlers}
    for const, handler in _LEGACY_ALIASES.items():
        assert handler in handler_names, f"{const} ({handler}) lost its route"


@pytest.mark.parametrize("const", sorted(_LEGACY_ALIASES))
def test_legacy_alias_values_are_own_scoped(const: str):
    # Aliases keep the own:* namespace so they can never collide with dev:* routes.
    assert CB[const].startswith("own:")
