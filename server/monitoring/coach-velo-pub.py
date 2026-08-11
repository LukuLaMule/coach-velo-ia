#!/home/opc/mcp/coros-mcp/.venv/bin/python
"""Genere la page publique velo.luku.fr/pub/ (vitrine reseaux sociaux)."""
import asyncio, datetime, json, os, re, subprocess, sys

sys.path.insert(0, "/home/opc/mcp/coros-mcp")
OUT = "/home/opc/Docker/sites/velo/public/pub"

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
    from coros_mcp.coros_api import try_auto_login, fetch_daily_records
    auth = await try_auto_login()
    today = datetime.date.today()
    d0 = (today - datetime.timedelta(days=42)).strftime("%Y%m%d")
    return await fetch_daily_records(auth, d0, today.strftime("%Y%m%d"))

daily = asyncio.run(coros())

IGP = r'''
import json, os
for line in open("/home/opc/mcp/secrets/igpsport.env"):
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1); os.environ[k] = v
from igpsport_mcp.tools._service import IGPSportService
from igpsport_mcp.config import load_config
svc = IGPSportService(load_config())
acts = svc.list_activities(limit=30)
rows = [{"date": a.get("date") or a.get("start_time"), "km": a.get("distance_km") or a.get("distance") or 0}
        for a in (acts.get("activities") or acts.get("items") or [])]
mx = 0
for a in rows[:6]:
    pass
print(json.dumps(rows))
'''
acts = []
try:
    r = subprocess.run(["/home/opc/mcp/igpsport-mcp/.venv/bin/python", "-c", IGP],
                       capture_output=True, text=True, timeout=120)
    acts = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else []
except Exception:
    acts = []

today = datetime.date.today()
d42 = today - datetime.timedelta(days=42)
recent = [a for a in acts if a.get("date") and str(a["date"])[:10] >= d42.isoformat()]
km42 = round(sum(a["km"] for a in recent))
nsort = len(recent)
maxkm = round(max((a["km"] for a in recent), default=0))
vo2 = next((r.vo2max for r in reversed(daily) if r.vo2max), 55)
last = daily[-1] if daily else None
cti = round(last.cti) if last and last.cti is not None else None
rhr = min((r.rhr for r in daily if r.rhr), default=None)
pmax = 856  # record releve sur les sorties recentes

weeks = {}
for a in recent:
    iso = datetime.date.fromisoformat(str(a["date"])[:10]).isocalendar()
    key = f"{iso[0]}-{iso[1]:02d}"
    weeks[key] = weeks.get(key, 0) + a["km"]
wk = [{"w": k[5:], "km": round(v)} for k, v in sorted(weeks.items())]

ctl_series = [{"d": r.date, "v": r.cti} for r in daily if r.cti is not None]
jv = (datetime.date(2026, 8, 15) - today).days

plan = []
for line in open("/home/opc/monitoring/coach-velo-plan.tsv"):
    p = line.rstrip("\n").split("\t")
    if len(p) >= 3:
        plan.append({"date": p[0], "seance": p[1], "phase": p[2]})
today_iso = today.isoformat()
did_today = any(str(a.get("date", ""))[:10] == today_iso for a in acts)
nxt = next((s for s in plan if s["date"] > today_iso or (s["date"] == today_iso and not did_today)), None)
done_count = sum(1 for s in plan if s["date"] < today_iso or (s["date"] == today_iso and did_today))

data = {"ftp": ftp, "vo2": vo2, "cti": cti, "rhr": rhr, "pmax": pmax,
        "km42": km42, "nsort": nsort, "maxkm": maxkm, "wk": wk,
        "ctl": ctl_series, "jv": jv,
        "maj": today.strftime("%d/%m/%Y")}

HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LUCAS — Objectif Ventoux &amp; 12 semaines FTP/VO2max</title>
<meta property="og:title" content="LUCAS — Objectif Ventoux">
<meta property="og:description" content="FTP 275 W · VO2max 55 · programme 12 semaines. Suivez la progression.">
<meta property="og:image" content="https://velo.luku.fr/pub/hero.png">
<style>
*{box-sizing:border-box;margin:0}
:root{--or:#ff7a1a;--bl:#3987e5;--gold:#ffd75e}
body{background:#0a0c10;color:#eef1f5;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;overflow-x:hidden}
.wrap{max-width:1060px;margin:auto;padding:0 18px}
.topnav{position:fixed;top:12px;right:14px;z-index:5;display:flex;gap:4px;
  background:rgba(10,12,16,.72);backdrop-filter:blur(8px);border:1px solid #2a3140;border-radius:9px;padding:3px}
.topnav a,.topnav .here{font-size:12.5px;padding:5px 11px;border-radius:6px;text-decoration:none;color:#c6ccd6}
.topnav a:hover{background:rgba(255,122,26,.18);color:#fff}
.topnav .here{background:var(--or);color:#0a0c10;font-weight:700}
.hero{position:relative;min-height:92vh;display:flex;align-items:flex-end;
  background:linear-gradient(180deg,rgba(10,12,16,.15) 30%,rgba(10,12,16,.96) 88%),url(hero.png) center 22%/cover no-repeat}
.hero .inner{padding:0 18px 46px;max-width:1060px;margin:auto;width:100%}
.kicker{color:var(--or);font-weight:800;letter-spacing:.28em;font-size:12px;text-transform:uppercase}
h1{font-size:clamp(44px,9vw,92px);font-weight:900;line-height:.95;letter-spacing:-.02em;text-transform:uppercase}
h1 span{color:var(--or)}
.sub{color:#aeb7c4;font-size:clamp(15px,2.4vw,19px);margin-top:10px;max-width:640px}
.badge{display:inline-block;background:var(--or);color:#0a0c10;font-weight:800;border-radius:999px;
  padding:7px 16px;margin-top:18px;font-size:14px;letter-spacing:.02em}
.band{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:2px;background:#1c222c;
  border-radius:14px;overflow:hidden;margin:-40px auto 0;position:relative;z-index:2;box-shadow:0 18px 50px rgba(0,0,0,.5)}
.band>div{background:#12161d;padding:20px 16px;text-align:center}
.band .n{font-size:clamp(26px,4.5vw,40px);font-weight:900;font-variant-numeric:tabular-nums}
.band .n em{font-style:normal;font-size:.45em;color:#8b96a5;font-weight:600}
.band .l{color:#8b96a5;font-size:11px;text-transform:uppercase;letter-spacing:.12em;margin-top:4px}
.band .hl .n{color:var(--or)}
section{margin:64px 0}
.nextstrip{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;
  background:#12161d;border:1px solid #1c222c;border-left:4px solid var(--or);border-radius:12px;
  padding:14px 18px;margin-top:14px}
.ns-l{color:var(--or);font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.18em;display:block}
.ns-t{font-size:17px;font-weight:800}
.ns-p{color:#8b96a5;font-size:13px;font-variant-numeric:tabular-nums}
h2{font-size:13px;font-weight:800;color:var(--or);text-transform:uppercase;letter-spacing:.22em;margin-bottom:22px}
.duo{display:grid;gap:34px;grid-template-columns:1fr;align-items:center}
@media(min-width:860px){.duo{grid-template-columns:380px 1fr}}
.fifa{position:relative;aspect-ratio:3/4.3;border-radius:22px;overflow:hidden;width:100%;max-width:380px;margin:auto;
  background:linear-gradient(160deg,#2a2410,#0f0c04 70%);
  box-shadow:0 0 0 1px rgba(255,215,94,.45),0 24px 70px rgba(0,0,0,.65), inset 0 0 90px rgba(255,215,94,.12)}
.fifa img{position:absolute;inset:0;width:100%;height:74%;object-fit:cover;object-position:center 18%;
  -webkit-mask-image:linear-gradient(180deg,#000 78%,transparent);mask-image:linear-gradient(180deg,#000 78%,transparent)}
.fifa .ov{position:absolute;top:16px;left:18px;color:var(--gold);text-shadow:0 2px 12px rgba(0,0,0,.7)}
.fifa .ov .r{font-size:56px;font-weight:900;line-height:1}
.fifa .ov .p{font-size:15px;font-weight:800;letter-spacing:.14em}
.fifa .name{position:absolute;bottom:27%;width:100%;text-align:center;font-size:30px;font-weight:900;
  letter-spacing:.1em;color:#fff;text-shadow:0 2px 14px rgba(0,0,0,.8)}
.fifa .stats{position:absolute;bottom:12px;left:0;right:0;display:grid;grid-template-columns:1fr 1fr;gap:3px 0;padding:0 26px}
.fifa .stats div{display:flex;justify-content:space-between;font-weight:800;font-size:15.5px;color:var(--gold)}
.fifa .stats span:last-child{color:#fff;font-variant-numeric:tabular-nums}
.fifa .sep{position:absolute;bottom:24.5%;left:12%;right:12%;height:1px;background:linear-gradient(90deg,transparent,rgba(255,215,94,.7),transparent)}
.copy p{color:#aeb7c4;margin-bottom:14px;font-size:16px}
.copy strong{color:#fff}
.phases{display:flex;border-radius:10px;overflow:hidden;height:56px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
.phases div{display:flex;align-items:center;justify-content:center;color:#0a0c10}
.chart-card{background:#12161d;border:1px solid #1c222c;border-radius:14px;padding:20px}
.charts2{display:grid;gap:14px;grid-template-columns:1fr}
@media(min-width:860px){.charts2{grid-template-columns:1fr 1fr}}
svg{width:100%;height:auto;display:block}
svg text{font:10.5px system-ui;fill:#8b96a5;font-variant-numeric:tabular-nums}
footer{color:#5c6672;font-size:12.5px;padding:30px 0 40px;text-align:center}
footer a{color:#8b96a5}
</style></head><body>

<nav class="topnav"><a href="/">Dashboard</a><span class="here">Page publique</span><a href="programme.pdf">PDF</a></nav>
<div class="hero"><div class="inner">
<div class="kicker">Programme 12 semaines · aout → octobre 2026</div>
<h1>Objectif<br><span>Ventoux</span></h1>
<div class="sub">21,5 km a 7,5 %. 1 600 m de denivele. Un plan d'entrainement pilote par les donnees — FTP, VO2max, charge, recuperation — genere et suivi automatiquement, jour apres jour.</div>
<div class="badge" id="jv">J-__JV__ avant le Geant de Provence</div>
</div></div>

<div class="wrap">
<div class="band">
<div class="hl"><div class="n">__FTP__<em> W</em></div><div class="l">FTP</div></div>
<div><div class="n">__VO2__</div><div class="l">VO2max</div></div>
<div><div class="n">__PMAX__<em> W</em></div><div class="l">Puissance max</div></div>
<div><div class="n">__KM42__<em> km</em></div><div class="l">6 dernieres semaines</div></div>
<div><div class="n">__NSORT__</div><div class="l">Sorties</div></div>
<div><div class="n">__RHR__<em> bpm</em></div><div class="l">FC repos</div></div>
</div>

<div class="nextstrip">
<div><span class="ns-l">Prochaine seance</span><span class="ns-t">__NEXT__</span></div>
<div class="ns-p">__DONE__/36 seances faites</div>
</div>

<section><div class="duo">
<div class="fifa">
<img src="card.png" alt="Lucas">
<div class="ov"><div class="r">__COTE__</div><div class="p">GRM</div></div>
<div class="name">LUCAS</div>
<div class="sep"></div>
<div class="stats">
<div><span>FTP</span><span>__FTP__ W</span></div><div><span>VO2</span><span>__VO2__</span></div>
<div><span>PMAX</span><span>__PMAX__ W</span></div><div><span>FC</span><span>__RHR__ bpm</span></div>
<div><span>VOL</span><span>__KM42__ km</span></div><div><span>CTL</span><span>__CTI__</span></div>
</div>
</div>
<div class="copy">
<h2>La carte du grimpeur</h2>
<p><strong>Toutes les donnees sont reelles</strong>, mesurees par capteur de puissance et montre : seuil fonctionnel a <strong>__FTP__ watts</strong>, VO2max estimee a <strong>__VO2__</strong>, pointe a <strong>__PMAX__ W</strong>.</p>
<p>Derriere cette carte, une machinerie complete : les sorties remontent du compteur, un coach automatique analyse chaque nuit (sommeil, variabilite cardiaque, charge) et adapte la seance du jour, le calendrier se met a jour tout seul.</p>
<p>Douze semaines, trois phases, un juge de paix : <strong>le Ventoux par Bedoin</strong>, puis un re-test FTP fin octobre pour mesurer le chemin parcouru.</p>
</div>
</div></section>

<section><h2>Le plan — 3 phases, 36 seances</h2>
<div class="phases">
<div style="flex:4;background:#5aa9e6">Base — sweet spot</div>
<div style="flex:4;background:#ff7a1a">Seuil</div>
<div style="flex:3;background:#e05a5a">VO2max</div>
<div style="flex:1;background:#ffd75e">Peak</div>
</div></section>

<section><h2>La progression, en direct</h2>
<div class="charts2">
<div class="chart-card"><div style="color:#aeb7c4;font-size:13px;margin-bottom:10px">Condition physique (CTL, 6 semaines)</div><div id="c-ctl"></div></div>
<div class="chart-card"><div style="color:#aeb7c4;font-size:13px;margin-bottom:10px">Volume hebdomadaire (km)</div><div id="c-km"></div></div>
</div></section>
</div>

<footer>Donnees live : capteur de puissance + COROS · page regeneree chaque matin · derniere maj __MAJ__<br>
Construite avec un coach IA autonome sur serveur perso.</footer>
<script>
const D = __DATA__;
const fdm = d => d.slice(6,8)+"/"+d.slice(4,6);
(function(){
  const W=460,H=170,PL=30,PR=8,PT=10,PB=22, s=D.ctl;
  const vs=s.map(r=>r.v), lo=Math.floor(Math.min(...vs)-3), hi=Math.ceil(Math.max(...vs)+3);
  const iw=W-PL-PR, ih=H-PT-PB;
  const X=i=>PL+i/(s.length-1)*iw, Y=v=>PT+(1-(v-lo)/(hi-lo))*ih;
  let pts=s.map((r,i)=>X(i)+","+Y(r.v)).join(" ");
  let x=`<svg viewBox="0 0 ${W} ${H}">`;
  for(const g of [lo,(lo+hi)/2,hi]) x+=`<line x1="${PL}" x2="${W-PR}" y1="${Y(g)}" y2="${Y(g)}" stroke="#1c222c"/><text x="${PL-5}" y="${Y(g)+3.5}" text-anchor="end">${Math.round(g)}</text>`;
  x+=`<polygon points="${PL},${PT+ih} ${pts} ${X(s.length-1)},${PT+ih}" fill="rgba(255,122,26,.16)"/>`;
  x+=`<polyline points="${pts}" fill="none" stroke="#ff7a1a" stroke-width="2.5" stroke-linejoin="round"/>`;
  for(let i=0;i<s.length;i+=7) x+=`<text x="${X(i)}" y="${H-6}" text-anchor="middle">${fdm(s[i].d)}</text>`;
  document.getElementById("c-ctl").innerHTML=x+"</svg>";
})();
(function(){
  const W=460,H=170,PL=30,PR=8,PT=10,PB=22, s=D.wk;
  const max=Math.max(...s.map(r=>r.km),1);
  const iw=W-PL-PR, ih=H-PT-PB, bw=iw/s.length;
  let x=`<svg viewBox="0 0 ${W} ${H}">`;
  for(const g of [0,Math.round(max/2),Math.round(max)]) { const y=PT+(1-g/max)*ih;
    x+=`<line x1="${PL}" x2="${W-PR}" y1="${y}" y2="${y}" stroke="#1c222c"/><text x="${PL-5}" y="${y+3.5}" text-anchor="end">${g}</text>`; }
  s.forEach((r,i)=>{
    const bx=PL+i*bw+5, w=bw-10, bh=r.km/max*ih;
    x+=`<rect x="${bx}" y="${PT+ih-bh}" width="${w}" height="${bh}" rx="4" fill="#3987e5"/>`;
    x+=`<text x="${bx+w/2}" y="${PT+ih-bh-5}" text-anchor="middle" fill="#eef1f5" font-weight="700">${r.km}</text>`;
    x+=`<text x="${bx+w/2}" y="${H-6}" text-anchor="middle">S${r.w}</text>`;
  });
  document.getElementById("c-km").innerHTML=x+"</svg>";
})();
</script></body></html>
"""

cote = min(99, round(data["vo2"] * 1.42))
page = HTML
for img in ["hero.png", "card.png"]:
    fp = f"{OUT}/{img}"
    v = int(os.path.getmtime(fp)) if os.path.exists(fp) else 0
    page = page.replace(img, f"{img}?v={v}")
for k, v in [("__FTP__", data["ftp"]), ("__VO2__", data["vo2"]), ("__PMAX__", data["pmax"]),
             ("__KM42__", data["km42"]), ("__NSORT__", data["nsort"]), ("__RHR__", data["rhr"]),
             ("__CTI__", data["cti"]), ("__COTE__", cote), ("__JV__", data["jv"]),
             ("__MAJ__", data["maj"]),
             ("__NEXT__", (nxt["seance"] + " — " + "/".join(reversed(nxt["date"].split("-")[1:]))) if nxt else "Programme termine"),
             ("__DONE__", done_count)]:
    page = page.replace(k, str(v))
page = page.replace("__DATA__", json.dumps(data, ensure_ascii=False))
with open(f"{OUT}/index.html", "w", encoding="utf-8") as f:
    f.write(page)
print("OK", f"{OUT}/index.html")
