"""Bahn-Fahrgastrechte: Entschädigung bei Zugverspätung — S-Bahn, RE/RB, metronom, enno, ICE … (21.09.2026).

Eikes Auftrag: „Erweitere die Garantie um Erstattungen bei Verspätung von DB, Metronom, S-Bahn und Ähnlichem“ — nach dem
Muster der ÜSTRA-Garantie: Friday sammelt, prüft, ob ein Anspruch besteht, und bereitet den Antrag vor. ABGESCHICKT WIRD
HIER NICHTS. Das Servicecenter Fahrgastrechte verlangt ein unterschriebenes Formular per Post, die S-Bahn Hannover ein
Webformular ohne Schnittstelle; Eike reicht selbst ein und markiert „eingereicht“ (`einreichen`).

Was gilt — am 21.09.2026 in den Quellen nachgelesen (`QUELLEN`, Einzelheiten in docs/LEHREN.md):
- Einzelfahrkarte (auch ICE/IC): ab 60 Minuten am ZIEL 25 %, ab 120 Minuten 50 % des gezahlten Preises, auf 5 Cent
  aufgerundet, je Fahrkarte und Richtung einmal, unter 4 € keine Auszahlung (EU 2021/782 Art. 19; Deutschlandtarif Teil A 8.2.6).
- Zeitkarte des Nahverkehrs: 1,50 € (2. Klasse) je Fall ab 60 Minuten, höchstens 25 % des GEZAHLTEN Kartenpreises, gesammelt
  nach Ablauf der Karte einzureichen (Teil C 8.1/8.2; S-Bahn Hannover). Beim Deutschlandticket (Teil C gilt nach Nr. 1.6 seiner
  Tarifbedingungen) dürfen auch Fälle ab 20 Minuten zusammengerechnet werden: je VOLLE 60 Minuten 1,50 €. Ausgezahlt wird
  erst, wenn der Anspruch 4 € übersteigt. Das Deutschlandticket ist „erheblich ermäßigt“: kein Umstieg in den Fernverkehr.
- Kein Anspruch bei außergewöhnlichen Umständen (Unwetter, Naturkatastrophen), eigenem Verschulden oder Dritten (Personen im
  Gleis, Kabeldiebstahl, Polizei) — ein Streik des eigenen Personals zählt NICHT als Ausnahme (Teil A 8.2.6). Die Ursache
  kennt Friday nicht; das steht in den Regeln der App.
- Wohin: Betreiber im Servicecenter Fahrgastrechte (DB, metronom/enno, start, erixx, eurobahn …) per Formular an das
  Servicecenter; die S-Bahn Hannover (Transdev) über ihr eigenes Formular; die WestfalenBahn ist nicht im Servicecenter.
- ÜSTRA-Garantie ODER Fahrgastrechte für dieselbe Fahrt, nie beides (Bedingungen der ÜSTRA). Die Sperre sitzt hier
  (`widersprueche`) und in der Garantie (`antrag.vorbereiten`, `abschicken.widersprueche`).
- Fristen: Die Tarifbedingungen lassen die Ansprüche ein Jahr nach Ablauf der Fahrkarte verjähren (Teil A 8.2.8); bahn.de
  nennt für Beschwerden nach der EU-Verordnung 3 Monate (an anderer Stelle 12). Friday rechnet vorsichtig mit 3 Monaten.

Die Verspätung am Ziel ist Fridays eigene Messung (Echtzeit der GVH-Auskunft nach der Ankunft, gespeichert) — wie bei der
Garantie. Ohne Messung kein Anspruch: Eine geschätzte Ankunft ginge als falsche Angabe hinaus.

Profile wie in der Garantie: Ein Fall gehört einer Person (0 = Eike); Ticket, Angaben und Rechnung gelten je Person. Das
Formular unterschreibt immer der Ticketinhaber selbst.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta

from kern import config, konnektoren
from kern.dienste import gvh
from kern.dienste.uestra import gleiche_linie
from kern.garantie import ablage, antrag, bahnablage

log = logging.getLogger("friday.fahrgastrechte")

KONNEKTOR = "fahrgastrechte"
TAKT_S = 300
NACHPRUEFEN_NACH = timedelta(minutes=5)
AUFGEBEN_NACH = timedelta(hours=6)       # so lange nach der Ankunft sucht Friday noch eine Messung

# ---------- Regeln (Stand 21.09.2026) ----------
EINZEL_STUFEN = ((120, 50), (60, 25))   # Minuten am Ziel → Prozent des gezahlten Preises (Art. 19, Teil A 8.2.6)
EINZEL_AB = 60
ZEITKARTE_CENT = {2: 150, 1: 225}       # je Fall ab 60 Minuten, Nahverkehr (Teil C 8.1)
ZEITKARTE_AB = 60
DTICKET_SAMMELN_AB = 20                 # Teil C 8.1: Fälle ab 20 Minuten zusammenrechnen, je volle 60 Minuten ein Satz
ZEITKARTE_DECKEL_PROZENT = 25
BAGATELL_CENT = 400
EMPFOHLEN_MONATE = 3                    # bahn.de: Beschwerden nach der EU-Verordnung binnen 3 Monaten — vorsichtig
VERJAEHRUNG_JAHRE = 1                   # Teil A 8.2.8: ein Jahr nach Ablauf der Fahrkarte
MONATE = antrag.MONATE

QUELLEN = (
    {"kurz": "EU-Verordnung 2021/782, Art. 19", "stand": "gültig seit 07.06.2023",
     "url": "https://eur-lex.europa.eu/legal-content/DE/TXT/HTML/?uri=CELEX:32021R0782"},
    {"kurz": "Deutschlandtarif Teil A, Nr. 8 (Fahrgastrechte)", "stand": "Stand 14.06.2026",
     "url": "https://www.deutschlandtarifverbund.de/wp-content/uploads/2026/06/20260528_A-1-TB-DT-Grundsaetze_Juni2026.pdf"},
    {"kurz": "Deutschlandtarif Teil C, Nr. 8 (Zeitkarten)", "stand": "Stand 14.12.2025",
     "url": "https://www.deutschlandtarifverbund.de/wp-content/uploads/2025/12/20251119_C_3_TB-DT-Zeitkarten_Dez2025.pdf"},
    {"kurz": "Tarifbedingungen Deutschland-Ticket, Nr. 1.4 und 1.6", "stand": "Stand 15.06.2026",
     "url": "https://www.deutschlandtarifverbund.de/wp-content/uploads/2026/06/20260612_TB-DTX_V16_Verl.Dez2026.pdf"},
    {"kurz": "S-Bahn Hannover: Fahrgastrechte", "stand": "abgerufen 21.09.2026",
     "url": "https://www.sbahn-hannover.de/de/service/fahrgastrechte"},
    {"kurz": "bahn.de: Rechte als Fahrgast", "stand": "abgerufen 21.09.2026",
     "url": "https://www.bahn.de/service/informationen-buchung/fahrgastrechte/rechtliche-regelungen"},
    {"kurz": "Servicecenter Fahrgastrechte: teilnehmende Bahnen", "stand": "Stand 12.06.2025",
     "url": "https://fahrgastrechte.info/wp-content/uploads/2025/06/250612_evu_im_scfgr.pdf"},
    {"kurz": "ÜSTRA-Garantie: Bedingungen (Garantie oder Fahrgastrechte)", "stand": "Stand 28.05.2026",
     "url": "https://www.uestra.de/service/uestra-gvh-garantie/"},
)
REGELN = (
    "Einzelfahrkarte (auch ICE/IC): ab 60 Minuten am Ziel 25 %, ab 120 Minuten 50 % des gezahlten Preises, auf 5 Cent "
    "aufgerundet — je Fahrkarte und Richtung einmal.",
    "Zeitkarte (Monatskarte, Jugendnetzkarte): 1,50 € je Fall ab 60 Minuten am Ziel, höchstens 25 % des gezahlten "
    "Kartenpreises. Eingereicht wird gesammelt, wenn die Karte abgelaufen ist.",
    "Deutschlandticket: wie eine Zeitkarte — zusätzlich zählen Fälle ab 20 Minuten zusammen: je volle 60 Minuten 1,50 €. "
    "Mit dem Deutschlandticket Hannover Jugend (35,40 €) sind das höchstens 8,85 € im Monat.",
    "Unter 4 € wird nichts ausgezahlt (bei Zeitkarten muss der Anspruch 4 € übersteigen).",
    "Kein Anspruch bei Unwetter, Naturkatastrophen, Personen im Gleis, Kabeldiebstahl oder Polizeieinsatz. Streik der Bahn "
    "selbst ist KEINE Ausnahme.",
    "Für dieselbe Fahrt entweder ÜSTRA-Garantie (5 € ab 20 Minuten) oder Fahrgastrechte — nie beides. Bei der S-Bahn ist die "
    "Garantie fast immer mehr wert.",
    "Einreichen: Servicecenter Fahrgastrechte (Formular unterschreiben, per Post) oder bei der S-Bahn Hannover über ihr "
    "Online-Formular. Die Tarife lassen den Anspruch ein Jahr nach Ablauf der Fahrkarte verjähren; bahn.de nennt für "
    "Beschwerden 3 Monate — Friday rechnet mit 3.",
)

# Wohin der Antrag geht — nach dem Betreiber, den die GVH-Auskunft nennt (gvh._betreiber).
STELLEN = {
    "servicecenter": {"name": "Servicecenter Fahrgastrechte", "weg": "post",
                      "adresse": "DB Fernverkehr AG, Servicecenter Fahrgastrechte, 60647 Frankfurt am Main",
                      "text": "Je Fall ein Formular — ausdrucken, unterschreiben und mit der Ticketkopie (und beim "
                              "Sammelantrag dem Preisnachweis) per Post schicken oder im DB Reisezentrum abgeben."},
    "transdev": {"name": "S-Bahn Hannover (Transdev)", "weg": "web",
                 "url": "https://www.sbahn-hannover.de/de/service/fahrgastrechte/fahrgastrechte-formular",
                 "text": "Online-Formular der S-Bahn Hannover. Ticket, Anschreiben und Preisnachweis als Beleg hochladen."},
    "westfalenbahn": {"name": "WestfalenBahn", "weg": "selbst", "url": "https://www.westfalenbahn.de",
                      "text": "Die WestfalenBahn ist nicht im Servicecenter Fahrgastrechte — der Antrag geht an sie selbst "
                              "(wie genau, hat Friday nicht geprüft)."},
    "unklar": {"name": "unklar", "weg": "", "text": "Welcher Betreiber den Zug fuhr, ist nicht bekannt."},
}
BETREIBER = (
    (re.compile(r"transdev|s-bahn hannover", re.I), "transdev"),
    (re.compile(r"westfalenbahn", re.I), "westfalenbahn"),
    (re.compile(r"db fernverkehr|db regio|metronom|\benno\b|\bstart\b|erixx|eurobahn|elbe-weser|\bevb\b|nordbahn|"
                r"s-bahn hamburg|\bakn\b|cantus|abellio", re.I), "servicecenter"),
)
# Fernzüge erkennt man an der Linie („ICE 643“, „IC 2446“) — die GVH-Auskunft nennt sie oft nur „Zug“ (gemessen 21.09.).
FERN = re.compile(r"^(ice|ic|ec|ece|en|nj|rj|rjx|tgv|flx)\b", re.I)
BAHN = re.compile(r"s-bahn|r-bahn|\bzug\b|regional|\bice\b|intercity|eurocity", re.I)
KEINE_BAHN = re.compile(r"bus|stadtbahn|straßenbahn|tram|u-bahn|ersatz|sprinti|taxi|fähre|schiff", re.I)
BAHN_LINIE = re.compile(r"^(s|re|rb|me|erx|enno|ire)\s?\d", re.I)


def benutzbar() -> bool:
    return konnektoren.benutzbar(KONNEKTOR)


def ist_bahn(kategorie: str, linie: str = "") -> bool:
    """Fahrgastrechte gibt es nur für Züge — nicht für Bus, Stadtbahn und Schienenersatzverkehr („von der S-Bahn bis
    zum ICE“, DB-Formular)."""
    k, l = (kategorie or "").strip(), (linie or "").strip()
    if KEINE_BAHN.search(k) or l.upper().startswith("SEV"):
        return False
    return bool(BAHN.search(k) or FERN.match(l) or BAHN_LINIE.match(l))


def ist_fernverkehr(kategorie: str, linie: str = "") -> bool:
    return bool(FERN.match((linie or "").strip()) or (kategorie or "").strip().upper() in ("ICE", "IC", "EC"))


def stelle_fuer(betreiber: str, linie: str = "") -> str:
    """Wohin der Antrag geht. Nennt die Auskunft keinen Betreiber (gemessen 22.09.2026 bei „ICE 144“), gehören ICE, IC und
    EC in Deutschland zu DB Fernverkehr — also ins Servicecenter. Nachtzüge und ausländische Züge bleiben unklar."""
    for muster, stelle in BETREIBER:
        if muster.search(betreiber or ""):
            return stelle
    if not (betreiber or "").strip() and re.match(r"^(ice|ic|ec|ece)\b", (linie or "").strip(), re.I):
        return "servicecenter"
    return "unklar"


def kartenart(ticket: dict | None) -> str | None:
    """„dticket“, „zeitkarte“ (Monatskarte, Jugendnetzkarte …) oder None (keine Zeitkarte, etwa eine Kundenkarte allein)."""
    if not ticket or ticket.get("ist_kundenkarte"):
        return None
    if ticket.get("art") == "Deutschlandticket":
        return "dticket"
    if ticket.get("art") == "Monatskarte":
        return "zeitkarte"
    return None


# ---------- Rechnen ----------

def einzel_betrag(preis_cent: int | None, verspaetung: int | None) -> tuple[int, int]:
    """(Betrag in Cent, Prozent) für eine Einzelfahrkarte — auf 5 Cent aufgerundet (Teil A 8.2.6). Die Bagatellgrenze
    prüft der Aufrufer."""
    if not preis_cent or verspaetung is None:
        return 0, 0
    prozent = next((p for ab, p in EINZEL_STUFEN if verspaetung >= ab), 0)
    return -(-(int(preis_cent) * prozent) // 500) * 5, prozent


def zeitkarte_rechnen(art: str, faelle: list[dict], deckel_cent: int | None, klasse: int = 2) -> dict:
    """Die Rechnung eines Sammelantrags. `faelle`: gemessene Fälle mit `verspaetung`. Beim Deutschlandticket zählen alle
    Fälle ab 20 Minuten zusammen (je volle 60 Minuten ein Satz) — das ist nie weniger als die Einzelfall-Rechnung, weil
    jeder Fall ab 60 Minuten mindestens 60 Minuten beiträgt. Sonst zählt jeder Fall ab 60 Minuten einmal.

    `benoetigt`: so wenige Fälle wie möglich für denselben Betrag — jeder Fall ist ein Formular, das die Person
    unterschreiben muss; über den 25-%-Deckel hinaus bringt ein weiterer Fall nichts."""
    satz = ZEITKARTE_CENT.get(klasse, ZEITKARTE_CENT[2])
    ab = DTICKET_SAMMELN_AB if art == "dticket" else ZEITKARTE_AB
    zaehlbar = sorted((f for f in faelle if (f.get("verspaetung") or 0) >= ab), key=lambda f: (-f["verspaetung"], f["id"]))
    minuten = sum(f["verspaetung"] for f in zaehlbar)
    einheiten = minuten // 60 if art == "dticket" else len(zaehlbar)
    ziel = einheiten if deckel_cent is None else min(einheiten, -(-deckel_cent // satz))
    benoetigt, summe = [], 0
    for f in zaehlbar:
        if (summe // 60 if art == "dticket" else len(benoetigt)) >= ziel:
            break
        benoetigt.append(f)
        summe += f["verspaetung"]
    einheiten_b = summe // 60 if art == "dticket" else len(benoetigt)
    roh = einheiten_b * satz
    betrag = min(roh, deckel_cent) if deckel_cent is not None else roh
    return {"satz_cent": satz, "ab_minuten": ab, "zaehlbar": len(zaehlbar), "minuten": minuten, "einheiten": einheiten,
            "benoetigt": [f["id"] for f in benoetigt], "minuten_benoetigt": summe, "einheiten_benoetigt": einheiten_b,
            "roh_cent": roh, "deckel_cent": deckel_cent, "gedeckelt": deckel_cent is not None and roh > deckel_cent,
            "betrag_cent": betrag}


def ausgezahlt(art: str, betrag_cent: int) -> bool:
    """Bagatellgrenze: bei Zeitkarten muss der Anspruch 4 € ÜBERSTEIGEN (Teil C 8.1), sonst genügen 4 € (Teil A 8.2.6)."""
    return betrag_cent > BAGATELL_CENT if art in ("dticket", "zeitkarte") else betrag_cent >= BAGATELL_CENT


def _plus_monate(tag: date, monate: int) -> date:
    j, m = divmod(tag.month - 1 + monate, 12)
    jahr, monat = tag.year + j, m + 1
    for d in (tag.day, 30, 29, 28):
        try:
            return date(jahr, monat, d)
        except ValueError:
            continue
    return date(jahr, monat, 28)


def fristen(ablauf: date) -> dict:
    """Ab wann und bis wann einreichen. `ablauf`: letzter Gültigkeitstag der Fahrkarte (Einzelfahrkarte: der Reisetag)."""
    return {"ab": (ablauf + timedelta(days=1)).isoformat(), "empfohlen": _plus_monate(ablauf, EMPFOHLEN_MONATE).isoformat(),
            "spaetestens": _plus_monate(ablauf, 12 * VERJAEHRUNG_JAHRE).isoformat()}


def euro(cent: int | None) -> str:
    return f"{(cent or 0) / 100:.2f}".replace(".", ",") + " €"


# ---------- Fälle bewerten ----------

def _zeit(wert):
    return antrag._zeit(wert)


def _ticket(profil: int, tid: str | None) -> dict | None:
    return next((t for t in ablage.standard().tickets(profil) if t.get("id") == tid), None) if tid else None


def _uhrzeit(text: str | None) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (text or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def widersprueche(f: dict) -> list[str]:
    """Was diesen Fall ausschließt, weil es einem anderen widerspricht — geht nie in einen Antrag.
    Dieselbe Fahrt als ÜSTRA-Garantie (Antrag oder schon Fall bei der ÜSTRA), zwei Fahrten derselben Person zur selben Zeit,
    ein Ticket auf einen anderen Namen."""
    from kern.garantie import abschicken
    a = ablage.standard()
    profil = int(f.get("profil") or 0)
    ab, an = _zeit(f.get("ab")), _zeit(f.get("an"))
    probleme = []
    if f.get("reise"):
        e = a.antrag_zur_reise(f["reise"], profil)
        if e:
            probleme.append(f"Für diese Fahrt gibt es schon den Garantie-Antrag #{e['id']} — ÜSTRA-Garantie ODER "
                            "Fahrgastrechte, nie beides.")
    if ab:
        for v in a.vorgaenge(10000, profil):
            if (v.get("fahrtdatum") == ab.date().isoformat() and _uhrzeit(v.get("ab")) == (ab.hour, ab.minute)
                    and gleiche_linie(f.get("linie") or "", v.get("linie") or "")):
                probleme.append(f"Diese Fahrt liegt schon als Garantiefall {v['vorgang']} bei der ÜSTRA — Garantie ODER "
                                "Fahrgastrechte, nie beides.")
    if ab and an:
        for x in bahnablage.faelle(profil):
            if x["id"] == f.get("id") or x["status"] == "verworfen":
                continue
            xab, xan = _zeit(x.get("ab")), _zeit(x.get("an"))
            if xab and xan and xab < an and ab < xan:
                probleme.append(f"Zur selben Zeit gibt es schon Bahn-Fall #{x['id']} ({x.get('linie')}, {xab:%d.%m. %H:%M}) — "
                                "niemand sitzt in zwei Zügen gleichzeitig.")
        for x in a.antraege(1000, profil):
            if x["status"] == "verworfen" or (f.get("reise") and x.get("reise") == f.get("reise")):
                continue
            xab, xan = _zeit(x.get("ab")), _zeit(x.get("an"))
            if xab and xan and xab < an and ab < xan:
                probleme.append(f"Zur selben Zeit gibt es schon den Garantie-Antrag #{x['id']} (Linie {x.get('linie')}) — "
                                "niemand sitzt in zwei Fahrzeugen gleichzeitig.")
    if f.get("fahrkarte") == "zeitkarte" and an:
        ticket = a.ticket_fuer(an.date(), profil) or {}
        d = a.angaben(profil)
        if ticket.get("inhaber") and not abschicken.name_passt(ticket["inhaber"], d.get("vorname", ""), d.get("nachname", "")):
            probleme.append(f"Auf dem Ticket steht „{ticket['inhaber']}“ — das passt nicht zu {d.get('vorname', '')} "
                            f"{d.get('nachname', '')}. Ist es das richtige Profil?")
    return probleme


def bewerten(f: dict) -> dict:
    """Der Stand eines Falls wird beim Lesen bestimmt — ein Ticket kommt nach, ein Bescheid wird eingetragen, die Ankunft
    ist vorbei. `stand`: wartet · wird nachgeprüft · gesammelt (Zeitkarte, zählt mit) · bereit (Einzelfahrkarte) ·
    unter 4 € · kein Anspruch · eingereicht · genehmigt · abgelehnt · verworfen."""
    a = ablage.standard()
    profil = int(f.get("profil") or 0)
    an, jetzt = _zeit(f.get("an")), config.jetzt()
    fern = ist_fernverkehr(f.get("kategorie") or "", f.get("linie") or "")
    stelle = stelle_fuer(f.get("betreiber") or "", f.get("linie") or "")
    hindernisse, hinweise, fehlt = [], list(f.get("hinweise") or []), []
    ticket, art = None, None
    if f.get("fahrkarte") == "einzel":
        art = "einzel"
        if not f.get("preis_cent"):
            fehlt.append("Preis der Fahrkarte")
    else:
        ticket = a.ticket_fuer(an.date(), profil) if an else None
        art = kartenart(ticket)
        if not ticket:
            fehlt.append(f"Ticket für {MONATE[an.month - 1]}" if an else "Ticket")
        elif not art:
            hindernisse.append(f"Das Ticket am Fahrtag („{ticket.get('bezeichnung') or ticket.get('art')}“) ist keine "
                               "Zeitkarte — mit einer Einzelfahrkarte den Fall als Einzelfahrkarte anlegen.")
        if fern:
            hindernisse.append("Mit einer Zeitkarte des Nahverkehrs (auch dem Deutschlandticket) darf man keinen Fernzug "
                               "nutzen — Fahrgastrechte gibt es hier nur mit einer Fahrkarte für diesen Zug.")
    v = f.get("verspaetung")
    # Ohne hochgeladenes Ticket gilt vorerst die mildeste Zeitkarten-Regel (Deutschlandticket, ab 20 Minuten) — sonst
    # fiele ein Fall heraus, bevor klar ist, welche Karte es war. Der Antrag bleibt ohnehin unvollständig, bis das Ticket da ist.
    mindest = (EINZEL_AB if art == "einzel" else ZEITKARTE_AB if art == "zeitkarte" else DTICKET_SAMMELN_AB)
    if f.get("nachgeprueft"):
        if v is None:
            hindernisse.append("Der Halt am Ziel fiel aus — welcher Zug danach kam, weiß Friday nicht; so ist nichts zu "
                               "belegen." if f.get("ziel_faellt_aus") else
                               "Für das Ziel gibt es keine gemessene Ankunft — so lässt sich nichts belegen.")
        elif v < mindest:
            hindernisse.append(f"Am Ziel waren es {v} Min — " + (
                "beim Deutschlandticket zählen Fälle erst ab 20 Minuten." if art == "dticket" else
                "Fahrgastrechte gibt es erst ab 60 Minuten."))
    hindernisse += widersprueche(f)
    betrag, prozent = einzel_betrag(f.get("preis_cent"), v) if art == "einzel" else (0, 0)
    eingereicht = bahnablage.antrag(f["antrag"]) if f.get("antrag") else None
    faellig = _zeit(f.get("faellig_ab"))
    if f.get("status") == "verworfen":
        stand = "verworfen"
    elif eingereicht:
        stand = eingereicht["status"]
    elif hindernisse:
        stand = "kein Anspruch"
    elif faellig and faellig > jetzt:
        stand = "wartet"
    elif not f.get("nachgeprueft"):
        stand = "wird nachgeprüft"
    elif art in ("dticket", "zeitkarte"):
        stand = "gesammelt"
    elif art == "einzel":
        stand = "bereit" if ausgezahlt(art, betrag) else "unter 4 €"
    else:
        stand = "gesammelt"                       # Zeitkarte ohne Ticket: zählt, sobald das Ticket da ist
    return {**f, "jid": None, "stand": stand, "art": art, "fern": fern, "stelle": stelle, "stelle_name": STELLEN[stelle]["name"],
            "ticket_id": (ticket or {}).get("id"), "hindernisse": hindernisse, "hinweise": list(dict.fromkeys(hinweise)),
            "fehlt": fehlt, "betrag_cent": betrag, "prozent": prozent, "person": a.profil_name(profil)}


# ---------- Sammelanträge (Entwürfe) ----------

def _schluessel(f: dict) -> str:
    if f["art"] == "einzel":
        return f"p{f['profil']}-ez-{f['id']}-{f['stelle']}"
    return f"p{f['profil']}-zk-{f['ticket_id'] or 'ohne'}-{f['stelle']}"


def _fehlende_angaben(profil: int) -> list[str]:
    a = ablage.standard()
    d = a.angaben(profil)
    namen = {"vorname": "Vorname", "nachname": "Nachname", "strasse": "Straße", "plz": "PLZ", "ort": "Ort",
             "anrede": "Anrede", "kontoinhaber": "Kontoinhaber"}
    fehlt = [text for feld, text in namen.items() if not (d.get(feld) or "").strip()]
    if profil and not (a.profil(profil) or {}).get("vollmacht"):
        fehlt.append("Zustimmung der Person")
    return fehlt


def _entwurf(schluessel: str, liste: list[dict], abzug_cent: int = 0) -> dict:
    """Ein Antrag aus seinen Fällen. `abzug_cent`: was von der 25-%-Grenze derselben Karte schon andere Anträge belegen."""
    a = ablage.standard()
    f0 = liste[0]
    profil, stelle, art = int(f0["profil"]), f0["stelle"], f0["art"]
    heute = config.jetzt().date()
    fehlt, hindernisse, hinweise = [], [], []
    if stelle == "unklar":
        hindernisse.append("Wohin der Antrag geht, ist unklar: Die Auskunft nannte keinen bekannten Betreiber "
                           f"(„{f0.get('betreiber') or '—'}“).")
    elif STELLEN[stelle]["weg"] == "selbst":
        hinweise.append(STELLEN[stelle]["text"])
    fehlt += [f"Angabe: {x}" for x in _fehlende_angaben(profil)]
    offen = [f for f in liste if f["stand"] in ("wartet", "wird nachgeprüft")]
    gemessen = [f for f in liste if f["stand"] in ("gesammelt", "bereit", "unter 4 €")]
    e = {"schluessel": schluessel, "profil": profil, "person": a.profil_name(profil), "stelle": stelle,
         "stelle_name": STELLEN[stelle]["name"], "weg": STELLEN[stelle].get("weg", ""), "stelle_text": STELLEN[stelle]["text"],
         "url": STELLEN[stelle].get("url", ""), "adresse": STELLEN[stelle].get("adresse", ""),
         "faelle": [f["id"] for f in liste], "offen": len(offen)}
    if art == "einzel":
        f = f0
        tag = _zeit(f["an"]).date()
        frist = fristen(tag)
        betrag = f["betrag_cent"]
        fehlt += f["fehlt"]
        if f["stand"] in ("wartet", "wird nachgeprüft"):
            stand = f["stand"]
        elif not ausgezahlt("einzel", betrag):
            stand = "unter 4 €"
        elif fehlt or hindernisse:
            stand = "unvollständig"
        elif heute.isoformat() > frist["spaetestens"]:
            stand = "Frist vorbei"
        else:
            stand = "bereit"
        if stelle == "servicecenter":
            hinweise.append("Liegt die Fahrkarte in deinem DB-Konto, geht es online schneller: DB Navigator → Meine Reisen → "
                            "Fahrgastrechte. Dann hier nur „eingereicht“ setzen.")
        e.update(art="einzel", titel=f"Einzelfahrkarte {f['linie']} am {tag:%d.%m.%Y}", von=tag.isoformat(), bis=tag.isoformat(),
                 betrag_cent=betrag, rechnung={"preis_cent": f.get("preis_cent"), "verspaetung": f.get("verspaetung"),
                                              "prozent": f["prozent"], "betrag_cent": betrag, "benoetigt": [f["id"]]},
                 benoetigt=[f["id"]], frist=frist, stand=stand)
    else:
        ticket = _ticket(profil, f0.get("ticket_id"))
        if not ticket:
            fehlt.append("Ticket für den Fahrtag")
        art = kartenart(ticket) or art or "zeitkarte"
        preis = round(float(ticket["preis"]) * 100) if ticket and ticket.get("preis") else None
        if ticket and preis is None:
            fehlt.append("Preis der Karte (für die 25-%-Grenze und als Nachweis)")
            if art == "dticket" and re.search(r"jugend", str(ticket.get("bezeichnung") or ""), re.I):
                hinweise.append("Das Deutschlandticket Hannover Jugend kostet laut ÜSTRA seit 01.01.2026 35,40 € im Monat — "
                                "trag beim Ticket den Preis ein, den du wirklich zahlst.")
        if art == "dticket":
            hinweise.append("Auf dem Deutschlandticket steht kein Preis: Einen Nachweis des gezahlten Preises beilegen "
                            "(Abo-Rechnung oder Kontoauszug, Teil C 8.2).")
        deckel_karte = preis * ZEITKARTE_DECKEL_PROZENT // 100 if preis is not None else None
        # „Insgesamt werden max. 25 % des gezahlten Zeitkartenpreises ausgezahlt“ (Teil C 8.1) — für die KARTE, nicht je
        # Antrag. Fahrten bei zwei Bahnen im selben Monat teilen sich die Grenze (gefunden am 22.09.2026 im Probelauf).
        deckel = max(0, deckel_karte - abzug_cent) if deckel_karte is not None else None
        if abzug_cent and deckel_karte is not None:
            hinweise.append(f"Die 25-%-Grenze ({euro(deckel_karte)}) gilt für die Karte insgesamt — {euro(abzug_cent)} davon "
                            "belegen schon andere Anträge.")
        rechnung = zeitkarte_rechnen(art, [f for f in gemessen], deckel)
        rechnung.update(preis_cent=preis, deckel_karte_cent=deckel_karte, abzug_cent=abzug_cent)
        von = (ticket or {}).get("gueltig_von") or ""
        bis = (ticket or {}).get("gueltig_bis") or ""
        frist = fristen(date.fromisoformat(bis)) if bis else None
        betrag = rechnung["betrag_cent"]
        if bis and heute.isoformat() <= bis:
            stand = "sammelt"                           # erst nach Ablauf der Karte gesammelt einreichen (Teil C 8.2)
        elif offen:
            stand = "wird nachgeprüft"
        elif not ausgezahlt(art, betrag):
            stand = "unter 4 €"
        elif fehlt or hindernisse:
            stand = "unvollständig"
        elif frist and heute.isoformat() > frist["spaetestens"]:
            stand = "Frist vorbei"
        else:
            stand = "bereit"
        name = "Deutschlandticket" if art == "dticket" else ((ticket or {}).get("bezeichnung") or "Zeitkarte")
        zeitraum = f"{date.fromisoformat(von):%d.%m.}–{date.fromisoformat(bis):%d.%m.%Y}" if von and bis else "ohne Ticket"
        e.update(art=art, titel=f"{name} {zeitraum}", von=von, bis=bis, ticket=(ticket or {}).get("id"),
                 ticket_name=(ticket or {}).get("bezeichnung") or (ticket or {}).get("art") or "",
                 betrag_cent=betrag, rechnung=rechnung, benoetigt=rechnung["benoetigt"], frist=frist, stand=stand)
    if eingereicht := bahnablage.antrag_zum_schluessel(schluessel):
        e["stand"] = "schon eingereicht"
        hinweise.insert(0, f"Für diesen Zeitraum ist schon Antrag B{eingereicht['id']} eingereicht — diese Fälle bleiben übrig.")
    e.update(fehlt=list(dict.fromkeys(fehlt)), hindernisse=hindernisse, hinweise=list(dict.fromkeys(hinweise)))
    return e


def entwuerfe(profil: int | None = 0, faelle_bewertet: list[dict] | None = None) -> list[dict]:
    """Alle Anträge, die sich aus den noch nicht eingereichten Fällen ergeben: je Zeitkarte und Stelle ein Sammelantrag,
    je Einzelfahrkarte einer. Fälle ohne Anspruch gehören zu keinem."""
    liste = faelle_bewertet if faelle_bewertet is not None else [bewerten(f) for f in bahnablage.faelle(profil)]
    gruppen: dict[str, list[dict]] = {}
    for f in liste:
        if f.get("antrag") or f["stand"] in ("kein Anspruch", "verworfen"):
            continue
        gruppen.setdefault(_schluessel(f), []).append(f)
    fertig, je_karte = [], {}
    for k, v in gruppen.items():
        if v[0]["art"] == "einzel":
            fertig.append(_entwurf(k, v))
        else:
            je_karte.setdefault((int(v[0]["profil"]), v[0].get("ticket_id")), []).append((k, v))
    for (pid, tid), teile in je_karte.items():
        # Die Grenze der Karte teilen sich alle ihre Anträge: erst was schon eingereicht ist (abgelehnte nicht), dann die
        # Entwürfe, der größte zuerst.
        belegt = sum(((x.get("erhalten_cent") if x["status"] == "genehmigt" else x.get("erwartet_cent")) or 0)
                     for x in bahnablage.antraege(pid) if tid and x.get("ticket") == tid and x["status"] != "abgelehnt")
        for k, v, _ in sorted(((k, v, _entwurf(k, v)) for k, v in teile), key=lambda x: -x[2]["betrag_cent"]):
            e = _entwurf(k, v, belegt)
            fertig.append(e)
            if e["stand"] != "schon eingereicht" and ausgezahlt(e["art"], e["betrag_cent"]):
                belegt += e["betrag_cent"]
    rang = {"bereit": 0, "unvollständig": 1, "wird nachgeprüft": 2, "sammelt": 3, "wartet": 3, "unter 4 €": 4,
            "schon eingereicht": 5, "Frist vorbei": 6}
    return sorted(fertig, key=lambda e: (rang.get(e["stand"], 9), (e.get("frist") or {}).get("empfohlen") or "9999",
                                         e["schluessel"]))


def entwurf(schluessel: str) -> dict | None:
    m = re.fullmatch(r"p(\d+)-.+", schluessel or "")
    if not m:
        return None
    return next((e for e in entwuerfe(int(m.group(1))) if e["schluessel"] == schluessel), None)


def einreichen(schluessel: str) -> dict:
    """Eike hat den Antrag selbst abgegeben (Post, Reisezentrum, Webformular) — Friday merkt sich, was drinsteht. Nur, was
    bereit ist; die Fälle sind danach gebunden und lassen sich nicht noch einmal beantragen."""
    e = entwurf(schluessel)
    if not e:
        raise LookupError("Diesen Antrag gibt es nicht (mehr).")
    if e["stand"] != "bereit":
        raise ValueError(f"Als eingereicht markieren geht nur bei einem Antrag, der bereit ist (Stand: {e['stand']}).")
    aid = bahnablage.einreichen({"profil": e["profil"], "schluessel": schluessel, "art": e["art"], "stelle": e["stelle"],
                                 "ticket": e.get("ticket"), "von": e.get("von"), "bis": e.get("bis"),
                                 "faelle": e["benoetigt"], "erwartet_cent": e["betrag_cent"], "rechnung": e["rechnung"],
                                 "eingereicht_am": config.jetzt().isoformat(timespec="minutes")}, e["benoetigt"])
    return antrag_oeffentlich(bahnablage.antrag(aid))


def bescheid(aid: int, status: str, erhalten_cent: int | None = None, grund: str = "", vorgang: str = "") -> dict:
    """Was die Bahn geantwortet hat — setzt nur Eike (Bescheide aus Gmail kommen später)."""
    e = bahnablage.antrag(aid)
    if not e:
        raise LookupError("Diesen Antrag gibt es nicht.")
    if status not in ("eingereicht", "genehmigt", "abgelehnt"):
        raise ValueError("Unbekannter Stand.")
    if status == "genehmigt" and (erhalten_cent is None or erhalten_cent < 0):
        raise ValueError("Wie viel wurde genehmigt?")
    bahnablage.antrag_aendern(aid, status=status, erhalten_cent=erhalten_cent if status == "genehmigt" else None,
                              grund=grund[:300], vorgang=vorgang[:40] or e.get("vorgang"),
                              entschieden_am=config.jetzt().isoformat(timespec="minutes") if status != "eingereicht" else None)
    return antrag_oeffentlich(bahnablage.antrag(aid))


def antrag_oeffentlich(e: dict) -> dict:
    a = ablage.standard()
    return {**e, "person": a.profil_name(int(e.get("profil") or 0)), "stelle_name": STELLEN.get(e.get("stelle") or "unklar",
            STELLEN["unklar"])["name"], "erwartet": euro(e.get("erwartet_cent")),
            "erhalten": euro(e.get("erhalten_cent")) if e.get("status") == "genehmigt" else ""}


# ---------- Fälle anlegen und nachprüfen ----------

def _messung(z: dict) -> tuple[datetime | None, bool]:
    """Die Ankunft am Ziel: gemessen (Echtzeit), sonst nichts. Fällt der Halt aus, gibt es keine."""
    return (_zeit(z.get("an_echt")) or None), bool(z.get("faellt_aus"))


async def erfassen(reise_id: str, start_ref: str, ziel_ref: str, profil: int = 0, fahrkarte: str = "zeitkarte",
                   preis_cent: int | None = None, auftrag: str = "", rueckfahrt: bool = False) -> dict:
    """Eine Zugfahrt als Fahrgastrechte-Fall einer Person anlegen. Gemessen wird wie bei der Garantie: die Echtzeit der
    GVH-Auskunft am Ziel, nach der Ankunft; liegt das Ziel in der Zukunft, wartet der Fall und `nachpruefen` misst später."""
    a = ablage.standard()
    if profil and not a.profil(profil):
        return {"ok": False, "text": "Dieses Profil gibt es nicht (mehr)."}
    if profil and a.profil(profil).get("archiviert"):
        return {"ok": False, "text": f"{a.profil_name(profil)} ist archiviert — hol das Profil zuerst zurück."}
    if fahrkarte not in ("zeitkarte", "einzel"):
        return {"ok": False, "text": "Unbekannte Fahrkarte."}
    if fahrkarte == "einzel" and not (preis_cent and 0 < preis_cent <= 100000):
        return {"ok": False, "text": "Für eine Einzelfahrkarte braucht Friday den gezahlten Preis (für diese Richtung)."}
    wer = "" if not profil else f" für {a.profil_name(profil)}"
    if alt := bahnablage.fall_zur_reise(reise_id, profil):
        return {"ok": False, "text": f"Für diese Fahrt gibt es schon Bahn-Fall #{alt['id']}{wer} — eine Fahrt zählt nur einmal."}
    if e := a.antrag_zur_reise(reise_id, profil):
        return {"ok": False, "text": f"Für diese Fahrt gibt es schon den Garantie-Antrag #{e['id']}{wer} — ÜSTRA-Garantie ODER "
                                     "Fahrgastrechte, nie beides. Den Garantie-Antrag erst verwerfen, wenn du wechseln willst."}
    try:
        v = await antrag.verlauf(reise_id)
        s, z = antrag._halte_waehlen(v, start_ref, ziel_ref)
    except (LookupError, ValueError, gvh.GvhFehler) as exc:
        return {"ok": False, "text": str(exc)}
    if not ist_bahn(v.get("kategorie") or "", v.get("linie") or ""):
        return {"ok": False, "text": f"{v.get('kategorie') or 'Diese Fahrt'} {v.get('linie')}: Fahrgastrechte gibt es nur für "
                                     "Züge (S-Bahn bis ICE) — für Bus und Stadtbahn ist die ÜSTRA-Garantie da."}
    reise = a.reise(reise_id) or {}
    betreiber = v.get("betreiber") or ""
    ab, an = s["ab_plan"] or s["an_plan"], z["an_plan"] or z["ab_plan"]
    echt, faellt_aus = _messung(z)
    verspaetung = round((echt - an).total_seconds() / 60) if echt else None
    faellig = an + timedelta(minutes=max(verspaetung or 0, 0))
    jetzt = config.jetzt()
    fertig = bool(echt) and faellig + NACHPRUEFEN_NACH <= jetzt
    hinweise = []
    if not betreiber:
        hinweise.append("Die Auskunft nannte keinen Betreiber — wohin der Antrag geht, ist noch offen.")
    fid = bahnablage.fall_speichern({
        "profil": profil, "reise": reise_id, "jid": v.get("jid") or reise.get("jid"), "linie": v["linie"],
        "kategorie": v.get("kategorie"), "betreiber": betreiber, "richtung": v.get("richtung"), "start": s["name"],
        "ziel": z["name"], "start_ref": start_ref, "ziel_ref": ziel_ref, "ab": ab, "an": an, "an_echt": echt,
        "verspaetung": verspaetung, "quelle": "Echtzeit der GVH-Auskunft" if echt else "", "faellig_ab": faellig,
        "nachgeprueft": fertig, "ziel_faellt_aus": faellt_aus and not echt, "fahrkarte": fahrkarte,
        "preis_cent": preis_cent if fahrkarte == "einzel" else None, "auftrag": (auftrag or "")[:40],
        "rueckfahrt": rueckfahrt, "hinweise": hinweise})
    f = bewerten(bahnablage.fall(fid))
    text = (f"Bahn-Fall #{fid}{wer}: {v['linie']} von {s['name']} ({ab:%d.%m. %H:%M}) nach {z['name']} "
            f"(planmäßig {an:%H:%M}): " + (f"{verspaetung} Min später" if verspaetung is not None else "noch keine Messung")
            + f". Stand: {f['stand']}" + (f" bis {faellig:%H:%M}" if f["stand"] == "wartet" else "") + ".")
    if f["hindernisse"]:
        text += " " + " ".join(f["hindernisse"])
    if f["art"] == "einzel" and f["betrag_cent"]:
        text += f" Das wären {f['prozent']} % = {euro(f['betrag_cent'])}."
    return {"ok": True, "text": text + " Eingereicht wird nichts — das machst du.", "fall": fall_oeffentlich(f)}


def fall_oeffentlich(f: dict) -> dict:
    """Für die App: ohne die lange Fahrt-Kennung."""
    return {k: v for k, v in f.items() if k not in ("jid",)}


async def nachpruefen() -> int:
    """Nach der Ankunft die Messung holen — einmal je Fall. Kommt auch Stunden später keine, gibt Friday auf (kein Anspruch
    ohne Messung). Gemeldet wird nicht hier, sondern in `erinnern`, sobald ein ANTRAG bereit ist — Zeitkarten-Fälle allein
    bringen 1,50 € und wären nur Lärm auf dem Handy."""
    a = ablage.standard()
    jetzt = config.jetzt()
    geprueft = 0
    for f in bahnablage.faelle(None):
        faellig = _zeit(f.get("faellig_ab"))
        if f["status"] != "offen" or f.get("antrag") or f.get("nachgeprueft") or (faellig and faellig + NACHPRUEFEN_NACH > jetzt):
            continue
        try:
            v = await antrag.verlauf(f["reise"]) if f.get("reise") and a.reise(f["reise"]) else await gvh.fahrt_verlauf(f["jid"])
            _, z = antrag._halte_waehlen(v, f["start_ref"], f["ziel_ref"])
        except Exception as exc:
            log.debug("Bahn-Fall %s nicht nachprüfbar: %s", f["id"], type(exc).__name__)
            if faellig and jetzt - faellig > AUFGEBEN_NACH:
                bahnablage.fall_aendern(f["id"], nachgeprueft=True)
            continue
        echt, faellt_aus = _messung(z)
        an = _zeit(f["an"])
        if not echt and faellig and jetzt - faellig <= AUFGEBEN_NACH and not faellt_aus:
            continue                                           # vielleicht kommt die Messung noch
        verspaetung = round((echt - an).total_seconds() / 60) if echt and an else None
        bahnablage.fall_aendern(f["id"], an_echt=echt, verspaetung=verspaetung, nachgeprueft=True,
                                ziel_faellt_aus=faellt_aus and not echt,
                                faellig_ab=an + timedelta(minutes=max(verspaetung or 0, 0)) if an else f.get("faellig_ab"),
                                quelle="Echtzeit der GVH-Auskunft" if echt else "")
        geprueft += 1
    return geprueft


def _gemeldet_datei():
    return config.daten_ordner() / "fahrgastrechte.json"


def _gemeldet() -> dict:
    try:
        d = json.loads(_gemeldet_datei().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


async def erinnern() -> int:
    """Genau EINE Nachricht, wenn ein Antrag bereit wird, und eine, wenn die (vorsichtige) Frist in zwei Wochen endet."""
    from kern.dienste import benachrichtigungen
    heute = config.jetzt().date()
    stand = _gemeldet()
    gemeldet = set(stand.get("gemeldet") or [])
    neu = 0
    for e in entwuerfe(None):
        if e["stand"] != "bereit":
            continue
        wer = f" ({e['person']})" if e["profil"] else ""
        if f"bereit:{e['schluessel']}" not in gemeldet:
            await asyncio.to_thread(benachrichtigungen.senden, "garantie", "Fahrgastrechte: Antrag bereit" + wer,
                                    f"{e['titel']}: {euro(e['betrag_cent'])} bei {e['stelle_name']}.", "/#studio",
                                    f"bahn-{e['schluessel']}")
            gemeldet.add(f"bereit:{e['schluessel']}")
            neu += 1
        empfohlen = (e.get("frist") or {}).get("empfohlen")
        if empfohlen and date.fromisoformat(empfohlen) - heute <= timedelta(days=14) and f"frist:{e['schluessel']}" not in gemeldet:
            await asyncio.to_thread(benachrichtigungen.senden, "garantie", "Fahrgastrechte: Frist läuft ab" + wer,
                                    f"{e['titel']} ({euro(e['betrag_cent'])}) bis {date.fromisoformat(empfohlen):%d.%m.} "
                                    "einreichen.", "/#studio", f"bahn-frist-{e['schluessel']}")
            gemeldet.add(f"frist:{e['schluessel']}")
            neu += 1
    if neu:
        ablage._schreiben_600(_gemeldet_datei(), json.dumps({"gemeldet": sorted(gemeldet)}, ensure_ascii=False).encode())
    return neu


async def dauerlauf() -> None:
    await asyncio.sleep(150)
    while True:
        try:
            if benutzbar():
                if n := await nachpruefen():
                    log.info("Fahrgastrechte: %s Fälle nachgeprüft", n)
                await erinnern()
        except Exception:
            log.exception("Fahrgastrechte: Lauf gescheitert")
        await asyncio.sleep(TAKT_S)


# ---------- Züge finden ----------

GEMERKT_STUNDEN = 48


def gemerkte_zuege() -> list[dict]:
    """Verspätete Züge aus dem Sammler der Garantie (ab 20 Minuten, letzte 48 Stunden) — Bus und Stadtbahn nicht."""
    from kern.garantie import suche
    grenze = config.jetzt() - timedelta(hours=GEMERKT_STUNDEN)
    liste = []
    for r in ablage.standard().reisen(mindest=DTICKET_SAMMELN_AB, tage=3):
        plan = _zeit(r.get("plan"))
        if plan and plan >= grenze and ist_bahn(r.get("kategorie") or "", r.get("linie") or ""):
            liste.append(suche.oeffentlich(r))
    return sorted(liste, key=lambda r: r["plan"], reverse=True)[:60]


async def zuege_am(halt: str, zeit: datetime) -> dict:
    """Züge, die an einem Bahnhof um eine Zeit abfahren — auch ICE und IC, die der Sammler nicht sucht. Jeder gezeigte Zug
    wird gemerkt, damit sich danach sein Verlauf öffnen lässt."""
    from kern.garantie import suche
    h = await gvh._eine(halt)
    liste = await gvh.tafel_roh(h["id"], zeit - timedelta(minutes=15), anzahl=100)
    a = ablage.standard()
    zuege = []
    for f in liste:
        if not ist_bahn(f.get("kategorie") or "", f.get("linie") or "") or f["plan"] > zeit + timedelta(hours=2):
            continue
        a.reise_merken({**f, "halt": h["name"], "bereich": "eingetragen"})
        zuege.append({**suche.oeffentlich({**f, "halt": h["name"], "bereich": "eingetragen"}),
                      "betreiber": f.get("betreiber") or "", "fern": ist_fernverkehr(f.get("kategorie") or "", f["linie"])})
    return {"halt": h["name"], "zuege": zuege[:40]}


# ---------- Für die App ----------

def summen(profil: int | None = 0) -> dict:
    antraege_ = bahnablage.antraege(profil)
    return {"genehmigt_cent": sum(e.get("erhalten_cent") or 0 for e in antraege_ if e["status"] == "genehmigt"),
            "offen_cent": sum(e.get("erwartet_cent") or 0 for e in antraege_ if e["status"] == "eingereicht"),
            "eingereicht": sum(e["status"] == "eingereicht" for e in antraege_),
            "genehmigt": sum(e["status"] == "genehmigt" for e in antraege_),
            "abgelehnt": sum(e["status"] == "abgelehnt" for e in antraege_)}


def stand(profil: int | None = 0) -> dict:
    """Alles für den Block „Bahn-Fahrgastrechte“ — einer Person oder (profil=None, Übersicht) aller. Keine IBAN, keine Adresse."""
    if not benutzbar():
        return {"aktiv": False, "regeln": REGELN, "quellen": QUELLEN}
    a = ablage.standard()
    bewertet = [bewerten(f) for f in bahnablage.faelle(profil)]
    personen = [{"id": 0, "name": "Ich"}] + [{"id": p["id"], "name": p.get("name") or f"Profil {p['id']}"}
                                             for p in a.profile() if not p.get("archiviert")]
    return {"aktiv": True, "profil": profil, "entwuerfe": entwuerfe(profil, bewertet),
            "faelle": [fall_oeffentlich(f) for f in bewertet if not f.get("antrag")][:200],
            "antraege": [antrag_oeffentlich(e) for e in bahnablage.antraege(profil)],
            "summen": summen(profil), "zuege": gemerkte_zuege(), "personen": personen if profil is None else [],
            "regeln": REGELN, "quellen": QUELLEN, "stellen": {k: {kk: vv for kk, vv in v.items()} for k, v in STELLEN.items()}}
