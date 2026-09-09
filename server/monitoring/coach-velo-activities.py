#!/usr/bin/env python
"""Collecte activite par activite, tous sports, sur les N derniers jours.

Deux sources complementaires, parce qu'aucune ne voit tout :
  - COROS    : ce que la montre enregistre (course, trail, natation, rando...)
  - iGPSport : les sorties velo, qui ne passent pas par la montre

Sortie : un JSON sur stdout, une entree par activite plus des agregats par
sport sur 7 et 28 jours. Destine a etre lu par le coach du matin, qui doit
pouvoir juger chaque seance individuellement et pas seulement une charge
globale.

Usage : coach-velo-activities.py [jours] [--debug]   (defaut 14)
"""
import json
import os
import subprocess
import sys
import datetime

MON = os.path.dirname(os.path.abspath(__file__))

# Les chemins suivent l utilisateur : le projet a tourne sous opc (Oracle) puis
# sous ubuntu (OVH). MCP_HOME permet de pointer ailleurs sans toucher au code.
MCP_HOME = os.environ.get("MCP_HOME", os.path.expanduser("~/mcp"))
SECRETS = os.path.join(MCP_HOME, "secrets")
COROS_PY = os.path.join(MCP_HOME, "coros-mcp", ".venv", "bin", "python")
IGP_PY = os.path.join(MCP_HOME, "igpsport-mcp", ".venv", "bin", "python")

DEBUG = "--debug" in sys.argv
ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]
DAYS = int(ARGS[0]) if ARGS else 14
TODAY = datetime.date.today()
SINCE = TODAY - datetime.timedelta(days=DAYS)

# COROS renvoie des codes numeriques ; on les ramene a des familles lisibles.
# Codes observes reellement : 100 Running, 103 Track Running, 200 Road Bike,
# 300 (natation piscine, remonte sans libelle : "Sport 300").
COROS_FAMILIES = {
    "course": [100, 101, 103],
    "trail": [102, 105],
    "rando": [104, 900],
    "velo": [200, 201, 202, 203, 204, 205, 299],
    "natation": [300, 301],
    "renfo": [400, 401, 402, 901, 902, 903, 904, 905, 906, 9901, 9902],
    "ski": [500, 501, 502, 503],
    "rameur": [700, 701],
}
SPORT_BY_CODE = {c: fam for fam, codes in COROS_FAMILIES.items() for c in codes}

# Repli quand le code numerique est inconnu : le libelle textuel du sport.
SPORT_BY_LABEL = [
    ("swim", "natation"), ("pool", "natation"),
    ("trail", "trail"),
    ("run", "course"),
    ("bike", "velo"), ("cycl", "velo"), ("ride", "velo"),
    ("hik", "rando"), ("walk", "rando"),
    ("row", "rameur"), ("ski", "ski"),
    ("strength", "renfo"), ("gym", "renfo"),
]


