"""Catalogue des seances structurees + export .zwo (Zwift) et .erg (Rouvy...).

Source de verite unique des segments d'entrainement : le push vers le compteur
iGPSport (mcp/push_program.py) et les fichiers telechargeables du tableau de
bord lisent cette meme liste. Avant, les segments n'existaient que dans
push_program.py et le dashboard ne connaissait que le titre de la seance.

Nom de fichier en underscores, contrairement aux autres scripts : c'est un
module importe, pas un executable, et `import coach-velo-seances` n'existe pas.
"""

# --- description des seances -------------------------------------------------

def step(name, intensity, secs, lo=None, hi=None, note=None):
    s = {"name": name, "intensity": intensity,
         "duration": {"type": "time", "value": secs}}
    if lo is not None:
        s["target"] = {"type": "power_percent_ftp", "value": 0, "min": lo, "max": hi}
    if note:
        s["note"] = note
    return s


def lap(name, intensity, lo=None, hi=None, note=None):
    """Segment ferme au bouton LAP : duree inconnue, donc non exportable."""
    s = {"name": name, "intensity": intensity, "duration": {"type": "lap_button"}}
    if lo is not None:
        s["target"] = {"type": "power_percent_ftp", "value": 0, "min": lo, "max": hi}
    if note:
        s["note"] = note
    return s


def rep(times, steps):
    return {"type": "repeat", "times": times, "steps": steps}


def catalogue(ftp):
    """Les 17 seances, descriptions chiffrees a la FTP passee en parametre."""

    def w(lo, hi):
        return f"{round(lo*ftp/100)}-{round(hi*ftp/100)}W ({lo}-{hi}% FTP)"

    WU = step("Echauffement", "warmup", 900, 50, 70)
    WU_LONG = step("Echauffement progressif", "warmup", 1200, 50, 75)
    CD = step("Retour au calme", "cooldown", 600, 40, 55)

    return [
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
      "description": f"15 aout. Pacing : {w(78,86)} sur toute la montee, max {round(0.91*ftp)}W dans la foret. LAP a Saint-Esteve et au Chalet Reynard.",
      "steps": [lap("Approche Z2", "warmup", 55, 70, "LAP a Saint-Esteve"),
                lap("Foret 9-10%", "active", 78, 86, "LAP au Chalet Reynard"),
                lap("Final decouvert", "active", 78, 88, "Gerer le vent, finir fort"),
                lap("Descente + retour", "cooldown")]},
    ]


# --- mise a plat --------------------------------------------------------------

def code(titre):
    """'P04 Sweet Spot 2x20' -> 'P04'. None si la seance n'est pas codee."""
    debut = (titre or "").split(" ")[0]
    return debut if len(debut) == 3 and debut[0] == "P" and debut[1:].isdigit() else None


def aplatir(steps):
    """Deroule les repetitions en une suite lineaire de segments.

    Renvoie None des qu'un segment se ferme au bouton LAP : sa duree n'est pas
    connue a l'avance, donc la seance n'a pas de fichier (P17 Ventoux, qui est
    de toute facon une sortie dehors).
    """
    plat = []
    for s in steps:
        if s.get("type") == "repeat":
            for _ in range(s["times"]):
                sous = aplatir(s["steps"])
                if sous is None:
                    return None
                plat += sous
            continue
        if s.get("duration", {}).get("type") != "time":
            return None
        cible = s.get("target") or {}
        plat.append({"nom": s["name"], "intensite": s.get("intensity", "active"),
                     "secs": s["duration"]["value"],
                     "lo": cible.get("min"), "hi": cible.get("max"),
                     "note": s.get("note")})
    return plat


def duree_txt(secs):
    h, m = divmod(round(secs / 60), 60)
    return f"{h} h {m:02d}" if h else f"{m} min"


def slug(titre):
    return "".join(c if c.isalnum() else "-" for c in titre).strip("-").replace("--", "-")


# --- export .zwo / .erg -------------------------------------------------------

