#!/home/opc/mcp/coros-mcp/.venv/bin/python
"""Genere le tableau de bord velo (index.html) sur velo.luku.fr."""
import asyncio, datetime, json, os, re, subprocess, sys

sys.path.insert(0, "/home/opc/mcp/coros-mcp")
MON = "/home/opc/monitoring"
OUT = "/home/opc/Docker/sites/velo/public"

ftp = 275
for line in open("/home/opc/mcp/secrets/igpsport.env"):
    m = re.match(r"IGPSPORT_FTP=(\d+)", line)
    if m:
        ftp = int(m.group(1))

async def coros():
    for line in open("/home/opc/mcp/secrets/coros.env"):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ[k] = v
    from coros_mcp.coros_api import try_auto_login, fetch_daily_records, fetch_sleep
    auth = await try_auto_login()
    today = datetime.date.today()
    d0 = (today - datetime.timedelta(days=42)).strftime("%Y%m%d")
    d1 = today.strftime("%Y%m%d")
    daily = await fetch_daily_records(auth, d0, d1)
    sleep = await fetch_sleep(auth, d0, d1)
    return daily, sleep

daily, sleep = asyncio.run(coros())

IGP_SNIPPET = r'''
import json, os
for line in open("/home/opc/mcp/secrets/igpsport.env"):
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1)
        os.environ[k] = v
from igpsport_mcp.tools._service import IGPSportService
from igpsport_mcp.config import load_config
svc = IGPSportService(load_config())
acts = svc.list_activities(limit=6)
rows = []
for a in (acts.get("activities") or acts.get("items") or [])[:6]:
    rid = a.get("ride_id") or a.get("id") or a.get("activity_id")
    row = {"date": a.get("date") or a.get("start_time"), "km": a.get("distance_km") or a.get("distance")}
    try:
        s = svc.get_activity_summary(rid)
        for src, dst in [("duration_s","dur"),("moving_time_s","dur"),("avg_power_w","avg"),
                         ("normalized_power_w","np"),("avg_hr_bpm","hr"),("elevation_gain_m","dplus")]:
            v = s.get(src) if isinstance(s, dict) else None
            if v is not None and dst not in row:
                row[dst] = v
        if isinstance(s, dict):
            for k2 in s:
                if "duration" in k2 and "dur" not in row and isinstance(s[k2], (int, float)):
                    row["dur"] = s[k2]
    except Exception:
        pass
    rows.append(row)
print(json.dumps(rows))
'''
acts = []
try:
    r = subprocess.run(["/home/opc/mcp/igpsport-mcp/.venv/bin/python", "-c", IGP_SNIPPET],
                       capture_output=True, text=True, timeout=120)
    acts = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else []
except Exception:
    acts = []

verdicts = {}
vf = f"{MON}/coach-velo-verdicts.tsv"
if os.path.exists(vf):
    for line in open(vf):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 2:
            verdicts[p[0]] = {"emoji": p[1], "note": p[2] if len(p) > 2 else ""}

plan = []
for line in open(f"{MON}/coach-velo-plan.tsv"):
    p = line.rstrip("\n").split("\t")
    if len(p) >= 3:
        v = verdicts.get(p[0], {})
        plan.append({"date": p[0], "seance": p[1], "phase": p[2],
                     "emoji": v.get("emoji", ""), "note": v.get("note", "")})

today_iso = datetime.date.today().isoformat()
did_today = any(str(a.get("date", ""))[:10] == today_iso for a in acts)
nxt = next((s for s in plan if s["date"] > today_iso or (s["date"] == today_iso and not did_today)), None)
done_today = next((s["seance"] for s in plan if s["date"] == today_iso), None) if did_today else None
vo2 = next((r.vo2max for r in reversed(daily) if r.vo2max), None)
last = daily[-1] if daily else None
last_sleep = sleep[-1] if sleep else None

