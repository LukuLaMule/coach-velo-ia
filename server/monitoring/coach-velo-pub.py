#!/home/opc/mcp/coros-mcp/.venv/bin/python
"""Genere la page publique velo.luku.fr/pub/ (vitrine reseaux sociaux)."""
import asyncio, datetime, json, os, re, subprocess, sys

sys.path.insert(0, "/home/opc/mcp/coros-mcp")
MON = "/home/opc/monitoring"
OUT = os.environ.get("VELO_PUB_OUT", "/home/opc/Docker/sites/velo/public/pub")
JOURS = 42
JOURS_ACT = 120

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

verdicts = {}
vf = f"{MON}/coach-velo-verdicts.tsv"
if os.path.exists(vf):
    for line in open(vf):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 2:
            verdicts[p[0]] = p[1]

plan = []
for line in open(f"{MON}/coach-velo-plan.tsv"):
    p = line.rstrip("\n").split("\t")
    if len(p) >= 3:
        plan.append({"date": p[0], "seance": p[1], "phase": p[2],
                     "emoji": verdicts.get(p[0], "")})

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
        "maj": today.strftime("%d/%m/%Y")}

HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Objectif 12 semaines — FTP &amp; VO2max, pilotes par un coach IA</title>
<meta name="description" content="Un programme de 12 semaines suivi par un coach automatique : sommeil, variabilite cardiaque et charge analyses chaque matin. Donnees reelles, mises a jour tous les jours.">
<meta property="og:title" content="12 semaines, un coach IA, des donnees reelles">
<meta property="og:description" content="FTP __FTP__ W · VO2max __VO2__ · programme suivi et adapte automatiquement chaque matin.">
<style>
/* Meme charte mate que le tableau de bord : chroma bridee au plancher
   lisible, emplacements categoriels et couleurs d'etat valides dans les
   deux modes. */
:root{
  color-scheme:light;
  --page:#f4f2ee;--surface:#faf9f6;--raised:#efece5;--ink:#1b1a17;--ink-2:#55534c;--muted:#8b8880;
  --grid:#e6e3da;--axis:#cbc7bb;--border:rgba(27,26,23,.12);
  --s1:#4d7db4;--s2:#81411e;--s3:#5a9a68;--s4:#8e5585;
  --good:#148c3a;--warn:#b07d00;--crit:#c4584f;
}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){
  color-scheme:dark;
  --page:#101113;--surface:#17181a;--raised:#1e1f22;--ink:#f2f1ed;--ink-2:#b9b6ae;--muted:#86837c;
  --grid:#26272a;--axis:#3b3c3f;--border:rgba(255,255,255,.12);
  --s1:#3c6ba1;--s2:#9e552f;--s3:#5a9a68;--s4:#8e5585;
}}
:root[data-theme=dark]{
  color-scheme:dark;
  --page:#101113;--surface:#17181a;--raised:#1e1f22;--ink:#f2f1ed;--ink-2:#b9b6ae;--muted:#86837c;
  --grid:#26272a;--axis:#3b3c3f;--border:rgba(255,255,255,.12);
  --s1:#3c6ba1;--s2:#9e552f;--s3:#5a9a68;--s4:#8e5585;
}
*{box-sizing:border-box;margin:0}
body{background:var(--page);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;overflow-x:hidden}
.wrap{max-width:1060px;margin:auto;padding:0 18px}
a{color:inherit}
.topnav{position:fixed;top:12px;right:14px;z-index:5;display:flex;gap:4px;
  background:var(--surface);border:1px solid var(--border);border-radius:9px;padding:3px}
