# -*- coding: utf-8 -*-
"""
iso8583_rules.py
Constants, maps, normalizers, and CSV rule loaders.
No dependency on Notepad++.
"""

from __future__ import annotations

import csv
import os
import re

# ---------------------------------------------------------------------------
# Paths & target leg
# ---------------------------------------------------------------------------

PRIMARY_PATHS = {
    "config_path": r"C:\Users\Alden\Documents\Script\Rule Set.csv",
    "emv_config_path": r"C:\Users\Alden\Documents\Script\Rule Set - EMV Tag.csv",
    "de61_config_path": r"C:\Users\Alden\Documents\Script\Rule Set - DE61.csv",
    "de63_config_path": r"C:\Users\Alden\Documents\Script\Rule Set - DE63.csv",
}

# Default folder the standalone CLI scans for .txt message traces.
# Override at runtime with --folder / -d.
DEFAULT_INPUT_DIR = r"C:\Users\Alden\Documents\Script\iso8583_split\Input"

# Default folder where validation reports are written.
# Override at runtime with --reports, or edit this constant.
DEFAULT_REPORTS_DIR = r"C:\Users\Alden\Documents\Script\iso8583_split\Reports"

TARGET_LEG = "ACQ REQ"  # Change to retarget (e.g. "ISS REQ", "ISS RESP")

# Preferred not-required token. Bare "X" still works (compat) but warns.
NOT_REQUIRED = "N"
NOT_REQUIRED_ALIASES = {"N", "X"}

# ---------------------------------------------------------------------------
# Card / txn / entry maps
# ---------------------------------------------------------------------------

CARD_MAP = {
    "VISA": "VISA", "V": "VISA",
    "MASTERCARD": "MASTERCARD", "MC": "MASTERCARD", "M": "MASTERCARD",
    "JCB": "JCB", "J": "JCB",
    "UNIONPAY": "UNIONPAY", "UP": "UNIONPAY", "UPI": "UNIONPAY", "U": "UNIONPAY",
}

TXN_MENU_ITEMS = [
    ("SALE", "SALE"),
    ("SALETIP", "SALE w/TIP"),
    ("SALEADJ", "SALE ADJUST"),
    ("VOID", "VOID"),
    ("COMPLETION", "COMPLETION"),
    ("CVOID", "COMPLETION VOID"),
    ("REFUND", "REFUND"),
    ("RVOID", "VOID REFUND"),
    ("PREAUTH", "PREAUTH"),
    ("PVOID", "VOID PREAUTH"),
    ("TCUPLOAD", "TC UPLOAD"),
    ("RSALE", "SALE REVERSAL"),
    ("RREFUND", "REFUND REVERSAL"),
    ("RPREAUTH", "PREAUTH REVERSAL"),
    ("RCOMPLETION", "COMPLETION REVERSAL"),
    ("HSBC", "HSBC INST"),
    ("BPI", "BPI INST"),
    ("MIPP", "MIPP INST"),
    ("BPIOPT", "BPI OPT"),
    ("SETTLE", "SETTLEMENT"),
]

TXN_MAP = {}
for _idx, (_code, _desc) in enumerate(TXN_MENU_ITEMS, 1):
    TXN_MAP[str(_idx)] = _code
    TXN_MAP[_code.upper()] = _code
    TXN_MAP[_desc.upper()] = _code

ENTRY_MAP = {
    "CHIP": "CHIP", "C": "CHIP",
    "TAP": "TAP", "T": "TAP",
    "SWIPE": "SWIPE", "S": "SWIPE",
    "FALLBACK": "FALLBACK", "F": "FALLBACK",
    "MANUAL": "MANUAL", "M": "MANUAL",
}

ENTRY_MODE_INTERNAL = {
    "CHIP": "C", "TAP": "T", "SWIPE": "S", "FALLBACK": "F", "MANUAL": "M",
}

CARD_FILENAME_ABBR = {
    "VISA": "V", "MASTERCARD": "MC", "UNIONPAY": "UP", "JCB": "JCB",
}

