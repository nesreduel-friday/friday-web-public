"""Garantie-Annahme: die feste Zusammenfassung für die Person (14.10.2026, Spec 2026-10-14-garantie-annahme-design.md).

Garantie-spezifisch und darum NICHT in `werkzeuge/akte.py` (dort stehen nur die generischen Akte-Primitive). `akte_zusammenfassung` baut
serverseitig, was eingereicht würde (Fahrt, gemessene Verspätung, Ticket); die Bestätigungs-Sperre vor dem Antrag steht in
`garantie_antrag_aus_akte` und prüft den hier gemerkten Stand."""

from __future__ import annotations

import logging
from datetime import timedelta

from kern.dienste.uestra import gleiche_linie
from kern import config
from kern.ehrlichkeit.wirkung import wirkt
from kern.garantie import ablage, antrag
from kern.personen import register
from kern.skills import akten, primitive, standard, zug as skillzug
from kern.werkzeuge import akte
from kern.werkzeuge.akte import _abfahrt, _akte, _fehlt_text, _skill, _tag, _ticket_fuer
from kern.werkzeuge.garantie import halt_im_verlauf

log = logging.getLogger("friday.garantie_annahme")
KONNEKTOR = "uestra_garantie"
TOLERANZ = timedelta(minutes=5)
MAX_PRUEFEN = 25        # höchstens so viele Fahrtverläufe je Suche
MAX_ZEIGEN = 4          # so viele Möglichkeiten liest Friday der Person höchstens vor        # ohne Freigabe der Garantie gibt es das Werkzeug nicht (Default-Deny)

ERKLAERUNGEN = [
    {"name": "akte_zusammenfassung",
     "description": ("Baut die feste Zusammenfassung der Angaben (Fahrt, gemessene Verspätung, Ticket) — die einzige Quelle für das, "
                     "was eingereicht wird. Lies sie der Person genau so vor und warte auf ihre Antwort. Fehlt etwas oder das "
                     "Ticket, nennt die Antwort es."),
     "parameters": {"type": "OBJECT", "properties": {
         "skill": {"type": "STRING", "description": "Name der Vereinbarung, nach der du gerade arbeitest (nie vorlesen)"},
         "vorgang": {"type": "STRING", "description": "optional: Kennung des Vorgangs"}}, "required": ["skill"]}},
]


def _registrieren() -> None:
    primitive.registrieren(
        "akte_zusammenfassung", "Baut die feste Zusammenfassung der Garantie-Angaben (Fahrt, Messung, Ticket) zum Vorlesen",
        gates=["nur die Akte dieser Person", "nur der Standard-Ablauf", "braucht ein gültiges Ticket", "merkt den Stand für die Bestätigung"])



_registrieren()


async def fahrten_suchen(werte: dict) -> list[dict]:
    """Gemessene Fahrten an diesem Tag, die von `start` nach `ziel` fahren — und zu `linie` und `abfahrt` passen, soweit die Person sie
    nannte (Abfahrt ±5 Minuten am Einstieg). Nach Abfahrt sortiert; leer, wenn nichts passt. Wirft ValueError bei einer kaputten Uhrzeit."""
    hm = None
    if werte.get("abfahrt"):
        hm = _abfahrt(werte["abfahrt"])
        if not hm:
            raise ValueError("Die Abfahrtszeit ist keine Uhrzeit (HH:MM).")
    kandidaten = [r for r in ablage.standard().reisen() if r["tag"] == werte["datum"]]
    if werte.get("linie"):
        kandidaten = [r for r in kandidaten if gleiche_linie(werte["linie"], r["linie"])
                      or werte["linie"].casefold() == (r["linie"] or "").casefold()]
    treffer = []
    for r in kandidaten[:MAX_PRUEFEN]:
        try:
            v = await antrag.verlauf(r["id"])
            von, bis = halt_im_verlauf(v, werte["start"]), halt_im_verlauf(v, werte["ziel"])
        except Exception:                                    # noqa: BLE001 — diese Fahrt passt nicht oder ist nicht abrufbar
            continue
        kennungen = [h["reference"] for h in v["halte"]]
        if kennungen.index(von) >= kennungen.index(bis):
            continue                                         # falsche Richtung
        halt = next(h for h in v["halte"] if h["reference"] == von)
        plan = halt.get("ab_plan") or halt.get("an_plan")
        if hm and (not plan or abs(plan - plan.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)) > TOLERANZ):
            continue
        treffer.append({"reise": r["id"], "von": von, "bis": bis, "linie": r["linie"], "plan": plan,
                        "uhr": plan.astimezone(config.jetzt().tzinfo).strftime("%H:%M") if plan else ""})
    return sorted(treffer, key=lambda t: (t["plan"] is None, t["plan"]))


def _ticket_zeile(t: dict) -> str:
    art = t.get("bezeichnung") or t.get("art") or "Ticket"
    teile = [f"{art}, gültig bis {_tag(t['gueltig_bis'])}" if t.get("gueltig_bis") else art]
    if t.get("ticketnummer"):
        teile.append(f"Ticketnummer {t['ticketnummer']}")
    if t.get("kundenkarte"):
        teile.append(f"Kundenkarte {t['kundenkarte']}")
    return ", ".join(teile) + (" (von dir bestätigt)" if t.get("pruefung_quelle") == "person" else "")


