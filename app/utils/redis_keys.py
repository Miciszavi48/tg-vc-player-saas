"""Central Redis key registry.

Every Redis key used in the project MUST be defined here as a constant or
factory function.  Never hardcode key strings in services/handlers.

Naming convention:  ``{domain}:{sub}:{id}``
"""
from __future__ import annotations

from app.config.settings import settings


def instance_key(key: str) -> str:
    """Prefix an instance-owned Redis key exactly once."""
    text = str(key)
    if text.startswith(settings.REDIS_NAMESPACE):
        return text
    return f"{settings.REDIS_NAMESPACE}{text}"


def strip_instance_namespace(key: str) -> str:
    """Return the logical key name from a namespaced Redis key."""
    text = str(key)
    if text.startswith(settings.REDIS_NAMESPACE):
        return text[len(settings.REDIS_NAMESPACE):]
    return text


def instance_scan_pattern(pattern: str) -> str:
    return instance_key(pattern)


# ── TTLs (seconds) ───────────────────────────────────────────────────────
TTL_ROLE = 600
TTL_SETTINGS = 300        # overridable via settings.REDIS_SETTINGS_TTL
TTL_CREDIT = 300          # overridable via settings.REDIS_CREDIT_TTL
TTL_VIP = 300
TTL_BLACKLIST = 300
TTL_GLOBAL_BAN = 300
TTL_LIST = 300
TTL_FM_TARGETS = 120
TTL_FM_OK = 180
TTL_FM_RATE_LIMIT = 5
TTL_BC_PROGRESS = 7200
TTL_BCW_STATE = 600
TTL_BCW_CONFIRM_CLAIM = 900
TTL_OWNER_BROADCAST_CONFIRM = 900
TTL_AN = 35 * 86400
TTL_REPORT_CACHE = 300
TTL_WIZARD_RETURN = 1800
TTL_PANEL_MESSAGE = 1800
TTL_PANEL_SUMMARY = 60
TTL_DOWNLOAD_CHOICE = 300
TTL_CALLSEC_STATE = 86_400  # 24h
TTL_CALLSEC_REPORT_COOLDOWN = 3600
# ── Chat settings ────────────────────────────────────────────────────────
def settings_key(chat_id: int, chat_type: str = "group") -> str:
    return instance_key(f"settings:{chat_type}:{chat_id}")

# ── Credit ───────────────────────────────────────────────────────────────
TTL_CREDIT_WARNING_COOLDOWN = 172_800  # 48 hours

def credit_key(chat_id: int, chat_type: str = "group") -> str:
    return instance_key(f"credit:{chat_type}:{chat_id}")

def credit_lock_key(chat_id: int, chat_type: str = "group") -> str:
    return instance_key(f"credit:{chat_type}:{chat_id}")


def credit_warning_sent_key(
    chat_id: int,
    remaining_days: int,
    date_str: str,
    chat_type: str = "group",
) -> str:
    """Redis key for credit-warning anti-spam (one send per chat type/days/date bucket)."""
    return instance_key(
        f"credit:warn:{chat_type}:{chat_id}:{remaining_days}:{date_str}"
    )


def bcw_confirm_claim_key(user_id: int, wizard_id: str) -> str:
    return instance_key(f"bcw:confirm:{user_id}:{wizard_id}")


def owner_broadcast_confirm_claim_key(
    user_id: int,
    source_chat_id: int,
    source_message_id: int,
    issued_at: int,
) -> str:
    return instance_key(
        f"owner:bc:confirm:{user_id}:{source_chat_id}:"
        f"{source_message_id}:{issued_at}"
    )

# ── Role cache ───────────────────────────────────────────────────────────
def role_key(user_id: int) -> str:
    return instance_key(f"role:{user_id}")


def player_owner_members_key(chat_id: int) -> str:
    return instance_key(f"player_role:owners:{chat_id}")


def player_deputy_members_key(chat_id: int) -> str:
    return instance_key(f"player_role:deputies:{chat_id}")


def music_admin_members_key(chat_id: int) -> str:
    return instance_key(f"player_role:music_admins:{chat_id}")


def player_vip_members_key(chat_id: int) -> str:
    return instance_key(f"player_role:vips:{chat_id}")


def player_role_summary_key(chat_id: int, user_id: int) -> str:
    return instance_key(f"player_role:summary:{chat_id}:{user_id}")


def player_role_version_key(chat_id: int) -> str:
    return instance_key(f"player_role:version:{chat_id}")


