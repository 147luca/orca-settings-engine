#!/usr/bin/env python3
"""
Enrich the settings dictionary with OrcaSlicer's own definitions.

Parses src/libslic3r/PrintConfig.cpp (each option declares label / category /
tooltip / sidetext(units) / min / max / enum_values) and joins it onto the
~760 keys discovered by orca_engine.py. Turns the scaffold into a real,
offline knowledge base: description, units, range, and enum domain come
straight from OrcaSlicer source — no per-setting web lookups needed.

Leftover columns (interacts_with / when_to_change) are still TODO — those are
tuning judgement, the next research pass.
"""

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CPP = HERE / "PrintConfig.cpp"
INDEX = HERE / "orca_all_settings.json"


def extract_literals(text):
    """Join adjacent C++ string literals: L("a" "b") -> 'ab'. Handles escapes."""
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', text)
    s = "".join(parts)
    s = s.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")
    # decode C++ \uXXXX escapes (e.g. ℃ -> ℃) without touching real UTF-8
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    return s.strip()


def parse_field(block, field):
    """Capture def->FIELD = L( ... ); spanning concatenated literals."""
    m = re.search(rf"def->{field}\s*=\s*L?\(", block)
    if not m:
        return None
    # read until the matching close paren on the assignment
    tail = block[m.end() :]
    depth = 1
    out = []
    for ch in tail:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        out.append(ch)
    return extract_literals("".join(out)) or None


def parse_scalar(block, field):
    m = re.search(rf"def->{field}\s*=\s*([-\d.]+)\s*;", block)
    return m.group(1) if m else None


def parse_enums(block):
    vals = re.findall(r"def->enum_values\.push_back\(\s*\"([^\"]+)\"", block)
    labels = re.findall(r"def->enum_labels\.push_back\(\s*L?\(?\s*\"([^\"]+)\"", block)
    if not vals:
        # newer Orca uses set_enum<>({{"key","Label"},...})
        pairs = re.findall(r'\{\s*"([^"]+)"\s*,\s*L?\(?\s*"([^"]*)"', block)
        if pairs:
            return [v for v, _ in pairs], [l for _, l in pairs]
    return vals, labels


def parse_printconfig():
    src = CPP.read_text(encoding="utf-8", errors="replace")
    adds = list(re.finditer(r'this->add\(\s*"([^"]+)"\s*,\s*(co\w+)', src))
    defs = {}
    for i, m in enumerate(adds):
        key = m.group(1)
        ctype = m.group(2)
        start = m.start()
        end = adds[i + 1].start() if i + 1 < len(adds) else len(src)
        block = src[start:end]
        vals, labels = parse_enums(block)
        defs[key] = {
            "label": parse_field(block, "label"),
            "category": parse_field(block, "category"),
            "tooltip": parse_field(block, "tooltip"),
            "units": parse_field(block, "sidetext"),
            "min": parse_scalar(block, "min"),
            "max": parse_scalar(block, "max"),
            "ctype": ctype,
            "enum_values": vals or None,
            "enum_labels": labels or None,
        }
    return defs


def main():
    defs = parse_printconfig()
    index = json.loads(INDEX.read_text())

    enriched = {}
    matched = 0
    total = 0
    for cat, keys in index.items():
        enriched[cat] = {}
        for k, info in keys.items():
            total += 1
            d = defs.get(k, {})
            if d.get("tooltip"):
                matched += 1
            enriched[cat][k] = {
                "label": d.get("label"),
                "tooltip": d.get("tooltip"),
                "units": d.get("units"),
                "ui_category": d.get("category"),
                "min": d.get("min"),
                "max": d.get("max"),
                "type": info.get("type"),
                "enum_values": info.get("enum_values") or d.get("enum_values"),
                "value_examples": info.get("value_examples"),
                "n_presets": info.get("n_presets"),
            }

    (HERE / "orca_settings_enriched.json").write_text(
        json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8"
    )

    # Rewrite the human dictionary with real descriptions.
    lines = [
        "# OrcaSlicer Settings Dictionary",
        "",
        f"{matched}/{total} settings auto-documented from OrcaSlicer "
        f"PrintConfig.cpp (label/tooltip/units/range). "
        "`interacts_with` / `when_to_change` remain for the tuning research pass.",
        "",
    ]
    for cat in ("process", "machine", "filament"):
        keys = enriched.get(cat, {})
        lines.append(f"## {cat.upper()} — {len(keys)} settings")
        lines.append("")
        lines.append(
            "| setting | label | type | units | range | description | when_to_change |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for k in sorted(keys):
            e = keys[k]
            rng = ""
            if e["min"] is not None or e["max"] is not None:
                rng = f"{e['min'] or ''}–{e['max'] or ''}"
            elif e["enum_values"]:
                rng = ", ".join(e["enum_values"][:6])
            tip = (e["tooltip"] or "").replace("|", "\\|")
            if len(tip) > 160:
                tip = tip[:157] + "..."
            label = (e["label"] or "").replace("|", "\\|")
            units = (e["units"] or "").replace("|", "\\|")
            lines.append(
                f"| `{k}` | {label} | {e['type']} | {units} | {rng} | {tip} | |"
            )
        lines.append("")
    (HERE / "orca_settings_dictionary.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    print(f"parsed PrintConfig.cpp options : {len(defs)}")
    print(f"dictionary keys                : {total}")
    print(f"auto-documented (have tooltip) : {matched}  ({100 * matched / total:.0f}%)")
    print("wrote: orca_settings_enriched.json + orca_settings_dictionary.md")


if __name__ == "__main__":
    main()
