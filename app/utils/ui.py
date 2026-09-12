from __future__ import annotations

from typing import TYPE_CHECKING

from pyrogram.enums import ButtonStyle
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.utils.button_style import (
    compatible_inline_button as _compatible_inline_button,
    mark_explicit_style_override,
    mark_toggle_state,
)
from app.utils.i18n import label as localized_label, t

if TYPE_CHECKING:
    from app.services.media_capability_service import MediaCapabilities

# ---------------------------------------------------------------------------
# Callback-data constants (stable English identifiers, never translated)
# ---------------------------------------------------------------------------
CB = {
    # ── Start menu ──
    "START_FORCE_JOIN": "start:force_join",
    "START_PRICING": "start:pricing",
    # ── Playback types ──
    "PB_AUDIO": "pb:type:audio",
    "PB_VIDEO": "pb:type:video",
    "PB_TV": "pb:type:tv",
    "PB_SAT": "pb:type:satellite",
    "PB_RADIO": "pb:type:radio",
    "PB_DOWNLOAD": "pb:type:download",
    # ── Playback controls ──
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
    # ── Developer panel ──
    "DEV_STATUS": "dev:status",
    "DEV_INCREASE_CREDIT": "dev:credit:inc",
    "DEV_DECREASE_CREDIT": "dev:credit:dec",
    "DEV_SEND_INVOICE": "dev:invoice",
    "DEV_INVOICE_HISTORY": "dev:inv:hist",
    "DEV_CAT_MONTHLY_INVOICE": "dev:monthly_invoice",
    "DEV_MONTHLY_INVOICE_CONFIG": "dev:monthly_invoice:config",
    "DEV_MONTHLY_INVOICE_SET_AMOUNT": "dev:monthly_invoice:set_amount",
    "DEV_MONTHLY_INVOICE_PREPARE": "dev:monthly_invoice:prepare",
    "DEV_MONTHLY_INVOICE_SEND": "dev:monthly_invoice:send",
    "DEV_MONTHLY_INVOICE_AUTO_TOGGLE": "dev:monthly_invoice:auto_toggle",
    "DEV_MONTHLY_INVOICE_LIST": "dev:monthly_invoice:list",
    "DEV_MONTHLY_INVOICE_DETAIL_PREFIX": "dev:monthly_invoice:detail:",
    "DEV_SET_BASE_RATE": "dev:rate:base",
    "DEV_SET_MUSIC_RATE": "dev:rate:music",
    "DEV_SET_VIDEO_RATE": "dev:rate:video",
    "DEV_SET_CALL_SECURITY_RATE": "dev:rate:call_security",
    "DEV_BROADCAST_GROUP": "dev:bc:group",
    "DEV_FORWARD_GROUP": "dev:fw:group",
    "DEV_BROADCAST_PRIVATE": "dev:bc:private",
    "DEV_FORWARD_PRIVATE": "dev:fw:private",
    "DEV_BROADCAST_CHANNEL": "dev:bc:channel",
    "DEV_FORWARD_CHANNEL": "dev:fw:channel",
    "DEV_FORCE_JOIN_TOGGLE": "dev:force_join",
    "DEV_CHANNEL_SECURITY_TOGGLE": "dev:channel_security",
    "DEV_SET_MEDIA_POLICY": "dev:media_policy",
    "DEV_AUTO_LEAVE_TOGGLE": "dev:auto_leave",
    "DEV_TRIAL_TOGGLE": "dev:trial",
    "DEV_BOT_ENABLED_TOGGLE": "dev:bot_enabled",
    "DEV_SUDO_PANEL_ENABLED_TOGGLE": "dev:sudo_panel_enabled",
    "DEV_MEDIA_HEALTH": "dev:media_health",
    "DEV_YOUTUBE_SESSIONS": "dev:yt_sessions",
    "DEV_FAST_CREAT_TOKENS": "dev:fast_creat_tokens",
    "DEV_MEDIA_EXPORT": "dev:media:export",
    "DEV_MEDIA_CLEANUP_PREVIEW": "dev:media:cleanup:preview",
    "DEV_MEDIA_CLEANUP_EXEC_PREFIX": "dev:media:cleanup:do:",
    "DEV_MEDIA_CLEANUP_ABORT_PREFIX": "dev:media:cleanup:no:",
    "DEV_MEDIA_RANKING": "dev:media:ranking",
    "DEV_BOT_UPDATE": "dev:bot_update",
    "DEV_BOT_UPDATE_REFRESH": "dev:bot_update:refresh",
    "DEV_BOT_UPDATE_RELOAD": "dev:bot_update:reload",
    "DEV_BOT_UPDATE_RELOAD_EXEC_PREFIX": "dev:bot_update:reload:do:",
    "DEV_BOT_UPDATE_RELOAD_ABORT_PREFIX": "dev:bot_update:reload:no:",
    "DEV_LIST_GROUPS": "dev:list:groups",
    "DEV_LIST_CHANNELS": "dev:list:channels",
    "DEV_LIST_NO_CREDIT": "dev:list:no_credit",
    "DEV_LIST_UNLIMITED_GROUPS": "dev:list:unlimited_groups",
    "DEV_LIST_CALL_SECURITY_GROUPS": "dev:list:call_security_groups",
    "DEV_LIST_PLAYBACK_GROUPS": "dev:list:playback_groups",
    "DEV_LIST_TEST_GROUPS": "dev:list:test_groups",
    "DEV_LIST_MUSIC_GROUPS": "dev:list:music_groups",
    "DEV_LIST_VIDEO_GROUPS": "dev:list:video_groups",
    "DEV_LIST_INACTIVE_GROUPS": "dev:list:inactive_groups",
    "DEV_LIST_INACTIVE_CHANNELS": "dev:list:inactive_channels",
    "DEV_LIST_ACTIVE_USERS": "dev:list:active_users",
    "DEV_LIST_INACTIVE_USERS": "dev:list:inactive_users",
    "DEV_FILTERS": "dev:filters",
    "DEV_FORCE_JOIN_MANAGE": "dev:force_join_manage",
    "DEV_FORCE_JOIN_ADD": "dev:fj:add",
    "DEV_FORCE_JOIN_LIST": "dev:fj:list",
    "DEV_FORCE_JOIN_PAGE_PREFIX": "dev:fj:pg:",
    "DEV_FORCE_JOIN_REMOVE_PREFIX": "dev:fj:rm:",
    "DEV_FORCE_JOIN_REMOVE_EXEC_PREFIX": "dev:fj:rm:do:",
    "DEV_FORCE_JOIN_REMOVE_ABORT_PREFIX": "dev:fj:rm:no:",
    "DEV_SUDO_MANAGE": "dev:sudo_manage",
    "DEV_SET_OWNER": "dev:set_owner",
    "DEV_LIST_OWNERS": "dev:owners:list",
    "DEV_LIST_SUDOS": "dev:sudos:list",
    "DEV_REMOVE_OWNER": "dev:owner:remove",
    "DEV_REMOVE_SUDO": "dev:sudo:remove",
    "DEV_TEXTS_LINKS": "dev:texts_links",
    "DEV_TEXTS_HOME": "dev:texts:home",
    "DEV_TEXT_FIELD_PREFIX": "dev:text:f:",
    "DEV_TEXT_SET_TEXT_PREFIX": "dev:text:txt:",
    "DEV_TEXT_SET_MEDIA_PREFIX": "dev:text:med:",
    "DEV_TEXT_CLEAR_PREFIX": "dev:text:clr:",
    "DEV_TEXT_CLEAR_EXEC_PREFIX": "dev:text:clr:do:",
    "DEV_TEXT_CLEAR_ABORT_PREFIX": "dev:text:clr:no:",
    "DEV_TEXT_PREVIEW_PREFIX": "dev:text:prv:",
    "DEV_START_STYLE_TOGGLE": "dev:start:style:toggle",
    "DEV_OWNER_REMOVE_EXEC_PREFIX": "dev:owner:rm:do:",
    "DEV_OWNER_REMOVE_ABORT_PREFIX": "dev:owner:rm:no:",
    "DEV_SUDO_REMOVE_EXEC_PREFIX": "dev:sudo:rm:do:",
    "DEV_SUDO_REMOVE_ABORT_PREFIX": "dev:sudo:rm:no:",
    "DEV_TEXTS_BACK": "dev:texts:back",
    "DEV_INSTALL_LIMITS": "dev:install_limits",
    "DEV_BLACKLIST": "dev:blacklist",
    "DEV_BANALL_HOME": "dev:banall:home",
    "DEV_BANALL_ADD": "dev:banall:add",
    "DEV_BANALL_REMOVE": "dev:banall:remove",
    "DEV_BANALL_LIST_PREFIX": "dev:banall:list:",
    "DEV_BANALL_CLEAR": "dev:banall:clear",
    "DEV_BANALL_CLEAR_DO_PREFIX": "dev:banall:clear:do:",
    "DEV_BANALL_CLEAR_NO_PREFIX": "dev:banall:clear:no:",
    "DEV_BANALL_RM_PREFIX": "dev:banall:rm:",
    "DEV_SET_LOG_CHANNEL": "dev:log_channel",
    "DEV_SET_MUSIC_SELL_RATE": "dev:rate:music_sell",
    "DEV_SET_VIDEO_SELL_RATE": "dev:rate:video_sell",
    "DEV_SEND_TO_SUDO": "dev:send_sudo",
    "DEV_LIST_RENEWAL_GROUPS": "dev:list:renewal",
    "DEV_LEAVE_GROUP": "dev:leave_group",
    "DEV_LEAVE_CONFIRM_PREFIX": "dev:leave:cfm:",
    "DEV_LEAVE_EXEC_PREFIX": "dev:leave:do:",
    "DEV_LEAVE_CANCEL_PREFIX": "dev:leave:no:",
    "DEV_LIST_DETAIL_PREFIX": "dev:list:detail:",
    "DEV_LIST_CREDIT_INC_PREFIX": "dev:list:credit:inc:",
    "DEV_LIST_CREDIT_DEC_PREFIX": "dev:list:credit:dec:",
    "DEV_LIST_BACK_PREFIX": "dev:list:back:",
    "DEV_SUDO_DETAIL_PREFIX": "dev:sudo:detail:",
    "DEV_SUDO_LIST_BACK_PREFIX": "dev:sudo:back:",
    "DEV_SUDO_PERM_TOGGLE_PREFIX": "dev:sp:t:",
    "DEV_SUDO_PERM_DO_PREFIX": "dev:sp:y:",
    "DEV_SUDO_PERM_NO_PREFIX": "dev:sp:n:",
    "DEV_ADMIN_TITLES": "dev:admin_titles",
    "DEV_TITLE_DEV_SET": "dev:title:dev:set",
    "DEV_TITLE_DEV_CLEAR": "dev:title:dev:clear",
    "DEV_TITLE_DEV_CLEAR_DO_PREFIX": "dev:title:dev:clear:do:",
    "DEV_TITLE_DEV_CLEAR_NO_PREFIX": "dev:title:dev:clear:no:",
    "DEV_TITLE_OWNER_LIST_PREFIX": "dev:title:own:list:",
    "DEV_TITLE_OWNER_SET_PREFIX": "dev:title:own:set:",
    "DEV_TITLE_OWNER_CLEAR_PREFIX": "dev:title:own:clear:",
    "DEV_TITLE_OWNER_CLEAR_DO_PREFIX": "dev:title:own:clear:do:",
    "DEV_TITLE_OWNER_CLEAR_NO_PREFIX": "dev:title:own:clear:no:",
    "DEV_TITLE_SUDO_SET_PREFIX": "dev:title:sudo:set:",
    "DEV_TITLE_SUDO_CLEAR_PREFIX": "dev:title:sudo:clear:",
    "DEV_TITLE_SUDO_CLEAR_DO_PREFIX": "dev:title:sudo:clear:do:",
    "DEV_TITLE_SUDO_CLEAR_NO_PREFIX": "dev:title:sudo:clear:no:",
    "DEV_TITLE_APPLY_DEV": "dev:title:apply:dev",
    "DEV_TITLE_APPLY_OWNER_PREFIX": "dev:title:apply:own:",
    "DEV_TITLE_APPLY_SUDO_PREFIX": "dev:title:apply:sudo:",
    "DEV_TITLE_APPLY_DO_PREFIX": "dev:title:apply:do:",
    "DEV_TITLE_APPLY_NO_PREFIX": "dev:title:apply:no:",
    "DEV_TGPROM_DEV": "dev:tgprom:dev",
    "DEV_TGPROM_OWNER_PREFIX": "dev:tgprom:own:",
    "DEV_TGPROM_SUDO_PREFIX": "dev:tgprom:sudo:",
    "DEV_TGPROM_DO_PREFIX": "dev:tgprom:do:",
    "DEV_TGPROM_NO_PREFIX": "dev:tgprom:no:",
    # ── Owner panel ──
    "OWN_STATS": "own:stats",
    "OWN_YOUTUBE_SESSIONS": "own:yt_sessions",
    "OWN_FAST_CREAT_TOKENS": "own:fast_creat_tokens",
    "OWN_GROUPS": "own:groups",
    "OWN_CREDIT": "own:credit",
    "OWN_CREDIT_ADD": "own:credit:add",
    "OWN_CREDIT_DEDUCT": "own:credit:deduct",
    "OWN_SUDOS": "own:sudos",
    "OWN_SUDO_TITLES": "own:sudo_titles",
    "OWN_START_TEXT": "own:start_text",
    "OWN_BROADCAST": "own:broadcast",
    "OWN_LISTS": "own:lists",
    "OWN_MODERATION": "own:moderation",
    "OWN_MEDIA": "own:media",
    "OWN_REPORTS": "own:reports",
    "OWN_LIST_GROUPS": "own:list:groups",
    "OWN_LIST_CHANNELS": "own:list:channels",
    "OWN_USERS": "own:users",
    "OWN_CREDIT_LINKS": "own:credit_links",
    "OWN_BROADCAST_GROUP": "own:bc:group",
    "OWN_FORWARD_GROUP": "own:fw:group",
    "OWN_BROADCAST_PRIVATE": "own:bc:private",
    "OWN_FORWARD_PRIVATE": "own:fw:private",
    "OWN_BROADCAST_CHANNEL": "own:bc:channel",
    "OWN_FORWARD_CHANNEL": "own:fw:channel",
    "OWN_FORCE_JOIN_TOGGLE": "own:force_join",
    "OWN_FORCE_JOIN_ENABLE_TOGGLE": "own:force_join:toggle",
    "OWN_CHANNEL_SECURITY_TOGGLE": "own:channel_security",
    "OWN_SET_MEDIA_POLICY": "own:media_policy",
    "OWN_MEDIA_AUDIO_TOGGLE": "own:media:audio",
    "OWN_MEDIA_VIDEO_TOGGLE": "own:media:video",
    "OWN_MEDIA_FILE_TOGGLE": "own:media:file",
    "OWN_MEDIA_DOWNLOAD_TOGGLE": "own:media:download",
    "OWN_MEDIA_BUTTONS_TOGGLE": "own:media:buttons",
    "OWN_AUTO_LEAVE_TOGGLE": "own:auto_leave",
    "OWN_TRIAL_TOGGLE": "own:trial",
    "OWN_LIST_GROUPS_CREDIT": "own:list_groups_credit",
    "OWN_LIST_CHANNELS_CREDIT": "own:list_channels_credit",
    "OWN_LIST_NO_CREDIT": "own:list:no_credit",
    "OWN_LIST_INACTIVE_GROUPS": "own:list:inactive_groups",
    "OWN_LIST_INACTIVE_CHANNELS": "own:list:inactive_channels",
    "OWN_LIST_RENEWAL": "own:list:renewal",
    "OWN_GRP_LIST_PREFIX": "own:grp:l:",
    "OWN_GRP_DETAIL_PREFIX": "own:grp:d:",
    "OWN_GRP_CREDIT_INC_PREFIX": "own:grp:ci:",
    "OWN_GRP_CREDIT_DEC_PREFIX": "own:grp:cd:",
    "OWN_GRP_LEAVE_CONFIRM_PREFIX": "own:grp:lv:",
    "OWN_GRP_LEAVE_EXEC_PREFIX": "own:grp:lv:do:",
    "OWN_GRP_LEAVE_CANCEL_PREFIX": "own:grp:lv:no:",
    "OWN_FILTERS": "own:filters",
    "OWN_FORCE_JOIN_MANAGE": "own:force_join_manage",
    "OWN_FORCE_JOIN_ADD": "own:fj:add",
    "OWN_FORCE_JOIN_LIST": "own:fj:list",
    "OWN_FORCE_JOIN_PAGE_PREFIX": "own:fj:pg:",
    "OWN_FORCE_JOIN_REMOVE_PREFIX": "own:fj:rm:",
    "OWN_FORCE_JOIN_REMOVE_DO_PREFIX": "own:fj:rm:do:",
    "OWN_FORCE_JOIN_REMOVE_NO_PREFIX": "own:fj:rm:no:",
    "OWN_SUDO_MANAGE": "own:sudo_manage",
    "OWN_SUDO_DETAIL_PREFIX": "own:sudo:detail:",
    "OWN_SUDO_LIST_BACK_PREFIX": "own:sudo:back:",
    "OWN_SUDO_PERM_TOGGLE_PREFIX": "own:sp:t:",
    "OWN_SUDO_PERM_DO_PREFIX": "own:sp:y:",
    "OWN_SUDO_PERM_NO_PREFIX": "own:sp:n:",
    "OWN_LIST_OWNERS": "own:owners:list",
    "OWN_LIST_SUDOS": "own:sudos:list",
    "OWN_REMOVE_OWNER": "own:owner:remove",
    "OWN_REMOVE_SUDO": "own:sudo:remove",
    "OWN_REMOVE_SUDO_DO_PREFIX": "own:sudo:remove:do:",
    "OWN_REMOVE_SUDO_NO_PREFIX": "own:sudo:remove:no:",
    "OWN_TITLE_SUDO_LIST_PREFIX": "own:title:sudo:list:",
    "OWN_TITLE_SUDO_SET_PREFIX": "own:title:sudo:set:",
    "OWN_TITLE_SUDO_CLEAR_PREFIX": "own:title:sudo:clear:",
    "OWN_TITLE_SUDO_CLEAR_DO_PREFIX": "own:title:sudo:clear:do:",
    "OWN_TITLE_SUDO_CLEAR_NO_PREFIX": "own:title:sudo:clear:no:",
    "OWN_TITLE_APPLY_SUDO_PREFIX": "own:title:apply:sudo:",
    "OWN_TITLE_APPLY_DO_PREFIX": "own:title:apply:do:",
    "OWN_TITLE_APPLY_NO_PREFIX": "own:title:apply:no:",
    "OWN_TGPROM_SUDO_PREFIX": "own:tgprom:sudo:",
    "OWN_TGPROM_DO_PREFIX": "own:tgprom:do:",
    "OWN_TGPROM_NO_PREFIX": "own:tgprom:no:",
    "OWN_INSTALL_LIMITS": "own:install_limits",
    "OWN_BLACKLIST": "own:blacklist",
    "OWN_INSTALL_REPORTS": "own:install_reports",
    "OWN_NO_CREDIT_REPORTS": "own:no_credit_reports",
    "OWN_BOT_CREDIT": "own:bot_credit",
    "OWN_INCREASE_BOT_CREDIT": "own:bot_credit:inc",
    "OWN_TOPUP_SUDO_WALLET": "own:topup_sudo",
    "OWN_BOT_INVOICES": "own:bot_invoices",
    "OWN_SALES_REPORT": "own:sales_report",
    "OWN_BANALL_HOME": "own:banall",
    "OWN_BANALL_ADD": "own:banall:add",
    "OWN_BANALL_ADD_DO_PREFIX": "own:banall:add:do:",
    "OWN_BANALL_ADD_NO_PREFIX": "own:banall:add:no:",
    "OWN_BANALL_LIST_PREFIX": "own:banall:list:",
    "OWN_BANALL_REMOVE": "own:banall:remove",
    "OWN_BANALL_RM_PREFIX": "own:banall:rm:",
    "OWN_BANALL_RM_DO_PREFIX": "own:banall:rm:do:",
    "OWN_BANALL_RM_NO_PREFIX": "own:banall:rm:no:",
    "OWN_BC_HISTORY": "own:bc:history",
    "OWN_BC_CANCEL_PREFIX": "own:bc:cancel:",
    "OWN_BC_CANCEL_DO_PREFIX": "own:bc:cancel:do:",
    "OWN_BC_CANCEL_NO_PREFIX": "own:bc:cancel:no:",
    "OWN_BC_CONFIRM_EXEC_PREFIX": "own:bc:x:",
    "OWN_BC_CONFIRM_CANCEL_PREFIX": "own:bc:xn:",
    "OWN_CAT_INSTALLS": "own:cat:installs",
    "OWN_CAT_BROADCAST": "own:cat:broadcast",
    "OWN_CAT_SETTINGS": "own:cat:settings",
    "OWN_CAT_USERS": "own:cat:users",
    "OWN_CAT_REPORTS": "own:cat:reports",
    "OWN_CAT_BILLING": "own:cat:billing",
    "OWN_CAT_TEXTS": "own:cat:texts",
    # ── Sudo panel ──
    "SUDO_STATUS": "sudo:status",
    "SUDO_GROUPS": "sudo:groups",
    "SUDO_CREDIT": "sudo:credit",
    "SUDO_PERMISSIONS": "sudo:permissions",
    "SUDO_LISTS": "sudo:lists",
    "SUDO_GROUP_SEARCH": "sudo:grp:search",
    "SUDO_GRP_LIST_PREFIX": "sudo:grp:l:",
    "SUDO_GRP_DETAIL_PREFIX": "sudo:grp:d:",
    "SUDO_GRP_CREDIT_INC_PREFIX": "sudo:grp:ci:",
    "SUDO_GRP_CREDIT_DEC_PREFIX": "sudo:grp:cd:",
    "SUDO_GRP_LEAVE_CONFIRM_PREFIX": "sudo:grp:lv:",
    "SUDO_GRP_LEAVE_EXEC_PREFIX": "sudo:grp:lv:do:",
    "SUDO_GRP_LEAVE_CANCEL_PREFIX": "sudo:grp:lv:no:",
    "SUDO_CREDIT_ADD": "sudo:credit:add",
    "SUDO_CREDIT_DEDUCT": "sudo:credit:deduct",
    "SUDO_CREDIT_LIST": "sudo:credit:list",
    "SUDO_CREDIT_NO_CREDIT": "sudo:credit:no_credit",
    "SUDO_CREDIT_RENEWAL": "sudo:credit:renewal",
    "SUDO_CREDIT_HISTORY": "sudo:credit:history",
    "SUDO_INSTALLS_REPORT": "sudo:installs_report",
    "SUDO_CREDIT_REPORT": "sudo:credit_report",
    "SUDO_STATS": "sudo:stats",
    "SUDO_LEAVE_INSTALLS": "sudo:leave_installs",
    "SUDO_LEAVE_CONFIRM_PREFIX": "sudo:leave_installs:confirm:",
    "SUDO_LEAVE_CANCEL_PREFIX": "sudo:leave_installs:cancel:",
    "SUDO_LOW_CREDIT": "sudo:low_credit",
    # ── Group panel ──
    "GRP_SETTINGS": "grp:settings",
    "GRP_MUSIC_VIDEO": "grp:set:music_video",
    "GRP_SECURITY_CALL": "grp:set:security_call",
    "GRP_CALLSEC": "grp:callsec",
    "GRP_CALLSEC_TOGGLE": "grp:callsec:toggle",
    "GRP_CALLSEC_OWNERS": "grp:callsec:owners",
    "GRP_CALLSEC_MUTE_IN": "grp:callsec:mute_in",
    "GRP_CALLSEC_SUMMARY": "grp:callsec:summary",
    "GRP_CALLSEC_REPORT": "grp:callsec:report",
    "GRP_CALLSEC_AGE": "grp:callsec:age",
    "GRP_CALLSEC_AGE_CANCEL": "grp:callsec:age:cancel",
    "GRP_CALLSEC_BACK": "grp:callsec:back",
    # ── VIP grant duration picker (ROLE-04) ──
    "VIP_DURATION_PREFIX": "vipd:",
    "GRP_REPEAT": "grp:set:repeat",
    "GRP_DOWNLOAD_USERS": "grp:set:download_users",
    "GRP_CALL_MESSAGE": "grp:set:call_message",
    "GRP_AUTO_CLEAN": "grp:set:auto_clean",
    # PANEL-04: evidenced "سرویس پلیر" join/service-message removal toggle.
    "GRP_SERVICE_CLEAN": "grp:set:service_clean",
    "GRP_QUEUE": "grp:set:queue",
    "GRP_AUTO_READY_CALL": "grp:set:auto_ready_call",
    "GRP_CALL_REPORT": "grp:set:call_report",
    "GRP_RECORD_CALL": "grp:set:record_call",
    "GRP_SHOW_ID": "grp:set:show_id",
    "GRP_SHOW_PHOTO": "grp:set:show_photo",
    "GRP_SHOW_TEXT": "grp:set:show_text",
    "GRP_CALL_STATS": "grp:set:call_stats",
    "GRP_ID_CALL_STATS": "grp:set:id_call_stats",
    "GRP_MANAGEMENT": "grp:management",
    "GRP_OWNERS_LIST": "grp:mgmt:owners",
    "GRP_DEPUTIES_LIST": "grp:mgmt:deputies",
    "GRP_ADMINS_LIST": "grp:mgmt:admins",
    "GRP_VIP_LIST": "grp:mgmt:vip",
    "GRP_VIP_DEMOTE_PREFIX": "grp:vip:rm:",
    "GRP_VIP_CLEAR_CONFIRM_PREFIX": "grp:vip:clear_confirm:",
    "GRP_VIP_CLEAR_CANCEL_PREFIX": "grp:vip:clear_cancel:",
    "PAGE_VIP": "pg:vip:",
    "GRP_CLEAR_ALL": "grp:mgmt:clear_all",
    "GRP_CLEAR_CONFIRM_PREFIX": "grp:mgmt:clear_confirm:",
    "GRP_CLEAR_CANCEL_PREFIX": "grp:mgmt:clear_cancel:",
    "GRP_HELP": "grp:help",
    "GRP_PROMOTE_DEMOTE": "grp:help:promote_demote",
    "GRP_PLAY_COMMANDS": "grp:help:play_commands",
    "GRP_GENERAL_COMMANDS": "grp:help:general_commands",
    "GRP_MANAGER_COMMANDS": "grp:help:manager_commands",
    "GRP_CALL_COMMANDS": "grp:help:call_commands",
    "GRP_SUPPORT_REQUEST": "grp:help:support_request",
    "GRP_SUPPORT": "grp:support",
    "GRP_CREATOR": "grp:support:creator",
    "GRP_SUDO": "grp:support:sudo",
    "GRP_GUIDE_CHANNEL": "grp:support:guide_channel",
    "GRP_SUPPORT_GROUP": "grp:support:support_group",
    # PANEL-02: evidenced 'کانال ربات' and 'پیامرسان' support links.
    "GRP_BOT_CHANNEL": "grp:support:bot_channel",
    "GRP_MESSENGER": "grp:support:messenger",
    "GRP_DEFAULT_MEDIA_TYPE": "grp:set:default_media",
    "GRP_LANGUAGE": "grp:set:language",
    "INSTALL_SETUP_PREFIX": "Add:Fa:",
    # ── Install policy (developer) ──
    "DEV_INSTALL_POLICY": "dev:install_policy",
    "DEV_INSTALL_POLICY_MODE": "dev:install_policy:mode",
    "DEV_INSTALL_POLICY_TRIAL": "dev:install_policy:trial",
    "DEV_INSTALL_POLICY_FEE_GROUP": "dev:install_policy:fee_group",
    "DEV_INSTALL_POLICY_FEE_CHAN": "dev:install_policy:fee_chan",
    "DEV_INSTALL_POLICY_WHITELIST": "dev:install_policy:whitelist",
    "DEV_INSTALL_POLICY_WHITELIST_ADD": "dev:install_policy:wl_add",
    "DEV_INSTALL_POLICY_WHITELIST_RM": "dev:install_policy:wl_rm",
    "DEV_INSTALL_POLICY_WHITELIST_LIST": "dev:install_policy:wl_list",
    "DEV_ABOUT": "dev:about",
    # ── Sudo link management ──
    "DEV_SUDO_LINK_MENU": "dev:sudo:link:menu",
    "DEV_SUDO_LINK_SET": "dev:sudo:link:set",
    "DEV_SUDO_LINK_RM": "dev:sudo:link:rm",
    "DEV_SUDO_LINK_LIST": "dev:sudo:link:list",
    # ── Owner extras ──
    "OWN_TEXTS_LINKS": "own:texts_links",
    "OWN_TEXTS_HOME": "own:texts:home",
    "OWN_TEXT_FIELD_PREFIX": "own:text:f:",
    "OWN_TEXT_SET_TEXT_PREFIX": "own:text:txt:",
    "OWN_TEXT_SET_MEDIA_PREFIX": "own:text:med:",
    "OWN_TEXT_CLEAR_PREFIX": "own:text:clr:",
    "OWN_TEXT_CLEAR_EXEC_PREFIX": "own:text:clr:do:",
    "OWN_TEXT_CLEAR_ABORT_PREFIX": "own:text:clr:no:",
    "OWN_TEXT_PREVIEW_PREFIX": "own:text:prv:",
    "OWN_START_STYLE_TOGGLE": "own:start:style:toggle",
    "OWN_TEXTS_BACK": "own:texts:back",
    # ── Start extras ──
    "START_ABOUT": "start:about",
    # ── Post-install panel ──
    "POST_INSTALL_INC_CREDIT": "postinst:credit:inc",
    "POST_INSTALL_DEC_CREDIT": "postinst:credit:dec",
    "POST_INSTALL_PANEL": "postinst:panel",
    "POST_INSTALL_HELP": "postinst:help",
    # ── Favorites ──
    "PB_FAV_LIST": "pb:fav:list",
    "FAV_INFO_PREFIX": "fav:info:",
    # ── Navigation ──
    "NAV_BACK": "nav:back",
    "NAV_CLOSE": "nav:close",
    # Private management-panel root → normal /start home
    "NAV_START": "nav:start",
    "WZ_HOME": "wz:home",
    "WZ_CANCEL_PREFIX": "wz:cancel:",
    "WZ_BACK_PREFIX": "wz:back:",
    "NOOP": "noop",
    # ── Forced Membership admin panel ──
    "FM_PANEL": "fm:panel",
    "FM_TOGGLE": "fm:toggle",
    "FM_LIST": "fm:list",
    "FM_ADD": "fm:add",
    "FM_REMOVE": "fm:rm",
    "FM_REMOVE_EXEC_PREFIX": "fm:rm:do:",
    "FM_REMOVE_ABORT_PREFIX": "fm:rm:no:",
    "FM_VERIFY_ALL": "fm:verify",
    "FM_TEST": "fm:test",
    # ── Broadcast V2 ──
    "BC_HISTORY": "bc:history",
    "BC_DETAIL": "bc:detail",
    "BC_CANCEL": "bc:cancel",
    "BC_CANCEL_CONFIRM_PREFIX": "bc:cancel:confirm:",
    "BC_CANCEL_ABORT_PREFIX": "bc:cancel:abort:",
    # ── Analytics panel ──
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
    # ── Helper Management panel ──
    "HLP_HOME": "hlp:home",
    "HLP_LIST": "hlp:list",
    "HLP_ADD": "hlp:add",
    "HLP_IMPORT_SESSION": "hlp:import",
    "HLP_ROTATE_KEY": "hlp:rotkey",
    "HLP_ROTATE_CONFIRM_PREFIX": "hlp:rotkey:confirm:",
    "HLP_ROTATE_CANCEL_PREFIX": "hlp:rotkey:cancel:",
    "HLP_HEALTH_CHECK": "hlp:health",
    "HLP_ADD_OTP": "hlp:add:otp",
    "HLP_SET_PROXY_PREFIX": "hlp:proxy:",
    "HLP_STATS": "hlp:stats",
    "HLP_DETAIL_PREFIX": "hlp:d:",
    "HLP_ENABLE": "hlp:en:",
    "HLP_ENABLE_CONFIRM_PREFIX": "hlp:en:do:",
    "HLP_ENABLE_ABORT_PREFIX": "hlp:en:no:",
    "HLP_DISABLE": "hlp:dis:",
    "HLP_DISABLE_CONFIRM_PREFIX": "hlp:dis:do:",
    "HLP_DISABLE_ABORT_PREFIX": "hlp:dis:no:",
    "HLP_QUARANTINE": "hlp:q:",
    "HLP_QUARANTINE_CONFIRM_PREFIX": "hlp:q:do:",
    "HLP_QUARANTINE_ABORT_PREFIX": "hlp:q:no:",
    "HLP_UNQUARANTINE": "hlp:uq:",
    "HLP_UNQUARANTINE_CONFIRM_PREFIX": "hlp:uq:do:",
    "HLP_UNQUARANTINE_ABORT_PREFIX": "hlp:uq:no:",
    # ── Help Center ──
    "HELP_HOME": "h:home",
    "HELP_GETTING_STARTED": "h:start",
    "HELP_PLAYBACK": "h:play",
    "HELP_CONTROLS": "h:ctrl",
    "HELP_PLAYLIST": "h:plist",
    "HELP_RADIO": "h:radio",
    "HELP_DOWNLOADS": "h:dl",
    "HELP_GROUP_PANEL": "h:grp",
    "HELP_SUDO_PANEL": "h:sudo",
    "HELP_OWNER_PANEL": "h:own",
    "HELP_DEV_PANEL": "h:dev",
    "HELP_FORCEJOIN": "h:fj",
    "HELP_TROUBLESHOOT": "h:trouble",
    "HELP_ABOUT": "h:about",
    "HELP_GROUP_COMMANDS": "h:grp_cmds",
    "HELP_CALL_COMMANDS": "h:call_cmds",
    "HELP_MANAGER_COMMANDS": "h:mgr_cmds",
    "HELP_PRIVATE_COMMANDS": "h:pm_cmds",
    "HELP_PUBLIC": "h:public",
    "HELP_PROMOTE": "h:promote",
    "HELP_PLAY_REPLY": "h:play:reply",
    "HELP_PLAY_LINK": "h:play:link",
    "HELP_PLAY_AUTO_MUSIC": "h:play:auto_music",
    "HELP_PLAY_AUTO_VIDEO": "h:play:auto_video",
    "HELP_PLAY_YOUTUBE": "h:play:youtube",
    "HELP_PLAY_RADIO": "h:play:radio",
    "HELP_PLAY_SERIAL": "h:play:serial",
    "HELP_PLAY_TV": "h:play:tv",
    "HELP_PLAY_SATELLITE": "h:play:satellite",
    "HELP_PLAY_CONTROLS": "h:play:controls",
    "HELP_PUBLIC_GROUP": "h:public:group",
    "HELP_PUBLIC_USER": "h:public:user",
    "HELP_PROMOTE_DEPUTY": "h:promote:deputy",
    "HELP_PROMOTE_ADMIN": "h:promote:admin",
    "HELP_PROMOTE_VIP": "h:promote:vip",
    "HELP_UTILITY": "h:utility",
    "HELP_CLOSE": "h:close",
    # ── Broadcast wizard ──
    "BCW_START": "bcw:start",
    "BCW_MODE_SEND": "bcw:mode:send",
    "BCW_MODE_FWD": "bcw:mode:fwd",
    "BCW_TGT_USERS": "bcw:tgt:users",
    "BCW_TGT_GROUPS": "bcw:tgt:groups",
    "BCW_TGT_CHANNELS": "bcw:tgt:chans",
    "BCW_TGT_NEXT": "bcw:tgt:next",
    "BCW_FILTER_ALL": "bcw:flt:all",
    "BCW_FILTER_7D": "bcw:flt:7d",
    "BCW_FILTER_30D": "bcw:flt:30d",
    "BCW_SEND_NOW": "bcw:sched:now",
    "BCW_SEND_AT": "bcw:sched:at",
    "BCW_SEND_AFTER": "bcw:sched:after",
    "BCW_SEND_RECURRING": "bcw:sched:recur",
    "BCW_CONFIRM": "bcw:confirm",
    "BCW_CANCEL": "bcw:cancel",
    "BCW_BACK_MODE": "bcw:back:mode",
    "BCW_BACK_TGT": "bcw:back:tgt",
    "BCW_BACK_FILTER": "bcw:back:flt",
    "BCW_BACK_SCHED": "bcw:back:sched",
    # ── Dev panel sub-menus ──
    "DEV_CAT_CREDIT": "dev:cat:credit",
    "DEV_CAT_RATES": "dev:cat:rates",
    "DEV_CAT_BROADCAST": "dev:cat:bc",
    "DEV_CAT_LISTS": "dev:cat:lists",
    "DEV_CAT_SETTINGS": "dev:cat:settings",
    "DEV_CAT_USERS": "dev:cat:users",
    "DEV_CAT_FORCE_JOIN": "dev:cat:force_join",
    "DEV_CAT_MODERATION": "dev:cat:moderation",
    "DEV_CAT_TEXTS": "dev:cat:texts",
    # ── Pagination ──
    "PAGE_DEV_GROUPS": "pg:dg:",
    "PAGE_DEV_CHANNELS": "pg:dc:",
    "PAGE_DEV_NO_CREDIT": "pg:dnc:",
    "PAGE_DEV_RENEWAL": "pg:drn:",
    "PAGE_DEV_LISTX_PREFIX": "pg:dx:",
    "PAGE_DEV_USERS_PREFIX": "pg:dux:",
    "PAGE_DEV_OWNERS": "pg:dev:owners:",
    "PAGE_DEV_SUDOS": "pg:dev:sudos:",
    "PAGE_OWN_OWNERS": "pg:own:owners:",
    "PAGE_OWN_SUDOS": "pg:own:sudos:",
    "PAGE_FAV": "pg:fav:",
    "FAV_RM_PREFIX": "fav:rm:",
    # ── YouTube cookie sessions ──
    "YT_SESSION_HOME": "yts:home",
    "YT_SESSION_ADD": "yts:add",
    "YT_SESSION_LIST_PREFIX": "yts:list:",
    "YT_SESSION_DETAIL_PREFIX": "yts:detail:",
    "YT_SESSION_TEST_PREFIX": "yts:test:",
    "YT_SESSION_ENABLE_PREFIX": "yts:enable:",
    "YT_SESSION_DISABLE_PREFIX": "yts:disable:",
    "YT_SESSION_DELETE_PROMPT_PREFIX": "yts:del:prompt:",
    "YT_SESSION_DELETE_CONFIRM_PREFIX": "yts:del:confirm:",
    "YT_SESSION_DELETE_CANCEL_PREFIX": "yts:del:cancel:",
    "YT_SESSION_REKEY_PROMPT": "yts:rekey:prompt",
    "YT_SESSION_REKEY_CONFIRM_PREFIX": "yts:rekey:confirm:",
    "YT_SESSION_REKEY_CANCEL_PREFIX": "yts:rekey:cancel:",
    # ── Fast-Creat API tokens ──
    "FAST_CREAT_HOME": "fct:home",
    "FAST_CREAT_PROVIDER_PREFIX": "fct:provider:",
    "FAST_CREAT_ADD_PREFIX": "fct:add:",
    "FAST_CREAT_LIST_PREFIX": "fct:list:",
    "FAST_CREAT_DETAIL_PREFIX": "fct:detail:",
    "FAST_CREAT_ENABLE_PREFIX": "fct:enable:",
    "FAST_CREAT_DISABLE_PREFIX": "fct:disable:",
    "FAST_CREAT_DELETE_PROMPT_PREFIX": "fct:del:prompt:",
    "FAST_CREAT_DELETE_CONFIRM_PREFIX": "fct:del:confirm:",
    "FAST_CREAT_DELETE_CANCEL_PREFIX": "fct:del:cancel:",
    # ── Direct YouTube download format choice ──
    "DOWNLOAD_FORMAT_PREFIX": "dl:fmt:",
}

