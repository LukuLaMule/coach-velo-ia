#!/home/opc/mcp/coros-mcp/.venv/bin/python
"""Genere le tableau de bord velo (index.html) sur velo.luku.fr."""
import asyncio, datetime, json, os, re, subprocess, sys

sys.path.insert(0, "/home/opc/mcp/coros-mcp")
MON = "/home/opc/monitoring"
# VELO_OUT permet de generer ailleurs pour verifier une modif sans toucher au site.
OUT = os.environ.get("VELO_OUT", "/home/opc/Docker/sites/velo/public")
JOURS = 56          # profondeur des series quotidiennes
JOURS_ACT = 120     # profondeur des activites, pour le volume par sport

# Repli si IGPSPORT_FTP manque dans les secrets : il a longtemps valu 275, la
# valeur d'avant le test du 10/08/2026, et la page affichait donc un FTP perime
# sans rien signaler. Source de verite : coach-velo.prompt.md.
ftp = 290
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
    "seances": seances[:12], "toutes": seances, "acts": acts, "plan": plan,
}

HTML = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Entrainement — programme 12 semaines</title>
<style>
/* Charte claire, une seule et meme sur les deux pages. Pas de mode sombre :
   la page est toujours sur papier chaud. Quatre emplacements categoriels
   (--s1..--s4), une rampe de sommeil (--sl1..--sl4), trois couleurs d'etat
   reservees (--good/--warn/--crit) jamais reutilisees comme couleur de serie,
   et un accent (--accent) reserve a l'interface : titre de section, selection,
   barre de progression. */
:root{
  color-scheme:light;
  --page:#f7f5f0;--page-2:#efebe2;--surface:#fffefc;--raised:#f2eee6;
  --ink:#17150f;--ink-2:#4d4941;--muted:#8b8579;
  --grid:#e8e3d8;--axis:#cfc9ba;--border:rgba(23,21,15,.11);
  --accent:#e2571f;--accent-2:#f4a423;
  --s1:#2b6ca8;--s2:#c1592a;--s3:#2f8f5b;--s4:#7d4f9c;
  --sl1:#0d3c69;--sl2:#2b6ca8;--sl3:#5e97cf;--sl4:#a9c9e8;
  --good:#17833c;--warn:#a9761a;--crit:#c8493d;
  --shadow:0 1px 2px rgba(23,21,15,.05),0 10px 30px rgba(23,21,15,.06);
}
*{box-sizing:border-box;margin:0}
html{scroll-behavior:smooth;scroll-padding-top:120px}
body{background:var(--page);color:var(--ink);
  font:14.5px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1400px;margin:auto;padding:0 20px}
a{color:inherit}
h2{font-size:19px;font-weight:700;letter-spacing:-.015em}
h3{font-size:11px;font-weight:600;color:var(--ink-2);text-transform:uppercase;
  letter-spacing:.07em;margin-bottom:9px}

/* ---------- barre de lecture + entete collante ---------- */
#prog{position:fixed;top:0;left:0;height:3px;width:0;z-index:40;
  background:linear-gradient(90deg,var(--accent-2),var(--accent))}
.topbar{position:sticky;top:0;z-index:20;border-bottom:1px solid var(--border);
  background:rgba(247,245,240,.88);backdrop-filter:blur(12px)}
.topbar .in{max-width:1400px;margin:auto;padding:9px 20px;display:flex;align-items:center;gap:12px}
.brand{display:flex;align-items:center;gap:10px;min-width:0}
.brand .mark{width:34px;height:34px;border-radius:11px;flex:none;display:grid;place-items:center;
  font-size:17px;background:linear-gradient(135deg,var(--accent-2),var(--accent));
  box-shadow:0 6px 16px rgba(226,87,31,.28)}
.brand b{display:block;font-size:14px;letter-spacing:-.01em}
.brand span{display:block;color:var(--muted);font-size:11.5px}
.tabs{margin-left:auto;display:flex;gap:3px;background:var(--raised);border-radius:10px;padding:3px;flex:none}
.tabs a,.tabs .here{font-size:12.5px;padding:6px 12px;border-radius:8px;text-decoration:none;color:var(--ink-2)}
.tabs a:hover{color:var(--ink)}
.tabs .here{background:var(--surface);color:var(--ink);font-weight:600;box-shadow:0 1px 3px rgba(0,0,0,.09)}

