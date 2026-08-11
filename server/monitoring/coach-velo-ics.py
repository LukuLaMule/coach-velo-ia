#!/home/opc/mcp/coros-mcp/.venv/bin/python
"""Genere le calendrier .ics du programme velo (plan + verdicts du matin)."""
import datetime, os, re

MON = "/home/opc/monitoring"
OUT_DIR = "/home/opc/Docker/sites/velo/public"
DESC = {
 "P01": "Echauffement + 3x1min haute cadence, puis 20MIN A FOND. FTP = 95% de la moyenne.",
 "P02": "2x15min a 255-270W (88-93% FTP), recup 5min.",
 "P03": "3x15min a 255-270W (88-93% FTP), recup 5min.",
 "P04": "2x20min a 255-270W (88-93% FTP), recup 5min.",
 "P05": "1x20min a 247-261W (85-90% FTP). Semaine legere.",
 "P06": "3x10min a 278-296W (96-102% FTP), recup 5min.",
 "P07": "2x15min a 278-290W (96-100% FTP), recup 6min.",
 "P08": "3x12min a 278-290W (96-100% FTP), recup 6min. Grosse seance.",
 "P09": "3x9min alternance 2min 95% / 1min 105% FTP, recup 5min.",
 "P10": "5x3min a 325-342W (112-118% FTP), recup 3min.",
 "P11": "6x3min a 325-342W (112-118% FTP), recup 3min.",
 "P12": "4x4min a 313-331W (108-114% FTP), recup 4min.",
 "P13": "2 series de 10x(30s 120% / 30s 60%), recup 5min entre series.",
 "P14": "3x3min a 319-334W (110-115% FTP), recup 3min. Affutage.",
 "P15": "2h a 174-209W (60-72% FTP), cadence souple.",
 "P16": "1h tres facile a 131-168W (45-58% FTP).",
 "Sortie": "Sortie plaisir sans structure, sous 200W.",
 "P17": "VENTOUX depuis Bedoin (21.5km, 7.5%, 1600m D+). Pacing 226-249W, max 264W dans la foret. LAP a Saint-Esteve et au Chalet Reynard. Partir tot (chaleur), 2 bidons + ravito Chalet Reynard.",
}
DUR = {"P01":75,"P02":65,"P03":85,"P04":75,"P05":50,"P06":65,"P07":70,"P08":85,
       "P09":70,"P10":70,"P11":75,"P12":75,"P13":70,"P14":55,"P15":120,"P16":60,"Sortie":90,"P17":300}

token = ""
for line in open(f"{MON}/ntfy-coach.conf"):
    if line.startswith("ICS_TOKEN="):
        token = line.strip().split("=",1)[1]

verdicts = {}
vf = f"{MON}/coach-velo-verdicts.tsv"
if os.path.exists(vf):
    for line in open(vf):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 2:
            verdicts[p[0]] = (p[1], p[2] if len(p) > 2 else "")

L = ["BEGIN:VCALENDAR","VERSION:2.0",
     "PRODID:-//Claude//Programme velo 12 semaines//FR",
     "X-WR-CALNAME:Velo FTP-VO2max","REFRESH-INTERVAL;VALUE=DURATION:PT1H",
     "X-PUBLISHED-TTL:PT1H"]
stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
for line in open(f"{MON}/coach-velo-plan.tsv"):
    p = line.rstrip("\n").split("\t")
    if len(p) < 3: continue
    d = datetime.date.fromisoformat(p[0]); title = p[1]; week = p[2]
    code = title.split(" ")[0]
    hour = 8 if code == "P17" else (9 if d.weekday() == 5 else 18)
    start = datetime.datetime(d.year, d.month, d.day, hour)
    end = start + datetime.timedelta(minutes=DUR.get(code, 75))
    emoji, note = verdicts.get(p[0], ("🚴", ""))
    desc = DESC.get(code, "")
    if note: desc = note + "\\n" + desc
    desc += f" Seance iGPSport : {title}."
    L += ["BEGIN:VEVENT", f"UID:velo-{p[0]}@claude", f"DTSTAMP:{stamp}",
          "DTSTART:" + start.strftime("%Y%m%dT%H%M%S"),
          "DTEND:" + end.strftime("%Y%m%dT%H%M%S"),
          f"SUMMARY:{emoji} {title} ({week})",
          f"DESCRIPTION:{desc}", "END:VEVENT"]
L.append("END:VCALENDAR")
with open(f"{OUT_DIR}/cal-{token}.ics", "w", encoding="utf-8", newline="\r\n") as f:
    f.write("\n".join(L) + "\n")
print(f"OK {OUT_DIR}/cal-{token}.ics")
