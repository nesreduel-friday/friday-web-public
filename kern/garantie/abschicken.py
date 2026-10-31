"""Einen Garantie-Antrag abschicken — erst nach allen Prüfungen, nur auf Eikes Knopfdruck.

Eike, 16.09.2026: „du kannst die Funktion zum Abschicken einbauen, wenn alle Angaben
korrekt sind — das soll überprüft werden, es darf nichts falsch sein, ein altes Ticket
oder so.“ Ausdrücklich erlaubt am selben Tag.

Schutzschichten:
- eigene Freigabe: „Anträge abschicken“ bei der ÜSTRA-Garantie (Schreiben), standardmäßig aus;
- nur über die App (POST /api/garantie/antrag/{id}/abschicken mit Bestätigung) —
  KEIN Werkzeug für Sprache oder Chat;
- `pruefen` geht jede Angabe durch und fragt Fahrt und Fahrkarte LIVE beim Formular
  ab; bei einem einzigen Problem wird nichts gesendet;
- nie automatisch wiederholt; ist unklar, ob der Antrag ankam, gilt er als
  abgeschickt, bis die Eingangsmail (Gmail) es klärt — lieber das als doppelt.

Aufbau der Anfrage: Mitschnitt vom 16.09.2026, siehe docs/LEHREN.md.

Für Freunde (Profile, 20.09.2026) gilt alles davon UND: Der Antrag geht nur mit den Angaben,
dem Ticket und der E-Mail-Adresse SEINES Profils raus — nie mit Eikes. Zusätzlich verlangt
`pruefen` bei fremden Profilen, dass der Freund zugestimmt hat (Häkchen im Profil), dass die
E-Mail-Adresse nicht Eikes eigene ist und dass ein erkannter Ticket-Inhaber zum Namen passt
(damit kein Screenshot beim falschen Freund landet). Die Bestätigung in der App nennt die Person.

Widersprüche (21.09.2026, `widersprueche`): Was sich gegenseitig ausschließt, geht nie raus — auch nicht als
„Zum Abtippen“ ohne Warnung. Name auf dem Ticket gegen die Person (auch bei Eike, mit Vorname), Kundenkartennummer
auf dem Ticket gegen die der Person, dieselbe Kundenkarte oder Ticketnummer bei zwei Personen, die Ticketnummer des
Vormonats im neuen Ticket, ein „Monatsticket“, das länger als einen Monat gilt, zwei Fahrten derselben Person zur
selben Zeit und eine Fahrt, die schon als Fall bei der ÜSTRA liegt. Papiertickets erst, wenn Eike die Nummern mit
dem Original verglichen hat.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

from kern import config, konnektoren
from kern.dienste import garantie as formular
from kern.dienste.uestra import gleiche_linie
from kern.garantie import ablage, antrag
from kern.garantie import ticket as ticketlesen

log = logging.getLogger("friday.garantie")

KONNEKTOR = "uestra_garantie"
ANREDEN = {"Herr": "MR", "Frau": "MS", "Divers": "DIVERS"}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
# Welches Deutschlandticket: aus der Bezeichnung auf dem Ticket. Reihenfolge zählt
# („Job Azubi“ vor „Job“). Passt nichts eindeutig, schickt Friday nicht ab.
DTICKET_DETAILS = (
    (re.compile(r"jugend", re.I), "Deutschlandticket (Hannover Jugend)"),
    (re.compile(r"sozial", re.I), "Deutschlandticket (Hannover Sozial)"),
    (re.compile(r"ehrenamt", re.I), "Deutschlandticket (Hannover Ehrenamt)"),
    (re.compile(r"job\s*azubi|azubi", re.I), "Deutschlandticket (Hannover Job Azubi)"),
    (re.compile(r"semester", re.I), "Deutschland-Semesterticket"),
    # Das reine Deutschlandticket ohne Zusatz (Formular-Option „Deutschlandticket“, gemessen 09.10.2026) — nur,
    # wenn die Bezeichnung nichts weiter nennt.
    (re.compile(r"^\s*(d-?ticket|deutschland\s*-?ticket)\s*$", re.I), "Deutschlandticket"),
)
# Felder, die Friday nicht kennt — verlangt das Formular sie, bleibt es beim Hinweis.
NICHT_UNTERSTUETZT = ("firstAndLastNameAvailable", "matriculationNumberAvailable", "tokenNumberAvailable")

_laeuft: set[int] = set()
_sperre = asyncio.Lock()


def _utc(zeit: datetime) -> str:
    return zeit.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ticket_detail(ticket: dict) -> str | None:
    text = " ".join(str(ticket.get(k) or "") for k in ("bezeichnung", "art"))
    for muster, detail in DTICKET_DETAILS[:-1]:
        if muster.search(text):
            return detail
    if DTICKET_DETAILS[-1][0].search(str(ticket.get("bezeichnung") or "")):
        return DTICKET_DETAILS[-1][1]
    return None


def anfrage_bauen(*, angaben: dict, fahrt: dict, leg_index: int, ab: datetime, verspaetung: int,
                  ticket: dict, detail: str, typ: str, erstattung: dict, anhang, geoeffnet: datetime,
                  art: str | None = None) -> dict:
    """Die Anfrage genau so, wie das Formular sie baut (Mitschnitt 16.09.2026).

    Welche Nummern mitgehen, entscheidet das Formular selbst über die Regeln der Fahrkarte (`erstattung`):
    `subscriptionCustomerNumberAvailable` → Abo-Nummer (Deutschlandticket), `customerCardFormat` → Kundenkarte und
    `ticketNumberAvailable` → Ticketnummer (Jugend-/Ausbildungsnetzkarte). Was nicht verlangt ist, bleibt null —
    so, wie es das Skript des Formulars macht."""
    route = {**fahrt, "selectedLegIndex": leg_index, "selectedLeg": fahrt["legs"][leg_index]}
    bis = datetime.fromisoformat(ticket["gueltig_bis"]).replace(hour=23, minute=59, tzinfo=ab.tzinfo)
    anhaenge = [anhang] if isinstance(anhang, str) else list(anhang)
    return {
        "customTemplate": None, "internal": False, "attachmentIds": [], "automaticConfirmationReceipt": True,
        "clientDto": {
            "eTicketEnabled": False, "tenMinEnabled": False, "formOfAddress": ANREDEN[angaben["anrede"]],
            "title": "", "enterprise": "", "firstName": angaben["vorname"], "lastName": angaben["nachname"],
            "streetNumber": angaben["strasse"], "zip": angaben["plz"], "city": angaben["ort"], "country": "",
            "email": angaben["email"], "phoneNo": "", "faxNo": "", "mobileNo": "", "externalClientId": "",
            "anonymous": False, "answerNotRequired": False, "signature": "", "marketingAgreement": False,
            "privacyType": "TEN_MIN"},
        "additionalInfo": {"startTimestamp": _utc(geoeffnet)},
        "incomingChannelDto": {},
        "complaintPointDtos": [{
            "creatorType": "TenMinutes", "type": "TEN_MIN", "guaranteeType": None,
            "routeDetailsDto": {"dateOfTrip": ab.date().isoformat(), "route": route,
                                "delayType": "DELAYED_AT_START", "tripNotStarted": False, "refundType": "ticket",
                                "refundAmount": None, "delayValue": int(verspaetung), "description": ""},
            "tripNotFoundDescription": "",
            "concernDto": {"concernDetail": "", "concernClient": "", "concernAcademia": ""},
            "guaranteeDto": {"departureTime": "", "arrivalTime": "", "complaintNumber": ""},
            "dateOfEvent": _utc(ab), "complaintDescription": "", "deviceInformation": "",
            "signedTicketTypeId": None, "ticketNumber": "", "noTimeOfEvent": True, "refundAmount": 0,
            "refundType": None, "ticketName": "",
            "ticketDto": {
                "signedId": None, "refundIdSignature": erstattung["refundIdSignature"],
                "electronicTicketDto": None, "expiryDate": _utc(bis), "personNumber": None, "ticketPrice": None,
                "ticketNumber": (ticket.get("ticketnummer") or None) if erstattung.get("ticketNumberAvailable") else None,
                "adultCompanions": 0, "childCompanions": 0, "firstClass": False,
                "firstClassCompanions": 0,
                "customerCard": (ticket.get("kundenkarte") or angaben.get("kundenkarte") or None)
                                if erstattung.get("customerCardFormat") is not None else None,
                "subscriptionCard": angaben.get("abo_nummer") if erstattung.get("subscriptionCustomerNumberAvailable") else None,
                "firstAndLastName": None, "tokenNumber": None, "attachmentIds": anhaenge,
                "matriculationNumber": None, "ticketKindName": art or ticket["art"], "ticketType": typ,
                "ticketDetails": detail},
            "bankData": {"accountOwner": angaben["kontoinhaber"], "iban": angaben["iban"]},
            "qualificationsDto": {"selectedSchool": {}, "documents": []},
            "selectedTransportationType": {}, "ticketValidationState": None, "attachmentIds": [],
            "shouldRenderLayeredTypeBlockConcernData": False, "shouldRenderBankDataBlockData": True,
            "realStartTime": None, "realEndTime": None, "realSectionStartTime": None, "realSectionEndTime": None}],
    }


def _worte(text: str) -> list[str]:
    """Namen vergleichbar machen: „Müller“ = „Mueller“, Akzente weg, Bindestrich trennt."""
    t = (text or "").casefold()
    for alt, neu in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(alt, neu)
    t = "".join(z for z in unicodedata.normalize("NFD", t) if not unicodedata.combining(z))
    return re.findall(r"[a-z]+", t)


def name_passt(inhaber: str, vorname: str, nachname: str) -> bool:
    """Passt der Name auf dem Ticket zur Person? Der Nachname muss ganz dastehen; steht mehr da, auch der erste Vorname
    (oder sein Anfangsbuchstabe, „T. Müller“). Geschwister haben denselben Nachnamen — der allein reicht nicht.
    Steht kein Name auf dem Ticket, gibt es nichts zu vergleichen."""
    auf_ticket = set(_worte(inhaber))
    nach, vor = _worte(nachname), _worte(vorname)
    if not auf_ticket or not nach:
        return True
    if not set(nach) <= auf_ticket:
        return False
    rest = auf_ticket - set(nach)
    if vor and rest:
        return vor[0] in rest or any(len(w) == 1 and w == vor[0][0] for w in rest)
    return True


def _andere_personen(a, profil: int) -> list[tuple[int, str]]:
    return [(pid, a.profil_name(pid) if pid else "dir") for pid in [0] + [p["id"] for p in a.profile()] if pid != profil]


def widersprueche(aid: int, ticket: dict | None = None) -> list[str]:
    """Was an diesem Antrag nicht zusammenpasst — Gründe, NICHTS abzuschicken und nichts abzutippen, bevor es geklärt ist.
    Liest nur die Ablage (kein Netz). `ticket`: das Ticket des Fahrtags, sonst sucht die Funktion es selbst."""
    a = ablage.standard()
    e = a.antrag(aid)
    if not e:
        return []
    profil = int(e.get("profil") or 0)
    person = a.profil_name(profil) if profil else "dich"
    ab, an = antrag._zeit(e.get("ab")), antrag._zeit(e.get("an"))
    d = a.angaben(profil)
    ticket = ticket if ticket is not None else ((a.ticket_fuer(an.date(), profil) if an else None) or {})
    probleme = []

    # Name auf dem Ticket — bei jeder Person, auch bei Eike (ein Freundesticket unter „Ich“ hochgeladen).
    if ticket.get("inhaber") and not name_passt(ticket["inhaber"], d.get("vorname", ""), d.get("nachname", "")):
        probleme.append(f"Auf dem Ticket steht „{ticket['inhaber']}“ — das passt nicht zu {d.get('vorname', '')} "
                        f"{d.get('nachname', '')}. Ist es das richtige Profil? (Falsch gelesen: beim Ticket unter "
                        "„Erkanntes korrigieren“ den Namen berichtigen.)")

    # Kundenkarte: Die Nummer gehört zur Person. Steht auf dem Ticket eine andere, ist eine von beiden falsch.
    kk_ticket, kk_person = ticket.get("kundenkarte") or "", d.get("kundenkarte") or ""
    if kk_ticket and kk_person and kk_ticket != kk_person:
        probleme.append(f"Auf dem Ticket steht die Kundenkartennummer {kk_ticket}, bei {person} gespeichert ist "
                        f"{kk_person} — eine davon ist falsch. Bitte mit der Kundenkarte vergleichen und berichtigen.")
    karte, nummer = kk_ticket or kk_person, ticket.get("ticketnummer") or ""
    for pid, wer in _andere_personen(a, profil):
        if karte and (a.angaben(pid, roh=True).get("kundenkarte") == karte
                      or any(t.get("kundenkarte") == karte for t in a.tickets(pid))):
            probleme.append(f"Die Kundenkartennummer {karte} gehört auch zu {wer} — eine Kundenkarte hat genau eine Person.")
        if nummer and any(t.get("ticketnummer") == nummer for t in a.tickets(pid)):
            probleme.append(f"Die Ticketnummer {nummer} steht auch bei einem Ticket von {wer} — ein Ticket gehört genau "
                            "einer Person. Ist das Ticket beim richtigen Profil?")

    s = ticketlesen.sorte(ticket)
    if s and ticket:
        # Die Nummer des Vormonats im neuen Ticket (abgeschrieben, altes Foto) — nur mit Eikes ausdrücklichem Blick.
        if nummer and not ticket.get("nummern_geprueft"):
            for t in a.tickets(profil):
                if (t.get("id") != ticket.get("id") and t.get("ticketnummer") == nummer
                        and (t.get("gueltig_von"), t.get("gueltig_bis")) != (ticket.get("gueltig_von"), ticket.get("gueltig_bis"))):
                    probleme.append(f"Die Ticketnummer {nummer} ist dieselbe wie beim Ticket bis {_tag(t.get('gueltig_bis'))} — "
                                    "ist das wirklich die Nummer des neuen Monatstickets? Vergleiche sie und setz beim Ticket "
                                    "das Häkchen „Nummern verglichen“.")
                    break
        grund = ticketlesen.muss_verglichen_werden(ticket)
        if grund:
            probleme.append(f"{grund[0].upper() + grund[1:]}: Vergleiche Kundenkarten- und Ticketnummer mit dem Original und "
                            "setz beim Ticket das Häkchen „Nummern verglichen“ — erst dann schickt Friday ab.")
        try:
            von, bis = date.fromisoformat(ticket.get("gueltig_von") or ""), date.fromisoformat(ticket.get("gueltig_bis") or "")
            if (bis - von).days > 31:
                probleme.append(f"Das Ticket gilt laut Friday vom {von:%d.%m.%Y} bis {bis:%d.%m.%Y} — länger als ein Monat. "
                                "Ist das wirklich die Monatskarte und nicht die Kundenkarte? Bitte die Gültigkeit prüfen.")
        except ValueError:
            pass

    # Dieselbe Person kann nicht zur selben Zeit in zwei Fahrzeugen sitzen — ein zweiter Antrag heißt: falsche Person
    # oder dieselbe Fahrt doppelt.
    if ab and an:
        for x in a.antraege(1000, profil):
            if x["id"] == aid or x["status"] == "verworfen":
                continue
            xab, xan = antrag._zeit(x.get("ab")), antrag._zeit(x.get("an"))
            if xab and xan and xab < an and ab < xan:
                probleme.append(f"Zur selben Zeit gibt es schon Antrag #{x['id']} (Linie {x.get('linie')}, "
                                f"{xab:%d.%m. %H:%M}–{xan:%H:%M}) für {person} — niemand sitzt in zwei Fahrzeugen gleichzeitig.")
        # Dieselbe Fahrt als Bahn-Fahrgastrecht dieser Person (Garantie ODER Fahrgastrechte, Bedingungen der ÜSTRA)?
        from kern.garantie import bahnablage
        if e.get("reise") and (fall := bahnablage.fall_zur_reise(e["reise"], profil)):
            probleme.append(f"Diese Fahrt ist schon Bahn-Fahrgastrecht (Fall #{fall['id']}) — ÜSTRA-Garantie ODER "
                            "Fahrgastrechte, nie beides.")
        # Schon als Fall bei der ÜSTRA (z. B. selbst auf der Webseite eingereicht)?
        for v in a.vorgaenge(10000, profil):
            if (v.get("antrag") != aid and v.get("fahrtdatum") == ab.date().isoformat() and _uhrzeit(v.get("ab")) == (ab.hour, ab.minute)
                    and gleiche_linie(e.get("linie") or "", v.get("linie") or "")):
                probleme.append(f"Diese Fahrt liegt schon als Fall {v['vorgang']} bei der ÜSTRA — sie wird nur einmal erstattet.")
    return probleme


def _uhrzeit(text: str | None) -> tuple[int, int] | None:
    """„9:05“ und „09:05“ sind dieselbe Zeit (die Mails der ÜSTRA schreiben die Stunde ohne führende Null)."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (text or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def _tag(iso: str | None) -> str:
    try:
        return date.fromisoformat(iso or "").strftime("%d.%m.%Y")
    except ValueError:
        return "?"