# Approximate industry BIN/IIN ranges (not an authoritative acquirer table).
BIN_RANGES = {
    "VISA": lambda pan: pan.startswith("4"),
    "MASTERCARD": lambda pan: (
        (len(pan) >= 2 and pan[:2].isdigit() and 51 <= int(pan[:2]) <= 55)
        or (len(pan) >= 4 and pan[:4].isdigit() and 2221 <= int(pan[:4]) <= 2720)
    ),
    "JCB": lambda pan: (
        len(pan) >= 4 and pan[:4].isdigit() and 3528 <= int(pan[:4]) <= 3589
    ),
    "UNIONPAY": lambda pan: pan.startswith("62"),
}

DE22_ENTRY_MODE_MAP = {
    "01": "MANUAL",
    "02": "SWIPE",
    "80": "SWIPE",
    "90": "SWIPE",
    "91": "TAP",
    "05": "CHIP",
    "95": "CHIP",
    "07": "TAP",
    "79": "FALLBACK",
}

# MTI + card-group + first-4 of DE3 -> txn code or internal bucket.
TXN_MTI_PROC_MAP = {
    ("0100", "V", "0030"): "PREAUTH",
    ("0100", "U", "0000"): "PREAUTH",
    ("0100", "U", "2000"): "PVOID",
    ("0200", "V", "0030"): "_SALE_GROUP_V",
    ("0200", "U", "0000"): "_SALE_GROUP_U",
    ("0200", "V", "0230"): "VOID",
    ("0200", "U", "0200"): "VOID",
    ("0200", "V", "2030"): "REFUND",
    ("0200", "U", "2000"): "REFUND",
    ("0220", "V", "0030"): "COMPLETION",
    ("0220", "U", "0000"): "COMPLETION",
    ("0220", "V", "0230"): "_CVOID_SALEADJ_GROUP_V",
    ("0220", "U", "0200"): "CVOID",
    ("0320", "V", "9430"): "TCUPLOAD",
    ("0320", "U", "9400"): "TCUPLOAD",
    ("0400", "V", "0030"): "_REVERSAL_GROUP_V",
    ("0400", "U", "0000"): "_REVERSAL_GROUP_U",
    ("0400", "V", "2030"): "RREFUND",
    ("0400", "U", "2000"): "RREFUND",
    ("0400", "V", "2230"): "RVOID",
}

REVERSAL_ORIGINAL_MTI_MAP = {
    "0200": "RSALE",
    "0100": "RPREAUTH",
    "0220": "RCOMPLETION",
}

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def first_nonempty(*values_then_default):
    values = values_then_default[:-1]
    default = values_then_default[-1]
    for v in values:
        if v:
            return v
    return default


def normalized_lookup(clean_row):
    return {re.sub(r"[^A-Z0-9]", "", k): v for k, v in clean_row.items()}


def get_normalized(norm_row, *names):
    for name in names:
        key = re.sub(r"[^A-Z0-9]", "", name.upper())
        if key in norm_row:
            return norm_row[key]
    return ""


def clean_csv_row(row):
    clean = {}
    for k, v in row.items():
        if k is None:
            continue
        clean_key = k.replace("\xef\xbb\xbf", "").strip().upper()
        clean[clean_key] = v.strip() if v is not None else ""
    return clean


def pad_subfield_id(sub_id):
    try:
        return "{:02d}".format(int(sub_id))
    except (ValueError, TypeError):
        return str(sub_id).zfill(2)


def resolve_path(primary_path, script_dir=None):
    if os.path.isfile(primary_path):
        return primary_path
    if script_dir:
        # Basename must work for both Windows (\) and POSIX (/) paths,
        # because PRIMARY_PATHS use Windows separators and may be resolved
        # on Linux or via --rules-dir.
        base = primary_path.replace("\\", "/").split("/")[-1]
        candidate = os.path.join(script_dir, base)
        if os.path.isfile(candidate):
            return candidate
    return primary_path


