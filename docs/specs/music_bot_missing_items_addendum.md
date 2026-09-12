# Music Bot Specification Addendum (Missing Items From Client Chat)

> **Canonical References:**
> - [../DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> This file is an **add-on** to `music_bot_complete_docs_EN_FINAL.md`.
> It does **not** delete or modify anything from existing docs. It only covers **items mentioned in the client chat that are not explicitly documented**, and the **minimum DB/JSON/logic additions** needed to support them.
>
> **Implementation status (2026-07-19): MIXED / PARTIAL.** This file records product intent and historical gap analysis. Confirmed runtime behavior lives in `docs/features/`; stale schema or resource examples below must not be treated as current implementation evidence.

---

## 1) Items from the client chat that are NOT part of the current specification documents

The following categories are **conversation-only / contract-only** and are **not included** as system requirements in the spec files:

1. **Greetings / small talk**
   - Examples: "سلام عرض ادب", "خوبی شما؟", "رئیس سلام عرض ادب", etc.

2. **Pricing, negotiation, timeline, discounts**
   - Examples: asking "چقدر زمان و چقدر هزینه"، numbers like "۶ میلیون"، "۱۲ تومن"، "۱۳ تا ۱۵ تومن"، "۲۰ الی ۳۰ روز"، درخواست تخفیف، پیش‌پرداخت درصدی، و…  
   - These are business terms and are not defined as functional requirements.

3. **Payment details / bank info**
   - Card number, IBAN, “اسکرین شات از رسید فراموش نشه”، tracking code references, etc.

4. **Project process notes**
   - Messages like: “اول دیتابیس رو طراحی کنم”، “سرور فعلا نیازی نیست”، “آخر هفته نسخه تست میفرستم”، etc.

5. **The "bot-builder" idea that was discussed but postponed**
   - The chat discusses building a separate “بات‌ساز” (a factory bot that spins up multiple bots) and then explicitly chooses **not** to do it now (راه اول).  
   - The current spec focuses on **one bot per deployment**, not a multi-bot orchestration platform.

These items are intentionally excluded from the technical spec, because they are not part of the software’s runtime behavior.

---

## 2) One important functional note from the chat that is NOT explicitly modeled as a requirement

### 2.1 “Paid install logic should be separable from the core source”
Client message meaning:
- The owner wants the ability to run **the same core bot**, but configure installation to be:
  - Paid for some deployments/customers, and
  - Free for others,
  - Without rewriting the whole project.

Even though the current spec has a billing system (credits, sudo wallets, invoices, etc.), it does **not** explicitly define a **first-class install policy layer** that can be swapped/disabled cleanly.

This addendum defines that missing layer.

---

## 3) Mandatory Addition: Installation Policy Layer (Free vs Paid vs Hybrid)

### 3.1 Goal
Introduce an **Installation Policy** component that determines:

- Whether *installing* into a group/channel costs anything
- Whether *trial* auto-credit is granted
- Whether billing starts immediately or after trial
- Whether the policy differs by:
  - chat type (group vs channel),
  - installer role (Owner vs Sudo),
  - explicit whitelist exceptions.

This must be done **without touching the playback system** logic.

---

### 3.2 Database additions

#### 3.2.1 Table: `install_policy_settings` (single-row config)
Stores the active policy and its parameters.

```sql
CREATE TABLE install_policy_settings (
  id                    SMALLINT PRIMARY KEY DEFAULT 1,
  policy_mode           TEXT NOT NULL, -- paid, free, hybrid
  trial_days            INT NOT NULL DEFAULT 3,
  charge_on_install     BOOLEAN NOT NULL DEFAULT TRUE,
  group_install_fee_irr BIGINT NOT NULL DEFAULT 0,
  chan_install_fee_irr  BIGINT NOT NULL DEFAULT 0,
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ensure single row
INSERT INTO install_policy_settings (id, policy_mode)
VALUES (1, 'paid')
ON CONFLICT (id) DO NOTHING;
```

Policy meaning:
- `paid`: always require install fee (unless whitelisted)
- `free`: install fee is 0; only consumption billing applies (daily credit deduction rules remain)
- `hybrid`: fee depends on chat type or installer role

> Note: this does not replace existing `BASE_CREDIT_RATE` / tariff concepts.
> It adds a clean decision point.

---

#### 3.2.2 Table: `free_install_whitelist`
Allows free install for specific chats (group/channel) even in paid mode.

```sql
CREATE TABLE free_install_whitelist (
  chat_id           BIGINT PRIMARY KEY,
  chat_type         TEXT NOT NULL,         -- group, channel
  reason            TEXT,
  created_by        BIGINT NOT NULL REFERENCES users(id),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at        TIMESTAMPTZ
);

CREATE INDEX idx_free_install_whitelist_expires ON free_install_whitelist(expires_at);
```

Rules:
- If `expires_at` is NULL, exception is permanent until removed.
- If expired, it is ignored (and can be cleaned by cron).

---

#### 3.2.3 Optional: link installation fee to invoices (recommended)
If install fee is paid, log it as an invoice item:

- Use existing invoice mechanism if present.
- Add `invoice_items` if not already defined:

```sql
CREATE TABLE invoice_items (
  id          BIGSERIAL PRIMARY KEY,
  invoice_id  BIGINT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  item_type   TEXT NOT NULL, -- install_fee, bulk_topup, etc
  item_ref_id BIGINT,        -- chat_id or sale_id
  amount_irr  BIGINT NOT NULL
);

CREATE INDEX idx_invoice_items_invoice ON invoice_items(invoice_id);
```

---

### 3.3 Core logic additions (high level)

#### 3.3.1 Central function: `compute_install_cost(chat_type, installer_role, chat_id) -> cost_irr`
1) Load `install_policy_settings` (cache in Redis for 60s)
2) If chat_id exists in `free_install_whitelist` and not expired -> cost = 0
3) Apply `policy_mode`:
- `free`: 0
- `paid`: `group_install_fee_irr` or `chan_install_fee_irr`
- `hybrid` (default rule):
  - group: free
  - channel: paid
  - (or configurable)