/* ---------- hero : la seance du jour ---------- */
.hero{position:relative;overflow:hidden;border-radius:20px;margin:16px 0 12px;color:#fff;
  background:linear-gradient(112deg,#f0761f 0%,#e2571f 44%,#b8365e 100%);
  box-shadow:0 18px 44px rgba(184,54,94,.20)}
.hero .lines{position:absolute;inset:-40% -20%;opacity:.16;pointer-events:none;
  background:repeating-linear-gradient(101deg,#fff 0 2px,transparent 2px 26px);
  animation:slide 9s linear infinite}
@keyframes slide{from{transform:translateX(0)}to{transform:translateX(26px)}}
.hero .in{position:relative;display:grid;gap:18px;padding:26px 26px 24px;
  grid-template-columns:1fr;align-items:center}
@media(min-width:900px){.hero .in{grid-template-columns:1fr auto;padding:30px 32px}}
.hero .kick{font-size:11.5px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;opacity:.85}
.hero .big{font-size:clamp(24px,3.4vw,36px);font-weight:800;letter-spacing:-.025em;line-height:1.1;margin-top:6px}
.hero .meta{margin-top:8px;font-size:13.5px;opacity:.92;max-width:62ch}
.hero .pills{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}
.hero .pill{display:inline-flex;align-items:center;gap:7px;background:rgba(255,255,255,.17);
  border:1px solid rgba(255,255,255,.28);border-radius:999px;padding:5px 12px;font-size:12.5px;font-weight:600}
.hero .pill i{width:8px;height:8px;border-radius:50%;background:#fff;flex:none}
.ring{display:flex;align-items:center;gap:16px}
.ring svg{width:104px;height:104px;flex:none}
.ring .rv{font-size:26px;font-weight:800;letter-spacing:-.03em}
.ring .rl{font-size:12px;opacity:.85;margin-top:2px;max-width:16ch}

/* ---------- nav des sections ---------- */
.secnav{position:sticky;top:53px;z-index:15;display:flex;gap:6px;overflow-x:auto;
  padding:10px 0 12px;scrollbar-width:none;
  background:linear-gradient(var(--page) 62%,rgba(247,245,240,0))}
.secnav::-webkit-scrollbar{display:none}
.secnav a{flex:none;font-size:12.5px;font-weight:600;text-decoration:none;color:var(--ink-2);
  background:var(--surface);border:1px solid var(--border);border-radius:999px;padding:6px 13px;
  transition:background .2s,color .2s,border-color .2s}
.secnav a:hover{border-color:var(--axis)}
.secnav a.on{background:var(--ink);color:var(--page);border-color:var(--ink)}

/* ---------- sections ---------- */
.sec{padding:6px 0 4px}
.sech{display:flex;align-items:center;gap:10px;margin:10px 0 14px}
.sech i{width:4px;height:22px;border-radius:2px;background:var(--accent);flex:none}
.sech .sub{color:var(--muted);font-size:12.5px;margin-left:2px}
@media(max-width:640px){.sech .sub{display:none}}

.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px;
  box-shadow:var(--shadow);transition:transform .25s ease,box-shadow .25s ease,opacity .5s ease}
.reveal{opacity:0;transform:translateY(16px);transition:opacity .55s ease,transform .55s cubic-bezier(.2,.7,.3,1)}
.reveal.in{opacity:1;transform:none}
.tile:hover,.chart-hover:hover{transform:translateY(-3px);box-shadow:0 14px 34px rgba(23,21,15,.10)}
svg polyline{transition:stroke-dashoffset 1.1s cubic-bezier(.3,.6,.2,1)}
svg .bar rect{transform-origin:bottom;transform:scaleY(0);transition:transform .6s cubic-bezier(.2,.7,.3,1)}
svg .bar rect.grown{transform:scaleY(1)}
svg .bar:hover rect{filter:brightness(1.1)}
@media(prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  .reveal{opacity:1;transform:none;transition:none}
  svg polyline{transition:none}
  svg .bar rect{transform:scaleY(1);transition:none}
  .card{transition:none}.hero .lines{animation:none}
  .ride img{transform:scaleX(-1)!important}
}
.tiles{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));margin-bottom:16px}
.tile{border-top:3px solid var(--tint,var(--border));padding:14px 15px}
.tile .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.tile .v{font-size:25px;font-weight:700;margin-top:3px;letter-spacing:-.02em}
.tile .v small{font-size:12px;color:var(--muted);font-weight:400}
.tile .st{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:var(--ink-2);margin-top:5px}
.tile .st i{width:7px;height:7px;border-radius:50%;display:inline-block;background:var(--tint,var(--axis));flex:none}
.charts{display:grid;gap:12px;grid-template-columns:1fr;margin-bottom:18px}
@media(min-width:780px){.charts{grid-template-columns:1fr 1fr}.card.wide{grid-column:1/-1}}
@media(min-width:1200px){.charts.c3{grid-template-columns:1fr 1fr 1fr}}
.explain{color:var(--muted);font-size:12px;line-height:1.5;margin:-2px 0 10px}
.explain b{color:var(--ink-2);font-weight:600}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--ink-2);margin-bottom:6px}
.legend i{display:inline-block;width:10px;height:3px;border-radius:2px;vertical-align:middle;margin-right:5px}
.legend i.sq{height:9px;width:9px;border-radius:2px}
.legend i.band{height:9px;width:9px;border-radius:2px;opacity:.22}
svg{width:100%;height:auto;display:block}
svg text{font:10px system-ui,sans-serif;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:8px 10px;text-align:left;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--ink-2)}
tr{transition:background .2s}
#seances tr:hover td{background:var(--raised)}
.ph{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.ph i{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none}
.note{color:var(--muted);font-size:12px}
.tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--page);
  font-size:11.5px;padding:5px 8px;border-radius:6px;opacity:0;transition:opacity .08s;
  font-variant-numeric:tabular-nums;z-index:35;white-space:nowrap;max-width:60vw}
.frise{display:flex;flex-wrap:wrap;gap:4px}
.frise b{width:15px;height:15px;border-radius:4px;display:block;background:var(--raised);
  border:1px solid var(--border);cursor:default}
.frise b.f-w{border-style:dashed}

/* ---------- planning interactif ---------- */
.plan-tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chips button{font:inherit;font-size:12.5px;font-weight:600;color:var(--ink-2);cursor:pointer;
  background:var(--surface);border:1px solid var(--border);border-radius:999px;padding:6px 13px;
  display:inline-flex;align-items:center;gap:6px;transition:background .18s,color .18s,border-color .18s}
