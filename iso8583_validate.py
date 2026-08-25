# -*- coding: utf-8 -*-
"""
iso8583_validate.py
Field validators, report formatters, validation runners, and report assembly.
No dependency on Notepad++.
"""

from __future__ import annotations

import re
from collections import namedtuple

from iso8583_rules import NOT_REQUIRED
from iso8583_parse import parse_tlv, parse_subfields_fixed, fmt_de63_display_val

# Column widths for every validated table
COL = {
    "type": 6,
    "id": 8,
    "rule": 6,
    "value": 30,
    "desc": 30,
    "result": 7,
}

ValidationResult = namedtuple(
    "ValidationResult",
    "type_ id_ rule value desc result remark raw_value",
)


def truncate(s, width):
    if len(s) <= width:
        return s
    return s[: width - 3] + "..."


# ---------------------------------------------------------------------------
# Validation engine
# ---------------------------------------------------------------------------

def val_mandatory(val, rule):
    if val == "":
        return "FAIL", "Missing Mandatory Field"
    return "PASS", ""


def val_optional(val, rule):
    return "PASS", ""


def val_conditional(val, rule):
    if val == "":
        return "FAIL", "Conditional Field Missing"
    return "PASS", ""


def val_not_required(val, rule):
    if val != "":
        return "FAIL", "Should NOT Exist"
    return "PASS", ""


def val_blank(val, rule):
    if val != "":
        return "FAIL", "Should Be Blank"
    return "PASS", ""


def val_pattern_or_exact(val, rule):
    """Exact match, or pattern match when the rule contains wildcards.

    Wildcard convention (payment-industry style):
      N  -> any decimal digit  [0-9]
      X  -> any hex digit      [0-9A-Fa-f]

    Bare single-character "N" or "X" never reach this function: the
    dispatcher maps them to val_not_required first. Only multi-character
    rules such as "0030NN", "01N", "XX", "9FXX" are handled here.
    """
    if val == "":
        return "FAIL", "Missing"

    if "N" in rule or "X" in rule:
        parts = []
        for ch in rule:
            if ch == "N":
                parts.append("[0-9]")
            elif ch == "X":
                parts.append("[0-9A-Fa-f]")
            else:
                parts.append(re.escape(ch))
        regex = "^" + "".join(parts) + "$"
        if not re.match(regex, val):
            return "FAIL", "Expected pattern " + rule
        return "PASS", ""

    if val != rule:
        return "FAIL", "Expected exact value " + rule
    return "PASS", ""


VALIDATION_DISPATCHER = {
    "M": val_mandatory,
    "O": val_optional,
    "C": val_conditional,
    "N": val_not_required,   # preferred
    "X": val_not_required,   # backward-compatible alias
    "": val_blank,
}


def validate_field(rule, value):
    validator = VALIDATION_DISPATCHER.get(rule, val_pattern_or_exact)
    return validator(value, rule)


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def fmt_header(type_, id_, rule, value, desc, result, remark):
    return "{:<{t}}| {:<{i}}| {:<{r}}| {:<{v}}| {:<{d}}| {:<{res}}| {}".format(
        type_, id_, rule, value, desc, result, remark,
        t=COL["type"], i=COL["id"], r=COL["rule"],
        v=COL["value"], d=COL["desc"], res=COL["result"],
    )


def fmt_row(type_, id_, rule, value, desc, result, remark):
    return "{:<{t}}  {:<{i}}  {:<{r}}  {:<{v}}  {:<{d}}  {:<{res}}  {}".format(
        type_, id_, rule, value, desc, result, remark,
        t=COL["type"], i=COL["id"], r=COL["rule"],
        v=COL["value"], d=COL["desc"], res=COL["result"],
    )


def render_result_row(res):
    disp_val = truncate(res.value, COL["value"])
    disp_desc = truncate(res.desc, COL["desc"])
    return fmt_row(
        res.type_, res.id_, res.rule, disp_val, disp_desc, res.result, res.remark
    )


# ---------------------------------------------------------------------------
# Validation runners
# ---------------------------------------------------------------------------

def run_standard_de_validation(RULES, fields, entry_mode):
    results = []
    lines = []
    lines.append(fmt_header("TYPE", "ID/TAG", "RULE", "VALUE", "DESCRIPTION", "RESULT", "REMARK"))
    lines.append("-" * 110)

    for de in sorted(RULES.keys(), key=lambda x: int(x)):
        rule = RULES[de].get(entry_mode, NOT_REQUIRED)
        value = fields.get(de, "")
        desc = RULES[de].get("DESC", "")
        result, remark = validate_field(rule, value)
        res = ValidationResult("DE", de, rule, value, desc, result, remark, value)
        results.append(res)
        lines.append(render_result_row(res))
    return results, lines


