"""Antworten der ÜSTRA aus Gmail — Eingang, Genehmigung, Ablehnung, Rückfrage.

Aufbau gelernt an Eikes echten Mails (September 2025 bis August 2026, alle von
`info@uestra.de`, Betreff „ÜSTRA Garantie: Ihr Garantiefall 8851675 vom 19.08.2026“ —
das Datum im Betreff ist der ANTRAGStag, nicht der Fahrtag):

- Eingang: „vielen Dank für Ihren Antrag mit den folgenden Angaben: Fahrtdatum: …
  Verbindung: S1 - Letter Bahnhof - Hannover Bahnhof Nordstadt (22:21 - 22:27)
  Fahrkarte: Deutschlandticket, … unter der Vorgangsnummer *8854083* aufgenommen“.
- Genehmigt: „Die Entschädigung in Höhe von 5,00 Euro werden wir … überweisen.“
- Abgelehnt: „lehnen wir diesen Antrag ab“ · „Tatsächlich ist die Fahrt planmäßig
  erfolgt … keine Entschädigung“ · „bereits einen Antrag … mit der ID: … Eine
  doppelte Auszahlung ist nicht möglich.“
- Rückfrage: „benötigen wir Ihre Fahrkarte … innerhalb von 14 Tagen“ · „nicht
  vollständig korrekte Angaben“.
- S-Bahn-Fälle bearbeitet Transdev, die Mails kommen trotzdem von der ÜSTRA.

Stolperstein (16.09.2026): Die meisten Mails sind HTML mit Entitäten
(`f&uuml;r`, `von&nbsp;5,00 Euro`). Ohne `html.unescape` blieben 28 von 49 Mails
unerkannt und kein Betrag wurde gelesen.

Ein Fall wird bei jedem Abgleich aus ALLEN seinen Mails neu zusammengesetzt
(`faelle`). So spielt die Reihenfolge keine Rolle, und eine verbesserte Auswertung
korrigiert alte Einträge von selbst.

Mailtexte sind fremder Text: Hier werden nur Nummern, Daten und feste Formulierungen
herausgezogen; nichts daraus wird als Anweisung behandelt.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from kern import config, konnektoren
from kern.dienste import gmail
from kern.dienste.uestra import gleiche_linie
from kern.garantie import ablage

log = logging.getLogger("friday.garantie")

KONNEKTOR = "gmail"
BERLIN = ZoneInfo(config.ZEITZONE)
OHNE_BESCHEID_NACH = timedelta(days=60)   # so lange gilt ein Eingang als „offen“
# Gemeldet wird nur, was gerade kam. Am 16.09. meldete eine verbesserte Auswertung
# sonst Genehmigungen vom September 2025 als neu.
MELDEN_BIS = timedelta(days=3)

BETREFF = re.compile(r"Garantiefall\s+(\d{6,9})(?:\s+vom\s+(\d{2})\.(\d{2})\.(\d{4}))?")
VORGANG = re.compile(r"Vorgangsnummer\s*\**\s*(\d{6,9})")
FAHRTDATUM = re.compile(r"Fahrtdatum:\s*\**\s*(\d{2})\.(\d{2})\.(\d{4})")
FAHRT_AM = re.compile(r"Fahrt am (\d{2})\.(\d{2})\.(\d{4})")
VERBINDUNG = re.compile(r"Verbindung:\s*\**\s*(?P<linie>\S+)\s+-\s+(?P<start>.+?)\s+-\s+(?P<ziel>.+?)\s*"
                        r"\((?P<ab>\d{1,2}:\d{2})\s*-\s*(?P<an>\d{1,2}:\d{2})\)")
FAHRKARTE = re.compile(r"Fahrkarte:\s*\**\s*([^\n]+)")
BETRAG = re.compile(r"in Höhe von\s*(\d+)(?:,(\d{2}))?\s*(?:Euro|EUR|€)")

ARTEN = (   # Reihenfolge zählt: eine Genehmigung erwähnt auch „Ihren Antrag“
    ("genehmigt", re.compile(r"Entschädigung in Höhe von|auf das im Antrag angegebene Konto überweisen", re.I)),
    ("abgelehnt", re.compile(r"lehnen wir diesen Antrag ab|keine Entschädigung|doppelte Auszahlung ist nicht möglich", re.I)),
    ("rueckfrage", re.compile(r"benötigen wir Ihre Fahrkarte|nicht vollständig korrekte Angaben", re.I)),
    ("eingang", re.compile(r"vielen Dank für Ihren Antrag", re.I)),
    ("weitergeleitet", re.compile(r"leiten wir Ihre E-Mail weiter", re.I)),
)
TEXTE = {"eingang": "eingegangen", "genehmigt": "genehmigt", "abgelehnt": "abgelehnt", "rueckfrage": "Rückfrage",
         "ohne_bescheid": "ohne Bescheid", "erledigt": "Rückfrage erledigt"}
# Welcher Stand gewinnt: Entscheidungen vor Rückfrage vor Eingang; zwischen gleichrangigen die neuere Mail.
RANG = {"eingang": 1, "rueckfrage": 2, "abgelehnt": 3, "genehmigt": 3}
FELDER = ("fahrtdatum", "linie", "start", "ziel", "ab", "an", "fahrkarte")


def klartext(text: str) -> str:
    """HTML-Entitäten auflösen, Tags entfernen, geschützte Leerzeichen zu normalen."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return text.replace("\xa0", " ").replace("\r", "")


