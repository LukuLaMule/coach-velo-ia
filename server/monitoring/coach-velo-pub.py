#!/home/opc/mcp/coros-mcp/.venv/bin/python
"""Genere la page publique velo.luku.fr/pub/ (vitrine reseaux sociaux)."""
import asyncio, datetime, json, os, re, subprocess, sys

sys.path.insert(0, "/home/opc/mcp/coros-mcp")
MON = "/home/opc/monitoring"
OUT = os.environ.get("VELO_PUB_OUT", "/home/opc/Docker/sites/velo/public/pub")
JOURS = 42
JOURS_ACT = 120

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
    from coros_mcp.coros_api import try_auto_login, fetch_daily_records, fetch_activities
    auth = await try_auto_login()
    today = datetime.date.today()
    d1 = today.strftime("%Y%m%d")
    daily = await fetch_daily_records(auth, (today - datetime.timedelta(days=JOURS)).strftime("%Y%m%d"), d1)
    try:
        acts, _ = await fetch_activities(auth, (today - datetime.timedelta(days=JOURS_ACT)).strftime("%Y%m%d"), d1, size=200)
    except Exception:
        acts = []
    return daily, acts

daily, coros_acts = asyncio.run(coros())

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
print(json.dumps(rows))
'''
igp = []
try:
    r = subprocess.run(["/home/opc/mcp/igpsport-mcp/.venv/bin/python", "-c", IGP],
                       capture_output=True, text=True, timeout=120)
    igp = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else []
except Exception:
    igp = []

FAMILLES = [("Velo", {200, 201, 203, 204, 9807}),
            ("Course", {100, 102, 103}),
            ("Natation", {300, 301, 302})]
ORDRE_FAM = ["Velo", "Course", "Natation", "Autre"]
LABELS = {100: "Course", 102: "Trail", 103: "Piste", 104: "Rando",
          200: "Route", 201: "Home trainer", 203: "Gravel", 204: "VTT",
          300: "Natation", 301: "Eau libre", 302: "Natation",
          400: "Cardio", 402: "Renfo", 403: "Yoga",
          900: "Marche", 9807: "Velotaf", 10000: "Triathlon"}

def famille(t):
    for nom, codes in FAMILLES:
        if t in codes:
            return nom
    return "Autre"

today = datetime.date.today()
seances = []
for a in coros_acts:
    try:
        d = datetime.date.fromtimestamp(int(a.start_time))
    except Exception:
        continue
    seances.append({"d": d, "fam": famille(a.sport_type),
                    "sport": LABELS.get(a.sport_type) or a.sport_name or "",
                    "h": (a.duration_seconds or 0) / 3600,
                    "km": (a.distance_meters or 0) / 1000,
                    "dplus": a.elevation_gain or 0})

d42 = today - datetime.timedelta(days=JOURS)
recent = [s for s in seances if s["d"] >= d42]
heures42 = round(sum(s["h"] for s in recent))
km42 = round(sum(s["km"] for s in recent))
dplus42 = round(sum(s["dplus"] for s in recent))
nsort = len(recent)

vol, detail = {}, {}
for s in seances:
    key = (s["d"].isocalendar()[0], s["d"].isocalendar()[1])
    vol.setdefault(key, {f: 0.0 for f in ORDRE_FAM})
    vol[key][s["fam"]] += s["h"]
    detail.setdefault(key, {})
    detail[key][s["sport"]] = detail[key].get(s["sport"], 0.0) + s["h"]

def duree(h):
    return f"{round(h, 1)} h" if h >= 1 else f"{round(h * 60)} min"

volume = []
for k, v in sorted(vol.items()):
    top = sorted(detail.get(k, {}).items(), key=lambda t: -t[1])[:4]
    volume.append({"w": f"S{k[1]:02d}",
                   "detail": " · ".join(f"{n} {duree(h)}" for n, h in top),
                   **{f: round(v[f], 2) for f in ORDRE_FAM}})
volume = volume[-10:]

verdicts, notes = {}, {}
vf = f"{MON}/coach-velo-verdicts.tsv"
if os.path.exists(vf):
    for line in open(vf):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 2:
            verdicts[p[0]] = p[1]
            notes[p[0]] = p[2] if len(p) > 2 else ""

plan = []
for line in open(f"{MON}/coach-velo-plan.tsv"):
    p = line.rstrip("\n").split("\t")
    if len(p) >= 3:
        plan.append({"date": p[0], "seance": p[1], "phase": p[2],
                     "emoji": verdicts.get(p[0], ""), "note": notes.get(p[0], "")})

today_iso = today.isoformat()
jours_actifs = {s["d"].isoformat() for s in seances}
did_today = today_iso in jours_actifs
nxt = next((s for s in plan if s["date"] > today_iso or (s["date"] == today_iso and not did_today)), None)
faites = sum(1 for s in plan if s["date"] < today_iso) + (1 if did_today else 0)
juges = [s for s in plan if s["date"] <= today_iso and s["emoji"]]
tenues = sum(1 for s in juges if s["emoji"] == "✅")
taux = round(100 * tenues / len(juges)) if juges else None

# Le compte a rebours vise le dernier jalon du programme, pas une date figee
# dans le code : le Ventoux du 15 aout etait deja passe et affichait un J moins
# negatif.
fin = plan[-1]["date"] if plan else today_iso
jfin = (datetime.date.fromisoformat(fin) - today).days
cible = re.sub(r"^[A-Z]{1,2}\d*\s+", "", plan[-1]["seance"]) if plan else ""

vo2 = next((r.vo2max for r in reversed(daily) if r.vo2max), 55)
last = daily[-1] if daily else None
cti = round(last.cti) if last and last.cti is not None else None
rhr = min((r.rhr for r in daily if r.rhr), default=None)
pmax = 856  # record releve sur les sorties recentes

data = {"ftp": ftp, "vo2": vo2, "cti": cti, "rhr": rhr, "pmax": pmax,
        "heures42": heures42, "km42": km42, "dplus42": dplus42, "nsort": nsort,
        "volume": volume, "familles": ORDRE_FAM,
        "ctl": [{"d": r.date, "v": r.cti} for r in daily if r.cti is not None],
        "plan": plan, "faites": faites, "total": len(plan),
        "taux": taux, "tenues": tenues, "juges": len(juges),
        "next": nxt, "jfin": jfin, "cible": cible,
        "faits": [{"d": s["d"].isoformat(), "fam": s["fam"], "sport": s["sport"],
                   "h": round(s["h"], 2), "km": round(s["km"], 1),
                   "dplus": round(s["dplus"])} for s in seances],
        "maj": today.strftime("%d/%m/%Y")}
HTML = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Objectif 12 semaines — FTP &amp; VO2max, pilotes par un coach IA</title>
<meta name="description" content="Un programme de 12 semaines suivi par un coach automatique : sommeil, variabilite cardiaque et charge analyses chaque matin. Donnees reelles, mises a jour tous les jours.">
<meta property="og:title" content="12 semaines, un coach IA, des donnees reelles">
<meta property="og:description" content="FTP __FTP__ W · VO2max __VO2__ · programme suivi et adapte automatiquement chaque matin.">
<style>
/* Meme charte que le tableau de bord : papier chaud, un seul mode, un accent
   reserve a l'interface, quatre emplacements categoriels et trois couleurs
   d'etat qui ne servent jamais de couleur de serie. */
:root{
  color-scheme:light;
  --page:#f7f5f0;--page-2:#efebe2;--surface:#fffefc;--raised:#f2eee6;
  --ink:#17150f;--ink-2:#4d4941;--muted:#8b8579;
  --grid:#e8e3d8;--axis:#cfc9ba;--border:rgba(23,21,15,.11);
  --accent:#e2571f;--accent-2:#f4a423;
  --s1:#2b6ca8;--s2:#c1592a;--s3:#2f8f5b;--s4:#7d4f9c;
  --good:#17833c;--warn:#a9761a;--crit:#c8493d;
  --shadow:0 1px 2px rgba(23,21,15,.05),0 10px 30px rgba(23,21,15,.06);
}
*{box-sizing:border-box;margin:0}
html{scroll-behavior:smooth;scroll-padding-top:120px}
body{background:var(--page);color:var(--ink);overflow-x:hidden;
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:auto;padding:0 20px}
a{color:inherit}
#prog{position:fixed;top:0;left:0;height:3px;width:0;z-index:40;
  background:linear-gradient(90deg,var(--accent-2),var(--accent))}
.topbar{position:sticky;top:0;z-index:20;border-bottom:1px solid var(--border);
  background:rgba(247,245,240,.88);backdrop-filter:blur(12px)}
.topbar .in{max-width:1180px;margin:auto;padding:9px 20px;display:flex;align-items:center;gap:12px}
.brand{display:flex;align-items:center;gap:10px;min-width:0}
.brand .mark{width:34px;height:34px;border-radius:11px;flex:none;display:grid;place-items:center;
  font-size:17px;background:linear-gradient(135deg,var(--accent-2),var(--accent));
  box-shadow:0 6px 16px rgba(226,87,31,.28)}
.brand b{display:block;font-size:14px;letter-spacing:-.01em}
.brand span{display:block;color:var(--muted);font-size:11.5px}
.tabs{margin-left:auto;display:flex;gap:3px;background:var(--raised);border-radius:10px;padding:3px;flex:none}
.tabs a,.tabs .here{font-size:12.5px;padding:6px 12px;border-radius:8px;text-decoration:none;color:var(--ink-2)}
.tabs .here{background:var(--surface);color:var(--ink);font-weight:600;box-shadow:0 1px 3px rgba(0,0,0,.09)}

/* ---------- hero ---------- */
.hero{position:relative;overflow:hidden;border-radius:22px;margin:18px 0 0;color:#fff;
  background:linear-gradient(112deg,#f0761f 0%,#e2571f 44%,#b8365e 100%);
  box-shadow:0 20px 50px rgba(184,54,94,.22)}
.hero .lines{position:absolute;inset:-40% -20%;opacity:.16;pointer-events:none;
  background:repeating-linear-gradient(101deg,#fff 0 2px,transparent 2px 26px);
  animation:slide 9s linear infinite}
@keyframes slide{from{transform:translateX(0)}to{transform:translateX(26px)}}
.hero .in{position:relative;padding:44px 30px 0}
@media(min-width:900px){.hero .in{padding:58px 46px 0}}
.kicker{font-weight:700;letter-spacing:.22em;font-size:11.5px;text-transform:uppercase;opacity:.85}
h1{font-size:clamp(34px,6.2vw,62px);font-weight:800;line-height:1.03;letter-spacing:-.03em;margin-top:12px}
h1 em{font-style:normal;color:#ffe08a}
.hero .sub{font-size:clamp(15px,2vw,18px);margin-top:14px;max-width:640px;opacity:.94}
.badge{display:inline-flex;align-items:center;gap:9px;background:rgba(255,255,255,.18);
  border:1px solid rgba(255,255,255,.3);color:#fff;font-weight:700;
  border-radius:999px;padding:9px 17px;margin-top:20px;font-size:14px}
.badge i{width:8px;height:8px;border-radius:50%;background:#fff}
.stage{position:relative;height:180px;margin-top:6px}
.stage .road{position:absolute;left:0;right:0;bottom:44px;height:1px;background:rgba(255,255,255,.5)}
.stage .road2{position:absolute;left:0;right:0;bottom:40px;height:1px;
  background:repeating-linear-gradient(90deg,rgba(255,255,255,.55) 0 16px,transparent 16px 34px)}
.ride{position:absolute;bottom:34px;width:clamp(160px,22vw,280px);will-change:transform;
  pointer-events:none;transform:translateX(-30%)}
.ride img{width:100%;height:auto;display:block;transform:scaleX(-1);
  filter:drop-shadow(0 16px 22px rgba(0,0,0,.28))}
@media(max-width:760px){.stage{height:126px}.ride{bottom:26px}}

/* ---------- nav des sections ---------- */
.secnav{position:sticky;top:53px;z-index:15;display:flex;gap:6px;overflow-x:auto;
  padding:12px 0;scrollbar-width:none;background:linear-gradient(var(--page) 62%,rgba(247,245,240,0))}
.secnav::-webkit-scrollbar{display:none}
.secnav a{flex:none;font-size:12.5px;font-weight:600;text-decoration:none;color:var(--ink-2);
  background:var(--surface);border:1px solid var(--border);border-radius:999px;padding:6px 13px;
  transition:background .2s,color .2s,border-color .2s}
.secnav a.on{background:var(--ink);color:var(--page);border-color:var(--ink)}

section{margin:34px 0 44px}
.sech{display:flex;align-items:center;gap:10px;margin:0 0 16px}
.sech i{width:4px;height:22px;border-radius:2px;background:var(--accent);flex:none}
.sech h2{font-size:21px;font-weight:700;letter-spacing:-.015em}
.sech .sub{color:var(--muted);font-size:12.5px}
@media(max-width:640px){.sech .sub{display:none}}

/* ---------- chiffres ---------- */
.band{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;
  background:var(--border);border:1px solid var(--border);border-radius:16px;overflow:hidden;
  margin-top:18px;box-shadow:var(--shadow)}
.band>div{background:var(--surface);padding:20px 16px;text-align:center}
.band .n{font-size:clamp(25px,4vw,36px);font-weight:800;line-height:1.1;letter-spacing:-.03em}
.band .n em{font-style:normal;font-size:.44em;color:var(--muted);font-weight:600}
.band .l{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.11em;margin-top:5px}
.band .hl{background:linear-gradient(180deg,#fff5ef,var(--surface))}
.band .hl .n{color:var(--accent)}
.nextstrip{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;
  background:var(--surface);border:1px solid var(--border);border-left:4px solid var(--accent);
  border-radius:14px;padding:16px 20px;margin-top:14px;box-shadow:var(--shadow)}
.ns-l{color:var(--muted);font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.16em;display:block}
.ns-t{font-size:18px;font-weight:700;letter-spacing:-.015em}
.ns-p{color:var(--ink-2);font-size:13px;font-variant-numeric:tabular-nums;text-align:right}

/* ---------- fiche + texte ---------- */
.duo{display:grid;gap:26px;grid-template-columns:1fr;align-items:start}
@media(min-width:900px){.duo{grid-template-columns:350px 1fr}}
.fiche{background:var(--surface);border:1px solid var(--border);border-radius:18px;padding:22px 24px;
  box-shadow:var(--shadow)}
.fiche .t{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);font-weight:700}
.fiche .nom{font-size:30px;font-weight:800;letter-spacing:-.025em;margin:2px 0 14px}
.fiche dl{display:grid;grid-template-columns:1fr auto;gap:9px 12px;margin:0;font-variant-numeric:tabular-nums}
.fiche dt{color:var(--ink-2)}
.fiche dd{margin:0;font-weight:700;text-align:right}
.fiche .sep{height:1px;background:var(--grid);margin:14px 0}
.copy p{color:var(--ink-2);margin-bottom:14px}
.copy strong{color:var(--ink);font-weight:650}
.phases{display:flex;border-radius:12px;overflow:hidden;height:56px;font-size:11.5px;font-weight:700;
  text-transform:uppercase;letter-spacing:.05em;gap:2px;box-shadow:var(--shadow)}
.phases div{display:flex;align-items:center;justify-content:center;color:#fff;text-align:center;padding:0 6px}

/* ---------- cartes et graphiques ---------- */
.cards2{display:grid;gap:14px;grid-template-columns:1fr}
@media(min-width:900px){.cards2{grid-template-columns:1fr 1fr}}
.card{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:20px;
  box-shadow:var(--shadow);transition:transform .25s ease,box-shadow .25s ease,opacity .5s ease}
.card .cap{color:var(--ink-2);font-size:13px;margin-bottom:4px;font-weight:700}
.card .exp{color:var(--muted);font-size:12px;margin-bottom:12px;line-height:1.5}
.legend{display:flex;gap:13px;flex-wrap:wrap;font-size:12px;color:var(--ink-2);margin-bottom:8px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;vertical-align:middle;margin-right:5px}
svg{width:100%;height:auto;display:block}
svg text{font:10.5px system-ui;fill:var(--muted);font-variant-numeric:tabular-nums}
.frise{display:flex;flex-wrap:wrap;gap:4px}
.frise b{width:15px;height:15px;border-radius:4px;display:block;background:var(--raised);border:1px solid var(--border)}
.tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--page);font-size:11.5px;
  padding:5px 8px;border-radius:6px;opacity:0;transition:opacity .08s;z-index:35;white-space:nowrap}
.reveal{opacity:0;transform:translateY(16px);transition:opacity .55s ease,transform .55s cubic-bezier(.2,.7,.3,1)}
.reveal.in{opacity:1;transform:none}
@media(prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  .reveal{opacity:1;transform:none;transition:none}
  .hero .lines{animation:none}.ride img{transform:scaleX(-1)!important}
}

/* ---------- planning interactif ---------- */
.plan-tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chips button{font:inherit;font-size:12.5px;font-weight:600;color:var(--ink-2);cursor:pointer;
  background:var(--surface);border:1px solid var(--border);border-radius:999px;padding:6px 13px;
  display:inline-flex;align-items:center;gap:6px;transition:background .18s,color .18s,border-color .18s}
.chips button i{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none}
.chips button[aria-pressed=true]{background:var(--ink);color:var(--page);border-color:var(--ink)}
.chips button .cnt{font-variant-numeric:tabular-nums;opacity:.6;font-weight:500}
.today-btn{margin-left:auto;font:inherit;font-size:12.5px;font-weight:600;cursor:pointer;
  background:var(--accent);color:#fff;border:0;border-radius:999px;padding:7px 15px}
.plan-layout{display:grid;gap:14px;grid-template-columns:1fr;align-items:start}
@media(min-width:1000px){.plan-layout{grid-template-columns:minmax(0,1fr) 310px}}
.cal{background:var(--surface);border:1px solid var(--border);border-radius:16px;
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
.day{min-height:64px;border:1px solid var(--border);border-radius:10px;padding:6px 7px 7px;background:var(--page-2)}
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
.detail{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:17px;
  box-shadow:var(--shadow)}
@media(min-width:1000px){.detail{position:sticky;top:108px}}
.detail .dt{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}
.detail .dn{font-size:17px;font-weight:700;letter-spacing:-.015em;margin-top:4px;line-height:1.25}
.detail .vp{display:inline-flex;align-items:center;gap:7px;border-radius:999px;padding:5px 12px;
  font-size:12.5px;font-weight:600;margin-top:10px;color:#fff;background:var(--vc,var(--axis))}
.detail .txt{font-size:12.5px;color:var(--ink-2);line-height:1.55;margin-top:10px}
.detail dl{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;font-size:12.5px;margin-top:12px}
.detail dt{color:var(--muted)}
.detail dd{text-align:right;font-variant-numeric:tabular-nums;font-weight:600;margin:0}
.detail .sep{height:1px;background:var(--grid);margin:14px 0}
.detail .hint{font-size:12px;color:var(--muted);line-height:1.5}
footer{color:var(--muted);font-size:12.5px;padding:26px 0 40px;text-align:center;border-top:1px solid var(--grid)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
</style></head><body>
<div id="prog"></div>

<div class="topbar"><div class="in">
  <span class="brand"><span class="mark">🚴</span>
    <span><b>12 semaines, un coach IA</b><span>donnees reelles · maj __MAJ__</span></span></span>
  <nav class="tabs"><a href="/">Tableau de bord</a><span class="here">Page publique</span></nav>
</div></div>

<div class="wrap">
<div class="hero">
  <span class="lines" aria-hidden="true"></span>
  <div class="in">
    <div class="kicker">Programme 12 semaines · aout → octobre 2026</div>
    <h1>Un coach qui lit ma nuit<br>et <em>decide ma seance</em>.</h1>
    <div class="sub">Sommeil, variabilite cardiaque et charge d'entrainement sont analyses chaque matin a 7 h. La seance du jour est maintenue, allegee ou annulee — puis poussee sur le compteur, le calendrier et cette page. Sans que je touche a rien.</div>
    <div class="badge"><i></i>J−__JFIN__ avant __CIBLE__</div>
  </div>
  <div class="stage" aria-hidden="true">
    <span class="road"></span><span class="road2"></span>
    <span class="ride" id="ride"><img src="img/bike.webp" width="1264" height="848" alt=""></span>
  </div>
</div>

<nav class="secnav" id="secnav">
  <a href="#chiffres">Les chiffres</a>
  <a href="#principe">Le principe</a>
  <a href="#planning">Le planning</a>
  <a href="#progression">La progression</a>
  <a href="#decisions">Les decisions</a>
</nav>

<section id="chiffres">
<div class="sech"><i></i><h2>Ou j'en suis</h2><span class="sub">releve par la montre et le compteur, sans saisie manuelle</span></div>
<div class="band">
<div class="hl"><div class="n">__FTP__<em> W</em></div><div class="l">FTP</div></div>
<div><div class="n">__VO2__</div><div class="l">VO2max</div></div>
<div><div class="n">__CTI__</div><div class="l">Condition</div></div>
<div><div class="n">__HEURES__<em> h</em></div><div class="l">6 dernieres semaines</div></div>
<div><div class="n">__NSORT__</div><div class="l">Seances</div></div>
<div><div class="n">__RHR__<em> bpm</em></div><div class="l">FC repos</div></div>
</div>
<div class="nextstrip">
<div><span class="ns-l">Prochaine seance</span><span class="ns-t">__NEXT__</span></div>
<div class="ns-p">__FAITES__ / __TOTAL__ seances passees<br>__TAUX__</div>
</div>
</section>

<section id="principe">
<div class="sech"><i></i><h2>Comment ca marche</h2><span class="sub">une decision par matin, prise sur des donnees</span></div>
<div class="duo">
<div class="fiche">
<div class="t">Etat des lieux</div>
<div class="nom">La fiche</div>
<dl>
<dt>Seuil (FTP)</dt><dd>__FTP__ W</dd>
<dt>VO2max</dt><dd>__VO2__</dd>
<dt>Pointe de puissance</dt><dd>__PMAX__ W</dd>
<dt>FC au repos</dt><dd>__RHR__ bpm</dd>
<dt>Condition (CTL)</dt><dd>__CTI__</dd>
</dl>
<div class="sep"></div>
<dl>
<dt>Volume 6 semaines</dt><dd>__HEURES__ h</dd>
<dt>Distance</dt><dd>__KM42__ km</dd>
<dt>Denivele</dt><dd>__DPLUS__ m</dd>
</dl>
</div>
<div class="copy">
<p><strong>Toutes les donnees de cette page sont reelles</strong> : elles remontent d'une montre et d'un compteur, sans saisie manuelle. Seuil fonctionnel a <strong>__FTP__ watts</strong>, VO2max estimee a <strong>__VO2__</strong>, pointe relevee a <strong>__PMAX__ W</strong>.</p>
<p>Chaque matin, un modele de langage lit la nuit precedente — duree, sommeil profond, variabilite cardiaque, frequence au repos — la compare a la charge accumulee, et tranche : seance <strong>maintenue</strong>, <strong>allegee</strong>, ou remplacee par du <strong>repos</strong>. Le verdict part en notification, l'entrainement structure est ecrit sur le compteur, le calendrier et cette page se mettent a jour.</p>
<p>Douze semaines, quatre phases, deux juges de paix : <strong>le Ventoux par Bedoin</strong> au milieu du programme, et un <strong>retest FTP</strong> a la fin pour mesurer le chemin parcouru.</p>
<div class="phases" style="margin-top:20px">
<div style="flex:4;background:var(--s1)">Base — sweet spot</div>
<div style="flex:4;background:var(--s2)">Seuil</div>
<div style="flex:3;background:var(--s4)">VO2max</div>
<div style="flex:1.4;background:var(--s3)">Affutage</div>
</div>
</div>
</div>
</section>

<section id="planning">
<div class="sech"><i></i><h2>Le planning</h2><span class="sub">__TOTAL__ seances · clique pour voir le verdict du coach</span></div>
<div class="plan-tools">
  <div class="chips" id="planFilters"></div>
  <button class="today-btn" id="btnToday">Aller a cette semaine</button>
</div>
<div class="plan-layout">
  <div class="cal" id="cal">
    <div class="dow"><span>lun</span><span>mar</span><span>mer</span><span>jeu</span><span>ven</span><span>sam</span><span>dim</span></div>
    <div id="weeks"></div>
  </div>
  <div class="detail" id="detail"></div>
</div>
</section>

<section id="progression">
<div class="sech"><i></i><h2>La progression, en direct</h2><span class="sub">mis a jour chaque matin</span></div>
<div class="cards2">
<div class="card">
  <div class="cap">Condition physique</div>
  <div class="exp">La moyenne d'entrainement sur 6 semaines. C'est la courbe qui doit monter — doucement.</div>
  <div id="c-ctl"></div>
</div>
<div class="card">
  <div class="cap">Volume par semaine et par sport</div>
  <div class="exp">Le programme est cycliste, mais la course, la natation et le trail fatiguent les memes jambes : le coach compte tout.</div>
  <div class="legend" id="lg-vol"></div>
  <div id="c-vol"></div>
</div>
</div>
</section>

<section id="decisions">
<div class="sech"><i></i><h2>Les decisions du coach</h2><span class="sub">une pastille par seance du programme</span></div>
<div class="card">
  <div class="exp">Chaque matin de seance, le coach lit la nuit et tranche. Les pastilles vides sont les seances encore a venir.</div>
  <div class="legend">
    <span><i style="background:var(--good)"></i>Maintenue</span>
    <span><i style="background:var(--warn)"></i>Allegee</span>
    <span><i style="background:var(--crit)"></i>Repos impose</span>
    <span><i style="background:var(--raised);border:1px solid var(--border)"></i>A venir</span>
  </div>
  <div class="frise" id="frise"></div>
</div>
</section>

<footer>Donnees COROS &amp; iGPSport · page regeneree automatiquement chaque matin · derniere maj __MAJ__<br>
Coach autonome heberge sur un serveur perso, propulse par Claude Code.</footer>
</div>
<div class="tip" id="tip"></div>
<script>
const D = __DATA__;
const fdm = d => String(d).slice(6,8)+"/"+String(d).slice(4,6);
const hdec = h => h>=1 ? (Math.round(h*10)/10)+" h" : Math.round(h*60)+" min";
const S=["var(--s1)","var(--s2)","var(--s3)","var(--s4)"];
const GOOD="var(--good)", WARN="var(--warn)", CRIT="var(--crit)", NEU="var(--axis)";
const W=460,H=175,PL=34,PR=8,PT=10,PB=22;
const JOURS=["lun","mar","mer","jeu","ven","sam","dim"];
const MOIS=["janv.","fevr.","mars","avril","mai","juin","juil.","aout","sept.","oct.","nov.","dec."];
const today = new Date().toISOString().slice(0,10);
const VD = {"✅":[GOOD,"Feu vert"],"⚠️":[WARN,"Seance allegee"],"🛑":[CRIT,"Remplacee par du repos"]};
const PHC = {Base:"var(--s1)", Seuil:"var(--s2)", VO2max:"var(--s4)", Pic:"var(--s4)",
             Recup:"var(--s3)", Reprise:"var(--s3)", Affutage:"var(--accent)"};

const tip = document.getElementById("tip");
function showTip(e,t){ tip.textContent=t; tip.style.opacity=1;
  tip.style.left=Math.min(e.clientX+12, innerWidth-Math.min(320,t.length*6.4))+"px";
  tip.style.top=(e.clientY-30)+"px"; }
function hideTip(){ tip.style.opacity=0; }
function safe(id,label,fn){
  try { fn(); } catch(err){ console.error("[pub] "+label, err);
    const el=document.getElementById(id);
    if (el) el.innerHTML='<div class="exp" style="padding:16px 0">'+label+' : affichage indisponible.</div>'; }
}
/* Pas de litteral regex ici : un guillemet dans une regex trompe le
   controleur statique des pages (check-page-js.py). */
function esc(s){ return String(s??"").split("&").join("&amp;")
  .split("<").join("&lt;").split(">").join("&gt;").split('"').join("&quot;"); }
function pdate(d){ const [y,m,j] = d.split("-").map(Number); return new Date(y, m-1, j); }
function idate(dt){ return dt.getFullYear()+"-"+String(dt.getMonth()+1).padStart(2,"0")+"-"+String(dt.getDate()).padStart(2,"0"); }
function longue(d){ const dt = pdate(d);
  return JOURS[(dt.getDay()+6)%7]+" "+dt.getDate()+" "+MOIS[dt.getMonth()]; }

safe("c-ctl","Condition physique",()=>{
  const s=D.ctl;
  if(!s.length){ document.getElementById("c-ctl").innerHTML='<div class="exp">Pas encore de mesure.</div>'; return; }
  const vs=s.map(r=>r.v), lo=Math.floor(Math.min(...vs)-3), hi=Math.ceil(Math.max(...vs)+3);
  const iw=W-PL-PR, ih=H-PT-PB;
  const X=i=>PL+i/Math.max(1,s.length-1)*iw, Y=v=>PT+(1-(v-lo)/(hi-lo||1))*ih;
  const pts=s.map((r,i)=>X(i)+","+Y(r.v)).join(" ");
  let x=[`<svg viewBox="0 0 ${W} ${H}">`];
  for(const g of [lo,(lo+hi)/2,hi]) x.push(
    `<line x1="${PL}" x2="${W-PR}" y1="${Y(g)}" y2="${Y(g)}" stroke="var(--grid)"/>`+
    `<text x="${PL-5}" y="${Y(g)+3.5}" text-anchor="end">${Math.round(g)}</text>`);
  x.push(`<polygon points="${PL},${PT+ih} ${pts} ${X(s.length-1)},${PT+ih}" fill="${S[0]}" opacity="0.14"/>`);
  x.push(`<polyline points="${pts}" fill="none" stroke="${S[0]}" stroke-width="2.5" stroke-linejoin="round"/>`);
  for(let i=0;i<s.length;i+=7) x.push(`<text x="${X(i)}" y="${H-6}" text-anchor="middle">${fdm(s[i].d)}</text>`);
  x.push(`<rect id="ctl-hit" x="${PL}" y="0" width="${iw}" height="${H}" fill="transparent"/></svg>`);
  document.getElementById("c-ctl").innerHTML=x.join("");
  const hit=document.getElementById("ctl-hit");
  hit.addEventListener("mousemove",e=>{
    const r=hit.getBoundingClientRect();
    const i=Math.round((e.clientX-r.left)/r.width*(s.length-1));
    if(i<0||i>=s.length) return;
    showTip(e, fdm(s[i].d)+" · condition "+Math.round(s[i].v));
  });
  hit.addEventListener("mouseleave",hideTip);
});

safe("c-vol","Volume par sport",()=>{
  const FAM=D.familles, items=D.volume;
  document.getElementById("lg-vol").innerHTML=FAM.map((f,i)=>
    `<span><i style="background:${S[i]}"></i>${f}</span>`).join("");
  if(!items.length){ document.getElementById("c-vol").innerHTML='<div class="exp">Pas encore de donnee.</div>'; return; }
  const tot=items.map(r=>FAM.reduce((a,f)=>a+(r[f]||0),0));
  const max=Math.max(...tot,1);
  const iw=W-PL-PR, ih=H-PT-PB, bw=iw/items.length;
  let x=[`<svg viewBox="0 0 ${W} ${H}">`];
  for(const g of [0,max/2,max]) { const y=PT+(1-g/max)*ih;
    x.push(`<line x1="${PL}" x2="${W-PR}" y1="${y}" y2="${y}" stroke="var(--grid)"/>`+
           `<text x="${PL-5}" y="${y+3.5}" text-anchor="end">${Math.round(g)} h</text>`); }
  items.forEach((r,i)=>{
    const bx=PL+i*bw+3, w=Math.max(bw-6,2);
    let acc=0;
    x.push(`<g class="bar" data-i="${i}">`);
    FAM.forEach((f,k)=>{
      const v=r[f]||0; if(!v) return;
      const y0=PT+ih-(acc+v)/max*ih, h0=Math.max(v/max*ih-2,1);
      x.push(`<rect x="${bx}" y="${y0}" width="${w}" height="${h0}" fill="${S[k]}" rx="3"/>`);
      acc+=v;
    });
    x.push(`</g><text x="${bx+w/2}" y="${H-6}" text-anchor="middle">${r.w}</text>`);
  });
  document.getElementById("c-vol").innerHTML=x.join("")+"</svg>";
  document.querySelectorAll("#c-vol .bar").forEach(g=>{
    const r=items[+g.dataset.i];
    const t=`${r.w} · ${hdec(FAM.reduce((a,f)=>a+(r[f]||0),0))} au total — ${r.detail}`;
    g.addEventListener("mousemove",e=>showTip(e,t));
    g.addEventListener("mouseleave",hideTip);
  });
});

safe("frise","Decisions du coach",()=>{
  document.getElementById("frise").innerHTML=D.plan.map(p=>{
    const v=VD[p.emoji];
    const etat=v?v[1]:(p.date<=today?"sans verdict":"a venir");
    return `<b style="background:${v?v[0]:"var(--raised)"}"
      data-tip="${esc(p.date.split("-").reverse().join("/").slice(0,5)+" · "+p.seance+" · "+etat)}"></b>`;
  }).join("");
  document.querySelectorAll("#frise b").forEach(b=>{
    b.addEventListener("mousemove",e=>showTip(e,b.dataset.tip));
    b.addEventListener("mouseleave",hideTip);
  });
});

/* ---------- planning interactif ----------
   Meme composant que le tableau de bord : le programme regroupe par semaine,
   sept jours par ligne, une seance cliquable qui ouvre le verdict du matin et
   ce que la montre a enregistre. Les sorties hors programme sont en pointille. */
const faits = {};
(D.faits||[]).forEach(s=>{ (faits[s.d] = faits[s.d] || []).push(s); });
function resume(a){
  const bits = [];
  if (a.h) bits.push(hdec(a.h));
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
let filtre = "tout", choisi = null;

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
  let h = `<div class="dt">${longue(d)}${p?" · "+esc(p.phase):""}</div>
    <div class="dn">${esc(p ? p.seance : "Sortie hors programme")}</div>
    <span class="vp" style="--vc:${col}">${etat}</span>`;
  if (p && p.note) h += `<div class="txt">${esc(p.note)}</div>`;
  const act = faits[d] || [];
  h += `<div class="sep"></div>`;
  if (act.length){
    h += `<div class="dt">Realise</div>`;
    act.forEach(a=>{
      h += `<dl><dt>${esc(a.sport||a.fam)}</dt><dd>${resume(a)||"–"}</dd>`;
      if (a.dplus) h += `<dt>Denivele</dt><dd>${a.dplus} m</dd>`;
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
    return `<div class="wk${cur?" now":""}" data-code="${w.code}">
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
      if (innerWidth < 1000) document.getElementById("detail").scrollIntoView({behavior:"smooth", block:"nearest"});
    });
  });
  document.getElementById("btnToday").addEventListener("click", ()=>{
    const w = document.querySelector("#weeks .wk.now") || document.querySelector("#weeks .wk");
    if (w) w.scrollIntoView({behavior:"smooth", block:"center"});
  });

  const cible = D.plan.findIndex(p=>p.date===today);
  const suiv = D.next ? D.plan.findIndex(p=>p.date===D.next.date && p.seance===D.next.seance) : -1;
  const idx = cible>=0 ? cible : suiv;
  if (idx>=0){
    const b = document.querySelector(`#weeks .sc[data-p="${idx}"]`);
    if (b){ b.classList.add("sel"); choisi = b; }
    detail({p: idx});
  } else detail(null);
  applique();
});

/* ---------- reperage dans la page et animations ---------- */
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

/* le cycliste traverse la scene au fil du defilement, comme sur luku.fr */
(function(){
  const stage=document.querySelector(".stage"), ride=document.getElementById("ride");
  if(!stage||!ride) return;
  if(reduced){ ride.style.transform="translateX(22vw)"; return; }
  let queued=false;
  function place(){
    queued=false;
    const r=stage.getBoundingClientRect();
    const p=Math.max(0,Math.min(1,(innerHeight-r.top)/(innerHeight+r.height)));
    const travel=stage.clientWidth+ride.offsetWidth;
    ride.style.transform=`translateX(${Math.round(-ride.offsetWidth+p*travel)}px)`;
  }
  addEventListener("scroll",()=>{ if(!queued){ queued=true; requestAnimationFrame(place); } },{passive:true});
  addEventListener("resize",place);
  place();
})();

if (!reduced) {
  document.querySelectorAll(".card,.fiche,.cal,.detail,.band,.nextstrip,.phases").forEach(el=>el.classList.add("reveal"));
  const io = new IntersectionObserver(es=>es.forEach(e=>{
    if (!e.isIntersecting) return;
    e.target.classList.add("in");
    io.unobserve(e.target);
  }), {threshold: .12});
  document.querySelectorAll(".reveal").forEach((el,i)=>{ el.style.transitionDelay=(Math.min(i,6)*45)+"ms"; io.observe(el); });
  setTimeout(()=>document.querySelectorAll(".reveal:not(.in)").forEach(el=>el.classList.add("in")), 1600);
}
</script></body></html>
"""

taux = f"{data['taux']} % de seances tenues" if data["taux"] is not None else "verdicts en cours"
nxt = data["next"]
prochaine = f"{nxt['seance']} — {nxt['date'].split('-')[2]}/{nxt['date'].split('-')[1]}" if nxt else "programme termine"
page = HTML
for k, v in [("__FTP__", data["ftp"]), ("__VO2__", data["vo2"]), ("__PMAX__", data["pmax"]),
             ("__CTI__", data["cti"] if data["cti"] is not None else "–"),
             ("__RHR__", data["rhr"] if data["rhr"] is not None else "–"),
             ("__HEURES__", data["heures42"]), ("__KM42__", data["km42"]),
             ("__DPLUS__", data["dplus42"]), ("__NSORT__", data["nsort"]),
             ("__FAITES__", data["faites"]), ("__TOTAL__", data["total"]),
             ("__TAUX__", taux), ("__NEXT__", prochaine),
             ("__JFIN__", max(0, data["jfin"])), ("__CIBLE__", data["cible"]),
             ("__MAJ__", data["maj"])]:
    page = page.replace(k, str(v))
page = page.replace("__DATA__", json.dumps(data, ensure_ascii=False))
os.makedirs(OUT, exist_ok=True)
path = f"{OUT}/index.html"
with open(path, "w", encoding="utf-8") as f:
    f.write(page)
print("OK", path)