def run_emv_validation(ALL_EMV_RULES, txn_type, card_norm, fields):
    results = []
    lines = []
    if txn_type not in ALL_EMV_RULES:
        return results, lines

    de55_val = fields.get("55", "")
    lines.append("")
    lines.append("-" * 40 + " DE 55 EMV TAGS " + "-" * 44)
    lines.append("")

    if not de55_val:
        lines.append("  DE55 not present in message - EMV tag validation skipped.")
        return results, lines

    emv_txn_rules = ALL_EMV_RULES[txn_type]
    emv_fields, de55_err = parse_tlv(de55_val)
    if de55_err:
        lines.append("  ** PARSE WARNING: %s **" % de55_err)
        lines.append("")
    lines.append(fmt_header("TYPE", "ID/TAG", "RULE", "VALUE", "DESCRIPTION", "RESULT", "REMARK"))
    lines.append("-" * 110)

    for tag in sorted(emv_txn_rules.keys()):
        rule = emv_txn_rules[tag].get(card_norm, NOT_REQUIRED)
        value = emv_fields.get(tag, "")
        desc = emv_txn_rules[tag].get("DESC", "")
        result, remark = validate_field(rule, value)
        res = ValidationResult("EMV", tag, rule, value, desc, result, remark, value)
        results.append(res)
        lines.append(render_result_row(res))
    return results, lines


def _run_subfield_validation(
    label,
    all_rules,
    fields,
    field_id,
    txn_type,
    entry_mode,
    display_val_fn=None,
    card_norm=None,
):
    results = []
    lines = []

    applied = {}
    for rule_tuple in all_rules:
        # Support both old 5-tuples and new 6-tuples (with cards).
        if len(rule_tuple) == 6:
            r_txn, r_entry, r_sub, r_rule, r_desc, r_cards = rule_tuple
        else:
            r_txn, r_entry, r_sub, r_rule, r_desc = rule_tuple
            r_cards = None

        txn_ok = (r_txn == "A") or (r_txn == txn_type)
        entry_ok = (r_entry == "A") or (r_entry == entry_mode)
        # cards is None => all brands; otherwise must contain card_norm
        if r_cards is None:
            card_ok = True
        else:
            card_ok = bool(card_norm) and (card_norm in r_cards)

        if txn_ok and entry_ok and card_ok:
            applied[r_sub] = {"rule": r_rule, "desc": r_desc}

    raw_val = fields.get(field_id, "")
    if not (raw_val or applied):
        return results, lines

    parsed, err = parse_subfields_fixed(raw_val)
    parsed_dict = {sid: {"len": ln, "val": v} for sid, ln, v in parsed}

    lines.append("")
    if err:
        lines.append("  ** PARSE WARNING: %s **" % err)
        lines.append("")

    if applied:
        title = (
            " DE 61 - Additional POS Data (Validated) "
            if label == "DE61"
            else " DE 63 - Additional Data (Validated) "
        )
        pad = (110 - len(title)) // 2
        lines.append("-" * pad + title + "-" * (110 - pad - len(title)))
        lines.append("")
        lines.append(fmt_header("TYPE", "SUBID", "RULE", "VALUE", "DESCRIPTION", "RESULT", "REMARK"))
        lines.append("-" * 110)

        all_keys = sorted(set(parsed_dict.keys()).union(applied.keys()))
        for sub_id in all_keys:
            sub_data = parsed_dict.get(sub_id, {"len": "", "val": ""})
            val = sub_data["val"]

            if sub_id not in applied and not val:
                continue

            if sub_id in applied:
                rule = applied[sub_id]["rule"]
                desc = applied[sub_id]["desc"]
                if not val and rule in ("O", ""):
                    continue
                result, remark = validate_field(rule, val)

                if result == "PASS" and not val:
                    results.append(
                        ValidationResult(label, sub_id, rule, val, desc, result, remark, val)
                    )
                    continue

                display = display_val_fn(sub_id, val) if display_val_fn else val
                res = ValidationResult(
                    label, sub_id, rule, display, desc, result, remark, val
                )
                results.append(res)
                lines.append(render_result_row(res))
            else:
                display = display_val_fn(sub_id, val) if display_val_fn else val
                res = ValidationResult(
                    label, sub_id, "-", display, "Unknown Subfield", "-", "", val
                )
                results.append(res)
                lines.append(render_result_row(res))
    else:
        title = (
            " DE 61 - Additional POS Data "
            if label == "DE61"
            else " DE 63 - Additional Data "
        )
        pad = (110 - len(title)) // 2
        lines.append("-" * pad + title + "-" * (110 - pad - len(title)))
        lines.append("")
        lines.append("%-10s | %-8s | %-50s" % ("Sub ID", "Length", "Value"))
        lines.append("-" * 110)
        if parsed:
            for sub_id, length_str, val in parsed:
                disp = display_val_fn(sub_id, val) if display_val_fn else val
                disp = truncate(disp, 60)
                lines.append("%-10s | %-8s | %-50s" % (sub_id, length_str, disp))
        else:
            disp = truncate(raw_val, 75)
            lines.append("%-10s | %-8s | %-50s" % ("RAW", "", disp))

    return results, lines