def offen_hinweise(an: datetime, profil: int) -> list[str]:
    """Nur zur Warnung in der Rückfrage: Warten schon Anträge auf einen Bescheid, kann dieser über die Höchstzahl rutschen,
    sobald sie genehmigt werden. Gesperrt wird nicht — für die Grenzen zählt nur Genehmigtes (Eike, 16.09.2026)."""
    genehmigt = antrag._genehmigt(profil=profil)
    offen = antrag.offene_fahrten(profil)
    am_tag = sum(x[0] == an.date() for x in genehmigt) + sum(t == an.date() for t in offen)
    hinweise = []
    if sum(t == an.date() for t in offen) and am_tag >= antrag.PRO_TAG:
        hinweise.append(f"⚠ Für den {an:%d.%m.} sind schon {am_tag} Fahrten genehmigt oder abgeschickt — werden die offenen "
                        f"genehmigt, liegt dieser Antrag über der Höchstzahl ({antrag.PRO_TAG} am Tag).")
    im_monat = [t for t in offen if (t.year, t.month) == (an.year, an.month)]
    genehmigt_monat = sum((x[0].year, x[0].month) == (an.year, an.month) for x in genehmigt)
    if im_monat and genehmigt_monat + len(im_monat) >= antrag.PRO_MONAT:
        hinweise.append(f"⚠ Im {antrag.MONATE[an.month - 1]} sind schon {genehmigt_monat + len(im_monat)} Fahrten genehmigt oder "
                        f"abgeschickt — mehr als {antrag.PRO_MONAT} erstattet die ÜSTRA nicht.")
    return hinweise