# Full mapping (handlers + stale callbacks); keyboard uses visible subset only.
_GRP_SETTING_TOGGLES: dict[str, str] = {
    "music_video": CB["GRP_MUSIC_VIDEO"],
    "security_call": CB["GRP_SECURITY_CALL"],
    "repeat": CB["GRP_REPEAT"],
    "download_users": CB["GRP_DOWNLOAD_USERS"],
    "call_message": CB["GRP_CALL_MESSAGE"],
    "auto_clean": CB["GRP_AUTO_CLEAN"],
    "service_clean": CB["GRP_SERVICE_CLEAN"],
    "queue": CB["GRP_QUEUE"],
    "auto_ready_call": CB["GRP_AUTO_READY_CALL"],
    "call_report": CB["GRP_CALL_REPORT"],
    "record_call": CB["GRP_RECORD_CALL"],
    "show_id": CB["GRP_SHOW_ID"],
    "show_photo": CB["GRP_SHOW_PHOTO"],
    "show_text": CB["GRP_SHOW_TEXT"],
    "call_stats": CB["GRP_CALL_STATS"],
    "id_call_stats": CB["GRP_ID_CALL_STATS"],
}

_GRP_VISIBLE_SETTING_TOGGLES: dict[str, str] = {
    "call_security": CB["GRP_CALLSEC"],
    "security_call": CB["GRP_SECURITY_CALL"],
    "download_users": CB["GRP_DOWNLOAD_USERS"],
    "auto_clean": CB["GRP_AUTO_CLEAN"],
    "service_clean": CB["GRP_SERVICE_CLEAN"],
    "call_message": CB["GRP_CALL_MESSAGE"],
    "auto_ready_call": CB["GRP_AUTO_READY_CALL"],
    "music_video": CB["GRP_MUSIC_VIDEO"],
    "language": CB["GRP_LANGUAGE"],
    "default_media_type": CB["GRP_DEFAULT_MEDIA_TYPE"],
    "call_report": CB["GRP_CALL_REPORT"],
    "queue": CB["GRP_QUEUE"],
    "show_id": CB["GRP_SHOW_ID"],
    "show_photo": CB["GRP_SHOW_PHOTO"],
    "show_text": CB["GRP_SHOW_TEXT"],
    "call_stats": CB["GRP_CALL_STATS"],
    "id_call_stats": CB["GRP_ID_CALL_STATS"],
}