def run_de61_validation(ALL_DE61_RULES, fields, txn_type, entry_mode, card_norm=None):
    return _run_subfield_validation(
        "DE61", ALL_DE61_RULES, fields, "61", txn_type, entry_mode, card_norm=None
    )


def run_de63_validation(ALL_DE63_RULES, fields, txn_type, entry_mode, card_norm=None):
    return _run_subfield_validation(
        "DE63",
        ALL_DE63_RULES,
        fields,
        "63",
        txn_type,
        entry_mode,
        display_val_fn=fmt_de63_display_val,
        card_norm=card_norm,
    )


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_report(
    txn_type,
    card_norm,
    entry_norm,
    detected_card,
    detected_entry,
    pin_status,
    matched_leg_name,
    csv_warnings,
    de_results,
    de_lines,
    emv_results,
    emv_lines,
    de61_results,
    de61_lines,
    de63_results,
    de63_lines,
):
    report = []
    report.append("=" * 100)
    report.append("GP ISO8583 VALIDATION REPORT (CSV-DRIVEN) by Alden")
    report.append("=" * 100)

    if csv_warnings:
        report.append("")
        report.append("CONFIG WARNINGS (rows skipped while loading Rule Set CSVs):")
        for w in csv_warnings:
            report.append("  - " + w)

    report.append("")
    report.append("Txn Type   : " + txn_type.title())
    card_src = (
        "auto-detected from PAN" if detected_card else "default (PAN/BIN not detected)"
    )
    entry_src = (
        "auto-detected from DE22" if detected_entry else "default (DE22 not detected)"
    )
    report.append("Card       : " + card_norm.title() + "  [" + card_src + "]")
    report.append("Entry Mode : " + entry_norm.title() + "  [" + entry_src + "]")
    report.append("Pin Status : " + pin_status)
    report.append(
        "Msg Leg    : "
        + (matched_leg_name if matched_leg_name else "(not detected - used full selection)")
    )
    report.append("")
    report.append("-" * 42 + " DATA ELEMENTS " + "-" * 43)
    report.append("")
    report.extend(de_lines)
    report.extend(emv_lines)
    report.extend(de61_lines)
    report.extend(de63_lines)

    all_results = de_results + emv_results + de61_results + de63_results
    countable = [r for r in all_results if r.result in ("PASS", "FAIL")]
    passed = sum(1 for r in countable if r.result == "PASS")
    failed = sum(1 for r in countable if r.result == "FAIL")
    failed_details = [r for r in countable if r.result == "FAIL"]

    report.append("")
    report.append("=" * 100)
    report.append("FIELD CHECK PASS : %d" % passed)
    report.append("FIELD CHECK FAIL : %d" % failed)
    if csv_warnings:
        report.append("CONFIG WARNINGS  : %d (see top of report)" % len(csv_warnings))

    if failed > 0:
        report.append("")
        report.append(
            fmt_header("TYPE", "ID/TAG", "RULE", "VALUE", "DESCRIPTION", "RESULT", "REMARK")
        )
        for res in failed_details:
            fail_val = res.value if res.value else "MISSING"
            if res.type_ == "DE63":
                fail_val = (
                    fmt_de63_display_val(res.id_, res.raw_value)
                    if res.raw_value
                    else fail_val
                )
            tmp = ValidationResult(
                res.type_,
                res.id_.lstrip("0") or "0" if res.type_ in ("DE61", "DE63") else res.id_,
                res.rule,
                fail_val,
                res.desc,
                res.result,
                res.remark,
                res.raw_value,
            )
            report.append(render_result_row(tmp))

    report.append("=" * 100)
    return "\r\n".join(report), passed, failed
