from __future__ import annotations

import pytest

from app.utils.manager_text_commands import parse_manager_text_command


@pytest.mark.parametrize(
    "raw,family",
    [
        ("افزودن موزیک", "install"),
        ("نصب موزیک", "install"),
        ("نصب پلیر", "install"),
        ("حذف نصب موزیک", "uninstall"),
        ("حذف نصب پلیر", "uninstall"),
        ("حذف موزیک", "uninstall"),
        ("خروج موزیک", "leave"),
        ("ترک گروه موزیک", "leave"),
        ("Addm", "install"),
        ("AddMusic", "install"),
        ("RemM", "uninstall"),
        ("RemMusic", "uninstall"),
        ("RemPlayer", "uninstall"),
        ("LeaveM", "leave"),
        ("LeaveMusic", "leave"),
        ("LeavePlayer", "leave"),
        ("افزودن کمکی موزیک", "add_helper"),
        ("افزودن کمکی پلیر", "add_helper"),
        ("AddhelperMusic", "add_helper"),
        ("AddhelperM", "add_helper"),
        ("پیکربندی پلیر", "config"),
        ("پیکربندی موزیک", "config"),
        ("ConfigMusic", "config"),
        ("ConfigPlayer", "config"),
        ("Config Player", "config"),
        ("اعتبار موزیک", "expire"),
        ("اعتبار پلیر", "expire"),
        ("MusicExpire", "expire"),
        ("ExpirePlayer", "expire"),
        ("Expire Player", "expire"),
        ("لیست مالک پلیر", "owner_list"),
        ("لیست مالکان موزیک", "owner_list"),
        ("پاکسازی لیست مالک موزیک", "owner_clear"),
        ("پاکسازی لیست مالکان پلیر", "owner_clear"),
        ("OwnerListM", "owner_list"),
        ("OwnerListMusic", "owner_list"),
        ("OwnerListPlayer", "owner_list"),
        ("ClearOwnerListM", "owner_clear"),
        ("ClearOwnerListMusic", "owner_clear"),
        ("ClearOwnerListPlayer", "owner_clear"),
        ("لیست معاون پلیر", "deputy_list"),
        ("لیست معاونان پلیر", "deputy_list"),
        ("لیست معاونین موزیک", "deputy_list"),
        ("پاکسازی لیست معاون موزیک", "deputy_clear"),
        ("پاکسازی لیست معاونان پلیر", "deputy_clear"),
        ("پاکسازی لیست معاونین پلیر", "deputy_clear"),
        ("DeputyListM", "deputy_list"),
        ("DeputyListMusic", "deputy_list"),
        ("DeputyListPlayer", "deputy_list"),
        ("ListDeputy Player", "deputy_list"),
        ("ClearDeputyListM", "deputy_clear"),
        ("ClearDeputyListMusic", "deputy_clear"),
        ("ClearDeputyListPlayer", "deputy_clear"),
        ("ClearListDeputy Player", "deputy_clear"),
        ("لیست مدیر موزیک", "mod_list"),
        ("لیست مدیران پلیر", "mod_list"),
        ("پاکسازی لیست مدیران موزیک", "mod_clear"),
        ("پاکسازی لیست مدیران پلیر", "mod_clear"),
        ("ModListM", "mod_list"),
        ("ModListMusic", "mod_list"),
        ("ModListPlayer", "mod_list"),
        ("ListAdmin Player", "mod_list"),
        ("ClearModListM", "mod_clear"),
        ("ClearModListPlayer", "mod_clear"),
        ("ClearListAdmin Player", "mod_clear"),
        ("لیست ویژه پلیر", "vip_list"),
        ("لیست ویژه‌های پلیر", "vip_list"),
        ("ListVip Player", "vip_list"),
        ("VipListPlayer", "vip_list"),
        ("پاکسازی لیست ویژه پلیر", "vip_clear"),
        ("ClearListVip Player", "vip_clear"),
        ("ClearVipListPlayer", "vip_clear"),
    ],
)
def test_parser_accepts_exact_aliases(raw: str, family: str):
    parsed = parse_manager_text_command(raw)
    assert parsed is not None
    assert parsed.family == family
    assert parsed.error is None


