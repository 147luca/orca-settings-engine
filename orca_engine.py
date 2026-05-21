#!/usr/bin/env python3
"""
OrcaSlicer Settings Engine — v0 (enumerate + dictionary scaffold)

Goal of v0: programmatically discover EVERY editable OrcaSlicer setting (no GUI,
no screen control), resolve preset `inherits` chains, classify each setting by
category (process / machine / filament), infer its type, and emit:

  1. orca_all_settings.json  — machine-readable index of every setting key:
        { category: { key: {type, value_examples, n_presets, enum_values?} } }
  2. orca_settings_dictionary.md — human-readable scaffold, one row per setting,
        with TODO columns (description / range / interacts_with / when_to_change)
        to be filled by the later "research once -> cache" step.

The settings dictionary is the build-once knowledge asset the rest of the
pipeline (decide -> write profile -> headless slice -> verify) depends on.

Pure stdlib. Read-only against OrcaSlicer's data; writes only into this dir.
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HOME = Path.home()
DATADIR = HOME / "Library/Application Support/OrcaSlicer"
APP_PROFILES = Path("/Applications/OrcaSlicer.app/Contents/Resources/profiles")
OUT_DIR = Path(__file__).resolve().parent

# Keys that are metadata, not user-editable print settings — excluded from the schema.
META_KEYS = {
    "name",
    "from",
    "inherits",
    "version",
    "type",
    "is_custom_defined",
    "instantiation",
    "setting_id",
    "filament_id",
    "print_settings_id",
    "printer_settings_id",
    "filament_settings_id",
    "url",
    "filament_settings_path",
}

# Vendor index files (not settings presets) contain these top-level arrays.
INDEX_MARKERS = {
    "machine_model_list",
    "machine_list",
    "process_list",
    "filament_list",
    "machine_variant_list",
}

# Category fingerprints: a setting key strongly implies its category.
PROCESS_HINTS = {
    "layer_height",
    "sparse_infill_density",
    "wall_loops",
    "outer_wall_speed",
    "brim_type",
    "support_type",
}
MACHINE_HINTS = {
    "printable_area",
    "nozzle_diameter",
    "printable_height",
    "machine_max_acceleration_x",
    "gcode_flavor",
    "printer_model",
}
FILAMENT_HINTS = {
    "filament_type",
    "nozzle_temperature",
    "filament_flow_ratio",
    "fan_max_speed",
    "filament_vendor",
    "filament_cost",
}


def load_json(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def is_settings_preset(d):
    if not isinstance(d, dict):
        return False
    if INDEX_MARKERS & d.keys():
        return False
    # A real preset has setting keys beyond pure metadata.
    return len(set(d.keys()) - META_KEYS) >= 3


def gather_presets():
    """Return list of (path, dict) for every settings preset, and a name->dict index."""
    presets = []
    roots = [
        APP_PROFILES,
        DATADIR / "user",
        DATADIR / "system",
        DATADIR / "printers",
    ]
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.json"):
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            d = load_json(p)
            if is_settings_preset(d):
                presets.append((p, d))
    name_index = {}
    for _, d in presets:
        nm = d.get("name")
        if nm and nm not in name_index:
            name_index[nm] = d
    return presets, name_index


def resolve(d, name_index, _seen=None):
    """Merge a preset over its inherits chain -> fully resolved settings dict."""
    if _seen is None:
        _seen = set()
    parent_name = d.get("inherits")
    if parent_name and parent_name in name_index and parent_name not in _seen:
        _seen.add(parent_name)
        base = resolve(name_index[parent_name], name_index, _seen)
        merged = dict(base)
        merged.update(d)
        return merged
    return dict(d)


def categorize_preset(raw, resolved):
    """Use the authoritative `type` field; fall back to key-hints for the
    handful of type-less presets. Returns a category or None to skip."""
    t = raw.get("type")
    if t == "process":
        return "process"
    if t == "machine":
        return "machine"
    if t == "filament":
        return "filament"
    if t == "machine_model":
        return None  # printer hardware descriptor, not editable slicing settings
    # type-less: fall back to key fingerprints
    keys = set(resolved.keys())
    if keys & MACHINE_HINTS:
        return "machine"
    if keys & FILAMENT_HINTS:
        return "filament"
    if keys & PROCESS_HINTS:
        return "process"
    return None


def infer_type(values):
    """Infer a setting's type from observed (stringified) values."""
    flat = []
    for v in values:
        if isinstance(v, list):
            flat.extend(v)
        else:
            flat.append(v)
    flat = [str(x) for x in flat if x is not None and str(x) != ""]
    if not flat:
        return "unknown"
    uniq = set(flat)
    if uniq <= {"0", "1", "true", "false", "True", "False"}:
        return "bool"
    if all(re.fullmatch(r"-?\d+%", x) for x in flat):
        return "percent"
    if all(re.fullmatch(r"-?\d+", x) for x in flat):
        return "int"
    if all(re.fullmatch(r"-?\d*\.?\d+%?", x) for x in flat):
        return "float"
    # small finite set of words -> enum
    if len(uniq) <= 12 and all(re.fullmatch(r"[A-Za-z0-9_\- ]+", x) for x in flat):
        return "enum"
    return "string"


