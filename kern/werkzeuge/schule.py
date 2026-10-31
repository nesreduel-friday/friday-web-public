"""Schul-Werkzeuge — der Schulmanager im Gespräch.

Die Arbeit macht `kern/dienste/schulmanager.py` (Anmeldung, API, Rohdaten);
hier steht nur, was daraus einen lesbaren Text macht. Vorbild ist das alte
Friday (`tools/schulmanager.py`), hier mit allen Angaben, die eine Stunde
hergibt: Fach, Raum, Lehrkraft, Vertretung, Ausfall, was eigentlich gewesen
wäre, und der Hinweis der Schule.

Elternbriefe sind schon verarbeitet, wenn Friday gefragt wird
(`kern/schule/briefe.py`): hier wird nur kurz nach Neuem geschaut und dann
aus der Ablage geantwortet.

Rein lesend. Nur verfügbar, wenn der Konnektor auf der Freigaben-Seite an
ist und Zugangsdaten hat (`KONNEKTOR`, geprüft in `katalog.py`).

Der Stundenplan kommt bewusst NICHT ins Gedächtnis: er ändert sich von
selbst, jeder Stand bliebe als überholter Fakt liegen.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx

from kern import config
from kern.dashboard import friday_karten
from kern.dienste import netz, schulmanager as dienst
from kern.gehirn.persona import WOCHENTAGE
from kern.schule import ablage, briefe
from kern.schule import hausaufgaben as kurse
from kern.schule.briefe import html_zu_text  # noqa: F401 — auch für Tests hier erreichbar

KONNEKTOR = "schulmanager"

_TAGE = {name.lower(): i for i, name in enumerate(WOCHENTAGE)}
_TAGE.update({"mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4, "sa": 5, "so": 6})

# Lange Texte kürzen — wer alles braucht, sieht es im Schulmanager.
MAX_AUFGABE_ZEICHEN = 400
MAX_BRIEF_ZEICHEN = 3000

ERKLAERUNGEN = [
    {
        "name": "stundenplan",
        "description": ("Holt Eikes Stundenplan aus dem Schulmanager: Fächer, Räume, Lehrkräfte, "
                        "Vertretungen, Ausfälle und Hinweise der Schule. Immer benutzen, wenn nach "
                        "Unterricht, Stunden, Räumen, Lehrern, Vertretung oder Ausfall gefragt wird."),
        "parameters": {"type": "OBJECT", "properties": {
            "tag": {"type": "STRING", "description": ("heute, morgen, übermorgen, ein Wochentag, ein Datum "
                                                      "wie 2026-09-14, 'diese Woche' oder 'nächste Woche'")},
        }},
    },
    {
        "name": "stundenplan_karte",
        "description": ("Legt Eikes Stundenplan für EINEN Tag als schöne Karte auf die Übersicht (Startseite der App), "
                        "samt Knopf zum Öffnen des Schulmanagers. Benutzen, wenn er den Stundenplan SEHEN will "
                        "('zeig mir den Stundenplan für Montag', 'was habe ich morgen' mit Wunsch nach Anzeige). "
                        "Zum reinen Nachfragen und Vorlesen bleibt es beim Werkzeug stundenplan."),
        "parameters": {"type": "OBJECT", "properties": {
            "tag": {"type": "STRING", "description": "heute, morgen, übermorgen, ein Wochentag oder ein Datum wie 2026-09-14"},
            "minuten": {"type": "INTEGER", "description": "Wie lange die Karte bleibt, in Minuten, 1 bis 720, Standard 120"},
        }},
    },
    {
        "name": "hausaufgaben",
        "description": ("Holt die Hausaufgaben aus dem Schulmanager. Achtung: dort steht kein "
                        "Abgabetermin, nur der Tag, an dem die Aufgabe gestellt wurde."),
        "parameters": {"type": "OBJECT", "properties": {
            "tage": {"type": "INTEGER", "description": "Wie viele Tage zurück, Standard 14"},
        }},
    },
    {
        "name": "elternbriefe",
        "description": ("Listet die Elternbriefe der Schule, schon gelesen und zusammengefasst (samt PDFs "
                        "und Bildern): Nummer, Titel, Datum, Kurzfassung, nächste Frist, ungelesen. "
                        "Sucht auch im Inhalt. Für alle Einzelheiten danach elternbrief mit der Nummer."),
        "parameters": {"type": "OBJECT", "properties": {
            "anzahl": {"type": "INTEGER", "description": "Wie viele, neueste zuerst (Standard 10)"},
            "suche": {"type": "STRING", "description": "Nur Briefe, in deren Titel oder Inhalt das Wort vorkommt"},
        }},
    },
    {
        "name": "elternbrief",
        "description": ("Ein Elternbrief ganz: Zusammenfassung inklusive Anhänge, Termine, Fristen, "
                        "was zu tun ist, Kosten und der Originaltext."),
        "parameters": {"type": "OBJECT", "properties": {
            "nummer": {"type": "INTEGER", "description": "Die Nummer aus elternbriefe"},
        }, "required": ["nummer"]},
    },
    {
        "name": "schultermine",
        "description": ("Termine und Fristen aus den Elternbriefen, die als Nächstes anstehen. Benutzen bei "
                        "'Was steht an?', 'Muss ich etwas abgeben, bezahlen oder unterschreiben?'"),
        "parameters": {"type": "OBJECT", "properties": {
            "tage": {"type": "INTEGER", "description": "Wie viele Tage voraus (Standard 60)"},
        }},
    },
]


def heute() -> date:
    return config.jetzt().date()


def tag_deuten(text: str | None, bezug: date | None = None) -> date:
    """'heute', 'morgen', 'Freitag' oder '2026-09-04' → Datum.
    Ein Wochentag meint IMMER die kommende Gelegenheit, heute eingeschlossen."""
    bezug = bezug or heute()
    wort = (text or "").strip().lower().removeprefix("am ").strip()
    if not wort or wort in ("heute", "jetzt"):
        return bezug
    if wort == "morgen":
        return bezug + timedelta(days=1)
    if wort in ("übermorgen", "uebermorgen"):
        return bezug + timedelta(days=2)
    if wort == "gestern":
        return bezug - timedelta(days=1)
    if wort in _TAGE:
        return bezug + timedelta(days=(_TAGE[wort] - bezug.weekday()) % 7)
    try:
        return date.fromisoformat(wort)
    except ValueError:
        return bezug


def _lehrkraefte(quelle: dict) -> str:
    """Nachnamen statt Kürzel — "bei Muster" lässt sich vorlesen, "bei MUS" nicht."""
    namen = [((l.get("lastname") or "").strip() or (l.get("abbreviation") or "").strip())
             for l in quelle.get("teachers") or []]
    namen = [n for n in namen if n]
    return namen[0] if len(namen) == 1 else (", ".join(namen[:-1]) + " und " + namen[-1] if namen else "")


def _fach(quelle: dict) -> str:
    """Der Fachname. Der Schulmanager liefert bei Kursen oft nur `subject.name = null` und ein Kürzel
    („BI281“ = Biologie, Kurs 281, gemessen 25.09.2026): das wird übersetzt, ein unbekanntes Kürzel bleibt."""
    s = quelle.get("subject") or {}
    name = (s.get("name") or "").strip()
    if name:
        return name
    kurz = (s.get("abbreviation") or quelle.get("subjectLabel") or "").strip()
    return kurse.fach(kurz) if kurz else "?"


def stunde_zeile(stunde: dict) -> str:
    art = stunde.get("type", "")
    aktuell = stunde.get("actualLesson") or {}
    original = (stunde.get("originalLessons") or [{}])[0] or {}
    quelle = aktuell or original
    std = stunde.get("classHour") or {}
    zeile = f"{std.get('number', '?')}. Stunde"
    if std.get("from") and std.get("until"):
        zeile += f" ({str(std['from'])[:5]}–{str(std['until'])[:5]})"
    zeile += f": {_fach(quelle)}"
    if lehrer := _lehrkraefte(quelle):
        zeile += f" bei {lehrer}"
    if raum := (quelle.get("room") or {}).get("name"):
        zeile += f" in {raum}"
    if art == "cancelledLesson":
        zeile += " — FÄLLT AUS"
    elif art == "substitution":
        zeile += " — VERTRETUNG"
        if original and (_fach(original) != _fach(aktuell) or _lehrkraefte(original) != _lehrkraefte(aktuell)):
            eigentlich = _fach(original)
            if l := _lehrkraefte(original):
                eigentlich += f" bei {l}"
            zeile += f", eigentlich {eigentlich}"
    elif art and art != "regularLesson":
        zeile += f" — {art}"
    kommentar = (aktuell.get("comment") or stunde.get("comment") or "").strip()
    if kommentar and kommentar.lower() not in ("vertretung", "entfall"):
        zeile += f" (Hinweis: {kommentar})"
    return zeile


def tages_text(stunden: list[dict], tag: date) -> str:
    eigene = [s for s in stunden if s.get("date") == tag.isoformat()]
    kopf = f"{WOCHENTAGE[tag.weekday()]}, {tag:%d.%m.%Y}"
    if not eigene:
        # „Kein Unterricht" und „keine Daten" sind zweierlei. Am 14.09.2026 meldete
        # das Werkzeug für den 17.11. „kein Unterricht eingetragen", woraufhin Friday
        # sich einen Stundenplan samt Lehrkraft „Peters" und Raum „B 1.15" ausdachte —
        # beide gibt es nicht. Unterschieden wird an der Woche: Hat KEIN Tag der Woche
        # Unterricht, ist das keine Aussage über den Tag.
        # Warum manche Wochen leer zurückkommen, ist NICHT geklärt (gemessen: 02.11.
        # voll mit 34 Stunden, 19.10., 16.11. und 30.11. leer). Deshalb behauptet der
        # Text auch keine Ursache — Ferien wären geraten.
        if not stunden:
            return (f"{kopf}: Dazu liegen im Schulmanager noch keine Stundenplandaten vor. "
                    "Das heißt NICHT, dass kein Unterricht ist.")
        return f"{kopf}: kein Unterricht eingetragen."
    ausfall = sum(1 for s in eigene if s.get("type") == "cancelledLesson")
    vertretung = sum(1 for s in eigene if s.get("type") == "substitution")
    zusatz = [f"{ausfall} fällt aus" if ausfall == 1 else f"{ausfall} fallen aus"] if ausfall else []
    if ausfall >= 4 and ausfall == len(eigene):
        # Gespräch 429 (03.10.2026): „Habe ich Montag Schule?“ — Friday sagte „Ja, aber …“, obwohl alles ausfiel.
        zusatz = [f"ALLE {ausfall} Stunden fallen aus — laut Plan findet an diesem Tag kein Unterricht statt: "
                  "„Habe ich Schule?“ beantwortest du mit Nein (und nennst, dass alles ausfällt)"]
    zusatz += [f"{vertretung} Vertretung" + ("en" if vertretung > 1 else "")] if vertretung else []
    return (f"{kopf}" + (f" ({', '.join(zusatz)})" if zusatz else "") + ":\n"
            + "\n".join(stunde_zeile(s) for s in eigene))


def _fehlertext(was: str, exc: Exception) -> str:
    if isinstance(exc, dienst.AnmeldeFehler):
        return f"Ich komme gerade nicht an {was}: {exc}"
    return netz.nicht_erreichbar("Der Schulmanager", exc, " Später nochmal versuchen.")


async def stundenplan(tag: str = "heute") -> str:
    wort = (tag or "").strip().lower()
    woche = "woche" in wort
    wann = heute() + timedelta(days=7) if woche and ("nächst" in wort or "naechst" in wort) \
        else (heute() if woche else tag_deuten(tag))
    montag = wann - timedelta(days=wann.weekday())
    try:
        stunden = await dienst.stundenplan(montag, montag + timedelta(days=6))
    except (dienst.AnmeldeFehler, httpx.HTTPError, RuntimeError, ValueError) as exc:
        return _fehlertext("den Stundenplan", exc)
    if not woche:
        return tages_text(stunden, wann)
    tage = [montag + timedelta(days=i) for i in range(7)]
    tage = [t for t in tage if t.weekday() < 5 or any(s.get("date") == t.isoformat() for s in stunden)]
    return "\n\n".join(tages_text(stunden, t) for t in tage)


def tages_liste(stunden: list[dict], tag: date) -> list[dict]:
    """Die Stunden EINES Tages für die Karte: Nummer, Zeiten, Fach, Raum, Lehrkraft, Status.
    Ausgefallene Stunden bleiben drin (mit ihrem Fach), Vertretungen nennen das eigentliche Fach."""
    liste = []
    for e in stunden:
        if e.get("date") != tag.isoformat():
            continue
        std = e.get("classHour") or {}
        original = (e.get("originalLessons") or [{}])[0] or {}
        quelle = e.get("actualLesson") or original
        art = e.get("type", "")
        status = "ausfall" if art == "cancelledLesson" else "vertretung" if art == "substitution" else "regulaer"
        hinweis = ""
        if status == "vertretung" and original and _fach(original) != _fach(quelle):
            hinweis = f"eigentlich {_fach(original)}"
        liste.append({"nr": str(std.get("number") or "").strip(), "von": str(std.get("from") or "")[:5],
                      "bis": str(std.get("until") or "")[:5], "fach": _fach(quelle),
                      "raum": (quelle.get("room") or {}).get("name", ""), "lehrer": _lehrkraefte(quelle),
                      "status": status, "hinweis": hinweis})
    liste.sort(key=lambda x: (int(x["nr"]) if x["nr"].isdigit() else 99, x["von"]))
    return liste


async def stundenplan_karte(tag: str = "heute", minuten: int = friday_karten.MINUTEN_STUNDENPLAN) -> str:
    wann = tag_deuten(tag)
    kopf = f"{WOCHENTAGE[wann.weekday()]}, {wann:%d.%m.%Y}"
    try:
        stunden = await dienst.stundenplan(wann, wann)
    except (dienst.AnmeldeFehler, httpx.HTTPError, RuntimeError, ValueError) as exc:
        return _fehlertext("den Stundenplan", exc)
    liste = tages_liste(stunden, wann)
    if not liste:
        # „Keine Daten“ ist nicht „kein Unterricht“ (14.09.2026): nichts behaupten, keine leere Karte.
        return (f"Für {kopf} liegen im Schulmanager keine Stunden vor. Das heißt nicht zwingend, dass kein "
                "Unterricht ist — ich lege deshalb keine Karte.")
    try:
        await friday_karten.legen_stundenplan(wann, liste, minuten)
    except friday_karten.KarteFehler as exc:
        return str(exc)
    return f"Der Stundenplan für {kopf} liegt jetzt als Karte auf der Übersicht ({len(liste)} Stunden)."


async def hausaufgaben(tage: int = 14) -> str:
    tage = max(1, min(int(tage), 120))
    try:
        eintraege = await dienst.hausaufgaben()
    except (dienst.AnmeldeFehler, httpx.HTTPError, RuntimeError, ValueError) as exc:
        return _fehlertext("die Hausaufgaben", exc)
    grenze = (heute() - timedelta(days=tage)).isoformat()
    passend = sorted((e for e in eintraege if isinstance(e, dict) and (e.get("date") or "") >= grenze
                      and (e.get("homework") or "").strip()), key=lambda e: e.get("date", ""))
    if not passend:
        return f"In den letzten {tage} Tagen sind keine Hausaufgaben eingetragen."
    zeilen = []
    for e in passend:
        fach = e.get("subject")
        fach = _fach({"subject": fach}) if isinstance(fach, dict) else (fach or "?")
        try:
            t = date.fromisoformat(e["date"])
            wann = f"{WOCHENTAGE[t.weekday()]} {t:%d.%m.}"
        except (KeyError, ValueError):
            wann = e.get("date", "?")
        text = " ".join(e["homework"].split())
        if len(text) > MAX_AUFGABE_ZEICHEN:
            text = text[:MAX_AUFGABE_ZEICHEN] + " …"
        zeilen.append(f"{fach}, gestellt am {wann}: {text}")
    return ("\n".join(zeilen) + "\n(Der Schulmanager kennt keinen Abgabetermin — "
            "genannt ist der Tag, an dem die Aufgabe gestellt wurde.)")


# ---------- Elternbriefe: aus der Ablage ----------

def _tag(iso) -> date | None:
    try:
        return date.fromisoformat(str(iso or "")[:10])
    except ValueError:
        return None


def _datum(iso) -> str:
    t = _tag(iso)
    return f"{t:%d.%m.%Y}" if t else "?"


def _eintraege(b: dict) -> list[tuple[date, str, str]]:
    """(Datum, Art, Was) aus einem Brief: Termine, Fristen und die Antwortfrist."""
    e = b.get("ergebnis") or {}
    liste = [(_tag(x.get("datum")), art, (x.get("was") or "").strip())
             for art, schluessel in (("Termin", "termine"), ("Frist", "fristen"))
             for x in e.get(schluessel) or [] if isinstance(x, dict)]
    if b.get("frist"):
        liste.append((_tag(b["frist"]), "Frist", "Antwort im Schulmanager"))
    return [(t, art, was) for t, art, was in liste if t]


async def elternbriefe(anzahl: int = 10, suche: str = "") -> str:
    anzahl = max(1, min(int(anzahl), 50))
    await briefe.auffrischen()
    alle = ablage.standard().liste()
    if not alle:
        return "Ich habe noch keine Elternbriefe — der Schulmanager antwortet vielleicht gerade nicht."
    wort = (suche or "").strip().lower()
    if wort:
        alle = [b for b in alle if wort in " ".join([b["titel"], (b.get("ergebnis") or {}).get("kurz", ""),
                                                     (b.get("ergebnis") or {}).get("inhalt", ""),
                                                     b.get("text") or ""]).lower()]
        if not alle:
            return f"Keine Elternbriefe zu „{suche}“ gefunden."
    zeilen, ab = [], heute()
    for b in alle[:anzahl]:
        zeile = f"Brief {b['id']}: „{b['titel'] or 'ohne Titel'}“ vom {_datum(b['gesendet'])}"
        if not b["gelesen"]:
            zeile += " (ungelesen)"
        if b.get("ergebnis"):
            zeile += f" — {b['ergebnis'].get('kurz', '').strip()}"
            fristen = sorted((t, was) for t, art, was in _eintraege(b) if art == "Frist" and t >= ab)
            if fristen:
                zeile += f" Nächste Frist: {fristen[0][0]:%d.%m.} ({fristen[0][1]})."
        else:
            zeile += " — noch nicht verarbeitet"
        zeilen.append(zeile)
    return "\n".join(zeilen) + "\n(Mit elternbrief und der Nummer bekommst du alle Einzelheiten.)"


async def elternbrief(nummer: int) -> str:
    await briefe.auffrischen()
    b = ablage.standard().eintrag(int(nummer))
    if not b:
        return f"Einen Elternbrief mit der Nummer {nummer} finde ich nicht."
    if not b.get("ergebnis") and b["status"] != "fehler":
        await briefe.verarbeiten(b["id"])   # gerade erst angekommen — jetzt gleich
        b = ablage.standard().eintrag(b["id"]) or b
    teile = [f"„{b['titel'] or 'ohne Titel'}“, verschickt am {_datum(b['gesendet'])}"
             + ("" if b["gelesen"] else " (im Schulmanager noch ungelesen)")]
    e = b.get("ergebnis")
    if e:
        teile.append(f"Zusammenfassung: {e.get('inhalt') or e.get('kurz') or ''}".strip())
        for titel, art in (("Termine", "Termin"), ("Fristen", "Frist")):
            eintraege = sorted((t, was) for t, a, was in _eintraege(b) if a == art)
            if eintraege:
                teile.append(f"{titel}: " + "; ".join(f"{t:%d.%m.%Y} {was}" for t, was in eintraege))
        if e.get("zu_tun"):
            teile.append("Zu tun: " + "; ".join(e["zu_tun"]))
        if (e.get("kosten") or "").strip():
            teile.append(f"Kosten: {e['kosten'].strip()}")
    elif b.get("frist"):
        teile.append(f"Antwort erbeten bis {_datum(b['frist'])}")
    if b["anhaenge"]:
        teile.append("Anhänge: " + ", ".join(a["name"] + (" (nicht lesbar)" if a.get("ungelesen") or a.get("fehler")
                                                          else "") for a in b["anhaenge"]))
    if b.get("text"):
        text = b["text"] if len(b["text"]) <= MAX_BRIEF_ZEICHEN else b["text"][:MAX_BRIEF_ZEICHEN] + " …"
        teile.append(f"Originaltext:\n{text}")
    elif not e:
        teile.append("Den Inhalt konnte ich noch nicht abrufen.")
    return "\n\n".join(teile)


async def schultermine(tage: int = 60) -> str:
    tage = max(1, min(int(tage), 365))
    await briefe.auffrischen()
    ab, bis = heute(), heute() + timedelta(days=tage)
    kommend = sorted((t, art, was, b["titel"], b["id"]) for b in ablage.standard().liste()
                     for t, art, was in _eintraege(b) if ab <= t <= bis)
    if not kommend:
        return f"In den nächsten {tage} Tagen stehen aus den Elternbriefen keine Termine oder Fristen an."
    return "\n".join(f"{WOCHENTAGE[t.weekday()]} {t:%d.%m.}: {art} — {was} (aus „{titel}“, Brief {bid})"
                     for t, art, was, titel, bid in kommend)


WERKZEUGE = {"stundenplan": stundenplan, "stundenplan_karte": stundenplan_karte, "hausaufgaben": hausaufgaben, "elternbriefe": elternbriefe,
             "elternbrief": elternbrief, "schultermine": schultermine}
# Brieftexte stammen von Dritten: danach wird "merken" zum Vorschlag (katalog.liefert_fremdes).
FREMDE_WERKZEUGE = {"elternbriefe", "elternbrief", "schultermine"}
