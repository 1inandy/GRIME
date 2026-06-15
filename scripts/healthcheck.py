#!/usr/bin/env python3
"""
GRIME endpoint health check.

Pings every external data source the pipeline depends on and prints UP/DOWN, so
dead endpoints (e.g. EPA EJSCREEN, decommissioned 2025-02-05 — see C4) are visible
before a run rather than silently swallowed by `safe_call`. The EJ source is the
Census ACS API (EJSCREEN's demographic index reconstructed from live ACS), which
this checks explicitly — the validate_pipeline notebook's Step 5 historically
tested NWIS/ECHO/Census but not EJ.

Run: python3 scripts/healthcheck.py
Exit code is non-zero only if a *required* source (Census ACS) is down.
"""
import os
import sys

try:
    import requests
except ImportError:
    print("requests not installed; pip install -r requirements.txt")
    sys.exit(1)

KEY = os.getenv("CENSUS_API_KEY", "").strip()
ACS = "https://api.census.gov/data/2022/acs/acs5"

# (label, method, url, params, required, note)
CHECKS = [
    ("USGS NWIS (discharge)", "GET",
     "https://waterservices.usgs.gov/nwis/dv/",
     {"format": "json", "sites": "02086849", "parameterCd": "00060",
      "startDT": "2023-01-01", "endDT": "2023-01-02"}, False, ""),
    ("EPA ECHO (NPDES/water systems)", "GET",
     "https://echo.epa.gov/api/facility-search/facilities",
     {"output": "JSON", "p_st": "NC", "responseset": "1"}, False, ""),
    ("EPA TRI (efservice)", "GET",
     "https://data.epa.gov/efservice/tri_facility/state_abbr/NC/JSON",
     {}, False, ""),
    ("EPA FRS/CERCLIS (Superfund)", "GET",
     "https://ofmpub.epa.gov/frs_public2/frs_rest_services.get_facilities",
     {"state_abbr": "NC", "program_acronym": "CERCLIS", "output": "JSON",
      "pagesize": "1"}, False, ""),
    ("USGS StreamStats", "GET",
     "https://streamstats.usgs.gov/streamstatsservices/watershed.json",
     {"rcode": "NC", "xlocation": "-78.9", "ylocation": "36.0", "crs": "4326",
      "includeparameters": "false", "includefeatures": "false"}, False, ""),
    ("Census ACS (population + EJ source)", "GET", ACS,
     {"get": "B01003_001E", "for": "county:063", "in": "state:37",
      **({"key": KEY} if KEY else {})}, True,
     "" if KEY else "no CENSUS_API_KEY set — ACS now rejects keyless requests"),
    ("Census TIGERweb (block-group geometry)", "GET",
     "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
     "tigerWMS_ACS2022/MapServer/10/query",
     {"where": "STATE=37 AND COUNTY=063", "outFields": "GEOID",
      "f": "json", "returnGeometry": "false", "resultRecordCount": "1"}, False, ""),
    ("EPA EJSCREEN broker (DEAD — expected DOWN, C4)", "GET",
     "https://ejscreen.epa.gov/mapper/ejscreenRESTbroker.aspx",
     {"f": "json"}, False, "decommissioned 2025-02-05; EJ now from Census ACS"),
]


def main():
    print("GRIME endpoint health check\n" + "=" * 52)
    required_down = False
    for label, method, url, params, required, note in CHECKS:
        status = "DOWN"
        try:
            r = requests.request(method, url, params=params, timeout=20)
            ok = r.status_code == 200 and "Missing Key" not in r.text[:300]
            status = "UP  " if ok else f"DOWN ({r.status_code})"
            if not ok and required:
                required_down = True
        except Exception as e:
            status = f"DOWN ({type(e).__name__})"
            if required:
                required_down = True
        tag = " *required*" if required else ""
        extra = f"  — {note}" if note else ""
        print(f"  [{status:>11}] {label}{tag}{extra}")
    print("=" * 52)
    print("EJ index source: Census ACS (5-yr) demographic reconstruction — "
          "EJSCREEN broker is intentionally dead.")
    sys.exit(1 if required_down else 0)


if __name__ == "__main__":
    main()