wk_acc = {}
for r in daily:
    d_ = datetime.date(int(r.date[:4]), int(r.date[4:6]), int(r.date[6:8]))
    iso = d_.isocalendar()
    key = f"S{iso[1]:02d}"
    wk_acc[key] = wk_acc.get(key, 0) + (r.training_load or 0)
weeks_data = [{"w": k, "load": round(v)} for k, v in sorted(wk_acc.items())]

data = {
    "maj": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
    "ftp": ftp, "vo2max": vo2,
    "hrv": last.avg_sleep_hrv if last else None,
    "hrv_base": last.baseline if last else None,
    "rhr": last.rhr if last else None,
    "tired": last.tired_rate if last else None,
    "ratio": last.training_load_ratio if last else None,
    "sleep_last": last_sleep.total_duration_minutes if last_sleep else None,
    "next": nxt, "done_today": done_today,
    "daily": [{"d": r.date, "hrv": r.avg_sleep_hrv, "base": r.baseline,
               "rhr": r.rhr, "ati": r.ati, "cti": r.cti,
               "ratio": r.training_load_ratio, "load": r.training_load} for r in daily],
    "weeks": weeks_data,
    "sleep": [{"d": r.date, "min": r.total_duration_minutes,
               "deep": r.phases.deep_minutes or 0} for r in sleep],
    "acts": acts, "plan": plan,
}

HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Entrainement — programme 12 semaines</title>
<style>
:root{
  color-scheme:light;
  --page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;
  --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
  --s1:#2a78d6;--s2:#eb6834;--s1-deep:#1c5cab;
  --good:#0ca30c;--warn:#fab219;--serious:#ec835a;--crit:#d03b3b;
}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){
  color-scheme:dark;
  --page:#0d0d0d;--surface:#1a1a19;--ink:#ffffff;--ink-2:#c3c2b7;--muted:#898781;
  --grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
  --s1:#3987e5;--s2:#d95926;--s1-deep:#184f95;
}}
*{box-sizing:border-box;margin:0}
body{background:var(--page);color:var(--ink);
  font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
  padding:24px 20px;max-width:1440px;margin:auto}
header{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:18px}
.topnav{display:flex;gap:4px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:3px}
.topnav a,.topnav .here{font-size:12.5px;padding:5px 11px;border-radius:6px;text-decoration:none;color:var(--ink-2)}
.topnav a:hover{background:color-mix(in srgb,var(--s1) 10%,transparent)}
.topnav .here{background:var(--s1);color:#fff;font-weight:600}
h1{font-size:17px;font-weight:600}
.sub{color:var(--muted);font-size:12px}
h2{font-size:11px;font-weight:600;color:var(--ink-2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;
  transition:transform .25s ease,box-shadow .25s ease,opacity .5s ease}
.reveal{opacity:0;transform:translateY(16px);transition:opacity .55s ease,transform .55s cubic-bezier(.2,.7,.3,1)}
.reveal.in{opacity:1;transform:none}
.tile:hover{transform:translateY(-3px);box-shadow:0 10px 26px rgba(0,0,0,.10)}
.chart-hover:hover{box-shadow:0 8px 24px rgba(0,0,0,.08)}
svg polyline{transition:stroke-dashoffset 1.1s cubic-bezier(.3,.6,.2,1)}
svg .bar rect{transform-origin:bottom;transform:scaleY(0);transition:transform .6s cubic-bezier(.2,.7,.3,1)}
svg .bar rect.grown{transform:scaleY(1)}
svg .bar:hover rect{filter:brightness(1.15)}
tr{transition:background .2s}
#plan tr:hover td,#acts tr:hover td{background:color-mix(in srgb,var(--s1) 5%,transparent)}
@media(prefers-reduced-motion:reduce){
  .reveal{opacity:1;transform:none;transition:none}
  svg polyline{transition:none}
  svg .bar rect{transform:scaleY(1);transition:none}
  .card{transition:none}
}
.tiles{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));margin-bottom:12px}
.tile{border-top:3px solid var(--tint,var(--border))}
.tile .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.tile .v{font-size:23px;font-weight:600;margin-top:2px;font-variant-numeric:tabular-nums}
.tile .v small{font-size:12px;color:var(--muted);font-weight:400}
.tile .st{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:var(--ink-2);margin-top:4px}
.tile .st i{width:7px;height:7px;border-radius:50%;display:inline-block;background:var(--tint,var(--axis))}
.next{display:flex;gap:12px;align-items:flex-start;margin-bottom:12px}
.next .body{flex:1}
.next .t{font-size:15px;font-weight:600}
.next .d{color:var(--ink-2);font-size:13px;margin-top:2px}
.next .when{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px}
.dot{width:10px;height:10px;border-radius:50%;flex:none;margin-top:5px}
.charts{display:grid;gap:10px;grid-template-columns:1fr;margin-bottom:12px}
@media(min-width:780px){.charts{grid-template-columns:1fr 1fr}.card.wide{grid-column:1/-1}}
@media(min-width:1200px){.charts{grid-template-columns:1fr 1fr 1fr}
  .bottom{display:grid;gap:10px;grid-template-columns:2fr 3fr;align-items:start}
  .bottom .card.mb{margin-bottom:0}}