def sanitize_filename_part(s):
    if not s:
        return ""
    return re.sub(r"[^A-Za-z0-9_\-]+", "", s.strip())


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------

def normalize_card(val):
    return CARD_MAP.get(val.strip().upper(), None) if val else None


def normalize_transaction_type(val):
    if not val:
        return None
    return TXN_MAP.get(val.strip().upper(), None)


def normalize_entry_mode(val):
    return ENTRY_MAP.get(val.strip().upper(), None) if val else None


# Cards column tokens for DE63 (comma-separated list, e.g. "V,M" or "VISA,MC").
# Empty / A / ALL / * => apply to every card brand.
CARD_TOKEN_MAP = {
    "V": "VISA",
    "VISA": "VISA",
    "M": "MASTERCARD",
    "MC": "MASTERCARD",
    "MASTERCARD": "MASTERCARD",
    "U": "UNIONPAY",
    "UP": "UNIONPAY",
    "UPI": "UNIONPAY",
    "UNIONPAY": "UNIONPAY",
    "J": "JCB",
    "JCB": "JCB",
}


def parse_cards_list(raw):
    """Parse a DE63 Cards cell into a frozenset of brand names, or None = all.

    Examples:
      "" / "A" / "ALL" / "*"  -> None (all cards)
      "V,M"                   -> frozenset({"VISA", "MASTERCARD"})
      "UP"                    -> frozenset({"UNIONPAY"})
    """
    if not raw or not str(raw).strip():
        return None
    tokens = [t.strip().upper() for t in str(raw).split(",") if t.strip()]
    if not tokens:
        return None
    if any(t in ("A", "ALL", "*") for t in tokens):
        return None
    brands = set()
    unknown = []
    for t in tokens:
        brand = CARD_TOKEN_MAP.get(t)
        if brand:
            brands.add(brand)
        else:
            unknown.append(t)
    if unknown and not brands:
        # Entirely unrecognised — treat as all so a typo doesn't hide rules
        return None
    return frozenset(brands) if brands else None


# ---------------------------------------------------------------------------
# CSV loaders
# ---------------------------------------------------------------------------

def _open_csv(path):
    return open(path, "r", newline="", encoding="utf-8-sig")


def load_standard_rules(config_path, csv_warnings):
    ALL_RULES = {}
    with _open_csv(config_path) as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, 2):
            clean = clean_csv_row(row)
            card = clean.get("CARDS", "").upper()
            tran_type = clean.get("TXN", "").upper()
            if not card or not tran_type:
                csv_warnings.append(
                    "Rule Set.csv row %d: skipped (missing CARDS or TXN)" % row_num
                )
                continue
            de_raw = clean.get("DE", "")
            try:
                de = str(int(de_raw))
            except ValueError:
                csv_warnings.append(
                    "Rule Set.csv row %d: skipped (bad DE value %r)" % (row_num, de_raw)
                )
                continue

            rule_key = "{}_{}".format(card, tran_type)
            if rule_key not in ALL_RULES:
                ALL_RULES[rule_key] = {}

            raw_rules = {
                "C": clean.get("C", NOT_REQUIRED),
                "S": clean.get("S", NOT_REQUIRED),
                "F": clean.get("F", NOT_REQUIRED),
                "M": clean.get("M", NOT_REQUIRED),
                "T": clean.get("T", NOT_REQUIRED),
            }
            ALL_RULES[rule_key][de] = {
                **raw_rules,
                "DESC": clean.get("DESCRIPTION", ""),
            }
    return ALL_RULES


