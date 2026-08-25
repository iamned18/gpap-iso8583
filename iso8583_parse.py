# -*- coding: utf-8 -*-
"""
iso8583_parse.py
Message leg isolation, DE field parsing, TLV/subfield parsers, and
auto-detection of card / entry mode / txn type.
No dependency on Notepad++.
"""

from __future__ import annotations

import re

from iso8583_rules import (
    BIN_RANGES,
    DE22_ENTRY_MODE_MAP,
    REVERSAL_ORIGINAL_MTI_MAP,
    TXN_MTI_PROC_MAP,
)

# ---------------------------------------------------------------------------
# Message extraction & field parsing
# ---------------------------------------------------------------------------

LEG_HEADER_RE = re.compile(
    r"^(Acq Req|Iss Req|Iss Resp|Acq Resp)\s*:", re.IGNORECASE
)


def extract_leg_block(text, target_leg):
    """Isolate one message leg from a multi-leg trace.

    Returns (leg_text, matched_name, all_leg_names) or (None, None, names)
    when the requested leg is absent.
    """
    lines = text.splitlines()
    leg_starts = []
    for idx, line in enumerate(lines):
        m = LEG_HEADER_RE.match(line.strip())
        if m:
            leg_starts.append((idx, m.group(1)))

    if not leg_starts:
        return text, None, []

    found_names = [name for _, name in leg_starts]
    target_upper = target_leg.strip().upper()
    for i, (idx, name) in enumerate(leg_starts):
        if name.strip().upper() == target_upper:
            start = idx
            end = leg_starts[i + 1][0] if i + 1 < len(leg_starts) else len(lines)
            return "\n".join(lines[start:end]), name, found_names
    return None, None, found_names


def parse_iso_fields(iso_text):
    """Parse DE lines of the form 'XXX nnn [value]' + Cont.. continuations.

    Switch-log quirks handled here:
      - ACCOUNT_NBR_LEN / PRIVATE_FLD61_LEN / etc. share the same DE number
        as the real value line — skip any field whose name ends with _LEN.
      - PRIMARY_BIT_MAP is also labeled 000; skip it so MESSAGE_ID_ISO keeps MTI.
      - Cont.. lines append to the active DE value.
    """
    fields = {}
    current_de = None
    for line in iso_text.splitlines():
        line = line.strip()
        start_match = re.search(r"^([A-Z0-9_]+)\s+(\d{3})\s+\[(.*)", line)
        if start_match:
            name = start_match.group(1).upper()
            de = str(int(start_match.group(2)))
            val = start_match.group(3)
            if val.endswith("]"):
                val = val[:-1]
            # Skip length-only lines and the primary bitmap (both collide on DE#)
            if name.endswith("_LEN") or name in ("PRIMARY_BIT_MAP", "SECONDARY_BIT_MAP"):
                continue
            current_de = de
            fields[current_de] = val
            continue
        if line.startswith("Cont..[") and current_de is not None:
            val = line[7:]
            if val.endswith("]"):
                val = val[:-1]
            fields[current_de] = fields.get(current_de, "") + val
    return fields


# ---------------------------------------------------------------------------
# Auto-detection (card / entry / txn)
# ---------------------------------------------------------------------------

def _extract_pan(fields):
    de2 = fields.get("2", "").strip()
    if de2:
        return re.sub(r"[^0-9]", "", de2)

    de35 = fields.get("35", "").strip()
    if de35:
        m = re.match(r"^([0-9]{6,19})[=D]", de35)
        if m:
            return m.group(1)

    de45 = fields.get("45", "").strip()
    if de45:
        m = re.match(r"^%?B?([0-9]{6,19})\^", de45)
        if m:
            return m.group(1)
    return ""


def detect_card_from_pan(fields):
    pan = _extract_pan(fields)
    if not pan or len(pan) < 2:
        return None
    for brand, matcher in BIN_RANGES.items():
        if matcher(pan):
            return brand
    return None


def detect_entry_mode_from_de22(fields):
    de22 = fields.get("22", "").strip()
    if len(de22) < 2:
        return None
    return DE22_ENTRY_MODE_MAP.get(de22[:2])


def de63_subfield_ids_present(fields):
    """Lightweight scan that only needs presence of subfield IDs."""
    parsed, _ = parse_subfields_fixed(fields.get("63", ""))
    return {sub_id for sub_id, _, _ in parsed}


def _resolve_txn_tiebreak(bucket, fields):
    if bucket in ("_REVERSAL_GROUP_V", "_REVERSAL_GROUP_U"):
        de60 = fields.get("60", "").strip()
        original_mti = de60[:4] if len(de60) >= 4 else ""
        return REVERSAL_ORIGINAL_MTI_MAP.get(original_mti)

    if bucket in ("_SALE_GROUP_V", "_SALE_GROUP_U"):
        if bucket == "_SALE_GROUP_V":
            # Hierarchy is non-overlapping on presence of subfields 10/20/23:
            #   BPI    -> 10+20+23
            #   MIPP   -> 10+20 (no 23)
            #   BPIOPT -> 20 only
            #   HSBC   -> 10 only
            sub_ids = de63_subfield_ids_present(fields)
            has10 = "10" in sub_ids
            has20 = "20" in sub_ids
            has23 = "23" in sub_ids
            if has10 and has20 and has23:
                return "BPI"
            if has10 and has20:
                return "MIPP"
            if has20 and not has10:
                return "BPIOPT"
            if has10:
                return "HSBC"
        if fields.get("54", "").strip():
            return "SALETIP"
        return "SALE"

    if bucket == "_CVOID_SALEADJ_GROUP_V":
        return "SALEADJ" if fields.get("54", "").strip() else "CVOID"

    return None