# --- Source 1 : COROS -------------------------------------------------------
# Champs reellement renvoyes par fetch_activities (modele ActivitySummary,
# verifie sur le serveur le 09/09/2026) :
#   activity_id, name, sport_type (int), sport_name, start_time / end_time
#   (epoch en secondes, sous forme de chaine), duration_seconds,
#   distance_meters, avg_hr, max_hr, calories, training_load,
#   avg_power, normalized_power, elevation_gain, elevation_loss
# Deux pieges qui faisaient tout jeter silencieusement :
#   - la fonction renvoie un TUPLE (liste, total), pas une liste ;
#   - il n'y a pas de champ "date" : start_time est un epoch.
COROS_SNIPPET = r'''
import asyncio, json, datetime, inspect
from zoneinfo import ZoneInfo
import coros_mcp.coros_api as api

# Le serveur tourne en UTC, l'athlete s'entraine en France : sans ce fuseau,
# une seance de fin de soiree serait datee du lendemain.
TZ = ZoneInfo("Europe/Paris")

CANDIDATES = ["fetch_activities", "fetch_sport_records", "query_sport_records",
              "fetch_workouts", "fetch_activity_list", "list_activities"]


def champ(rec, *noms):
    """Les enregistrements peuvent etre des objets ou des dictionnaires."""
    for n in noms:
        v = rec.get(n) if isinstance(rec, dict) else getattr(rec, n, None)
        if v is not None:
            return v
    return None


def deballe(res):
    """fetch_activities renvoie (liste, total). D'autres versions pourraient
    renvoyer la liste nue : on accepte les deux plutot que de tout jeter."""
    if isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], list):
        return res[0], res[1]
    if isinstance(res, list):
        return res, None
    if isinstance(res, dict):
        for k in ("activities", "items", "list", "records", "data"):
            if isinstance(res.get(k), list):
                return res[k], res.get("total")
    return [], None


def jour(rec):
    """start_time est un epoch en secondes rendu sous forme de chaine."""
    v = champ(rec, "start_time", "startTime", "date", "day", "end_time")
    if v is None:
        return None
    s = str(v).strip()
    if s.isdigit() and len(s) >= 10:          # epoch (s ou ms)
        ts = int(s)
        if ts > 1e11:
            ts /= 1000
        return datetime.datetime.fromtimestamp(ts, TZ).date().isoformat()
    return s          # deja une date lisible, normalisee cote appelant


async def main():
    fn_name = next((n for n in CANDIDATES if hasattr(api, n)), None)
    if fn_name is None:
        print(json.dumps({"error": "no_activity_fn",
                          "available": [n for n in dir(api)
                                        if not n.startswith("_") and callable(getattr(api, n))]}))
        return
    fn = getattr(api, fn_name)
    try:
        params = inspect.signature(fn).parameters
        signature = str(inspect.signature(fn))
    except (TypeError, ValueError):
        params, signature = {}, "(?)"

    auth = await api.try_auto_login()
    if auth is None:
        print(json.dumps({"error": "coros_auth_absente",
                          "detail": "try_auto_login() n'a rien renvoye "
                                    "(secrets/coros.env charge ?)"}))
        return

    d0, d1 = "__SINCE__", "__TODAY__"
    # La taille de page par defaut est 30 : sur une fenetre longue on
    # tronquerait sans le voir. On pagine jusqu'au total annonce.
    records, total, pages = [], None, 0
    while pages < 20:
        pages += 1
        kw = {}
        if "size" in params:
            kw["size"] = 100
        if "page" in params:
            kw["page"] = pages
        res = fn(auth, d0, d1, **kw)
        if inspect.isawaitable(res):
            res = await res
        lot, tot = deballe(res)
        if tot is not None:
            total = tot
        records.extend(lot)
        if not lot or "page" not in params:
            break
        if total is not None and len(records) >= total:
            break

    out = []
    for r in records:
        km = champ(r, "distance_km")
        if km is None:
            m = champ(r, "distance_meters", "distance")
            km = m / 1000.0 if isinstance(m, (int, float)) else None
        # COROS compte en calories, pas en kilocalories : 743363 pour une
        # heure de course. On ramene, sans ecraser une valeur deja en kcal.
        kcal = champ(r, "calories", "total_calories")
        if isinstance(kcal, (int, float)) and kcal > 10000:
            kcal = kcal / 1000.0
        out.append({
            "date": jour(r),
            "code": champ(r, "sport_type", "sportType", "type"),
            "sport_label": champ(r, "sport_name", "sportName"),
            "name": champ(r, "name", "label", "location"),
            "seconds": champ(r, "duration_seconds", "total_time", "duration",
                             "workout_time", "moving_time"),
            "km": km,
            "dplus": champ(r, "elevation_gain", "total_ascent", "ascent"),
            "hr": champ(r, "avg_hr", "average_hr", "avg_heart_rate"),
            "kcal": kcal,
            "load": champ(r, "training_load", "load", "trainingLoad"),
        })

    echantillon = None
    if records:
        r0 = records[0]
        if isinstance(r0, dict):
            echantillon = {k: str(v)[:70] for k, v in r0.items()}
        else:
            champs = getattr(type(r0), "model_fields", None) or {}
            noms = list(champs) or [a for a in dir(r0) if not a.startswith("_")
                                    and not callable(getattr(r0, a, None))]
            echantillon = {a: str(getattr(r0, a, None))[:70] for a in noms}
        echantillon["__type__"] = type(r0).__name__
    print(json.dumps({"activities": out, "fn": fn_name, "signature": signature,
                      "recus": len(records), "total_annonce": total,
                      "echantillon": echantillon}, default=str))

asyncio.run(main())
'''


