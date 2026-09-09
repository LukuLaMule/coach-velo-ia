#!/usr/bin/env python
"""Supprime les seances P0x en double sur l'iGPSport.

Contexte : sur le serveur international, `list_workouts()` renvoie une liste
vide alors que les seances existent. push_program.py s'en sert pour supprimer
les anciennes avant de repousser, donc la suppression ne fait rien et chaque
push empile 17 doublons.

Ce script essaie plusieurs facons de lister les seances. S'il y arrive, il
regroupe par titre et ne garde que la plus recente de chaque groupe, d'apres
son horodatage de creation, en supprimant les autres. Les doublons sont des
copies identiques (memes cibles, memes descriptions) : seule leur date les
distingue. S'il n'y arrive pas, il affiche de quoi
comprendre pourquoi plutot que de faire semblant d'avoir nettoye.

PAR DEFAUT IL NE SUPPRIME RIEN : il montre ce qu'il ferait. Ajouter --apply
pour executer reellement.

Usage :
    set -a; source ~/mcp/secrets/igpsport.env; set +a
    ~/mcp/igpsport-mcp/.venv/bin/python mcp/igpsport_cleanup.py           # apercu
    ~/mcp/igpsport-mcp/.venv/bin/python mcp/igpsport_cleanup.py --apply   # nettoyage
"""
import datetime
import inspect
import re
import sys
import textwrap

from igpsport_mcp.tools._service import IGPSportService
from igpsport_mcp.config import load_config

APPLY = "--apply" in sys.argv
TITLE_RE = re.compile(r"^P\d{2}\b")


SECRET_RE = re.compile(r"token|cookie|auth|password|passwd|secret|key|session|bearer|cred",
                       re.IGNORECASE)


def redact(name, value):
    """Masque toute valeur dont le nom evoque un secret : la sortie de ce
    script est destinee a etre collee dans une conversation."""
    if SECRET_RE.search(name):
        text = str(value)
        return f"<masque, {len(text)} caracteres>"
    return repr(value)


def extract(res):
    """Sort une liste de seances d'une reponse de forme variable."""
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        for key in ("workouts", "items", "data", "list", "records", "rows"):
            val = res.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):  # parfois {"data": {"list": [...]}}
                for sub in ("list", "items", "records", "rows"):
                    if isinstance(val.get(sub), list):
                        return val[sub]
    return []


def raw_listing():
    """Refait la requete de client.list_workouts sans son filtre final.

    client.list_workouts() se termine par :
        return list(result) if isinstance(result, list) else []
    Sur le serveur international la reponse est un objet et non une liste nue,
    donc tout est jete et la fonction renvoie []. C'est la cause du "deleted 0"
    de push_program.py. On refait ici la meme requete et on lit la reponse
    telle qu'elle arrive.

    Passe par des attributs privees du connecteur : c'est l'appel HTTP brut
    que le README preconise. Il peut casser a une mise a jour d'igpsport-mcp,
    d'ou le repli sur le chemin normal en premier.
    """
    client = getattr(svc, "client", None)
    if client is None:
        return None
    profile = client._profile
    endpoints = getattr(sys.modules.get(type(client).__module__), "ep", None)
    if endpoints is not None:
        path = profile.resolve_path(endpoints.PATH_WORKOUT_LIST,
                                    profile.path_workout_list)
    else:
        path = profile.path_workout_list
    query = ("?PageIndex=1&PageSize=200" if profile.key == "intl"
             else "?pageNo=1&pageSize=200")
    return client._request_business("GET", path + query, jwt=client._jwt(),
                                    **client._WO_HDR)


def find_listing():
    """Essaie les signatures et methodes plausibles, renvoie (nom, seances)."""
    # list_workouts() ne prend aucun argument (verifie sur le serveur) : inutile
    # de tenter limit/page/size, ils levent tous un TypeError.
    attempts = [("list_workouts()", "list_workouts", {})]
    # d'autres methodes du service peuvent lister les seances
    for name in dir(svc):
        if name.startswith("_") or name in ("list_workouts",):
            continue
        if "workout" in name and any(k in name for k in ("list", "get", "query", "all")):
            attempts.append((f"{name}()", name, {}))

    for label, name, kwargs in attempts:
        fn = getattr(svc, name, None)
        if not callable(fn):
            continue
        try:
            items = extract(fn(**kwargs))
        except Exception as exc:
            print(f"  {label:<34} {type(exc).__name__}: {str(exc)[:70]}")
            continue
        print(f"  {label:<34} {len(items)} seance(s)")
        if items:
            return label, items

    # Le chemin normal jette la reponse du serveur international : on refait
    # la requete nous-memes.
    try:
        raw = raw_listing()
        items = extract(raw)
        print(f"  {'appel HTTP brut':<34} {len(items)} seance(s)")
        if items:
            return "appel HTTP brut", items
        if raw is not None:
            forme = type(raw).__name__
            cles = list(raw)[:10] if isinstance(raw, dict) else ""
            print(f"    reponse : {forme} {cles}")
    except Exception as exc:
        print(f"  {'appel HTTP brut':<34} {type(exc).__name__}: {str(exc)[:80]}")

    return None, []


