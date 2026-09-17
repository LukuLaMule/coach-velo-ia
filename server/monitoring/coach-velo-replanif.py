#!/home/opc/mcp/coros-mcp/.venv/bin/python
"""Repere les seances velo non faites et les repousse dans le programme.

Pourquoi un script et pas une consigne au coach : reecrire le plan est la seule
ecriture vraiment destructrice du projet. Les regles de report sont donc figees
ici — verifiables, rejouables, identiques d'un matin a l'autre — et le coach ne
fait que declencher, puis raconter.

Mecanique : a trois ou quatre seances par semaine il n'existe aucun trou ou
glisser un rattrapage. Les dates sont donc traitees comme des creneaux : la
seance manquee reprend la tete de la file, les suivantes reculent d'un cran, et
le rythme prevu (jours d'entrainement, semaines, phases) reste intact.

Garde-fous, dans l'ordre ou ils s'appliquent :
  - une source d'activites en echec interdit d'ecrire quoi que ce soit : une
    liste vide veut dire "je ne sais pas", pas "il n'a rien fait" (meme piege
    que le list_workouts d'iGPSport) ;
  - on ne juge que le passe, jamais la seance du jour, qui est encore a faire ;
  - au plus --max rattrapages par matin (2 par defaut), les plus recents :
    au-dela ce n'est plus un oubli mais une interruption, et empiler dix
    seances en retard ne ferait que fabriquer la semaine ratee suivante ;
  - une seance deja rattrapee une fois et manquee a nouveau est abandonnee :
    le programme finit le 31 octobre, il ne s'etire pas indefiniment ;
  - une seance abandonnee est marquee comme telle dans le plan, sinon le
    lendemain matin la verrait toujours "en retard" et decalerait le programme
    un cran de plus, chaque jour ;
  - le dernier creneau est une ancre (le retest FTP) : il ne bouge jamais ;
  - ce qui ne rentre plus n'est pas "la fin du programme" mais les seances les
    moins structurantes de ce qui reste (recup, puis endurance) : decaler ne
    doit pas manger l'affutage juste avant le retest.

Ce script ne verifie pas qu'il ne colle pas deux seances dures : c'est le
verdict du matin qui tranche ce cas-la, en degradant la seance du jour.

4e colonne du plan, ecrite ici : `rattrapee:<date>` (seance manquee replacee,
une seule fois), `decalee:<date>` (seance qui a simplement recule d'un cran) ou
`abandonnee` (seance passee, non faite, non rattrapable).
Les trois generateurs de pages lisent les trois premieres colonnes et ignorent
celle-la.

Usage : coach-velo-replanif.py [--depuis N] [--max N] [--activites f.json]
                               [--apply] [--json]
Sans --apply, rien n'est ecrit : c'est un apercu.
"""
import datetime
import json
import os
import re
import subprocess
import sys

MON = os.path.dirname(os.path.abspath(__file__))
PLAN = f"{MON}/coach-velo-plan.tsv"
sys.path.insert(0, MON)
from coach_velo_seances import catalogue, aplatir, code as code_seance

DEPUIS = 10          # jours de passe examines
MAX_REPORTS = 2      # rattrapages appliques en un matin
SEUIL_FAIT = 0.5     # part de la duree prevue a partir de laquelle on dit "faite"

# Duree de chaque P0x, prise sur les segments du catalogue. La FTP passee a
# `catalogue` ne change que le texte des descriptions, pas les durees.
DUREES_P = {}
for _w in catalogue(290):
    _segs = aplatir(_w["steps"])
    DUREES_P[code_seance(_w["title"])] = round(sum(s["secs"] for s in _segs) / 60) if _segs else None


def arg(nom, defaut):
    return int(sys.argv[sys.argv.index(nom) + 1]) if nom in sys.argv else defaut


def argstr(nom, defaut=None):
    return sys.argv[sys.argv.index(nom) + 1] if nom in sys.argv else defaut


def charge_plan():
    lignes = []
    for line in open(PLAN):
        p = line.rstrip("\n").split("\t")
        if len(p) < 3:
            continue
        lignes.append({"date": p[0], "seance": p[1], "phase": p[2],
                       "marque": p[3] if len(p) > 3 else ""})
    lignes.sort(key=lambda l: l["date"])
    return lignes


def ecrit_plan(lignes):
    """Sauvegarde du jour puis reecriture, triee par date."""
    with open(PLAN) as f:
        avant = f.read()
    with open(f"{PLAN}.bak-{datetime.date.today().isoformat()}", "w") as f:
        f.write(avant)
    with open(PLAN, "w") as f:
        for l in sorted(lignes, key=lambda x: x["date"]):
            cols = [l["date"], l["seance"], l["phase"]]
            if l["marque"]:
                cols.append(l["marque"])
            f.write("\t".join(cols) + "\n")