async def pruefen(aid: int) -> dict:
    """Alles durchgehen. {"ok", "probleme", "punkte", "_bauteile"} — `_bauteile` nur intern."""
    a = ablage.standard()
    probleme, punkte = [], []
    if not konnektoren.benutzbar(KONNEKTOR):
        probleme.append("Die Freigabe „ÜSTRA-Garantie“ ist aus.")
    if not konnektoren.schreiben_erlaubt(KONNEKTOR):
        probleme.append("„Anträge abschicken“ ist in den Freigaben (ÜSTRA-Garantie) noch aus.")
    roh = a.antrag(aid, mit_daten=True)
    if not roh:
        return {"ok": False, "probleme": ["Diesen Antrag gibt es nicht."], "punkte": []}
    e = antrag.bewerten({**roh, "daten": None})
    if e["status"] != "offen" or e.get("vorgang"):
        return {"ok": False, "probleme": ["Dieser Antrag ist schon abgeschickt oder verworfen."], "punkte": []}
    if e["stand"] != "bereit":
        probleme.append(f"Der Antrag ist nicht bereit (Stand: {e['stand']})."
                        + (f" Fehlt: {', '.join(e['fehlt'])}." if e.get("fehlt") else ""))
    ab, an = antrag._zeit(e["ab"]), antrag._zeit(e["an"])
    profil = int(roh.get("profil") or 0)
    person = a.profil_name(profil)
    wessen = "deinen Angaben" if not profil else f"den Angaben von {person}"
    if profil:
        p = a.profil(profil)
        if not p:
            probleme.append("Das Profil dieses Antrags gibt es nicht mehr.")
        else:
            if p.get("archiviert"):
                probleme.append(f"Das Profil {person} ist archiviert.")
            if not p.get("vollmacht"):
                probleme.append(f"{person} hat noch nicht zugestimmt, dass du für sie oder ihn einreichst — "
                                "setz im Profil das Häkchen, wenn es so ist.")
        punkte.append(f"ANTRAG FÜR {person.upper()} — nicht für dich selbst")
    probleme += antrag._grenzen(an, ausser=aid, profil=profil)
    probleme += antrag.hoechstzahl_mit_offenen(an, ausser=aid, profil=profil)
    andere = a.antrag_zur_reise(e["reise"], profil) if e.get("reise") else None
    if andere and andere["id"] != aid:
        probleme.append(f"Für diese Fahrt gibt es schon Antrag #{andere['id']}.")
    verspaetung = e.get("verspaetung")
    # Gemessen heißt: vom Formular selbst oder aus der Echtzeit der GVH-Auskunft nach der Ankunft.
    # Das Formular zeigt die echte Ankunft oft gar nicht (gemessen 16.09., Linie 320).
    gemessen = (e.get("quelle") or "").startswith(("vom GVH gemessen", "Echtzeit der GVH-Auskunft"))
    if verspaetung is None or verspaetung <= antrag.MINDEST or not gemessen:
        probleme.append("Es fehlt eine gemessene Verspätung über 20 Minuten am Ziel.")
    else:
        punkte.append(f"Verspätung am Ziel: {verspaetung} Min ({e['quelle']})")

    # Angaben
    d = a.angaben(profil)
    for feld, text in (("anrede", "Anrede"), ("vorname", "Vorname"), ("nachname", "Nachname"), ("strasse", "Straße"),
                       ("ort", "Ort"), ("kontoinhaber", "Kontoinhaber")):
        if not (d.get(feld) or "").strip():
            probleme.append(f"In {wessen} fehlt: {text}.")
    if d.get("anrede") and d["anrede"] not in ANREDEN:
        probleme.append("Die Anrede muss Herr, Frau oder Divers sein.")
    if not EMAIL.match(d.get("email") or ""):
        probleme.append(f"Die E-Mail-Adresse in {wessen} sieht nicht richtig aus.")
    elif profil:
        # Jede Person braucht ihre EIGENE Adresse (bei Gmail: name+lea@gmail.com) — sonst lassen sich die Bescheide
        # nicht zuordnen und Eikes eigene Adresse landet im Antrag eines Freundes.
        meine = d["email"].strip().casefold()
        andere_adressen = [("deine eigene", a.angaben(0).get("email"))] + [
            (f"die von {p['name']}", p.get("email")) for p in a.profile() if p["id"] != profil]
        for wer, adresse in andere_adressen:
            if adresse and adresse.strip().casefold() == meine:
                probleme.append(f"Die E-Mail-Adresse von {person} ist {wer} — jede Person braucht ihre eigene. Bei Gmail "
                                "geht ein Alias mit Plus, z. B. deinname+lea@gmail.com; die Bescheide kommen trotzdem bei dir an.")
                break
    if not re.fullmatch(r"\d{5}", d.get("plz") or ""):
        probleme.append("Die PLZ muss fünf Ziffern haben.")
    if not ablage.iban_gueltig(d.get("iban") or ""):
        probleme.append(f"Die IBAN in {wessen if not profil else 'der Auszahlung'} fehlt oder stimmt nicht (Prüfsumme).")
    if not probleme:
        punkte.append(f"{d['anrede']} {d['vorname']} {d['nachname']}, {d['strasse']}, {d['plz']} {d['ort']}, {d['email']}")
        konto = f"Konto: {d['kontoinhaber']}, IBAN {d['iban'][:2]}•• … {d['iban'][-4:]}"
        if profil:
            konto += " (dein Konto)" if a.profil(profil).get("auszahlung", "ich") == "ich" else f" (Konto von {person})"
        punkte.append(konto)

    # Ticket
    ticket = a.ticket_fuer(an.date(), profil) if an else None
    detail = None
    if not ticket:
        probleme.append("Für den Fahrtag gibt es kein hochgeladenes, gültiges Ticket"
                        + ("." if not profil else f" von {person}."))
    else:
        probleme += widersprueche(aid, ticket)
        sorte = ticketlesen.sorte(ticket)
        if sorte:
            for feld, name in (("kundenkarte", "die Kundenkartennummer"), ("ticketnummer", "die Nummer des Monatstickets")):
                if not (ticket.get(feld) or d.get(feld)):
                    probleme.append(f"Für die {sorte['name']} verlangt das Formular {name} — sie fehlt noch.")
        elif ticket.get("art") != "Deutschlandticket":
            probleme.append(f"Automatisch abschicken geht bisher nur mit dem Deutschlandticket, nicht mit "
                            f"„{ticket.get('art')}“ — bitte auf der Webseite einreichen.")
        if ticket.get("zweifel") and not ticket.get("korrigiert"):
            probleme.append(f"Friday war beim Ticket unsicher ({ticket['zweifel']}). Prüfe es unter „Erkanntes "
                            "korrigieren“ und drück „Übernehmen“.")
        probleme += antrag._ticket_hindernisse(ticket, profil)    # u. a. nicht von der ÜSTRA ausgegeben
        detail = sorte["detail"] if sorte else ticket_detail(ticket)
        if not detail:
            probleme.append(f"Aus „{ticket.get('bezeichnung') or ticket.get('art')}“ geht nicht eindeutig hervor, "
                            "welches Deutschlandticket es ist.")
        datei = a.ordner / "garantie" / "tickets" / str(ticket.get("datei") or "")
        if not ticket.get("datei") or not datei.is_file():
            probleme.append("Die Ticketdatei fehlt auf dem Server — bitte neu hochladen.")
        if detail:
            punkte.append(f"Fahrkarte: {detail}, gültig {ticket['gueltig_von']} bis {ticket['gueltig_bis']}")

    daten = roh.get("daten") or {}
    if not (daten.get("start") and daten.get("ziel")):
        probleme.append("Start oder Ziel sind im Formular nicht zugeordnet.")
    if probleme:
        return {"ok": False, "probleme": probleme, "punkte": punkte}

    # Live beim Formular: Fahrt und Fahrkarte — welche Art, entscheidet das Ticket.
    art = sorte["art"] if sorte else "Deutschlandticket"
    typ = sorte["typ"] if sorte else "Deutschlandticket"
    try:
        fahrt, leg = await antrag._im_formular(daten["start"], daten["ziel"], ab, e["linie"])
        if not leg:
            probleme.append("Das Formular bietet die Fahrt gerade nicht an.")
        elif leg.get("selectable") is False:
            probleme.append("Im Formular ist diese Fahrt nicht auswählbar.")
        else:
            gemessen = antrag._zeit(leg.get("realToDateTime"))
            plan = antrag._zeit(leg.get("scheduledToDateTime"))
            if gemessen and plan and round((gemessen - plan).total_seconds() / 60) <= antrag.MINDEST:
                probleme.append("Das Formular misst am Ziel höchstens 20 Minuten Verspätung.")
            punkte.append(f"Fahrt: Linie {leg.get('lineName')} {leg.get('fromStationName')} → "
                          f"{leg.get('toStationName')}, ab {ab:%d.%m. %H:%M}")
        typen = await formular.fahrkarten_typen(ab, art)
        details = await formular.fahrkarten_details(ab, art, typ) if typ in typen else []
        if typ not in typen or detail not in details:
            probleme.append(f"Das Formular kennt „{detail}“ gerade nicht.")
            erstattung = None
        else:
            arten = [x for x in await formular.erstattungsarten(ab, art, typ, detail)
                     if x.get("ticketDetails") == detail and x.get("refundIdSignature")]
            erstattung = arten[0] if len(arten) == 1 else None
            if not erstattung:
                probleme.append("Die Fahrkarte lässt sich im Formular nicht eindeutig zuordnen.")
            else:
                if any(erstattung.get(k) for k in NICHT_UNTERSTUETZT):
                    probleme.append("Das Formular verlangt für diese Fahrkarte Angaben, die Friday nicht kennt.")
                if erstattung.get("subscriptionCustomerNumberAvailable"):
                    if not re.fullmatch(r"\d{4,12}", d.get("abo_nummer") or ""):
                        probleme.append("Das Formular verlangt die Abo-Kundennummer — trag sie in deinen Angaben ein "
                                        "oder lade das Ticket neu hoch.")
                    else:
                        punkte.append(f"Abo-Kundennummer: {d['abo_nummer']}")
                # Kundenkarte und Ticketnummer: genau das, was die Fahrkarte laut Formular verlangt.
                if erstattung.get("customerCardFormat") is not None:
                    karte = ticket.get("kundenkarte") or d.get("kundenkarte") or ""
                    if not re.fullmatch(r"\d{%d}" % erstattung["customerCardFormat"], karte):
                        probleme.append(f"Das Formular verlangt die Kundenkartennummer mit "
                                        f"{erstattung['customerCardFormat']} Ziffern.")
                    else:
                        punkte.append(f"Kundenkarte: {karte}")
                if erstattung.get("ticketNumberAvailable"):
                    nummer = ticket.get("ticketnummer") or ""
                    if not ticketlesen.TICKETNUMMER.match(nummer):
                        probleme.append("Das Formular verlangt die Nummer des Monatstickets — sie fehlt oder passt nicht.")
                    else:
                        punkte.append(f"Ticketnummer: {nummer}")
    except formular.GarantieFehler as exc:
        probleme.append(f"Das Formular ist gerade nicht erreichbar ({exc}).")
        fahrt = leg = erstattung = None
    if probleme:
        return {"ok": False, "probleme": probleme, "punkte": punkte}
    punkte += offen_hinweise(an, profil)
    return {"ok": True, "probleme": [], "punkte": punkte,
            "_bauteile": {"e": e, "profil": profil, "angaben": d, "ticket": ticket, "detail": detail, "typ": typ, "fahrt": fahrt,
                          "leg_index": fahrt["legs"].index(leg), "ab": ab, "erstattung": erstattung, "art": art,
                          "datei": a.ordner / "garantie" / "tickets" / ticket["datei"]}}


