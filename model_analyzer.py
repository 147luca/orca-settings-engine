#!/usr/bin/env python3
"""
Model analyzer — turn an STL into a feature vector the tuning engine reasons over.

Pure geometry, no slicer needed. Run with the 3d-pipeline venv (trimesh+numpy):
  ~/3d-pipeline/.venv/bin/python3 model_analyzer.py model.stl

Emits a JSON feature vector. On any load problem it emits {"error": "..."} (exit 2)
rather than crashing, so the orchestrator can report cleanly.

Key correctness rule: a downward face resting ON the build plate (near z-min) is
NOT an overhang — only elevated downward faces are. Without this, a plain cube or
flat plate falsely reads as "heavy overhang" (its flat bottom).
"""

import json
import sys

import numpy as np
import trimesh


def analyze(stl_path, sample=1500, support_radius=1.0):
    try:
        m = trimesh.load(stl_path, force="mesh")
    except Exception as e:
        return {"error": f"could not load mesh: {e}"}
    if m is None or not hasattr(m, "faces") or len(m.faces) == 0:
        return {"error": "no triangles found (corrupt, empty, or not a mesh)"}

    ext = m.bounding_box.extents
    w, d, h = (float(ext[0]), float(ext[1]), float(ext[2]))
    if max(w, d, h) <= 0:
        return {"error": "degenerate mesh (zero bounding box)"}
    footprint = w * d
    aspect = h / max(min(w, d), 1e-6)

    n = m.face_normals
    nz = n[:, 2]
    areas = m.area_faces
    cz = m.triangles_center[:, 2]
    zmin = float(m.bounds[0][2])
    plate_eps = max(
        0.5, 0.02 * h
    )  # faces within this of the plate are "resting", not overhangs
    elevated = cz > (zmin + plate_eps)
    down = (nz < -1e-6) & elevated  # downward AND off the plate

    sev = np.zeros(len(n))
    sev[down] = 90.0 - np.degrees(np.arccos(np.clip(-nz[down], -1, 1)))
    down_area = float(areas[down].sum())

    def band(lo, hi):
        sel = down & (sev >= lo) & (sev < hi)
        return float(areas[sel].sum())

    fine, moderate = band(0, 45), band(45, 60)
    steep, nearflat = band(60, 80), band(80, 90.01)
    overhang_area = steep + nearflat

    # Self-support: ray down from steep-face centroids — model geometry below
    # (built-in pillar/wall) vs open span (bridge / true overhang)?
    steep_mask = down & (sev >= 60)
    idx = np.where(steep_mask)[0]
    self_supported_pct = None
    if len(idx):
        rng = np.random.default_rng(0)
        if len(idx) > sample:
            idx = rng.choice(idx, sample, replace=False)
        V = m.vertices
        if len(V) > 200_000:  # perf cap: subsample the occupancy cloud for huge meshes
            V = V[rng.choice(len(V), 200_000, replace=False)]
        cent = m.triangles_center[idx]
        sup = 0
        for c in cent:
            if (
                (np.abs(V[:, 0] - c[0]) < support_radius)
                & (np.abs(V[:, 1] - c[1]) < support_radius)
                & (V[:, 2] < c[2] - 0.3)
            ).any():
                sup += 1
        self_supported_pct = round(100 * sup / len(idx), 1)
    open_span_pct = (
        round(100 - self_supported_pct, 1) if self_supported_pct is not None else None
    )

    try:
        bodies = int(m.body_count)  # may pull scipy; optional
    except Exception:
        bodies = 1

    return {
        "file": stl_path,
        "dims_mm": {"w": round(w, 1), "d": round(d, 1), "h": round(h, 1)},
        "footprint_mm2": round(footprint, 1),
        "aspect_ratio": round(aspect, 2),
        "watertight": bool(m.is_watertight),
        "triangles": int(len(m.faces)),
        "bodies": bodies,
        "overhang": {
            "down_area_cm2": round(down_area / 100, 1),
            "fine_pct": round(100 * fine / down_area, 1) if down_area else 0,
            "moderate_pct": round(100 * moderate / down_area, 1) if down_area else 0,
            "steep_pct": round(100 * steep / down_area, 1) if down_area else 0,
            "nearflat_pct": round(100 * nearflat / down_area, 1) if down_area else 0,
            "risky_overhang_cm2": round(overhang_area / 100, 1),
            "self_supported_pct": self_supported_pct,
            "open_span_pct": open_span_pct,
        },
        "flags": {
            "tall_narrow": aspect >= 3.0,
            "big_footprint": footprint >= 20000,
            "heavy_overhang": down_area > 1.0
            and (overhang_area / max(down_area, 1)) >= 0.25,
            "mostly_self_supported": (self_supported_pct or 0) >= 60,
            "tiny": max(w, d, h) <= 40,
            "multi_body": bodies > 1,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: model_analyzer.py model.stl")
    result = analyze(sys.argv[1])
    print(json.dumps(result, indent=2))
    sys.exit(2 if "error" in result else 0)