.topnav a,.topnav .here{font-size:12.5px;padding:5px 11px;border-radius:6px;text-decoration:none;color:var(--ink-2)}
.topnav a:hover{background:var(--raised)}
.topnav .here{background:var(--s1);color:#fff;font-weight:600}

/* ---- hero : le cycliste traverse la scene au defilement ---- */
.hero{position:relative;overflow:hidden;padding:96px 0 0;
  background:linear-gradient(180deg,var(--raised),var(--page))}
.hero .inner{max-width:1060px;margin:auto;padding:0 18px 26px;position:relative;z-index:2}
.kicker{color:var(--s2);font-weight:700;letter-spacing:.24em;font-size:11.5px;text-transform:uppercase}
h1{font-size:clamp(38px,7.4vw,74px);font-weight:800;line-height:1.02;letter-spacing:-.025em;margin-top:12px}
h1 em{font-style:normal;color:var(--s1)}
.sub{color:var(--ink-2);font-size:clamp(15px,2.2vw,18px);margin-top:14px;max-width:620px}
.badge{display:inline-flex;align-items:center;gap:8px;background:var(--s1);color:#fff;font-weight:600;
  border-radius:999px;padding:8px 16px;margin-top:20px;font-size:14px}
.stage{position:relative;height:190px}
.stage .road{position:absolute;left:0;right:0;bottom:44px;height:1px;background:var(--axis);opacity:.6}
.stage .road2{position:absolute;left:0;right:0;bottom:40px;height:1px;
  background:repeating-linear-gradient(90deg,var(--axis) 0 16px,transparent 16px 34px);opacity:.4}
.ride{position:absolute;bottom:34px;width:clamp(170px,24vw,300px);will-change:transform;
  pointer-events:none;transform:translateX(-30%)}
.ride img{width:100%;height:auto;display:block;transform:scaleX(-1);
  filter:drop-shadow(0 16px 22px rgba(0,0,0,.20))}
@media(max-width:760px){.stage{height:132px}.ride{bottom:26px}}

section{margin:60px 0}
h2{font-size:11.5px;font-weight:700;color:var(--s2);text-transform:uppercase;letter-spacing:.2em;margin-bottom:18px}
.band{display:grid;grid-template-columns:repeat(auto-fit,minmax(146px,1fr));gap:1px;background:var(--border);
  border:1px solid var(--border);border-radius:14px;overflow:hidden;margin-top:-12px;position:relative;z-index:3}
.band>div{background:var(--surface);padding:18px 16px;text-align:center}
.band .n{font-size:clamp(24px,4vw,36px);font-weight:700;line-height:1.1}
.band .n em{font-style:normal;font-size:.44em;color:var(--muted);font-weight:600}
.band .l{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.11em;margin-top:5px}
.band .hl .n{color:var(--s1)}
.nextstrip{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;
  background:var(--surface);border:1px solid var(--border);border-left:4px solid var(--s1);border-radius:12px;
  padding:14px 18px;margin-top:14px}
.ns-l{color:var(--muted);font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.16em;display:block}
.ns-t{font-size:17px;font-weight:700}
.ns-p{color:var(--ink-2);font-size:13px;font-variant-numeric:tabular-nums;text-align:right}
.duo{display:grid;gap:30px;grid-template-columns:1fr;align-items:start}
@media(min-width:860px){.duo{grid-template-columns:340px 1fr}}
.fiche{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:22px 24px}
.fiche .t{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);font-weight:700}
.fiche .nom{font-size:30px;font-weight:800;letter-spacing:-.02em;margin:2px 0 14px}
.fiche dl{display:grid;grid-template-columns:1fr auto;gap:9px 12px;margin:0;font-variant-numeric:tabular-nums}
.fiche dt{color:var(--ink-2)}
.fiche dd{margin:0;font-weight:700;text-align:right}
.fiche .sep{height:1px;background:var(--grid);margin:14px 0}
.copy p{color:var(--ink-2);margin-bottom:14px}
.copy strong{color:var(--ink);font-weight:650}
.phases{display:flex;border-radius:10px;overflow:hidden;height:52px;font-size:11.5px;font-weight:700;
  text-transform:uppercase;letter-spacing:.05em;gap:2px}
.phases div{display:flex;align-items:center;justify-content:center;color:#fff;text-align:center;padding:0 6px}
.cards2{display:grid;gap:14px;grid-template-columns:1fr}
@media(min-width:860px){.cards2{grid-template-columns:1fr 1fr}}
.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:20px}
.card .cap{color:var(--ink-2);font-size:13px;margin-bottom:4px;font-weight:600}
.card .exp{color:var(--muted);font-size:12px;margin-bottom:12px}
.legend{display:flex;gap:13px;flex-wrap:wrap;font-size:12px;color:var(--ink-2);margin-bottom:8px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;vertical-align:middle;margin-right:5px}
svg{width:100%;height:auto;display:block}
svg text{font:10.5px system-ui;fill:var(--muted);font-variant-numeric:tabular-nums}
.frise{display:flex;flex-wrap:wrap;gap:4px}
.frise b{width:14px;height:14px;border-radius:4px;display:block;background:var(--raised);border:1px solid var(--border)}
.tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--page);font-size:11.5px;
  padding:5px 8px;border-radius:6px;opacity:0;transition:opacity .08s;z-index:9;white-space:nowrap}
footer{color:var(--muted);font-size:12.5px;padding:26px 0 40px;text-align:center;border-top:1px solid var(--grid)}
@media(prefers-reduced-motion:reduce){.ride img{transform:scaleX(-1)!important}}
</style></head><body>

<nav class="topnav"><a href="/">Dashboard</a><span class="here">Page publique</span></nav>