def wid(w):
    for k in ("workout_id", "id", "workoutId", "trainingId"):
        v = w.get(k)
        if v is not None:
            return int(v)
    return None


def title(w):
    return (w.get("title") or w.get("name") or "").strip()


def cree_le(w):
    """Horodatage de creation, en secondes. C'est le seul critere fiable :
    les doublons sont des copies identiques, descriptions comprises."""
    for k in ("createTimestamp", "createTime", "created_at", "createdAt"):
        v = w.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return v / 1000 if v > 1e11 else v
    return None


def date_lisible(w):
    ts = cree_le(w)
    if ts is None:
        return "date inconnue"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


svc = IGPSportService(load_config())

print("=== Recherche d'une methode de listing qui fonctionne ===")
label, workouts = find_listing()

if not workouts:
    print("\nAucune methode ne renvoie de seances. Elements utiles pour la suite :")
    print("\n  Methodes du service liees aux seances :")
    for name in sorted(dir(svc)):
        if not name.startswith("_") and "workout" in name and callable(getattr(svc, name, None)):
            try:
                sig = str(inspect.signature(getattr(svc, name)))
            except (TypeError, ValueError):
                sig = "(?)"
            print(f"    svc.{name}{sig}")
    print("\n  Code de svc.list_workouts (quel endpoint est appele) :")
    try:
        print(textwrap.indent(inspect.getsource(svc.list_workouts).strip(), "    "))
    except Exception as exc:
        print(f"    indisponible : {exc}")

    client = getattr(svc, "client", None)
    if client is not None:
        print(f"\n  Methodes de {type(client).__name__} :")
        for name in sorted(dir(client)):
            fn = getattr(client, name, None)
            if name.startswith("_") or not callable(fn):
                continue
            try:
                sig = str(inspect.signature(fn))
            except (TypeError, ValueError):
                sig = "(?)"
            print(f"    client.{name}{sig}")

        print("\n  Code de client.list_workouts (l'appel HTTP reel) :")
        try:
            print(textwrap.indent(inspect.getsource(client.list_workouts).strip(), "    "))
        except Exception as exc:
            print(f"    indisponible : {exc}")

        # la methode de requete sous-jacente porte l'URL de base et le routage
        # par region : c'est la que se joue le comportement du serveur intl.
        for helper in ("_request", "_get", "_post", "_call", "request"):
            fn = getattr(client, helper, None)
            if not callable(fn):
                continue
            try:
                src = inspect.getsource(fn).strip()
            except Exception:
                continue
            print(f"\n  Code de client.{helper} (tronque) :")
            print(textwrap.indent(src[:1200], "    "))
            break

        print("\n  Reglages du client (secrets masques) :")
        for name in sorted(dir(client)):
            if name.startswith("_"):
                continue
            val = getattr(client, name, None)
            if not isinstance(val, (str, int, bool)):
                continue
            print(f"    client.{name} = {redact(name, val)}")
    sys.exit(1)

print(f"\n=== {len(workouts)} seance(s) listee(s) via {label} ===")

groups = {}
for w in workouts:
    t = title(w)
    if TITLE_RE.match(t):
        groups.setdefault(t, []).append(w)

doublons = {t: ws for t, ws in groups.items() if len(ws) > 1}
if not doublons:
    print("Aucun doublon : chaque seance P0x est unique. Rien a faire.")
    sys.exit(0)

print(f"{len(doublons)} titre(s) en double.\n")
a_supprimer = []
for t in sorted(doublons):
    ws = sorted(doublons[t], key=lambda w: (cree_le(w) or 0, wid(w) or 0), reverse=True)
    garde, jette = ws[0], ws[1:]
    print(f"  {t}")
    print(f"    GARDE    id={wid(garde)}  creee le {date_lisible(garde)}")
    for w in jette:
        print(f"    SUPPRIME id={wid(w)}  creee le {date_lisible(w)}")
        a_supprimer.append((t, wid(w)))

print(f"\n{len(a_supprimer)} seance(s) a supprimer.")

if not APPLY:
    print("\nApercu uniquement — rien n'a ete supprime.")
    print("Les doublons sont des copies identiques : on conserve simplement la plus")
    print("recente. Verifie que les lignes GARDE portent bien la date du dernier")
    print("push, puis relance avec --apply.")
    sys.exit(0)

ok = ko = 0
for t, i in a_supprimer:
    try:
        svc.delete_workout(int(i), confirm=True)
        ok += 1
    except Exception as exc:
        ko += 1
        print(f"  echec {t} id={i} : {str(exc)[:80]}")
print(f"\nSupprimees : {ok}/{len(a_supprimer)}" + (f", {ko} en echec" if ko else ""))