# Semantic row groups for group settings keyboard (keys must exist in _GRP_VISIBLE_SETTING_TOGGLES).
_GRP_SETTING_ROWS: tuple[tuple[str, ...], ...] = (
    ("call_security",),
    ("security_call", "music_video"),
    ("download_users", "auto_clean"),
    ("service_clean",),
    ("call_message", "call_report"),
    ("auto_ready_call", "queue"),
    ("show_id", "show_photo", "show_text"),
    ("call_stats",),
    ("id_call_stats",),
    ("default_media_type",),
    ("language",),
)


def _group_setting_button(
    lang: str, key: str, settings_dict: dict[str, bool]
) -> InlineKeyboardButton:
    """Build one group-settings button (toggle, language, media type, or sub-panel)."""
    cb_data = _GRP_VISIBLE_SETTING_TOGGLES[key]
    if key == "language":
        chat_lang = str(settings_dict.get("language", "fa")).lower()
        if chat_lang not in ("fa", "en"):
            chat_lang = "fa"
        label = t(lang, f"panels.group.settings.language_button_{chat_lang}")
        return _btn(label, cb_data)
    if key == "default_media_type":
        from app.services.media_capability_service import normalize_default_media_type

        media = normalize_default_media_type(settings_dict.get("default_media_type"))
        label = t(lang, f"panels.group.settings.default_media_button_{media}")
        return _btn(label, cb_data)
    if key == "call_security":
        return _btn(t(lang, "panels.group.settings.call_security"), cb_data)
    base = t(lang, f"panels.group.settings.{key}")
    enabled = bool(settings_dict.get(key, False))
    return _btn(
        toggle_label(lang, base, enabled),
        cb_data,
        toggle_state=enabled,
    )


def compatible_inline_button(
    label: str,
    *,
    style: ButtonStyle = ButtonStyle.DEFAULT,
    **kwargs,
) -> InlineKeyboardButton:
    """Backward-compatible re-export of the centralized constructor."""

    return _compatible_inline_button(label, style=style, **kwargs)


def _btn(
    label: str,
    callback_data: str,
    *,
    style: ButtonStyle = ButtonStyle.DEFAULT,
    toggle_state: bool | None = None,
) -> InlineKeyboardButton:
    button = compatible_inline_button(
        label,
        callback_data=callback_data,
        style=style,
    )
    if toggle_state is not None:
        mark_toggle_state(button, toggle_state)
    return button


def _help_cb(callback_data: str, user_id: int | None = None) -> str:
    if user_id is None:
        return callback_data
    return f"{callback_data}:U{int(user_id)}"


def _help_btn(lang: str, label_key: str, callback_data: str, user_id: int | None) -> InlineKeyboardButton:
    return _btn(t(lang, f"help.btn.{label_key}"), _help_cb(callback_data, user_id))


def _url_btn(label: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, url=url)


_START_BUTTON_STYLES = {
    "R": ButtonStyle.DANGER,
    "G": ButtonStyle.SUCCESS,
    "B": ButtonStyle.PRIMARY,
    "N": ButtonStyle.DEFAULT,
}


def _install_setup_cb(action: str, chat_id: int, user_id: int, issued_at: int) -> str:
    return f"{CB['INSTALL_SETUP_PREFIX']}{action}:G{int(chat_id)}:U{int(user_id)}:T{int(issued_at)}"


def _install_setup_state_icon(lang: str, enabled: bool) -> str:
    key = "state_on" if enabled else "state_off"
    return t(lang, f"panels.group.install_setup.{key}")


def _chunk_buttons(
    buttons: list[InlineKeyboardButton], size: int = 2
) -> list[list[InlineKeyboardButton]]:
    """Split buttons into rows of *size* (last row may be shorter)."""
    return [buttons[i : i + size] for i in range(0, len(buttons), size)]


def _chunk_by_width(
    buttons: list[InlineKeyboardButton],
    *,
    max_chars: int = 18,
    max_row_size: int = 2,
) -> list[list[InlineKeyboardButton]]:
    """Group buttons into rows; pair only when labels are short enough."""
    rows: list[list[InlineKeyboardButton]] = []
    pending: list[InlineKeyboardButton] = []
    for btn in buttons:
        label_len = len(btn.text or "")
        if label_len > max_chars:
            if pending:
                rows.extend(_chunk_buttons(pending, size=max_row_size))
                pending = []
            rows.append([btn])
            continue
        pending.append(btn)
        if len(pending) >= max_row_size:
            rows.append(pending)
            pending = []
    if pending:
        rows.append(pending)
    return rows