def player_vip_expiry_key(chat_id: int) -> str:
    return instance_key(f"player_role:vip_expiry:{chat_id}")

# ── Bot settings ─────────────────────────────────────────────────────────
def botset_key(key: str) -> str:
    return instance_key(f"botset:{key}")

# ── Blacklist ────────────────────────────────────────────────────────────
def blacklist_group_key(entity_id: int) -> str:
    return instance_key(f"blacklist:group:{entity_id}")

def blacklist_user_key(entity_id: int) -> str:
    return instance_key(f"blacklist:user:{entity_id}")


def global_ban_user_key(user_id: int) -> str:
    return instance_key(f"global_ban:user:{user_id}")

# ── VIP ──────────────────────────────────────────────────────────────────
def vip_key(chat_id: int, user_id: int) -> str:
    return instance_key(f"vip:{chat_id}:{user_id}")

# ── Forced membership ────────────────────────────────────────────────────
FM_TARGETS = instance_key("fm:targets")

def fm_ok_key(user_id: int) -> str:
    return instance_key(f"fm:ok:{user_id}")

def fm_rate_limit_key(user_id: int) -> str:
    return instance_key(f"fm:rl:{user_id}")

FM_VERIFY_LOCK = instance_key("fm:verify")

# ── Call Security ────────────────────────────────────────────────────────────

def callsec_state_key(chat_id: int) -> str:
    return instance_key(f"callsec:{chat_id}:state")


def callsec_user_key(chat_id: int, user_id: int) -> str:
    return instance_key(f"callsec:{chat_id}:user:{user_id}")


def callsec_report_cooldown_key(chat_id: int, user_id: int, reason: str, bucket: str) -> str:
    return instance_key(f"callsec:report:{chat_id}:{user_id}:{reason}:{bucket}")


def callsec_active_chat_key(chat_id: int) -> str:
    return instance_key(f"callsec:active:{chat_id}")


def callsec_callmap_key(call_id: int) -> str:
    return instance_key(f"callsec:callmap:{call_id}")

# ── Broadcast ────────────────────────────────────────────────────────────
BC_ACTIVE = instance_key("bc:active")

def bc_lock_key(broadcast_id: int) -> str:
    return instance_key(f"bc:{broadcast_id}")

def bc_cursor_key(broadcast_id: int) -> str:
    return instance_key(f"bc:{broadcast_id}:cursor")

def bc_sent_key(broadcast_id: int) -> str:
    return instance_key(f"bc:{broadcast_id}:sent")

def bc_fail_key(broadcast_id: int) -> str:
    return instance_key(f"bc:{broadcast_id}:fail")

# ── Broadcast wizard FSM ────────────────────────────────────────────────
def bcw_state_key(user_id: int) -> str:
    return instance_key(f"bcw:state:{user_id}")

# ── Analytics ────────────────────────────────────────────────────────────
ANALYTICS_FLUSH_LOCK = instance_key("analytics:flush")
AN_SCAN_PATTERN = instance_scan_pattern("an:*")

def an_counter_key(day: str, hour: str, scope: str, scope_key: str, metric: str) -> str:
    return instance_key(f"an:{day}:{hour}:{scope}:{scope_key}:{metric}")

# ── Distributed locks ────────────────────────────────────────────────────
def lock_key(key: str) -> str:
    return instance_key(f"lock:{strip_instance_namespace(key)}")

PG_ADVISORY_TOKEN_PREFIX = "pgadv:"

# ── Credit expiry ────────────────────────────────────────────────────────
CREDIT_EXPIRED_PENDING = instance_key("credit:expired_pending_leave")


def credit_expired_pending_member(chat_id: int, chat_type: str = "group") -> str:
    return f"{chat_type}:{chat_id}"


def daily_deduct_done_key(day_iso: str) -> str:
    """Redis set of chat keys already deducted on a calendar day (staging batching)."""
    return instance_key(f"cron:daily_deduct:done:{day_iso}")

# ── Playlist ─────────────────────────────────────────────────────────────
def playlist_lock_key(chat_id: int) -> str:
    return instance_key(f"playlist:{chat_id}")

# ── Seek tracker ─────────────────────────────────────────────────────────
SEEK_PREFIX = instance_key("seek:")

def seek_key(chat_id: int) -> str:
    return instance_key(f"seek:{chat_id}")

# ── Helper pool ──────────────────────────────────────────────────────────
def helper_join_lock_key(helper_id: int) -> str:
    return instance_key(f"helper:join:{helper_id}")

