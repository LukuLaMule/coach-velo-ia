"""Pousse/recalibre les seances du programme sur iGPSport (watts = %FTP x FTP config).
Supprime d'abord les anciennes P01-P17 pour eviter les doublons.

Le listing passe par lister_seances() : sur le serveur international,
svc.list_workouts() renvoie une liste vide alors que les seances existent, et
la suppression ne supprimait donc rien. Voir le commentaire de _listing_brut.
"""
import os, re, sys
from igpsport_mcp.tools._service import IGPSportService
from igpsport_mcp.config import load_config

svc = IGPSportService(load_config())
# Valeur du test du 10/08/2026 (305 W sur 20 min). A remonter apres chaque
# test FTP, sinon les cibles poussees sont fausses : elles sont compilees en
# watts absolus au moment du push.
FTP = int(os.environ.get("IGPSPORT_FTP", "290"))


def _extrait(res):
    """Sort une liste de seances d'une reponse de forme variable."""
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        for cle in ("workouts", "items", "data", "list", "records", "rows"):
            val = res.get(cle)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):          # parfois {"data": {"list": [...]}}
                for sous in ("list", "items", "records", "rows"):
                    if isinstance(val.get(sous), list):
                        return val[sous]
    return []


def _listing_brut():
    """Refait la requete de client.list_workouts sans son filtre final.

    client.list_workouts() se termine par :
        return list(result) if isinstance(result, list) else []
    Sur le serveur international la reponse est un objet et non une liste nue :
    tout est jete et la fonction renvoie []. C'est la cause du "deleted 0" qui
    faisait empiler 17 doublons a chaque push. On refait ici la meme requete et
    on lit la reponse telle qu'elle arrive.

    Passe par des attributs prives du connecteur, donc susceptible de casser a
    une mise a jour d'igpsport-mcp : d'ou le chemin normal essaye en premier.
    """
    client = getattr(svc, "client", None)
    if client is None:
        return None
    profil = client._profile
    endpoints = getattr(sys.modules.get(type(client).__module__), "ep", None)
    if endpoints is not None:
        chemin = profil.resolve_path(endpoints.PATH_WORKOUT_LIST,
                                     profil.path_workout_list)
    else:
        chemin = profil.path_workout_list
    requete = ("?PageIndex=1&PageSize=200" if profil.key == "intl"
               else "?pageNo=1&pageSize=200")
    return client._request_business("GET", chemin + requete, jwt=client._jwt(),
                                    **client._WO_HDR)


def lister_seances():
    """Renvoie (seances, fiable). fiable=False signale qu'aucune methode n'a
    pu repondre : une liste vide veut alors dire "je ne sais pas", pas
    "le compte est vide". C'est la distinction qui manquait."""
    try:
        seances = _extrait(svc.list_workouts())
        if seances:
            return seances, True
    except Exception as exc:
        print(f"list_workouts() a echoue : {type(exc).__name__}: {str(exc)[:80]}")

    try:
        brut = _listing_brut()
    except Exception as exc:
        print(f"appel HTTP brut en echec : {type(exc).__name__}: {str(exc)[:80]}")
        return [], False
    if brut is None:
        return [], False
    seances = _extrait(brut)
    if seances:
        print(f"list_workouts() n'a rien renvoye, appel HTTP brut : "
              f"{len(seances)} seance(s)")
        return seances, True
    # Le serveur a repondu quelque chose d'exploitable mais vide : compte
    # reellement vierge. On distingue ce cas d'une reponse incomprise.
    if isinstance(brut, (list, dict)):
        return [], True
    return [], False

def w(lo, hi):
    return f"{round(lo*FTP/100)}-{round(hi*FTP/100)}W ({lo}-{hi}% FTP)"

def step(name, intensity, secs, lo=None, hi=None, note=None):
    s = {"name": name, "intensity": intensity,
         "duration": {"type": "time", "value": secs}}
    if lo is not None:
        s["target"] = {"type": "power_percent_ftp", "value": 0, "min": lo, "max": hi}
    if note:
        s["note"] = note
    return s

