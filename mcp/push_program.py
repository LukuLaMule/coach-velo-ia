"""Pousse/recalibre les seances du programme sur iGPSport (watts = %FTP x FTP config).
Supprime d'abord les anciennes P01-P17 pour eviter les doublons.

Les seances elles-memes vivent dans ~/monitoring/coach_velo_seances.py : le
tableau de bord y lit les memes segments pour produire les fichiers .zwo/.erg
telechargeables. Une seule source, donc pas de derive entre le compteur et la page.

Options :
  --si-manquant  ne pousse que si des seances P0x ont disparu du compteur.
                 Destine au coach du matin, qui peut le lancer sans risque.
                 Attention : cette option ne voit pas un changement de FTP, les
                 watts etant compiles a l'ecriture. Apres un retest, relancer
                 sans option pour tout recalibrer.
  --dry-run      montre ce qui serait pousse, n'ecrit rien.

Le listing passe par lister_seances() : sur le serveur international,
svc.list_workouts() renvoie une liste vide alors que les seances existent, et
la suppression ne supprimait donc rien. Voir le commentaire de _listing_brut.
"""
import os, re, sys
from igpsport_mcp.tools._service import IGPSportService
from igpsport_mcp.config import load_config

sys.path.insert(0, os.path.expanduser("~/monitoring"))
from coach_velo_seances import catalogue, code as code_seance

SI_MANQUANT = "--si-manquant" in sys.argv
DRY = "--dry-run" in sys.argv

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

WORKOUTS = catalogue(FTP)

old, fiable = lister_seances()
if not fiable:
    # Pousser sans avoir pu lister, c'est empiler un jeu de doublons de plus.
    # Mieux vaut ne rien faire et le dire.
    sys.exit("Listing des seances existantes impossible : rien n'a ete pousse "
             "(sinon on ajouterait 17 doublons de plus).")

presentes = {code_seance(wk.get("title") or wk.get("name") or "") for wk in old}
manquantes = sorted({code_seance(w["title"]) for w in WORKOUTS} - presentes - {None})
if SI_MANQUANT and not manquantes:
    # Rien a faire est un succes, pas une erreur : le coach teste le code retour.
    print(f"Compteur a jour : les {len(WORKOUTS)} seances y sont, rien a pousser.")
    sys.exit(0)
if SI_MANQUANT:
    print("Manquent sur le compteur :", ", ".join(manquantes))
if DRY:
    print(f"--dry-run : {len(WORKOUTS)} seances seraient repoussees (FTP {FTP}W).")
    sys.exit(0)

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
