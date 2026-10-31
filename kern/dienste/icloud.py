"""iCloud-Kalender über CalDAV — nur lesend.

Apple bietet keinen eigenen Kalender-API-Zugang, wohl aber CalDAV unter
`caldav.icloud.com`. Genutzt wird die etablierte Bibliothek `caldav` statt
eines eigenen Clients — sie kennt die Eigenheiten (Principal-Suche,
Zeitzonen, wiederkehrende Termine mit `expand`).

Zugang: Apple-ID + **app-spezifisches Passwort** (appleid.apple.com →
Anmeldung und Sicherheit). Das normale Apple-Passwort funktioniert nicht,
weil Apple Zwei-Faktor erzwingt. Eike legt es selbst an und trägt es auf der
Freigaben-Seite ein.

Nur lesend: Friday liest Termine, legt keine an, ändert und löscht keine.
Blockierend (die Bibliothek ist synchron) — Aufrufer nutzen `asyncio.to_thread`.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import date, datetime, time, timedelta, timezone

from kern import konnektoren

log = logging.getLogger("friday.icloud")

KONNEKTOR = "icloud_kalender"
URL = "https://caldav.icloud.com/"
ZEITLIMIT = 30

_abgewiesen: str | None = None


class AnmeldeFehler(RuntimeError):
    """Anmeldung am iCloud-Kalender nicht möglich — vorlesbar."""


def zugang() -> tuple[str, str]:
    z = konnektoren.zugang(KONNEKTOR)
    if not z:
        raise AnmeldeFehler("Für den iCloud-Kalender sind keine Zugangsdaten eingetragen.")
    return z


def vergessen() -> None:
    global _abgewiesen
    _abgewiesen = None


def _fingerabdruck(benutzer: str, passwort: str) -> str:
    return hashlib.sha256(f"{benutzer}\0{passwort}".encode()).hexdigest()


def _verbinden():
    """Angemeldeter CalDAV-Zugang. Genau ein Versuch — Apple sperrt sonst."""
    global _abgewiesen
    from caldav import DAVClient   # erst hier: Tests brauchen die Bibliothek nicht

    benutzer, passwort = zugang()
    fp = _fingerabdruck(benutzer, passwort)
    if _abgewiesen == fp:
        raise AnmeldeFehler("Apple hat diese Zugangsdaten abgewiesen. Bitte ein neues app-spezifisches "
                            "Passwort eintragen — ich probiere dieselben nicht noch einmal.")
    client = DAVClient(url=URL, username=benutzer, password=passwort, timeout=ZEITLIMIT)
    try:
        return client.principal()
    except Exception as exc:
        if "401" in str(exc) or "403" in str(exc) or "Unauthorized" in str(exc):
            _abgewiesen = fp
            raise AnmeldeFehler("Apple hat die Anmeldung abgewiesen. Brauchst du ein app-spezifisches "
                                "Passwort? Das normale Apple-Passwort funktioniert bei aktiver "
                                "Zwei-Faktor-Anmeldung nicht.") from None
        raise AnmeldeFehler(f"iCloud ist nicht erreichbar ({type(exc).__name__}).") from None


def kalender() -> list[str]:
    """Namen der Kalender — für die Prüfung auf der Freigaben-Seite."""
    return [(k.name or "ohne Namen") for k in _verbinden().calendars()]


def _zeitpunkt(wert) -> tuple[str, bool]:
    """(ISO-Zeit, ganztägig). Apple liefert `date` für ganztägige Termine."""
    if isinstance(wert, datetime):
        w = wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)
        return w.isoformat(timespec="minutes"), False
    if isinstance(wert, date):
        return datetime.combine(wert, time.min, tzinfo=timezone.utc).isoformat(timespec="minutes"), True
    return "", False


def termine(von: datetime, bis: datetime, kalendername: str = "") -> list[dict]:
    """Termine im Zeitraum, chronologisch. Wiederkehrende werden aufgelöst."""
    gefunden = []
    for kal in _verbinden().calendars():
        if kalendername and kalendername.lower() not in (kal.name or "").lower():
            continue
        try:
            treffer = kal.search(start=von, end=bis, event=True, expand=True)
        except Exception as exc:
            log.warning("Kalender %s nicht lesbar: %s", kal.name, type(exc).__name__)
            continue
        for eintrag in treffer:
            komp = eintrag.icalendar_component
            if komp is None:
                continue
            beginn, ganztags = _zeitpunkt(komp.get("dtstart").dt if komp.get("dtstart") else None)
            ende, _ = _zeitpunkt(komp.get("dtend").dt if komp.get("dtend") else None)
            gefunden.append({
                "kalender": kal.name or "",
                "titel": str(komp.get("summary") or "ohne Titel"),
                "beginn": beginn, "ende": ende, "ganztags": ganztags,
                "ort": str(komp.get("location") or ""),
                "notiz": str(komp.get("description") or "")[:400],
            })
    return sorted(gefunden, key=lambda t: t["beginn"])


# Eike hat 7 Kalender. Friday wählt nach Thema (Schule, Feuerwehr, Arbeit …),
# aber in diese hier schreibt sie nie: abonnierte oder automatisch gefüllte
# Kalender, in denen ein Termin von Friday nichts zu suchen hat.
STANDARD_KALENDER = "Privat"
NICHT_SCHREIBEN = ("abfall", "müll", "feiertag", "geburtstag", "erinnerungen", "holiday", "birthday")


def _passt_nicht(name: str) -> bool:
    return any(wort in (name or "").lower() for wort in NICHT_SCHREIBEN)


def aufloesen(wunsch: str = "") -> str:
    """Welchen Kalender meint Friday? Exakter Name, sonst Teiltreffer, sonst der
    Standardkalender. Gesperrte Kalender werden nie zum Ziel."""
    namen = [(k.name or "") for k in _verbinden().calendars()]
    if not namen:
        raise AnmeldeFehler("In iCloud ist kein Kalender vorhanden.")
    standard = (os.getenv("ICLOUD_KALENDER", "").strip() or STANDARD_KALENDER)
    erlaubt = [n for n in namen if not _passt_nicht(n)]
    if not erlaubt:
        raise AnmeldeFehler("Es gibt keinen Kalender, in den ich schreiben darf.")
    gesucht = (wunsch or "").strip().lower()
    if gesucht and not _passt_nicht(gesucht):
        for n in erlaubt:
            if n.lower() == gesucht:
                return n
        for n in erlaubt:
            if gesucht in n.lower() or n.lower() in gesucht:
                return n
    for n in erlaubt:
        if n.lower() == standard.lower():
            return n
    return erlaubt[0]


def anlegen(titel: str, beginn: datetime, ende: datetime, ort: str = "", notiz: str = "",
            kalendername: str = "") -> dict:
    """Einen Termin eintragen. Wird NUR nach Eikes ausdrücklicher Zustimmung
    aufgerufen (siehe `kern/werkzeuge/kalender.py`). Der Termin wird als
    vollständige iCalendar-Datei gebaut und abgelegt — verlässlicher, als sich
    auf die Bequemlichkeiten der Bibliothek zu verlassen."""
    import uuid

    from icalendar import Calendar as ICal
    from icalendar import Event

    principal = _verbinden()
    kalender_liste = principal.calendars()
    if not kalender_liste:
        raise AnmeldeFehler("In iCloud ist kein Kalender vorhanden.")
    # Der Name kommt aus `aufloesen` und ist damit schon geprüft — hier nur noch finden.
    gesucht = (kalendername or STANDARD_KALENDER).strip().lower()
    ziel = next((k for k in kalender_liste if (k.name or "").lower() == gesucht), None)
    if ziel is None or _passt_nicht(ziel.name or ""):
        ziel = next((k for k in kalender_liste if not _passt_nicht(k.name or "")), kalender_liste[0])

    ical = ICal()
    ical.add("prodid", "-//Friday//Assistent//DE")
    ical.add("version", "2.0")
    termin = Event()
    termin.add("summary", titel)
    termin.add("dtstart", beginn)
    termin.add("dtend", ende)
    termin.add("dtstamp", datetime.now(timezone.utc))
    termin["uid"] = f"{uuid.uuid4().hex}@friday"
    if ort:
        termin.add("location", ort)
    if notiz:
        termin.add("description", notiz)
    ical.add_component(termin)
    ziel.save_event(ical.to_ical().decode("utf-8"))
    log.info("Termin angelegt: %s (%s)", titel, ziel.name)
    return {"kalender": ziel.name or "", "titel": titel,
            "beginn": beginn.isoformat(timespec="minutes"), "ende": ende.isoformat(timespec="minutes")}


def pruefen() -> str:
    namen = kalender()
    return f"Angemeldet bei iCloud. {len(namen)} Kalender: " + ", ".join(namen[:6]) if namen \
        else "Angemeldet bei iCloud, aber kein Kalender gefunden."
