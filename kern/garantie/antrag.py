"""Einen Antrag vorbereiten — alles bis vor das Abschicken.

Eike wählt eine verspätete Fahrt, sieht ihren Verlauf mit allen Halten und tippt an,
wo er ein- und ausgestiegen ist. Friday sucht dieselbe Fahrt im Garantie-Formular
und nimmt dessen GEMESSENE Ankunft; fehlt sie, die Echtzeit der GVH-Auskunft.

Die Bedingungen (ÜSTRA, Stand 01.06.2026) und was das Formular erzwingt:
- gilt für ALLE Fahrten mit ÜSTRA-Fahrausweis — auch S-Bahn (das Formular bietet
  S1, S2, S5, S21 an, gemessen 15.09.). Ausgenommen: nicht von der ÜSTRA
  ausgegebene Deutschlandtickets, Deutschlandticket Job 100, Schulfahrkarte,
  ÜSTRA easy, sprinti;
- mehr als 20 Minuten am FAHRZIEL — nicht irgendwo unterwegs;
- erst nach der Ankunft: „Die angegebene Verspätung liegt in der Zukunft.“ lehnt
  das Formular vorher ab, und seine Fahrtsuche findet künftige Fahrten gar nicht.
  Ein Antrag für ein Ziel in der Zukunft darf vorbereitet werden, steht aber auf
  „wartet“, bis Ankunft plus Verspätung vorbei ist — dann prüft Friday mit
  Messung und Formular nach (`nachpruefen`);
- binnen 14 Tagen; höchstens 2 Fahrten am Tag, 10 im Monat; mit dem
  Deutschlandticket höchstens 5 im Monat; eine Fahrt nur einmal;
- das Ticket muss am Fahrtag gegolten haben — es zählt das hochgeladene Ticket,
  dessen Gültigkeit Friday ausgelesen hat (`ticket.py`).

Abgeschickt wird hier NICHTS. „abgeschickt“ setzt nur Eike selbst, solange er auf
der Webseite einreicht — damit die Zähler stimmen.

Profile (20.09.2026): Eike reicht auch für Freunde ein. Ein Antrag gehört einem Profil
(`profil`, 0 = Eike). Ticket, Angaben und alle Höchstzahlen gelten je Person — zwei Freunde
im selben verspäteten Bus haben je einen Antrag und je ihre eigenen Zähler. Wo `profil`
fehlt, gilt Eike.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

from kern import config
from kern.dienste import garantie as formular
from kern.dienste import gvh
from kern.dienste.uestra import gleiche_linie
from kern.garantie import ablage
from kern.garantie.ticket import fehlende_felder, ist_uestra, muss_verglichen_werden

log = logging.getLogger("friday.garantie")

MINDEST = 20
FRIST_TAGE = 14
PRO_TAG = 2
PRO_MONAT = 10
DTICKET_MONAT = 5
NACHPRUEFEN_NACH = timedelta(minutes=5)
ERSTATTUNG_CENT = 500          # je Fahrt (uestra.de) — nur für die Schätzung „noch frei“ bei der Preisgrenze
FORMULAR_SUCHEN = 3
MONATE = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
          "November", "Dezember")
AUSGESCHLOSSEN = re.compile(r"job\s*100|schul|\beasy\b", re.I)
IM_FORMULAR_SPAETER = "Das Formular zeigt Fahrten erst, wenn sie vorbei sind — Friday sucht sie nach der Ankunft."


def _zeit(wert) -> datetime | None:
    if isinstance(wert, datetime):
        return wert
    try:
        return datetime.fromisoformat(str(wert).replace("Z", "+00:00")).astimezone(config.jetzt().tzinfo) if wert else None
    except ValueError:
        return None


def _iso(wert) -> str | None:
    return wert.isoformat() if isinstance(wert, datetime) else wert


def verlauf_oeffentlich(v: dict) -> dict:
    return {**v, "jid": None, "halte": [{k: _iso(w) for k, w in h.items()} for h in v["halte"]]}


ZEITEN = ("an_plan", "an_echt", "ab_plan", "ab_echt")


def _halte_laden(liste: list[dict]) -> list[dict]:
    return [{**h, **{k: _zeit(h.get(k)) for k in ZEITEN}} for h in liste]


def _mit_gespeichertem(rid: str, v: dict) -> dict:
    """Echtzeit, die die Auskunft inzwischen vergessen hat, aus dem gespeicherten Verlauf
    ergänzen — und was jetzt gemessen ist, für später sichern."""
    a = ablage.standard()
    alt = a.gespeicherter_verlauf(rid)
    if alt:
        je_halt = {(h.get("reference"), h.get("an_plan") or h.get("ab_plan")): h for h in _halte_laden(alt)}
        for h in v["halte"]:
            if h.get("an_echt") or h.get("ab_echt"):
                continue
            s = je_halt.get((h.get("reference"), h.get("an_plan") or h.get("ab_plan")))
            if s and (s.get("an_echt") or s.get("ab_echt")):
                h.update(an_echt=s["an_echt"], ab_echt=s["ab_echt"], verspaetung=s.get("verspaetung"),
                         faellt_aus=bool(s.get("faellt_aus")), gespeichert=True)
    if any(h.get("an_echt") or h.get("ab_echt") for h in v["halte"]):
        a.verlauf_merken(rid, [{k: _iso(w) for k, w in h.items()} for h in v["halte"]])
    return v


async def verlauf(reise_id: str) -> dict:
    a = ablage.standard()
    reise = a.reise(reise_id)
    if not reise:
        raise LookupError("Diese Fahrt kenne ich nicht (mehr).")
    try:
        v = await gvh.fahrt_verlauf(reise["jid"])
    except gvh.GvhFehler:
        alt = a.gespeicherter_verlauf(reise_id)
        if not alt:
            raise
        # Die Auskunft kennt die Fahrt nicht mehr — der gespeicherte Verlauf genügt.
        v = {"jid": reise["jid"], "linie": reise["linie"], "kategorie": reise.get("kategorie") or "",
             "richtung": reise.get("richtung") or "", "halte": _halte_laden(alt)}
    v = _mit_gespeichertem(reise_id, v)
    v["id"] = reise_id
    return v


# ---------- Messen: im Formular, sonst Echtzeit der Auskunft ----------

async def _formular_halt(h: dict) -> dict | None:
    """Die Haltestelle, wie das Formular sie kennt — über die Kennung, nie über den Namen
    allein („Bennigsen“ ergab dort zuerst „Alt Bennebek“)."""
    for suche in dict.fromkeys((h["name"], h["name"].split()[0])):
        for t in await formular.stationen(suche):
            if t["reference"] == h["reference"]:
                return t
    return None


def halt_passt(name_formular: str, name_gesucht: str) -> bool:
    """Dieselbe Haltestelle? Die Auskünfte schreiben sie verschieden („Hannover, Kröpcke“, „Hannover Kröpcke“,
    „Hannover/Kröpcke“). Die Kennung gibt das Formular in den Teilstrecken nicht mit (`toStationReference` ist
    leer, gemessen 20.09.2026), also über die tragenden Wörter — eine Menge muss die andere enthalten."""
    a, b = gvh._worte(name_formular), gvh._worte(name_gesucht)
    return bool(a) and bool(b) and (a <= b or b <= a)


async def _im_formular(start: dict, ziel: dict, ab: datetime, linie: str) -> tuple[dict | None, dict | None]:
    """Die EINE Teilstrecke, in der Eike saß: richtige Linie, richtige Abfahrtszeit — und sie muss an SEINEM
    Ziel enden. Ohne die letzte Prüfung nahm der Code am 20.09.2026 für „Ahlem → Kröpcke“ die Teilstrecke
    „Ahlem → Bernhard-Caspar-Straße“ einer Umsteigeverbindung, obwohl das Formular den Bus auch durchgehend
    anbot — der Antrag hätte eine andere Strecke genannt als die, die Eike gewählt hat. Findet sich nichts
    Passendes, kommt lieber nichts zurück (dann gibt es ein Hindernis, keinen falschen Antrag)."""
    zeit = ab - timedelta(minutes=1)
    for _ in range(FORMULAR_SUCHEN):
        liste = await formular.fahrten(start, ziel, zeit)
        if not liste:
            break
        spaetester = zeit
        for f in liste:
            for leg in f.get("legs") or []:
                von = _zeit(leg.get("scheduledFromDateTime"))
                if von and von > spaetester:
                    spaetester = von
                if (von and abs((von - ab).total_seconds()) <= 60
                        and gleiche_linie(linie, str(leg.get("lineName") or ""))
                        and halt_passt(str(leg.get("fromStationName") or ""), start["name"])
                        and halt_passt(str(leg.get("toStationName") or ""), ziel["name"])):
                    return f, leg
        if spaetester <= zeit or spaetester > ab + timedelta(minutes=30):
            break
        zeit = spaetester + timedelta(minutes=1)
    return None, None


async def _messen(linie: str, s: dict, z: dict, ab: datetime, an: datetime) -> dict:
    m = {"echt": None, "quelle": "keine Messung", "fahrt": None, "im_formular": False, "hinweise": [],
         "start": None, "ziel": None, "waehlbar": True}
    try:
        m["start"], m["ziel"] = await _formular_halt(s), await _formular_halt(z)
        if m["start"] and m["ziel"]:
            m["fahrt"], leg = await _im_formular(m["start"], m["ziel"], ab, linie)
            if leg:
                m["im_formular"], m["waehlbar"] = True, leg.get("selectable") is not False
                if echt := _zeit(leg.get("realToDateTime")):
                    m["echt"], m["quelle"] = echt, "vom GVH gemessen"
    except formular.GarantieFehler as exc:
        m["hinweise"].append(f"Das Garantie-Formular war nicht erreichbar ({exc}).")
    if not m["echt"] and z.get("an_echt"):
        m["echt"] = _zeit(z["an_echt"])
        m["quelle"] = "Echtzeit der GVH-Auskunft" + (" (gespeichert)" if z.get("gespeichert") else "")
    return m


# ---------- Regeln ----------

def _genehmigt(ausser: int | None = None, profil: int = 0) -> list[tuple]:
    """Die GENEHMIGTEN Fahrten als (Fahrtag, Fahrkarte) — nur sie zählen für die
    Höchstzahlen (Eike, 16.09.2026). Quelle sind die Bescheide aus Gmail, auch für
    Anträge, die Eike früher selbst gestellt hat."""
    liste = []
    for v in ablage.standard().vorgaenge(10000, profil):
        if v.get("status") == "genehmigt" and v.get("fahrtdatum") and (ausser is None or v.get("antrag") != ausser):
            liste.append((datetime.fromisoformat(v["fahrtdatum"]).date(),
                          "Deutschlandticket" if "deutschlandticket" in (v.get("fahrkarte") or "").casefold() else "",
                          int(v.get("cent") or 0)))
    return liste


def offene_fahrten(profil: int = 0) -> list:
    """Die Fahrtage der Anträge, die abgeschickt, aber noch nicht entschieden sind — sie zählen (noch) nicht für die
    Höchstzahlen, können aber dazukommen. Ein Antrag mit Vorgangsnummer steht als Fall in `vorgaenge` und zählt dort."""
    a = ablage.standard()
    from kern.garantie import post
    faelle = a.vorgaenge(10000, profil)
    mit_fall = {v.get("antrag") for v in faelle if v.get("antrag")}
    tage = [_zeit(e["an"]).date() for e in a.antraege(1000, profil)
            if e["status"] == "abgeschickt" and not e.get("vorgang") and e["id"] not in mit_fall and _zeit(e.get("an"))]
    for v in faelle:
        if post.anzeige_stand(v) in ("eingang", "rueckfrage") and v.get("fahrtdatum"):
            try:
                tage.append(datetime.fromisoformat(v["fahrtdatum"]).date())
            except ValueError:
                pass
    return tage


def _offen(profil: int = 0) -> int:
    """Abgeschickt, aber noch ohne Entscheidung — zählt (noch) nicht, kann aber dazukommen."""
    a = ablage.standard()
    from kern.garantie import post
    faelle = a.vorgaenge(10000, profil)
    mit_fall = {v.get("antrag") for v in faelle if v.get("antrag")}
    ohne_fall = sum(1 for e in a.antraege(1000, profil)
                    if e["status"] == "abgeschickt" and not e.get("vorgang") and e["id"] not in mit_fall)
    return ohne_fall + sum(1 for v in faelle if post.anzeige_stand(v) in ("eingang", "rueckfrage"))


def _dticket_am(tag, profil: int = 0) -> bool:
    return (ablage.standard().ticket_fuer(tag, profil) or {}).get("art") == "Deutschlandticket"


def zaehler(profil: int = 0) -> dict:
    """Wie viele Fahrten genehmigt sind — nach Fahrtag, so zählen die Bedingungen. Je Person."""
    jetzt = config.jetzt()
    genehmigt = _genehmigt(profil=profil)
    monat = [x for x in genehmigt if (x[0].year, x[0].month) == (jetzt.year, jetzt.month)]
    stand = [bewerten(e)["stand"] for e in ablage.standard().antraege(1000, profil)]
    heute = sum(x[0] == jetzt.date() for x in genehmigt)
    dticket = sum(x[1] == "Deutschlandticket" for x in monat)
    mit_dticket = _dticket_am(jetzt.date(), profil)
    # Was für Eike gerade gilt: mit Deutschlandticket 5 im Monat, sonst 10.
    grenze = min(PRO_MONAT, DTICKET_MONAT) if mit_dticket else PRO_MONAT
    gezaehlt = dticket if mit_dticket else len(monat)
    monat_rest, heute_rest = max(0, min(grenze - gezaehlt, PRO_MONAT - len(monat))), max(0, PRO_TAG - heute)
    # Für die Übersicht (21.09.2026): Was realistisch noch geht, wenn die offenen Anträge genehmigt werden, und was die
    # Preisgrenze der Monatskarte übrig lässt. Gesperrt wird weiter nur über `_limits` (nur Genehmigtes zählt).
    offen_tage = offene_fahrten(profil)
    offen_heute = sum(t == jetzt.date() for t in offen_tage)
    offen_monat = sum((t.year, t.month) == (jetzt.year, jetzt.month) for t in offen_tage)
    karte = ablage.standard().ticket_fuer(jetzt.date(), profil) or {}
    preis_rest = _preis_rest(jetzt, profil, genehmigt)
    frei_monat = max(0, monat_rest - offen_monat)
    # Was von den offenen Anträgen am Kartenpreis nagt, sind nur die Fahrten innerhalb der Gültigkeit dieser Karte.
    offen_ticket = sum(karte["gueltig_von"] <= t.isoformat() <= karte["gueltig_bis"] for t in offen_tage) \
        if karte.get("gueltig_von") and karte.get("gueltig_bis") else 0
    if preis_rest is not None:
        frei_monat = min(frei_monat, max(0, -(-(preis_rest - ERSTATTUNG_CENT * offen_ticket) // ERSTATTUNG_CENT)))
    # Für die Übersicht (02.10.2026): anders als `preis_rest` (nur dieser Kalendermonat, für die Sperre) zählt
    # hier die GANZE Gültigkeit des aktuellen Tickets — sonst zeigt die Übersicht am Monatsanfang „0 €“, obwohl
    # mit demselben Ticket schon erstattet wurde (ein Monats-/Jugendticket reicht oft bis in den nächsten Monat).
    ticket_erstattet_cent = None
    if preis_rest is not None and karte.get("gueltig_von") and karte.get("gueltig_bis"):
        von, bis = karte["gueltig_von"], karte["gueltig_bis"]
        schon_ticket = sum(cent for tag, _, cent in genehmigt if von <= tag.isoformat() <= bis)
        ticket_erstattet_cent = min(schon_ticket, round(karte["preis"] * 100))
        # Was noch auf den Bescheid wartet (Schätzung), nie mehr als der Rest des Kartenpreises.
        ticket_offen_cent = min(ERSTATTUNG_CENT * sum(von <= t.isoformat() <= bis for t in offen_tage),
                                max(0, round(karte["preis"] * 100) - ticket_erstattet_cent))
    else:
        ticket_offen_cent = 0
    return {"ticket_offen_cent": ticket_offen_cent, "monat_erstattet_cent": sum(x[2] for x in monat),"heute": heute, "tag_max": PRO_TAG,
            "monat": len(monat), "monat_max": PRO_MONAT,
            "dticket": dticket, "dticket_max": DTICKET_MONAT, "mit_dticket": mit_dticket,
            "monat_name": MONATE[jetzt.month - 1],
            "monat_rest": monat_rest, "heute_rest": heute_rest,
            "grenze": grenze, "gezaehlt": gezaehlt, "ticket_erstattet_cent": ticket_erstattet_cent,
            "preis_cent": round(karte["preis"] * 100) if preis_rest is not None else None,
            "preis_rest_cent": preis_rest,
            "offen_heute": offen_heute, "offen_monat": offen_monat, "offen_ticket": offen_ticket,
            "frei_monat": frei_monat, "frei_heute": min(frei_monat, max(0, heute_rest - offen_heute)),
            "offen": _offen(profil), "insgesamt": len(genehmigt),
            "wartet": stand.count("wartet"), "bereit": stand.count("bereit"),
            "limit": stand.count("Limit erreicht"),
            "vorbereitet": sum(s in ("bereit", "wartet", "unvollständig", "wird nachgeprüft") for s in stand)}


def _ticket_hindernisse(ticket: dict | None, profil: int = 0) -> list[str]:
    """Was die Bedingungen am Ticket ausschließen — nur, was darauf erkannt wurde."""
    if not ticket:
        return []
    hindernisse = []
    if ticket.get("art") == "Deutschlandticket" and ist_uestra(ticket.get("herausgeber", "")) is False:
        hindernisse.append(f"{'Dein' if not profil else 'Das'} Deutschlandticket hat „{ticket['herausgeber']}“ ausgegeben — die Garantie gilt nur "
                           "für von der ÜSTRA ausgegebene Deutschlandtickets.")
    if AUSGESCHLOSSEN.search(ticket.get("bezeichnung") or ""):
        hindernisse.append(f"„{ticket['bezeichnung']}“ ist von der Garantie ausgenommen (Job 100, Schulfahrkarte, easy).")
    return hindernisse


GRENZ_TEXTE = ("Fahrten genehmigt", "Fahrten abgegeben", "Frist ist vorbei", "höchstens 5 Fahrten",
               "das ist die Höchstzahl", "Kartenpreis")


def _ist_grenze(text: str) -> bool:
    return any(t in text for t in GRENZ_TEXTE)


def _frist(an: datetime) -> list[str]:
    if an.date() < config.jetzt().date() - timedelta(days=FRIST_TAGE):
        return [f"Die Fahrt liegt mehr als {FRIST_TAGE} Tage zurück — die Frist ist vorbei."]
    return []


def _limits(an: datetime, ausser: int | None = None, profil: int = 0) -> list[str]:
    """Die Höchstzahlen — gezählt werden nur genehmigte Fahrten dieser Person."""
    genehmigt = _genehmigt(ausser, profil)
    am_tag = [x for x in genehmigt if x[0] == an.date()]
    im_monat = [x for x in genehmigt if (x[0].year, x[0].month) == (an.year, an.month)]
    monat = MONATE[an.month - 1]
    texte = []
    if len(am_tag) >= PRO_TAG:
        texte.append(f"Für diesen Tag sind schon {PRO_TAG} Fahrten genehmigt — mehr erstattet die Garantie nicht.")
    if len(im_monat) >= PRO_MONAT:
        texte.append(f"Im {monat} sind schon {PRO_MONAT} Fahrten genehmigt — das ist die Höchstzahl.")
    if _dticket_am(an.date(), profil) and sum(x[1] == "Deutschlandticket" for x in im_monat) >= DTICKET_MONAT:
        texte.append(f"Mit dem Deutschlandticket werden höchstens {DTICKET_MONAT} Fahrten im Monat erstattet — "
                     f"im {monat} sind schon {DTICKET_MONAT} Fahrten genehmigt.")
    rest = _preis_rest(an, profil, genehmigt)
    if rest is not None and rest <= 0:
        texte.append(f"Bei Monatskarten wird höchstens der Kartenpreis erstattet — er ist mit den "
                     "genehmigten Fahrten dieser Karte schon ausgeschöpft.")
    return texte


def hoechstzahl_mit_offenen(an: datetime, ausser: int | None = None, profil: int = 0) -> list[str]:
    """HARTE Sperre fürs Abschicken (Eike, 15.10.2026): genehmigte UND schon abgeschickte, noch unentschiedene Fahrten
    zählen zusammen. Sonst gingen am 15.10. sechs Anträge raus, erlaubt sind mit dem Deutschlandticket fünf im Monat.
    Wer abgelehnt wird, schafft wieder Platz — erst dann geht der nächste. Gilt nur beim Abschicken, nicht für die
    Anzeige „bereit“."""
    genehmigt = _genehmigt(ausser, profil)
    offen = offene_fahrten(profil)
    monat = (an.year, an.month)
    im_monat = [x for x in genehmigt if (x[0].year, x[0].month) == monat]
    offen_monat = [t for t in offen if (t.year, t.month) == monat]
    am_tag = sum(x[0] == an.date() for x in genehmigt) + sum(t == an.date() for t in offen)
    name = MONATE[an.month - 1]
    texte = []
    if am_tag >= PRO_TAG:
        texte.append(f"Für diesen Tag sind genehmigte und noch offene Anträge schon {am_tag} — erlaubt sind {PRO_TAG}.")
    grenze = DTICKET_MONAT if _dticket_am(an.date(), profil) else PRO_MONAT
    zaehlt = (sum(x[1] == "Deutschlandticket" for x in im_monat) if grenze == DTICKET_MONAT else len(im_monat)) \
        + len(offen_monat)
    if zaehlt >= grenze:
        texte.append(f"Im {name} sind {zaehlt} Fahrten genehmigt oder noch ohne Bescheid — erlaubt sind {grenze}. "
                     "Erst wenn ein offener Antrag abgelehnt wird, wird wieder Platz.")
    return texte


def _preis_rest(an: datetime, profil: int, genehmigt: list[tuple] | None = None) -> int | None:
    """Was bei einer Monatskarte in diesem Monat noch erstattet werden kann (Cent) — die Erstattung ist auf den gezahlten
    Kartenpreis je Monat begrenzt (ÜSTRA, 20.09.2026). None: keine Monatskarte oder Preis unbekannt (dann gilt nur die
    Zahl der Fahrten). Weniger als 5 € heißt: die nächste Fahrt bringt nur noch den Rest — sie ist möglich, nicht gesperrt."""
    karte = ablage.standard().ticket_fuer(an.date(), profil) or {}
    if karte.get("art") != "Monatskarte" or not karte.get("preis"):
        return None
    genehmigt = _genehmigt(profil=profil) if genehmigt is None else genehmigt
    # Gerechnet wird über die Gültigkeit DIESER Karte (27.10.2026): Eine Karte vom 25.10. bis 24.11. startet bei null,
    # auch wenn im Oktober mit der vorigen schon erstattet wurde. `ticket_fuer` liefert nur Karten mit von und bis.
    schon = sum(x[2] for x in genehmigt if karte["gueltig_von"] <= x[0].isoformat() <= karte["gueltig_bis"])
    return round(karte["preis"] * 100) - schon


def _grenzen(an: datetime, ausser: int | None = None, profil: int = 0) -> list[str]:
    ticket = ablage.standard().ticket_fuer(an.date(), profil)
    return _frist(an) + _limits(an, ausser, profil) + _ticket_hindernisse(ticket, profil)


def bewerten(e: dict) -> dict:
    """Der Stand wird beim Lesen bestimmt: Lädt Eike sein Ticket nach oder ist die
    Ankunft vorbei, ändert er sich ohne neuen Durchlauf."""
    a = ablage.standard()
    profil = int(e.get("profil") or 0)
    faellig, an = _zeit(e.get("faellig_ab")), _zeit(e.get("an"))
    fehlt = a.fehlende_angaben(profil)
    if an and not a.ticket_fuer(an.date(), profil):
        fehlt = fehlt + [f"Ticket für {MONATE[an.month - 1]}"]
    elif an:
        karte = a.ticket_fuer(an.date(), profil)
        fehlt = fehlt + fehlende_felder(karte, a.angaben(profil))                           # z. B. bei der Jugendnetzkarte
        if muss_verglichen_werden(karte):                                                    # Papier: erst Eikes Blick
            fehlt = fehlt + ["Nummern mit dem Ticket vergleichen"]
    # Frist und Höchstzahlen ändern sich, ohne dass der Antrag angefasst wird (ein
    # Bescheid kommt, ein Tag vergeht) — deshalb für offene Anträge jedes Mal neu.
    grenzen = []
    if e["status"] == "offen" and an:
        e = {**e, "hindernisse": [h for h in e["hindernisse"] if not _ist_grenze(h)] + _frist(an)}
        grenzen = _limits(an, e["id"], profil)
    if e["status"] == "abgeschickt":
        # Was die ÜSTRA per Mail geantwortet hat (post.py) — sonst nur „abgeschickt“.
        stand = {"eingang": "eingegangen", "genehmigt": "genehmigt", "abgelehnt": "abgelehnt",
                 "rueckfrage": "Rückfrage"}.get(e.get("entscheidung") or "", "abgeschickt")
    elif e["status"] == "verworfen":
        stand = "verworfen"
    elif e["hindernisse"]:
        stand = "nicht beantragbar"
    elif grenzen:
        stand = "Limit erreicht"
    elif faellig and faellig > config.jetzt():
        stand = "wartet"
    elif not e["nachgeprueft"]:
        stand = "wird nachgeprüft"
    elif fehlt:
        stand = "unvollständig"
    else:
        stand = "bereit"
    hinweise = list(e["hinweise"])
    if e["status"] == "offen" and an and (rest := _preis_rest(an, profil)) is not None and 0 < rest < 500:
        hinweise.append(f"Bei Monatskarten wird höchstens der Kartenpreis je Monat erstattet: für den {MONATE[an.month - 1]} sind noch "
                        f"{rest / 100:.2f} € möglich, mehr bringt diese Fahrt nicht.".replace(".", ",", 1))
    return {**e, "hinweise": hinweise, "hindernisse": e["hindernisse"] + grenzen, "stand": stand,
            "fehlt": fehlt if stand in ("unvollständig", "wartet", "wird nachgeprüft") else []}


# ---------- Vorbereiten und Nachprüfen ----------

def _halte_waehlen(v: dict, start_ref: str, ziel_ref: str) -> tuple[dict, dict]:
    refs = [h["reference"] for h in v["halte"]]
    if start_ref not in refs or ziel_ref not in refs:
        raise ValueError("Diese Haltestelle liegt nicht auf der gewählten Fahrt.")
    i = refs.index(start_ref)
    k = len(refs) - 1 - refs[::-1].index(ziel_ref)
    if i >= k:
        raise ValueError("Das Ziel muss nach dem Start kommen.")
    return v["halte"][i], v["halte"][k]


def _verspaetung(m: dict, an: datetime) -> int | None:
    return round((m["echt"] - an).total_seconds() / 60) if m["echt"] else None


async def vorbereiten(reise_id: str, start_ref: str, ziel_ref: str, profil: int = 0) -> dict:
    a = ablage.standard()
    if profil and not a.profil(profil):
        return {"ok": False, "text": "Dieses Profil gibt es nicht (mehr)."}
    if profil and a.profil(profil).get("archiviert"):
        return {"ok": False, "text": f"{a.profil_name(profil)} ist archiviert — hol das Profil zuerst zurück."}
    wer = "" if not profil else f" für {a.profil_name(profil)}"
    if alt := a.antrag_zur_reise(reise_id, profil):
        return {"ok": False, "text": f"Für diese Fahrt gibt es schon Antrag #{alt['id']}{wer} — eine Fahrt wird nur "
                                     "einmal erstattet, auch bei mehreren Haltestellen."}
    from kern.garantie import bahnablage
    if fall := bahnablage.fall_zur_reise(reise_id, profil):
        return {"ok": False, "text": f"Diese Fahrt ist schon Bahn-Fahrgastrecht (Fall #{fall['id']}){wer} — ÜSTRA-Garantie "
                                     "ODER Fahrgastrechte, nie beides (Bedingungen der ÜSTRA)."}
    try:
        v = await verlauf(reise_id)
        s, z = _halte_waehlen(v, start_ref, ziel_ref)
    except (LookupError, ValueError, gvh.GvhFehler) as exc:
        return {"ok": False, "text": str(exc)}
    ab, an = s["ab_plan"] or s["an_plan"], z["an_plan"] or z["ab_plan"]
    m = await _messen(v["linie"], s, z, ab, an)
    verspaetung = _verspaetung(m, an)
    jetzt = config.jetzt()
    faellig = an + timedelta(minutes=max(verspaetung or 0, 0))

    hindernisse = _grenzen(an, profil=profil)
    hinweise = list(m["hinweise"])
    if not m["hinweise"] and not m["im_formular"]:
        if faellig > jetzt:
            hinweise.append(IM_FORMULAR_SPAETER)
        else:
            hindernisse.append("Das Garantie-Formular bietet diese Fahrt nicht an.")
    if not m["waehlbar"]:
        hindernisse.append("Im Garantie-Formular ist diese Fahrt nicht auswählbar.")
    fertig = faellig + NACHPRUEFEN_NACH <= jetzt and m["quelle"] == "vom GVH gemessen"
    if verspaetung is None:
        hinweise.append("Für das Ziel gibt es noch keine Echtzeit — Friday prüft nach der Ankunft nach.")
    elif verspaetung <= MINDEST:
        if faellig > jetzt:
            hinweise.append(f"Stand jetzt {verspaetung} Min am Ziel — Friday prüft nach der Ankunft nach.")
        else:
            hindernisse.append(f"Am Ziel waren es {verspaetung} Min — der Anspruch beginnt erst über {MINDEST} Minuten.")

    ticket = a.ticket_fuer(an.date(), profil)
    aid = a.antrag_speichern({
        "profil": profil, "reise": reise_id, "jid": v["jid"], "linie": v["linie"], "richtung": v["richtung"],
        "start": s["name"], "ziel": z["name"], "start_ref": start_ref, "ziel_ref": ziel_ref, "ab": ab, "an": an,
        "verspaetung": verspaetung, "quelle": m["quelle"], "faellig_ab": faellig, "hindernisse": hindernisse,
        "hinweise": hinweise, "fahrkarte": (ticket or {}).get("art"), "nachgeprueft": fertig,
        # Die signierte Fahrt, wie das Formular sie zurückschicken müsste — erst beim Abschicken gebraucht.
        "daten": {"fahrt": m["fahrt"], "start": m["start"], "ziel": m["ziel"]}})
    e = bewerten(a.antrag(aid))
    text = (f"Antrag #{aid}{wer}: {v['kategorie']} {v['linie']} von {s['name']} ({ab:%d.%m. %H:%M}) nach {z['name']} "
            f"(planmäßig {an:%H:%M}): "
            + (f"{verspaetung} Min später, {m['quelle']}" if verspaetung is not None else "noch keine Messung")
            + f". Stand: {e['stand']}" + (f" bis {faellig:%H:%M}" if e["stand"] == "wartet" else "") + ".")
    if e["hindernisse"] or e["hinweise"] or e["fehlt"]:
        text += " " + " ".join(e["hindernisse"] + e["hinweise"])
        if e["fehlt"]:
            text += " Fehlt noch: " + ", ".join(e["fehlt"]) + "."
    return {"ok": True, "text": text + " Abgeschickt wird nichts — das entscheidest du.", "antrag": e}


async def nachpruefen() -> int:
    """Nach der Ankunft: mit Messung und Formular neu bewerten. Einmal je Antrag."""
    a = ablage.standard()
    jetzt = config.jetzt()
    geprueft = 0
    for e in a.antraege(200, None):                    # aller Profile
        faellig = _zeit(e["faellig_ab"])
        if e["status"] != "offen" or e["nachgeprueft"] or e["hindernisse"] or (faellig and faellig + NACHPRUEFEN_NACH > jetzt):
            continue
        try:
            v = await verlauf(e["reise"]) if e.get("reise") else await gvh.fahrt_verlauf(e["jid"])
            s, z = _halte_waehlen(v, e["start_ref"], e["ziel_ref"])
            m = await _messen(e["linie"], s, z, _zeit(e["ab"]), _zeit(e["an"]))
        except Exception as exc:
            log.debug("Antrag %s nicht nachprüfbar: %s", e["id"], type(exc).__name__)
            continue
        if m["hinweise"]:
            continue                                   # Formular gerade weg: später noch einmal
        verspaetung = _verspaetung(m, _zeit(e["an"]))
        hindernisse = []
        if not m["im_formular"]:
            hindernisse.append("Das Garantie-Formular bietet diese Fahrt auch nach der Ankunft nicht an.")
        if verspaetung is None:
            hindernisse.append("Auch nach der Ankunft gibt es keine gemessene Verspätung — so lässt sich nichts "
                               "belegen.")
        elif verspaetung <= MINDEST:
            hindernisse.append(f"Am Ziel waren es am Ende {verspaetung} Min — der Anspruch beginnt erst über "
                               f"{MINDEST} Minuten.")
        hinweise = [h for h in e["hinweise"] if "nach der Ankunft" not in h]
        daten = a.antrag(e["id"], mit_daten=True)["daten"]
        if m["fahrt"]:
            daten = {"fahrt": m["fahrt"], "start": m["start"], "ziel": m["ziel"]}
        a.antrag_aendern(e["id"], verspaetung=verspaetung, quelle=m["quelle"], hindernisse=hindernisse,
                         hinweise=hinweise, nachgeprueft=1, daten=daten)
        geprueft += 1
        if not hindernisse:
            from kern.dienste import benachrichtigungen
            wer = f" ({a.profil_name(e['profil'])})" if e.get("profil") else ""
            await asyncio.to_thread(benachrichtigungen.senden, "garantie", "Garantie-Antrag bereit" + wer,
                                    f"Linie {e['linie']} nach {e['ziel']}: {verspaetung} Min — jetzt beantragbar.",
                                    "/#studio", f"garantie-{e['id']}")
    return geprueft


def status_setzen(aid: int, status: str) -> dict:
    """Nur Eike: „abgeschickt“ (er hat auf der Webseite eingereicht), „verworfen“,
    oder zurück auf „offen“. Friday selbst schickt nichts ab."""
    a = ablage.standard()
    e = a.antrag(aid)
    if not e:
        raise LookupError("Diesen Antrag gibt es nicht.")
    # Es zählt das Ticket des Fahrtags beim ABGEBEN: Lädt Eike es erst nach dem
    # Vorbereiten hoch, stünde sonst keine Fahrkarte im Antrag — und die Grenze fürs
    # Deutschlandticket (5 im Monat) würde still nicht mitgezählt.
    profil = int(e.get("profil") or 0)
    fahrkarte = e.get("fahrkarte") or (a.ticket_fuer(_zeit(e["an"]).date(), profil) or {}).get("art")
    if status == "abgeschickt":
        if bewerten(e)["stand"] != "bereit":
            raise ValueError("Als abgeschickt markieren geht nur bei einem Antrag, der bereit ist.")
        if hindernisse := _grenzen(_zeit(e["an"]), ausser=aid, profil=profil):
            raise ValueError(" ".join(hindernisse))
    a.antrag_aendern(aid, status=status, fahrkarte=fahrkarte,
                     abgeschickt_am=config.jetzt().isoformat(timespec="minutes") if status == "abgeschickt" else None)
    return bewerten(a.antrag(aid))
