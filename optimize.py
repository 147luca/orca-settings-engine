#!/usr/bin/env python3
"""
optimize.py — end-to-end automated OrcaSlicer settings.

  STL --> analyze geometry --> tuning rules --> write compatible profile
      --> headless slice --> report (settings + rationale + slice result)

This is the v1 the whole engine was building toward. Run with SYSTEM python
(needs orca_engine + tuning_rules); model analysis is delegated to the
3d-pipeline venv (trimesh).

  python3 optimize.py model.stl --intent balanced --printer "Elegoo Centauri Carbon 0.4 nozzle"

Honest about limits: the compatible-triple selection is heuristic (v1). If the
headless slice fails, the generated profile and the reason are still reported.
"""

import argparse
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orca_engine as E  # noqa: E402
from community_intel import extract_intel, to_optimizer_hints  # noqa: E402,F401
from tuning_rules import decide  # noqa: E402

HERE = Path(__file__).resolve().parent
VENV_PY = os.path.expanduser("~/3d-pipeline/.venv/bin/python3")
BIN = "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"
DD = os.path.expanduser("~/Library/Application Support/OrcaSlicer")
VALID_KEYS = {
    k
    for cat in json.loads((HERE / "orca_all_settings.json").read_text()).values()
    for k in cat
}


def first(seq, *preds):
    """Return the first item matching the highest-priority predicate that hits."""
    for pred in preds:
        for x in seq:
            if pred(x):
                return x
    return seq[0] if seq else None


def flatten(d, idx, ptype, machine_name=None):
    r = E.resolve(d, idx)
    r.pop("inherits", None)
    r.pop("compatible_printers_condition", None)
    r["type"] = ptype
    r["from"] = "system"
    if machine_name and ptype in ("process", "filament"):
        r["compatible_printers"] = [machine_name]
    return r


def nozzle_of(mach):
    nd = mach.get("nozzle_diameter")
    if isinstance(nd, list):
        nd = nd[0]
    try:
        return float(nd)
    except (TypeError, ValueError):
        return 0.4


def bed_size(mach):
    """(x, y, z) build volume from the machine preset, or None if unparseable."""
    pa = mach.get("printable_area")
    ph = mach.get("printable_height")
    try:
        xs, ys = [], []
        for pt in pa:
            s = str(pt).replace(
                ",", "x"
            )  # OrcaSlicer uses "257x0"; tolerate commas too
            x, y = s.split("x")
            xs.append(float(x))
            ys.append(float(y))
        h = float(ph[0] if isinstance(ph, list) else ph)
        return (round(max(xs) - min(xs), 1), round(max(ys) - min(ys), 1), h)
    except Exception:
        return None


def filament_type_of(fil):
    ft = fil.get("filament_type")
    ft = ft[0] if isinstance(ft, list) else ft
    return (ft or "PLA").upper()