def _datum(treffer) -> str:
    return f"{treffer.group(3)}-{treffer.group(2)}-{treffer.group(1)}" if treffer else ""


def _begruendung(flach: str) -> str:
    """Der Satz nach „… in Anspruch nehmen.“ bis zur Grußformel — nur zum Anzeigen."""
    m = re.search(r"in Anspruch nehmen\.\s*(.+?)\s*(?:Bis bald|Bei Fragen|Mit freundlichen|$)", flach)
    return (m.group(1) if m else "")[:300]


def auswerten(mail: dict) -> dict | None:
    """Eine Mail → ein Stand eines Garantiefalls. Ohne Vorgangsnummer: keine Garantie-Mail."""
    text = klartext(mail.get("text"))
    flach = " ".join(text.split())                    # Sätze brechen in den Mails mitten im Satz um
    kopf = BETREFF.search(mail.get("betreff") or "")
    nummer = kopf or VORGANG.search(flach)
    if not nummer:
        return None
    art = next((name for name, muster in ARTEN if muster.search(flach)), "sonstiges")
    # Antworten aufs Kontaktformular („Re: … Garantiefall …“) sind Schriftverkehr, kein Bescheid.
    if re.match(r"\s*(re|aw|wg|fwd?|mitteilung)\s*:", mail.get("betreff") or "", re.I) and art != "genehmigt":
        art = "sonstiges"
    verbindung = VERBINDUNG.search(flach)
    fahrkarte = FAHRKARTE.search(text)
    betrag = BETRAG.search(flach)
    return {"vorgang": nummer.group(1), "art": art,
            "antragstag": f"{kopf.group(4)}-{kopf.group(3)}-{kopf.group(2)}" if kopf and kopf.group(2) else "",
            "fahrtdatum": _datum(FAHRTDATUM.search(flach) or FAHRT_AM.search(flach)),
            "linie": verbindung.group("linie") if verbindung else "",
            "start": verbindung.group("start").strip() if verbindung else "",
            "ziel": verbindung.group("ziel").strip() if verbindung else "",
            "ab": verbindung.group("ab") if verbindung else "", "an": verbindung.group("an") if verbindung else "",
            "fahrkarte": " ".join(fahrkarte.group(1).replace("*", "").split()).strip(" ,")[:120] if fahrkarte else "",
            "cent": int(betrag.group(1)) * 100 + int(betrag.group(2) or 0) if betrag else None,
            "grund": _begruendung(flach) if art in ("abgelehnt", "rueckfrage") else "",
            "bearbeiter": "Transdev" if "Transdev" in flach else "ÜSTRA",
            "empfaenger": [str(x).lower() for x in mail.get("an") or []],
            "datum": mail.get("datum") or ""}


def _zeit(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso).astimezone(BERLIN) if iso else None
    except ValueError:
        return None


def faelle(mails: list[dict]) -> dict[str, dict]:
    """Alle Mails → je Vorgangsnummer EIN Fall mit dem gültigen Stand."""
    je_fall: dict[str, list[dict]] = {}
    for m in mails:
        v = auswerten(m)
        if v:
            je_fall.setdefault(v["vorgang"], []).append(v)
    ergebnis = {}
    for nummer, liste in je_fall.items():
        liste.sort(key=lambda v: _zeit(v["datum"]) or datetime.min.replace(tzinfo=BERLIN))
        fall = {"vorgang": nummer, "status": None, "grund": "", "cent": None, "eingang_am": None,
                "entschieden_am": None, "bearbeiter": "ÜSTRA", "mails": len(liste),
                "empfaenger": sorted({x for v in liste for x in v.get("empfaenger") or []}),
                "letzte_mail": max(v["datum"] for v in liste), **{k: "" for k in FELDER}, "antragstag": ""}
        for v in liste:                        # Angaben: die erste Mail, die sie nennt
            for k in (*FELDER, "antragstag"):
                if v.get(k) and not fall[k]:
                    fall[k] = v[k]
            if v["bearbeiter"] == "Transdev":
                fall["bearbeiter"] = "Transdev"
            if v["art"] == "eingang" and not fall["eingang_am"]:
                fall["eingang_am"] = v["datum"]
        for v in liste:                        # Stand: höchster Rang, bei Gleichstand die neuere Mail
            if v["art"] in RANG and RANG[v["art"]] >= RANG.get(fall["status"], 0):
                fall["status"] = v["art"]
                fall["grund"] = v["grund"] if v["art"] in ("abgelehnt", "rueckfrage") else ""
                fall["cent"] = (v["cent"] if v["cent"] is not None else 500) if v["art"] == "genehmigt" else None
                fall["entschieden_am"] = v["datum"] if RANG[v["art"]] == 3 else None
        if fall["status"]:
            ergebnis[nummer] = fall
    return ergebnis