def helper_floodwait_key(helper_id: int) -> str:
    return instance_key(f"helper:floodwait:{helper_id}")

def helper_join_idem_key(helper_id: int, chat_id: int) -> str:
    return instance_key(f"idem:helper:join:{helper_id}:{chat_id}")

def helper_otp_state_key(user_id: int) -> str:
    return instance_key(f"wz:helper_otp:{user_id}")


def helper_proxy_state_key(user_id: int) -> str:
    return instance_key(f"wz:helper_proxy:{user_id}")

TTL_HELPER_OTP = 300


def helper_add_lock_key(phone: str) -> str:
    clean = str(phone).replace(" ", "").replace("-", "")
    return instance_key(f"helper:add:{clean}")

# ── Wizard navigation return token ──────────────────────────────────────
def wizard_return_key(user_id: int) -> str:
    return instance_key(f"wizard:return:{user_id}")


def panel_message_key(chat_id: int, user_id: int | None = None) -> str:
    """Redis key for the active panel message anchor (edit-first navigation)."""
    if user_id is None:
        return instance_key(f"panelmsg:{chat_id}")
    return instance_key(f"panelmsg:{chat_id}:{user_id}")


# ── Admin panel summaries ───────────────────────────────────────────────
def panel_summary_key(scope: str) -> str:
    return instance_key(f"panel:summary:{scope}")


# ── Direct YouTube download format choice ─────────────────────────────────
def download_choice_state_key(user_id: int, token: str) -> str:
    return instance_key(f"download:choice:{user_id}:{token}")

# ── Install ──────────────────────────────────────────────────────────────
def install_lock_key(chat_id: int) -> str:
    return instance_key(f"install:{chat_id}")

def install_fee_lock_key(chat_id: int, installer_id: int) -> str:
    return instance_key(f"install_fee:{chat_id}:{installer_id}")

# ── Wallet ───────────────────────────────────────────────────────────────
def wallet_lock_key(user_id: int) -> str:
    return instance_key(f"wallet:{user_id}")

# ── Chat (generic per-chat locks used in playlist etc.) ──────────────────
def chat_lock_key(chat_id: int) -> str:
    return instance_key(f"chat:{chat_id}")

# ── Media cache ──────────────────────────────────────────────────────────
MEDIA_CACHE_EVICTION_LOCK = instance_key("media_cache:evict")
MEDIA_HEALTH_YTDLP_PROBE = instance_key("media_health:ytdlp_probe")
MEDIA_HEALTH_LAST_EVICTION = instance_key("media_health:last_eviction")
MEDIA_HEALTH_LAST_CLEANUP = instance_key("media_health:last_cleanup")
TTL_MEDIA_HEALTH_PROBE = 300
TTL_YOUTUBE_REKEY_LOCK = 900
TTL_YOUTUBE_REKEY_CONFIRM = 300
TTL_YOUTUBE_SESSION_UNAVAILABLE_ALERT = 21_600
TTL_YOUTUBE_SESSION_EXPIRY_ALERT = 172_800
TTL_FAST_CREAT_TOKEN_DELETE_CONFIRM = 300


def youtube_rekey_lock_key() -> str:
    return instance_key("youtube:sessions:rekey:lock")


def youtube_session_unavailable_alert_key() -> str:
    return instance_key("youtube:sessions:alert:unavailable")


def youtube_session_expiry_alert_key(session_id: int, date_iso: str) -> str:
    return instance_key(f"youtube:sessions:alert:expiry:{session_id}:{date_iso}")


def youtube_rekey_confirm_claim_key(user_id: int, issued_at: int) -> str:
    return instance_key(f"youtube:sessions:rekey:claim:{user_id}:{issued_at}")


def fast_creat_token_delete_claim_key(token_id: int, user_id: int, issued_at: int) -> str:
    return instance_key(
        f"fastcreat:token:delete:claim:{token_id}:{user_id}:{issued_at}"
    )

def media_cache_hit_key(url_hash: str) -> str:
    return instance_key(f"media_cache:hit:{url_hash}")

# ── Prefetch ─────────────────────────────────────────────────────────────
def prefetch_key(chat_id: int) -> str:
    return instance_key(f"prefetch:{chat_id}")

# ── Filter words / Sudo list / Owner list ────────────────────────────────
FILTERWORDS = instance_key("filterwords")
SUDOLIST = instance_key("sudolist")
OWNERLIST = instance_key("ownerlist")
