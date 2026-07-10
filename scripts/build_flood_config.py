#!/usr/bin/env python3
"""
GRIME — stamp SIR 2014-5030 HR4 flood config into scripts/regions.json
(fix-pass-2 Phase 2, P1.5).

For every NC Coastal Plain region (notes contain "NC Coastal Plain"), fetch
the NOAA Atlas 14 24-hour/50-year mean precipitation depth at the region
center (the HR4 regression's I24H50Y covariate), set
flood_method = "sir2014_hr4", record "i24h50y_in", and update the note.
Fetches are disk-cached (cache/wire/noaa14) so re-runs are free.

Scope decisions (documented in FIX2_REPORT.md):
- Blue Ridge stays flood_method "none": SIR 2014-5030 states its methods are
  "not applicable to urban streams in the Blue Ridge region (HR2)" — FIX_PLAN_2
  P1.5's premise that the report covers Blue Ridge was wrong; substituting the
  Mason et al. (2002) NC urban equation would be a different dataset (rule 9)
  and is left as a documented roadmap item instead.
- The SIR Sand Hills strip (HR3) is not separable under the DEQ 3-province
  physiography this config was built from; affected towns (Fayetteville /
  Southern Pines area) are treated as Coastal Plain (HR4), noted per region.

Run: python3 scripts/build_flood_config.py           # applies + reports
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.real_sources import noaa_atlas14_24h50y_in

CONFIG = os.path.join(_ROOT, "scripts", "regions.json")

SANDHILLS_SLUGS = {
    # towns inside/straddling the SIR fig. 2 Sand Hills strip, kept on HR4
    # under the DEQ 3-province classification (see module docstring)
    "fayetteville", "spring-lake", "hope-mills", "pinehurst", "laurinburg",
}


def main():
    with open(CONFIG) as f:
        doc = json.load(f)
    regions = doc["regions"] if isinstance(doc, dict) and "regions" in doc else doc

    updated, failed = 0, []
    for r in regions:
        if "NC Coastal Plain" not in r.get("notes", ""):
            continue
        lat, lon = r["center"][1], r["center"][0]
        depth = noaa_atlas14_24h50y_in(lat, lon)
        if depth is None:
            failed.append(r["slug"])
            continue
        r["flood_method"] = "sir2014_hr4"
        r["i24h50y_in"] = round(float(depth), 2)
        base = "NC Coastal Plain (SIR 2014-5030 HR4; I24H50Y from NOAA Atlas 14 at region center)"
        if r["slug"] in SANDHILLS_SLUGS:
            base += ("; straddles the SIR Sand Hills strip (HR3 not separable "
                     "under DEQ 3-province physiography — HR4 applied, noted)")
        # keep any "; merged nearby towns: ..." suffix from the original note
        old = r.get("notes", "")
        suffix = old[old.index("; merged nearby towns:"):] if "; merged nearby towns:" in old else ""
        r["notes"] = base + suffix
        updated += 1
        print(f"  {r['slug']:24s} I24H50Y = {r['i24h50y_in']:.2f} in")

    with open(CONFIG, "w") as f:
        f.write(json.dumps(doc, indent=1) + "\n")
    print(f"\nupdated {updated} coastal regions; {len(failed)} fetch failures: {failed}")
    if failed:
        print("failed regions keep flood_method 'none' (documented fallback)")


if __name__ == "__main__":
    main()