@wirkt("akte", ok=lambda t: t.startswith("Zusammenfassung"), detail=lambda a, t: "Zusammenfassung erstellt")
async def akte_zusammenfassung(kontakt_id: str = "", skill: str = "", vorgang: str = "") -> str:
    s, absage = _skill(kontakt_id, "akte_zusammenfassung", skill)
    if s is None:
        return absage
    if s["name"] != standard.NAME:
        return "Dazu ist mit dir nichts vereinbart. Sag das schlicht in einem Satz."
    a, absage = _akte(kontakt_id, s, vorgang, anlegen=False)
    if a is None:
        return absage
    if a["status"] != "offen":
        return "Dafür ist schon ein Antrag vorbereitet. Eike gibt ihn frei — mehr ist nicht zu tun."
    store = akten.standard()
    werte = store.werte(a["id"])
    pflicht, _ = akten.fehlt(s["felder"], werte)
    if pflicht:
        return "Es fehlen noch Angaben, deshalb gibt es noch keine Zusammenfassung. " + _fehlt_text(s, werte)
    person = register.holen(kontakt_id)
    ticket = _ticket_fuer(person, werte)
    if not ticket:
        return (f"Es fehlt noch das Ticket: Für den {_tag(werte['datum'])} ist kein gültiges Ticket gespeichert. Bitte die Person um "
                "ein Foto ihres Tickets und lies es dann mit ticket_lesen.")
    fehlend = [f for f in ("linie", "abfahrt") if f not in werte]
    if fehlend:                                              # Die Person kannte Linie oder Abfahrt nicht: die Fahrt suchen
        try:
            treffer = await fahrten_suchen(werte)
        except ValueError as exc:
            return f"{exc} Frag die Person noch einmal nach der Uhrzeit."
        if not treffer:
            return ("Zu diesen Angaben finde ich keine gemessene Fahrt. Frag die Person nach der Linie und der geplanten Abfahrt "
                    "(Uhrzeit) und halte sie fest. Weiß sie beides nicht, sag ehrlich, dass sich der Antrag so nicht vorbereiten lässt.")
        if len(treffer) > 1:
            zeigen = "; ".join(f"Linie {t['linie']} um {t['uhr']}" for t in treffer[:MAX_ZEIGEN])
            if len(treffer) > MAX_ZEIGEN:
                return (f"Es passen mehrere Fahrten, zum Beispiel: {zeigen}. Frag die Person, um wie viel Uhr ungefähr sie "
                        "eingestiegen ist, und halte es fest.")
            return (f"Mehrere Fahrten passen zu den Angaben: {zeigen}. Lies der Person die Möglichkeiten vor, frag, welche es war "
                    "(Linie oder Uhrzeit), und halte es fest.")
        t = treffer[0]
        _ok, absagen = store.schreiben(a, s["felder"], {f: (t["linie"] if f == "linie" else t["uhr"]) for f in fehlend})
        if absagen:
            return "Die gefundene Fahrt ließ sich nicht eintragen. Frag die Person nach Linie und Abfahrt. " + " ".join(absagen)
        werte = store.werte(a["id"])
    fahrt, _grund = await akte._fahrt_finden(werte)
    messung = ""
    if fahrt is not None:
        try:
            v = await antrag.verlauf(fahrt["reise"])
            halt = next((h for h in v["halte"] if h["reference"] == fahrt["bis"]), None)
            if isinstance((halt or {}).get("verspaetung"), int):
                messung = f"Gemessene Verspätung am Ziel: {halt['verspaetung']} Minuten."
        except Exception:                                    # noqa: BLE001 — ohne Messung nur ohne diese Zeile
            log.exception("Zusammenfassung: Messung nicht lesbar")
    zeilen = [f"Fahrt am {_tag(werte['datum'])}: Linie {werte['linie']} um {werte['abfahrt']} von {werte['start']} nach {werte['ziel']}.",
              messung or ("Dazu finde ich keine gemessene Fahrt — Eike schaut dann selbst nach." if fahrt is None
                          else "Die Verspätung wird beim Vorbereiten gemessen."),
              "Ticket: " + _ticket_zeile(ticket) + "."]
    # Vorgemerkt, nicht geschrieben: Als gezeigt gilt sie erst, wenn die Antwort an die Person die Merkstücke nennt (`zug.festschreiben`).
    zeilen = [z for z in zeilen if z]
    skillzug.vormerken(a["id"], akten.stand_hash(werte, ticket),
                       [_tag(werte["datum"])[:6], werte["linie"], werte["abfahrt"], werte["start"], werte["ziel"]], "\n".join(zeilen))
    return ("Zusammenfassung für die Person. Schick ihr in deiner Antwort GENAU diesen Text — nichts ändern, nichts weglassen — und "
            "frag am Ende, ob alles stimmt:\n---\n" + "\n".join(zeilen) + "\n---\nWarte auf ihre Antwort; erst danach darfst du "
            "den Antrag vorbereiten.")


akte_zusammenfassung.kennt_kontakt = True


WERKZEUGE = {"akte_zusammenfassung": akte_zusammenfassung}