def eingang_merken(aid: int, antwort: dict, zeit: str) -> None:
    """Die Eingangsbestätigung des Formulars sichern und den Fall gleich anlegen — die
    Eingangsmail kommt oft erst später und wird dann über die Vorgangsnummer zugeordnet."""
    from kern.garantie import post
    a = ablage.standard()
    e = a.antrag(aid, mit_daten=True)
    nummer = re.sub(r"\D", "", str(antwort.get("id") or ""))
    text = " ".join(post.klartext(antwort.get("bestaetigung") or "").split())[:3000]
    gesendet = {"zeit": zeit, "id": nummer, "bestaetigung": text}
    a.antrag_aendern(aid, status="abgeschickt", abgeschickt_am=zeit, fahrkarte=e.get("fahrkarte") or "Deutschlandticket",
                     vorgang=nummer or None, entscheidung="eingang" if nummer else None,
                     daten={**(e["daten"] or {}), "gesendet": gesendet})
    if nummer and not a.vorgang(nummer):
        ab, an = antrag._zeit(e["ab"]), antrag._zeit(e["an"])
        ticket = a.ticket_fuer(an.date(), int(e.get("profil") or 0)) if an else None
        a.vorgang_speichern({
            "vorgang": nummer, "fahrtdatum": ab.date().isoformat() if ab else "", "linie": e["linie"],
            "start": e["start"], "ziel": e["ziel"], "ab": f"{ab:%H:%M}" if ab else "",
            "an": f"{an:%H:%M}" if an else "",
            "fahrkarte": ", ".join(x for x in ((ticket or {}).get("art"), ticket_detail(ticket or {})) if x),
            "status": "eingang", "bearbeiter": "ÜSTRA", "eingang_am": zeit, "letzte_mail": zeit,
            "antrag": aid, "gemeldet": "eingang", "mails": 0})


