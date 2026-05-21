#!/usr/bin/env python3
"""
Community intel layer — the moat.

Extracts what real makers discovered from a model's Printables/MMF page +
comments: layer heights that worked, support consensus, materials, scale/ball
sizes, print times, and recurring failure modes. Feeds the optimizer hints no
competitor has ("what 130 people who printed this actually learned").

Two modes:
  * parse a saved page-text dump (built/tested on the Apex comment dump)
  * (live fetch is driven via the Playwright MCP, which bypasses Cloudflare;
     the page text is handed to extract_intel())

  python3 community_intel.py "page_text.md"  ->  JSON intel + consensus
"""

import json
import re
import sys
from collections import Counter


def extract_intel(text):
    t = text
    low = t.lower()

    layer_heights = [float(m) for m in re.findall(r"(0?\.\d{2})\s*mm", low)]
    scales = [int(x) for x in re.findall(r"(\d{2,3})\s*%", t)]
    ball_sizes = [
        float(x)
        for x in re.findall(
            r"(\d{1,2}(?:\.\d)?)\s*mm\s*(?:steel\s*)?(?:ball|bb|marble|bearing)", low
        )
    ]
    times = [int(x) for x in re.findall(r"(\d{1,2})\s*(?:h\b|hr|hour)", low)]

    # support consensus
    no_support = len(
        re.findall(
            r"(no support|without support|don'?t.*support|no extra support|prints?.*no support)",
            low,
        )
    )
    used_support = len(
        re.findall(
            r"(added support|used support|with support|needed support|unneeded support|trusted.*slicer)",
            low,
        )
    )

    # materials
    mats = Counter()
    for m in ["petg", "pla+", "pla", "abs", "asa", "silk", "tpu"]:
        c = len(re.findall(rf"\b{re.escape(m)}\b", low))
        if c:
            mats[m.upper()] += c

    # recurring failure modes / advice
    issues = {}
    for label, pat in {
        "stringing": r"string|wisp|wispy",
        "bed_adhesion/tipping": r"adhesion|tipp|slid|brim",
        "ball_fit_problems": r"too big|didn'?t fit|wouldn'?t fit|right size|drill",
        "support_removal_pain": r"remove.*support|drill.*arch|unneeded support",
    }.items():
        n = len(re.findall(pat, low))
        if n:
            issues[label] = n

    def topcount(seq, k=5):
        return [v for v, _ in Counter(seq).most_common(k)]

    consensus = {
        "recommended_layer_heights_mm": sorted(
            set(h for h in layer_heights if 0.08 <= h <= 0.32)
        ),
        "common_layer_height_mm": (
            Counter([h for h in layer_heights if 0.08 <= h <= 0.32]).most_common(1)
            or [(None,)]
        )[0][0],
        "support_verdict": (
            "NO supports (community consensus)"
            if no_support >= used_support and no_support
            else "supports debated"
            if used_support
            else "unclear"
        ),
        "support_mentions": {"no_support": no_support, "used_support": used_support},
        "materials_used": dict(mats.most_common()),
        "ball_sizes_mm": sorted(set(b for b in ball_sizes if 3 <= b <= 25)),
        "scale_factors_pct": sorted(set(s for s in scales if 40 <= s <= 300)),
        "print_time_hours_range": [min(times), max(times)] if times else None,
        "recurring_issues": dict(sorted(issues.items(), key=lambda x: -x[1])),
    }
    return consensus


def to_optimizer_hints(consensus):
    """Translate community consensus into optimizer-actionable hints."""
    hints = {}
    if "NO supports" in (consensus.get("support_verdict") or ""):
        hints["force_supports_off"] = True
        hints["reason_supports"] = (
            "community consensus: prints cleanly without supports"
        )
    if consensus.get("common_layer_height_mm"):
        hints["suggested_layer_height"] = consensus["common_layer_height_mm"]
    if consensus.get("recurring_issues", {}).get("stringing"):
        hints["dry_filament_warning"] = (
            "stringing is the top reported failure — dry filament, tune retraction"
        )
    return hints


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: community_intel.py page_text.(md|txt)")
    text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    consensus = extract_intel(text)
    out = {"consensus": consensus, "optimizer_hints": to_optimizer_hints(consensus)}
    print(json.dumps(out, indent=2))
