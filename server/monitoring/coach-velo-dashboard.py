#!/home/opc/mcp/coros-mcp/.venv/bin/python
"""Genere le tableau de bord velo (index.html) sur velo.luku.fr."""
import asyncio, datetime, json, os, re, subprocess, sys

sys.path.insert(0, "/home/opc/mcp/coros-mcp")
MON = "/home/opc/monitoring"
# VELO_OUT permet de generer ailleurs pour verifier une modif sans toucher au site.
OUT = os.environ.get("VELO_OUT", "/home/opc/Docker/sites/velo/public")
JOURS = 56          # profondeur des series quotidiennes
JOURS_ACT = 120     # profondeur des activites, pour le volume par sport

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
    from coros_mcp.coros_api import (try_auto_login, fetch_daily_records,
                                     fetch_sleep, fetch_activities)
    auth = await try_auto_login()
    today = datetime.date.today()
    d1 = today.strftime("%Y%m%d")
    d0 = (today - datetime.timedelta(days=JOURS)).strftime("%Y%m%d")
    da = (today - datetime.timedelta(days=JOURS_ACT)).strftime("%Y%m%d")
    daily = await fetch_daily_records(auth, d0, d1)
    sleep = await fetch_sleep(auth, d0, d1)
    # fetch_activities renvoie (liste, total) : on ne garde que la liste.
    try:
        acts, _ = await fetch_activities(auth, da, d1, size=200)
    except Exception:
        acts = []
    return daily, sleep, acts

daily, sleep, coros_acts = asyncio.run(coros())