def anzeige_stand(fall: dict) -> str:
    """Ein Eingang ohne Antwort nach 60 Tagen ist nicht mehr „offen“, sondern ohne Bescheid. Eine Rückfrage, die Eike
    als erledigt abgehakt hat (21.09.2026), ist „erledigt“ — bis eine neuere Mail zu dem Fall kommt: Dann steht in
    `erledigt` nicht mehr die letzte Mail, und die Rückfrage ist wieder offen."""
    if fall.get("status") == "rueckfrage" and fall.get("erledigt") and fall.get("erledigt") == fall.get("letzte_mail"):
        return "erledigt"
    if fall.get("status") in ("eingang", "rueckfrage"):
        zeit = _zeit(fall.get("letzte_mail") or "")
        if zeit and config.jetzt() - zeit > OHNE_BESCHEID_NACH:
            return "ohne_bescheid"
    return fall.get("status") or ""


def _frisch(fall: dict) -> bool:
    zeit = _zeit(fall.get("letzte_mail") or "")
    return bool(zeit) and config.jetzt() - zeit <= MELDEN_BIS


def euro(cent: int | None) -> str:
    return f"{(cent or 0) / 100:.2f}".replace(".", ",") + " €"


def statistik(profil: int | None = 0) -> dict:
    """Was die Garantie eingebracht hat — gezählt nach dem Tag des Bescheids. Je Profil (Vorgabe: Eike)."""
    jetzt = config.jetzt()
    faelle_ = ablage.standard().vorgaenge(10000, profil)
    monate: dict[str, dict] = {}
    mit_friday = [f for f in faelle_ if f.get("status") == "genehmigt" and f.get("antrag")]
    for f in faelle_:
        if f.get("status") != "genehmigt":
            continue
        zeit = _zeit(f.get("entschieden_am") or f.get("letzte_mail") or "")
        schluessel = f"{zeit:%Y-%m}" if zeit else "?"
        m = monate.setdefault(schluessel, {"monat": schluessel, "cent": 0, "anzahl": 0})
        m["cent"] += f.get("cent") or 0
        m["anzahl"] += 1
    stande = [anzeige_stand(f) for f in faelle_]
    entschieden = stande.count("genehmigt") + stande.count("abgelehnt")
    diesen = monate.get(f"{jetzt:%Y-%m}", {"cent": 0, "anzahl": 0})
    return {"insgesamt_cent": sum(m["cent"] for m in monate.values()),
            # Nur Fälle, die zu einem von Friday vorbereiteten Antrag gehören.
            "mit_friday_cent": sum(f.get("cent") or 0 for f in mit_friday), "mit_friday_anzahl": len(mit_friday),
            "monat_cent": diesen["cent"], "monat_anzahl": diesen["anzahl"],
            "jahr_cent": sum(m["cent"] for k, m in monate.items() if k.startswith(f"{jetzt:%Y}")),
            "monate": sorted(monate.values(), key=lambda m: m["monat"], reverse=True)[:12],
            "faelle": len(faelle_), "genehmigt": stande.count("genehmigt"), "abgelehnt": stande.count("abgelehnt"),
            "offen": stande.count("eingang") + stande.count("rueckfrage"),
            "rueckfragen": stande.count("rueckfrage"), "ohne_bescheid": stande.count("ohne_bescheid"),
            "quote": round(100 * stande.count("genehmigt") / entschieden) if entschieden else None}


def oeffentlich(fall: dict) -> dict:
    stand = anzeige_stand(fall)
    return {**fall, "stand": stand, "stand_text": TEXTE.get(stand, stand),
            "euro": euro(fall.get("cent")) if fall.get("status") == "genehmigt" else ""}


def _worte(text: str) -> set[str]:
    return {w for w in re.sub(r"[^\wäöüß]+", " ", (text or "").casefold()).split() if len(w) > 2}