#### 3.3.2 Enforcement point
During “install flow” (bot added to group/channel OR install command):
- Compute cost
- If cost > 0:
  - Only allow installation if installer is Owner/Developer OR Sudo with sufficient wallet (depending on your business rule)
  - Create invoice or direct wallet deduction (atomic)
- Then grant trial days (if trial_days > 0), as described below.

#### 3.3.3 Trial behavior (explicit)
- If `trial_days > 0`:
  - Insert `group_credits` / `channel_credits` with `credit_days = trial_days`
  - Mark `credited_by = 'trial'`
- After trial ends:
  - Continue standard charging / daily deduction
  - If paid mode requires renewal, follow existing sudo wallet credit top-up flows

---

## 4) Admin Panel & JSON (Persian-only text)

All bot messages and button labels must be **Persian** and loaded from JSON (same rule as the main spec).

### 4.1 New JSON keys (example)
Add to your `buttons.json` / `messages.json` (structured nesting recommended):

```json
{
  "admin": {
    "install_policy": {
      "title": "سیاست نصب",
      "mode": {
        "paid": "نصب پولی",
        "free": "نصب رایگان",
        "hybrid": "نصب ترکیبی"
      },
      "trial_days": "تنظیم روزهای آزمایشی",
      "fees": {
        "group": "هزینه نصب گروه",
        "channel": "هزینه نصب کانال"
      },
      "whitelist": {
        "title": "استثناهای نصب رایگان",
        "add": "افزودن نصب رایگان",
        "remove": "حذف نصب رایگان",
        "list": "لیست نصب رایگان"
      }
    }
  },
  "errors": {
    "install_paid_required": "نصب این پلیر نیاز به پرداخت دارد.",
    "install_whitelisted_free": "این گروه/کانال در لیست نصب رایگان قرار دارد."
  }
}
```

### 4.2 Commands (Owner/Developer panel)
- `سیاست نصب`  → open menu
- `تنظیم روزهای آزمایشی` → set integer
- `هزینه نصب گروه` / `هزینه نصب کانال` → set amounts
- `افزودن نصب رایگان` → ask chat_id + type + optional expiry
- `حذف نصب رایگان` → ask chat_id
- `لیست نصب رایگان` → show list + expiry status

---

## 5) Update-friendly packaging (to satisfy “minimal file replacement”)

This is a process requirement from chat: “فایل ها حداقل باشه برای آپدیت بعدی”.

### 5.1 Rule
**Never hardcode deployment-specific values in code.** Keep them in:
- `config.env`
- `messages.fa.json` (+ optional `messages.en.json`)
- DB tables (`bot_settings`, `install_policy_settings`, etc.)

### 5.2 Recommended release layout
- `/opt/musicbot/app/` (code)
- `/etc/musicbot/config.env` (secrets + env)
- `/etc/musicbot/i18n/` (JSON texts)
- `/var/lib/musicbot/` (downloads/cache if needed)
- DB + Redis external

Update process:
1) Replace `/opt/musicbot/app/` only
2) Run migrations
3) Restart service

### 5.3 Single command helper (optional)
Provide a local script (not a Telegram command) such as:

- `./deploy.sh --upgrade`
  - pulls new code
  - runs migrations
  - restarts service

This keeps future updates “one-shot” and avoids manual file-by-file replacement.

---

## 6) Explicit Out-of-scope for now (but recorded)
The chat discussed a future “بات‌ساز” platform (multi-bot orchestration).  
This addendum does not design that system because the chosen path was **راه اول** (single bot deployment).  
If you later want it, it should be a separate spec and likely a separate service.

---

## 7) Quick Checklist (what you must implement if you apply this addendum)

- [ ] Add `install_policy_settings` table (single row)
- [ ] Add `free_install_whitelist` table
- [ ] Add admin panel actions + JSON keys (Persian text)
- [ ] Insert enforcement in install flow (compute install cost, then proceed)
- [ ] Ensure invoices/wallet deductions are atomic
- [ ] Keep policy values out of code (config/db/json only)
- [ ] Provide update workflow that replaces only code, not configs
