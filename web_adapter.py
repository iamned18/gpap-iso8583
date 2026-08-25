# -*- coding: utf-8 -*-
"""
web_adapter.py
Browser-facing glue for the Pyodide build. Wraps the untouched engine
modules (iso8583_rules / iso8583_parse / iso8583_validate) so the page's
JS can pass plain strings (pasted trace text, uploaded CSV text already
written to the Pyodide virtual filesystem) instead of Windows file paths
and interactive prompts.

Nothing in iso8583_rules.py / iso8583_parse.py / iso8583_validate.py is
modified to make this work -- this file is the only new code.
"""

from __future__ import annotations

import json

from iso8583_rules import (
    TXN_MENU_ITEMS,
    ENTRY_MODE_INTERNAL,
    normalize_card,
    normalize_entry_mode,
    normalize_transaction_type,
    load_standard_rules,
    load_emv_rules,
    load_de61_rules,
    load_de63_rules,
)
from iso8583_parse import (
    extract_leg_block,
    parse_iso_fields,
    detect_card_from_pan,
    detect_entry_mode_from_de22,
    detect_txn_type,
)
from iso8583_validate import (
    run_standard_de_validation,
    run_emv_validation,
    run_de61_validation,
    run_de63_validation,
    build_report,
)


def txn_menu_json():
    return json.dumps(TXN_MENU_ITEMS)


def web_run(
    iso_text,
    target_leg,
    config_path,
    emv_config_path,
    de61_config_path,
    de63_config_path,
    card_override=None,
    entry_override=None,
    txn_override=None,
):
    """Run the full validation pipeline against pasted trace text.

    Rule CSVs are read from paths on the Pyodide virtual filesystem --
    the JS side writes uploaded file contents there before calling this.
    Returns a plain dict (JSON-serialisable).
    """
    result = {"ok": False}

    csv_warnings = []
    try:
        all_rules = load_standard_rules(config_path, csv_warnings)
        all_emv_rules = load_emv_rules(emv_config_path, csv_warnings)
        all_de61_rules = load_de61_rules(de61_config_path, csv_warnings)
        all_de63_rules = load_de63_rules(de63_config_path, csv_warnings)
    except Exception as e:
        result["error"] = "Failed to parse a rules CSV: " + str(e)
        return result

    leg_text, matched_leg_name, legs_found = extract_leg_block(iso_text, target_leg)
    if leg_text is None:
        result["error"] = (
            "Could not find the '%s' leg in the pasted trace.\n\nLegs found: %s"
            % (target_leg, ", ".join(legs_found) if legs_found else "(none)")
        )
        result["legs_found"] = legs_found
        return result

    fields = parse_iso_fields(leg_text)
    has_pin_data = bool(fields.get("52", "")) or bool(fields.get("53", ""))
    pin_status = "Yes" if has_pin_data else "No"

    detected_card = detect_card_from_pan(fields)
    detected_card_group = (
        "V"
        if detected_card in ("VISA", "MASTERCARD", "JCB")
        else "U"
        if detected_card == "UNIONPAY"
        else None
    )
    detected_entry = detect_entry_mode_from_de22(fields)
    detected_txn = detect_txn_type(fields, detected_card_group)

    card_norm = (
        normalize_card(card_override)
        if card_override
        else (detected_card if detected_card else "MASTERCARD")
    )
    if not card_norm:
        card_norm = detected_card if detected_card else "MASTERCARD"
    card_code = (
        "V"
        if card_norm in ("VISA", "MASTERCARD", "JCB")
        else "U"
        if card_norm == "UNIONPAY"
        else card_norm
    )

    entry_norm = (
        normalize_entry_mode(entry_override)
        if entry_override
        else (detected_entry if detected_entry else "TAP")
    )
    if not entry_norm:
        entry_norm = detected_entry if detected_entry else "TAP"
    entry_mode = ENTRY_MODE_INTERNAL.get(entry_norm, "C")

    if txn_override:
        txn_type = normalize_transaction_type(txn_override)
    else:
        txn_type = detected_txn

    if not txn_type:
        result["error"] = (
            "Could not determine Transaction Type. Nothing was auto-detected "
            "from the MTI/DE3 -- pick one explicitly from the Transaction "
            "Type dropdown and run again."
        )
        result["detected_card"] = detected_card
        result["detected_entry"] = detected_entry
        result["detected_txn"] = detected_txn
        result["matched_leg_name"] = matched_leg_name
        result["legs_found"] = legs_found
        return result

    rule_key = "{}_{}".format(card_code, txn_type)
    if rule_key not in all_rules:
        result["error"] = (
            "Configuration for %s not found in the Standard Rule Set CSV." % rule_key
        )
        return result
    rules = all_rules[rule_key]

    de_results, de_lines = run_standard_de_validation(rules, fields, entry_mode)
    emv_results, emv_lines = run_emv_validation(
        all_emv_rules, txn_type, card_norm, fields
    )
    de61_results, de61_lines = run_de61_validation(
        all_de61_rules, fields, txn_type, entry_mode
    )
    de63_results, de63_lines = run_de63_validation(
        all_de63_rules, fields, txn_type, entry_mode, card_norm=card_norm
    )

    report_text, passed, failed = build_report(
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
    )

    de_ids_present = sorted(
        {d for d in fields.keys() if d.isdigit() and 1 <= int(d) <= 64}, key=int
    )

    result.update(
        {
            "ok": True,
            "report_text": report_text,
            "passed": passed,
            "failed": failed,
            "txn_type": txn_type,
            "card_norm": card_norm,
            "entry_norm": entry_norm,
            "detected_card": detected_card,
            "detected_entry": detected_entry,
            "detected_txn": detected_txn,
            "matched_leg_name": matched_leg_name,
            "legs_found": legs_found,
            "pin_status": pin_status,
            "csv_warnings": csv_warnings,
            "de_ids_present": de_ids_present,
        }
    )
    return result


def web_run_json(*args, **kwargs):
    return json.dumps(web_run(*args, **kwargs))