.chips button:hover{border-color:var(--axis)}
.chips button i{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none}
.chips button[aria-pressed=true]{background:var(--ink);color:var(--page);border-color:var(--ink)}
.chips button .cnt{font-variant-numeric:tabular-nums;opacity:.6;font-weight:500}
.today-btn{margin-left:auto;font:inherit;font-size:12.5px;font-weight:600;cursor:pointer;
  background:var(--accent);color:#fff;border:0;border-radius:999px;padding:7px 15px}
.plan-tools.periode{margin-top:-4px}
.plan-tools .lbl{font-size:10.5px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
  color:var(--muted)}
.sel{font:inherit;font-size:12.5px;font-weight:600;color:var(--ink-2);cursor:pointer;
  background:var(--surface);border:1px solid var(--border);border-radius:999px;padding:6px 12px}
.sel:hover{border-color:var(--axis)}
.wk.hide{display:none}
.plan-layout{display:grid;gap:14px;grid-template-columns:1fr;align-items:start}
@media(min-width:1080px){.plan-layout{grid-template-columns:minmax(0,1fr) 330px}}
.cal{background:var(--surface);border:1px solid var(--border);border-radius:14px;
  padding:6px 14px 16px;box-shadow:var(--shadow)}
.dow{display:none;grid-template-columns:repeat(7,1fr);gap:7px;padding:10px 0 2px}
.dow span{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;text-align:center}
@media(min-width:900px){.dow{display:grid}}
.wk{padding:9px 0 4px;border-top:1px solid var(--grid)}
.wk:first-of-type{border-top:0}
.wk.now{background:linear-gradient(90deg,rgba(226,87,31,.07),transparent 60%);
  border-radius:10px;box-shadow:inset 3px 0 0 var(--accent);padding-left:10px;margin-left:-10px}
.wk-h{display:flex;align-items:center;gap:9px;margin-bottom:7px;flex-wrap:wrap}
.wk-h b{font-size:12px;letter-spacing:.05em;color:var(--ink-2)}
.wk-h .phc{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:600;
  color:var(--ink-2);background:var(--raised);border-radius:999px;padding:2px 9px}
.wk-h .phc i{width:7px;height:7px;border-radius:50%;display:inline-block}
.wk-h .rg{font-size:11.5px;color:var(--muted);margin-left:auto;font-variant-numeric:tabular-nums}
.days{display:grid;grid-template-columns:repeat(2,1fr);gap:7px}
@media(min-width:560px){.days{grid-template-columns:repeat(4,1fr)}}
@media(min-width:900px){.days{grid-template-columns:repeat(7,1fr)}}
.day{min-height:64px;border:1px solid var(--border);border-radius:10px;padding:6px 7px 7px;
  background:var(--page-2)}
.day.empty{background:transparent;border-style:dashed;opacity:.55}
@media(max-width:899px){.day.empty{display:none}}
.day.is-today{border-color:var(--accent);box-shadow:0 0 0 2px rgba(226,87,31,.16)}
.day .dn{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;
  font-variant-numeric:tabular-nums}
.sc{display:block;width:100%;text-align:left;font:inherit;font-size:12px;cursor:pointer;
  background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--c,var(--axis));
  border-radius:8px;padding:6px 7px;margin-top:5px;transition:transform .16s,box-shadow .16s,opacity .2s}
.sc:hover{transform:translateY(-2px);box-shadow:0 6px 16px rgba(23,21,15,.10)}
.sc .n{font-weight:600;line-height:1.28;display:block}
.sc .r{display:flex;align-items:center;gap:5px;margin-top:4px;font-size:10.5px;color:var(--muted)}
.sc .r i{width:7px;height:7px;border-radius:50%;display:inline-block;flex:none;background:var(--v,var(--axis))}
.sc.free{border-left-style:dotted;background:var(--raised)}
.sc.sel{box-shadow:0 0 0 2px var(--accent);transform:translateY(-2px)}
.sc.dim{opacity:.28}
.detail{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:17px;
  box-shadow:var(--shadow)}