def main():
    if not APP_PROFILES.exists() and not DATADIR.exists():
        sys.exit("OrcaSlicer not found — checked app bundle and Application Support.")

    presets, name_index = gather_presets()

    # category -> key -> set of observed values
    cat_keys = defaultdict(lambda: defaultdict(list))
    cat_preset_count = defaultdict(int)

    for _, d in presets:
        resolved = resolve(d, name_index)
        cat = categorize_preset(d, resolved)
        if cat is None:
            continue
        cat_preset_count[cat] += 1
        for k, v in resolved.items():
            if k in META_KEYS:
                continue
            cat_keys[cat][k].append(v)

    # Build the machine-readable index.
    index = {}
    for cat, keys in cat_keys.items():
        index[cat] = {}
        for k, vals in keys.items():
            t = infer_type(vals)
            # collect a few example values + enum domain
            flat = []
            for v in vals:
                flat.extend(v if isinstance(v, list) else [v])
            flat = [str(x) for x in flat if x is not None and str(x) != ""]
            examples = sorted(set(flat))[:6]
            entry = {"type": t, "value_examples": examples, "n_presets": len(vals)}
            if t == "enum":
                entry["enum_values"] = sorted(set(flat))[:30]
            index[cat][k] = entry

    (OUT_DIR / "orca_all_settings.json").write_text(
        json.dumps(index, indent=2, sort_keys=True), encoding="utf-8"
    )

    # Build the human-readable dictionary scaffold.
    lines = [
        "# OrcaSlicer Settings Dictionary (scaffold)",
        "",
        f"Auto-generated by orca_engine.py from {len(presets)} presets "
        f"(app bundle + your user/system profiles).",
        "",
        "TODO columns (`description`, `range`, `interacts_with`, "
        "`when_to_change`) are filled by the research step — this file is the "
        "build-once knowledge base the tuning engine reads.",
        "",
    ]
    total = 0
    for cat in ("process", "machine", "filament"):
        keys = index.get(cat, {})
        total += len(keys)
        lines.append(
            f"## {cat.upper()} — {len(keys)} settings "
            f"({cat_preset_count.get(cat, 0)} presets scanned)"
        )
        lines.append("")
        lines.append(
            "| setting | type | example values | description | range | interacts_with | when_to_change |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for k in sorted(keys):
            e = keys[k]
            ex = ", ".join(e["value_examples"][:4]).replace("|", "\\|")
            lines.append(f"| `{k}` | {e['type']} | {ex} | | | | |")
        lines.append("")
    (OUT_DIR / "orca_settings_dictionary.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    # Console summary.
    print(f"presets scanned : {len(presets)}")
    for cat in ("process", "machine", "filament"):
        print(
            f"  {cat:8s}: {len(index.get(cat, {})):4d} settings"
            f"  ({cat_preset_count.get(cat, 0)} presets)"
        )
    print(f"TOTAL unique settings: {total}")
    print(f"wrote: {OUT_DIR / 'orca_all_settings.json'}")
    print(f"wrote: {OUT_DIR / 'orca_settings_dictionary.md'}")


if __name__ == "__main__":
    main()