def _nav_row(
    lang: str,
    *,
    back_cb: str | None = None,
    home_cb: str | None = None,
    close_cb: str | None = None,
) -> list[InlineKeyboardButton]:
    """Build a single navigation row (back / home / close)."""
    nav: list[InlineKeyboardButton] = []
    if back_cb is not None:
        nav.append(_btn(t(lang, "common.buttons.back"), back_cb))
    if home_cb is not None:
        nav.append(_btn(t(lang, "common.buttons.home"), home_cb))
    if close_cb is not None:
        nav.append(_btn(t(lang, "common.buttons.close"), close_cb))
    return nav


def toggle_label(lang: str, base: str, enabled: bool) -> str:
    """Build a toggle button label with localized state emoji appended."""
    state_key = "status_indicator.active" if enabled else "status_indicator.inactive"
    return f"{base} {t(lang, state_key)}"


class KeyboardFactory:
    """Builds all inline keyboards. Every visible label comes from ``t()``."""

    # ── Start menu ────────────────────────────────────────────────────────

    @staticmethod
    def start_menu(lang: str, links: dict[str, str]) -> InlineKeyboardMarkup | None:
        def _link(name: str) -> str:
            return str(links.get(name) or "").strip()

        def _url_or_none(label_key: str, name: str) -> InlineKeyboardButton | None:
            url = _link(name)
            if not url:
                return None
            return _url_btn(t(lang, label_key), url)

        rows: list[list[InlineKeyboardButton]] = []

        creator_btn = _url_or_none("start.menu.buy_from_creator", "creator")
        if creator_btn is not None:
            rows.append([creator_btn])

        secondary: list[InlineKeyboardButton] = []
        for label_key, name in (
            ("start.menu.bot_channel", "bot_channel"),
            ("start.menu.support_group", "support_group"),
            ("start.menu.guide_channel", "guide_channel"),
            ("start.menu.custom_link", "custom_link"),
            ("start.menu.buy_from_sudo_1", "sudo_1"),
            ("start.menu.buy_from_sudo_2", "sudo_2"),
        ):
            btn = _url_or_none(label_key, name)
            if btn is not None:
                secondary.append(btn)

        rows.extend(secondary[i : i + 2] for i in range(0, len(secondary), 2))
        if not rows:
            return None
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def start_custom_menu(rendered_menu) -> InlineKeyboardMarkup | None:
        rows: list[list[InlineKeyboardButton]] = []
        for rendered_row in getattr(rendered_menu, "rows", ()) or ():
            row: list[InlineKeyboardButton] = []
            for descriptor in rendered_row:
                label = str(getattr(descriptor, "label", "") or "").strip()
                if not label:
                    continue
                url = getattr(descriptor, "url", None)
                callback_data = getattr(descriptor, "callback_data", None)
                style = _START_BUTTON_STYLES.get(
                    getattr(descriptor, "color_token", None),
                    ButtonStyle.DEFAULT,
                )
                icon_custom_emoji_id = getattr(
                    descriptor,
                    "icon_custom_emoji_id",
                    None,
                )
                if url:
                    button = compatible_inline_button(
                        label,
                        url=str(url),
                        style=style,
                        icon_custom_emoji_id=icon_custom_emoji_id,
                    )
                    if getattr(descriptor, "color_token", None) is not None:
                        mark_explicit_style_override(button)
                    row.append(button)
                elif callback_data:
                    button = compatible_inline_button(
                        label,
                        callback_data=str(callback_data),
                        style=style,
                        icon_custom_emoji_id=icon_custom_emoji_id,
                    )
                    if getattr(descriptor, "color_token", None) is not None:
                        mark_explicit_style_override(button)
                    row.append(button)
            if row:
                rows.append(row)
        if not rows:
            return None
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def with_management_entry(
        lang: str,
        role: str,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> InlineKeyboardMarkup:
        label_key = (
            "start.menu.developer_panel"
            if role == "developer"
            else "start.menu.management_panel"
        )
        management_row = [
            _btn(
                t(lang, label_key),
                CB["WZ_HOME"],
            )
        ]
        existing_rows = list(getattr(reply_markup, "inline_keyboard", ()) or ())
        return InlineKeyboardMarkup([management_row, *existing_rows])

    # ── Playback type selection ───────────────────────────────────────────

    @staticmethod
    def playback_type_menu(
        lang: str,
        capabilities: MediaCapabilities | None = None,
    ) -> InlineKeyboardMarkup:
        """Build playback type selection keyboard.

        When *capabilities* is omitted, every type button is shown (legacy).
        When provided, only features enabled on the capabilities object appear.
        """
        type_buttons = [
            ("audio", CB["PB_AUDIO"], "playback.types.audio"),
            ("video", CB["PB_VIDEO"], "playback.types.video"),
            ("tv", CB["PB_TV"], "playback.types.tv"),
            ("satellite", CB["PB_SAT"], "playback.types.satellite"),
            ("radio", CB["PB_RADIO"], "playback.types.radio"),
            ("download", CB["PB_DOWNLOAD"], "playback.types.download"),
        ]
        rows: list[list[InlineKeyboardButton]] = []
        current_row: list[InlineKeyboardButton] = []
        for feature, callback_data, label_key in type_buttons:
            if capabilities is not None and not getattr(capabilities, feature, True):
                continue
            current_row.append(_btn(t(lang, label_key), callback_data))
            if len(current_row) == 2:
                rows.append(current_row)
                current_row = []
        if current_row:
            rows.append(current_row)
        rows.append([_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])])
        return InlineKeyboardMarkup(rows)

    # ── Now-playing controls ──────────────────────────────────────────────

    @staticmethod
    def now_playing_controls(
        lang: str,
        repeat_on: bool,
        *,
        show_extras: bool = True,
    ) -> InlineKeyboardMarkup:
        """Build now-playing inline controls.

        When *show_extras* is False, only pause/resume/stop remain visible.
        """
        essential_row = [
            _btn(t(lang, "common.buttons.pause"), CB["PB_PAUSE"]),
            _btn(t(lang, "common.buttons.resume"), CB["PB_RESUME"]),
            _btn(t(lang, "common.buttons.stop"), CB["PB_STOP"]),
        ]
        if not show_extras:
            return InlineKeyboardMarkup([essential_row])

        repeat_label = (
            t(lang, "playback.controls.repeat_on")
            if repeat_on
            else t(lang, "playback.controls.repeat_off")
        )

        play_row = [
            _btn(t(lang, "playback.controls.prev"), CB["PB_PREV"]),
            _btn(t(lang, "common.buttons.pause"), CB["PB_PAUSE"]),
            _btn(t(lang, "common.buttons.resume"), CB["PB_RESUME"]),
            _btn(t(lang, "playback.controls.next"), CB["PB_NEXT"]),
        ]
        return InlineKeyboardMarkup(
            [
                play_row,
                [
                    _btn(t(lang, "playback.controls.vol_down"), CB["PB_VOL_DOWN"]),
                    _btn(t(lang, "playback.controls.vol_up"), CB["PB_VOL_UP"]),
                ],
                [
                    _btn(t(lang, "playback.controls.speed_down"), CB["PB_SPEED_DOWN"]),
                    _btn(t(lang, "playback.controls.speed_up"), CB["PB_SPEED_UP"]),
                ],
                [
                    _btn(
                        repeat_label,
                        CB["PB_REPEAT_TOGGLE"],
                        toggle_state=repeat_on,
                    ),
                    _btn(t(lang, "playback.controls.fav_add"), CB["PB_FAV_ADD"]),
                    _btn(t(lang, "playback.controls.fav_play"), CB["PB_FAV_PLAY"]),
                ],
                essential_row,
            ]
        )

    # ── Developer panel ───────────────────────────────────────────────────

    @staticmethod
    def developer_panel(lang: str) -> InlineKeyboardMarkup:
        status = [_btn(t(lang, "panels.developer.status"), CB["DEV_STATUS"])]
        categories = [
            _btn(t(lang, "panels.developer.cat_credit"), CB["DEV_CAT_CREDIT"]),
            _btn(t(lang, "panels.developer.cat_broadcast"), CB["DEV_CAT_BROADCAST"]),
            _btn(t(lang, "panels.developer.cat_lists"), CB["DEV_CAT_LISTS"]),
            _btn(t(lang, "panels.developer.cat_settings"), CB["DEV_CAT_SETTINGS"]),
            _btn(t(lang, "panels.developer.cat_users"), CB["DEV_CAT_USERS"]),
            _btn(t(lang, "panels.developer.cat_force_join"), CB["DEV_CAT_FORCE_JOIN"]),
            _btn(t(lang, "panels.developer.cat_moderation"), CB["DEV_CAT_MODERATION"]),
            _btn(t(lang, "panels.developer.cat_texts"), CB["DEV_CAT_TEXTS"]),
        ]
        tools = [
            _btn(t(lang, "admin.install_policy.title"), CB["DEV_INSTALL_POLICY"]),
            _btn(t(lang, "admin.helpers.btn_label"), CB["HLP_HOME"]),
            _btn(t(lang, "admin.analytics.btn_label"), CB["AN_HOME"]),
            _btn(t(lang, "help.btn.about"), CB["DEV_ABOUT"]),
        ]
        rows = [status] + _chunk_by_width(categories) + _chunk_by_width(tools)
        rows.append([_btn(t(lang, "common.buttons.back"), CB["NAV_START"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def dev_sub_credit(lang: str) -> InlineKeyboardMarkup:
        credit = [
            _btn(t(lang, "panels.developer.increase_credit"), CB["DEV_INCREASE_CREDIT"]),
            _btn(t(lang, "panels.developer.decrease_credit"), CB["DEV_DECREASE_CREDIT"]),
        ]
        rows = _chunk_by_width(credit)
        rows.append([_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def dev_sub_monthly_invoice(lang: str, *, auto_send_enabled: bool = False) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])]]
        )

    @staticmethod
    def dev_sub_rates(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])]]
        )

    @staticmethod
    def dev_sub_broadcast(lang: str) -> InlineKeyboardMarkup:
        wizard = [
            _btn(t(lang, "broadcast.wizard.title"), CB["BCW_START"]),
            _btn(t(lang, "admin.bc.history_btn"), CB["BC_HISTORY"]),
        ]
        bcast = [
            _btn(t(lang, "panels.developer.broadcast_group"), CB["DEV_BROADCAST_GROUP"]),
            _btn(t(lang, "panels.developer.broadcast_private"), CB["DEV_BROADCAST_PRIVATE"]),
            _btn(t(lang, "panels.developer.broadcast_channel"), CB["DEV_BROADCAST_CHANNEL"]),
            _btn(t(lang, "panels.developer.send_to_sudo"), CB["DEV_SEND_TO_SUDO"]),
        ]
        fwd = [
            _btn(t(lang, "panels.developer.forward_group"), CB["DEV_FORWARD_GROUP"]),
            _btn(t(lang, "panels.developer.forward_private"), CB["DEV_FORWARD_PRIVATE"]),
            _btn(t(lang, "panels.developer.forward_channel"), CB["DEV_FORWARD_CHANNEL"]),
        ]
        rows = _chunk_by_width(wizard) + _chunk_by_width(bcast) + _chunk_by_width(fwd)
        rows.append([_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def dev_sub_lists(lang: str) -> InlineKeyboardMarkup:
        rows = [
            [
                _btn(t(lang, "panels.developer.list_groups"), CB["DEV_LIST_GROUPS"]),
                _btn(t(lang, "panels.developer.list_unlimited_groups"), CB["DEV_LIST_UNLIMITED_GROUPS"]),
            ],
            [
                _btn(t(lang, "panels.developer.list_call_security_groups"), CB["DEV_LIST_CALL_SECURITY_GROUPS"]),
            ],
            [
                _btn(t(lang, "panels.developer.list_renewal"), CB["DEV_LIST_RENEWAL_GROUPS"]),
                _btn(t(lang, "panels.developer.list_no_credit"), CB["DEV_LIST_NO_CREDIT"]),
            ],
            [
                _btn(t(lang, "panels.developer.list_playback_groups"), CB["DEV_LIST_PLAYBACK_GROUPS"]),
                _btn(t(lang, "panels.developer.list_test_groups"), CB["DEV_LIST_TEST_GROUPS"]),
            ],
            [
                _btn(t(lang, "panels.developer.list_music_groups"), CB["DEV_LIST_MUSIC_GROUPS"]),
                _btn(t(lang, "panels.developer.list_video_groups"), CB["DEV_LIST_VIDEO_GROUPS"]),
            ],
            [
                _btn(t(lang, "panels.developer.list_inactive_channels"), CB["DEV_LIST_INACTIVE_CHANNELS"]),
                _btn(t(lang, "panels.developer.list_inactive_groups"), CB["DEV_LIST_INACTIVE_GROUPS"]),
            ],
            [
                _btn(t(lang, "panels.developer.list_inactive_users"), CB["DEV_LIST_INACTIVE_USERS"]),
                _btn(t(lang, "panels.developer.list_active_users"), CB["DEV_LIST_ACTIVE_USERS"]),
            ],
            [
                _btn(t(lang, "panels.developer.list_channels"), CB["DEV_LIST_CHANNELS"]),
                _btn(t(lang, "panels.developer.leave_group"), CB["DEV_LEAVE_GROUP"]),
            ],
        ]
        rows.append([_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def dev_sub_settings(
        lang: str,
        *,
        bot_enabled: bool = True,
        sudo_panel_enabled: bool = True,
        force_join_enabled: bool = False,
        auto_leave_enabled: bool = False,
        trial_enabled: bool = False,
    ) -> InlineKeyboardMarkup:
        toggles = [
            _btn(
                toggle_label(lang, t(lang, "panels.developer.toggle_bot"), bot_enabled),
                CB["DEV_BOT_ENABLED_TOGGLE"],
                toggle_state=bot_enabled,
            ),
            _btn(
                toggle_label(
                    lang,
                    t(lang, "panels.developer.toggle_sudo_panel"),
                    sudo_panel_enabled,
                ),
                CB["DEV_SUDO_PANEL_ENABLED_TOGGLE"],
                toggle_state=sudo_panel_enabled,
            ),
            _btn(
                toggle_label(
                    lang,
                    t(lang, "panels.developer.toggle_auto_leave"),
                    auto_leave_enabled,
                ),
                CB["DEV_AUTO_LEAVE_TOGGLE"],
                toggle_state=auto_leave_enabled,
            ),
            _btn(
                toggle_label(lang, t(lang, "panels.developer.toggle_trial"), trial_enabled),
                CB["DEV_TRIAL_TOGGLE"],
                toggle_state=trial_enabled,
            ),
        ]
        tools = [
            _btn(t(lang, "panels.developer.webservice_check"), CB["DEV_MEDIA_HEALTH"]),
            _btn(t(lang, "youtube_sessions.panel_button"), CB["DEV_YOUTUBE_SESSIONS"]),
            _btn(t(lang, "fast_creat_tokens.panel_button"), CB["DEV_FAST_CREAT_TOKENS"]),
            _btn(t(lang, "bot_update.home_button"), CB["DEV_BOT_UPDATE"]),
            _btn(t(lang, "panels.developer.set_media_policy"), CB["DEV_SET_MEDIA_POLICY"]),
            _btn(t(lang, "panels.developer.install_limits"), CB["DEV_INSTALL_LIMITS"]),
            _btn(t(lang, "panels.developer.set_log_channel"), CB["DEV_SET_LOG_CHANNEL"]),
        ]
        rows = _chunk_by_width(toggles) + _chunk_by_width(tools)
        rows.append(_nav_row(lang, back_cb=CB["NAV_BACK"]))
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def dev_sub_force_join(
        lang: str,
        *,
        force_join_enabled: bool = False,
    ) -> InlineKeyboardMarkup:
        rows = [
            [
                _btn(
                    toggle_label(
                        lang,
                        t(lang, "panels.developer.toggle_force_join"),
                        force_join_enabled,
                    ),
                    CB["DEV_FORCE_JOIN_TOGGLE"],
                    toggle_state=force_join_enabled,
                )
            ],
            [_btn(t(lang, "panels.developer.force_join_manage"), CB["DEV_FORCE_JOIN_MANAGE"])],
            [_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])],
        ]
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def dev_sub_moderation(lang: str) -> InlineKeyboardMarkup:
        moderation = [
            _btn(t(lang, "panels.developer.filters"), CB["DEV_FILTERS"]),
            _btn(t(lang, "panels.developer.blacklist"), CB["DEV_BLACKLIST"]),
            _btn(t(lang, "panels.developer.global_ban"), CB["DEV_BANALL_HOME"]),
        ]
        rows = _chunk_by_width(moderation)
        rows.append([_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def dev_media_health(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [_btn(t(lang, "youtube_sessions.panel_button"), CB["DEV_YOUTUBE_SESSIONS"])],
            [_btn(t(lang, "media_health.export_json"), CB["DEV_MEDIA_EXPORT"])],
            [_btn(t(lang, "media_health.cleanup_preview"), CB["DEV_MEDIA_CLEANUP_PREVIEW"])],
            [_btn(t(lang, "media_health.ranking_button"), CB["DEV_MEDIA_RANKING"])],
            [_btn(t(lang, "common.buttons.back"), f"{CB['WZ_BACK_PREFIX']}dev_settings")],
        ])

    @staticmethod
    def youtube_sessions_home(
        lang: str,
        *,
        back_callback: str,
        rekey_available: bool = False,
    ) -> InlineKeyboardMarkup:
        rows = [
            [
                _btn(t(lang, "youtube_sessions.add"), CB["YT_SESSION_ADD"]),
                _btn(t(lang, "youtube_sessions.list"), f"{CB['YT_SESSION_LIST_PREFIX']}0"),
            ],
            [_btn(t(lang, "youtube_sessions.refresh"), CB["YT_SESSION_HOME"])],
        ]
        if rekey_available:
            rows.append([_btn(t(lang, "youtube_sessions.rekey"), CB["YT_SESSION_REKEY_PROMPT"])])
        rows.append([_btn(t(lang, "common.buttons.back"), back_callback)])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def youtube_sessions_list(
        lang: str,
        rows: list[tuple[int, str]],
        *,
        page: int,
        total_pages: int,
    ) -> InlineKeyboardMarkup:
        keyboard = [
            [_btn(t(lang, "youtube_sessions.list_item", session_id=session_id, status=status), f"{CB['YT_SESSION_DETAIL_PREFIX']}{session_id}:{page}")]
            for session_id, status in rows
        ]
        if total_pages > 1:
            pager = []
            if page > 0:
                pager.append(_btn("‹", f"{CB['YT_SESSION_LIST_PREFIX']}{page - 1}"))
            if page + 1 < total_pages:
                pager.append(_btn("›", f"{CB['YT_SESSION_LIST_PREFIX']}{page + 1}"))
            if pager:
                keyboard.append(pager)
        keyboard.append([_btn(t(lang, "common.buttons.back"), CB["YT_SESSION_HOME"])])
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def youtube_session_detail(
        lang: str,
        *,
        session_id: int,
        status: str,
        page: int,
    ) -> InlineKeyboardMarkup:
        rows = [[_btn(t(lang, "youtube_sessions.test"), f"{CB['YT_SESSION_TEST_PREFIX']}{session_id}:{page}")]]
        if status == "active":
            rows.append([_btn(t(lang, "youtube_sessions.disable"), f"{CB['YT_SESSION_DISABLE_PREFIX']}{session_id}:{page}")])
        elif status == "disabled":
            rows.append([_btn(t(lang, "youtube_sessions.enable"), f"{CB['YT_SESSION_ENABLE_PREFIX']}{session_id}:{page}")])
        rows.append([_btn(t(lang, "youtube_sessions.delete"), f"{CB['YT_SESSION_DELETE_PROMPT_PREFIX']}{session_id}:{page}")])
        rows.append([_btn(t(lang, "common.buttons.back"), f"{CB['YT_SESSION_LIST_PREFIX']}{page}")])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def youtube_session_delete_confirm(
        lang: str,
        *,
        session_id: int,
        page: int,
        user_id: int,
        issued_at: int,
    ) -> InlineKeyboardMarkup:
        token = f"{session_id}:{page}:{user_id}:{issued_at}"
        return InlineKeyboardMarkup([
            [
                _btn(t(lang, "youtube_sessions.delete_confirm"), f"{CB['YT_SESSION_DELETE_CONFIRM_PREFIX']}{token}"),
                _btn(t(lang, "youtube_sessions.delete_cancel"), f"{CB['YT_SESSION_DELETE_CANCEL_PREFIX']}{token}"),
            ],
            [_btn(t(lang, "common.buttons.back"), f"{CB['YT_SESSION_DETAIL_PREFIX']}{session_id}:{page}")],
        ])

    @staticmethod
    def youtube_sessions_rekey_confirm(
        lang: str,
        *,
        user_id: int,
        issued_at: int,
    ) -> InlineKeyboardMarkup:
        token = f"{user_id}:{issued_at}"
        return InlineKeyboardMarkup([
            [
                _btn(t(lang, "youtube_sessions.rekey_confirm"), f"{CB['YT_SESSION_REKEY_CONFIRM_PREFIX']}{token}"),
                _btn(t(lang, "youtube_sessions.rekey_cancel"), f"{CB['YT_SESSION_REKEY_CANCEL_PREFIX']}{token}"),
            ],
            [_btn(t(lang, "common.buttons.back"), CB["YT_SESSION_HOME"])],
        ])

    @staticmethod
    def youtube_download_format(lang: str, token: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _btn(t(lang, "youtube_sessions.download_audio"), f"{CB['DOWNLOAD_FORMAT_PREFIX']}a:{token}"),
                _btn(t(lang, "youtube_sessions.download_video"), f"{CB['DOWNLOAD_FORMAT_PREFIX']}v:{token}"),
            ],
            [_btn(t(lang, "youtube_sessions.download_cancel"), f"{CB['DOWNLOAD_FORMAT_PREFIX']}x:{token}")],
        ])

    @staticmethod
    def fast_creat_tokens_home(lang: str, *, back_callback: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _btn(t(lang, "fast_creat_tokens.provider_instagram"), f"{CB['FAST_CREAT_PROVIDER_PREFIX']}instagram"),
                _btn(t(lang, "fast_creat_tokens.provider_tiktok"), f"{CB['FAST_CREAT_PROVIDER_PREFIX']}tiktok"),
            ],
            [_btn(t(lang, "fast_creat_tokens.provider_spotify"), f"{CB['FAST_CREAT_PROVIDER_PREFIX']}spotify")],
            [_btn(t(lang, "common.buttons.back"), back_callback)],
        ])

    @staticmethod
    def fast_creat_tokens_provider_home(lang: str, *, provider: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _btn(t(lang, "fast_creat_tokens.add"), f"{CB['FAST_CREAT_ADD_PREFIX']}{provider}"),
                _btn(t(lang, "fast_creat_tokens.list"), f"{CB['FAST_CREAT_LIST_PREFIX']}{provider}:0"),
            ],
            [_btn(t(lang, "common.buttons.back"), CB["FAST_CREAT_HOME"])],
        ])

    @staticmethod
    def fast_creat_tokens_list(
        lang: str,
        rows: list[tuple[int, str]],
        *,
        provider: str,
        page: int,
        total_pages: int,
    ) -> InlineKeyboardMarkup:
        keyboard = [
            [_btn(t(lang, "fast_creat_tokens.list_item", token_id=token_id, status=status), f"{CB['FAST_CREAT_DETAIL_PREFIX']}{provider}:{token_id}:{page}")]
            for token_id, status in rows
        ]
        if total_pages > 1:
            pager = []
            if page > 0:
                pager.append(_btn("‹", f"{CB['FAST_CREAT_LIST_PREFIX']}{provider}:{page - 1}"))
            if page + 1 < total_pages:
                pager.append(_btn("›", f"{CB['FAST_CREAT_LIST_PREFIX']}{provider}:{page + 1}"))
            if pager:
                keyboard.append(pager)
        keyboard.append([_btn(t(lang, "common.buttons.back"), f"{CB['FAST_CREAT_PROVIDER_PREFIX']}{provider}")])
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def fast_creat_token_detail(
        lang: str,
        *,
        provider: str,
        token_id: int,
        status: str,
        page: int,
    ) -> InlineKeyboardMarkup:
        rows = []
        if status == "active":
            rows.append([_btn(t(lang, "fast_creat_tokens.disable"), f"{CB['FAST_CREAT_DISABLE_PREFIX']}{provider}:{token_id}:{page}")])
        else:
            rows.append([_btn(t(lang, "fast_creat_tokens.enable"), f"{CB['FAST_CREAT_ENABLE_PREFIX']}{provider}:{token_id}:{page}")])
        rows.append([_btn(t(lang, "fast_creat_tokens.delete"), f"{CB['FAST_CREAT_DELETE_PROMPT_PREFIX']}{provider}:{token_id}:{page}")])
        rows.append([_btn(t(lang, "common.buttons.back"), f"{CB['FAST_CREAT_LIST_PREFIX']}{provider}:{page}")])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def fast_creat_token_delete_confirm(
        lang: str,
        *,
        provider: str,
        token_id: int,
        page: int,
        user_id: int,
        issued_at: int,
    ) -> InlineKeyboardMarkup:
        token = f"{provider}:{token_id}:{page}:{user_id}:{issued_at}"
        return InlineKeyboardMarkup([
            [
                _btn(t(lang, "fast_creat_tokens.delete_confirm"), f"{CB['FAST_CREAT_DELETE_CONFIRM_PREFIX']}{token}"),
                _btn(t(lang, "fast_creat_tokens.delete_cancel"), f"{CB['FAST_CREAT_DELETE_CANCEL_PREFIX']}{token}"),
            ],
            [_btn(t(lang, "common.buttons.back"), f"{CB['FAST_CREAT_DETAIL_PREFIX']}{provider}:{token_id}:{page}")],
        ])

    @staticmethod
    def dev_media_cleanup_confirm(
        lang: str,
        user_id: int,
        issued_at: int,
    ) -> InlineKeyboardMarkup:
        token = f"{user_id}:{issued_at}"
        return InlineKeyboardMarkup([
            [
                _btn(
                    t(lang, "media_health.cleanup_confirm_yes"),
                    f"{CB['DEV_MEDIA_CLEANUP_EXEC_PREFIX']}{token}",
                ),
                _btn(
                    t(lang, "media_health.cleanup_confirm_no"),
                    f"{CB['DEV_MEDIA_CLEANUP_ABORT_PREFIX']}{token}",
                ),
            ],
            [_btn(t(lang, "common.buttons.back"), CB["DEV_MEDIA_HEALTH"])],
        ])

    @staticmethod
    def dev_bot_update(lang: str, *, reload_available: bool) -> InlineKeyboardMarkup:
        rows = [
            [_btn(t(lang, "bot_update.refresh_button"), CB["DEV_BOT_UPDATE_REFRESH"])],
        ]
        if reload_available:
            rows.append([_btn(t(lang, "bot_update.reload_button"), CB["DEV_BOT_UPDATE_RELOAD"])])
        rows.append([_btn(t(lang, "common.buttons.back"), f"{CB['WZ_BACK_PREFIX']}dev_settings")])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def dev_bot_update_reload_confirm(
        lang: str,
        user_id: int,
        issued_at: int,
    ) -> InlineKeyboardMarkup:
        token = f"{user_id}:{issued_at}"
        return InlineKeyboardMarkup([
            [
                _btn(
                    t(lang, "bot_update.reload_confirm_yes"),
                    f"{CB['DEV_BOT_UPDATE_RELOAD_EXEC_PREFIX']}{token}",
                ),
                _btn(
                    t(lang, "bot_update.reload_confirm_no"),
                    f"{CB['DEV_BOT_UPDATE_RELOAD_ABORT_PREFIX']}{token}",
                ),
            ],
            [_btn(t(lang, "common.buttons.back"), CB["DEV_BOT_UPDATE"])],
        ])

    @staticmethod
    def dev_sub_users(lang: str) -> InlineKeyboardMarkup:
        roles = [
            _btn(t(lang, "admin_titles.settings_title"), CB["DEV_ADMIN_TITLES"]),
            _btn(t(lang, "panels.developer.set_owner"), CB["DEV_SET_OWNER"]),
            _btn(t(lang, "panels.developer.sudo_manage"), CB["DEV_SUDO_MANAGE"]),
        ]
        lists = [
            _btn(t(lang, "panels.developer.list_owners"), CB["DEV_LIST_OWNERS"]),
            _btn(t(lang, "panels.developer.list_sudos"), CB["DEV_LIST_SUDOS"]),
        ]
        removes = [
            _btn(t(lang, "panels.developer.remove_owner"), CB["DEV_REMOVE_OWNER"]),
            _btn(t(lang, "panels.developer.remove_sudo"), CB["DEV_REMOVE_SUDO"]),
        ]
        rows = _chunk_by_width(roles) + _chunk_by_width(lists) + _chunk_by_width(removes)
        rows.append([_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def dev_banall_home(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _btn(t(lang, "global_ban.add"), CB["DEV_BANALL_ADD"]),
                _btn(t(lang, "global_ban.remove"), CB["DEV_BANALL_REMOVE"]),
            ],
            [
                _btn(t(lang, "global_ban.list"), f"{CB['DEV_BANALL_LIST_PREFIX']}0"),
                _btn(t(lang, "global_ban.clear"), CB["DEV_BANALL_CLEAR"]),
            ],
            _nav_row(lang, back_cb=f"{CB['WZ_BACK_PREFIX']}dev_moderation"),
        ])

    @staticmethod
    def dev_banall_clear_confirm(
        lang: str, actor_id: int, issued_at: int,
    ) -> InlineKeyboardMarkup:
        token = f"{actor_id}:{issued_at}"
        return InlineKeyboardMarkup([
            [_btn(t(lang, "common.buttons.confirm"), f"{CB['DEV_BANALL_CLEAR_DO_PREFIX']}{token}")],
            [_btn(t(lang, "common.buttons.cancel_inline"), f"{CB['DEV_BANALL_CLEAR_NO_PREFIX']}{token}")],
            [_btn(t(lang, "common.buttons.back"), CB["DEV_BANALL_HOME"])],
        ])

    @staticmethod
    def dev_banall_list(
        lang: str,
        bans: list,
        *,
        page: int,
        total_pages: int,
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for ban in bans:
            rows.append([
                _btn(
                    t(lang, "global_ban.row_remove", user_id=ban.user_id),
                    f"{CB['DEV_BANALL_RM_PREFIX']}{ban.user_id}:{page}",
                ),
            ])
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(_btn(t(lang, "common.buttons.prev"), f"{CB['DEV_BANALL_LIST_PREFIX']}{page - 1}"))
        if page + 1 < total_pages:
            nav.append(_btn(t(lang, "common.buttons.next"), f"{CB['DEV_BANALL_LIST_PREFIX']}{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([_btn(t(lang, "common.buttons.back"), CB["DEV_BANALL_HOME"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def dev_admin_titles(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _btn(t(lang, "admin_titles.set_developer"), CB["DEV_TITLE_DEV_SET"]),
                _btn(t(lang, "admin_titles.clear_developer"), CB["DEV_TITLE_DEV_CLEAR"]),
            ],
            [
                _btn(t(lang, "admin_titles.apply_developer"), CB["DEV_TITLE_APPLY_DEV"]),
                _btn(t(lang, "admin_titles.promote_developer"), CB["DEV_TGPROM_DEV"]),
            ],
            [_btn(t(lang, "admin_titles.owner_titles"), f"{CB['DEV_TITLE_OWNER_LIST_PREFIX']}0")],
            [_btn(t(lang, "common.buttons.back"), f"{CB['WZ_BACK_PREFIX']}dev_users")],
        ])

    @staticmethod
    def dev_sub_texts(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [_btn(t(lang, "panels.developer.texts_links"), CB["DEV_TEXTS_LINKS"])],
            [_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])],
        ])

    # ── Owner panel ───────────────────────────────────────────────────────

    @staticmethod
    def owner_panel(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [_btn(t(lang, "panels.owner.root_stats"), CB["OWN_STATS"])],
                [
                    _btn(t(lang, "panels.owner.root_groups"), CB["OWN_GROUPS"]),
                    _btn(t(lang, "panels.owner.root_credit"), CB["OWN_CREDIT"]),
                ],
                [
                    _btn(t(lang, "panels.owner.root_sudos"), CB["OWN_SUDOS"]),
                    _btn(t(lang, "panels.owner.root_sudo_titles"), CB["OWN_SUDO_TITLES"]),
                ],
                [
                    _btn(t(lang, "panels.owner.root_start_text"), CB["OWN_START_TEXT"]),
                    _btn(t(lang, "panels.owner.root_force_join"), CB["OWN_FORCE_JOIN_TOGGLE"]),
                ],
                [
                    _btn(t(lang, "panels.owner.root_broadcast"), CB["OWN_BROADCAST"]),
                    _btn(t(lang, "panels.owner.root_lists"), CB["OWN_LISTS"]),
                ],
                [_btn(t(lang, "panels.owner.root_moderation"), CB["OWN_MODERATION"])],
                [
                    _btn(t(lang, "panels.owner.root_media"), CB["OWN_MEDIA"]),
                    _btn(t(lang, "panels.owner.root_reports"), CB["OWN_REPORTS"]),
                ],
                [
                    _btn(t(lang, "youtube_sessions.panel_button"), CB["OWN_YOUTUBE_SESSIONS"]),
                    _btn(t(lang, "fast_creat_tokens.panel_button"), CB["OWN_FAST_CREAT_TOKENS"]),
                ],
                [_btn(t(lang, "common.buttons.back"), CB["NAV_START"])],
            ]
        )

    @staticmethod
    def owner_sub_groups(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    _btn(t(lang, "panels.owner.list_installs_groups"), CB["OWN_LIST_GROUPS"]),
                    _btn(t(lang, "panels.owner.list_installs_channels"), CB["OWN_LIST_CHANNELS"]),
                ],
                [_btn(t(lang, "panels.owner.list_no_credit"), CB["OWN_LIST_NO_CREDIT"])],
                [
                    _btn(t(lang, "panels.owner.list_inactive_groups"), CB["OWN_LIST_INACTIVE_GROUPS"]),
                    _btn(t(lang, "panels.owner.list_inactive_channels"), CB["OWN_LIST_INACTIVE_CHANNELS"]),
                ],
                [_btn(t(lang, "panels.owner.list_renewal"), CB["OWN_LIST_RENEWAL"])],
                _nav_row(lang, back_cb=CB["NAV_BACK"]),
            ]
        )

    @staticmethod
    def owner_sub_credit(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    _btn(t(lang, "panels.owner.list_groups"), CB["OWN_LIST_GROUPS_CREDIT"]),
                    _btn(t(lang, "panels.owner.list_channels"), CB["OWN_LIST_CHANNELS_CREDIT"]),
                ],
                [_btn(t(lang, "panels.owner.list_no_credit"), CB["OWN_LIST_NO_CREDIT"])],
                _nav_row(lang, back_cb=CB["NAV_BACK"]),
            ]
        )

    @staticmethod
    def owner_sub_lists(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    _btn(t(lang, "panels.owner.list_installs_groups"), CB["OWN_LIST_GROUPS"]),
                    _btn(t(lang, "panels.owner.list_installs_channels"), CB["OWN_LIST_CHANNELS"]),
                ],
                [
                    _btn(t(lang, "panels.owner.no_credit_reports"), CB["OWN_NO_CREDIT_REPORTS"]),
                ],
                _nav_row(lang, back_cb=CB["NAV_BACK"]),
            ]
        )

    @staticmethod
    def owner_sub_moderation(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [_btn(t(lang, "panels.owner.filters"), CB["OWN_FILTERS"])],
                [_btn(t(lang, "panels.owner.global_ban"), CB["OWN_BANALL_HOME"])],
                _nav_row(lang, back_cb=CB["NAV_BACK"]),
            ]
        )

    @staticmethod
    def owner_sub_media(lang: str, states: dict[str, bool] | None = None) -> InlineKeyboardMarkup:
        states = states or {}
        return InlineKeyboardMarkup(
            [
                [
                    _btn(
                        toggle_label(lang, t(lang, "panels.owner.media_audio"), bool(states.get("audio", True))),
                        CB["OWN_MEDIA_AUDIO_TOGGLE"],
                        toggle_state=bool(states.get("audio", True)),
                    ),
                    _btn(
                        toggle_label(lang, t(lang, "panels.owner.media_video"), bool(states.get("video", True))),
                        CB["OWN_MEDIA_VIDEO_TOGGLE"],
                        toggle_state=bool(states.get("video", True)),
                    ),
                ],
                [
                    _btn(
                        toggle_label(lang, t(lang, "panels.owner.media_file"), bool(states.get("file", True))),
                        CB["OWN_MEDIA_FILE_TOGGLE"],
                        toggle_state=bool(states.get("file", True)),
                    ),
                    _btn(
                        toggle_label(lang, t(lang, "panels.owner.media_download"), bool(states.get("download", True))),
                        CB["OWN_MEDIA_DOWNLOAD_TOGGLE"],
                        toggle_state=bool(states.get("download", True)),
                    ),
                ],
                [
                    _btn(
                        toggle_label(lang, t(lang, "panels.owner.media_buttons"), bool(states.get("buttons", True))),
                        CB["OWN_MEDIA_BUTTONS_TOGGLE"],
                        toggle_state=bool(states.get("buttons", True)),
                    )
                ],
                _nav_row(lang, back_cb=CB["NAV_BACK"]),
            ]
        )

    @staticmethod
    def owner_sub_sudo_titles(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [_btn(t(lang, "panels.owner.list_sudo_titles"), f"{CB['OWN_TITLE_SUDO_LIST_PREFIX']}0")],
                _nav_row(lang, back_cb=CB["NAV_BACK"]),
            ]
        )

    @staticmethod
    def owner_sub_installs(lang: str) -> InlineKeyboardMarkup:
        lists = [
            _btn(t(lang, "panels.owner.list_installs_groups"), CB["OWN_LIST_GROUPS"]),
            _btn(t(lang, "panels.owner.list_installs_channels"), CB["OWN_LIST_CHANNELS"]),
            _btn(t(lang, "panels.owner.list_groups"), CB["OWN_LIST_GROUPS_CREDIT"]),
            _btn(t(lang, "panels.owner.list_channels"), CB["OWN_LIST_CHANNELS_CREDIT"]),
            _btn(t(lang, "panels.owner.list_no_credit"), CB["OWN_LIST_NO_CREDIT"]),
        ]
        rows = _chunk_by_width(lists)
        rows.append([_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def owner_sub_broadcast(lang: str) -> InlineKeyboardMarkup:
        bcast = [
            _btn(t(lang, "panels.owner.broadcast_group"), CB["OWN_BROADCAST_GROUP"]),
            _btn(t(lang, "panels.owner.broadcast_private"), CB["OWN_BROADCAST_PRIVATE"]),
            _btn(t(lang, "panels.owner.broadcast_channel"), CB["OWN_BROADCAST_CHANNEL"]),
        ]
        fwd = [
            _btn(t(lang, "panels.owner.forward_group"), CB["OWN_FORWARD_GROUP"]),
            _btn(t(lang, "panels.owner.forward_private"), CB["OWN_FORWARD_PRIVATE"]),
            _btn(t(lang, "panels.owner.forward_channel"), CB["OWN_FORWARD_CHANNEL"]),
        ]
        rows = _chunk_by_width(bcast) + _chunk_by_width(fwd)
        rows.append([_btn(t(lang, "panels.owner.broadcast_history"), CB["OWN_BC_HISTORY"])])
        rows.append([_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def owner_sub_settings(
        lang: str,
        *,
        force_join_enabled: bool = False,
        auto_leave_enabled: bool = False,
        trial_enabled: bool = False,
    ) -> InlineKeyboardMarkup:
        toggles = [
            _btn(
                toggle_label(
                    lang,
                    t(lang, "panels.owner.toggle_force_join"),
                    force_join_enabled,
                ),
                CB["OWN_FORCE_JOIN_ENABLE_TOGGLE"],
                toggle_state=force_join_enabled,
            ),
            _btn(
                toggle_label(
                    lang,
                    t(lang, "panels.owner.toggle_auto_leave"),
                    auto_leave_enabled,
                ),
                CB["OWN_AUTO_LEAVE_TOGGLE"],
                toggle_state=auto_leave_enabled,
            ),
            _btn(
                toggle_label(lang, t(lang, "panels.owner.toggle_trial"), trial_enabled),
                CB["OWN_TRIAL_TOGGLE"],
                toggle_state=trial_enabled,
            ),
        ]
        tools = [
            _btn(t(lang, "panels.owner.set_media_policy"), CB["OWN_SET_MEDIA_POLICY"]),
            _btn(t(lang, "panels.owner.force_join_manage"), CB["OWN_FORCE_JOIN_MANAGE"]),
            _btn(t(lang, "panels.owner.filters"), CB["OWN_FILTERS"]),
            _btn(t(lang, "panels.owner.install_limits"), CB["OWN_INSTALL_LIMITS"]),
            _btn(t(lang, "panels.owner.blacklist"), CB["OWN_BLACKLIST"]),
        ]
        rows = _chunk_by_width(toggles) + _chunk_by_width(tools)
        rows.append(_nav_row(lang, back_cb=CB["NAV_BACK"]))
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def owner_sub_users(lang: str) -> InlineKeyboardMarkup:
        tools = [
            _btn(t(lang, "panels.owner.list_sudos"), CB["OWN_LIST_SUDOS"]),
            _btn(t(lang, "panels.owner.sudo_manage"), CB["OWN_SUDO_MANAGE"]),
            _btn(t(lang, "panels.owner.remove_sudo"), CB["OWN_REMOVE_SUDO"]),
        ]
        rows = _chunk_by_width(tools)
        rows.append(_nav_row(lang, back_cb=CB["NAV_BACK"]))
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def owner_sub_reports(lang: str) -> InlineKeyboardMarkup:
        reports = [
            _btn(t(lang, "panels.owner.no_credit_reports"), CB["OWN_NO_CREDIT_REPORTS"]),
        ]
        rows = _chunk_by_width(reports)
        rows.append(_nav_row(lang, back_cb=CB["NAV_BACK"]))
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def owner_sub_billing(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])],
            ]
        )

    @staticmethod
    def owner_sub_texts(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [_btn(t(lang, "panels.owner.texts_links"), CB["OWN_TEXTS_LINKS"])],
                [_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])],
            ]
        )

    # ── Sudo panel ────────────────────────────────────────────────────────

    @staticmethod
    def sudo_panel(lang: str, *, show_leave_installs: bool = True) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [_btn(t(lang, "panels.sudo.root_status"), CB["SUDO_STATUS"])],
                [
                    _btn(t(lang, "panels.sudo.root_groups"), CB["SUDO_GROUPS"]),
                    _btn(t(lang, "panels.sudo.root_credit"), CB["SUDO_CREDIT"]),
                ],
                [_btn(t(lang, "panels.sudo.root_permissions"), CB["SUDO_PERMISSIONS"])],
                [_btn(t(lang, "panels.sudo.root_lists"), CB["SUDO_LISTS"])],
                [_btn(t(lang, "common.buttons.back"), CB["NAV_START"])],
            ]
        )

    @staticmethod
    def sudo_leave_confirm(lang: str, user_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    _btn(
                        t(lang, "common.buttons.confirm"),
                        f"{CB['SUDO_LEAVE_CONFIRM_PREFIX']}{user_id}",
                    )
                ],
                [
                    _btn(
                        t(lang, "common.buttons.cancel"),
                        f"{CB['SUDO_LEAVE_CANCEL_PREFIX']}{user_id}",
                    )
                ],
            ]
        )

    # ── Group panel ───────────────────────────────────────────────────────

    @staticmethod
    def group_panel(lang: str) -> InlineKeyboardMarkup:
        nav = [
            _btn(t(lang, "panels.group.nav.settings"), CB["GRP_SETTINGS"]),
            _btn(t(lang, "panels.group.nav.management"), CB["GRP_MANAGEMENT"]),
            _btn(t(lang, "panels.group.nav.help"), CB["GRP_HELP"]),
            _btn(t(lang, "panels.group.nav.support"), CB["GRP_SUPPORT"]),
        ]
        rows = _chunk_by_width(nav)
        rows.append([_btn(t(lang, "common.buttons.close"), CB["NAV_CLOSE"])])
        return InlineKeyboardMarkup(rows)

    # ── Group settings toggles ────────────────────────────────────────────

    @staticmethod
    def group_settings(
        lang: str, settings_dict: dict[str, bool]
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = [
            [_group_setting_button(lang, key, settings_dict) for key in row_keys]
            for row_keys in _GRP_SETTING_ROWS
        ]
        rows.append(_nav_row(lang, back_cb=CB["NAV_BACK"], home_cb=CB["WZ_HOME"]))
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def install_player_setup_panel(
        lang: str,
        chat_id: int,
        user_id: int,
        issued_at: int,
        *,
        show_charge: bool = False,
    ) -> InlineKeyboardMarkup:
        charge_helper_row = []
        if show_charge:
            charge_helper_row.append(
                _btn(
                    t(lang, "panels.group.install_setup.btn_charge"),
                    _install_setup_cb("ShowSetCharge", chat_id, user_id, issued_at),
                )
            )
        charge_helper_row.append(
            _btn(
                t(lang, "panels.group.install_setup.btn_helper"),
                _install_setup_cb("AddCli", chat_id, user_id, issued_at),
            )
        )
        return InlineKeyboardMarkup([
            [
                _btn(
                    t(lang, "panels.group.install_setup.btn_config"),
                    _install_setup_cb("ConfigAdmin", chat_id, user_id, issued_at),
                ),
                _btn(
                    t(lang, "panels.group.install_setup.btn_language"),
                    _install_setup_cb("Lang", chat_id, user_id, issued_at),
                ),
            ],
            charge_helper_row,
            [
                _btn(
                    t(lang, "panels.group.install_setup.btn_access"),
                    _install_setup_cb("Access", chat_id, user_id, issued_at),
                )
            ],
            [
                _btn(
                    t(lang, "panels.group.install_setup.btn_exit"),
                    _install_setup_cb("Exit", chat_id, user_id, issued_at),
                )
            ],
        ])

    @staticmethod
    def install_player_charge_menu(
        lang: str,
        chat_id: int,
        user_id: int,
        issued_at: int,
    ) -> InlineKeyboardMarkup:
        def _charge(days: int) -> str:
            return _install_setup_cb(f"SetCharge{days}", chat_id, user_id, issued_at)

        return InlineKeyboardMarkup([
            [
                _btn(t(lang, "panels.group.install_setup.charge_10"), _charge(10)),
                _btn(t(lang, "panels.group.install_setup.charge_5"), _charge(5)),
                _btn(t(lang, "panels.group.install_setup.charge_3"), _charge(3)),
            ],
            [
                _btn(t(lang, "panels.group.install_setup.charge_30"), _charge(30)),
                _btn(t(lang, "panels.group.install_setup.charge_20"), _charge(20)),
                _btn(t(lang, "panels.group.install_setup.charge_15"), _charge(15)),
            ],
            [
                _btn(t(lang, "panels.group.install_setup.charge_120"), _charge(120)),
                _btn(t(lang, "panels.group.install_setup.charge_90"), _charge(90)),
                _btn(t(lang, "panels.group.install_setup.charge_60"), _charge(60)),
            ],
            [
                _btn(t(lang, "panels.group.install_setup.charge_360"), _charge(360)),
                _btn(t(lang, "panels.group.install_setup.charge_180"), _charge(180)),
                _btn(t(lang, "panels.group.install_setup.charge_150"), _charge(150)),
            ],
            [
                _btn(t(lang, "panels.group.install_setup.charge_0"), _charge(0)),
                _btn(t(lang, "panels.group.install_setup.charge_2"), _charge(2)),
            ],
            [
                _btn(
                    t(lang, "common.buttons.back"),
                    _install_setup_cb("Home", chat_id, user_id, issued_at),
                )
            ],
        ])

    @staticmethod
    def install_player_access_menu(
        lang: str,
        chat_id: int,
        user_id: int,
        issued_at: int,
        *,
        audio_enabled: bool,
        video_enabled: bool,
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _btn(
                    t(
                        lang,
                        "panels.group.install_setup.access_music_btn",
                        state=_install_setup_state_icon(lang, audio_enabled),
                    ),
                    _install_setup_cb("Access:Music", chat_id, user_id, issued_at),
                    toggle_state=audio_enabled,
                ),
                _btn(
                    t(
                        lang,
                        "panels.group.install_setup.access_video_btn",
                        state=_install_setup_state_icon(lang, video_enabled),
                    ),
                    _install_setup_cb("Access:Video", chat_id, user_id, issued_at),
                    toggle_state=video_enabled,
                ),
            ],
            [
                _btn(
                    t(lang, "common.buttons.back"),
                    _install_setup_cb("Home", chat_id, user_id, issued_at),
                )
            ],
        ])

    @staticmethod
    def install_player_language_menu(
        lang: str,
        chat_id: int,
        user_id: int,
        issued_at: int,
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _btn(
                    t(lang, "panels.group.install_setup.lang_fa_btn"),
                    _install_setup_cb("SetLangFa", chat_id, user_id, issued_at),
                ),
                _btn(
                    t(lang, "panels.group.install_setup.lang_en_btn"),
                    _install_setup_cb("SetLangEn", chat_id, user_id, issued_at),
                ),
            ],
            [
                _btn(
                    t(lang, "common.buttons.back"),
                    _install_setup_cb("Home", chat_id, user_id, issued_at),
                )
            ],
        ])

    @staticmethod
    def call_stats_selection_menu(
        lang: str,
        chat_id: int,
        user_id: int,
    ) -> InlineKeyboardMarkup:
        """Build the 9 scope/period buttons plus close for call-stats reports."""

        def _sel(scope: str, period: str) -> str:
            return f"grp:callstats:sel:{scope}:{period}:{chat_id}:{user_id}"

        close_cb = f"grp:callstats:close:{chat_id}:{user_id}"
        rows: list[list[InlineKeyboardButton]] = [
            [_btn(t(lang, "call_stats_panel.btn_group_all"), _sel("all", "all"))],
            [
                _btn(t(lang, "call_stats_panel.btn_group_week"), _sel("all", "week")),
                _btn(t(lang, "call_stats_panel.btn_group_today"), _sel("all", "today")),
            ],
            [_btn(t(lang, "call_stats_panel.btn_vip_all"), _sel("vip", "all"))],
            [
                _btn(t(lang, "call_stats_panel.btn_vip_week"), _sel("vip", "week")),
                _btn(t(lang, "call_stats_panel.btn_vip_today"), _sel("vip", "today")),
            ],
            [_btn(t(lang, "call_stats_panel.btn_admin_all"), _sel("admin", "all"))],
            [
                _btn(t(lang, "call_stats_panel.btn_admin_week"), _sel("admin", "week")),
                _btn(t(lang, "call_stats_panel.btn_admin_today"), _sel("admin", "today")),
            ],
            [_btn(t(lang, "common.buttons.close"), close_cb)],
        ]
        return InlineKeyboardMarkup(rows)

    # ── TV channels menu ──────────────────────────────────────────────────

    @staticmethod
    def tv_channels_menu(
        lang: str, channels: list[dict[str, str]]
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for ch in channels:
            rows.append(
                [_btn(ch["name"], f"pb:tv:{ch['id']}")]
            )
        rows.append(
            [_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])]
        )
        return InlineKeyboardMarkup(rows)

    # ── Install policy panel ─────────────────────────────────────────────

    @staticmethod
    def install_policy_panel(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    _btn(
                        t(lang, "admin.install_policy.set_mode"),
                        CB["DEV_INSTALL_POLICY_MODE"],
                    ),
                    _btn(
                        t(lang, "admin.install_policy.set_trial"),
                        CB["DEV_INSTALL_POLICY_TRIAL"],
                    ),
                ],
                [
                    _btn(
                        t(lang, "admin.install_policy.whitelist_title"),
                        CB["DEV_INSTALL_POLICY_WHITELIST"],
                    )
                ],
                _nav_row(lang, back_cb=CB["NAV_BACK"]),
            ]
        )

    @staticmethod
    def install_policy_whitelist_panel(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    _btn(
                        t(lang, "admin.install_policy.whitelist_add"),
                        CB["DEV_INSTALL_POLICY_WHITELIST_ADD"],
                    ),
                    _btn(
                        t(lang, "admin.install_policy.whitelist_remove"),
                        CB["DEV_INSTALL_POLICY_WHITELIST_RM"],
                    ),
                ],
                [
                    _btn(
                        t(lang, "admin.install_policy.whitelist_list"),
                        CB["DEV_INSTALL_POLICY_WHITELIST_LIST"],
                    )
                ],
                _nav_row(lang, back_cb=CB["DEV_INSTALL_POLICY"]),
            ]
        )

    # ── Satellite channels menu ──────────────────────────────────────────

    @staticmethod
    def satellite_channels_menu(
        lang: str, channels: list[dict[str, str]], page: int = 0, per_page: int = 8,
    ) -> InlineKeyboardMarkup:
        total_pages = max(1, (len(channels) + per_page - 1) // per_page)
        start = page * per_page
        end = start + per_page
        page_items = channels[start:end]

        rows: list[list[InlineKeyboardButton]] = []
        for ch in page_items:
            rows.append([_btn(ch["name"], f"pb:sat:{ch['id']}")])

        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(_btn(t(lang, "common.buttons.prev"), f"pb:sat:page:{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(_btn(t(lang, "common.buttons.next"), f"pb:sat:page:{page + 1}"))
        if nav_row:
            rows.append(nav_row)

        rows.append([_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])])
        rows.append([_btn(t(lang, "common.buttons.home"), CB["WZ_HOME"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def catalog_group_menu(
        lang: str,
        groups: list[tuple[str, str, int]],
        *,
        callback_prefix: str,
    ) -> InlineKeyboardMarkup:
        """Category/language step for a stream catalog (CONTENT-03/04/05/06).

        ``groups`` is ``(slug, label, count)``; two buttons per row.
        """
        rows: list[list[InlineKeyboardButton]] = []
        for index in range(0, len(groups), 2):
            rows.append(
                [
                    _btn(f"{label} ({count})", f"{callback_prefix}{slug}")
                    for slug, label, count in groups[index : index + 2]
                ]
            )
        rows.append([_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])])
        rows.append([_btn(t(lang, "common.buttons.home"), CB["WZ_HOME"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def satellite_group_channels_menu(
        lang: str,
        channels: list[dict[str, str]],
        group_slug: str,
        page: int = 0,
        per_page: int = 8,
    ) -> InlineKeyboardMarkup:
        """Paginated satellite channels within one topic group."""
        total_pages = max(1, (len(channels) + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        start = page * per_page
        page_items = channels[start : start + per_page]

        rows: list[list[InlineKeyboardButton]] = [
            [_btn(ch["name"], f"pb:sat:{ch['id']}")] for ch in page_items
        ]
        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(
                _btn(t(lang, "common.buttons.prev"), f"pb:sat:g:{group_slug}:p:{page - 1}")
            )
        if page < total_pages - 1:
            nav_row.append(
                _btn(t(lang, "common.buttons.next"), f"pb:sat:g:{group_slug}:p:{page + 1}")
            )
        if nav_row:
            rows.append(nav_row)

        rows.append([_btn(t(lang, "common.buttons.back"), CB["PB_SAT"])])
        rows.append([_btn(t(lang, "common.buttons.home"), CB["WZ_HOME"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def radio_group_stations_menu(
        lang: str, stations: list[dict[str, str]],
    ) -> InlineKeyboardMarkup:
        """Radio stations within one country group."""
        rows: list[list[InlineKeyboardButton]] = [
            [_btn(s["name"], f"pb:radio:{s['id']}")] for s in stations
        ]
        rows.append([_btn(t(lang, "common.buttons.back"), CB["PB_RADIO"])])
        rows.append([_btn(t(lang, "common.buttons.home"), CB["WZ_HOME"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def equalizer_menu(lang: str, current: str = "normal") -> InlineKeyboardMarkup:
        """Audio-filter preset picker; the active preset is marked inline."""
        from app.services.group_text_call_command_service import EQUALIZER_PRESETS

        rows: list[list[InlineKeyboardButton]] = []
        for index in range(0, len(EQUALIZER_PRESETS), 2):
            row = []
            for preset in EQUALIZER_PRESETS[index : index + 2]:
                label = t(lang, f"group_text_call.eq_preset_{preset}")
                if preset == current:
                    label = f"✅ {label}"
                row.append(_btn(label, f"eq:set:{preset}"))
            rows.append(row)
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def vip_duration_picker(
        lang: str, user_id: int, days: int = 0, hours: int = 0,
    ) -> InlineKeyboardMarkup:
        """Day/hour stepper used to set the expiry of a freshly granted VIP role."""
        base = f"{CB['VIP_DURATION_PREFIX']}%s:{int(user_id)}:{int(days)}:{int(hours)}"
        return InlineKeyboardMarkup(
            [
                [
                    _btn("➖", base % "dm"),
                    _btn(t(lang, "manager_text.vip_dur_days", days=int(days)), "noop"),
                    _btn("➕", base % "dp"),
                ],
                [
                    _btn("➖", base % "hm"),
                    _btn(t(lang, "manager_text.vip_dur_hours", hours=int(hours)), "noop"),
                    _btn("➕", base % "hp"),
                ],
                [
                    _btn(t(lang, "manager_text.vip_dur_confirm"), base % "ok"),
                    _btn(t(lang, "manager_text.vip_dur_close"), base % "x"),
                ],
            ]
        )

    # ── Group management submenu ─────────────────────────────────────────

    @staticmethod
    def group_management_menu(lang: str) -> InlineKeyboardMarkup:
        rows = [
            [
                _btn(t(lang, "panels.group.management.owners_list"), CB["GRP_OWNERS_LIST"]),
                _btn(t(lang, "panels.group.management.deputies_list"), CB["GRP_DEPUTIES_LIST"]),
            ],
            [
                _btn(t(lang, "panels.group.management.admins_list"), CB["GRP_ADMINS_LIST"]),
                _btn(t(lang, "panels.group.management.vip_list"), CB["GRP_VIP_LIST"]),
            ],
            [
                _btn(t(lang, "panels.group.management.clear_music_video_admins"), CB["GRP_CLEAR_ALL"]),
            ],
        ]
        rows.append(_nav_row(lang, back_cb=CB["NAV_BACK"]))
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def group_clear_admins_confirm(
        lang: str, chat_id: int, user_id: int, issued_at: int,
    ) -> InlineKeyboardMarkup:
        token = f"{chat_id}:{user_id}:{issued_at}"
        return InlineKeyboardMarkup([
            [_btn(t(lang, "common.buttons.confirm"), f"{CB['GRP_CLEAR_CONFIRM_PREFIX']}{token}")],
            [_btn(t(lang, "common.buttons.cancel_inline"), f"{CB['GRP_CLEAR_CANCEL_PREFIX']}{token}")],
            [_btn(t(lang, "common.buttons.back"), CB["GRP_MANAGEMENT"])],
        ])

    @staticmethod
    def group_vip_clear_confirm(
        lang: str, chat_id: int, user_id: int, issued_at: int,
    ) -> InlineKeyboardMarkup:
        token = f"{chat_id}:{user_id}:{issued_at}"
        return InlineKeyboardMarkup([
            [_btn(t(lang, "common.buttons.confirm"), f"{CB['GRP_VIP_CLEAR_CONFIRM_PREFIX']}{token}")],
            [_btn(t(lang, "common.buttons.cancel_inline"), f"{CB['GRP_VIP_CLEAR_CANCEL_PREFIX']}{token}")],
            [_btn(t(lang, "common.buttons.back"), CB["GRP_MANAGEMENT"])],
        ])

    # ── Group help submenu ───────────────────────────────────────────────

    @staticmethod
    def group_help_menu(lang: str) -> InlineKeyboardMarkup:
        nav = [
            _btn(t(lang, "panels.group.help.promote_demote"), CB["GRP_PROMOTE_DEMOTE"]),
            _btn(t(lang, "panels.group.help.play_commands"), CB["GRP_PLAY_COMMANDS"]),
            _btn(t(lang, "panels.group.help.general_commands"), CB["GRP_GENERAL_COMMANDS"]),
            _btn(t(lang, "panels.group.help.manager_commands"), CB["GRP_MANAGER_COMMANDS"]),
            _btn(t(lang, "panels.group.help.call_commands"), CB["GRP_CALL_COMMANDS"]),
            _btn(t(lang, "panels.group.help.support_request"), CB["GRP_SUPPORT_REQUEST"]),
        ]
        rows = _chunk_by_width(nav)
        rows.append(_nav_row(lang, back_cb=CB["NAV_BACK"]))
        return InlineKeyboardMarkup(rows)

    # ── Group support submenu ────────────────────────────────────────────

    @staticmethod
    def group_support_menu(lang: str) -> InlineKeyboardMarkup:
        nav = [
            _btn(t(lang, "panels.group.support.sudo"), CB["GRP_SUDO"]),
            _btn(t(lang, "panels.group.support.guide_channel"), CB["GRP_GUIDE_CHANNEL"]),
            _btn(t(lang, "panels.group.support.support_group"), CB["GRP_SUPPORT_GROUP"]),
            _btn(t(lang, "panels.group.support.bot_channel"), CB["GRP_BOT_CHANNEL"]),
            _btn(t(lang, "panels.group.support.messenger"), CB["GRP_MESSENGER"]),
        ]
        rows = _chunk_by_width(nav)
        rows.append(_nav_row(lang, back_cb=CB["NAV_BACK"]))
        return InlineKeyboardMarkup(rows)

    # ── Post-install panel ───────────────────────────────────────────────

    @staticmethod
    def post_install_panel(
        lang: str,
        credit_days: int,
        charged_by: str | None,
        guide_link: str,
        *,
        show_credit_controls: bool = False,
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = [
            [_btn(t(lang, "install.panel_credit", days=credit_days), CB["NOOP"])],
        ]
        if charged_by:
            rows.append(
                [_btn(t(lang, "install.panel_charged_by", user=charged_by), CB["NOOP"])]
            )
        if show_credit_controls:
            rows.append(
                [
                    _btn(t(lang, "install.increase_credit_btn"), CB["POST_INSTALL_INC_CREDIT"]),
                    _btn(t(lang, "install.decrease_credit_btn"), CB["POST_INSTALL_DEC_CREDIT"]),
                ]
            )
        rows += [
            [_btn(t(lang, "install.player_panel_btn"), CB["POST_INSTALL_PANEL"])],
            [
                _btn(t(lang, "install.player_help_btn"), CB["POST_INSTALL_HELP"]),
                _url_btn(t(lang, "install.guide_channel_btn"), guide_link),
            ],
            [_btn(t(lang, "common.buttons.close"), CB["NAV_CLOSE"])],
        ]
        return InlineKeyboardMarkup(rows)

    # ── Sudo link management panel ───────────────────────────────────────

    @staticmethod
    def sudo_link_panel(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [_btn(t(lang, "sudo_mgmt.link_set_btn"), CB["DEV_SUDO_LINK_SET"])],
            [_btn(t(lang, "sudo_mgmt.link_remove_btn"), CB["DEV_SUDO_LINK_RM"])],
            [_btn(t(lang, "sudo_mgmt.link_list_title"), CB["DEV_SUDO_LINK_LIST"])],
            [_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])],
        ])

    @staticmethod
    def sudo_link_list_back(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [_btn(t(lang, "common.buttons.back"), CB["DEV_SUDO_LINK_MENU"])],
        ])

    _BC_CANCELABLE_STATUSES = frozenset({"pending", "running"})

    @classmethod
    def _bc_is_cancelable(cls, status: str | None) -> bool:
        return status in cls._BC_CANCELABLE_STATUSES

    @staticmethod
    def broadcast_detail(lang: str, broadcast) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        status = getattr(broadcast, "status", None)
        bc_id = getattr(broadcast, "id", None)
        if bc_id is not None and KeyboardFactory._bc_is_cancelable(status):
            rows.append([
                _btn(t(lang, "admin.bc.cancel_btn"), f"{CB['BC_CANCEL']}:{bc_id}"),
            ])
        rows.append([_btn(t(lang, "common.buttons.back"), CB["BC_HISTORY"])])
        return InlineKeyboardMarkup(rows)

    # ── Forced membership panel ──────────────────────────────────────────

    @staticmethod
    def forced_membership_panel(
        lang: str, is_enabled: bool, target_count: int,
    ) -> InlineKeyboardMarkup:
        status_key = "admin.fm.status_enabled" if is_enabled else "admin.fm.status_disabled"
        toggle_label = t(lang, "admin.fm.toggle_btn")
        return InlineKeyboardMarkup(
            [
                [_btn(t(lang, status_key), CB["FM_PANEL"])],
                [_btn(t(lang, "admin.fm.target_count", count=target_count), CB["FM_PANEL"])],
                [
                    _btn(
                        toggle_label,
                        CB["FM_TOGGLE"],
                        toggle_state=is_enabled,
                    )
                ],
                [
                    _btn(t(lang, "admin.fm.list_btn"), CB["FM_LIST"]),
                    _btn(t(lang, "admin.fm.add_btn"), CB["FM_ADD"]),
                ],
                [
                    _btn(t(lang, "admin.fm.verify_btn"), CB["FM_VERIFY_ALL"]),
                    _btn(t(lang, "admin.fm.test_btn"), CB["FM_TEST"]),
                ],
                [_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])],
            ]
        )

    # ── Broadcast history ─────────────────────────────────────────────

    @staticmethod
    def broadcast_history(
        lang: str, broadcasts: list,
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for bc in broadcasts:
            button_label = t(
                lang,
                "admin.bc.history_item",
                id=bc.id,
                scope=localized_label(lang, "broadcast_scope", bc.target_scope),
                status=localized_label(lang, "broadcast_status", bc.status),
                sent=bc.sent_count,
                total=bc.total_recipients,
            )
            row = [_btn(button_label, f"{CB['BC_DETAIL']}:{bc.id}")]
            if KeyboardFactory._bc_is_cancelable(getattr(bc, "status", None)):
                row.append(
                    _btn(t(lang, "admin.bc.cancel_btn"), f"{CB['BC_CANCEL']}:{bc.id}"),
                )
            rows.append(row)
        if not rows:
            rows.append([_btn(t(lang, "admin.bc.history_empty"), CB["NAV_BACK"])])
        rows.append([_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def broadcast_cancel_confirm(
        lang: str,
        broadcast_id: int,
        user_id: int,
        issued_at: int,
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _btn(
                    t(lang, "admin.bc.cancel_confirm_btn"),
                    f"{CB['BC_CANCEL_CONFIRM_PREFIX']}{broadcast_id}:{user_id}:{issued_at}",
                )
            ],
            [
                _btn(
                    t(lang, "admin.bc.cancel_abort_btn"),
                    f"{CB['BC_CANCEL_ABORT_PREFIX']}{broadcast_id}:{user_id}:{issued_at}",
                )
            ],
            [_btn(t(lang, "common.buttons.back"), CB["BC_HISTORY"])],
        ])

    # ── Back button (reusable) ────────────────────────────────────────────

    @staticmethod
    def back_button(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])]]
        )

    # ── Analytics panel ────────────────────────────────────────────────────

    @staticmethod
    def analytics_home(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _btn(t(lang, "admin.analytics.yesterday_btn"), CB["AN_YESTERDAY"]),
                _btn(t(lang, "admin.analytics.last_7d_btn"), CB["AN_7DAYS"]),
            ],
            [
                _btn(t(lang, "admin.analytics.last_14d_btn"), CB["AN_14DAYS"]),
                _btn(t(lang, "admin.analytics.last_30d_btn"), CB["AN_30DAYS"]),
            ],
            [
                _btn(t(lang, "admin.analytics.peak_hours_btn"), CB["AN_PEAK"]),
                _btn(t(lang, "admin.analytics.errors_btn"), CB["AN_ERRORS"]),
            ],
            [_btn(t(lang, "common.buttons.back"), CB["NAV_BACK"])],
        ])

    @staticmethod
    def analytics_report(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _btn(t(lang, "admin.analytics.by_feature_btn"), CB["AN_BY_FEATURE"]),
                _btn(t(lang, "admin.analytics.by_chat_type_btn"), CB["AN_BY_CHAT_TYPE"]),
            ],
            [
                _btn(t(lang, "admin.analytics.by_role_btn"), CB["AN_BY_ROLE"]),
            ],
            [
                _btn(t(lang, "common.buttons.back"), CB["AN_HOME"]),
            ],
        ])

    @staticmethod
    def analytics_drilldown_back(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [_btn(t(lang, "common.buttons.back"), CB["AN_HOME"])],
        ])

    # ── Helper management panel ────────────────────────────────────────────

    @staticmethod
    def helper_home(lang: str, active: int, disabled: int, quarantined: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [_btn(t(lang, "admin.helpers.summary",
                    active=active, disabled=disabled, quarantined=quarantined),
                  CB["HLP_HOME"])],
            [
                _btn(t(lang, "admin.helpers.list_btn"), CB["HLP_LIST"]),
                _btn(t(lang, "admin.helpers.health_btn"), CB["HLP_HEALTH_CHECK"]),
            ],
            [
                _btn(t(lang, "admin.helpers.add_btn"), CB["HLP_ADD"]),
                _btn(t(lang, "admin.helpers.rotate_key_btn"), CB["HLP_ROTATE_KEY"]),
            ],
            [_btn(t(lang, "admin.helpers.stats_btn"), CB["HLP_STATS"])],
            _nav_row(lang, back_cb=CB["NAV_BACK"]),
        ])

    @staticmethod
    def helper_rotate_key_confirm(lang: str, user_id: int, issued_at: int) -> InlineKeyboardMarkup:
        token = f"{user_id}:{issued_at}"
        return InlineKeyboardMarkup([
            [_btn(t(lang, "common.buttons.confirm"), f"{CB['HLP_ROTATE_CONFIRM_PREFIX']}{token}")],
            [_btn(t(lang, "common.buttons.cancel_inline"), f"{CB['HLP_ROTATE_CANCEL_PREFIX']}{token}")],
            [_btn(t(lang, "common.buttons.back"), CB["HLP_HOME"])],
        ])

    @staticmethod
    def helper_state_action_confirm(
        lang: str,
        exec_prefix: str,
        abort_prefix: str,
        helper_id: int,
        user_id: int,
        issued_at: int,
    ) -> InlineKeyboardMarkup:
        token = f"{helper_id}:{user_id}:{issued_at}"
        return InlineKeyboardMarkup([
            [_btn(t(lang, "common.buttons.confirm"), f"{exec_prefix}{token}")],
            [_btn(t(lang, "common.buttons.cancel_inline"), f"{abort_prefix}{token}")],
            [_btn(t(lang, "common.buttons.back"), f"{CB['HLP_DETAIL_PREFIX']}{helper_id}")],
        ])

    @staticmethod
    def dev_text_clear_confirm(
        lang: str,
        field: str,
        user_id: int,
        issued_at: int,
    ) -> InlineKeyboardMarkup:
        token = f"{field}:{user_id}:{issued_at}"
        return InlineKeyboardMarkup([
            [_btn(t(lang, "common.buttons.confirm"), f"{CB['DEV_TEXT_CLEAR_EXEC_PREFIX']}{token}")],
            [_btn(t(lang, "common.buttons.cancel_inline"), f"{CB['DEV_TEXT_CLEAR_ABORT_PREFIX']}{token}")],
            [_btn(t(lang, "common.buttons.back"), f"{CB['DEV_TEXT_FIELD_PREFIX']}{field}")],
        ])

    @staticmethod
    def owner_text_clear_confirm(
        lang: str,
        field: str,
        user_id: int,
        issued_at: int,
    ) -> InlineKeyboardMarkup:
        token = f"{field}:{user_id}:{issued_at}"
        return InlineKeyboardMarkup([
            [_btn(t(lang, "common.buttons.confirm"), f"{CB['OWN_TEXT_CLEAR_EXEC_PREFIX']}{token}")],
            [_btn(t(lang, "common.buttons.cancel_inline"), f"{CB['OWN_TEXT_CLEAR_ABORT_PREFIX']}{token}")],
            [_btn(t(lang, "common.buttons.back"), f"{CB['OWN_TEXT_FIELD_PREFIX']}{field}")],
        ])

    @staticmethod
    def dev_privileged_user_remove_confirm(
        lang: str,
        role: str,
        target_uid: int,
        user_id: int,
        issued_at: int,
    ) -> InlineKeyboardMarkup:
        token = f"{target_uid}:{user_id}:{issued_at}"
        if role == "owner":
            exec_prefix = CB["DEV_OWNER_REMOVE_EXEC_PREFIX"]
            abort_prefix = CB["DEV_OWNER_REMOVE_ABORT_PREFIX"]
        else:
            exec_prefix = CB["DEV_SUDO_REMOVE_EXEC_PREFIX"]
            abort_prefix = CB["DEV_SUDO_REMOVE_ABORT_PREFIX"]
        return InlineKeyboardMarkup([
            [
                _btn(
                    t(lang, "common.buttons.confirm"),
                    f"{exec_prefix}{token}",
                ),
                _btn(
                    t(lang, "common.buttons.cancel_inline"),
                    f"{abort_prefix}{token}",
                ),
            ],
            [_btn(t(lang, "common.buttons.back"), f"{CB['WZ_BACK_PREFIX']}dev_users")],
        ])

    @staticmethod
    def helper_add_menu(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _btn(t(lang, "admin.helpers.add_method_otp_btn"), CB["HLP_ADD_OTP"]),
                _btn(t(lang, "admin.helpers.add_method_import_btn"), CB["HLP_IMPORT_SESSION"]),
            ],
            _nav_row(lang, back_cb=CB["HLP_HOME"], home_cb=CB["WZ_HOME"]),
        ])

    @staticmethod
    def helper_detail(lang: str, helper_id: int, status: str) -> InlineKeyboardMarkup:
        rows = []
        if status == "active":
            rows.append([_btn(t(lang, "admin.helpers.disable_btn"),
                              f"{CB['HLP_DISABLE']}{helper_id}")])
        else:
            rows.append([_btn(t(lang, "admin.helpers.enable_btn"),
                              f"{CB['HLP_ENABLE']}{helper_id}")])
        if status == "quarantined":
            rows.append([_btn(t(lang, "admin.helpers.unquarantine_btn"),
                              f"{CB['HLP_UNQUARANTINE']}{helper_id}")])
        else:
            rows.append([_btn(t(lang, "admin.helpers.quarantine_btn"),
                              f"{CB['HLP_QUARANTINE']}{helper_id}")])
        rows.append([_btn(t(lang, "admin.helpers.set_proxy_btn"), f"{CB['HLP_SET_PROXY_PREFIX']}{helper_id}")])
        rows.append([_btn(t(lang, "common.buttons.back"), CB["HLP_LIST"])])
        return InlineKeyboardMarkup(rows)

    # ── Help Center keyboards ─────────────────────────────────────────────

    @staticmethod
    def help_home(
        lang: str,
        role: str,  # noqa: ARG004 - kept for compatibility with existing callers.
        is_group: bool = False,  # noqa: ARG004 - Help reference layout is scope-neutral.
        user_id: int | None = None,
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [_help_btn(lang, "promote", CB["HELP_PROMOTE"], user_id)],
            [
                _help_btn(lang, "play", CB["HELP_PLAYBACK"], user_id),
                _help_btn(lang, "public", CB["HELP_PUBLIC"], user_id),
            ],
            [_help_btn(lang, "utility", CB["HELP_UTILITY"], user_id)],
            [_help_btn(lang, "close", CB["HELP_CLOSE"], user_id)],
        ])

    @staticmethod
    def help_play_menu(lang: str, user_id: int | None = None) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _help_btn(lang, "play_reply", CB["HELP_PLAY_REPLY"], user_id),
                _help_btn(lang, "play_link", CB["HELP_PLAY_LINK"], user_id),
            ],
            [
                _help_btn(lang, "play_auto_music", CB["HELP_PLAY_AUTO_MUSIC"], user_id),
                _help_btn(lang, "play_auto_video", CB["HELP_PLAY_AUTO_VIDEO"], user_id),
            ],
            [
                _help_btn(lang, "youtube_search", CB["HELP_PLAY_YOUTUBE"], user_id),
                _help_btn(lang, "play_radio", CB["HELP_PLAY_RADIO"], user_id),
            ],
            [
                _help_btn(lang, "play_tv", CB["HELP_PLAY_TV"], user_id),
                _help_btn(lang, "play_satellite", CB["HELP_PLAY_SATELLITE"], user_id),
            ],
            [_help_btn(lang, "play_controls", CB["HELP_PLAY_CONTROLS"], user_id)],
            [
                _help_btn(lang, "close", CB["HELP_CLOSE"], user_id),
                _help_btn(lang, "back", CB["HELP_HOME"], user_id),
            ],
        ])

    @staticmethod
    def help_public_menu(lang: str, user_id: int | None = None) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                _help_btn(lang, "public_group", CB["HELP_PUBLIC_GROUP"], user_id),
                _help_btn(lang, "public_user", CB["HELP_PUBLIC_USER"], user_id),
            ],
            [
                _help_btn(lang, "close", CB["HELP_CLOSE"], user_id),
                _help_btn(lang, "back", CB["HELP_HOME"], user_id),
            ],
        ])

    @staticmethod
    def help_promote_menu(lang: str, user_id: int | None = None) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [_help_btn(lang, "promote_deputy", CB["HELP_PROMOTE_DEPUTY"], user_id)],
            [_help_btn(lang, "promote_admin", CB["HELP_PROMOTE_ADMIN"], user_id)],
            [_help_btn(lang, "promote_vip", CB["HELP_PROMOTE_VIP"], user_id)],
            [
                _help_btn(lang, "close", CB["HELP_CLOSE"], user_id),
                _help_btn(lang, "back", CB["HELP_HOME"], user_id),
            ],
        ])

    @staticmethod
    def help_detail_nav(
        lang: str,
        parent_cb: str,
        user_id: int | None = None,
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            _help_btn(lang, "close", CB["HELP_CLOSE"], user_id),
            _help_btn(lang, "back", parent_cb, user_id),
        ]])

    @staticmethod
    def help_section_back(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [_btn(t(lang, "common.buttons.back"), CB["HELP_HOME"])],
        ])