.explain{color:var(--muted);font-size:12px;line-height:1.5;margin:-6px 0 10px}
.explain b{color:var(--ink-2)}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--ink-2);margin-bottom:6px}
.legend i{display:inline-block;width:10px;height:3px;border-radius:2px;vertical-align:middle;margin-right:5px}
.legend i.sq{height:8px;border-radius:2px;opacity:.55}
svg{width:100%;height:auto;display:block}
svg text{font:10px system-ui,sans-serif;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:7px 10px;text-align:left;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--ink-2)}
tr.past td{color:var(--muted)}
tr.today td{background:color-mix(in srgb,var(--s1) 7%,transparent)}
.ph{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.ph i{width:8px;height:8px;border-radius:50%;display:inline-block}
.note{color:var(--muted);font-size:12px}
.tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--page);
  font-size:11.5px;padding:5px 8px;border-radius:6px;opacity:0;transition:opacity .08s;
  font-variant-numeric:tabular-nums;z-index:9;white-space:nowrap}
.mb{margin-bottom:12px}
footer{color:var(--muted);font-size:11px;margin-top:16px}
</style></head><body>
<header><div><h1>Entrainement — programme 12 semaines</h1>
<span class="sub">FTP &amp; VO2max · 10 aout → 31 oct. 2026 · maj __MAJ__</span></div>
<nav class="topnav"><span class="here">Dashboard</span><a href="/pub/">Page publique</a><a href="/pub/programme.pdf">PDF</a></nav></header>

<div class="tiles" id="tiles"></div>
<div class="card next" id="next"></div>