def parse_3mf(path):
    """Pull predicted time / filament weight from the sliced 3mf if present."""
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith("slice_info.config"):
                    txt = z.read(name).decode("utf-8", "replace")
                    out = {}
                    for key in ("prediction", "weight", "filament"):
                        for line in txt.splitlines():
                            if f'key="{key}"' in line:
                                v = line.split('value="')[1].split('"')[0]
                                out[key] = v
                                break
                    return out
    except Exception as e:
        return {"parse_error": str(e)}
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--intent", default="balanced")
    ap.add_argument("--printer", default="Elegoo Centauri Carbon 0.4 nozzle")
    ap.add_argument("--no-slice", action="store_true")
    ap.add_argument(
        "--community",
        help="path to a saved Printables/MMF page text to mine for maker-discovered settings",
    )
    args = ap.parse_args()

    if not Path(BIN).exists():
        sys.exit(f"OrcaSlicer binary not found: {BIN}")
    if not Path(VENV_PY).exists():
        sys.exit(f"analyzer python (trimesh venv) not found: {VENV_PY}")
    if not Path(args.model).exists():
        sys.exit(f"model not found: {args.model}")

    presets, idx = E.gather_presets()

    # --- machine (prefer exact system preset, avoid user "- Copy") ---
    machines = [d for _, d in presets if d.get("type") == "machine" and d.get("name")]
    mach_def = first(
        machines,
        lambda d: d["name"] == args.printer and d.get("from") != "User",
        lambda d: d["name"] == args.printer,
        lambda d: args.printer in d["name"],
    )
    if not mach_def:
        sys.exit(f"no machine matching {args.printer!r}")
    machine_name = mach_def["name"]
    mach = flatten(mach_def, idx, "machine")
    nozzle = nozzle_of(mach)

    # --- process base compatible with that machine ---
    procs = [d for _, d in presets if d.get("type") == "process" and d.get("name")]

    def proc_compatible(d):
        return machine_name in (E.resolve(d, idx).get("compatible_printers") or [])

    proc_def = first(
        [d for d in procs if proc_compatible(d)],
        lambda d: "0.20" in d["name"] and "Standard" in d["name"],
        lambda d: "0.20" in d["name"],
        lambda d: True,
    ) or first(
        procs, lambda d: "Elegoo" in d["name"] and "0.4" in d["name"], lambda d: True
    )
    base_proc = flatten(proc_def, idx, "process", machine_name)

    # --- filament: a PLA compatible with the machine ---
    fils = [d for _, d in presets if d.get("type") == "filament" and d.get("name")]
    fil_def = first(
        fils,
        lambda d: (
            "PLA" in d["name"]
            and machine_name in (E.resolve(d, idx).get("compatible_printers") or [])
        ),
        lambda d: d["name"].startswith("Generic PLA"),
        lambda d: "PLA" in d["name"],
    )
    if not fil_def:
        sys.exit("no PLA filament preset found for this printer")
    fil = flatten(fil_def, idx, "filament", machine_name)
    fil_type = filament_type_of(fil)

    # --- analyze model (delegate to trimesh venv; handle clean errors) ---
    pr = subprocess.run(
        [VENV_PY, str(HERE / "model_analyzer.py"), args.model],
        capture_output=True,
        text=True,
    )
    try:
        feats = json.loads(pr.stdout)
    except json.JSONDecodeError:
        sys.exit("model analysis failed:\n" + (pr.stderr or pr.stdout)[-500:])
    if "error" in feats:
        sys.exit(f"model analysis: {feats['error']}")

    # --- bed-size guard ---
    warnings = []
    bed = bed_size(mach)
    dm = feats["dims_mm"]
    if bed and (dm["w"] > bed[0] or dm["d"] > bed[1] or dm["h"] > bed[2]):
        warnings.append(
            f"MODEL EXCEEDS BED {bed[0]}x{bed[1]}x{bed[2]}mm "
            f"(model {dm['w']}x{dm['d']}x{dm['h']}) — scale it down or it will not slice"
        )

    # --- decide + validate against real key schema ---
    overrides, why = decide(feats, args.intent, nozzle)
    # filament-aware cooling: high part fan warps/delaminates ABS/ASA/PC/Nylon.
    if fil_type in ("ABS", "ASA", "PC", "PA", "NYLON", "PA-CF", "ABS-GF", "PC-CF"):
        for k in ("fan_max_speed", "overhang_fan_speed"):
            if k in overrides:
                overrides[k] = "30"
                why[k] = f"{fil_type}: low fan to avoid warping/delamination"
    # --- community intel (the moat): fold maker-discovered settings in ---
    community = None
    if args.community and Path(args.community).exists():
        community = extract_intel(
            Path(args.community).read_text(encoding="utf-8", errors="replace")
        )
        hints = to_optimizer_hints(community)
        if hints.get("force_supports_off"):
            overrides["enable_support"] = "0"
            why["enable_support"] = "COMMUNITY: " + hints["reason_supports"]
        if hints.get("dry_filament_warning"):
            warnings.append("COMMUNITY: " + hints["dry_filament_warning"])

    dropped = [k for k in overrides if k not in VALID_KEYS]
    overrides = {k: v for k, v in overrides.items() if k in VALID_KEYS}
    base_proc.update(overrides)
    base_proc["name"] = f"AUTO {args.intent} ({Path(args.model).stem})"

    # --- write the triple ---
    pf, mf, ff = (
        "/tmp/auto_process.json",
        "/tmp/auto_machine.json",
        "/tmp/auto_filament.json",
    )
    for obj, path in [(base_proc, pf), (mach, mf), (fil, ff)]:
        Path(path).write_text(json.dumps(obj, indent=2))

    # --- report header ---
    print(
        f"\n=== AUTOMATED SETTINGS: {Path(args.model).name} | intent={args.intent} ==="
    )
    print(f"printer : {machine_name}  (nozzle {nozzle}mm, bed {bed})")
    print(f"process : {proc_def['name']}")
    print(f"filament: {fil_def['name']}  ({fil_type})")
    d = feats["dims_mm"]
    o = feats["overhang"]
    print(
        f"model   : {d['w']}x{d['d']}x{d['h']}mm | overhang {o['risky_overhang_cm2']}cm^2, "
        f"self-supported {o['self_supported_pct']}%"
    )
    if community:
        print(
            f"community: support={community['support_verdict']} | "
            f"common layer {community['common_layer_height_mm']}mm | "
            f"top issues {list(community['recurring_issues'])[:3]}"
        )
    for warn in warnings:
        print(f"!! WARNING: {warn}")
    print(f"\n--- {len(overrides)} settings decided ---")
    for k in sorted(overrides):
        print(f"  {k:24s}= {overrides[k]:14s} | {why[k]}")
    if dropped:
        print(f"(dropped {len(dropped)} keys not in schema: {dropped})")

    if args.no_slice:
        print("\n(--no-slice) profile written to /tmp/auto_*.json")
        return
    if warnings:
        print(
            "\nskipping slice — model won't fit the bed as-is (fix scale first). "
            "profile written to /tmp/auto_*.json"
        )
        return

    # --- headless slice ---
    # NOTE: with --outputdir set, --export-3mf must be a RELATIVE name, or
    # OrcaSlicer concatenates outputdir + path into an invalid /tmp//tmp/... path.
    out_name = "auto_sliced.3mf"
    out3mf = "/tmp/" + out_name
    if os.path.exists(out3mf):
        os.remove(out3mf)
    cmd = [
        BIN,
        "--datadir",
        DD,
        "--load-settings",
        f"{pf};{mf}",
        "--load-filaments",
        ff,
        "--slice",
        "0",
        "--export-3mf",
        out_name,
        "--outputdir",
        "/tmp",
        args.model,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-4:])
        print("\n--- slice result ---")
        if os.path.exists(out3mf):
            info = parse_3mf(out3mf)
            print(f"  SUCCESS -> {out3mf}")
            print(f"  predicted: {info}")
        else:
            print(f"  slice did not produce a 3mf (exit {r.returncode}). last output:")
            print("  " + tail.replace("\n", "\n  "))
    except subprocess.TimeoutExpired:
        print("\n--- slice timed out (180s) ---")


if __name__ == "__main__":
    main()
