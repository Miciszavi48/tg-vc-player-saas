# Help Reference Missing / Later-Phase Commands

This file lists Help commands that are still not fully implemented as real runtime behavior. Customer-provided Help copy (2026-06-20) and `app/resources/i18n/*/system/help.json` are the canonical Help text sources. Fully implemented commands are intentionally not listed here as missing. Placeholder commands remain listed with the exact caveat.

| Help section | Persian command | English command | Help page implemented | Runtime handler found | Runtime supported | Status | Search evidence | Notes |
|---|---|---:|---:|---:|---:|---|---|---|
| Serial playback | `پخش سریال` | `Serial Play` | yes | placeholder only | partial | coming soon placeholder | `app/handlers/playback.py`; `rg "پخش سریال|Serial Play|serial" app tests app/resources docs` | Exact no-argument commands return coming-soon feedback. Help text preserves customer copy plus an explicit coming-soon note. |

## Notes

- Speed controls are not missing: exact text commands and `pb:speed:+/-` now perform bounded speed changes for trusted finite local media and already cached finite URL/YouTube audio/video originals.
- Volume step controls are not missing: exact text commands and `pb:vol:+/-` share the bounded runtime volume path.
- Seek controls are not missing: exact text commands perform real finite-source seek through safe stream replacement. Unsupported live/uncached sources return explicit feedback.
- Public group/user commands, Channel ID, Show-ID toggles, and VIP list/clear are now routed through existing group settings/VIP infrastructure.
- Real serial playback remains a later implementation phase.
- Utility Help (`#کاربردی`) is informational only; customer copy for Text/Cover contains incomplete placeholders (`...`, `..`).