# --- activites iGPSport : elles seules portent la puissance normalisee velo ---
IGP_SNIPPET = r'''
import json, os
for line in open("/home/opc/mcp/secrets/igpsport.env"):
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1)
        os.environ[k] = v
from igpsport_mcp.tools._service import IGPSportService
from igpsport_mcp.config import load_config
svc = IGPSportService(load_config())
acts = svc.list_activities(limit=12)
rows = []
for a in (acts.get("activities") or acts.get("items") or [])[:12]:
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

# --- familles de sport : quatre seaux, donc quatre couleurs, jamais plus ---
FAMILLES = [("Velo", {200, 201, 203, 204, 9807}),
            ("Course", {100, 102, 103}),
            ("Natation", {300, 301, 302})]
ORDRE_FAM = ["Velo", "Course", "Natation", "Autre"]
# L'API ne nomme ni la natation ni le triathlon : elle rend "Sport 300",
# "Sport 10000". On complete la table plutot que d'afficher ces codes.
LABELS = {100: "Course", 102: "Trail", 103: "Piste", 104: "Rando",
          200: "Route", 201: "Home trainer", 203: "Gravel", 204: "VTT",
          300: "Natation", 301: "Eau libre", 302: "Natation",
          400: "Cardio", 402: "Renfo", 403: "Yoga",
          900: "Marche", 9807: "Velotaf", 10000: "Triathlon"}

def famille(sport_type):
    for nom, codes in FAMILLES:
        if sport_type in codes:
            return nom
    return "Autre"

def jour_de(ts):
    """L'API COROS date les activites en secondes epoch, en texte."""
    try:
        return datetime.date.fromtimestamp(int(ts))
    except Exception:
        return None

seances = []
for a in coros_acts:
    d = jour_de(a.start_time)
    if d is None:
        continue
    seances.append({
        "d": d.isoformat(), "nom": a.name or "", "fam": famille(a.sport_type),
        "sport": LABELS.get(a.sport_type) or a.sport_name or "",
        "dur": a.duration_seconds or 0,
        "km": round((a.distance_meters or 0) / 1000, 1),
        "hr": a.avg_hr, "hrmax": a.max_hr, "load": a.training_load,
        "dplus": a.elevation_gain, "watt": a.avg_power or None,
    })
seances.sort(key=lambda s: s["d"], reverse=True)

# volume hebdomadaire par famille, en heures
vol, detail = {}, {}
for s in seances:
    d = datetime.date.fromisoformat(s["d"])
    key = (d.isocalendar()[0], d.isocalendar()[1])
    vol.setdefault(key, {f: 0.0 for f in ORDRE_FAM})
    vol[key][s["fam"]] += s["dur"] / 3600
    detail.setdefault(key, {})
    lbl = s["sport"] or s["fam"]
    detail[key][lbl] = detail[key].get(lbl, 0.0) + s["dur"] / 3600

def duree(h):
    return f"{round(h, 1)} h" if h >= 1 else f"{round(h * 60)} min"

volume = []
for k, v in sorted(vol.items()):
    top = sorted(detail.get(k, {}).items(), key=lambda t: -t[1])[:4]
    volume.append({"w": f"S{k[1]:02d}",
                   "detail": " · ".join(f"{nom} {duree(h)}" for nom, h in top),
                   **{f: round(v[f], 2) for f in ORDRE_FAM}})
volume = volume[-10:]

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
jours_actifs = {s["d"] for s in seances}
did_today = today_iso in jours_actifs or any(str(a.get("date", ""))[:10] == today_iso for a in acts)
nxt = next((s for s in plan if s["date"] > today_iso or (s["date"] == today_iso and not did_today)), None)
done_today = next((s["seance"] for s in plan if s["date"] == today_iso), None) if did_today else None

# taux de seances tenues : sur les seances passees qui ont recu un verdict
juges = [s for s in plan if s["date"] <= today_iso and s["emoji"]]
tenues = sum(1 for s in juges if s["emoji"] == "✅")
allegees = sum(1 for s in juges if s["emoji"] == "⚠️")
taux = round(100 * tenues / len(juges)) if juges else None
faites = sum(1 for s in plan if s["date"] < today_iso) + (1 if done_today else 0)

vo2 = next((r.vo2max for r in reversed(daily) if r.vo2max), None)
last = daily[-1] if daily else None
last_sleep = sleep[-1] if sleep else None

wk_acc = {}
for r in daily:
    d_ = datetime.date(int(r.date[:4]), int(r.date[4:6]), int(r.date[6:8]))
    key = f"S{d_.isocalendar()[1]:02d}"
    wk_acc[key] = wk_acc.get(key, 0) + (r.training_load or 0)
weeks_data = [{"w": k, "load": round(v)} for k, v in sorted(wk_acc.items())]

def phase(r, champ):
    return (getattr(r.phases, champ) or 0) if r.phases else 0

data = {
    "maj": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
    "ftp": ftp, "vo2max": vo2,
    "hrv": last.avg_sleep_hrv if last else None,
    "hrv_base": last.baseline if last else None,
    "rhr": last.rhr if last else None,
    "ratio": last.training_load_ratio if last else None,
    "sleep_last": last_sleep.total_duration_minutes if last_sleep else None,
    "next": nxt, "done_today": done_today,
    "taux": taux, "tenues": tenues, "allegees": allegees, "juges": len(juges),
    "faites": faites, "total_seances": len(plan),
    "daily": [{"d": r.date, "hrv": r.avg_sleep_hrv, "base": r.baseline,
               "rhr": r.rhr, "ati": r.ati, "cti": r.cti,
               "ratio": r.training_load_ratio, "load": r.training_load,
               "vo2": r.vo2max, "stam": r.stamina_level, "stam7": r.stamina_level_7d,
               "lthr": r.lthr} for r in daily],
    "weeks": weeks_data,
    "sleep": [{"d": r.date, "min": r.total_duration_minutes,
               "deep": phase(r, "deep_minutes"), "light": phase(r, "light_minutes"),
               "rem": phase(r, "rem_minutes"), "awake": phase(r, "awake_minutes"),
               "hr": r.avg_hr, "hrmin": r.min_hr, "hrmax": r.max_hr} for r in sleep],
    "volume": volume, "familles": ORDRE_FAM,
    "seances": seances[:12], "acts": acts, "plan": plan,
}

HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Entrainement — programme 12 semaines</title>
<style>
/* Palette mate : chroma bridee au plancher lisible (OKLCH C 0.100), pas
   au-dela — au-dessous une teinte vire au gris. Les quatre emplacements
   categoriels et la rampe de sommeil ont ete valides (bande de luminosite,
   plancher de chroma, separation daltonisme, plancher vision normale,
   contraste sur fond) dans les deux modes. Les couleurs d'etat sont
   reservees : jamais reutilisees comme couleur de serie. */
:root{
  color-scheme:light;
  --page:#f4f2ee;--surface:#faf9f6;--raised:#efece5;--ink:#1b1a17;--ink-2:#55534c;--muted:#8b8880;
  --grid:#e6e3da;--axis:#cbc7bb;--border:rgba(27,26,23,.12);
  --s1:#4d7db4;--s2:#81411e;--s3:#5a9a68;--s4:#8e5585;
  --sl1:#05386a;--sl2:#2b5a8e;--sl3:#4d7db4;--sl4:#78a2d2;
  --good:#148c3a;--warn:#b07d00;--crit:#c4584f;
}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){
  color-scheme:dark;
  --page:#101113;--surface:#17181a;--raised:#1e1f22;--ink:#f2f1ed;--ink-2:#b9b6ae;--muted:#86837c;
  --grid:#26272a;--axis:#3b3c3f;--border:rgba(255,255,255,.12);
  --s1:#3c6ba1;--s2:#9e552f;--s3:#5a9a68;--s4:#8e5585;
  --sl1:#36659b;--sl2:#5080b7;--sl3:#729ccc;--sl4:#8eb8ea;
}}
:root[data-theme=dark]{
  color-scheme:dark;
  --page:#101113;--surface:#17181a;--raised:#1e1f22;--ink:#f2f1ed;--ink-2:#b9b6ae;--muted:#86837c;
  --grid:#26272a;--axis:#3b3c3f;--border:rgba(255,255,255,.12);
  --s1:#3c6ba1;--s2:#9e552f;--s3:#5a9a68;--s4:#8e5585;
  --sl1:#36659b;--sl2:#5080b7;--sl3:#729ccc;--sl4:#8eb8ea;
}
*{box-sizing:border-box;margin:0}
body{background:var(--page);color:var(--ink);
  font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
  padding:24px 20px 0;max-width:1440px;margin:auto}
header{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:18px}
.topnav{display:flex;gap:4px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:3px}
.topnav a,.topnav .here{font-size:12.5px;padding:5px 11px;border-radius:6px;text-decoration:none;color:var(--ink-2)}
.topnav a:hover{background:var(--raised)}
.topnav .here{background:var(--s1);color:#fff;font-weight:600}
h1{font-size:17px;font-weight:600;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:12px}
h2{font-size:11px;font-weight:600;color:var(--ink-2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;
  transition:transform .25s ease,box-shadow .25s ease,opacity .5s ease}
.reveal{opacity:0;transform:translateY(16px);transition:opacity .55s ease,transform .55s cubic-bezier(.2,.7,.3,1)}
.reveal.in{opacity:1;transform:none}
.tile:hover{transform:translateY(-3px);box-shadow:0 10px 26px rgba(0,0,0,.08)}
.chart-hover:hover{box-shadow:0 8px 24px rgba(0,0,0,.06)}
svg polyline{transition:stroke-dashoffset 1.1s cubic-bezier(.3,.6,.2,1)}
svg .bar rect{transform-origin:bottom;transform:scaleY(0);transition:transform .6s cubic-bezier(.2,.7,.3,1)}
svg .bar rect.grown{transform:scaleY(1)}
svg .bar:hover rect{filter:brightness(1.12)}
tr{transition:background .2s}
#plan tr:hover td,#seances tr:hover td{background:var(--raised)}
@media(prefers-reduced-motion:reduce){
  .reveal{opacity:1;transform:none;transition:none}
  svg polyline{transition:none}
  svg .bar rect{transform:scaleY(1);transition:none}
  .card{transition:none}
  .ride img{transform:scaleX(-1)!important}
}
.tiles{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));margin-bottom:12px}
.tile{border-top:3px solid var(--tint,var(--border))}
.tile .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.tile .v{font-size:23px;font-weight:600;margin-top:2px}
.tile .v small{font-size:12px;color:var(--muted);font-weight:400}
.tile .st{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:var(--ink-2);margin-top:4px}
.tile .st i{width:7px;height:7px;border-radius:50%;display:inline-block;background:var(--tint,var(--axis));flex:none}
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
.explain{color:var(--muted);font-size:12px;line-height:1.5;margin:-4px 0 10px}
.explain b{color:var(--ink-2);font-weight:600}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--ink-2);margin-bottom:6px}
.legend i{display:inline-block;width:10px;height:3px;border-radius:2px;vertical-align:middle;margin-right:5px}
.legend i.sq{height:9px;width:9px;border-radius:2px}
.legend i.band{height:9px;width:9px;border-radius:2px;opacity:.22}
svg{width:100%;height:auto;display:block}
svg text{font:10px system-ui,sans-serif;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:7px 10px;text-align:left;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--ink-2)}
tr.past td{color:var(--muted)}
tr.today td{background:var(--raised)}
.ph{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.ph i{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none}
.note{color:var(--muted);font-size:12px}
.tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--page);
  font-size:11.5px;padding:5px 8px;border-radius:6px;opacity:0;transition:opacity .08s;
  font-variant-numeric:tabular-nums;z-index:9;white-space:nowrap;max-width:60vw}
.mb{margin-bottom:12px}
/* frise des verdicts : une pastille par seance du programme */
.frise{display:flex;flex-wrap:wrap;gap:4px}
.frise b{width:15px;height:15px;border-radius:4px;display:block;background:var(--raised);
  border:1px solid var(--border);cursor:default}
.frise b.f-w{border-style:dashed}
/* le cycliste traverse la bande au fil du defilement, comme sur luku.fr */
.rideband{position:relative;height:132px;margin:2px 0 12px;overflow:hidden;
  border-radius:10px;border:1px solid var(--border);background:var(--surface)}
.rideband .road{position:absolute;left:0;right:0;bottom:26px;height:1px;background:var(--axis);opacity:.55}
.rideband .road2{position:absolute;left:0;right:0;bottom:22px;height:1px;
  background:repeating-linear-gradient(90deg,var(--axis) 0 14px,transparent 14px 30px);opacity:.35}
.rideband .cap{position:absolute;left:16px;top:12px;color:var(--muted);font-size:11px;
  text-transform:uppercase;letter-spacing:.06em}
.ride{position:absolute;bottom:18px;width:152px;will-change:transform;pointer-events:none;
  transform:translateX(-30%)}
.ride img{width:100%;height:auto;display:block;transform:scaleX(-1);
  filter:drop-shadow(0 10px 14px rgba(0,0,0,.18))}
@media(max-width:760px){.rideband{height:96px}.ride{width:104px;bottom:14px}}
footer{color:var(--muted);font-size:11px;margin:16px 0 28px}
</style></head><body>
<header><div><h1>Entrainement — programme 12 semaines</h1>
<span class="sub">FTP &amp; VO2max · 10 aout → 31 oct. 2026 · maj __MAJ__</span></div>
<nav class="topnav"><span class="here">Dashboard</span><a href="/pub/">Page publique</a></nav></header>

<div class="tiles" id="tiles"></div>
<div class="card next" id="next"></div>

<div class="charts">
<div class="card wide"><h2>Condition, fatigue et forme — 8 semaines</h2>
<div class="explain">Le graphique central, celui de TrainingPeaks, en clair : la <b>condition</b> est ta moyenne d'entrainement sur 6 semaines (elle doit monter doucement — c'est ta progression) ; la <b>fatigue</b> est la moyenne des 7 derniers jours (elle fait le yoyo, c'est normal) ; la <b>forme</b> = condition − fatigue : negative en pleine charge, positive quand tu recuperes — c'est la qu'on performe.</div>
<div class="legend"><span><i style="background:var(--s1)"></i>Condition (6 sem.)</span><span><i style="background:var(--s2)"></i>Fatigue (7 j)</span><span><i style="background:var(--s3)"></i>Forme</span><span><i class="band" style="background:var(--good)"></i>Zone de performance</span><span><i class="band" style="background:var(--crit)"></i>Zone de surmenage</span></div>
<div id="c-pmc"></div></div>

<div class="card"><h2>Charge par semaine</h2>
<div class="explain">La dose d'entrainement hebdomadaire, tous sports confondus. Elle doit monter progressivement, avec un creux les semaines 4, 8 et 12.</div>
<div id="c-week"></div></div>

<div class="card"><h2>Ratio de charge</h2>
<div class="explain">Fatigue ÷ condition. Entre 0,8 et 1,3 : tu progresses sans risque. Au-dessus de 1,5 : danger blessure ou surmenage.</div>
<div class="legend"><span><i class="band" style="background:var(--good)"></i>Zone saine 0,8 – 1,3</span></div>
<div id="c-ratio"></div></div>

<div class="card"><h2>Volume par sport — 10 semaines</h2>
<div class="explain">Combien d'heures par semaine, et dans quoi elles sont passees. Le coach tient compte de <b>tout</b> : la course et la natation fatiguent aussi les jambes.</div>
<div class="legend" id="lg-vol"></div>
<div id="c-vol"></div></div>

<div class="card"><h2>Sommeil — 21 nuits</h2>
<div class="explain">Chaque barre est une nuit, empilee du plus reparateur au plus leger. Le <b>profond</b> repare les jambes, le <b>REM</b> la tete. La ligne pointillee marque 7 h 30 de sommeil ; le segment le plus pale, tout en haut, ce sont les <b>reveils</b> — ils s'ajoutent a la barre sans compter comme du sommeil.</div>
<div class="legend"><span><i class="sq" style="background:var(--sl1)"></i>Profond</span><span><i class="sq" style="background:var(--sl2)"></i>Leger</span><span><i class="sq" style="background:var(--sl3)"></i>REM</span><span><i class="sq" style="background:var(--sl4)"></i>Eveille</span></div>
<div id="c-sleep"></div></div>

<div class="card"><h2>Coeur pendant la nuit — 21 nuits</h2>
<div class="explain">La zone claire va du minimum au maximum de la nuit, la ligne est la moyenne. Une moyenne qui grimpe alors que tu dors autant = digestion, alcool, stress ou infection.</div>
<div class="legend"><span><i style="background:var(--s1)"></i>Moyenne</span><span><i class="band" style="background:var(--s1)"></i>Min → max</span></div>
<div id="c-nuit"></div></div>

<div class="card"><h2>HRV nocturne — 8 semaines</h2>
<div class="explain">Ton systeme nerveux. Proche de la ligne pointillee (ta norme) = tout va bien ; nettement en dessous plusieurs jours = leve le pied.</div>
<div class="legend"><span><i style="background:var(--s1)"></i>HRV</span><span><i style="background:var(--muted)"></i>Ta norme</span></div>
<div id="c-hrv"></div></div>

<div class="card"><h2>FC de repos — 8 semaines</h2>
<div class="explain">Plus c'est bas, mieux c'est. Une hausse soudaine de 5 a 8 bpm annonce une fatigue ou une infection qui couve.</div>
<div id="c-rhr"></div></div>

<div class="card"><h2>VO2max</h2>
<div class="explain">La cylindree du moteur : le volume d'oxygene que tu sais consommer. La montre ne la recalcule que les jours de seance assez intense, d'ou les trous.</div>
<div id="c-vo2"></div></div>

<div class="card"><h2>Endurance de base</h2>
<div class="explain">L'indice d'endurance de la montre (<b>stamina</b>) : le fond de caisse accumule. Il monte lentement et se perd vite — c'est lui qui tient sur les longues sorties.</div>
<div id="c-stam"></div></div>

<div class="card wide"><h2>Les decisions du coach — programme complet</h2>
<div class="explain">Une pastille par seance prevue. Chaque matin de seance, le coach lit la nuit et tranche : maintenue, allegee, ou remplacee par du repos. Les pastilles vides sont les seances encore a venir.</div>
<div class="legend"><span><i class="sq" style="background:var(--good)"></i>Maintenue</span><span><i class="sq" style="background:var(--warn)"></i>Allegee</span><span><i class="sq" style="background:var(--crit)"></i>Repos impose</span><span><i class="sq" style="background:var(--raised);border:1px solid var(--border)"></i>A venir</span></div>
<div class="frise" id="frise"></div></div>
</div>

<div class="rideband" aria-hidden="true">
  <span class="cap">10 aout → 31 octobre 2026</span>
  <span class="road"></span><span class="road2"></span>
  <span class="ride" id="ride"><img src="/img/bike.webp" width="1264" height="848" alt="" loading="lazy"></span>
</div>

<div class="bottom">
<div class="card mb"><h2>Dernieres seances — tous sports</h2>
<div style="overflow-x:auto"><table id="seances"></table></div></div>
<div class="card"><h2>Planning</h2><div style="overflow-x:auto"><table id="plan"></table></div></div>
</div>
<footer>Donnees COROS &amp; iGPSport · calendrier abonne, verdicts quotidiens et cette page generes automatiquement chaque matin.</footer>
<div class="tip" id="tip"></div>
<script>
const D = __DATA__;
const fdm = d => d ? String(d).replace(/-/g,"").slice(6,8)+"/"+String(d).replace(/-/g,"").slice(4,6) : "";
const fiso = d => d.split("-").reverse().join("/").slice(0,5);
const hm = m => Math.floor(m/60)+"h"+String(Math.round(m%60)).padStart(2,"0");
const hdec = h => h>=1 ? (Math.round(h*10)/10)+" h" : Math.round(h*60)+" min";
const cssv = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const GOOD="var(--good)", WARN="var(--warn)", CRIT="var(--crit)", NEU="var(--axis)";
const S=["var(--s1)","var(--s2)","var(--s3)","var(--s4)"];
const SL=["var(--sl1)","var(--sl2)","var(--sl3)","var(--sl4)"];

const dd = D.daily, lastD = dd[dd.length-1] || {};
const tsbNow = (lastD.cti!=null && lastD.ati!=null) ? Math.round(lastD.cti-lastD.ati) : null;
const rhrs = dd.map(r=>r.rhr).filter(v=>v!=null);
const rhrMean = rhrs.length ? rhrs.reduce((a,b)=>a+b,0)/rhrs.length : null;

function st(rules, fallback){ for (const [cond, color, label] of rules) if (cond) return [color, label]; return fallback; }
const tHRV = D.hrv==null||D.hrv_base==null ? [NEU,"pas de mesure"] : st([
  [D.hrv >= D.hrv_base-5, GOOD, "normale"],
  [D.hrv >= D.hrv_base-12, WARN, "sous ta norme"],
  [true, CRIT, "basse"]]);
const tRHR = D.rhr==null||rhrMean==null ? [NEU,"pas de mesure"] : st([
  [D.rhr <= rhrMean+3, GOOD, "normale"],
  [D.rhr <= rhrMean+8, WARN, "elevee"],
  [true, CRIT, "tres elevee"]]);
const tSLP = D.sleep_last==null ? [NEU,"pas de mesure"] : st([
  [D.sleep_last >= 420, GOOD, "bonne nuit"],
  [D.sleep_last >= 360, WARN, "courte"],
  [true, CRIT, "insuffisante"]]);
const tTSB = tsbNow==null ? [NEU,"pas de mesure"] : st([
  [tsbNow > 5, GOOD, "frais"],
  [tsbNow > -10, GOOD, "equilibre"],
  [tsbNow > -30, WARN, "en charge"],
  [true, CRIT, "surmenage"]]);
const tRAT = D.ratio==null ? [NEU,"pas de mesure"] : st([
  [D.ratio < 0.8, NEU, "leger"],
  [D.ratio <= 1.3, GOOD, "optimal"],
  [D.ratio <= 1.5, WARN, "eleve"],
  [true, CRIT, "risque"]]);
const tTAUX = D.taux==null ? [NEU,"pas encore de verdict"] : st([
  [D.taux >= 80, GOOD, D.tenues+" tenues sur "+D.juges],
  [D.taux >= 60, WARN, D.tenues+" tenues sur "+D.juges],
  [true, CRIT, D.tenues+" tenues sur "+D.juges]]);

const tiles = [
  ["FTP", D.ftp, "W", [NEU, "seuil"]],
  ["VO2max", D.vo2max, "", [NEU, "cylindree du moteur"]],
  ["HRV", D.hrv!=null?D.hrv:null, D.hrv_base!=null?"/ "+D.hrv_base:"", tHRV],
  ["FC repos", D.rhr, "bpm", tRHR],
  ["Sommeil", D.sleep_last!=null?hm(D.sleep_last):null, "", tSLP],
  ["Forme (TSB)", tsbNow!=null?(tsbNow>0?"+":"")+tsbNow:null, "", tTSB],
  ["Ratio charge", D.ratio!=null?D.ratio.toFixed(2):null, "", tRAT],
  ["Seances tenues", D.taux!=null?D.taux:null, D.taux!=null?"%":"", tTAUX],
];
safe("tiles", "Indicateurs", ()=>{
document.getElementById("tiles").innerHTML = tiles.map(([l,v,u,s]) =>
  `<div class="card tile" style="--tint:${s[0]}"><div class="l">${l}</div>
   <div class="v">${v??"–"}${u?` <small>${u}</small>`:""}</div>
   <span class="st"><i></i>${s[1]}</span></div>`).join("");
});


const n = D.next;
const VD = {"✅":[GOOD,"Feu vert"],"⚠️":[WARN,"Seance allegee"],"🛑":[CRIT,"Remplacee par du repos"]};
const doneLine = D.done_today ?
  `<div class="d" style="color:var(--good);font-weight:600;margin-bottom:6px">Aujourd'hui : ${D.done_today} — fait</div>` : "";
if (n) {
  const showVerdict = !D.done_today;
  const [c, lab] = showVerdict ? (VD[n.emoji] || [NEU, ""]) : [NEU, ""];
  document.getElementById("next").innerHTML =
   `<span class="dot" style="background:${D.done_today?GOOD:c}"></span><div class="body">${doneLine}
    <div class="when">Prochaine seance — ${n.date.split("-").reverse().join("/")}</div>
    <div class="t">${n.seance} <span style="color:var(--muted);font-weight:400">· ${n.phase}${lab?" · "+lab:""}</span></div>
    ${showVerdict && n.note?`<div class="d">${n.note}</div>`:""}
    <div class="d" style="color:var(--muted)">${D.faites} seances passees sur ${D.total_seances} au programme</div></div>`;
} else document.getElementById("next").innerHTML =
  `<span class="dot" style="background:${GOOD}"></span><div class="body">${doneLine}<div class="t">Programme termine.</div></div>`;

/* Chaque bloc est monte a part : si l'un casse, les autres restent affiches
   et seule la carte fautive porte le message. */
function safe(id, label, fn){
  try { fn(); }
  catch (err) {
    console.error("[dashboard] " + label, err);
    const el = document.getElementById(id);
    if (el) el.innerHTML = `<div class="note" style="padding:18px 0">${label} : affichage indisponible.</div>`;
  }
}

const tip = document.getElementById("tip");
function showTip(e, txt){ tip.textContent = txt; tip.style.opacity = 1;
  tip.style.left = Math.min(e.clientX+12, innerWidth-Math.min(320, txt.length*6.4))+"px";
  tip.style.top = (e.clientY-30)+"px"; }
function hideTip(){ tip.style.opacity = 0; }

const W=460, H=150, PL=34, PR=6, PT=8, PB=20;
function lineChart(el, dates, series, opts={}){
  const W2 = opts.w || W, H2 = opts.h || H;
  const vals = series.flatMap(s=>s.v).filter(v=>v!=null).concat(opts.include||[]);
  if (!vals.length){ document.getElementById(el).innerHTML =
    `<div class="note" style="padding:18px 0">Pas encore de mesure sur la periode.</div>`; return; }
  const lo = opts.lo ?? Math.floor(Math.min(...vals)-2), hi = opts.hi ?? Math.ceil(Math.max(...vals)+2);
  const iw = W2-PL-PR, ih = H2-PT-PB, nx = dates.length;
  const X = i => PL + i/Math.max(1,nx-1)*iw, Y = v => PT + (1-(v-lo)/(hi-lo||1))*ih;
  let s = [`<svg viewBox="0 0 ${W2} ${H2}">`];
  for (const b of (opts.bands||[])) {
    const y1 = Y(Math.min(b.hi, hi)), y2 = Y(Math.max(b.lo, lo));
    if (y2 > y1) s.push(`<rect x="${PL}" y="${y1}" width="${iw}" height="${y2-y1}" fill="${b.c}" opacity="0.11"/>`);
  }
  const ticks = opts.ticks || [lo,(lo+hi)/2,hi];
  for (const g of ticks) s.push(`<line x1="${PL}" x2="${W2-PR}" y1="${Y(g)}" y2="${Y(g)}" stroke="var(--grid)"/>`+
    `<text x="${PL-5}" y="${Y(g)+3.5}" fill="var(--muted)" text-anchor="end">${opts.fmt?opts.fmt(g):Math.round(g)}</text>`);
  if (opts.zero!=null) s.push(`<line x1="${PL}" x2="${W2-PR}" y1="${Y(opts.zero)}" y2="${Y(opts.zero)}" stroke="var(--axis)"/>`);
  const every = opts.every || 7;
  for (let i=0;i<nx;i+=every) s.push(`<text x="${X(i)}" y="${H2-5}" fill="var(--muted)" text-anchor="middle">${fdm(dates[i])}</text>`);
  for (const sr of series){
    let pts=[], seg=[];
    sr.v.forEach((v,i)=>{ if(v==null){ if(seg.length)pts.push(seg),seg=[]; } else seg.push(X(i)+","+Y(v)); });
    if (seg.length) pts.push(seg);
    for (const p of pts) {
      if (p.length===1) s.push(`<circle cx="${p[0].split(",")[0]}" cy="${p[0].split(",")[1]}" r="2.6" fill="${sr.c}"/>`);
      else s.push(`<polyline points="${p.join(" ")}" fill="none" stroke="${sr.c}" stroke-width="2" stroke-dasharray="${sr.d||""}" stroke-linejoin="round" stroke-linecap="round"/>`);
    }
  }
  s.push(`<rect id="${el}-hit" x="${PL}" y="0" width="${iw}" height="${H2}" fill="transparent"/></svg>`);
  const div = document.getElementById(el); div.innerHTML = s.join("");
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
function bandChart(el, dates, lows, highs, mid, opts={}){
  const vals = [...lows,...highs,...mid].filter(v=>v!=null);
  if (!vals.length){ document.getElementById(el).innerHTML =
    `<div class="note" style="padding:18px 0">Pas encore de mesure sur la periode.</div>`; return; }
  const lo = Math.floor(Math.min(...vals)-3), hi = Math.ceil(Math.max(...vals)+3);
  const iw = W-PL-PR, ih = H-PT-PB, nx = dates.length;
  const X = i => PL + i/Math.max(1,nx-1)*iw, Y = v => PT + (1-(v-lo)/(hi-lo||1))*ih;
  let s = [`<svg viewBox="0 0 ${W} ${H}">`];
  const ticks = [lo,(lo+hi)/2,hi];
  for (const g of ticks) s.push(`<line x1="${PL}" x2="${W-PR}" y1="${Y(g)}" y2="${Y(g)}" stroke="var(--grid)"/>`+
    `<text x="${PL-5}" y="${Y(g)+3.5}" fill="var(--muted)" text-anchor="end">${Math.round(g)}</text>`);
  const top = highs.map((v,i)=> v==null?null:X(i)+","+Y(v)).filter(Boolean);
  const bot = lows.map((v,i)=> v==null?null:X(i)+","+Y(v)).filter(Boolean).reverse();
  if (top.length && bot.length) s.push(`<polygon points="${top.concat(bot).join(" ")}" fill="${opts.c||S[0]}" opacity="0.20"/>`);
  const line = mid.map((v,i)=> v==null?null:X(i)+","+Y(v)).filter(Boolean);
  if (line.length) s.push(`<polyline points="${line.join(" ")}" fill="none" stroke="${opts.c||S[0]}" stroke-width="2" stroke-linejoin="round"/>`);
  for (let i=0;i<nx;i+=3) s.push(`<text x="${X(i)}" y="${H-5}" fill="var(--muted)" text-anchor="middle">${fdm(dates[i])}</text>`);
  s.push(`<rect id="${el}-hit" x="${PL}" y="0" width="${iw}" height="${H}" fill="transparent"/></svg>`);
  document.getElementById(el).innerHTML = s.join("");
  const hit = document.getElementById(el+"-hit");
  hit.addEventListener("mousemove", e=>{
    const r = hit.getBoundingClientRect();
    const i = Math.round((e.clientX-r.left)/r.width*(nx-1));
    if (i<0||i>=nx||mid[i]==null) return;
    showTip(e, `${fdm(dates[i])} · moyenne ${mid[i]} bpm · min ${lows[i]??"–"} · max ${highs[i]??"–"}`);
  });
  hit.addEventListener("mouseleave", hideTip);
}
function barChart(el, items, opts={}){
  const vals = items.map(r=>r.v);
  if (!items.length){ document.getElementById(el).innerHTML =
    `<div class="note" style="padding:18px 0">Pas encore de donnee.</div>`; return; }
  const max = opts.max ?? Math.max(...vals, 1);
  const iw = W-PL-PR, ih = H-PT-PB, bw = iw/items.length;
  let x = [`<svg viewBox="0 0 ${W} ${H}">`];
  const ticks = (opts.ticks || [0, Math.round(max/2), Math.round(max)]).filter(g=>g<=max);
  for (const g of ticks) { const y = PT+(1-g/max)*ih;
    x.push(`<line x1="${PL}" x2="${W-PR}" y1="${y}" y2="${y}" stroke="var(--grid)"/>`+
         `<text x="${PL-5}" y="${y+3.5}" fill="var(--muted)" text-anchor="end">${opts.fmt?opts.fmt(g):g}</text>`); }
  items.forEach((r,i)=>{
    const bx = PL+i*bw+ (bw>10?2:0.5), w = Math.max(bw-(bw>10?4:1), 1.5);
    const bh = Math.min(r.v,max)/max*ih;
    x.push(`<g class="bar" data-i="${i}"><rect x="${bx}" y="${PT+ih-bh}" width="${w}" height="${bh}" fill="${r.c||S[0]}" rx="3"/></g>`);
    if (i % (opts.lab||7) === 0) x.push(`<text x="${bx+w/2}" y="${H-5}" fill="var(--muted)" text-anchor="middle">${r.lbl||fdm(r.d)}</text>`);
  });
  if (opts.ref!=null) x.push(`<line x1="${PL}" x2="${W-PR}" y1="${PT+(1-opts.ref/max)*ih}" y2="${PT+(1-opts.ref/max)*ih}" stroke="var(--axis)" stroke-dasharray="4 3"/>`);
  const div = document.getElementById(el); div.innerHTML = x.join("")+"</svg>";
  div.querySelectorAll(".bar").forEach(g=>{
    const r = items[+g.dataset.i];
    g.addEventListener("mousemove", e=>showTip(e, r.tip || (fdm(r.d)+" · "+r.v)));
    g.addEventListener("mouseleave", hideTip);
  });
}
/* barres empilees : 2 px de fond entre deux segments, comme entre deux barres */
function stackChart(el, items, colors, opts={}){
  if (!items.length || !items.some(r=>r.segs.some(v=>v>0))){ document.getElementById(el).innerHTML =
    `<div class="note" style="padding:18px 0">Pas encore de donnee.</div>`; return; }
  const totals = items.map(r=>r.segs.reduce((a,b)=>a+(b||0),0));
  const max = opts.max ?? Math.max(...totals, 1);
  const iw = W-PL-PR, ih = H-PT-PB, bw = iw/items.length;
  let x = [`<svg viewBox="0 0 ${W} ${H}">`];
  const ticks = (opts.ticks || [0, max/2, max]).filter(g=>g<=max);
  for (const g of ticks) { const y = PT+(1-g/max)*ih;
    x.push(`<line x1="${PL}" x2="${W-PR}" y1="${y}" y2="${y}" stroke="var(--grid)"/>`+
         `<text x="${PL-5}" y="${y+3.5}" fill="var(--muted)" text-anchor="end">${opts.fmt?opts.fmt(g):Math.round(g)}</text>`); }
  items.forEach((r,i)=>{
    const bx = PL+i*bw+(bw>10?2:0.5), w = Math.max(bw-(bw>10?4:1), 1.5);
    let acc = 0;
    x.push(`<g class="bar" data-i="${i}">`);
    r.segs.forEach((v,k)=>{
      if (!v) return;
      const h0 = v/max*ih, y0 = PT+ih-(acc+v)/max*ih;
      const h1 = Math.max(h0-2, 1);
      x.push(`<rect x="${bx}" y="${y0}" width="${w}" height="${h1}" fill="${colors[k]}" rx="3"/>`);
      acc += v;
    });
    x.push(`</g>`);
    if (i % (opts.lab||1) === 0) x.push(`<text x="${bx+w/2}" y="${H-5}" fill="var(--muted)" text-anchor="middle">${r.lbl}</text>`);
  });
  if (opts.ref!=null) x.push(`<line x1="${PL}" x2="${W-PR}" y1="${PT+(1-opts.ref/max)*ih}" y2="${PT+(1-opts.ref/max)*ih}" stroke="var(--axis)" stroke-dasharray="4 3"/>`);
  const div = document.getElementById(el); div.innerHTML = x.join("")+"</svg>";
  div.querySelectorAll(".bar").forEach(g=>{
    const r = items[+g.dataset.i];
    g.addEventListener("mousemove", e=>showTip(e, r.tip));
    g.addEventListener("mouseleave", hideTip);
  });
}

const dts = dd.map(r=>r.d);
const tsb = dd.map(r=>(r.cti!=null&&r.ati!=null)?+(r.cti-r.ati).toFixed(1):null);
safe("c-pmc", "Condition, fatigue et forme", ()=>lineChart("c-pmc", dts, [
  {v: tsb, c: S[2], n: "Forme"},
  {v: dd.map(r=>r.ati), c: S[1], n: "Fatigue"},
  {v: dd.map(r=>r.cti), c: S[0], n: "Condition"}],
  {w: 940, h: 220, zero: 0, include: [10, -35],
   bands: [{lo: 5, hi: 999, c: "var(--good)"}, {lo: -999, hi: -30, c: "var(--crit)"}]}););
safe("c-ratio", "Ratio de charge", ()=>/* Le ratio vit entre 0 et 2 : la marge de +/- 2 des autres courbes ecrasait
   la zone saine 0,8-1,3 sur 7 % de la hauteur. Echelle posee a la main. */
const RMAX = Math.max(1.8, Math.ceil((Math.max(...dd.map(r=>r.ratio).filter(v=>v!=null), 1.3)+0.2)*10)/10);
lineChart("c-ratio", dts, [{v:dd.map(r=>r.ratio), c:S[0], n:"ratio"}], {
  lo:0, hi:RMAX, ticks:[0, 0.8, 1.3, RMAX],
  bands:[{lo:0.8, hi:1.3, c:"var(--good)"}], fmt:v=>(+v).toFixed(1)}););
safe("c-hrv", "HRV nocturne", ()=>lineChart("c-hrv", dts, [
  {v: dd.map(r=>r.base), c:"var(--muted)", d:"4 3", n:"norme"},
  {v: dd.map(r=>r.hrv), c:S[0], n:"HRV"}]););
safe("c-rhr", "FC de repos", ()=>lineChart("c-rhr", dts, [{v: dd.map(r=>r.rhr), c:S[0], n:"FC"}]););
safe("c-vo2", "VO2max", ()=>lineChart("c-vo2", dts, [{v: dd.map(r=>r.vo2), c:S[0], n:"VO2max"}], {every:14}););
safe("c-stam", "Endurance de base", ()=>lineChart("c-stam", dts, [{v: dd.map(r=>r.stam), c:S[0], n:"endurance"}], {every:14, fmt:v=>Math.round(v)}););
safe("c-week", "Charge par semaine", ()=>barChart("c-week", D.weeks.map(r=>({d:r.w, lbl:r.w, v:r.load, tip:r.w+" · charge "+r.load})), {lab:1}););

const S21 = D.sleep.slice(-21);
const SMAX = Math.max(600, Math.ceil(Math.max(...S21.map(r=>r.deep+r.light+r.rem+r.awake), 0)/60)*60);
safe("c-sleep", "Sommeil", ()=>{
stackChart("c-sleep", S21.map(r=>({lbl:fdm(r.d),
  segs:[r.deep, r.light, r.rem, r.awake],
  tip:`${fdm(r.d)} · ${hm(r.min)} de sommeil — profond ${hm(r.deep)} · leger ${hm(r.light)} · REM ${hm(r.rem)} · eveille ${hm(r.awake)}`})),
  SL, {max:SMAX, ticks:[0,120,240,360,480,600,720], fmt:v=>Math.round(v/60)+"h", ref:450, lab:3});
});

safe("c-nuit", "Coeur pendant la nuit", ()=>bandChart("c-nuit", S21.map(r=>r.d), S21.map(r=>r.hrmin), S21.map(r=>r.hrmax), S21.map(r=>r.hr)););

const FAM = D.familles;
document.getElementById("lg-vol").innerHTML = FAM.map((f,i)=>
  `<span><i class="sq" style="background:${S[i]}"></i>${f}</span>`).join("");
safe("c-vol", "Volume par sport", ()=>{
stackChart("c-vol", D.volume.map(r=>({lbl:r.w,
  segs:FAM.map(f=>r[f]||0),
  tip:`${r.w} · ${hdec(FAM.reduce((a,f)=>a+(r[f]||0),0))} au total — ${r.detail}`})),
  S, {fmt:v=>Math.round(v)+" h", lab:1});
});


const today = new Date().toISOString().slice(0,10);
safe("frise", "Decisions du coach", ()=>{
document.getElementById("frise").innerHTML = D.plan.map(p=>{
  const v = VD[p.emoji];
  const bg = v ? v[0] : "var(--raised)";
  const cls = p.emoji==="⚠️" ? "f-w" : "";
  const etat = v ? v[1] : (p.date<=today ? "sans verdict" : "a venir");
  return `<b class="${cls}" style="background:${bg}" data-tip="${fiso(p.date)} · ${p.seance} · ${etat}"></b>`;
}).join("");
document.querySelectorAll("#frise b").forEach(b=>{
  b.addEventListener("mousemove", e=>showTip(e, b.dataset.tip));
  b.addEventListener("mouseleave", hideTip);
});
});


const puis = {};
for (const a of D.acts) if (a.date) puis[String(a.date).slice(0,10)] = a;
safe("seances", "Dernieres seances", ()=>{
document.getElementById("seances").innerHTML =
 "<tr><th>Date</th><th>Sport</th><th>Seance</th><th>Duree</th><th>Distance</th><th>FC</th><th>Charge</th><th>NP</th><th>IF</th></tr>" +
 (D.seances.length ? D.seances.map(a=>{
  const p = puis[a.d] || {};
  const iff = p.np ? (p.np/D.ftp).toFixed(2) : "";
  const ci = Math.max(0, FAM.indexOf(a.fam));
  return `<tr><td class="num">${fiso(a.d)}</td>
   <td><span class="ph"><i style="background:${S[ci]}"></i>${a.sport||a.fam}</span></td>
   <td>${a.nom}</td>
   <td class="num">${a.dur?hm(Math.round(a.dur/60)):""}</td>
   <td class="num">${a.km?a.km.toFixed(1)+" km":""}</td>
   <td class="num">${a.hr?a.hr+" bpm":""}</td>
   <td class="num">${a.load??""}</td>
   <td class="num">${p.np?Math.round(p.np)+" W":""}</td>
   <td class="num">${iff}</td></tr>`;
 }).join("") : "<tr><td colspan=9 class=note>Aucune seance enregistree.</td></tr>");
});


const PH = {Base:S[0], Seuil:S[1], VO2max:S[3], Recup:S[2], Affutage:S[0]};
safe("plan", "Planning", ()=>{
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
});


/* ---- animations ---- */
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* le cycliste traverse la bande pendant qu'elle traverse l'ecran */
(function(){
  const band = document.querySelector(".rideband"), ride = document.getElementById("ride");
  if (!band || !ride) return;
  if (reduced){ ride.style.transform = "translateX(120px)"; return; }
  let queued = false;
  function place(){
    queued = false;
    const r = band.getBoundingClientRect();
    const p = Math.max(0, Math.min(1, (innerHeight - r.top) / (innerHeight + r.height)));
    const travel = band.clientWidth + ride.offsetWidth;
    ride.style.transform = `translateX(${Math.round(-ride.offsetWidth + p*travel)}px)`;
  }
  addEventListener("scroll", ()=>{ if(!queued){ queued=true; requestAnimationFrame(place); } }, {passive:true});
  addEventListener("resize", place);
  place();
})();

if (!reduced) {
  document.querySelectorAll(".card,.tile").forEach(el=>{ el.classList.add("reveal");
    if (el.closest(".charts")) el.classList.add("chart-hover"); });
  const io = new IntersectionObserver(es=>es.forEach(e=>{
    if (!e.isIntersecting) return;
    e.target.classList.add("in");
    e.target.querySelectorAll("svg polyline").forEach((pl,j)=>{
      if (pl.getAttribute("stroke-dasharray")) return;
      const len = pl.getTotalLength();
      pl.style.strokeDasharray = len; pl.style.strokeDashoffset = len;
      requestAnimationFrame(()=>requestAnimationFrame(()=>{ pl.style.transitionDelay=(j*120)+"ms"; pl.style.strokeDashoffset = 0; }));
      setTimeout(()=>{ pl.style.strokeDasharray = ""; pl.style.strokeDashoffset = ""; }, 1500+j*120);
    });
    e.target.querySelectorAll("svg .bar rect").forEach((rc,j)=>{
      setTimeout(()=>rc.classList.add("grown"), 24*j);
    });
    io.unobserve(e.target);
  }), {threshold: .12});
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
  }, 1600);
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