def duree_prevue(nom):
    """Minutes attendues : somme des segments pour les P0x, duree annoncee dans
    le titre pour les sorties dehors (SL ... 3h30)."""
    c = code_seance(nom)
    if c and DUREES_P.get(c):
        return DUREES_P[c]
    m = re.search(r"(\d+)\s*h\s*(\d{2})?", nom)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2) or 0)
    m = re.search(r"(\d+)\s*min", nom)
    if m:
        return int(m.group(1))
    return 60


def collecte(jours, fichier=None):
    """Les activites reelles. `fichier` sert a reutiliser le JSON que le coach
    vient de recuperer, plutot que de refaire deux minutes d'API."""
    if fichier and os.path.exists(fichier):
        try:
            d = json.load(open(fichier))
        except Exception:
            d = {}
        # Un JSON d'hier, ou tronque par une collecte en echec, ferait passer
        # toutes les seances pour manquees : on ne s'en sert que s'il est du
        # jour et complet, sinon on recollecte.
        if d.get("aujourdhui") == datetime.date.today().isoformat() and "activites" in d:
            return d
        print("JSON d'activites inutilisable (absent, tronque ou perime) : "
              "nouvelle collecte.", file=sys.stderr)
    r = subprocess.run([sys.executable, f"{MON}/coach-velo-activities.py", str(jours)],
                       capture_output=True, text=True, timeout=600)
    sortie = [l for l in r.stdout.strip().splitlines() if l.startswith("{")]
    if not sortie:
        raise SystemExit("collecte des activites impossible : rien n'a ete modifie.\n"
                         + (r.stderr or "")[-400:])
    return json.loads(sortie[-1])


def velo_par_jour(acts):
    par_jour = {}
    for a in acts.get("activites", []):
        if a.get("sport") == "velo" and a.get("date"):
            par_jour[a["date"]] = par_jour.get(a["date"], 0) + (a.get("minutes") or 0)
    return par_jour


# Ordre dans lequel on sacrifie une seance quand tout ne rentre plus : la recup
# part la premiere, le test et les seances qualitatives en dernier.
PRIORITE = {"P16": 0, "SR": 0, "P05": 1, "P15": 1, "SP": 1, "SV": 2,
            "P02": 3, "P03": 3, "P04": 3, "SL": 3, "P01": 9}


def priorite(nom):
    cle = code_seance(nom) or nom.split(" ")[0]
    if cle in PRIORITE:
        return PRIORITE[cle]
    return 4 if code_seance(nom) else 2   # P06-P14 = qualitatif, reste = sortie libre


def decale(lignes, a_rattraper, today):
    """Reassigne les seances aux creneaux a venir : rattrapages en tete, le
    reste dans l'ordre, l'ancre finale a sa place.

    Les colonnes date et phase decrivent le creneau, pas la seance : elles ne
    suivent donc pas la seance qui recule. Sinon une seance etiquetee S6 se
    retrouverait posee dans la semaine S7 et disparaitrait du calendrier des
    pages, qui groupe les jours par code de semaine.
    """
    creneaux = [l for l in lignes if l["date"] > today.isoformat()]
    if len(creneaux) < 2:
        return [], [{"seance": m["seance"], "date": m["date"],
                     "raison": "il ne reste plus de creneau avant la fin du programme"}
                    for m in a_rattraper], 0

    ouverts, ancre = creneaux[:-1], creneaux[-1]
    file = ([{"seance": m["seance"], "marque": f"rattrapee:{m['date']}"} for m in a_rattraper]
            + [{"seance": c["seance"], "marque": c["marque"], "date": c["date"]} for c in ouverts])

    reportees, perdues, decalees = [], [], 0
    # Trop de seances pour les creneaux restants : on sacrifie les moins
    # structurantes, pas les dernieres de la liste.
    for item in sorted(file, key=lambda i: (priorite(i["seance"]), i.get("date", "")))[:max(0, len(file) - len(ouverts))]:
        file.remove(item)
        perdues.append({"seance": item["seance"], "date": item.get("date", ""),
                        "raison": "ne rentre plus dans les creneaux restants"})
    for creneau, item in zip(ouverts, file):
        bouge = item.get("date") != creneau["date"]
        if bouge and not item["marque"].startswith("rattrapee:"):
            item["marque"] = f"decalee:{item['date']}"
        if bouge and "date" in item:
            decalees += 1
        creneau["seance"], creneau["marque"] = item["seance"], item["marque"]
    for m, creneau in zip(a_rattraper, ouverts):
        reportees.append({"seance": m["seance"], "de": m["date"], "vers": creneau["date"]})
    # La ligne passee disparait : la seance vient d'etre replacee plus loin, la
    # garder ferait un doublon dans le calendrier et dans les compteurs.
    for m in a_rattraper:
        lignes.remove(next(l for l in lignes
                           if l["date"] == m["date"] and l["seance"] == m["seance"]))
    return reportees, perdues, decalees


