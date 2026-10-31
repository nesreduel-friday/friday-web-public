"""Profile für Freunde: Eike reicht die Garantie auch für sie ein und teilt sich das Geld mit ihnen.

Ein Profil ist eine andere Person — mit ihrem Namen, ihrer Adresse, ihrer E-Mail-Adresse, ihrem
Ticket und ihren eigenen Höchstzahlen (2 am Tag, 10 im Monat, 5 mit Deutschlandticket). Nichts
davon vermischt sich mit Eikes eigenen Angaben; ein Antrag gehört von Anfang an genau einem Profil
und wird nur mit dessen Daten abgeschickt (`abschicken.py`).

Hier: prüfen, was Eike für ein Profil einträgt, und die Abrechnung — was die Garantie für den
Freund eingebracht hat, wie viel Eike davon behält und was wer wem noch schuldet. Das Geld geht
entweder auf Eikes Konto („ich“: Eike zahlt den Anteil des Freundes aus) oder auf das des Freundes
(„eigenes“: der Freund gibt Eike seinen Anteil ab).
"""

from __future__ import annotations

import re

from kern.garantie import ablage

ANREDEN = ("Herr", "Frau", "Divers")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
AUSZAHLUNG = ("ich", "eigenes")


def _text(d: dict, feld: str, name: str, laenge: int) -> str:
    return " ".join(str(d.get(feld) or "").split())[:laenge]


def pruefen(d: dict, neu: bool = False) -> dict:
    """Eingaben zu einem Profil prüfen und säubern. Es kommen nur Felder zurück, die im Aufruf standen.
    Wirft ValueError mit einem Text für Eike."""
    aus: dict = {}
    if "name" in d or neu:
        name = _text(d, "name", "Der Name", 60)
        if not name:
            raise ValueError("Wie soll das Profil heißen? (z. B. der Vorname des Freundes)")
        aus["name"] = name
    for feld, name, laenge in (("vorname", "Vorname", 80), ("nachname", "Nachname", 80), ("strasse", "Straße", 120),
                               ("ort", "Ort", 80), ("kontoinhaber", "Kontoinhaber", 120)):
        if feld in d:
            aus[feld] = _text(d, feld, name, laenge)
    if "anrede" in d:
        anrede = _text(d, "anrede", "Anrede", 20)
        if anrede and anrede not in ANREDEN:
            raise ValueError("Die Anrede muss Herr, Frau oder Divers sein.")
        aus["anrede"] = anrede
    if "email" in d:
        email = _text(d, "email", "E-Mail", 120)
        if email and not EMAIL.match(email):
            raise ValueError("Die E-Mail-Adresse sieht nicht richtig aus.")
        aus["email"] = email
    if "plz" in d:
        plz = _text(d, "plz", "PLZ", 10)
        if plz and not re.fullmatch(r"\d{5}", plz):
            raise ValueError("Die PLZ muss fünf Ziffern haben.")
        aus["plz"] = plz
    if "abo_nummer" in d:
        abo = _text(d, "abo_nummer", "Abo-Nummer", 20)
        if abo and not re.fullmatch(r"\d{4,12}", abo):
            raise ValueError("Die Abo-Kundennummer besteht aus 4 bis 12 Ziffern.")
        aus["abo_nummer"] = abo
    if "kundenkarte" in d:
        karte = re.sub(r"\s", "", str(d.get("kundenkarte") or ""))
        if karte and not re.fullmatch(r"\d{6}", karte):
            raise ValueError("Die Kundenkartennummer hat genau 6 Ziffern.")
        aus["kundenkarte"] = karte
    if "iban" in d:
        iban = re.sub(r"\s", "", str(d.get("iban") or "")).upper()
        if iban and not ablage.iban_gueltig(iban):
            raise ValueError("Diese IBAN stimmt nicht — die Prüfsumme passt nicht. Bitte noch einmal prüfen.")
        aus["iban"] = iban
    if "auszahlung" in d:
        if d["auszahlung"] not in AUSZAHLUNG:
            raise ValueError("Die Auszahlung geht entweder auf dein Konto oder auf das des Freundes.")
        aus["auszahlung"] = d["auszahlung"]
    if "anteil" in d:
        try:
            anteil = float(str(d["anteil"]).replace(",", "."))
        except (TypeError, ValueError):
            raise ValueError("Dein Anteil ist keine Zahl.") from None
        if not 0 <= anteil <= 100:
            raise ValueError("Dein Anteil muss zwischen 0 und 100 Prozent liegen.")
        aus["anteil"] = anteil
    for feld in ("vollmacht", "archiviert"):
        if feld in d:
            aus[feld] = bool(d[feld])
    if "notiz" in d:
        aus["notiz"] = str(d.get("notiz") or "").strip()[:500]
    return aus