def run_python(interpreter, snippet, env_file=None, timeout=120):
    """Execute un snippet dans le venv d'un connecteur, renvoie le JSON final."""
    env = dict(os.environ)
    if env_file and os.path.exists(env_file):
        for line in open(env_file):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v
    try:
        r = subprocess.run([interpreter, "-c", snippet], capture_output=True,
                           text=True, timeout=timeout, env=env)
        lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
        return json.loads(lines[-1]) if lines else {"error": "empty_output",
                                                    "stderr": r.stderr[-400:]}
    except Exception as exc:
        return {"error": type(exc).__name__, "detail": str(exc)[:300]}


def famille(code, label):
    """Code numerique d'abord, libelle en repli : un sport inconnu doit
    ressortir sous son nom plutot que d'etre noye dans "autre"."""
    if code is not None:
        try:
            fam = SPORT_BY_CODE.get(int(code))
        except (TypeError, ValueError):
            fam = None
        if fam:
            return fam
    texte = (label or "").lower()
    for cle, fam in SPORT_BY_LABEL:
        if cle in texte:
            return fam
    return "autre"


def collect_coros():
    snippet = (COROS_SNIPPET
               .replace("__SINCE__", SINCE.strftime("%Y%m%d"))
               .replace("__TODAY__", TODAY.strftime("%Y%m%d")))
    res = run_python(COROS_PY, snippet, os.path.join(SECRETS, "coros.env"))
    if "error" in res:
        return [], res
    if DEBUG:
        print(f"--- COROS : {res.get('fn')}{res.get('signature')}, "
              f"{res.get('recus')} enregistrement(s) recu(s) "
              f"(total annonce {res.get('total_annonce')}) ---", file=sys.stderr)
        if res.get("echantillon"):
            print("--- attributs reels du premier enregistrement COROS ---",
                  file=sys.stderr)
            print(json.dumps(res["echantillon"], indent=2, ensure_ascii=False)[:1800],
                  file=sys.stderr)
    acts = []
    for a in res.get("activities", []):
        acts.append(normalize(
            source="coros",
            date=a.get("date"),
            sport=famille(a.get("code"), a.get("sport_label")),
            name=a.get("name") or a.get("sport_label"),
            seconds=a.get("seconds"),
            km=a.get("km"),
            dplus=a.get("dplus"),
            hr=a.get("hr"),
            kcal=a.get("kcal"),
            load=a.get("load"),
        ))
    return acts, None