async def abschicken(aid: int) -> dict:
    """Prüfen, Ticket hochladen, Antrag senden. Nie doppelt, nie automatisch wiederholt."""
    async with _sperre:
        if aid in _laeuft:
            return {"ok": False, "text": "Dieser Antrag wird gerade schon abgeschickt."}
        _laeuft.add(aid)
    try:
        geoeffnet = config.jetzt() - timedelta(minutes=2)
        p = await pruefen(aid)
        if not p["ok"]:
            return {"ok": False, "text": "Nicht abgeschickt: " + " ".join(p["probleme"]), "probleme": p["probleme"]}
        b = p["_bauteile"]
        a = ablage.standard()
        t = b["ticket"]
        anhang = await formular.hochladen(f"ticket.{t['datei'].rsplit('.', 1)[-1]}", t["mime"], b["datei"].read_bytes())
        anfrage = anfrage_bauen(angaben=b["angaben"], fahrt=b["fahrt"], leg_index=b["leg_index"], ab=b["ab"],
                                verspaetung=b["e"]["verspaetung"], ticket=t, detail=b["detail"], typ=b["typ"],
                                erstattung=b["erstattung"], anhang=anhang, geoeffnet=geoeffnet, art=b["art"])
        jetzt = config.jetzt().isoformat(timespec="minutes")
        daten = a.antrag(aid, mit_daten=True)["daten"] or {}
        try:
            antwort = await formular.einreichen(anfrage)
        except formular.UnklarerAusgang as exc:
            # Vielleicht angekommen: als abgeschickt zählen, die Eingangsmail klärt es.
            a.antrag_aendern(aid, status="abgeschickt", abgeschickt_am=jetzt, fahrkarte=t["art"],
                             hinweise=b["e"]["hinweise"] + [f"Antwort unklar ({exc}) — Friday wartet auf die "
                                                            "Eingangsmail der ÜSTRA. Nicht noch einmal abschicken."],
                             daten={**daten, "gesendet": {"zeit": jetzt, "unklar": True}})
            log.warning("Garantie-Antrag %s: Ausgang unklar (%s)", aid, exc)
            return {"ok": False, "text": f"Die ÜSTRA hat nicht klar geantwortet ({exc}). Der Antrag zählt vorsichtshalber "
                                         "als abgeschickt; die Eingangsmail zeigt, ob er ankam. Bitte NICHT noch "
                                         "einmal abschicken."}
        if antwort.get("fehler"):
            a.antrag_aendern(aid, daten={**daten, "abgelehnt_vom_formular": {"zeit": jetzt, "fehler": antwort["fehler"]}})
            return {"ok": False, "text": "Das Formular hat den Antrag nicht angenommen: " + " ".join(antwort["fehler"]),
                    "probleme": antwort["fehler"]}
        eingang_merken(aid, antwort, jetzt)
        log.info("Garantie-Antrag %s abgeschickt", aid)
        return {"ok": True, "text": f"Abgeschickt. Die ÜSTRA hat den Antrag angenommen (Nr. {antwort['id']}); die "
                                    "Eingangsmail kommt per Gmail.", "antrag": antrag.bewerten(a.antrag(aid))}
    except formular.GarantieFehler as exc:
        return {"ok": False, "text": f"Nicht abgeschickt: {exc}"}
    finally:
        _laeuft.discard(aid)