def anteil_cent(cent: int, prozent: float) -> int:
    """Eikes Anteil in Cent, kaufmännisch gerundet (der Rest gehört dem Freund — es geht kein Cent verloren)."""
    return int(cent * prozent / 100 + 0.5)


def abrechnung(pid: int) -> dict:
    """Was die Garantie für dieses Profil eingebracht hat und wer wem was schuldet.
    Nur genehmigte Fälle; „erledigt“ setzt Eike selbst, wenn er verrechnet hat."""
    a = ablage.standard()
    p = a.profil(pid) or {}
    prozent = float(p.get("anteil") or 0)
    erledigt = set(p.get("ausgezahlt") or [])
    auf_mein_konto = p.get("auszahlung", "ich") == "ich"
    zeilen = []
    for v in a.vorgaenge(10000, pid):
        if v.get("status") != "genehmigt":
            continue
        cent = int(v.get("cent") or 0)
        mein = anteil_cent(cent, prozent)
        zeilen.append({"vorgang": v["vorgang"], "fahrtdatum": v.get("fahrtdatum") or "", "linie": v.get("linie") or "",
                       "start": v.get("start") or "", "ziel": v.get("ziel") or "", "cent": cent, "mein_cent": mein,
                       "freund_cent": cent - mein, "erledigt": v["vorgang"] in erledigt})
    zeilen.sort(key=lambda z: (z["fahrtdatum"], z["vorgang"]), reverse=True)
    # Wer zahlt wem? Landet das Geld bei Eike, schuldet er dem Freund dessen Teil; sonst der Freund Eike seinen.
    offen = sum(z["freund_cent"] if auf_mein_konto else z["mein_cent"] for z in zeilen if not z["erledigt"])
    return {"zeilen": zeilen, "anteil": prozent, "summe_cent": sum(z["cent"] for z in zeilen),
            "mein_cent": sum(z["mein_cent"] for z in zeilen), "freund_cent": sum(z["freund_cent"] for z in zeilen),
            "offen_cent": offen, "ich_zahle_aus": auf_mein_konto, "anzahl": len(zeilen)}


def oeffentlich(p: dict) -> dict:
    """Ein Profil für die Oberfläche: nie die IBAN, dafür Zahlen und was noch fehlt."""
    a = ablage.standard()
    pid = p["id"]
    r = abrechnung(pid)
    return {"id": pid, "name": p.get("name") or f"Profil {pid}", "anteil": float(p.get("anteil") or 0),
            "auszahlung": p.get("auszahlung", "ich"), "vollmacht": bool(p.get("vollmacht")),
            "archiviert": bool(p.get("archiviert")), "fehlt": a.fehlende_angaben(pid),
            "antraege": len(a.antraege(1000, pid)), "genehmigt": r["anzahl"], "summe_cent": r["summe_cent"],
            "mein_cent": r["mein_cent"], "offen_cent": r["offen_cent"], "ich_zahle_aus": r["ich_zahle_aus"]}
