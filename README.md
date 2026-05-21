# orca-settings-engine

Automated, **explained** OrcaSlicer print settings. Point it at an STL and it
analyzes the geometry, decides ~22 high-impact settings (with a reason for each),
optionally folds in what real makers discovered, writes a compatible OrcaSlicer
profile, and headless-slices it to verify print time and material.

No GUI automation, no clicking 760 fields — it works directly with OrcaSlicer's
JSON profiles and headless CLI.

## Why
Slicer settings are the hardest part of 3D printing for most people. There are
~760 settings; ~25 carry 90% of print quality. This turns model geometry + intent
into a good profile, and explains every choice.

## Pipeline
```
STL ─► model_analyzer.py ─► tuning_rules.py ─► [community_intel.py] ─► profile.json ─► OrcaSlicer --slice ─► verified
       (geometry features)   (decision engine)   (maker consensus)      (compatible)    (time/weight)
```

## Use
```bash
python3 optimize.py "model.stl" --intent balanced            # quality|strength|speed|balanced
python3 optimize.py "model.stl" --intent strength --community page.txt
python3 service.py                                            # HTTP API on :8799
```

## Components
| file | role |
|---|---|
| `orca_engine.py` | enumerate all ~760 OrcaSlicer settings; resolve preset `inherits` |
| `enrich_dictionary.py` | join OrcaSlicer source docs (label/tooltip/range/enum) onto settings |
| `model_analyzer.py` | STL → geometry feature vector (overhang, self-support, footprint, aspect) |
| `tuning_rules.py` | feature vector + intent → setting overrides + rationale |
| `community_intel.py` | extract maker-discovered settings from a model's page/comments |
| `optimize.py` | orchestrator: analyze → decide → write profile → slice → report |
| `service.py` | minimal HTTP API |

## Validation
On a real marble-machine model the engine produced 11.0h / 233g (balanced) — matching
the designer's "~250g" and makers' "10–15h". Intent moves it: speed 7.9h, strength 15.6h.

## Status
v1 — works end-to-end. Geometry analysis needs trimesh (a Python venv with
trimesh+numpy). Default printer profile is an Elegoo Centauri Carbon 0.4mm.

## License
MIT (this code). OrcaSlicer setting *definitions* are read from OrcaSlicer (AGPL/GPL)
at build time and are not redistributed here.
