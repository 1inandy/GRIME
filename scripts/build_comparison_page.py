#!/usr/bin/env python3
"""
Build comparison/before_after.html — a self-contained dual-map page (opens over
file:// with no server) showing the BEFORE (candidates.geojson) vs AFTER
(candidates_v2.geojson) top-15 Durham sites on two synced Mapbox globes.

Data is inlined into the HTML (file:// can't fetch local JSON), so this reads the
two geojsons and emits a standalone page. The Mapbox token is read at runtime from
the gitignored comparison/token.js.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V1 = json.loads((ROOT / "mock_data/candidates.geojson").read_text())
V2 = json.loads((ROOT / "mock_data/candidates_v2.geojson").read_text())
PROV = V2.get("provenance", {}).get("parameters", {})
OUT = ROOT / "comparison/before_after.html"

# real-vs-fallback status per parameter (from the v2 provenance block)
REAL = {k for k, v in PROV.items() if v.get("kind") == "real"}
FALLBACK = {k for k, v in PROV.items() if v.get("kind") == "fallback"}

# the 11 parameters this pass targeted (constant in v1)
WIRED = ["impervious_pct", "runoff_coeff_C", "usgs_mean_q_cfs", "seasonal_cv",
         "flood_q10_cfs", "channel_width_score", "tri_facility_density",
         "npdes_points", "cso_density", "land_ownership", "bridge_proximity_bonus"]


def slim(fc, keep_rank_max=15):
    """Keep every site (for context) but tag the top-N; carry the fields the
    popups need."""
    feats = []
    for f in fc["features"]:
        p = f["properties"]
        feats.append({
            "lon": f["geometry"]["coordinates"][0],
            "lat": f["geometry"]["coordinates"][1],
            "rank": p.get("rank"),
            "seg": p.get("segment_id"),
            "composite": round(p.get("composite_score", 0), 1),
            "gen": round(p.get("generation_score", 0)),
            "flow": round(p.get("flow_score", 0)),
            "impact": round(p.get("impact_score", 0)),
            "feas": round(p.get("feasibility_score", 0)),
            "top": p.get("rank", 999) <= keep_rank_max,
            "params": {k: p.get(k) for k in WIRED},
        })
    return feats


DATA = {
    "before": slim(V1),
    "after": slim(V2),
    "real": sorted(REAL),
    "fallback": sorted(FALLBACK),
    "wired": WIRED,
    "n_vary_before": 11,
    "n_vary_after": len(V2.get("provenance", {}).get("varying", [])),
}

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GRIME — Durham: real data before vs after</title>
<script src="https://api.mapbox.com/mapbox-gl-js/v3.4.0/mapbox-gl.js"></script>
<link href="https://api.mapbox.com/mapbox-gl-js/v3.4.0/mapbox-gl.css" rel="stylesheet">
<script src="token.js"></script>
<style>
  :root{--bg:#1E1B12;--panel:#2A2518;--bdr:#4A4330;--t:#e8e2d4;--td:#8a7f68;--tb:#f5f0e4;
        --acc:#58B09C;--gen:#f97316;--flow:#3b82f6;--imp:#8b5cf6;--feas:#10b981;
        --mono:'JetBrains Mono',ui-monospace,monospace;--sans:'DM Sans',-apple-system,sans-serif;}
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{height:100%;font-family:var(--sans);background:var(--bg);color:var(--t)}
  #head{padding:12px 20px;background:var(--panel);border-bottom:1px solid var(--bdr);display:flex;
        align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
  #head h1{font-family:var(--mono);font-size:16px;color:var(--tb)}
  #head h1 span{color:var(--acc)}
  #head .meta{font-family:var(--mono);font-size:11px;color:var(--td);line-height:1.5}
  #head .meta b{color:var(--acc)}
  #legend{display:flex;gap:14px;font-size:11px;color:var(--td);align-items:center;flex-wrap:wrap}
  .lg{display:flex;align-items:center;gap:5px}
  .dot{width:11px;height:11px;border-radius:50%;border:2px solid #fff}
  #maps{display:flex;height:calc(100vh - 58px)}
  .col{flex:1;position:relative;min-width:0;border-right:1px solid var(--bdr)}
  .col:last-child{border-right:none}
  .col .map{position:absolute;inset:0}
  .tag{position:absolute;top:10px;left:10px;z-index:2;background:rgba(42,37,24,.94);
       border:1px solid var(--bdr);border-radius:7px;padding:7px 12px;font-family:var(--mono);
       font-size:12px;color:var(--tb);box-shadow:0 6px 22px rgba(0,0,0,.4)}
  .tag small{display:block;color:var(--td);font-size:10px;margin-top:2px}
  .tag.after{border-color:var(--acc)}
  .mapboxgl-popup-content{background:var(--panel)!important;border:1px solid var(--bdr)!important;
       border-radius:9px!important;color:var(--t)!important;font-family:var(--sans)!important;
       padding:11px 13px!important;box-shadow:0 8px 30px rgba(0,0,0,.5)!important;max-width:290px!important}
  .mapboxgl-popup-tip{border-top-color:var(--panel)!important;border-bottom-color:var(--panel)!important}
  .mapboxgl-popup-close-button{color:var(--td)!important;font-size:17px!important}
  .pop-h{font-family:var(--mono);font-weight:700;color:var(--tb);font-size:13px;margin-bottom:2px}
  .pop-sub{font-size:10px;color:var(--td);font-family:var(--mono);margin-bottom:8px}
  .ss{display:grid;grid-template-columns:64px 1fr 30px;gap:5px;align-items:center;margin-bottom:4px;font-size:11px}
  .ss .bar{height:6px;border-radius:3px;background:var(--bg);overflow:hidden}
  .ss .fill{height:100%;border-radius:3px}
  .ss .v{font-family:var(--mono);text-align:right;font-size:10px}
  .pp{margin-top:8px;border-top:1px solid var(--bdr);padding-top:7px;font-size:9.5px;line-height:1.5}
  .pp-row{display:flex;justify-content:space-between;gap:8px}
  .chip{font-family:var(--mono);font-size:8px;padding:1px 4px;border-radius:3px;margin-left:4px;vertical-align:1px}
  .chip.real{background:rgba(88,176,156,.18);color:var(--acc)}
  .chip.fb{background:rgba(245,158,11,.16);color:#f59e0b}
</style>
</head>
<body>
<div id="head">
  <div>
    <h1><span>G</span>RIME — Durham: real data <b style="color:var(--td);font-weight:400">before → after</b></h1>
    <div class="meta">Same 147 sites, same scoring math. Parameters varying per-site:
      <b>__NVB__ → __NVA__</b> of 27 · <b>#1 moved: seg 11 → seg 23</b> · click a marker for detail</div>
  </div>
  <div id="legend">
    <div class="lg"><span class="dot" style="background:#ef4444"></span>low</div>
    <div class="lg"><span class="dot" style="background:#f59e0b"></span>mid</div>
    <div class="lg"><span class="dot" style="background:#10b981"></span>high</div>
    <div class="lg"><span class="dot" style="background:#22d3ee"></span>top</div>
    <div class="lg" style="margin-left:8px">big ring = top-15</div>
  </div>
</div>
<div id="maps">
  <div class="col"><div class="tag">BEFORE<small>candidates.geojson · 11 real params</small></div><div id="mapL" class="map"></div></div>
  <div class="col"><div class="tag after">AFTER<small>candidates_v2.geojson · 21 real params</small></div><div id="mapR" class="map"></div></div>
</div>
<script>
const DATA = __DATA__;
mapboxgl.accessToken = window.MAPBOX_TOKEN || "";
if(!mapboxgl.accessToken){document.body.insertAdjacentHTML('afterbegin',
  '<div style="position:fixed;z-index:9;top:60px;left:50%;transform:translateX(-50%);background:#2A2518;border:1px solid #4A4330;padding:12px 18px;border-radius:8px;font-family:monospace;font-size:12px">Mapbox token missing — set it in comparison/token.js</div>');}

const CENTER=[-78.905,35.995], ZOOM=11.4;
function mkMap(id){return new mapboxgl.Map({container:id,style:'mapbox://styles/mapbox/dark-v11',
  center:CENTER,zoom:ZOOM,pitch:0,attributionControl:false});}
const L=mkMap('mapL'), R=mkMap('mapR');

// ── two-way sync (guard against feedback loop) ──
let syncing=false;
function link(a,b){a.on('move',()=>{if(syncing)return;syncing=true;
  b.jumpTo({center:a.getCenter(),zoom:a.getZoom(),bearing:a.getBearing(),pitch:a.getPitch()});
  syncing=false;});}
link(L,R); link(R,L);

const colorFor=c=>c>=52?'#22d3ee':c>=45?'#10b981':c>=35?'#f59e0b':'#ef4444';
function geo(feats){return{type:'FeatureCollection',features:feats.map(s=>({type:'Feature',
  geometry:{type:'Point',coordinates:[s.lon,s.lat]},properties:{...s,color:colorFor(s.composite),
  radius:s.top?9:4.5, params:JSON.stringify(s.params)}}))};}

const REAL=new Set(DATA.real), WIRED=DATA.wired;
const LABELS={impervious_pct:'Impervious %',runoff_coeff_C:'Runoff C',usgs_mean_q_cfs:'Mean Q (cfs)',
  seasonal_cv:'Seasonal CV',flood_q10_cfs:'Flood Q10 (cfs)',channel_width_score:'Width score',
  tri_facility_density:'TRI /km²',npdes_points:'NPDES pts',cso_density:'CSO density',
  land_ownership:'Land owner',bridge_proximity_bonus:'Bridge bonus'};

function popup(side, s){
  const p=typeof s.params==='string'?JSON.parse(s.params):s.params;
  const bar=(l,v,c)=>`<div class="ss"><span style="color:${c}">${l}</span><div class="bar"><div class="fill" style="width:${Math.max(0,Math.min(100,v))}%;background:${c}"></div></div><span class="v">${v}</span></div>`;
  let rows='';
  for(const k of WIRED){
    const isReal = side==='after' && REAL.has(k);
    const chip = side==='after'
      ? (isReal?'<span class="chip real">REAL</span>':'<span class="chip fb">FALLBACK</span>')
      : '<span class="chip fb">CONST</span>';
    let val=p[k]; if(typeof val==='number') val=Math.abs(val)>=100?val.toFixed(0):val.toFixed(2);
    rows+=`<div class="pp-row"><span>${LABELS[k]}${chip}</span><span style="font-family:var(--mono)">${val}</span></div>`;
  }
  return `<div class="pop-h">#${s.rank} · segment ${s.seg}</div>
    <div class="pop-sub">composite ${s.composite} · ${s.lat.toFixed(4)}, ${s.lon.toFixed(4)}</div>
    ${bar('Generation',s.gen,'#f97316')}${bar('Flow',s.flow,'#3b82f6')}${bar('Impact',s.impact,'#8b5cf6')}${bar('Feasibility',s.feas,'#10b981')}
    <div class="pp"><div style="color:var(--td);margin-bottom:4px">${side==='after'?'Wired parameters (real vs fallback):':'Wired parameters (all constant in v1):'}</div>${rows}</div>`;
}

function addLayer(map, feats, side){
  map.addSource('sites',{type:'geojson',data:geo(feats)});
  map.addLayer({id:'glow',type:'circle',source:'sites',filter:['==',['get','top'],true],
    paint:{'circle-radius':['*',['get','radius'],2],'circle-color':['get','color'],'circle-opacity':.18,'circle-blur':1}});
  map.addLayer({id:'sites',type:'circle',source:'sites',
    paint:{'circle-radius':['get','radius'],'circle-color':['get','color'],
      'circle-stroke-width':['case',['==',['get','top'],true],2.5,1],'circle-stroke-color':'#fff'}});
  map.addLayer({id:'labels',type:'symbol',source:'sites',filter:['==',['get','top'],true],
    layout:{'text-field':['get','rank'],'text-size':10,'text-font':['DIN Pro Bold','Arial Unicode MS Bold'],'text-offset':[0,0]},
    paint:{'text-color':'#0b0b0b'}});
  let pop=null;
  map.on('click','sites',e=>{const s=e.features[0].properties;
    if(pop)pop.remove();
    pop=new mapboxgl.Popup({offset:12}).setLngLat(e.lngLat).setHTML(popup(side,s)).addTo(map);});
  map.on('mouseenter','sites',()=>map.getCanvas().style.cursor='pointer');
  map.on('mouseleave','sites',()=>map.getCanvas().style.cursor='');
}
L.on('load',()=>addLayer(L,DATA.before,'before'));
R.on('load',()=>addLayer(R,DATA.after,'after'));
</script>
</body>
</html>
"""

html = (HTML
        .replace("__DATA__", json.dumps(DATA))
        .replace("__NVB__", str(DATA["n_vary_before"]))
        .replace("__NVA__", str(DATA["n_vary_after"])))
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(html)
print(f"Wrote {OUT} ({len(html)//1024} KB) — open it directly in a browser (file://).")
print(f"  before top-15 + after top-15 highlighted; {len(DATA['before'])} sites each side.")