@pytest.mark.parametrize(
    "raw,family",
    [
        ("ارتقا مالک موزیک", "owner_add"),
        ("افزودن مالک پلیر", "owner_add"),
        ("عزل مالک موزیک", "owner_remove"),
        ("حذف مالک پلیر", "owner_remove"),
        ("AddOwnerM", "owner_add"),
        ("SetOwnerMusic", "owner_add"),
        ("RemOwnerMusic", "owner_remove"),
        ("DemOwnerpPlayer", "owner_remove"),
        ("ارتقا معاون موزیک", "deputy_add"),
        ("ارتقا معاون پلیر", "deputy_add"),
        ("افزودن معاون پلیر", "deputy_add"),
        ("عزل معاون موزیک", "deputy_remove"),
        ("عزل معاون پلیر", "deputy_remove"),
        ("حذف معاون پلیر", "deputy_remove"),
        ("AddDeputyM", "deputy_add"),
        ("SetDeputyMusic", "deputy_add"),
        ("SetDeputy Player", "deputy_add"),
        ("RemDeputyMusic", "deputy_remove"),
        ("RemDeputy Player", "deputy_remove"),
        ("DemDeputypPlayer", "deputy_remove"),
        ("ترفیع موزیک", "mod_add"),
        ("ترفیع پلیر", "mod_add"),
        ("ارتقا مقام پلیر", "mod_add"),
        ("عزل موزیک", "mod_remove"),
        ("عزل مقام پلیر", "mod_remove"),
        ("PromoteM", "mod_add"),
        ("PromoteMusic", "mod_add"),
        ("PromotePLayer", "mod_add"),
        ("Promote Player", "mod_add"),
        ("DemoteM", "mod_remove"),
        ("DemoteMusic", "mod_remove"),
        ("DemotePlayer", "mod_remove"),
        ("Demote Player", "mod_remove"),
        ("ترفیع ویژه", "vip_add"),
        ("ارتقا ویژه پلیر", "vip_add"),
        ("افزودن ویژه پلیر", "vip_add"),
        ("SetVip Player", "vip_add"),
        ("AddVip Player", "vip_add"),
        ("PromoteVip", "vip_add"),
        ("عزل ویژه", "vip_remove"),
        ("عزل ویژه پلیر", "vip_remove"),
        ("حذف ویژه پلیر", "vip_remove"),
        ("RemVip Player", "vip_remove"),
        ("DemVip Player", "vip_remove"),
        ("DemoteVip", "vip_remove"),
    ],
)
def test_parser_accepts_target_aliases_with_reply_compatible_empty_target(raw: str, family: str):
    parsed = parse_manager_text_command(raw)
    assert parsed is not None
    assert parsed.family == family
    assert parsed.target_user is None


@pytest.mark.parametrize(
    "raw,mode,amount,target_chat_id",
    [
        ("شارژ موزیک 100", "increase", 100, None),
        ("شارژ پلیر 100+", "increase", 100, None),
        ("شارژ موزیک 100-", "decrease", 100, None),
        ("شارژ پلیر نامحدود", "unlimited", None, None),
        ("ChargeMusic +100", "increase", 100, None),
        ("ChargePlayer 100", "increase", 100, None),
        ("ChargeMusic -100", "decrease", 100, None),
        ("ChargePlayer Unlimit", "unlimited", None, None),
        ("شارژ موزیک 1002545672488- 20", "increase", 20, -1002545672488),
        ("شارژ پلیر 1002545672488- 20-", "decrease", 20, -1002545672488),
        ("ChargeMusic -1002545672488 +20", "increase", 20, -1002545672488),
        ("ChargePlayer -1002545672488 -20", "decrease", 20, -1002545672488),
        ("ChargeM -1002545672488 +20", "increase", 20, -1002545672488),
    ],
)
def test_parser_charge_aliases(raw: str, mode: str, amount: int | None, target_chat_id: int | None):
    parsed = parse_manager_text_command(raw)
    assert parsed is not None
    assert parsed.family == "charge"
    assert parsed.charge_mode == mode
    assert parsed.amount == amount
    assert parsed.target_chat_id == target_chat_id
    assert parsed.error is None


def test_parser_normalizes_case_digits_whitespace_and_zwnj():
    parsed = parse_manager_text_command("  chargemusic   +۱۲۳  ")
    assert parsed is not None
    assert parsed.family == "charge"
    assert parsed.amount == 123
    assert parsed.charge_mode == "increase"
    assert parse_manager_text_command("لیست\u200c مالکان موزیک") is not None


@pytest.mark.parametrize(
    "raw",
    [
        "متن عادی",
        "آپدیت شارژ 30",
        "StartCall",
        "پخش آهنگ",
        "/cancel",
        "/AddMusic",
        "ChargeMusic abc",
    ],
)
def test_parser_ignores_unrelated_or_invalid_unanchored_text(raw: str):
    parsed = parse_manager_text_command(raw)
    if raw == "ChargeMusic abc":
        assert parsed is not None
        assert parsed.error == "invalid_amount"
    else:
        assert parsed is None