@media(min-width:1080px){.detail{position:sticky;top:108px}}
.detail .dt{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}
.detail .dn{font-size:17px;font-weight:700;letter-spacing:-.015em;margin-top:4px;line-height:1.25}
.detail .vp{display:inline-flex;align-items:center;gap:7px;border-radius:999px;padding:5px 12px;
  font-size:12.5px;font-weight:600;margin-top:10px;color:#fff;background:var(--vc,var(--axis))}
.detail .txt{font-size:12.5px;color:var(--ink-2);line-height:1.55;margin-top:10px}
.detail dl{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;font-size:12.5px;margin-top:12px}
.detail dt{color:var(--muted)}
.detail dd{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.detail .sep{height:1px;background:var(--grid);margin:14px 0}
.detail .hint{font-size:12px;color:var(--muted);line-height:1.5}

/* ---------- bande du cycliste ---------- */
.rideband{position:relative;height:126px;margin:4px 0 18px;overflow:hidden;
  border-radius:14px;border:1px solid var(--border);background:var(--surface)}
.rideband .road{position:absolute;left:0;right:0;bottom:26px;height:1px;background:var(--axis);opacity:.55}
.rideband .road2{position:absolute;left:0;right:0;bottom:22px;height:1px;
  background:repeating-linear-gradient(90deg,var(--axis) 0 14px,transparent 14px 30px);opacity:.35}
.rideband .cap{position:absolute;left:16px;top:12px;color:var(--muted);font-size:11px;
  text-transform:uppercase;letter-spacing:.06em}
.ride{position:absolute;bottom:18px;width:150px;will-change:transform;pointer-events:none;transform:translateX(-30%)}
.ride img{width:100%;height:auto;display:block;transform:scaleX(-1);
  filter:drop-shadow(0 10px 14px rgba(0,0,0,.16))}
@media(max-width:760px){.rideband{height:96px}.ride{width:104px;bottom:14px}}
footer{color:var(--muted);font-size:11.5px;margin:20px 0 34px;line-height:1.6}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
</style></head><body>
<div id="prog"></div>

<div class="topbar"><div class="in">
  <span class="brand"><span class="mark">🚴</span>
    <span><b>Programme 12 semaines</b><span>FTP &amp; VO2max · 10 aout → 31 oct. 2026 · maj __MAJ__</span></span></span>
  <nav class="tabs"><span class="here">Tableau de bord</span><a href="/pub/">Page publique</a></nav>
</div></div>

<div class="wrap">

<section class="hero" id="aujourdhui">
  <span class="lines" aria-hidden="true"></span>
  <div class="in">
    <div id="heroNext"></div>
    <div class="ring" id="heroRing"></div>
  </div>
</section>

<nav class="secnav" id="secnav">
  <a href="#aujourdhui">Aujourd'hui</a>
  <a href="#forme">Forme &amp; charge</a>
  <a href="#recup">Recuperation</a>
  <a href="#moteur">Moteur &amp; volume</a>
  <a href="#planning">Planning</a>
  <a href="#seances">Seances &amp; decisions</a>
</nav>

<div class="tiles" id="tiles"></div>

<section class="sec" id="forme">
<div class="sech"><i></i><h2>Forme &amp; charge</h2><span class="sub">ce que le coach regarde en premier</span></div>
<div class="charts">
<div class="card wide"><h3>Condition, fatigue et forme — 8 semaines</h3>
<div class="explain">Le graphique central, celui de TrainingPeaks, en clair : la <b>condition</b> est ta moyenne d'entrainement sur 6 semaines (elle doit monter doucement — c'est ta progression) ; la <b>fatigue</b> est la moyenne des 7 derniers jours (elle fait le yoyo, c'est normal) ; la <b>forme</b> = condition − fatigue : negative en pleine charge, positive quand tu recuperes — c'est la qu'on performe.</div>
<div class="legend"><span><i style="background:var(--s1)"></i>Condition (6 sem.)</span><span><i style="background:var(--s2)"></i>Fatigue (7 j)</span><span><i style="background:var(--s3)"></i>Forme</span><span><i class="band" style="background:var(--good)"></i>Zone de performance</span><span><i class="band" style="background:var(--crit)"></i>Zone de surmenage</span></div>
<div id="c-pmc"></div></div>

<div class="card"><h3>Charge par semaine</h3>
<div class="explain">La dose d'entrainement hebdomadaire, tous sports confondus. Elle doit monter progressivement, avec un creux les semaines 4, 8 et 12.</div>
<div id="c-week"></div></div>

<div class="card"><h3>Ratio de charge</h3>
<div class="explain">Fatigue ÷ condition. Entre 0,8 et 1,3 : tu progresses sans risque. Au-dessus de 1,5 : danger blessure ou surmenage.</div>
<div class="legend"><span><i class="band" style="background:var(--good)"></i>Zone saine 0,8 – 1,3</span></div>
<div id="c-ratio"></div></div>
</div>
</section>

<section class="sec" id="recup">
<div class="sech"><i></i><h2>Recuperation</h2><span class="sub">la nuit decide de la seance du matin</span></div>
<div class="charts c3">
<div class="card"><h3>Sommeil — 21 nuits</h3>
<div class="explain">Chaque barre est une nuit, empilee du plus reparateur au plus leger. Le <b>profond</b> repare les jambes, le <b>REM</b> la tete. La ligne pointillee marque 7 h 30 de sommeil ; le segment le plus pale, tout en haut, ce sont les <b>reveils</b> — ils s'ajoutent a la barre sans compter comme du sommeil.</div>
<div class="legend"><span><i class="sq" style="background:var(--sl1)"></i>Profond</span><span><i class="sq" style="background:var(--sl2)"></i>Leger</span><span><i class="sq" style="background:var(--sl3)"></i>REM</span><span><i class="sq" style="background:var(--sl4)"></i>Eveille</span></div>
<div id="c-sleep"></div></div>

<div class="card"><h3>Coeur pendant la nuit — 21 nuits</h3>
<div class="explain">La zone claire va du minimum au maximum de la nuit, la ligne est la moyenne. Une moyenne qui grimpe alors que tu dors autant = digestion, alcool, stress ou infection.</div>
<div class="legend"><span><i style="background:var(--s1)"></i>Moyenne</span><span><i class="band" style="background:var(--s1)"></i>Min → max</span></div>
<div id="c-nuit"></div></div>

<div class="card"><h3>HRV nocturne — 8 semaines</h3>
<div class="explain">Ton systeme nerveux. Proche de la ligne pointillee (ta norme) = tout va bien ; nettement en dessous plusieurs jours = leve le pied.</div>
<div class="legend"><span><i style="background:var(--s1)"></i>HRV</span><span><i style="background:var(--muted)"></i>Ta norme</span></div>
<div id="c-hrv"></div></div>

<div class="card"><h3>FC de repos — 8 semaines</h3>
<div class="explain">Plus c'est bas, mieux c'est. Une hausse soudaine de 5 a 8 bpm annonce une fatigue ou une infection qui couve.</div>
<div id="c-rhr"></div></div>
</div>
</section>

<section class="sec" id="moteur">
<div class="sech"><i></i><h2>Moteur &amp; volume</h2><span class="sub">ce qui progresse sur douze semaines</span></div>
<div class="charts c3">
<div class="card"><h3>VO2max</h3>
<div class="explain">La cylindree du moteur : le volume d'oxygene que tu sais consommer. La montre ne la recalcule que les jours de seance assez intense, d'ou les trous.</div>
<div id="c-vo2"></div></div>

<div class="card"><h3>Endurance de base</h3>
<div class="explain">L'indice d'endurance de la montre (<b>stamina</b>) : le fond de caisse accumule. Il monte lentement et se perd vite — c'est lui qui tient sur les longues sorties.</div>
<div id="c-stam"></div></div>

<div class="card"><h3>Volume par sport — 10 semaines</h3>
<div class="explain">Combien d'heures par semaine, et dans quoi elles sont passees. Le coach tient compte de <b>tout</b> : la course et la natation fatiguent aussi les jambes.</div>
<div class="legend" id="lg-vol"></div>
<div id="c-vol"></div></div>
</div>
</section>

<div class="rideband" aria-hidden="true">
  <span class="cap">10 aout → 31 octobre 2026</span>
  <span class="road"></span><span class="road2"></span>
  <span class="ride" id="ride"><img src="/img/bike.webp" width="1264" height="848" alt="" loading="lazy"></span>
</div>

<section class="sec" id="planning">
<div class="sech"><i></i><h2>Planning</h2><span class="sub">clique une seance pour son verdict et ce qui a ete fait</span></div>
<div class="plan-tools">
  <div class="chips" id="planFilters"></div>
  <button class="today-btn" id="btnToday">Aller a cette semaine</button>
</div>
<div class="plan-tools periode">
  <span class="lbl">Periode</span>
  <div class="chips" id="planMois"></div>
  <select class="sel" id="planSem" aria-label="Choisir une semaine"></select>
</div>
<div class="plan-layout">
  <div class="cal" id="cal">
    <div class="dow"><span>lun</span><span>mar</span><span>mer</span><span>jeu</span><span>ven</span><span>sam</span><span>dim</span></div>
    <div id="weeks"></div>
  </div>
  <div class="detail" id="detail"></div>
</div>
</section>

<section class="sec" id="seances">
<div class="sech"><i></i><h2>Seances &amp; decisions</h2><span class="sub">ce qui est reellement sorti de la montre</span></div>
<div class="charts">
<div class="card wide"><h3>Les decisions du coach — programme complet</h3>
<div class="explain">Une pastille par seance prevue. Chaque matin de seance, le coach lit la nuit et tranche : maintenue, allegee, ou remplacee par du repos. Les pastilles vides sont les seances encore a venir.</div>
<div class="legend"><span><i class="sq" style="background:var(--good)"></i>Maintenue</span><span><i class="sq" style="background:var(--warn)"></i>Allegee</span><span><i class="sq" style="background:var(--crit)"></i>Repos impose</span><span><i class="sq" style="background:var(--raised);border:1px solid var(--border)"></i>A venir</span></div>
<div class="frise" id="frise"></div></div>
<div class="card wide"><h3>Dernieres seances — tous sports</h3>
<div style="overflow-x:auto"><table id="seancesT"></table></div></div>
</div>
</section>

<footer>Donnees COROS &amp; iGPSport · calendrier abonne, verdicts quotidiens et cette page generes automatiquement chaque matin.</footer>
</div>
<div class="tip" id="tip"></div>
<script>
const D = __DATA__;
const fdm = d => d ? String(d).replace(/-/g,"").slice(6,8)+"/"+String(d).replace(/-/g,"").slice(4,6) : "";
const fiso = d => d.split("-").reverse().join("/").slice(0,5);
const hm = m => Math.floor(m/60)+"h"+String(Math.round(m%60)).padStart(2,"0");
const hdec = h => h>=1 ? (Math.round(h*10)/10)+" h" : Math.round(h*60)+" min";
const GOOD="var(--good)", WARN="var(--warn)", CRIT="var(--crit)", NEU="var(--axis)";
const S=["var(--s1)","var(--s2)","var(--s3)","var(--s4)"];
const SL=["var(--sl1)","var(--sl2)","var(--sl3)","var(--sl4)"];
const JOURS=["lun","mar","mer","jeu","ven","sam","dim"];
const MOIS=["janv.","fevr.","mars","avril","mai","juin","juil.","aout","sept.","oct.","nov.","dec."];
const today = new Date().toISOString().slice(0,10);

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
/* Pas de litteral regex ici : un guillemet dans une regex trompe le
   controleur statique des pages (check-page-js.py). */
function esc(s){ return String(s??"").split("&").join("&amp;")
  .split("<").join("&lt;").split(">").join("&gt;").split('"').join("&quot;"); }
function pdate(d){ const [y,m,j] = d.split("-").map(Number); return new Date(y, m-1, j); }
function idate(dt){ return dt.getFullYear()+"-"+String(dt.getMonth()+1).padStart(2,"0")+"-"+String(dt.getDate()).padStart(2,"0"); }
function longue(d){ const dt = pdate(d);
  return JOURS[(dt.getDay()+6)%7]+" "+dt.getDate()+" "+MOIS[dt.getMonth()]; }

const dd = D.daily, lastD = dd[dd.length-1] || {};
const tsbNow = (lastD.cti!=null && lastD.ati!=null) ? Math.round(lastD.cti-lastD.ati) : null;
const rhrs = dd.map(r=>r.rhr).filter(v=>v!=null);
const rhrMean = rhrs.length ? rhrs.reduce((a,b)=>a+b,0)/rhrs.length : null;
const VD = {"✅":[GOOD,"Feu vert"],"⚠️":[WARN,"Seance allegee"],"🛑":[CRIT,"Remplacee par du repos"]};
const PHC = {Base:"var(--s1)", Seuil:"var(--s2)", VO2max:"var(--s4)", Recup:"var(--s3)",
             Reprise:"var(--s3)", Affutage:"var(--accent)"};

/* ---------- tuiles ---------- */
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

/* ---------- hero : seance du jour + avancement ---------- */
safe("heroNext", "Seance du jour", ()=>{
  const n = D.next;
  const fin = D.plan.length ? D.plan[D.plan.length-1] : null;
  const jfin = fin ? Math.max(0, Math.round((pdate(fin.date)-pdate(today))/86400000)) : 0;
  let h = "";
  if (D.done_today) {
    h += `<div class="kick">Aujourd'hui — fait</div><div class="big">${esc(D.done_today)}</div>`;
    if (n) h += `<div class="meta">Prochaine seance : <b>${esc(n.seance)}</b> — ${longue(n.date)}.</div>`;
  } else if (n) {
    const when = n.date===today ? "Seance du jour" : "Prochaine seance — "+longue(n.date);
    const lab = (VD[n.emoji]||[null,null])[1];
    h += `<div class="kick">${when}</div><div class="big">${esc(n.seance)}</div>`;
    h += `<div class="pills"><span class="pill"><i></i>${esc(n.phase)}</span>` +
         (lab ? `<span class="pill"><i></i>${lab}</span>` : "") + `</div>`;
    if (n.note) h += `<div class="meta">${esc(n.note)}</div>`;
  } else {
    h += `<div class="kick">Programme</div><div class="big">Programme termine.</div>`;
  }
  h += `<div class="meta" style="opacity:.8;margin-top:10px">J−${jfin} avant ${esc(fin ? fin.seance.replace(/^[A-Z]{1,2}\d*\s+/,"") : "la fin")}</div>`;
  document.getElementById("heroNext").innerHTML = h;

  const pct = D.total_seances ? D.faites/D.total_seances : 0;
  const R = 44, C = 2*Math.PI*R;
  document.getElementById("heroRing").innerHTML =
   `<svg viewBox="0 0 104 104" aria-hidden="true">
      <circle cx="52" cy="52" r="${R}" fill="none" stroke="rgba(255,255,255,.28)" stroke-width="9"/>
      <circle id="ringArc" cx="52" cy="52" r="${R}" fill="none" stroke="#fff" stroke-width="9"
        stroke-linecap="round" transform="rotate(-90 52 52)"
        stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${C.toFixed(1)}"
        style="transition:stroke-dashoffset 1.2s cubic-bezier(.3,.7,.2,1)"/>
    </svg>
    <div><div class="rv">${D.faites} / ${D.total_seances}</div>
    <div class="rl">seances du programme passees</div></div>`;
  requestAnimationFrame(()=>requestAnimationFrame(()=>{
    const arc = document.getElementById("ringArc");
    if (arc) arc.setAttribute("stroke-dashoffset", (C*(1-pct)).toFixed(1));
  }));
});

/* ---------- graphiques ---------- */
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
  for (const g of [lo,(lo+hi)/2,hi]) s.push(`<line x1="${PL}" x2="${W-PR}" y1="${Y(g)}" y2="${Y(g)}" stroke="var(--grid)"/>`+
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
      x.push(`<rect x="${bx}" y="${y0}" width="${w}" height="${Math.max(h0-2, 1)}" fill="${colors[k]}" rx="3"/>`);
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
safe("c-pmc", "Condition, fatigue et forme", ()=>{lineChart("c-pmc", dts, [
  {v: tsb, c: S[2], n: "Forme"},
  {v: dd.map(r=>r.ati), c: S[1], n: "Fatigue"},
  {v: dd.map(r=>r.cti), c: S[0], n: "Condition"}],
  {w: 940, h: 220, zero: 0, include: [10, -35],
   bands: [{lo: 5, hi: 999, c: "var(--good)"}, {lo: -999, hi: -30, c: "var(--crit)"}]});});
/* Le ratio vit entre 0 et 2 : la marge de +/- 2 des autres courbes ecrasait
   la zone saine 0,8-1,3 sur 7 % de la hauteur. Echelle posee a la main. */
safe("c-ratio", "Ratio de charge", ()=>{
  const RMAX = Math.max(1.8, Math.ceil((Math.max(...dd.map(r=>r.ratio).filter(v=>v!=null), 1.3)+0.2)*10)/10);
  lineChart("c-ratio", dts, [{v:dd.map(r=>r.ratio), c:S[0], n:"ratio"}], {
    lo:0, hi:RMAX, ticks:[0, 0.8, 1.3, RMAX],
    bands:[{lo:0.8, hi:1.3, c:"var(--good)"}], fmt:v=>(+v).toFixed(1)});
});
safe("c-hrv", "HRV nocturne", ()=>{lineChart("c-hrv", dts, [
  {v: dd.map(r=>r.base), c:"var(--muted)", d:"4 3", n:"norme"},
  {v: dd.map(r=>r.hrv), c:S[0], n:"HRV"}]);});
safe("c-rhr", "FC de repos", ()=>{lineChart("c-rhr", dts, [{v: dd.map(r=>r.rhr), c:S[0], n:"FC"}]);});
safe("c-vo2", "VO2max", ()=>{lineChart("c-vo2", dts, [{v: dd.map(r=>r.vo2), c:S[0], n:"VO2max"}], {every:14});});
safe("c-stam", "Endurance de base", ()=>{lineChart("c-stam", dts, [{v: dd.map(r=>r.stam), c:S[0], n:"endurance"}], {every:14, fmt:v=>Math.round(v)});});
safe("c-week", "Charge par semaine", ()=>{barChart("c-week", D.weeks.map(r=>({d:r.w, lbl:r.w, v:r.load, tip:r.w+" · charge "+r.load})), {lab:1});});

const S21 = D.sleep.slice(-21);
const SMAX = Math.max(600, Math.ceil(Math.max(...S21.map(r=>r.deep+r.light+r.rem+r.awake), 0)/60)*60);
safe("c-sleep", "Sommeil", ()=>{
stackChart("c-sleep", S21.map(r=>({lbl:fdm(r.d),
  segs:[r.deep, r.light, r.rem, r.awake],
  tip:`${fdm(r.d)} · ${hm(r.min)} de sommeil — profond ${hm(r.deep)} · leger ${hm(r.light)} · REM ${hm(r.rem)} · eveille ${hm(r.awake)}`})),
  SL, {max:SMAX, ticks:[0,120,240,360,480,600,720], fmt:v=>Math.round(v/60)+"h", ref:450, lab:3});
});
safe("c-nuit", "Coeur pendant la nuit", ()=>{bandChart("c-nuit", S21.map(r=>r.d), S21.map(r=>r.hrmin), S21.map(r=>r.hrmax), S21.map(r=>r.hr));});

const FAM = D.familles;
document.getElementById("lg-vol").innerHTML = FAM.map((f,i)=>
  `<span><i class="sq" style="background:${S[i]}"></i>${f}</span>`).join("");
safe("c-vol", "Volume par sport", ()=>{
stackChart("c-vol", D.volume.map(r=>({lbl:r.w,
  segs:FAM.map(f=>r[f]||0),
  tip:`${r.w} · ${hdec(FAM.reduce((a,f)=>a+(r[f]||0),0))} au total — ${r.detail}`})),
  S, {fmt:v=>Math.round(v)+" h", lab:1});
});

safe("frise", "Decisions du coach", ()=>{
document.getElementById("frise").innerHTML = D.plan.map(p=>{
  const v = VD[p.emoji];
  const etat = v ? v[1] : (p.date<=today ? "sans verdict" : "a venir");
  return `<b class="${p.emoji==="⚠️"?"f-w":""}" style="background:${v?v[0]:"var(--raised)"}"
    data-tip="${esc(fiso(p.date)+" · "+p.seance+" · "+etat)}"></b>`;
}).join("");
document.querySelectorAll("#frise b").forEach(b=>{
  b.addEventListener("mousemove", e=>showTip(e, b.dataset.tip));
  b.addEventListener("mouseleave", hideTip);
});
});

/* ---------- table des seances ---------- */
const puis = {};
for (const a of D.acts) if (a.date) puis[String(a.date).slice(0,10)] = a;
safe("seancesT", "Dernieres seances", ()=>{
document.getElementById("seancesT").innerHTML =
 "<tr><th>Date</th><th>Sport</th><th>Seance</th><th>Duree</th><th>Distance</th><th>FC</th><th>Charge</th><th>NP</th><th>IF</th></tr>" +
 (D.seances.length ? D.seances.map(a=>{
  const p = puis[a.d] || {};
  const iff = p.np ? (p.np/D.ftp).toFixed(2) : "";
  const ci = Math.max(0, FAM.indexOf(a.fam));
  return `<tr><td class="num">${fiso(a.d)}</td>
   <td><span class="ph"><i style="background:${S[ci]}"></i>${esc(a.sport||a.fam)}</span></td>
   <td>${esc(a.nom)}</td>
   <td class="num">${a.dur?hm(Math.round(a.dur/60)):""}</td>
   <td class="num">${a.km?a.km.toFixed(1)+" km":""}</td>
   <td class="num">${a.hr?a.hr+" bpm":""}</td>
   <td class="num">${a.load??""}</td>
   <td class="num">${p.np?Math.round(p.np)+" W":""}</td>
   <td class="num">${iff}</td></tr>`;
 }).join("") : "<tr><td colspan=9 class=note>Aucune seance enregistree.</td></tr>");
});

/* ---------- planning interactif ----------
   Le programme est regroupe par semaine (le code S01..S12 porte par la phase),
   chaque semaine est une ligne de sept jours. Une seance prevue est un bouton :
   il ouvre le detail — verdict du coach, sa note, et ce que la montre a
   reellement enregistre ce jour-la. Les sorties hors programme apparaissent
   en pointille pour que le calendrier dise la verite, pas juste l'intention. */
const faits = {};
(D.toutes||[]).forEach(s=>{ (faits[s.d] = faits[s.d] || []).push(s); });

function resume(a){
  const bits = [];
  if (a.dur) bits.push(hm(Math.round(a.dur/60)));
  if (a.km) bits.push(a.km.toFixed(1)+" km");
  return bits.join(" · ");
}
const FILTRES = [
  ["tout", "Tout le programme", null],
  ["avenir", "A venir", null],
  ["ok", "Maintenues", GOOD],
  ["warn", "Allegees", WARN],
  ["stop", "Repos impose", CRIT],
];
function match(p, f){
  if (f==="tout") return true;
  if (f==="avenir") return p.date > today || (p.date===today && !p.emoji);
  return (f==="ok" && p.emoji==="✅") || (f==="warn" && p.emoji==="⚠️") || (f==="stop" && p.emoji==="🛑");
}
let filtre = "tout", mois = "tout", semChoisie = "", choisi = null;

function detail(key){
  const el = document.getElementById("detail");
  const p = key!=null && key.p!=null ? D.plan[key.p] : null;
  const d = p ? p.date : (key ? key.d : null);
  if (!d){ el.innerHTML = `<div class="dt">Planning</div>
    <div class="dn">Choisis une seance</div>
    <div class="hint" style="margin-top:10px">Chaque case du calendrier ouvre ici le verdict du matin,
      la consigne du coach et ce que la montre a enregistre ce jour-la.</div>`; return; }
  const v = p ? VD[p.emoji] : null;
  const etat = v ? v[1] : (p ? (p.date<=today ? "Sans verdict" : "A venir") : "Hors programme");
  const col = v ? v[0] : (p && p.date>today ? "var(--s1)" : "var(--axis)");
  const ph = p ? (p.phase.split(" ")[1]||"") : "";
  let h = `<div class="dt">${longue(d)}${p?" · "+esc(p.phase):""}</div>
    <div class="dn">${esc(p ? p.seance : "Sortie hors programme")}</div>
    <span class="vp" style="--vc:${col}">${etat}</span>`;
  if (p && p.note) h += `<div class="txt">${esc(p.note)}</div>`;
  const act = faits[d] || [];
  h += `<div class="sep"></div>`;
  if (act.length){
    h += `<div class="dt">Realise</div>`;
    act.forEach(a=>{
      const np = puis[a.d];
      h += `<dl><dt>${esc(a.sport||a.fam)}</dt><dd>${resume(a)||"–"}</dd>`;
      if (a.dplus) h += `<dt>Denivele</dt><dd>${Math.round(a.dplus)} m</dd>`;
      if (a.hr) h += `<dt>FC moyenne</dt><dd>${a.hr} bpm</dd>`;
      if (a.load!=null) h += `<dt>Charge</dt><dd>${a.load}</dd>`;
      if (np && np.np) h += `<dt>NP · IF</dt><dd>${Math.round(np.np)} W · ${(np.np/D.ftp).toFixed(2)}</dd>`;
      h += `</dl>`;
    });
  } else {
    h += `<div class="hint">${d<=today ? "Rien d'enregistre par la montre ce jour-la." : "Seance a venir."}</div>`;
  }
  el.innerHTML = h;
}

safe("weeks", "Planning", ()=>{
  const sem = [];
  D.plan.forEach((p,i)=>{
    const code = p.phase.split(" ")[0]||"S?", lab = p.phase.split(" ")[1]||"";
    let w = sem.find(x=>x.code===code);
    if (!w){ const d0 = pdate(p.date); d0.setDate(d0.getDate()-((d0.getDay()+6)%7));
      w = {code, lab, lundi:d0, items:[]}; sem.push(w); }
    w.items.push({p, i});
  });
  const html = sem.map(w=>{
    const jours = [];
    let cur = false;
    for (let k=0;k<7;k++){
      const dt = new Date(w.lundi); dt.setDate(dt.getDate()+k);
      const key = idate(dt);
      if (key===today) cur = true;
      const prevus = w.items.filter(x=>x.p.date===key);
      const libres = (faits[key]||[]).length && !prevus.length;
      let cell = `<div class="day${prevus.length||libres?"":" empty"}${key===today?" is-today":""}">
        <div class="dn">${JOURS[k]} ${String(dt.getDate()).padStart(2,"0")}</div>`;
      prevus.forEach(({p,i})=>{
        const v = VD[p.emoji];
        const etat = v ? v[1] : (p.date<=today ? "sans verdict" : "a venir");
        const fait = faits[key] ? resume(faits[key][0]) : "";
        cell += `<button class="sc" data-p="${i}" data-d="${key}"
          style="--c:${PHC[p.phase.split(" ")[1]]||"var(--axis)"};--v:${v?v[0]:"var(--axis)"}"
          title="${esc(p.seance+" · "+etat)}">
          <span class="n">${esc(p.seance)}</span>
          <span class="r"><i></i>${fait || etat}</span></button>`;
      });
      if (libres) (faits[key]||[]).forEach(a=>{
        cell += `<button class="sc free" data-d="${key}" title="Sortie hors programme">
          <span class="n">${esc(a.sport||a.fam)}</span>
          <span class="r"><i style="background:var(--muted)"></i>${resume(a)||"hors programme"}</span></button>`;
      });
      jours.push(cell+`</div>`);
    }
    const fin = new Date(w.lundi); fin.setDate(fin.getDate()+6);
    const mkey = d => d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0");
    const mois2 = mkey(w.lundi)===mkey(fin) ? [mkey(w.lundi)] : [mkey(w.lundi), mkey(fin)];
    return `<div class="wk${cur?" now":""}" data-code="${w.code}" data-mois="${mois2.join(" ")}">
      <div class="wk-h"><b>${w.code}</b>
        <span class="phc"><i style="background:${PHC[w.lab]||"var(--axis)"}"></i>${esc(w.lab)}</span>
        <span class="rg">${w.lundi.getDate()} → ${fin.getDate()} ${MOIS[fin.getMonth()]}</span></div>
      <div class="days">${jours.join("")}</div></div>`;
  }).join("");
  document.getElementById("weeks").innerHTML = html;

  document.getElementById("planFilters").innerHTML = FILTRES.map(([k,lab,c])=>{
    const n = k==="tout" ? D.plan.length : D.plan.filter(p=>match(p,k)).length;
    return `<button data-f="${k}" aria-pressed="${k===filtre}">${c?`<i style="background:${c}"></i>`:""}${lab}<span class="cnt">${n}</span></button>`;
  }).join("");

  function applique(){
    document.querySelectorAll("#weeks .sc[data-p]").forEach(b=>{
      b.classList.toggle("dim", !match(D.plan[+b.dataset.p], filtre));
    });
    document.querySelectorAll("#weeks .sc.free").forEach(b=>b.classList.toggle("dim", filtre!=="tout"));
  }
  /* Periode : des pastilles de mois et une liste de semaines. Le programme
     tient sur quatre mois et douze semaines — assez pour tout afficher, trop
     pour tout lire d'un coup. */
  const mkey = d => d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0");
  const moisDispo = [];
  sem.forEach(w=>{
    const fin = new Date(w.lundi); fin.setDate(fin.getDate()+6);
    [mkey(w.lundi), mkey(fin)].forEach(m=>{ if (!moisDispo.includes(m)) moisDispo.push(m); });
  });
  moisDispo.sort();
  document.getElementById("planMois").innerHTML =
    [["tout","Tous les mois"]].concat(moisDispo.map(m=>[m, MOIS[+m.slice(5)-1]]))
      .map(([k,lab])=>`<button data-m="${k}" aria-pressed="${k===mois}">${lab}</button>`).join("");
  document.getElementById("planSem").innerHTML =
    `<option value="">Toutes les semaines</option>` + sem.map(w=>{
      const fin = new Date(w.lundi); fin.setDate(fin.getDate()+6);
      return `<option value="${w.code}">${w.code} · ${w.lundi.getDate()} → ${fin.getDate()} ${MOIS[fin.getMonth()]}</option>`;
    }).join("");

  function periode(){
    document.querySelectorAll("#weeks .wk").forEach(w=>{
      const ok = semChoisie ? w.dataset.code===semChoisie
                            : (mois==="tout" || w.dataset.mois.split(" ").includes(mois));
      w.classList.toggle("hide", !ok);
    });
  }
  document.querySelectorAll("#planMois button").forEach(b=>{
    b.addEventListener("click", ()=>{
      mois = b.dataset.m; semChoisie = "";
      document.getElementById("planSem").value = "";
      document.querySelectorAll("#planMois button").forEach(x=>x.setAttribute("aria-pressed", x===b));
      periode();
    });
  });
  document.getElementById("planSem").addEventListener("change", e=>{
    semChoisie = e.target.value;
    periode();
  });

  document.querySelectorAll("#planFilters button").forEach(b=>{
    b.addEventListener("click", ()=>{
      filtre = b.dataset.f;
      document.querySelectorAll("#planFilters button").forEach(x=>x.setAttribute("aria-pressed", x===b));
      applique();
    });
  });
  document.querySelectorAll("#weeks .sc").forEach(b=>{
    b.addEventListener("click", ()=>{
      if (choisi) choisi.classList.remove("sel");
      choisi = b; b.classList.add("sel");
      detail({p: b.dataset.p!=null && b.dataset.p!=="" ? +b.dataset.p : null, d: b.dataset.d});
      if (innerWidth < 1080) document.getElementById("detail").scrollIntoView({behavior:"smooth", block:"nearest"});
    });
  });
  document.getElementById("btnToday").addEventListener("click", ()=>{
    // la semaine en cours peut etre masquee par le tri : on remet tout a plat
    mois = "tout"; semChoisie = "";
    document.getElementById("planSem").value = "";
    document.querySelectorAll("#planMois button").forEach(x=>x.setAttribute("aria-pressed", x.dataset.m==="tout"));
    periode();
    const w = document.querySelector("#weeks .wk.now") || document.querySelector("#weeks .wk");
    if (w) w.scrollIntoView({behavior:"smooth", block:"center"});
  });

  // ouverture par defaut sur la seance du jour, sinon la prochaine
  const cible = D.plan.findIndex(p=>p.date===today);
  const suiv = D.next ? D.plan.findIndex(p=>p.date===D.next.date && p.seance===D.next.seance) : -1;
  const idx = cible>=0 ? cible : suiv;
  if (idx>=0){
    const b = document.querySelector(`#weeks .sc[data-p="${idx}"]`);
    if (b){ b.classList.add("sel"); choisi = b; }
    detail({p: idx});
  } else detail(null);
  applique();
  periode();
});

/* ---------- animations et reperage dans la page ---------- */
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

addEventListener("scroll", ()=>{
  const h = document.documentElement;
  const p = h.scrollTop / Math.max(1, h.scrollHeight - h.clientHeight);
  document.getElementById("prog").style.width = (p*100).toFixed(1)+"%";
}, {passive:true});

(function(){
  const liens = [...document.querySelectorAll(".secnav a")];
  const secs = liens.map(a=>document.querySelector(a.getAttribute("href"))).filter(Boolean);
  if (!secs.length) return;
  const io = new IntersectionObserver(es=>{
    es.forEach(e=>{
      if (!e.isIntersecting) return;
      liens.forEach(a=>a.classList.toggle("on", a.getAttribute("href")==="#"+e.target.id));
    });
  }, {rootMargin:"-45% 0px -50% 0px"});
  secs.forEach(s=>io.observe(s));
})();

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
  document.querySelectorAll(".card,.tile,.cal,.detail").forEach(el=>{ el.classList.add("reveal");
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
    const m = el.childNodes[0] && el.childNodes[0].nodeValue && el.childNodes[0].nodeValue.match(/^[+-]?\d+$/);
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