def main():
    appliquer = "--apply" in sys.argv
    depuis = arg("--depuis", DEPUIS)
    maxi = arg("--max", MAX_REPORTS)
    today = datetime.date.today()

    lignes = charge_plan()
    if not lignes:
        raise SystemExit("plan vide")

    acts = collecte(max(depuis + 2, 14), argstr("--activites"))
    echecs = acts.get("sources_en_echec") or {}
    faits = velo_par_jour(acts)

    debut = (today - datetime.timedelta(days=depuis)).isoformat()
    manquees = []
    for l in lignes:
        if not (debut <= l["date"] < today.isoformat()):
            continue
        attendu = duree_prevue(l["seance"])
        fait = round(faits.get(l["date"], 0))
        if fait >= SEUIL_FAIT * attendu or l["marque"].startswith("abandonnee"):
            continue
        manquees.append({"date": l["date"], "seance": l["seance"], "fait_min": fait,
                         "attendu_min": attendu,
                         "deja_rattrapee": l["marque"].startswith("rattrapee:")})

    if echecs:
        # Sans savoir ce qui a ete fait, un "report" serait une invention.
        return rendu({"applique": False, "bloque": True, "decalees": 0,
                      "raison": f"source(s) en echec : {', '.join(echecs)}",
                      "manquees": [], "reportees": [], "perdues": []})

    perdues = [{"seance": m["seance"], "date": m["date"],
                "raison": "deja rattrapee une fois, on n'etire pas le programme"}
               for m in manquees if m["deja_rattrapee"]]
    candidates = [m for m in manquees if not m["deja_rattrapee"]]
    # On garde les plus recentes : rattraper le sweet spot d'il y a dix jours
    # avant celui d'avant-hier n'a aucun sens physiologique.
    perdues += [{"seance": m["seance"], "date": m["date"],
                 "raison": f"plus de {maxi} seances en retard, celle-ci est abandonnee"}
                for m in candidates[:-maxi]] if len(candidates) > maxi else []
    a_rattraper = candidates[-maxi:]

    reportees, decalees = [], 0
    if a_rattraper:
        reportees, perdues_fin, decalees = decale(lignes, a_rattraper, today)
        perdues += perdues_fin

    # Graver l'abandon dans le plan : sans cette marque, la seance resterait
    # "en retard" demain matin et ferait reculer le programme un cran de plus.
    dates_perdues = {p["date"] for p in perdues if p["date"] and p["date"] < today.isoformat()}
    marquees = [l for l in lignes if l["date"] in dates_perdues and not l["marque"]]
    for l in marquees:
        l["marque"] = "abandonnee"

    if (reportees or marquees) and appliquer:
        ecrit_plan(lignes)

    return rendu({"applique": bool(reportees or marquees) and appliquer, "bloque": False,
                  "manquees": manquees, "reportees": reportees,
                  "perdues": perdues, "decalees": decalees})


def jj(d):
    return datetime.date.fromisoformat(d).strftime("%d/%m") if d else ""


def rendu(res):
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if res["bloque"]:
        print("Replanification bloquee :", res["raison"], "— rien n'a ete modifie.")
        return 0
    if not res["manquees"]:
        print("Aucune seance manquee sur la fenetre : rien a reporter.")
        return 0
    print(f"{len(res['manquees'])} seance(s) prevue(s) et non faite(s) :")
    for m in res["manquees"]:
        print(f"  {jj(m['date'])}  {m['seance']:<32} {m['fait_min']} min de velo "
              f"pour {m['attendu_min']} attendues")
    for r in res["reportees"]:
        print(f"  -> {r['seance']} : {jj(r['de'])} repoussee au {jj(r['vers'])}")
    if res["decalees"]:
        print(f"  -> {res['decalees']} seance(s) suivante(s) reculent d'un cran")
    for p in res["perdues"]:
        ou = f" ({jj(p['date'])})" if p.get("date") else ""
        print(f"  -> {p['seance']}{ou} abandonnee : {p['raison']}")
    print("Applique." if res["applique"] else "Apercu seulement (--apply pour ecrire le plan).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