def lap(name, intensity, lo=None, hi=None, note=None):
    s = {"name": name, "intensity": intensity, "duration": {"type": "lap_button"}}
    if lo is not None:
        s["target"] = {"type": "power_percent_ftp", "value": 0, "min": lo, "max": hi}
    if note:
        s["note"] = note
    return s

def rep(times, steps):
    return {"type": "repeat", "times": times, "steps": steps}

WU = step("Echauffement", "warmup", 900, 50, 70)
WU_LONG = step("Echauffement progressif", "warmup", 1200, 50, 75)
CD = step("Retour au calme", "cooldown", 600, 40, 55)

WORKOUTS = [
 {"title": "P01 Test FTP 20min",
  "description": "Semaines 1 et 12. Apres echauffement + 3x1min haute cadence, 20min a fond regulier. FTP = 95% de la puissance moyenne des 20min. Communiquer le resultat.",
  "steps": [WU_LONG,
            rep(3, [step("1min cadence 100+", "active", 60, 90, 100),
                    step("Recup", "rest", 60, 45, 55)]),
            step("Tranquille", "rest", 300, 50, 60),
            step("TEST 20min A FOND", "active", 1200, 95, 120, "Regulier, a bloc"),
            CD]},
 {"title": "P02 Sweet Spot 2x15",
  "description": f"Semaine 1. 2x15min a {w(88,93)}, recup 5min.",
  "steps": [WU, rep(2, [step("Sweet spot 15min", "active", 900, 88, 93),
                        step("Recup", "rest", 300, 45, 55)]), CD]},
 {"title": "P03 Sweet Spot 3x15",
  "description": f"Semaines 2-3. 3x15min a {w(88,93)}, recup 5min.",
  "steps": [WU, rep(3, [step("Sweet spot 15min", "active", 900, 88, 93),
                        step("Recup", "rest", 300, 45, 55)]), CD]},
 {"title": "P04 Sweet Spot 2x20",
  "description": f"Semaines 3-6 et 10. 2x20min a {w(88,93)}, recup 5min.",
  "steps": [WU, rep(2, [step("Sweet spot 20min", "active", 1200, 88, 93),
                        step("Recup", "rest", 300, 45, 55)]), CD]},
 {"title": "P05 Sweet Spot 1x20 leger",
  "description": f"Semaines 4 et 8 (recup). Un seul bloc de 20min a {w(85,90)}.",
  "steps": [WU, step("Sweet spot 20min", "active", 1200, 85, 90), CD]},
 {"title": "P06 Seuil 3x10",
  "description": f"Semaine 5. 3x10min a {w(96,102)}, recup 5min.",
  "steps": [WU, rep(3, [step("Seuil 10min", "active", 600, 96, 102),
                        step("Recup", "rest", 300, 45, 55)]), CD]},
 {"title": "P07 Seuil 2x15",
  "description": f"Semaines 6 et 9. 2x15min a {w(96,100)}, recup 6min.",
  "steps": [WU, rep(2, [step("Seuil 15min", "active", 900, 96, 100),
                        step("Recup", "rest", 360, 45, 55)]), CD]},
 {"title": "P08 Seuil 3x12",
  "description": f"Semaine 7. 3x12min a {w(96,100)}, recup 6min. Grosse seance.",
  "steps": [WU, rep(3, [step("Seuil 12min", "active", 720, 96, 100),
                        step("Recup", "rest", 360, 45, 55)]), CD]},
 {"title": "P09 Over-Unders 3x9",
  "description": "Semaine 7. 3 blocs de 9min alternant 2min a 95% / 1min a 105% FTP, recup 5min.",
  "steps": [WU, rep(3, [rep(3, [step("Under 2min 95%", "active", 120, 92, 97),
                                step("Over 1min 105%", "active", 60, 103, 108)]),
                        step("Recup", "rest", 300, 45, 55)]), CD]},
 {"title": "P10 VO2max 5x3",
  "description": f"Semaine 9. 5x3min a {w(112,118)}, recup 3min. Dur mais payant.",
  "steps": [WU_LONG, rep(5, [step("VO2 3min", "active", 180, 112, 118),
                             step("Recup", "rest", 180, 45, 55)]), CD]},
 {"title": "P11 VO2max 6x3",
  "description": f"Semaine 10. 6x3min a {w(112,118)}, recup 3min.",
  "steps": [WU_LONG, rep(6, [step("VO2 3min", "active", 180, 112, 118),
                             step("Recup", "rest", 180, 45, 55)]), CD]},
 {"title": "P12 VO2max 4x4",
  "description": f"Semaine 11. 4x4min a {w(108,114)}, recup 4min.",
  "steps": [WU_LONG, rep(4, [step("VO2 4min", "active", 240, 108, 114),
                             step("Recup", "rest", 240, 45, 55)]), CD]},
 {"title": "P13 VO2max 30-30 2x10",
  "description": "Semaine 11. 2 series de 10x(30s a 120% / 30s a 60% FTP), recup 5min entre series.",
  "steps": [WU_LONG, rep(2, [rep(10, [step("30s fort", "active", 30, 115, 125),
                                      step("30s souple", "rest", 30, 55, 65)]),
                             step("Recup serie", "rest", 300, 45, 55)]), CD]},
 {"title": "P14 VO2max 3x3 rappel",
  "description": f"Semaine 12 (affutage). 3x3min a {w(110,115)}, recup 3min.",
  "steps": [WU, rep(3, [step("VO2 3min", "active", 180, 110, 115),
                        step("Recup", "rest", 180, 45, 55)]), CD]},
 {"title": "P15 Endurance Z2 2h",
  "description": f"Toutes les semaines. 2h a {w(60,72)}, cadence souple.",
  "steps": [step("Mise en route", "warmup", 600, 50, 62),
            step("Endurance Z2", "active", 6000, 60, 72),
            step("Retour au calme", "cooldown", 600, 45, 55)]},
 {"title": "P16 Recup Z1 1h",
  "description": f"Semaines 4, 8 et 12. 1h tres facile a {w(45,58)}, ne pas depasser.",
  "steps": [step("Tres facile", "active", 3600, 45, 58)]},
 {"title": "P17 Ventoux Bedoin",
  "description": f"15 aout. Pacing : {w(78,86)} sur toute la montee, max {round(0.91*FTP)}W dans la foret. LAP a Saint-Esteve et au Chalet Reynard.",
  "steps": [lap("Approche Z2", "warmup", 55, 70, "LAP a Saint-Esteve"),
            lap("Foret 9-10%", "active", 78, 86, "LAP au Chalet Reynard"),
            lap("Final decouvert", "active", 78, 88, "Gerer le vent, finir fort"),
            lap("Descente + retour", "cooldown")],
 },
]

old, fiable = lister_seances()
if not fiable:
    # Pousser sans avoir pu lister, c'est empiler un jeu de doublons de plus.
    # Mieux vaut ne rien faire et le dire.
    sys.exit("Listing des seances existantes impossible : rien n'a ete pousse "
             "(sinon on ajouterait 17 doublons de plus).")

deleted = 0
for wk in old:
    title = wk.get("title") or wk.get("name") or ""
    if re.match(r"^P\d{2} ", title):
        wid = wk.get("workout_id") or wk.get("id") or wk.get("workoutId")
        try:
            svc.delete_workout(int(wid), confirm=True)
            deleted += 1
        except Exception as e:
            print("del KO:", title, str(e)[:80])
print(f"deleted {deleted} old workouts (sur {len(old)} listee(s))")

ok, ko = [], []
for wkt in WORKOUTS:
    try:
        r = svc.create_workout(wkt, dry_run=False)
        ok.append(wkt["title"]) if r.get("success") else ko.append((wkt["title"], str(r)[:100]))
    except Exception as e:
        ko.append((wkt["title"], str(e)[:100]))
print(f"PUSHED {len(ok)}/{len(WORKOUTS)} (FTP {FTP}W)")
for t, e in ko:
    print(" KO:", t, "|", e)