def load_emv_rules(emv_config_path, csv_warnings):
    ALL_EMV_RULES = {}
    with _open_csv(emv_config_path) as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, 2):
            clean = {}
            emv_desc = ""
            for k, v in row.items():
                if k is None:
                    continue
                clean_key = k.replace("\xef\xbb\xbf", "").strip().upper()
                clean[clean_key] = v.strip().upper() if v is not None else ""
                if clean_key == "DESCRIPTION":
                    emv_desc = v.strip() if v is not None else ""

            raw_txn = clean.get("TXN", "")
            tran_type = normalize_transaction_type(raw_txn)
            tag = clean.get("TAG", "").strip().upper()
            if not tran_type or not tag:
                csv_warnings.append(
                    "EMV Tag CSV row %d: skipped (unrecognized TXN %r or missing TAG)"
                    % (row_num, raw_txn)
                )
                continue

            if tran_type not in ALL_EMV_RULES:
                ALL_EMV_RULES[tran_type] = {}

            brand_rules = {
                "VISA": clean.get("VISA", NOT_REQUIRED),
                "MASTERCARD": clean.get("MC", NOT_REQUIRED),
                "UNIONPAY": clean.get("UP", NOT_REQUIRED),
                "JCB": clean.get("JCB", NOT_REQUIRED),
            }
            ALL_EMV_RULES[tran_type][tag] = {**brand_rules, "DESC": emv_desc}
    return ALL_EMV_RULES


def _load_subfield_rules(path, label, csv_warnings, require_txn=True, parse_cards=False):
    """Load DE61/DE63 style rules.

    Each rule is a tuple:
      (tran_type, entry, padded_sub_id, rule, desc, cards)
    where cards is None (all brands) or a frozenset of brand names
    (VISA / MASTERCARD / UNIONPAY / JCB). cards is only populated when
    parse_cards=True (DE63); DE61 always stores None.
    """
    rules = []
    if not os.path.isfile(path):
        return rules

    with _open_csv(path) as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, 2):
            clean = clean_csv_row(row)
            norm = normalized_lookup(clean)

            raw_txn = first_nonempty(get_normalized(norm, "TXN"), "")
            if raw_txn:
                tran_type = normalize_transaction_type(raw_txn)
                if not tran_type:
                    csv_warnings.append(
                        "%s CSV row %d: skipped (unrecognized TXN %r)"
                        % (label, row_num, raw_txn)
                    )
                    continue
            else:
                if require_txn:
                    csv_warnings.append(
                        "%s CSV row %d: skipped (missing / unrecognized TXN)"
                        % (label, row_num)
                    )
                    continue
                tran_type = "A"

            entry = first_nonempty(
                get_normalized(norm, "ENTRY_MODE", "ENTRY MODE", "ENTRYMODE"),
                "A",
            ).upper()

            sub_id = first_nonempty(
                get_normalized(
                    norm,
                    "SUBFIELD_ID", "SUBFIELD ID", "SUBFIELDID", "SUB ID", "SUBID",
                ),
                "",
            )
            rule = first_nonempty(
                get_normalized(norm, "RULE"),
                get_normalized(norm, "RULE DESCRIPTION", "RULEDESCRIPTION"),
                NOT_REQUIRED,
            ).upper()
            desc = get_normalized(norm, "DESCRIPTION")

            cards = None
            if parse_cards:
                raw_cards = first_nonempty(
                    get_normalized(norm, "CARDS", "CARD", "CARD_TYPE", "CARDTYPE"),
                    "",
                )
                cards = parse_cards_list(raw_cards)

            if not sub_id:
                csv_warnings.append(
                    "%s CSV row %d: skipped (missing SUBFIELD_ID)" % (label, row_num)
                )
                continue

            rules.append(
                (tran_type, entry, pad_subfield_id(sub_id), rule, desc, cards)
            )
    return rules


def load_de61_rules(path, csv_warnings):
    # DE61: no Cards column filtering
    return _load_subfield_rules(
        path, "DE61", csv_warnings, require_txn=False, parse_cards=False
    )


def load_de63_rules(path, csv_warnings):
    # DE63: Cards column supported (comma-separated, e.g. "V,M")
    return _load_subfield_rules(
        path, "DE63", csv_warnings, require_txn=True, parse_cards=True
    )
