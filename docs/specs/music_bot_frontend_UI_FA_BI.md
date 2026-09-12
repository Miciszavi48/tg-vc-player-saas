# Bot Front-End (UI Layer) Contract

> **Canonical References:**
> - [../features/i18n.md](../features/i18n.md)
> 
> **Legacy Note:** The monolith `strings/fa.json` layout described in this spec has been replaced by the split-fragment architecture. See the canonical reference above.
>
> **Implementation status (2026-07-19): PARTIALLY SUPERSEDED.** The UI principles and stable callback requirement remain product intent. The monolith layout, optional-English assumption, and fallback examples are obsolete; runtime uses mandatory FA/EN split fragments registered in `app/resources/i18n/manifest.json`.
> 
> **Scope:** Telegram bot visible UI (messages, button titles, keyboards) driven 100% from JSON.  
> **Default language:** Persian (FA, RTL).  
> **Current runtime:** FA and EN fragment trees are both required and key-parity checked; `chat_settings.language` selects the active language.

---

## 1) Goals and rules

### 1.1 No hardcoded UI text
- Any visible text (messages, button titles, menu labels) must come from JSON.
- Code must only reference stable keys like `t("start.menu.pricing")`.

### 1.2 Callback data is NOT localized
- `callback_data` values must be stable constants (English-like identifiers).
- Only the visible button label is translated.

### 1.3 Placeholders
- Use Python `.format()` placeholders like `{mention}`, `{chat_title}`, `{credit_days}`.
- Placeholders must be identical across languages.

### 1.4 Direction and typography
- FA is RTL. Keep punctuation and spacing Persian-friendly.
- Use standard digits `0-9` unless you explicitly decide to convert to Persian digits everywhere.

---

## 2) Resource file layout

Recommended layout (simple and practical):

```
resources/
  strings/
    fa.json          # required
    en.json          # optional (same keys as fa.json)
bot/
  utils/
    i18n.py          # TextService (loads JSON + t())
    ui.py            # KeyboardFactory (builds keyboards using keys)
```

Notes:
- You can keep everything inside `fa.json` and `en.json` (messages + buttons + labels).
- Keyboard layout can live in code (recommended) to avoid JSON complexity, but all titles still come from JSON.

---

## 3) JSON contract (keys and structure)

### 3.1 Stable nested keys
Use a strict nested hierarchy. Example pattern:

- `common.buttons.*`
- `common.errors.*`
- `start.*`
- `playback.*`
- `panels.developer.*`
- `panels.owner.*`
- `panels.sudo.*`
- `panels.group.*`

### 3.2 Minimum required sections
Your JSON must include at least:

- `meta.language`, `meta.fallback`, `meta.version`
- `common.buttons`
- `common.errors`
- `start.welcome`, `start.menu_title`, `start.menu.*`
- `playback.choose_source`, `playback.types.*`, `playback.controls.*`
- `panels.*` (developer/owner/sudo/group titles and labels)

---

## 4) TextService (i18n loader)

Create `bot/utils/i18n.py`:

```python
import json
from pathlib import Path
from typing import Any

class TextService:
    def __init__(self, base_dir: str = "resources/strings"):
        self.base_dir = Path(base_dir)
        self.cache: dict[str, dict[str, Any]] = {}

    def load(self, lang: str) -> dict[str, Any]:
        if lang in self.cache:
            return self.cache[lang]

        path = self.base_dir / f"{lang}.json"
        if not path.exists():
            # fallback to fa
            path = self.base_dir / "fa.json"

        data = json.loads(path.read_text(encoding="utf-8"))
        self.cache[lang] = data
        return data

    def t(self, lang: str, key: str, **kwargs) -> str:
        data = self.load(lang)

        cur: Any = data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return f"[missing:{key}]"
            cur = cur[part]

        if not isinstance(cur, str):
            return f"[invalid:{key}]"

        try:
            return cur.format(**kwargs)
        except Exception:
            return cur  # safe fallback (do not crash UI)

def get_chat_lang(chat_settings) -> str:
    # chat_settings.language comes from DB (default 'fa')
    # keep it simple: only 'fa' or 'en'
    return (getattr(chat_settings, "language", None) or "fa").lower()
```

