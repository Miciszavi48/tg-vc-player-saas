# Callback risk audit (auto-generated)

Modules in registration order: **39**
Callback handlers scanned: **521**

## Cross-panel shortcut risks

No unresolved cross-panel shortcut risks detected.

## Duplicate exact-match handlers

- `AN_HOME` (`an:home`): analytics_panel.an_home, dev_panel.dev_shortcut_analytics_home
- `BCW_START` (`bcw:start`): broadcast_wizard.bcw_start, dev_panel.dev_shortcut_bcw_start
- `BC_HISTORY` (`bc:history`): broadcast_panel.bc_history, dev_panel.dev_shortcut_bc_history
- `HLP_HOME` (`hlp:home`): dev_panel.dev_shortcut_helper_home, helper_panel.hlp_home
- `NAV_BACK` (`nav:back`): callbacks.nav_back, group_panel.grp_nav_back
- `WZ_HOME` (`wz:home`): callbacks.wz_home, group_panel.grp_wz_home

## Edit-path gaps (raw edit_text, no safe helper)

Total: **133** (informational; not all are bugs)
- `broadcast_panel.bc_detail` (line 144)
- `broadcast_panel.bc_cancel` (line 177)
- `broadcast_panel.bc_cancel_confirm` (line 202)
- `broadcast_panel.bc_cancel_abort` (line 231)
- `call_security_panel.call_security_back` (line 220)
- `callbacks.start_about` (line 1031)
- `callbacks.postinst_panel` (line 1173)
- `callbacks.postinst_help` (line 1187)
- `dev_banall_panel.dev_banall_home` (line 120)
- `dev_banall_panel.dev_banall_clear` (line 236)
- `dev_banall_panel.dev_banall_clear_confirm` (line 248)
- `dev_banall_panel.dev_banall_clear_cancel` (line 269)
- `dev_panel.dev_status` (line 2903)
- `dev_panel.dev_monthly_invoice_prepare` (line 3057)
- `dev_panel.dev_monthly_invoice_send` (line 3071)
- `dev_panel.dev_monthly_invoice_auto_toggle` (line 3084)
- `dev_panel.dev_monthly_invoice_list` (line 3112)
- `dev_panel.dev_monthly_invoice_detail` (line 3122)
- `dev_panel.dev_title_apply_confirm` (line 3292)
- `dev_panel.dev_title_developer_clear` (line 3505)
- `dev_panel.dev_title_owner_clear` (line 3615)
- `dev_panel.dev_title_sudo_clear` (line 3791)
- `dev_panel.dev_sudo_perm_toggle` (line 3856)
- `dev_panel.dev_cat_texts` (line 4137)
- `dev_panel.dev_texts_back` (line 4147)
- `dev_panel.dev_texts_home` (line 4159)
- `dev_panel.dev_texts_home` (line 4159)
- `dev_panel.dev_start_style_toggle` (line 4172)
- `dev_panel.dev_text_field` (line 4192)
- `dev_panel.dev_text_clear` (line 4312)
- `dev_panel.dev_text_clear_confirm` (line 4329)
- `dev_panel.dev_text_clear_abort` (line 4354)
- `dev_panel.dev_invoice_history` (line 4576)
- `dev_panel.dev_channel_security_toggle` (line 4744)
- `dev_panel.dev_bot_update_reload_prompt` (line 4792)
- `dev_panel.dev_bot_update_reload_confirm` (line 4811)
- `dev_panel.dev_media_cleanup_preview` (line 4882)
- `dev_panel.dev_media_cleanup_confirm` (line 4913)
- `dev_panel.dev_media_ranking` (line 4969)
- `dev_panel.dev_media_export` (line 4987)
- … and 93 more