def abtippen(aid: int) -> dict | None:
    """Alle Angaben eines Antrags, so sortiert, wie das Formular der ÜSTRA sie fragt — zum Abtippen, wenn Eike ihn selbst
    auf der Webseite einreicht (Jugendnetzkarte & Co., wo Friday nicht selbst abschickt). Die IBAN kommt nie im Klartext."""
    a = ablage.standard()
    e = a.antrag(aid)
    if not e:
        return None
    profil = int(e.get("profil") or 0)
    d = a.angaben(profil)
    ab, an = antrag._zeit(e["ab"]), antrag._zeit(e["an"])
    ticket = (a.ticket_fuer(an.date(), profil) if an else None) or {}
    sorte = ticketlesen.sorte(ticket)
    datum = lambda iso: f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}" if iso else ""
    iban = d.get("iban") or ""
    fahrkarte = [["Art der Fahrkarte", (sorte or {}).get("art") or ticket.get("art") or ""]]
    if sorte:
        fahrkarte += [["Typ", sorte["typ"]], ["Genauer Name", sorte["detail"]]]
    else:
        detail = ticket_detail(ticket) if ticket else None
        if detail:
            fahrkarte += [["Typ", "Deutschlandticket"], ["Genauer Name", detail]]
    fahrkarte += [["Gültig bis", datum(ticket.get("gueltig_bis") or "")]]
    if sorte:
        fahrkarte += [["Kundenkartennummer", ticket.get("kundenkarte") or d.get("kundenkarte") or "— fehlt —"],
                      ["Ticketnummer", ticket.get("ticketnummer") or "— fehlt —"]]
        gedruckt = ticket.get("nummer_gedruckt") or ""
        if gedruckt and gedruckt != ticket.get("ticketnummer"):
            fahrkarte += [["So steht sie auf dem Ticket (das Formular nimmt nur Buchstaben und Ziffern)", gedruckt]]
    elif d.get("abo_nummer"):
        fahrkarte += [["Abo-Kundennummer", d["abo_nummer"]]]
    # Auch von Hand soll nichts Widersprüchliches rausgehen: dieselben Prüfungen wie vor dem Abschicken, als Warnung oben.
    return {"person": a.profil_name(profil), "warnungen": widersprueche(aid, ticket), "gruppen": [
        {"titel": "Fahrt", "zeilen": [
            ["Datum der Fahrt", f"{ab:%d.%m.%Y}" if ab else ""], ["Linie", e.get("linie") or ""],
            ["Von", e.get("start") or ""], ["Nach", e.get("ziel") or ""],
            ["Abfahrt (Plan)", f"{ab:%H:%M}" if ab else ""], ["Ankunft (Plan)", f"{an:%H:%M}" if an else ""],
            ["Verspätung am Ziel (Min)", str(e["verspaetung"]) if e.get("verspaetung") is not None else ""]]},
        {"titel": "Person", "zeilen": [
            ["Anrede", d.get("anrede") or ""], ["Vorname", d.get("vorname") or ""], ["Nachname", d.get("nachname") or ""],
            ["Straße und Hausnummer", d.get("strasse") or ""], ["PLZ", d.get("plz") or ""], ["Ort", d.get("ort") or ""],
            ["E-Mail", d.get("email") or ""]]},
        {"titel": "Fahrkarte", "zeilen": fahrkarte},
        {"titel": "Konto für die Auszahlung", "zeilen": [
            ["Kontoinhaber", d.get("kontoinhaber") or ""],
            ["IBAN (nur die Enden — die volle kennst du oder steht in deinen Unterlagen)",
             f"{iban[:2]}•• … {iban[-4:]}" if iban else "— fehlt —"]]}]}