# --- Source 2 : iGPSport (le velo) ------------------------------------------
# Meme approche defensive que le dashboard : les cles varient d'une version a
# l'autre du connecteur, on essaie les alias connus.
IGP_SNIPPET = r'''
import json, os
for line in open("__IGP_ENV__"):
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1)
        os.environ[k] = v
from igpsport_mcp.tools._service import IGPSportService
from igpsport_mcp.config import load_config

svc = IGPSportService(load_config())
acts = svc.list_activities(limit=__LIMIT__)
rows = []
echantillon = None
for a in (acts.get("activities") or acts.get("items") or []):
    rid = a.get("ride_id") or a.get("id") or a.get("activity_id")
    row = {"date": a.get("start_time") or a.get("date"),
           "name": a.get("title") or a.get("name"),
           "km": a.get("distance_km") or a.get("distance"),
           "dplus": a.get("elevation_gain_m"),
           "seconds": a.get("duration_s")}
    resume = None
    try:
        resume = svc.get_activity_summary(rid)
    except Exception as exc:
        resume = {"_erreur": f"{type(exc).__name__}: {str(exc)[:120]}"}
    if isinstance(resume, dict):
        # la duree est a la racine, les metriques d'effort sous "summary"
        det = resume.get("summary")
        det = det if isinstance(det, dict) else {}
        for src, dst, source in [("duration_s", "seconds", resume),
                                 ("elevation_gain_m", "dplus", det),
                                 ("avg_hr_bpm", "hr", det),
                                 ("avg_power_w", "avg_w", det),
                                 ("normalized_power_w", "np_w", det),
                                 ("intensity_factor", "if_", det),
                                 ("work_kj", "kj", det),
                                 ("tss", "load", det)]:
            v = source.get(src)
            if v is not None and row.get(dst) is None:
                row[dst] = v
    if echantillon is None:
        echantillon = {"activite_brute": a, "resume_brut": resume}
    rows.append(row)
print(json.dumps({"activities": rows,
                  "echantillon": echantillon if __DEBUG__ else None}, default=str))
'''


def collect_igpsport():
    # Large marge : on filtre par date ensuite, le connecteur ne sait pas le faire.
    snippet = (IGP_SNIPPET
               .replace("__LIMIT__", str(max(20, DAYS * 2)))
               .replace("__DEBUG__", "True" if DEBUG else "False")
               .replace("__IGP_ENV__", os.path.join(SECRETS, "igpsport.env")))
    res = run_python(IGP_PY, snippet)
    if "error" in res:
        return [], res
    if DEBUG and res.get("echantillon"):
        print("--- echantillon brut iGPSport (cles reellement disponibles) ---",
              file=sys.stderr)
        print(json.dumps(res["echantillon"], indent=2, ensure_ascii=False)[:2000],
              file=sys.stderr)
    acts = []
    for a in res.get("activities", []):
        acts.append(normalize(
            source="igpsport",
            date=a.get("date"),
            sport="velo",
            name=a.get("name"),
            seconds=a.get("seconds"),
            km=a.get("km"),
            dplus=a.get("dplus"),
            hr=a.get("hr"),
            kcal=a.get("kcal"),
            load=a.get("load"),
            avg_w=a.get("avg_w"),
            np_w=a.get("np_w"),
            if_=a.get("if_"),
            kj=a.get("kj"),
        ))
    return acts, None


# --- Normalisation et agregats ----------------------------------------------
def parse_date(value):
    """COROS renvoie un epoch (deja converti en ISO par le snippet, mais on
    reste tolerant), iGPSport un datetime ISO."""
    if value is None:
        return None
    s = str(value).strip().replace("/", "-")
    if s.isdigit() and len(s) >= 10:
        ts = int(s)
        return datetime.datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts).date()
    for length, fmt in ((10, "%Y-%m-%d"), (8, "%Y%m%d")):
        try:
            return datetime.datetime.strptime(s[:length], fmt).date()
        except ValueError:
            continue
    return None


def normalize(source, date, sport, name, seconds, km, dplus, hr, kcal, load,
              avg_w=None, np_w=None, if_=None, kj=None):
    d = parse_date(date)
    minutes = round(seconds / 60.0, 1) if isinstance(seconds, (int, float)) else None
    act = {
        "date": d.isoformat() if d else None,
        "sport": sport,
        "nom": name,
        "minutes": minutes,
        "km": round(km, 1) if isinstance(km, (int, float)) else None,
        "dplus_m": round(dplus) if isinstance(dplus, (int, float)) else None,
        "fc_moy": round(hr) if isinstance(hr, (int, float)) else None,
        "kcal": round(kcal) if isinstance(kcal, (int, float)) else None,
        "charge": round(load) if isinstance(load, (int, float)) else None,
        "source": source,
    }
    if avg_w is not None:
        act["watts_moy"] = round(avg_w)
    if np_w is not None:
        act["np_w"] = round(np_w)
    if if_ is not None:
        act["intensite"] = round(if_, 2)
    if kj is not None:
        act["kj"] = round(kj)
    # Le TSS iGPSport et le training_load COROS sont de vraies charges et
    # servent tels quels. Si aucune n'est fournie, estimation grossiere mais
    # coherente : duree ponderee par le denivele, explicitement marquee.
    if act["charge"] is None and minutes:
        est = minutes + (act["dplus_m"] or 0) / 100.0 * 3
        act["charge"] = round(est)
        act["charge_estimee"] = True
    return act