def detect_txn_type(fields, detected_card_group):
    mti = fields.get("0", "").strip()
    de3 = fields.get("3", "").strip()
    if len(mti) < 4:
        return None
    mti4 = mti[:4]
    if mti4 == "0500":
        return "SETTLE"
    if len(de3) < 4:
        return None
    proc4 = de3[:4]

    if detected_card_group in ("V", "U"):
        candidate_groups = [detected_card_group]
    else:
        candidate_groups = ["V", "U"]

    matches = []
    for grp in candidate_groups:
        bucket = TXN_MTI_PROC_MAP.get((mti4, grp, proc4))
        if bucket:
            matches.append(bucket)

    if len(matches) != 1:
        return None
    bucket = matches[0]
    if bucket.startswith("_"):
        return _resolve_txn_tiebreak(bucket, fields)
    return bucket


# ---------------------------------------------------------------------------
# Parsers (DE55 TLV & DE61/DE63 fixed subfields)
# ---------------------------------------------------------------------------

def parse_tlv(hex_str):
    """Return (tags_dict, error_message_or_None)."""
    tags = {}
    if not hex_str:
        return tags, None
    hex_str = re.sub(r"[^0-9A-Fa-f]", "", hex_str).upper()
    if len(hex_str) % 2 != 0:
        return tags, "DE55 hex string has odd length - likely truncated or corrupt"

    i = 0
    try:
        while i < len(hex_str):
            if hex_str[i : i + 2] in ("00", "FF"):
                i += 2
                continue

            tag_start = i
            tag_byte1 = int(hex_str[i : i + 2], 16)
            i += 2

            if (tag_byte1 & 0x1F) == 0x1F:
                if i >= len(hex_str):
                    return tags, "DE55 truncated mid-tag at offset %d" % tag_start
                tag_byte2 = int(hex_str[i : i + 2], 16)
                i += 2
                while (tag_byte2 & 0x80) == 0x80:
                    if i >= len(hex_str):
                        return tags, "DE55 truncated mid-tag at offset %d" % tag_start
                    tag_byte2 = int(hex_str[i : i + 2], 16)
                    i += 2

            tag = hex_str[tag_start:i]

            if i >= len(hex_str):
                return tags, "DE55 truncated after tag %s (missing length byte)" % tag
            len_byte = int(hex_str[i : i + 2], 16)
            i += 2

            if len_byte & 0x80:
                bytes_to_read = len_byte & 0x7F
                if bytes_to_read > 0:
                    if i + (bytes_to_read * 2) > len(hex_str):
                        return tags, "DE55 truncated reading extended length for tag %s" % tag
                    length = int(hex_str[i : i + (bytes_to_read * 2)], 16)
                    i += bytes_to_read * 2
                else:
                    length = 0
            else:
                length = len_byte

            if i + (length * 2) > len(hex_str):
                tags[tag] = hex_str[i:]
                return tags, (
                    "DE55 tag %s declared length %d but only %d bytes remained"
                    % (tag, length, (len(hex_str) - i) // 2)
                )

            value = hex_str[i : i + (length * 2)]
            i += length * 2
            tags[tag] = value
    except Exception as e:
        return tags, "DE55 parse error: " + str(e)
    return tags, None


def parse_subfields_fixed(hex_str):
    """Return (list_of_(sub_id, length_str, value), error_message_or_None)."""
    parsed_items = []
    if not hex_str:
        return parsed_items, None
    hex_str = re.sub(r"[^0-9A-Fa-f]", "", hex_str).upper()
    if len(hex_str) % 2 != 0:
        return parsed_items, "Subfield hex string has odd length - likely truncated or corrupt"

    i = 0
    try:
        while i < len(hex_str):
            if i + 8 > len(hex_str):
                return parsed_items, "Truncated mid-subfield-header at offset %d" % i

            sub_id_hex = hex_str[i : i + 4]
            try:
                decoded_sub = bytes.fromhex(sub_id_hex).decode("ascii", errors="replace")
                sub_id = decoded_sub.zfill(2) if decoded_sub.isalnum() else sub_id_hex
            except Exception:
                sub_id = sub_id_hex

            length_str = hex_str[i + 4 : i + 8]
            i += 8
            # Length is a 4-digit decimal byte count encoded as hex digits
            # (e.g. "0015" => 15 bytes, not 0x15=21). DE61 "0001" works either way.
            length_bytes = int(length_str, 10)
            value_len_hex = length_bytes * 2

            if i + value_len_hex > len(hex_str):
                value = hex_str[i:]
                parsed_items.append((sub_id, length_str, value))
                return parsed_items, (
                    "Subfield %s declared length %d but only %d bytes remained"
                    % (sub_id, length_bytes, (len(hex_str) - i) // 2)
                )

            value = hex_str[i : i + value_len_hex]
            parsed_items.append((sub_id, length_str, value))
            i += value_len_hex
    except Exception as e:
        return parsed_items, "Subfield parse error: " + str(e)
    return parsed_items, None


def hex_to_ascii_text(hex_val):
    if not hex_val:
        return None
    h = hex_val.strip()
    if len(h) % 2 != 0:
        return None
    try:
        raw = bytes.fromhex(h)
        text = raw.decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return None
    if not all(32 <= ord(c) <= 126 for c in text):
        return None
    return text


def fmt_de63_display_val(sub_id, raw_val):
    if sub_id == "11" and raw_val:
        decoded = hex_to_ascii_text(raw_val)
        if decoded is not None:
            return raw_val + "/" + decoded
    return raw_val