def _passender_antrag(v: dict) -> dict | None:
    """Welcher Antrag in Friday gehört zu diesem Fall? Fahrtag, Linie und Starthaltestelle
    müssen passen — sonst lieber keiner als der falsche. Passen mehrere (Freunde im selben
    Bus, jeder mit eigenem Antrag), ist die Mail nicht zuzuordnen: dann keiner. Die
    von Friday abgeschickten Anträge kennen ihre Vorgangsnummer ohnehin selbst."""
    if not v.get("fahrtdatum") or not v.get("linie"):
        return None
    treffer = []
    for e in ablage.standard().antraege(500, None):              # aller Profile
        if e.get("vorgang") or e["status"] == "verworfen" or not e.get("an"):
            continue
        if (e["an"][:10] == v["fahrtdatum"] and gleiche_linie(v["linie"], e["linie"] or "")
                and _worte(v["start"]) & _worte(e["start"])):
            treffer.append(e)
    if len(treffer) > 1 and v.get("empfaenger"):
        # Mehrere passen — aber jede Person hat ihre eigene Adresse (Alias): an wen ging die Mail?
        def adresse(e):
            return (ablage.standard().angaben(int(e.get("profil") or 0), roh=True).get("email") or "").strip().lower()
        treffer = [e for e in treffer if adresse(e) and adresse(e) in v["empfaenger"]]
    return treffer[0] if len(treffer) == 1 else None


def _holen(seit: str) -> list[dict]:
    with gmail.Verbindung() as g:
        return g.uestra_mails(seit)


async def abgleich(seit: str = "") -> dict:
    """Gmail lesen, Fälle neu zusammensetzen, Anträge verknüpfen, Bescheide melden.
    Beim allerersten Abgleich wird still übernommen — sonst kämen alle alten
    Bescheide auf einmal aufs Handy."""
    a = ablage.standard()
    erster = not a.vorgaenge(1, None)
    mails = await asyncio.to_thread(_holen, seit)
    geaendert = verknuepft = 0
    for nummer, fall in faelle(mails).items():
        alt = a.vorgang(nummer) or {}
        e = (a.antrag(alt["antrag"]) if alt.get("antrag") else None) or a.antrag_zum_vorgang(nummer) \
            or _passender_antrag(fall)
        fall["antrag"] = e["id"] if e else None
        fall["gemeldet"] = alt.get("gemeldet")
        fall["erledigt"] = alt.get("erledigt")                  # Eikes Haken bleibt; eine neue Mail hebt ihn auf
        # Was Friday beim Abschicken schon wusste, füllt Lücken der Mail (z. B. vor der Eingangsmail).
        for k in (*FELDER, "eingang_am"):
            if not fall.get(k) and alt.get(k):
                fall[k] = alt[k]
        if any(alt.get(k) != fall.get(k) for k in ("status", "cent", "grund", *FELDER)):
            geaendert += 1
        if e:
            a.antrag_aendern(e["id"], vorgang=nummer, entscheidung=fall["status"], status="abgeschickt",
                             abgeschickt_am=e.get("abgeschickt_am") or fall.get("eingang_am") or fall["letzte_mail"])
            verknuepft += 1
        if not erster and fall["status"] != fall["gemeldet"] and _frisch(fall):
            await _melden(fall, a.profil_name(e["profil"]) if e and e.get("profil") else "")
        fall["gemeldet"] = fall["status"]
        a.vorgang_speichern(fall)
    return {"mails": len(mails), "geaendert": geaendert, "verknuepft": verknuepft, "erster": erster}


async def _melden(v: dict, person: str = "") -> None:
    from kern.dienste import benachrichtigungen
    fahrt = f"{v['linie']} am {v['fahrtdatum'][8:10]}.{v['fahrtdatum'][5:7]}." if v.get("fahrtdatum") else ""
    fahrt += f" für {person}" if person else ""
    titel, text = {
        "eingang": (f"Garantiefall {v['vorgang']} eingegangen", f"Die ÜSTRA hat deinen Antrag ({fahrt}) aufgenommen."),
        "genehmigt": (f"+{euro(v.get('cent'))} Garantie genehmigt", f"Fall {v['vorgang']} ({fahrt}) wird überwiesen."),
        "abgelehnt": (f"Garantiefall {v['vorgang']} abgelehnt", v.get("grund") or "Die Begründung steht in Friday."),
        "rueckfrage": (f"Rückfrage zu Fall {v['vorgang']}", v.get("grund") or "Die ÜSTRA braucht noch etwas von dir."),
    }.get(v["status"], (f"Garantiefall {v['vorgang']}", TEXTE.get(v["status"], v["status"])))
    await asyncio.to_thread(benachrichtigungen.senden, "garantie", titel, text[:180],
                            "/?garantie=post#studio", f"garantie-vorgang-{v['vorgang']}")


def benutzbar() -> bool:
    return konnektoren.benutzbar(KONNEKTOR)