---

## 5) KeyboardFactory (all labels from JSON)

Create `bot/utils/ui.py`:

```python
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.utils.i18n import TextService

# callback constants (stable, NOT translated)
CB = {
    "START_FORCE_JOIN": "start:force_join",
    "START_PRICING": "start:pricing",
    "START_BUY_CREATOR": "start:buy_creator",
    "START_BUY_SUDO_1": "start:buy_sudo_1",
    "START_BUY_SUDO_2": "start:buy_sudo_2",
    "START_GUIDE": "start:guide",
    "START_BOT_CH": "start:bot_channel",
    "START_SUPPORT": "start:support_group",
    "START_CUSTOM": "start:custom_link",
    "START_ADD_GROUP": "start:add_group",
    "START_ADD_CHANNEL": "start:add_channel",

    "PB_AUDIO": "pb:type:audio",
    "PB_VIDEO": "pb:type:video",
    "PB_TV": "pb:type:tv",
    "PB_SAT": "pb:type:satellite",
    "PB_RADIO": "pb:type:radio",
    "PB_DOWNLOAD": "pb:type:download",

    "PB_VOL_UP": "pb:vol:+",
    "PB_VOL_DOWN": "pb:vol:-",
    "PB_SPEED_UP": "pb:speed:+",
    "PB_SPEED_DOWN": "pb:speed:-",
    "PB_NEXT": "pb:next",
    "PB_PREV": "pb:prev",
    "PB_REPEAT_TOGGLE": "pb:repeat",
    "PB_FAV_ADD": "pb:fav:add",
    "PB_FAV_PLAY": "pb:fav:play",
    "PB_STOP": "pb:stop",
    "PB_PAUSE": "pb:pause",
    "PB_RESUME": "pb:resume",
}

class KeyboardFactory:
    def __init__(self, texts: TextService):
        self.texts = texts

    def start_menu(self, lang: str, links: dict) -> InlineKeyboardMarkup:
        t = lambda k: self.texts.t(lang, k)

        return InlineKeyboardMarkup([
            [InlineKeyboardButton(t("start.menu.force_join"), callback_data=CB["START_FORCE_JOIN"])],
            [InlineKeyboardButton(t("start.menu.pricing"), callback_data=CB["START_PRICING"])],
            [
                InlineKeyboardButton(t("start.menu.buy_from_creator"), url=links["creator"]),
            ],
            [
                InlineKeyboardButton(t("start.menu.buy_from_sudo_1"), url=links.get("sudo_1", links["creator"])),
                InlineKeyboardButton(t("start.menu.buy_from_sudo_2"), url=links.get("sudo_2", links["creator"])),
            ],
            [
                InlineKeyboardButton(t("start.menu.guide_channel"), url=links["guide_channel"]),
                InlineKeyboardButton(t("start.menu.bot_channel"), url=links["bot_channel"]),
            ],
            [InlineKeyboardButton(t("start.menu.support_group"), url=links["support_group"])],
            [InlineKeyboardButton(t("start.menu.custom_link"), url=links.get("custom_link", links["bot_channel"]))],
            [
                InlineKeyboardButton(t("start.menu.add_to_group"), url=links["add_to_group"]),
                InlineKeyboardButton(t("start.menu.add_to_channel"), url=links["add_to_channel"]),
            ],
        ])

    def playback_type_menu(self, lang: str) -> InlineKeyboardMarkup:
        t = lambda k: self.texts.t(lang, k)

        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(t("playback.types.audio"), callback_data=CB["PB_AUDIO"]),
                InlineKeyboardButton(t("playback.types.video"), callback_data=CB["PB_VIDEO"]),
            ],
            [
                InlineKeyboardButton(t("playback.types.tv"), callback_data=CB["PB_TV"]),
                InlineKeyboardButton(t("playback.types.satellite"), callback_data=CB["PB_SAT"]),
            ],
            [
                InlineKeyboardButton(t("playback.types.radio"), callback_data=CB["PB_RADIO"]),
                InlineKeyboardButton(t("playback.types.download"), callback_data=CB["PB_DOWNLOAD"]),
            ],
            [InlineKeyboardButton(t("common.buttons.back"), callback_data="nav:back")],
        ])

    def now_playing_controls(self, lang: str, repeat_on: bool) -> InlineKeyboardMarkup:
        t = lambda k: self.texts.t(lang, k)

        repeat_label = t("playback.controls.repeat_on") if repeat_on else t("playback.controls.repeat_off")

        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(t("playback.controls.vol_down"), callback_data=CB["PB_VOL_DOWN"]),
                InlineKeyboardButton(t("playback.controls.vol_up"), callback_data=CB["PB_VOL_UP"]),
            ],
            [
                InlineKeyboardButton(t("playback.controls.speed_down"), callback_data=CB["PB_SPEED_DOWN"]),
                InlineKeyboardButton(t("playback.controls.speed_up"), callback_data=CB["PB_SPEED_UP"]),
            ],
            [
                InlineKeyboardButton(t("playback.controls.prev"), callback_data=CB["PB_PREV"]),
                InlineKeyboardButton(t("playback.controls.next"), callback_data=CB["PB_NEXT"]),
            ],
            [
                InlineKeyboardButton(repeat_label, callback_data=CB["PB_REPEAT_TOGGLE"]),
                InlineKeyboardButton(t("playback.controls.fav_add"), callback_data=CB["PB_FAV_ADD"]),
                InlineKeyboardButton(t("playback.controls.fav_play"), callback_data=CB["PB_FAV_PLAY"]),
            ],
            [
                InlineKeyboardButton(t("common.buttons.pause"), callback_data=CB["PB_PAUSE"]),
                InlineKeyboardButton(t("common.buttons.resume"), callback_data=CB["PB_RESUME"]),
                InlineKeyboardButton(t("common.buttons.stop"), callback_data=CB["PB_STOP"]),
            ],
        ])
```