def _xml(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def zwo(wkt, segments, auteur="Coach velo IA"):
    """Fichier Zwift (.zwo), lu aussi par Rouvy, MyWhoosh, indieVelo.

    Les puissances y sont des fractions de FTP : le fichier reste juste apres
    un test FTP, chaque appli applique la sienne.
    """
    lignes = ['<?xml version="1.0" encoding="UTF-8"?>', "<workout_file>",
              f"  <author>{_xml(auteur)}</author>",
              f"  <name>{_xml(wkt['title'])}</name>",
              f"  <description>{_xml(wkt['description'])}</description>",
              "  <sportType>bike</sportType>", "  <tags/>", "  <workout>"]
    for i, seg in enumerate(segments):
        lo = (seg["lo"] or 50) / 100
        hi = (seg["hi"] or seg["lo"] or 60) / 100
        gamme = f' · {seg["lo"]}-{seg["hi"]}% FTP' if seg["lo"] else ""
        msg = seg["nom"] + gamme + (" · " + seg["note"] if seg["note"] else "")
        texte = f'<textevent timeoffset="0" message="{_xml(msg)}"/>'
        if seg["intensite"] == "warmup" and i == 0:
            ouvre = f'<Warmup Duration="{seg["secs"]}" PowerLow="{lo:.3f}" PowerHigh="{hi:.3f}">'
            ferme = "</Warmup>"
        elif seg["intensite"] == "cooldown" and i == len(segments) - 1:
            # Zwift lit le Cooldown comme une rampe descendante : PowerLow est
            # le depart, PowerHigh l'arrivee. D'ou l'inversion apparente.
            ouvre = f'<Cooldown Duration="{seg["secs"]}" PowerLow="{hi:.3f}" PowerHigh="{lo:.3f}">'
            ferme = "</Cooldown>"
        else:
            # Bloc tenu : on vise le milieu de la fourchette, comme le fait le
            # mode ERG du home trainer. Pas de PowerLow/PowerHigh ici : Zwift en
            # ferait une rampe de 88 a 93%, alors que la consigne est un palier.
            ouvre = f'<SteadyState Duration="{seg["secs"]}" Power="{(lo+hi)/2:.3f}">'
            ferme = "</SteadyState>"
        lignes.append(f"    {ouvre}{texte}{ferme}")
    lignes += ["  </workout>", "</workout_file>", ""]
    return "\n".join(lignes)


def erg(wkt, segments, ftp):
    """Fichier .erg (watts absolus) : Rouvy, TrainerRoad, Wahoo, GoldenCheetah.

    Les watts y sont figes a la FTP du moment : a regenerer apres un test.
    """
    tete = ["[COURSE HEADER]", "VERSION = 2", "UNITS = ENGLISH",
            f"DESCRIPTION = {wkt['description']}",
            f"FILE NAME = {slug(wkt['title'])}.erg",
            "MINUTES\tWATTS", "[END COURSE HEADER]", "[COURSE DATA]"]
    corps, t = [], 0.0
    for seg in segments:
        lo = round((seg["lo"] or 50) * ftp / 100)
        hi = round((seg["hi"] or seg["lo"] or 60) * ftp / 100)
        if seg["intensite"] in ("warmup", "cooldown"):
            debut, fin = (lo, hi) if seg["intensite"] == "warmup" else (hi, lo)
        else:
            debut = fin = round((lo + hi) / 2)
        corps.append(f"{t:.2f}\t{debut}")
        t += seg["secs"] / 60
        corps.append(f"{t:.2f}\t{fin}")
    return "\n".join(tete + corps + ["[END COURSE DATA]", ""])


def ecrire(dest, ftp, auteur="Coach velo IA"):
    """Ecrit un .zwo et un .erg par seance exportable dans `dest`.

    Renvoie {code: {...}} pour que la page sache quoi proposer au telechargement.
    """
    import os
    os.makedirs(dest, exist_ok=True)
    fiches = {}
    for wkt in catalogue(ftp):
        c = code(wkt["title"])
        segments = aplatir(wkt["steps"])
        if not c or not segments:
            continue
        base = slug(wkt["title"])
        with open(f"{dest}/{base}.zwo", "w", encoding="utf-8") as f:
            f.write(zwo(wkt, segments, auteur))
        with open(f"{dest}/{base}.erg", "w", encoding="utf-8") as f:
            f.write(erg(wkt, segments, ftp))
        secs = sum(s["secs"] for s in segments)
        fiches[c] = {"titre": wkt["title"], "base": base,
                     "duree": duree_txt(secs), "secs": secs,
                     "desc": wkt["description"]}
    return fiches
