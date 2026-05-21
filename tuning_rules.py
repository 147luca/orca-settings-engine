#!/usr/bin/env python3
"""
Tuning rules — map (model feature vector + intent + nozzle) -> setting overrides.

This is the v1 decision engine: a transparent ruleset, not a black box. It
returns both the overrides AND a per-setting rationale (the "explained profile"
— the thing that makes the optimizer trustworthy). Codifies the reasoning used
by hand on the Apex marble machine plus standard intent trade-offs.

Conservative by design: speeds are sane starting points (printer acceleration is
unknown here), and supports are only enabled when geometry genuinely needs them —
supportless-by-design models (high self-support) are respected.

Pure stdlib. OrcaSlicer stores values as strings, so everything is stringified.
"""

INTENTS = ("quality", "balanced", "strength", "speed")


def decide(features, intent="balanced", nozzle=0.4):
    if intent not in INTENTS:
        intent = "balanced"
    ov = features["overhang"]
    flags = features["flags"]
    out, why = {}, {}

    def setv(k, v, reason):
        out[k] = str(v)
        why[k] = reason

    # ---- layer height (capped at 75% of nozzle) ----
    lh = min(
        {"quality": 0.12, "balanced": 0.20, "strength": 0.20, "speed": 0.28}[intent],
        round(0.75 * nozzle, 2),
    )
    setv("layer_height", lh, f"{intent} intent on {nozzle}mm nozzle (<=75% nozzle)")
    setv(
        "initial_layer_print_height",
        min(0.25, round(0.6 * nozzle, 2)),
        "thicker first layer for bed adhesion",
    )

    # ---- walls & shells ----
    setv("wall_generator", "arachne", "variable-width walls handle thin/odd features")
    setv(
        "wall_loops",
        {"quality": 3, "balanced": 2, "strength": 4, "speed": 2}[intent],
        f"{intent}: perimeter count",
    )
    setv(
        "top_shell_layers",
        {"quality": 5, "balanced": 4, "strength": 5, "speed": 3}[intent],
        f"{intent}: top solid layers",
    )
    setv(
        "bottom_shell_layers",
        {"quality": 4, "balanced": 3, "strength": 4, "speed": 3}[intent],
        f"{intent}: bottom solid layers",
    )

    # ---- infill ----
    setv(
        "sparse_infill_density",
        f"{ {'quality': 15, 'balanced': 15, 'strength': 40, 'speed': 10}[intent] }%",
        f"{intent}: infill density",
    )
    setv(
        "sparse_infill_pattern",
        {
            "quality": "gyroid",
            "balanced": "gyroid",
            "strength": "gyroid",
            "speed": "grid",
        }[intent],
        "gyroid = isotropic strength; grid = faster",
    )

    # ---- speeds (conservative starting points; printer accel unknown) ----
    setv(
        "outer_wall_speed",
        {"quality": 50, "balanced": 120, "strength": 100, "speed": 150}[intent],
        f"{intent}: outer wall (visible surface)",
    )
    setv(
        "inner_wall_speed",
        {"quality": 80, "balanced": 150, "strength": 150, "speed": 250}[intent],
        f"{intent}: inner wall (hidden, can be faster)",
    )
    setv(
        "seam_position",
        "aligned" if intent == "quality" else "back",
        "aligned seam looks cleanest" if intent == "quality" else "hide seam at back",
    )

    # ---- overhang / bridge handling (the Apex lesson) ----
    if flags["heavy_overhang"]:
        setv(
            "enable_overhang_speed",
            1,
            f"heavy overhangs ({ov['risky_overhang_cm2']}cm^2): slow them",
        )
        setv("detect_overhang_wall", 1, "treat overhang walls specially")
        setv(
            "overhang_1_4_speed", 0, "mild overhang: no slowdown (0 = keep wall speed)"
        )
        setv("overhang_2_4_speed", 50, "moderate overhang: ease off")
        setv("overhang_3_4_speed", 25, "steep overhang: slow")
        setv("overhang_4_4_speed", 10, "near-flat overhang/bridge edge: very slow")
        setv("bridge_speed", 25, "slow bridges so unsupported spans solidify")
        setv("fan_max_speed", 100, "max part cooling to set overhangs/bridges")
        setv("overhang_fan_speed", 100, "full fan over overhang regions")
    else:
        setv("fan_max_speed", 90, "standard part cooling")

    # ---- supports: only when geometry truly needs them ----
    needs = flags["heavy_overhang"] and (ov["self_supported_pct"] or 0) < 40
    if needs:
        setv(
            "enable_support",
            1,
            f"overhangs are mostly open spans (self-supported only "
            f"{ov['self_supported_pct']}%) -> supports needed",
        )
        setv(
            "support_type", "tree(auto)", "tree supports: less scarring, easier removal"
        )
        setv("support_threshold_angle", 30, "support anything past 30deg from vertical")
    else:
        ss = ov["self_supported_pct"]
        if ss is None:
            reason = "no significant overhangs detected — supports not needed"
        else:
            reason = (
                f"design is self-supporting ({ss}% of steep faces sit on built-in "
                f"structure) — supports would jam internal features"
            )
        setv("enable_support", 0, reason)

    # ---- bed adhesion ----
    if flags["tall_narrow"] or flags["tiny"]:
        reason = (
            "tall & narrow (tip-over risk)"
            if flags["tall_narrow"]
            else "small footprint"
        )
        setv("brim_type", "outer_only", f"brim for adhesion: {reason}")
        setv("brim_width", 5, "5mm brim")
    else:
        setv(
            "brim_type",
            "auto_brim",
            "let slicer add brim only if it detects weak adhesion",
        )

    return out, why


if __name__ == "__main__":
    import json
    import sys

    feats = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.load(sys.stdin)
    intent = sys.argv[2] if len(sys.argv) > 2 else "balanced"
    overrides, rationale = decide(feats, intent)
    print(
        json.dumps(
            {"intent": intent, "overrides": overrides, "rationale": rationale}, indent=2
        )
    )