---

## 6) Persian first, optional bilingual

### 6.1 Default Persian
- DB default: `chat_settings.language = 'fa'`.
- Always ship `resources/strings/fa.json`.

### 6.2 Optional English later (no breaking changes)
- Add `resources/strings/en.json` with the same keys.
- Add a group setting UI in `Group Panel` to change language if you want.
- If `en.json` is missing, system falls back to `fa.json`.

---

## 7) JSON files (FA complete baseline + EN minimal stub)

### 7.1 `resources/strings/fa.json` (baseline, extend safely)
This is a clean baseline that covers the visible UI mentioned in the specification.
You can extend it without changing keys.

```json
{
  "meta": {
    "language": "fa",
    "fallback": "fa",
    "version": "1.0.0",
    "direction": "rtl"
  },

  "common": {
    "buttons": {
      "back": "⬅️ بازگشت",
      "close": "✖️ بستن",
      "cancel": "لغو",
      "confirm": "تایید",
      "next": "بعدی",
      "prev": "قبلی",
      "stop": "⛔ توقف",
      "pause": "⏸ مکث",
      "resume": "▶️ ازسرگیری"
    },
    "errors": {
      "no_access": "شما دسترسی ندارید.",
      "not_installed": "پلیر در این چت نصب نیست.",
      "invalid_number": "عدد وارد شده معتبر نیست.",
      "try_later": "لطفا چند ثانیه بعد دوباره تلاش کنید.",
      "flood_wait": "محدودیت تلگرام فعال شد، لطفا کمی بعد تلاش کنید.",
      "helper_unavailable": "هلپر در دسترس نیست. لطفا چند دقیقه بعد تلاش کنید."
    },
    "labels": {
      "on": "فعال",
      "off": "غیرفعال",
      "enabled": "فعال",
      "disabled": "غیرفعال"
    }
  },

  "start": {
    "welcome": "سلام {mention} عزیز! 🎵 به ربات موزیک پلیر خوش آمدی",
    "menu_title": "منوی اصلی",
    "menu": {
      "force_join": "نمایش عضویت اجبار",
      "pricing": "تعرفه ربات",
      "buy_from_creator": "خرید از سازنده",
      "buy_from_sudo_1": "خرید از سودو اول",
      "buy_from_sudo_2": "خرید از سودو دوم",
      "guide_channel": "کانال راهنما",
      "bot_channel": "کانال ربات",
      "support_group": "گروه پشتیبانی",
      "custom_link": "لینک دلخواه",
      "add_to_group": "افزودن به گروه",
      "add_to_channel": "افزودن به کانال",
      "about": "درباره ما"
    },
    "force_join": {
      "title": "عضویت اجباری",
      "empty": "هیچ کانالی برای عضویت اجباری ثبت نشده است."
    }
  },

  "playback": {
    "choose_source": "• جهت پخش، یکی از گزینه های زیر را انتخاب کنید:",
    "choose_tv": "• جهت پخش، یکی از شبکه های زیر را انتخاب کنید :",
    "now_playing": "⌯ در حال پخش میباشد 🔊\n⊹ نام درخواست کننده : {user}\n⊹ ساعت : `{datetime}` 📅",
    "track_card": {
      "title": "🎵 {title}",
      "artist": "👤 خواننده: {artist}",
      "duration": "⏱ مدت: {duration}",
      "requested_by": "🙋 درخواست: {user}"
    },
    "types": {
      "audio": "صوتی",
      "video": "تصویری",
      "tv": "تلویزیون",
      "satellite": "ماهواره",
      "radio": "رادیو",
      "download": "دانلود رسانه"
    },
    "controls": {
      "vol_up": "🔊 افزایش صدا",
      "vol_down": "🔉 کاهش صدا",
      "speed_up": "⏩ افزایش سرعت",
      "speed_down": "⏪ کاهش سرعت",
      "next": "⏭ بعدی",
      "prev": "⏮ قبلی",
      "repeat_on": "🔁 تکرار: فعال",
      "repeat_off": "🔁 تکرار: خاموش",
      "fav_add": "⭐ افزودن به لیست دلخواه",
      "fav_play": "🎧 پخش لیست دلخواه"
    },
    "queue": {
      "empty": "لیست پخش خالی است.",
      "added": "✅ به صف اضافه شد.",
      "skipped": "⏭ آهنگ رد شد."
    }
  },

  "panels": {
    "developer": {
      "title": "پنل برنامه نویس",
      "status": "وضعیت ربات",
      "increase_credit": "افزایش اعتبار",
      "decrease_credit": "کسر اعتبار",
      "send_invoice": "ارسال فاکتور",
      "set_base_rate": "تنظیم نرخ پایه",
      "set_music_rate": "تنظیم نرخ موزیک",
      "set_video_rate": "تنظیم نرخ ویدیو",
      "set_call_security_rate": "تنظیم نرخ امنیت کال",
      "broadcast_group": "ارسال همگانی گروه",
      "forward_group": "فوروارد همگانی گروه",
      "broadcast_private": "ارسال همگانی پیوی",
      "forward_private": "فوروارد همگانی پیوی",
      "broadcast_channel": "ارسال همگانی کانال",
      "forward_channel": "فوروارد همگانی کانال",
      "force_join_toggle": "عضویت اجباری فعال/غیرفعال",
      "channel_security_toggle": "امنیت کانال فعال/غیرفعال",
      "set_media_policy": "تنظیم رسانه پخش",
      "auto_leave_toggle": "خروج خودکار بدون اعتبار فعال/غیرفعال",
      "trial_toggle": "تنظیم اعتبار آزمایشی فعال/غیرفعال",
      "list_groups": "لیست گروه ها (اعتبار/لینک/خروج)",
      "list_channels": "لیست کانال ها (اعتبار/لینک/خروج)",
      "list_no_credit": "لیست کانال و گروه های بدون اعتبار",
      "filters": "مدیریت کلمات فیلتر",
      "force_join_manage": "مدیریت عضویت اجبار",
      "sudo_manage": "مدیریت سودوها",
      "set_owner": "تنظیم مالک ربات",
      "texts_links": "تنظیم متن ها و لینک ها",
      "install_limits": "تنظیم محدودیت نصب",
      "blacklist": "مسدود/آزاد کانال گروه و کاربر",
      "set_log_channel": "تنظیم کانال گزارشات و لاگ ها"
    },

    "owner": {
      "title": "پنل مالک ربات",
      "stats": "نمایش آمار",
      "list_installs_groups": "لیست گروه های نصب",
      "list_installs_channels": "لیست کانال های نصب",
      "users": "کاربران",
      "credit_and_links": "میزان اعتبار و لینک",
      "broadcast_group": "ارسال همگانی گروه",
      "forward_group": "فوروارد همگانی گروه",
      "broadcast_private": "ارسال همگانی پیوی",
      "forward_private": "فوروارد همگانی پیوی",
      "broadcast_channel": "ارسال همگانی کانال",
      "forward_channel": "فوروارد همگانی کانال",
      "force_join_toggle": "عضویت اجباری فعال/غیرفعال",
      "channel_security_toggle": "امنیت کانال فعال/غیرفعال",
      "set_media_policy": "تنظیم رسانه پخش",
      "auto_leave_toggle": "خروج خودکار بدون اعتبار فعال/غیرفعال",
      "trial_toggle": "تنظیم اعتبار آزمایشی فعال/غیرفعال",
      "list_groups": "لیست گروه ها (اعتبار/لینک/خروج)",
      "list_channels": "لیست کانال ها (اعتبار/لینک/خروج)",
      "list_no_credit": "لیست کانال و گروه های بدون اعتبار",
      "filters": "مدیریت کلمات فیلتر",
      "force_join_manage": "مدیریت عضویت اجبار",
      "sudo_manage": "مدیریت سودوها",
      "install_limits": "تنظیم محدودیت نصب",
      "blacklist": "مسدود/آزاد کانال گروه و کاربر",
      "install_reports": "دریافت گزارشات نصب توسط سودو",
      "no_credit_reports": "دریافت گزارشات عدم اعتبار",
      "bot_credit": "نمایش اعتبار ربات",
      "increase_bot_credit": "افزایش اعتبار ربات",
      "bot_invoices": "نمایش فاکتور ربات"
    },

    "sudo": {
      "title": "پنل سودو",
      "installs_report": "گزارش نصب پلیر",
      "credit_report": "گزارش اعتبار باقی مانده",
      "stats": "آمار کلی نصب پلیر",
      "leave_installs": "خروج پلیر از نصب های خود"
    },

    "group": {
      "title": "پنل گروه",
      "settings_title": "تنظیمات",
      "settings": {
        "music_video": "فعال سازی موزیک ویدیو/غیرفعال",
        "security_call": "فعالسازی امنیت کال/غیرفعال",
        "repeat": "تکرار فعال/غیرفعال",
        "download_users": "دانلود رسانه برای کاربر عادی",
        "call_message": "پیام کال فعال/غیرفعال",
        "auto_clean": "پاکسازی خودکار فعال/غیرفعال",
        "queue": "لیست انتظار فعال/غیرفعال",
        "auto_ready_call": "آماد کال خودکار فعال/غیرفعال",
        "call_report": "ارسال گزارشات کال فعال/غیرفعال",
        "record_call": "ضبط کال فعال/غیرفعال",
        "show_id": "نمایش شناسه",
        "show_photo": "نمایش عکس",
        "show_text": "نمایش متن"
      },
      "management_title": "مدیریت",
      "management": {
        "owners_list": "نمایش لیست مالکان پلیر",
        "admins_list": "نمایش لیست مدیران پلیر",
        "vip_list": "نمایش لیست ویژه های پلیر",
        "clear_all": "پاکسازی همه"
      },
      "help_title": "راهنما پلیر",
      "help": {
        "promote_demote": "ارتقا و عزل",
        "play_commands": "دستورات پخش",
        "general_commands": "دستورات عمومی",
        "support_request": "درخواست پشتیبانی"
      },
      "support_title": "پشتیبانی ها",
      "support": {
        "creator": "ارتباط با سازنده",
        "sudo": "ارتباط با سودو",
        "guide_channel": "کانال راهنما",
        "support_group": "گروه پشتیبانی"
      }
    }
  }
}
```