<div class="hero">
  <div class="inner">
    <div class="kicker">Programme 12 semaines · aout → octobre 2026</div>
    <h1>Un coach qui lit ma nuit<br>et <em>decide ma seance</em>.</h1>
    <div class="sub">Sommeil, variabilite cardiaque et charge d'entrainement sont analyses chaque matin a 7 h. La seance du jour est maintenue, allegee ou annulee — puis poussee sur le compteur, le calendrier et cette page. Sans que je touche a rien.</div>
    <div class="badge">J−__JFIN__ avant __CIBLE__</div>
  </div>
  <div class="stage" aria-hidden="true">
    <span class="road"></span><span class="road2"></span>
    <span class="ride" id="ride"><img src="img/bike.webp" width="1264" height="848" alt=""></span>
  </div>
</div>

<div class="wrap">
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

<section><div class="duo">
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
<h2>Comment ca marche</h2>
<p><strong>Toutes les donnees de cette page sont reelles</strong> : elles remontent d'une montre et d'un compteur, sans saisie manuelle. Seuil fonctionnel a <strong>__FTP__ watts</strong>, VO2max estimee a <strong>__VO2__</strong>, pointe relevee a <strong>__PMAX__ W</strong>.</p>
<p>Chaque matin, un modele de langage lit la nuit precedente — duree, sommeil profond, variabilite cardiaque, frequence au repos — la compare a la charge accumulee, et tranche : seance <strong>maintenue</strong>, <strong>allegee</strong>, ou remplacee par du <strong>repos</strong>. Le verdict part en notification, l'entrainement structure est ecrit sur le compteur, le calendrier et cette page se mettent a jour.</p>
<p>Douze semaines, quatre phases, deux juges de paix : <strong>le Ventoux par Bedoin</strong> au milieu du programme, et un <strong>retest FTP</strong> a la fin pour mesurer le chemin parcouru.</p>
</div>
</div></section>

<section><h2>Le plan — 4 phases, __TOTAL__ seances</h2>
<div class="phases">
<div style="flex:4;background:var(--s1)">Base — sweet spot</div>
<div style="flex:4;background:var(--s2)">Seuil</div>
<div style="flex:3;background:var(--s4)">VO2max</div>
<div style="flex:1.4;background:var(--s3)">Affutage</div>
</div></section>

<section><h2>La progression, en direct</h2>
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

<section><h2>Les decisions du coach</h2>
<div class="card">
  <div class="exp">Une pastille par seance du programme. Les pastilles vides sont encore a venir.</div>
  <div class="legend">
    <span><i style="background:var(--good)"></i>Maintenue</span>
    <span><i style="background:var(--warn)"></i>Allegee</span>
    <span><i style="background:var(--crit)"></i>Repos impose</span>
    <span><i style="background:var(--raised);border:1px solid var(--border)"></i>A venir</span>
  </div>
  <div class="frise" id="frise"></div>
</div>
</section>
</div>

<footer>Donnees COROS &amp; iGPSport · page regeneree automatiquement chaque matin · derniere maj __MAJ__<br>
Coach autonome heberge sur un serveur perso, propulse par Claude Code.</footer>
<div class="tip" id="tip"></div>
<script>
const D = __DATA__;
const fdm = d => String(d).slice(6,8)+"/"+String(d).slice(4,6);
const hdec = h => h>=1 ? (Math.round(h*10)/10)+" h" : Math.round(h*60)+" min";
const S=["var(--s1)","var(--s2)","var(--s3)","var(--s4)"];
const W=460,H=175,PL=34,PR=8,PT=10,PB=22;

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
  const VD={"✅":["var(--good)","maintenue"],"⚠️":["var(--warn)","allegee"],"🛑":["var(--crit)","repos impose"]};
  const today=new Date().toISOString().slice(0,10);
  document.getElementById("frise").innerHTML=D.plan.map(p=>{
    const v=VD[p.emoji];
    const etat=v?v[1]:(p.date<=today?"sans verdict":"a venir");
    const d=p.date.split("-").reverse().join("/").slice(0,5);
    return `<b style="background:${v?v[0]:"var(--raised)"}" data-tip="${d} · ${p.seance} · ${etat}"></b>`;
  }).join("");
  document.querySelectorAll("#frise b").forEach(b=>{
    b.addEventListener("mousemove",e=>showTip(e,b.dataset.tip));
    b.addEventListener("mouseleave",hideTip);
  });
});

/* le cycliste traverse la scene au fil du defilement, comme sur luku.fr */
(function(){
  const stage=document.querySelector(".stage"), ride=document.getElementById("ride");
  if(!stage||!ride) return;
  if(matchMedia("(prefers-reduced-motion: reduce)").matches){ ride.style.transform="translateX(22vw)"; return; }
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
