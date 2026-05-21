#!/usr/bin/env python3
"""
merge_tuning.py — fold the parallel agents' tuning research (dict_fragments/*.json)
into the enriched settings dictionary.

Pipeline stage 3: orca_engine (enumerate) -> enrich_dictionary (source docs) ->
merge_tuning (the judgement layer: interacts_with / when_to_change / failure_if_wrong).

Reads:  orca_settings_enriched.json + dict_fragments/*.json
Writes: orca_settings_enriched.json (augmented) + orca_settings_dictionary.md (tuning cols)
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENRICHED = HERE / "orca_settings_enriched.json"
FRAGMENTS = HERE / "dict_fragments"

TUNING_FIELDS = (
    "interacts_with",
    "when_to_change",
    "failure_if_wrong",
    "typical_range",
)


def main():
    enriched = json.loads(ENRICHED.read_text())

    # gather all fragment entries (last-writer-wins on dup keys)
    tuning = {}
    for f in sorted(FRAGMENTS.glob("*.json")):
        try:
            tuning.update(json.loads(f.read_text()))
        except Exception as e:
            print(f"  skip {f.name}: {e}")

    merged = 0
    for cat in enriched.values():
        for key, entry in cat.items():
            t = tuning.get(key)
            if not t:
                continue
            for fld in TUNING_FIELDS:
                if t.get(fld):
                    entry[fld] = t[fld]
            merged += 1

    ENRICHED.write_text(json.dumps(enriched, indent=2, sort_keys=True))

    # regenerate the human dictionary with the tuning columns
    total = sum(len(c) for c in enriched.values())
    documented = sum(
        1 for c in enriched.values() for e in c.values() if e.get("tooltip")
    )
    tuned = sum(
        1 for c in enriched.values() for e in c.values() if e.get("when_to_change")
    )
    lines = [
        "# OrcaSlicer Settings Dictionary",
        "",
        f"{documented}/{total} source-documented · {tuned}/{total} have tuning judgement "
        "(interacts_with / when_to_change / failure_if_wrong).",
        "",
    ]
    for cat in ("process", "machine", "filament"):
        keys = enriched.get(cat, {})
        lines += [
            f"## {cat.upper()} — {len(keys)} settings",
            "",
            "| setting | label | type | units | range | description | when_to_change | interacts_with | failure_if_wrong |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for k in sorted(keys):
            e = keys[k]
            rng = ""
            if e.get("min") is not None or e.get("max") is not None:
                rng = f"{e.get('min') or ''}–{e.get('max') or ''}"
            elif e.get("enum_values"):
                rng = ", ".join(e["enum_values"][:6])

            def cell(v, n=150):
                s = ", ".join(v) if isinstance(v, list) else (v or "")
                s = str(s).replace("|", "\\|")
                return s[:n] + ("..." if len(s) > n else "")

            lines.append(
                f"| `{k}` | {cell(e.get('label'), 40)} | {e.get('type')} | "
                f"{cell(e.get('units'), 12)} | {rng} | {cell(e.get('tooltip'))} | "
                f"{cell(e.get('when_to_change'))} | {cell(e.get('interacts_with'), 80)} | "
                f"{cell(e.get('failure_if_wrong'))} |"
            )
        lines.append("")
    (HERE / "orca_settings_dictionary.md").write_text("\n".join(lines))

    print(f"merged tuning into {merged} settings")
    print(
        f"dictionary: {documented}/{total} documented, {tuned}/{total} with tuning judgement"
    )


if __name__ == "__main__":
    main()