### 7.2 `resources/strings/en.json` (optional, minimal stub)
Keep keys identical. You can expand later.

```json
{
  "meta": {
    "language": "en",
    "fallback": "fa",
    "version": "1.0.0",
    "direction": "ltr"
  },
  "common": {
    "buttons": {
      "back": "Back",
      "close": "Close",
      "cancel": "Cancel",
      "confirm": "Confirm",
      "next": "Next",
      "prev": "Prev",
      "stop": "Stop",
      "pause": "Pause",
      "resume": "Resume"
    },
    "errors": {
      "no_access": "You do not have access.",
      "not_installed": "Player is not installed in this chat.",
      "invalid_number": "Invalid number.",
      "try_later": "Please try again in a few seconds.",
      "flood_wait": "Telegram rate limit. Please try later.",
      "helper_unavailable": "Helper is unavailable. Please try later."
    },
    "labels": {
      "on": "On",
      "off": "Off",
      "enabled": "Enabled",
      "disabled": "Disabled"
    }
  },
  "start": {
    "welcome": "Hi {mention}! 🎵 Welcome to Music Player Bot",
    "menu_title": "Main Menu",
    "menu": {
      "force_join": "Force Join Status",
      "pricing": "Pricing",
      "buy_from_creator": "Buy from Creator",
      "buy_from_sudo_1": "Buy from Sudo 1",
      "buy_from_sudo_2": "Buy from Sudo 2",
      "guide_channel": "Guide Channel",
      "bot_channel": "Bot Channel",
      "support_group": "Support Group",
      "custom_link": "Custom Link",
      "add_to_group": "Add to Group",
      "add_to_channel": "Add to Channel",
      "about": "About"
    },
    "force_join": {
      "title": "Force Join",
      "empty": "No force-join channels configured."
    }
  },
  "playback": {
    "choose_source": "Choose one option to play:",
    "choose_tv": "Choose a TV channel:",
    "now_playing": "Now playing 🔊\nRequested by: {user}\nTime: `{datetime}`",
    "track_card": {
      "title": "🎵 {title}",
      "artist": "Artist: {artist}",
      "duration": "Duration: {duration}",
      "requested_by": "Requested by: {user}"
    },
    "types": {
      "audio": "Audio",
      "video": "Video",
      "tv": "TV",
      "satellite": "Satellite",
      "radio": "Radio",
      "download": "Download"
    },
    "controls": {
      "vol_up": "Volume +",
      "vol_down": "Volume -",
      "speed_up": "Speed +",
      "speed_down": "Speed -",
      "next": "Next",
      "prev": "Prev",
      "repeat_on": "Repeat: On",
      "repeat_off": "Repeat: Off",
      "fav_add": "Add to Favorites",
      "fav_play": "Play Favorites"
    },
    "queue": {
      "empty": "Queue is empty.",
      "added": "Added to queue.",
      "skipped": "Skipped."
    }
  },
  "panels": {
    "developer": { "title": "Developer Panel" },
    "owner": { "title": "Owner Panel" },
    "sudo": { "title": "Sudo Panel" },
    "group": { "title": "Group Panel" }
  }
}
```

---

## 8) Checklist for UI completeness

Before shipping:
- Every button label shown to users exists as a JSON key.
- Every message text sent to users exists as a JSON key.
- No `reply_text("...")` with Persian hardcoded strings.
- Keyboard labels always use `t(lang, "...")`.
- `callback_data` constants are stable and not translated.
- Adding EN later does not require code changes.

---

## 9) What you should NOT do
- Do not store long UI texts in `config.env`.
- Do not change keys after release (only add new keys).
- Do not localize callback values.
