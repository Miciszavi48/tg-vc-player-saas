# Specification: Analytics & Reporting (Section 4)

> **Canonical References:**
> - [../DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [../REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
> 
> **Implementation status (2026-07-19): PARTIAL.** Current analytics, call reports, hourly aggregation, call-stats panel, and operator reports exist, but this full event catalog remains product intent. Verify shipped behavior in [../features/admin_panels.md](../features/admin_panels.md), [../features/commands.md](../features/commands.md), current models/services, and tests.
> **Codebase**: tg-vc-player-saas (Python 3.12, kurigram, SQLAlchemy 2.0 async, Redis 7, APScheduler)  
> **Depends on**: Existing `cache.py` lock/pipeline patterns, `scheduler.py` job registration, current repositories, existing `User`/`Group`/`Channel` models, and split i18n via `t()`.

---

## 4.1 Metrics Catalog

### Event Names

| Category | Event Name | Dimensions |
|----------|-----------|------------|
| **Core** | `bot.start` | chat_type=private, role |
| **Panels** | `ui.open_panel.dev` / `.owner` / `.sudo` / `.group` | role |
| **Install** | `install.created.group` / `.channel` | role, status |
| | `install.failed` | reason |
| **Credit** | `credit.charge` / `credit.deduct` / `credit.trial_activated` | role |
| **Playback** | `playback.play_audio` / `.play_video` / `.stop` / `.pause` / `.resume` | chat_type |
| | `playback.next` / `.prev` / `.volume_change` / `.speed_change` | chat_type |
| | `playback.download_request` / `.download_success` / `.download_fail` | chat_type |
| **Broadcast** | `broadcast.created.send` / `.created.forward` | scope |
| | `broadcast.sent` / `broadcast.failed` / `broadcast.floodwait` | scope |
| **FM** | `fm.check` / `fm.blocked` / `fm.passed` | — |
| **Security** | `cs.verify_run` / `cs.target_broken` / `cs.target_ok` | — |
| **Errors** | `errors.telegram_rpc` / `.db` / `.redis` / `.ffmpeg` / `.pytgcalls` | — |

### Event Dimensions

Each event carries:

| Dimension | Type | Example |
|-----------|------|---------|
| `day` | date (UTC) | `2026-02-25` |
| `hour` | int 0–23 | `14` |
| `chat_type` | enum | `private`, `group`, `supergroup`, `channel` |
| `role` | enum | `developer`, `owner`, `sudo`, `user`, `group_admin` |
| `feature` | enum | `playback`, `install`, `credit`, `broadcast`, `fm`, `security` |
| `status` | enum | `success`, `fail` |
| `fail_reason` | string | `flood_wait`, `user_blocked`, `db_error` |

---

## 4.2 Data Model

### Table: `analytics_hourly`

```python
class AnalyticsHourly(Base):
    __tablename__ = "analytics_hourly"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    hour: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    metric: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("day", "hour", "scope", "scope_key", "metric",
                         name="uq_analytics_hourly_composite"),
        Index("ix_analytics_day_scope", "day", "scope", "scope_key"),
        Index("ix_analytics_day_metric", "day", "metric"),
        Index("ix_analytics_day_hour_metric", "day", "hour", "metric"),
    )
```

### Scope/ScopeKey Design

| scope | scope_key | Use case |
|-------|-----------|----------|
| `global` | `global` | Bot-wide totals |
| `chat_type` | `group` / `supergroup` / `channel` / `private` | Per chat type |
| `feature` | `playback` / `install` / `credit` / `broadcast` / `fm` / `security` | Per feature |
| `role` | `developer` / `owner` / `sudo` / `user` | Per actor role |
| `sudo` | `{sudo_user_id}` | Per-sudo installs/credits (for sudo self-reports) |

---

## 4.3 Ingestion Strategy

### Redis-First Counters

On every tracked action, increment Redis counters atomically.

**Key pattern:**

```
an:{YYYYMMDD}:{HH}:{scope}:{scope_key}:{metric}
```

Examples:
```
an:20260225:14:global:global:events_total
an:20260225:14:feature:playback:play_audio
an:20260225:14:chat_type:group:events_total
an:20260225:14:role:sudo:credit.charge
an:20260225:14:sudo:123456:install.created.group
```

**TTL**: 35 days (set once via `EXPIRE` on first increment, using a marker key `an:ttl:{YYYYMMDD}:{HH}:{scope}:{scope_key}:{metric}` with `SETNX`).

### Tracking Helper

```python
async def track_event(
    event_name: str,
    chat_type: str = "private",
    role: str = "user",
    feature: str | None = None,
    status: str = "success",
    value: int = 1,
    sudo_id: int | None = None,
) -> None:
    """Increment Redis analytics counters. Swallows errors to never crash user flows."""
    try:
        r = await get_redis()
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        hour = str(datetime.now(timezone.utc).hour)
        
        pipe = r.pipeline()
        
        # Always increment global
        _incr(pipe, day, hour, "global", "global", "events_total", value)
        _incr(pipe, day, hour, "global", "global", event_name, value)
        
        # By chat_type
        _incr(pipe, day, hour, "chat_type", chat_type, "events_total", value)
        _incr(pipe, day, hour, "chat_type", chat_type, event_name, value)
        
        # By role
        _incr(pipe, day, hour, "role", role, "events_total", value)
        
        # By feature
        if feature:
            _incr(pipe, day, hour, "feature", feature, "events_total", value)
            _incr(pipe, day, hour, "feature", feature, event_name, value)
        
        # Per-sudo tracking
        if sudo_id:
            _incr(pipe, day, hour, "sudo", str(sudo_id), event_name, value)
        
        await pipe.execute()
    except Exception:
        logger.debug("Analytics tracking failed for %s", event_name)


def _incr(pipe, day: str, hour: str, scope: str, scope_key: str, metric: str, value: int):
    key = f"an:{day}:{hour}:{scope}:{scope_key}:{metric}"
    pipe.incrby(key, value)
    pipe.expire(key, 35 * 86400)  # 35 days
```

### Flush Job (APScheduler, Every 5 Minutes)

```python
async def flush_analytics() -> None:
    """Flush Redis analytics counters to Postgres analytics_hourly table."""
    token = await acquire_lock("analytics:flush", ttl_ms=60_000)
    if not token:
        return
    try:
        r = await get_redis()
        
        # Scan for all analytics keys
        cursor = 0
        batch = []
        while True:
            cursor, keys = await r.scan(cursor, match="an:*", count=500)
            batch.extend(keys)
            if cursor == 0:
                break
        
        if not batch:
            return
        
        # Atomically get and delete values
        pipe = r.pipeline()
        for key in batch:
            pipe.getdel(key)  # Redis 6.2+ GETDEL
        values = await pipe.execute()
        
        # Parse keys and build upsert rows
        rows = []
        for key, val in zip(batch, values):
            if val is None:
                continue
            parts = key.split(":", 5)
            # an:{day}:{hour}:{scope}:{scope_key}:{metric}
            if len(parts) != 6:
                continue
            _, day_str, hour_str, scope, scope_key, metric = parts
            rows.append({
                "day": date.fromisoformat(f"{day_str[:4]}-{day_str[4:6]}-{day_str[6:8]}"),
                "hour": int(hour_str),
                "scope": scope,
                "scope_key": scope_key,
                "metric": metric,
                "delta": int(val),
            })
        
        # Upsert into Postgres in a single transaction
        if rows:
            async with async_session() as session:
                async with session.begin():
                    for row in rows:
                        await session.execute(text("""
                            INSERT INTO analytics_hourly (day, hour, scope, scope_key, metric, value)
                            VALUES (:day, :hour, :scope, :scope_key, :metric, :delta)
                            ON CONFLICT (day, hour, scope, scope_key, metric)
                            DO UPDATE SET value = analytics_hourly.value + EXCLUDED.value
                        """), row)
        
        logger.info("Analytics flush: %d keys processed", len(rows))
    finally:
        await release_lock("analytics:flush", token)
```

---

## 4.4 Reports

### A) Yesterday Report

Query `analytics_hourly` where `day = yesterday()`.

```sql
SELECT scope, scope_key, metric, SUM(value) as total
FROM analytics_hourly
WHERE day = :yesterday AND scope = 'global' AND scope_key = 'global'
GROUP BY scope, scope_key, metric
ORDER BY total DESC;
```

Output sections:
- **Overview**: total events, unique features active
- **Installs**: `install.created.group`, `install.created.channel`, `install.failed`
- **Credits**: `credit.charge`, `credit.deduct`, `credit.trial_activated`
- **Playback**: `playback.play_audio`, `playback.play_video`, `playback.stop`
- **Broadcast**: `broadcast.created.send`, `broadcast.sent`, `broadcast.failed`
- **FM**: `fm.check`, `fm.blocked`, `fm.passed`
- **Errors**: `errors.*` aggregated

### B) Last N Days Report (7/14/30)

```sql
SELECT metric, SUM(value) as total
FROM analytics_hourly
WHERE day BETWEEN :start_date AND :end_date
  AND scope = 'global' AND scope_key = 'global'
GROUP BY metric
ORDER BY total DESC;
```

Optional day-by-day breakdown:
```sql
SELECT day, metric, SUM(value)
FROM analytics_hourly
WHERE day BETWEEN :start AND :end AND scope='global' AND scope_key='global'
  AND metric IN ('events_total', 'playback.play_audio', 'install.created.group')
GROUP BY day, metric
ORDER BY day;
```

### C) Peak Hours Report

```sql
SELECT hour, SUM(value) as total
FROM analytics_hourly
WHERE day BETWEEN :start AND :end
  AND scope = 'global' AND scope_key = 'global'
  AND metric = 'events_total'
GROUP BY hour
ORDER BY total DESC
LIMIT 3;
```

Display in configured timezone (default `Asia/Tehran`):

```
Peak hours (Asia/Tehran):
1. 21:00–22:00 — 1,247 events
2. 20:00–21:00 — 1,102 events
3. 15:00–16:00 — 892 events
```

### Report Caching

```
cache:analytics:report:{type}:{range}:{scope}:{scope_key}
TTL: 300s (5 minutes)
```

---

## 4.5 UX Flows

### Entry Points

```
Developer Panel → [📊 Analytics]  → Analytics Home
Owner Panel    → [📊 Analytics]  → Analytics Home
Sudo Panel     → [📊 My Stats]   → Sudo-scoped reports only
```

### Analytics Home (PM, developer/owner)

```
┌──────────────────────────────────────────────┐
│  ┈┅┅━━| 📊 Analytics & Reports |━━┅┅┈       │
│                                              │
│  [Yesterday]        [Last 7 Days]            │
│  [Last 14 Days]     [Last 30 Days]           │
│  [Peak Hours]       [Errors / Health]        │
│  [Back]                                      │
└──────────────────────────────────────────────┘
```

### Report Screen (example: Yesterday)

```
┌──────────────────────────────────────────────┐
│  📊 Yesterday Report — 2026-02-24            │
│  Generated: 2026-02-25 10:30 AST             │
│                                              │
│  ⊹ Total events: 4,521                      │
│  ⊹ Installs: 12 groups, 3 channels          │
│  ⊹ Credits: 8 charged, 2 deducted           │
│  ⊹ Playback: 342 audio, 87 video            │
│  ⊹ Broadcast: 2 sent (450 delivered)        │
│  ⊹ FM checks: 1,204 (38 blocked)            │
│  ⊹ Errors: 3 (2 FloodWait, 1 DB)           │
│                                              │
│  [By Feature]  [By Chat Type]  [By Role]     │
│  [Back]                                      │
└──────────────────────────────────────────────┘
```

### Drill-down: By Feature

```
┌──────────────────────────────────────────────┐
│  📊 Yesterday — By Feature                   │
│                                              │
│  ⊹ playback: 1,892                          │
│  ⊹ fm: 1,242                                │
│  ⊹ install: 15                               │
│  ⊹ credit: 10                                │
│  ⊹ broadcast: 452                            │
│  ⊹ security: 6                               │
│                                              │
│  [Back]                                      │
└──────────────────────────────────────────────┘
```

### Sudo Self-Report

```
┌──────────────────────────────────────────────┐
│  📊 My Stats (Last 7 Days)                   │
│                                              │
│  ⊹ Installs by me: 5 groups, 1 channel      │
│  ⊹ Credits charged by me: 3                  │
│                                              │
│  [Back]                                      │
└──────────────────────────────────────────────┘
```

### Group Context

No global analytics exposed in groups. If per-chat tracking is enabled (scope=`chat_id`), a limited "This Group Stats" button may be added to the group panel showing only that chat's playback/credit events.

---

## 4.6 Permissions

| Role | Access |
|------|--------|
| Developer | Full global analytics, all scopes, all drilldowns |
| Owner | Global analytics (same as developer for now) |
| Sudo | Only `scope=sudo`, `scope_key={own_user_id}` — installs + credits by this sudo |
| User | No analytics |

---

## 4.7 Redis Keys & TTL

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `an:{YYYYMMDD}:{HH}:{scope}:{scope_key}:{metric}` | 35 days | Raw counters |
| `lock:analytics:flush` | 60s | Flush job mutex |
| `cache:analytics:report:{type}:{range}:{scope}:{scope_key}` | 300s | Report cache |

All Redis clients use `decode_responses=True`.

---

## 4.8 Alembic Migration

```python
"""Add analytics_hourly table."""

def upgrade():
    op.create_table(
        "analytics_hourly",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("hour", sa.SmallInteger, nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("scope_key", sa.String(128), nullable=False),
        sa.Column("metric", sa.String(128), nullable=False),
        sa.Column("value", sa.BigInteger, nullable=False, server_default="0"),
    )
    op.create_unique_constraint(
        "uq_analytics_hourly_composite", "analytics_hourly",
        ["day", "hour", "scope", "scope_key", "metric"],
    )
    op.create_index("ix_analytics_day_scope", "analytics_hourly", ["day", "scope", "scope_key"])
    op.create_index("ix_analytics_day_metric", "analytics_hourly", ["day", "metric"])
    op.create_index("ix_analytics_day_hour_metric", "analytics_hourly", ["day", "hour", "metric"])

def downgrade():
    op.drop_table("analytics_hourly")
```

---

## 4.9 CB Constants

```python
# Analytics panel
"AN_HOME": "an:home",
"AN_YESTERDAY": "an:yesterday",
"AN_7DAYS": "an:7d",
"AN_14DAYS": "an:14d",
"AN_30DAYS": "an:30d",
"AN_PEAK": "an:peak",
"AN_ERRORS": "an:errors",
"AN_BY_FEATURE": "an:drill:feature",
"AN_BY_CHAT_TYPE": "an:drill:chattype",
"AN_BY_ROLE": "an:drill:role",
"SUDO_MY_STATS": "sudo:my_stats",
```

---

## 4.10 i18n Keys

```jsonc
{
  "admin": {
    "analytics": {
      "title": "...",              // Panel header
      "yesterday_btn": "...",
      "last_7d_btn": "...",
      "last_14d_btn": "...",
      "last_30d_btn": "...",
      "peak_hours_btn": "...",
      "errors_btn": "...",
      "by_feature_btn": "...",
      "by_chat_type_btn": "...",
      "by_role_btn": "...",
      "report_header": "...",      // "📊 {type} Report — {range}"
      "generated_at": "...",       // "Generated: {timestamp}"
      "total_events": "...",
      "installs_summary": "...",   // "{groups} groups, {channels} channels"
      "credits_summary": "...",
      "playback_summary": "...",
      "broadcast_summary": "...",
      "fm_summary": "...",
      "errors_summary": "...",
      "peak_hour_item": "...",     // "{hour} — {count} events"
      "peak_timezone_note": "...", // "Times shown in {tz}"
      "no_data": "...",
      "drill_feature_title": "...",
      "drill_chattype_title": "...",
      "drill_role_title": "...",
      "drill_row": "...",          // "⊹ {key}: {value}"
      "sudo_my_stats_title": "...",
      "sudo_installs_by_me": "...",
      "sudo_credits_by_me": "..."
    }
  }
}
```

---

## 4.11 Error Handling

- `track_event()` wraps all Redis calls in try/except — **never crashes user flows**.
- `flush_analytics()` uses distributed lock — safe under multiple instances.
- `GETDEL` ensures no double-counting even if flush is interrupted (key is deleted atomically with read).
- Report queries use read-only sessions with short timeouts.
- Report results are cached for 300s — repeated clicks don't re-query.

Log fields: `component=analytics`, `action=track|flush|report`, `day`, `hour`, `scope`, `metric`.
Never log: invite links, tokens, user PII.

---

## 4.12 Test Plan

### Unit Tests

| Test | Description |
|------|-------------|
| `test_track_event_increments_redis_keys` | Call `track_event("bot.start")`, verify Redis keys `an:*:global:global:events_total` and `an:*:global:global:bot.start` are set |
| `test_track_event_swallows_errors` | Patch Redis to raise, verify no exception propagates |
| `test_flush_upserts_and_clears` | Set 3 Redis keys, run flush, verify DB rows exist and Redis keys deleted |
| `test_flush_idempotent` | Flush same data twice, verify DB value is not doubled |
| `test_report_yesterday` | Insert known rows into `analytics_hourly`, query yesterday report, verify totals |
| `test_peak_hours_calculation` | Insert hourly data for 7 days, query peak, verify top 3 hours correct |
| `test_report_caching` | Request report twice, verify second is served from Redis cache |

### Integration Tests

| Test | Description |
|------|-------------|
| `test_end_to_end_track_flush_report` | Track 10 events, run flush, request yesterday report, verify counts |
| `test_sudo_visibility` | Track events with `sudo_id=X`, verify sudo can see only their own scope |
| `test_developer_sees_global` | Verify developer report includes all scopes |
| `test_concurrent_flush_lock` | Two flush calls simultaneously, only one succeeds |

### Manual Tests

- [ ] Run the bot, trigger `/start`, `/play`, `/install`, verify Redis keys appear
- [ ] Wait for flush (or trigger manually), verify `analytics_hourly` rows
- [ ] Open Analytics panel → Yesterday → verify numbers match
- [ ] Open Peak Hours → verify top 3 hours displayed with correct timezone
- [ ] As Sudo, open "My Stats" → verify only sudo-scoped data shown
- [ ] Verify Back/Home navigation works on all analytics screens

---

## 4.13 Implementation Checklist

| # | Task | Files | Est. |
|---|------|-------|------|
| 1 | Alembic migration `0005_analytics_hourly` | `migrations/versions/` | 20m |
| 2 | Add `AnalyticsHourly` model | `models.py` | 10m |
| 3 | Create `app/services/analytics_service.py` (track + flush + reports) | `services/` | 3h |
| 4 | Add Redis key helpers to `cache.py` | `utils/cache.py` | 20m |
| 5 | Add CB constants to `ui.py` | `utils/ui.py` | 10m |
| 6 | Add keyboard builders (analytics home, report, drilldown) | `utils/ui.py` | 30m |
| 7 | Add i18n keys (fa + en) | `resources/strings/` | 30m |
| 8 | Create `app/handlers/analytics_panel.py` | `handlers/` | 2h |
| 9 | Register handler + scheduler job in `__init__.py` / `scheduler.py` | | 10m |
| 10 | Instrument existing handlers with `track_event()` calls | `handlers/*.py` | 1.5h |
| 11 | Write unit tests | `tests/` | 2h |
| 12 | Write integration tests | `tests/` | 1.5h |
| 13 | Manual QA | — | 1h |

**Total estimated**: ~13 hours

---

## 4.14 Compatibility Notes

- **Multi-instance safe**: flush uses `acquire_lock("analytics:flush")` with 60s TTL. Only one instance flushes at a time. `GETDEL` prevents double-counting.
- **Redis pipelines**: `track_event` batches all increments into a single pipeline (1 RTT). Flush uses pipeline for batch `GETDEL` (1 RTT for up to 500 keys).
- **DB transactions**: Flush upsert runs inside a single `async with session.begin()` — all-or-nothing.
- **Report caching**: 300s TTL prevents re-querying on repeated button clicks. Cache key includes scope+range so different reports don't collide.
- **Retention**: Redis keys expire after 35 days. DB `analytics_hourly` rows can be pruned with a scheduled cleanup job (optional, spec recommends 90-day retention).