<div class="charts">
<div class="card wide"><h2>Condition, fatigue et forme — 42 jours</h2>
<div class="explain">Le graphique central, celui de TrainingPeaks, en clair : la <b style="color:var(--s1)">condition</b> est ta moyenne d'entrainement sur 6 semaines (elle doit monter doucement — c'est ta progression) ; la <b style="color:var(--s2)">fatigue</b> est la moyenne des 7 derniers jours (elle fait le yoyo, c'est normal) ; la <b>forme</b> = condition − fatigue : negative en pleine charge, positive quand tu recuperes — c'est la qu'on performe.</div>
<div class="legend"><span><i style="background:var(--s1)"></i>Condition (6 sem.)</span><span><i style="background:var(--s2)"></i>Fatigue (7 j)</span><span><i style="background:var(--good)"></i>Forme</span></div>
<div id="c-pmc"></div></div>
<div class="card"><h2>Charge par semaine</h2>
<div class="explain">La dose d'entrainement hebdomadaire. Elle doit monter progressivement, avec un creux les semaines 4, 8 et 12.</div>
<div id="c-week"></div></div>
<div class="card"><h2>Ratio de charge</h2>
<div class="explain">Fatigue ÷ condition. Entre 0,8 et 1,3 : tu progresses sans risque. Au-dessus de 1,5 : danger blessure/surmenage.</div>
<div class="legend"><span><i class="sq" style="background:var(--good)"></i>Zone saine 0,8 – 1,3</span></div>
<div id="c-ratio"></div></div>
<div class="card"><h2>HRV nocturne — 42 jours</h2>
<div class="explain">Ton systeme nerveux. Proche de la ligne pointillee (ta norme) = tout va bien ; nettement en dessous plusieurs jours = leve le pied.</div>
<div class="legend"><span><i style="background:var(--s1)"></i>HRV</span><span><i style="background:var(--muted)"></i>Ta norme</span></div>
<div id="c-hrv"></div></div>
<div class="card"><h2>Sommeil — 14 nuits</h2>
<div class="explain">La barre pointillee = 7 h 30, l'objectif. Le bleu fonce, c'est le sommeil profond : celui qui repare les jambes.</div>
<div class="legend"><span><i style="background:var(--s1)"></i>Total</span><span><i style="background:var(--s1-deep)"></i>Profond</span></div>
<div id="c-sleep"></div></div>
<div class="card"><h2>FC de repos — 42 jours</h2>
<div class="explain">Plus c'est bas, mieux c'est. Une hausse soudaine de 5-8 bpm = fatigue ou maladie qui couve.</div>
<div id="c-rhr"></div></div>
</div>

<div class="bottom">
<div class="card mb"><h2>Dernieres sorties</h2><div style="overflow-x:auto"><table id="acts"></table></div></div>
<div class="card"><h2>Planning</h2><div style="overflow-x:auto"><table id="plan"></table></div></div>
</div>
<footer>Donnees COROS &amp; iGPSport · calendrier abonne et verdicts quotidiens generes automatiquement.</footer>
<div class="tip" id="tip"></div>
<script>
const D = __DATA__;
const fdm = d => d ? String(d).replace(/-/g,"").slice(6,8)+"/"+String(d).replace(/-/g,"").slice(4,6) : "";
const fiso = d => d.split("-").reverse().join("/").slice(0,5);
const hm = m => Math.floor(m/60)+"h"+String(m%60).padStart(2,"0");
const GOOD="var(--good)", WARN="var(--warn)", SER="var(--serious)", CRIT="var(--crit)", NEU="var(--axis)";

const dd = D.daily, lastD = dd[dd.length-1] || {};
const tsbNow = (lastD.cti!=null && lastD.ati!=null) ? Math.round(lastD.cti-lastD.ati) : null;
const rhrs = dd.map(r=>r.rhr).filter(v=>v!=null);
const rhrMean = rhrs.length ? rhrs.reduce((a,b)=>a+b,0)/rhrs.length : null;

function st(val, rules, fallback){ for (const [cond, color, label] of rules) if (cond) return [color, label]; return fallback; }
const tHRV = D.hrv==null ? [NEU,"–"] : st(0, [
  [D.hrv >= D.hrv_base-5, GOOD, "normale"],
  [D.hrv >= D.hrv_base-12, WARN, "sous baseline"],
  [true, CRIT, "basse"]]);
const tRHR = D.rhr==null||rhrMean==null ? [NEU,"–"] : st(0, [
  [D.rhr <= rhrMean+3, GOOD, "normale"],
  [D.rhr <= rhrMean+8, WARN, "elevee"],
  [true, CRIT, "tres elevee"]]);
const tSLP = D.sleep_last==null ? [NEU,"–"] : st(0, [
  [D.sleep_last >= 420, GOOD, "bonne nuit"],
  [D.sleep_last >= 360, WARN, "courte"],
  [true, CRIT, "insuffisante"]]);
const tTSB = tsbNow==null ? [NEU,"–"] : st(0, [
  [tsbNow > 5, GOOD, "frais"],
  [tsbNow > -10, GOOD, "equilibre"],
  [tsbNow > -30, WARN, "en charge"],
  [true, CRIT, "surmenage"]]);
const tRAT = D.ratio==null ? [NEU,"–"] : st(0, [
  [D.ratio < 0.8, NEU, "leger"],
  [D.ratio <= 1.3, GOOD, "optimal"],
  [D.ratio <= 1.5, WARN, "eleve"],
  [true, CRIT, "risque"]]);

const tiles = [
  ["FTP", D.ftp, "W", [NEU, "seuil"]],
  ["VO2max", D.vo2max, "", [GOOD, "niveau eleve"]],
  ["HRV", D.hrv!=null?D.hrv:null, D.hrv_base!=null?"/ "+D.hrv_base:"", tHRV],
  ["FC repos", D.rhr, "bpm", tRHR],
  ["Sommeil", D.sleep_last!=null?hm(D.sleep_last):null, "", tSLP],
  ["Forme (TSB)", tsbNow!=null?(tsbNow>0?"+":"")+tsbNow:null, "", tTSB],
  ["Ratio charge", D.ratio!=null?D.ratio.toFixed(2):null, "", tRAT],
];
document.getElementById("tiles").innerHTML = tiles.map(([l,v,u,s]) =>
  `<div class="card tile" style="--tint:${s[0]}"><div class="l">${l}</div>
   <div class="v">${v??"–"}${u?` <small>${u}</small>`:""}</div>
   <span class="st"><i></i>${s[1]}</span></div>`).join("");

const n = D.next;
const VD = {"✅":[GOOD,"Feu vert"],"⚠️":[WARN,"Seance allegee"],"🛑":[CRIT,"Remplacee par recup"]};
const doneLine = D.done_today ?
  `<div class="d" style="color:var(--good);font-weight:600;margin-bottom:6px">Aujourd'hui : ${D.done_today} — fait ✓</div>` : "";
if (n) {
  const showVerdict = !D.done_today;
  const [c, lab] = showVerdict ? (VD[n.emoji] || [NEU, ""]) : [NEU, ""];
  document.getElementById("next").innerHTML =
   `<span class="dot" style="background:${D.done_today?GOOD:c}"></span><div class="body">${doneLine}
    <div class="when">Prochaine seance — ${n.date.split("-").reverse().join("/")}</div>
    <div class="t">${n.seance} <span style="color:var(--muted);font-weight:400">· ${n.phase}${lab?" · "+lab:""}</span></div>
    ${showVerdict && n.note?`<div class="d">${n.note}</div>`:""}</div>`;
} else document.getElementById("next").innerHTML =
  `<span class="dot" style="background:${GOOD}"></span><div class="body">${doneLine}<div class="t">Programme termine.</div></div>`;

const tip = document.getElementById("tip");
function showTip(e, txt){ tip.textContent = txt; tip.style.opacity = 1;
  tip.style.left = Math.min(e.clientX+12, innerWidth-170)+"px"; tip.style.top = (e.clientY-30)+"px"; }
function hideTip(){ tip.style.opacity = 0; }

const W=460, H=150, PL=32, PR=6, PT=8, PB=20;
function lineChart(el, dates, series, opts={}){
  const W = opts.w || 460, H = opts.h || 150;
  const vals = series.flatMap(s=>s.v).filter(v=>v!=null).concat(opts.include||[]);
  const lo = opts.lo ?? Math.floor(Math.min(...vals)-2), hi = opts.hi ?? Math.ceil(Math.max(...vals)+2);
  const iw = W-PL-PR, ih = H-PT-PB, nx = dates.length;
  const X = i => PL + i/(nx-1)*iw, Y = v => PT + (1-(v-lo)/(hi-lo))*ih;
  let s = `<svg viewBox="0 0 ${W} ${H}">`;
  for (const b of (opts.bands||[])) {
    const y1 = Y(Math.min(b.hi, hi)), y2 = Y(Math.max(b.lo, lo));
    if (y2 > y1) s += `<rect x="${PL}" y="${y1}" width="${iw}" height="${y2-y1}" fill="${b.c}" opacity="0.10"/>`;
  }
  const ticks = opts.ticks || [lo,(lo+hi)/2,hi];
  for (const g of ticks) s += `<line x1="${PL}" x2="${W-PR}" y1="${Y(g)}" y2="${Y(g)}" stroke="var(--grid)"/>`+
    `<text x="${PL-5}" y="${Y(g)+3.5}" fill="var(--muted)" text-anchor="end">${opts.fmt?opts.fmt(g):Math.round(g)}</text>`;
  if (opts.zero!=null) s += `<line x1="${PL}" x2="${W-PR}" y1="${Y(opts.zero)}" y2="${Y(opts.zero)}" stroke="var(--axis)"/>`;
  for (let i=0;i<nx;i+=7) s += `<text x="${X(i)}" y="${H-5}" fill="var(--muted)" text-anchor="middle">${fdm(dates[i])}</text>`;
  for (const sr of series){
    let pts=[], seg=[];
    sr.v.forEach((v,i)=>{ if(v==null){ if(seg.length)pts.push(seg),seg=[]; } else seg.push(X(i)+","+Y(v)); });
    if (seg.length) pts.push(seg);
    for (const p of pts) s += `<polyline points="${p.join(" ")}" fill="none" stroke="${sr.c}" stroke-width="2" stroke-dasharray="${sr.d||""}" stroke-linejoin="round"/>`;
  }
  s += `<rect id="${el}-hit" x="${PL}" y="0" width="${iw}" height="${H}" fill="transparent"/></svg>`;
  const div = document.getElementById(el); div.innerHTML = s;
  const hit = document.getElementById(el+"-hit");
  hit.addEventListener("mousemove", e=>{
    const r = hit.getBoundingClientRect();
    const i = Math.round((e.clientX-r.left)/r.width*(nx-1));
    if (i<0||i>=nx) return;
    const parts = series.filter(s=>s.n && s.v[i]!=null).map(s=>`${s.n} ${opts.fmt?opts.fmt(s.v[i]):s.v[i]}`);
    if (parts.length) showTip(e, fdm(dates[i])+" · "+parts.join(" · "));
  });
  hit.addEventListener("mouseleave", hideTip);
}
function barChart(el, items, opts={}){
  const vals = items.map(r=>r.v);
  const max = opts.max ?? Math.max(...vals, 1);
  const iw = W-PL-PR, ih = H-PT-PB, bw = iw/items.length;
  let x = `<svg viewBox="0 0 ${W} ${H}">`;
  const ticks = opts.ticks || [0, Math.round(max/2), Math.round(max)];
  for (const g of ticks) { const y = PT+(1-g/max)*ih;
    x += `<line x1="${PL}" x2="${W-PR}" y1="${y}" y2="${y}" stroke="var(--grid)"/>`+
         `<text x="${PL-5}" y="${y+3.5}" fill="var(--muted)" text-anchor="end">${opts.fmt?opts.fmt(g):g}</text>`; }
  items.forEach((r,i)=>{
    const bx = PL+i*bw+ (bw>10?2:0.5), w = Math.max(bw-(bw>10?4:1), 1.5);
    const bh = Math.min(r.v,max)/max*ih;
    x += `<g class="bar" data-i="${i}"><rect x="${bx}" y="${PT+ih-bh}" width="${w}" height="${bh}" fill="${r.c||"var(--s1)"}" rx="2"/>`;
    if (r.v2!=null){ const dh = Math.min(r.v2,max)/max*ih;
      x += `<rect x="${bx}" y="${PT+ih-dh}" width="${w}" height="${dh}" fill="var(--s1-deep)" rx="2"/>`; }
    x += `</g>`;
    if (i % (opts.lab||7) === 0) x += `<text x="${bx+w/2}" y="${H-5}" fill="var(--muted)" text-anchor="middle">${r.lbl||fdm(r.d)}</text>`;
  });
  if (opts.ref!=null) x += `<line x1="${PL}" x2="${W-PR}" y1="${PT+(1-opts.ref/max)*ih}" y2="${PT+(1-opts.ref/max)*ih}" stroke="var(--axis)" stroke-dasharray="4 3"/>`;
  const div = document.getElementById(el); div.innerHTML = x+"</svg>";
  div.querySelectorAll(".bar").forEach(g=>{
    const r = items[+g.dataset.i];
    g.addEventListener("mousemove", e=>showTip(e, r.tip || (fdm(r.d)+" · "+r.v)));
    g.addEventListener("mouseleave", hideTip);
  });
}

const dts = dd.map(r=>r.d);
const tsb = dd.map(r=>(r.cti!=null&&r.ati!=null)?+(r.cti-r.ati).toFixed(1):null);
lineChart("c-pmc", dts, [
  {v: tsb, c: GOOD, n: "Forme"},
  {v: dd.map(r=>r.ati), c: "var(--s2)", n: "Fatigue"},
  {v: dd.map(r=>r.cti), c: "var(--s1)", n: "Condition", w3: 3}],
  {w: 940, h: 210, zero: 0, include: [10, -35],
   bands: [{lo: 5, hi: 999, c: GOOD}, {lo: -999, hi: -30, c: CRIT}]});
lineChart("c-ratio", dts, [{v:dd.map(r=>r.ratio), c:"var(--s1)", n:"ratio"}], {
  include:[0.5,1.6], bands:[{lo:0.8, hi:1.3, c:GOOD}], fmt:v=>(+v).toFixed(1)});
lineChart("c-hrv", dts, [
  {v: dd.map(r=>r.base), c:"var(--muted)", d:"4 3", n:"norme"},
  {v: dd.map(r=>r.hrv), c:"var(--s1)", n:"HRV"}]);
lineChart("c-rhr", dts, [{v: dd.map(r=>r.rhr), c:"var(--s1)", n:"FC"}]);
barChart("c-week", D.weeks.map(r=>({d:r.w, lbl:r.w, v:r.load, tip:r.w+" · charge "+r.load})), {lab:1});
barChart("c-sleep", D.sleep.slice(-14).map(r=>({d:r.d, v:Math.min(r.min,600), v2:r.deep,
  tip:fdm(r.d)+" · "+hm(r.min)+" dont "+hm(r.deep)+" profond"})),
  {max:600, ticks:[0,240,480], fmt:v=>v/60+"h", ref:450, lab:2});

document.getElementById("acts").innerHTML =
 "<tr><th>Date</th><th>Distance</th><th>Duree</th><th>Puissance</th><th>NP</th><th>IF</th></tr>" +
 (D.acts.length ? D.acts.map(a=>{
  const iff = a.np ? (a.np/D.ftp).toFixed(2) : "";
  return `<tr><td class="num">${a.date?String(a.date).slice(0,10).split("-").reverse().join("/"):""}</td>
   <td class="num">${a.km!=null?(+a.km).toFixed(1)+" km":""}</td>
   <td class="num">${a.dur?hm(Math.round(a.dur/60)):""}</td>
   <td class="num">${a.avg?Math.round(a.avg)+" W":""}</td>
   <td class="num">${a.np?Math.round(a.np)+" W":""}</td>
   <td class="num">${iff}</td></tr>`;
 }).join("") : "<tr><td colspan=6 class=note>Aucune sortie recente.</td></tr>");

const PH = {Base:"var(--s1)", Seuil:"var(--s2)", VO2max:CRIT, Recup:GOOD, Affutage:"var(--s1-deep)"};
const today = new Date().toISOString().slice(0,10);
document.getElementById("plan").innerHTML =
 "<tr><th>Date</th><th>Seance</th><th>Phase</th><th>Verdict</th></tr>" +
 D.plan.map(p=>{
  const cls = p.date<today ? "past" : (p.date===today ? "today" : "");
  const ph = p.phase.split(" ")[1]||"";
  const v = VD[p.emoji];
  return `<tr class="${cls}"><td class="num">${fiso(p.date)}</td><td>${p.seance}</td>
   <td><span class="ph"><i style="background:${PH[ph]||NEU}"></i>${p.phase}</span></td>
   <td>${v?`<span class="ph"><i style="background:${v[0]}"></i>${v[1]}</span>${p.note?` <span class="note">— ${p.note}</span>`:""}`:""}</td></tr>`;
 }).join("");

/* ---- animations ---- */
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
if (!reduced) {
  document.querySelectorAll(".card,.tile").forEach((el,i)=>{ el.classList.add("reveal");
    if (el.classList.contains("charts")||el.closest(".charts")) el.classList.add("chart-hover"); });
  const io = new IntersectionObserver(es=>es.forEach(e=>{
    if (!e.isIntersecting) return;
    e.target.classList.add("in");
    e.target.querySelectorAll("svg polyline").forEach((pl,j)=>{
      const len = pl.getTotalLength();
      pl.style.strokeDasharray = pl.getAttribute("stroke-dasharray") ? pl.style.strokeDasharray : len;
      if (!pl.getAttribute("stroke-dasharray")) {
        pl.style.strokeDasharray = len; pl.style.strokeDashoffset = len;
        requestAnimationFrame(()=>requestAnimationFrame(()=>{ pl.style.transitionDelay=(j*120)+"ms"; pl.style.strokeDashoffset = 0; }));
        setTimeout(()=>{ pl.style.strokeDasharray = ""; pl.style.strokeDashoffset = ""; }, 1500+j*120);
      }
    });
    e.target.querySelectorAll("svg .bar rect").forEach((rc,j)=>{
      setTimeout(()=>rc.classList.add("grown"), 30*j);
    });
    io.unobserve(e.target);
  }), {threshold: .15});
  document.querySelectorAll(".reveal").forEach((el,i)=>{ el.style.transitionDelay=(Math.min(i,8)*45)+"ms"; io.observe(el); });
  document.querySelectorAll(".tile .v").forEach(el=>{
    const m = el.childNodes[0] && el.childNodes[0].nodeValue && el.childNodes[0].nodeValue.match(/^[+-]?\\d+$/);
    if (!m) return;
    const target = parseInt(m[0]), sign = m[0].startsWith("+") ? "+" : "", t0 = performance.now(), dur = 900;
    const fmt = v => (v > 0 ? sign : "") + String(v);
    const tick = t=>{ const p = Math.min(1,(t-t0)/dur), ease = 1-Math.pow(1-p,3);
      el.childNodes[0].nodeValue = fmt(Math.round(target*ease));
      if (p<1) requestAnimationFrame(tick); };
    el.childNodes[0].nodeValue = "0"; requestAnimationFrame(tick);
    setTimeout(()=>{ el.childNodes[0].nodeValue = fmt(target); }, dur+150);
  });
  // filet de securite : si IO/rAF n'ont pas fait le travail, tout afficher
  setTimeout(()=>{
    document.querySelectorAll(".reveal:not(.in)").forEach(el=>el.classList.add("in"));
    document.querySelectorAll("svg .bar rect:not(.grown)").forEach(rc=>rc.classList.add("grown"));
    document.querySelectorAll("svg polyline").forEach(pl=>{ pl.style.strokeDasharray=pl.getAttribute("stroke-dasharray")||""; pl.style.strokeDashoffset=""; });
  }, 1500);
} else {
  document.querySelectorAll("svg .bar rect").forEach(rc=>rc.classList.add("grown"));
}
</script></body></html>
"""

page = HTML.replace("__MAJ__", data["maj"]).replace("__DATA__", json.dumps(data, ensure_ascii=False))
path = f"{OUT}/index.html"
with open(path, "w", encoding="utf-8") as f:
    f.write(page)
print("OK", path)