def proche(a, b, tolerance=0.15):
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    if max(a, b) == 0:
        return True
    return abs(a - b) / max(a, b) <= tolerance


def fusionne_velo(acts):
    """Une meme sortie velo peut etre vue par les deux sources si la montre
    tournait en plus du compteur : sans cela sa charge serait comptee deux
    fois. On ne rapproche que le meme jour, avec duree ET distance voisines,
    pour ne pas confondre deux sorties distinctes du meme jour (le 29/08 :
    3 km de liaison a la montre et 76 km au compteur). Le compteur fait foi,
    c'est lui qui porte la puissance."""
    igp = [a for a in acts if a["source"] == "igpsport"]
    garde, doublons = [], 0
    for a in acts:
        if a["source"] == "coros" and a["sport"] == "velo":
            jumelle = next((b for b in igp
                            if b["date"] == a["date"]
                            and proche(a["minutes"], b["minutes"])
                            and proche(a["km"], b["km"])), None)
            if jumelle is not None:
                jumelle["vue_aussi_par"] = "coros"
                doublons += 1
                continue
        garde.append(a)
    return garde, doublons


def aggregate(acts, days):
    floor = TODAY - datetime.timedelta(days=days)
    window = [a for a in acts if a["date"] and
              datetime.date.fromisoformat(a["date"]) > floor]
    by_sport = {}
    for a in window:
        s = by_sport.setdefault(a["sport"], {"seances": 0, "minutes": 0,
                                             "km": 0, "dplus_m": 0, "charge": 0})
        s["seances"] += 1
        for k in ("minutes", "km", "dplus_m", "charge"):
            if a.get(k):
                s[k] += a[k]
    for s in by_sport.values():
        s["minutes"] = round(s["minutes"])
        s["km"] = round(s["km"], 1)
    return by_sport


def main():
    coros_acts, coros_err = collect_coros()
    igp_acts, igp_err = collect_igpsport()

    acts, doublons = fusionne_velo(coros_acts + igp_acts)
    sans_date = [a for a in acts if not a["date"]]
    acts = [a for a in acts
            if a["date"] and datetime.date.fromisoformat(a["date"]) >= SINCE]
    acts.sort(key=lambda a: a["date"], reverse=True)

    # Une source qui repond mais dont rien n'est exploitable ne doit pas
    # passer pour un succes : c'est ainsi que le velo est reste invisible.
    retenus = {"coros": 0, "igpsport": 0}
    for a in acts:
        retenus[a["source"]] += 1

    out = {
        "aujourdhui": TODAY.isoformat(),
        "fenetre_jours": DAYS,
        "activites": acts,
        "agregats_7j": aggregate(acts, 7),
        "agregats_28j": aggregate(acts, 28),
        "sources_en_echec": {k: v for k, v in
                             [("coros", coros_err), ("igpsport", igp_err)] if v},
        "sources_muettes": [nom for nom, brut, net in
                            [("coros", coros_acts, retenus["coros"]),
                             ("igpsport", igp_acts, retenus["igpsport"])]
                            if not net],
        "diagnostic": {
            "recus": {"coros": len(coros_acts), "igpsport": len(igp_acts)},
            "retenus": retenus,
            "sans_date_ignorees": len(sans_date),
            "doublons_velo_fusionnes": doublons,
        },
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
