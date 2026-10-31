"""Snapchat — Friday schickt Eike echte Snaps und liest seine Antworten.

Läuft über ein echtes Samsung-Handy per USB an hphome, gesteuert vom Host-
Helfer `host-helfer/snap_helfer.py` (kein Emulator: Snapchats Play-Integrity-
Prüfung blockt Emulatoren; kein Root: das würde dasselbe Risiko zurückbringen).
Dienst und Helfer tauschen sich nur über Dateien in `data/snapchat/` aus —
genau wie bei `hphome.py`/`hphome_helfer.py`, kein HTTP zwischen ihnen.

Sende- und Prüfrhythmus des Helfers sind bewusst unregelmäßig und auf aktive
Stunden begrenzt (Muster wie `kern/dienste/whatsapp.py`) — normale Vorsicht,
keine gezielte Tarnung vor Erkennung.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kern import config
from kern.personen import register
from kern.protokoll import speicher as pspeicher

KONNEKTOR = "snapchat"

# Sendegrenze je Fenster — praktisch aufgehoben, der Mechanismus bleibt.
SENDE_GRENZE = 9999  # Eike (29.09.2026): die Grenze ist nicht nötig
SENDE_FENSTER_S = 2400
DUBLETTE_S = 60  # derselbe einmalige Text an dieselbe Person wird innerhalb dieser Zeit nicht noch einmal gelegt

# Wie im Web-Chat: Ein Faden bleibt, solange Eike schreibt, und beginnt nach Ruhe neu. Nur der Verlauf verfällt — das
# Langzeitgedächtnis bleibt.
RUHE_MINUTEN = 25
VERLAUF_ZUEGE = 8      # so viele frühere Beiträge (beider Seiten) gehen mit in die Antwort
AUFTRAG_TEXT_MAX = 2000   # so viel Text nimmt ein Auftrag höchstens an (vorher still 280: Antworten wurden mitten im Satz gekürzt)
QUEUE_MAX = 4          # so viele Aufträge dürfen hinter dem laufenden warten
GESENDET_STUNDEN = 12  # so lange weiß Friday, was sie Eike geschickt hat — egal, wann er den Snap öffnet
GESENDET_MAX = 20
ART = "snapchat"

_sperre = threading.RLock()  # Rekursiv: auftrag_legen hält den Lock und ruft _schreiben auf


class SnapchatFehler(RuntimeError):
    """Vorlesbar: sagt, was los ist."""


def eike_name() -> str:
    """So heißt Eike in Fridays Snapchat-Konto (Umgebung `SNAPCHAT_EIKE_NAME`, Standard „Eike“)."""
    return os.getenv("SNAPCHAT_EIKE_NAME", "").strip() or "Eike"


def kontakte() -> list[dict]:
    """Wem Friday über Snapchat schreiben darf: Eike und jede aktive (nicht pausierte) Person mit Snapchat-Namen."""
    liste = [{"anzeige": eike_name(), "person": None}]
    for p in register.liste():
        anzeige = (p.get("kanaele") or {}).get("snapchat", "").strip()
        if anzeige and not p.get("pausiert"):
            liste.append({"anzeige": anzeige, "person": p["id"]})
    return liste


def kontakte_schreiben() -> None:
    """Die Kontaktliste für den Helfer (`kontakte.json`): Er sendet nur an Namen, die hier stehen, und prüft, dass Person und
    Empfänger eines Auftrags zusammenpassen. Wird bei jedem Auftrag UND im Takt der Wache aufgefrischt, aber nur geschrieben, wenn
    sich etwas geändert hat — pausiert oder benennt Eike eine Person um, gilt das sofort auch für schon gelegte Aufträge."""
    liste = kontakte()
    alt = _lesen("kontakte.json", None)
    if isinstance(alt, dict) and alt.get("kontakte") == liste:
        return
    _schreiben("kontakte.json", {"kontakte": liste, "zeit": time.time()})


def _empfaenger(person: str | None) -> str:
    """Der Anzeigename, an den ein Auftrag geht: Eike bei `person=None`, sonst der Snapchat-Name der Person. Unbekannte,
    pausierte und Personen ohne Snapchat-Namen bekommen keinen Auftrag."""
    for k in kontakte():
        if k["person"] == person:
            return k["anzeige"]
    raise SnapchatFehler("Diese Person hat keinen Snapchat-Kontakt oder ist pausiert — ich schicke nichts.")


def _ordner() -> Path:
    p = config.daten_ordner() / "snapchat"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _lesen(name: str, standard):
    try:
        return json.loads((_ordner() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return standard


def _schreiben(name: str, daten) -> None:
    ziel = _ordner() / name
    neu = ziel.with_suffix(".neu")
    with _sperre:
        neu.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
        os.chmod(neu, 0o600)
        neu.replace(ziel)


# ---------- Senden ----------

def sende_zaehler() -> int:
    """Wie viele Sendeaufträge in den letzten `SENDE_FENSTER_S` angelegt wurden."""
    grenze = time.time() - SENDE_FENSTER_S
    return len([t for t in _lesen("versendet.json", []) if isinstance(t, (int, float)) and t >= grenze])


def _schlange() -> Path:
    p = _ordner() / "warteschlange"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _platz_belegen(eintrag: dict) -> bool:
    """Legt den Auftrag auf den Platz, den der Helfer abarbeitet (`auftrag.json`) — nur, wenn er frei ist.
    Exklusiv angelegt, damit sich Dienst (Container) und Helfer (Host) nie gegenseitig überschreiben."""
    try:
        fd = os.open(_ordner() / "auftrag.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(eintrag, f, ensure_ascii=False)
    return True


def auftraege_anzahl() -> int:
    """Wie viele Aufträge vor dem Helfer liegen: der laufende und die wartenden."""
    return (1 if (_ordner() / "auftrag.json").exists() else 0) + len(list(_schlange().glob("*.json")))


def wartender_snap() -> dict | None:
    """Der laufende oder wartende Bild-Snap (Auftrag mit `art` „snap“), sonst None."""
    dateien = [_ordner() / "auftrag.json", *sorted(_schlange().glob("*.json"))]
    for d in dateien:
        try:
            a = json.loads(d.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(a, dict) and a.get("art") == "snap":
            return a
    return None


def snap_wartet() -> bool:
    """Wartet ein Bild-Snap (laufend oder in der Schlange)? Ein Snap nach dem anderen: Der Helfer steuert EIN Handy,
    zwei gleichzeitig würden sich in die Quere kommen (Galerie, Director-Modus)."""
    dateien = [_ordner() / "auftrag.json", *sorted(_schlange().glob("*.json"))]
    for d in dateien:
        try:
            if json.loads(d.read_text(encoding="utf-8")).get("art") == "snap":
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def teilen(text: str, maximal: int | None = None) -> list[str]:
    """Teilt einen langen Text in Stücke von höchstens `maximal` Zeichen — an Absatz-, dann Satzgrenzen, sonst an einem Leerzeichen,
    nur im Notfall (ein Wort länger als das Maximum) hart. Es geht nichts verloren, kein Wort wird doppelt. Leerer Text → leere Liste."""
    maximal = maximal or AUFTRAG_TEXT_MAX
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= maximal:
        return [text]
    grenzen = re.split(r"(?<=[.!?…])\s+|\n\s*\n", text)          # Sätze und Absätze
    teile: list[str] = []
    aktuell = ""
    for satz in (s.strip() for s in grenzen if s and s.strip()):
        while len(satz) > maximal:                                   # ein einzelner Satz, der allein nicht passt
            if aktuell:
                teile.append(aktuell)
                aktuell = ""
            schnitt = satz.rfind(" ", 0, maximal + 1)
            if schnitt <= 0:
                schnitt = maximal                                    # ein Wort länger als das Maximum: hart
            teile.append(satz[:schnitt].strip())
            satz = satz[schnitt:].strip()
        if not satz:
            continue
        if aktuell and len(aktuell) + 1 + len(satz) > maximal:
            teile.append(aktuell)
            aktuell = satz
        else:
            aktuell = f"{aktuell} {satz}".strip()
    if aktuell:
        teile.append(aktuell)
    return teile


def auftrag_legen(art: str, text: str = "", bild_pfad: str | None = None, person: str | None = None,
                   snapchat: str = "", einmalig: bool = False, ausloeser: float | None = None) -> str:
    """Legt einen Auftrag für den Host-Helfer an. `art`: "snap" (Bild-Snap,
    braucht `bild_pfad` oder nur Text-Snap ohne Bild), "text" (reiner Chat-Text,
    für Fridays Gesprächsantworten — kein Bild, kein Director Mode nötig),
    "anfrage" (Kontakt-Anfrage an einen Snapchat-Namen, der noch nicht in
    `kontakte.json` steht — Empfänger kommt aus `snapchat`, nicht aus dem Register)
    "annehmen" (eingegangene Anfrage annehmen: `snapchat` ist der Anzeigename aus „Added Me“),
    oder "suche" (Handy-Suche nach einem Namen, tippt NICHTS an — `snapchat`
    ist der Suchbegriff, das Ergebnis steht in `suchergebnisse.json`).

    Der Helfer arbeitet einen Auftrag nach dem anderen ab: Ist sein Platz belegt, wartet der neue in der
    Schlange (bis `QUEUE_MAX`), und der Helfer rückt ihn nach, sobald der laufende fertig ist.

    Jeder Auftrag trägt eine `id` (der Helfer sendet jede ID höchstens einmal). `einmalig=True` (Direktversand auf Eikes
    Bitte, 07.10.2026): derselbe Text an dieselbe Person binnen `DUBLETTE_S` wird abgewiesen — Gesprächsantworten nicht."""
    if art not in ("snap", "text", "anfrage", "suche", "stand", "annehmen"):
        raise SnapchatFehler(f'Unbekannte Auftragsart "{art}".')
    if art == "suche":
        empfaenger = (snapchat or "").strip()
        if not empfaenger:
            raise SnapchatFehler("Wonach soll das Handy suchen? Sag mir einen Namen.")
    elif art == "stand":
        empfaenger = (snapchat or "").strip()
        if not empfaenger:
            raise SnapchatFehler("Wessen Freundschaftsstand soll das Handy ansehen?")
    elif art == "annehmen":
        empfaenger = (snapchat or "").strip()
        if not empfaenger:
            raise SnapchatFehler("Wessen Anfrage soll das Handy annehmen? Sag mir den Namen.")
    elif art == "anfrage":
        empfaenger = (snapchat or "").strip()
        if not empfaenger:
            raise SnapchatFehler("Ohne Snapchat-Namen frage ich niemanden an.")
        if person is not None:
            profil = register.holen(person)
            if not profil or profil.get("pausiert"):
                raise SnapchatFehler("Diese Person ist pausiert oder unbekannt — ich frage niemanden an.")
    else:
        empfaenger = _empfaenger(person)       # wirft bei unbekannter/pausierter Person, bevor irgendetwas angelegt wird
    kontakte_schreiben()
    with _sperre:
        # Beide Checks und Writes müssen atomar erfolgen, um Race Conditions
        # bei parallelen Aufrufen (z.B. aus FastAPI threadpool) zu vermeiden.
        grenze = time.time() - SENDE_FENSTER_S
        versendet = _lesen("versendet.json", [])
        zaehler = len([t for t in versendet if isinstance(t, (int, float)) and t >= grenze])
        if zaehler >= SENDE_GRENZE and art != "annehmen":     # Nachsehen unter „Added Me“ verschickt nichts
            raise SnapchatFehler(
                f"Ich habe in dieser Stunde schon {SENDE_GRENZE} Snaps/Nachrichten verschickt. "
                "Das reicht erstmal.")
        h, alt = None, []
        if einmalig:
            gleich = " ".join(re.sub(r"[\W_]+", " ", text).casefold().split())      # Satzzeichen/Großschreibung/Leerraum zählen nicht
            h = hashlib.sha256(f"{art}|{person}|{gleich}".encode()).hexdigest()
            alt = [d for d in _lesen("dubletten.json", []) if isinstance(d, dict) and time.time() - d.get("t", 0) < DUBLETTE_S]
            if any(d.get("h") == h for d in alt):
                raise SnapchatFehler("Das habe ich eben schon geschickt — ich schicke es nicht doppelt.")
        if len(text) > AUFTRAG_TEXT_MAX:
            raise SnapchatFehler(f"Der Text ist zu lang für eine Nachricht (höchstens {AUFTRAG_TEXT_MAX} Zeichen) — er wurde nicht "
                                 "gesendet. Teile ihn auf (`teilen`).")
        jetzt = time.time()
        eintrag = {"art": art, "text": text, "bild_pfad": bild_pfad, "gelegt": jetzt,
                   "empfaenger": empfaenger, "person": person, "id": uuid.uuid4().hex}
        if ausloeser is not None:      # wann der Eingang entstand, der diese Antwort ausgelöst hat (Latenzmessung, 07.10.2026)
            eintrag["ausloeser"] = float(ausloeser)
        if not _platz_belegen(eintrag):
            if len(list(_schlange().glob("*.json"))) >= QUEUE_MAX:
                raise SnapchatFehler(f"Es warten schon {QUEUE_MAX + 1} Aufträge vor dem Handy. "
                                     "Ich schicke den nächsten, sobald einer durch ist.")
            ziel = _schlange() / f"{time.time_ns()}.json"
            neu = ziel.with_suffix(".neu")
            neu.write_text(json.dumps(eintrag, ensure_ascii=False), encoding="utf-8")
            os.chmod(neu, 0o600)
            neu.replace(ziel)
        if h:     # erst nach erfolgreichem Legen: ein gescheiterter Auftrag darf die Wiederholung nicht sperren
            _schreiben("dubletten.json", [*alt, {"h": h, "t": time.time()}][-50:])
        if art != "annehmen":
            versendet.append(jetzt)
            _schreiben("versendet.json", versendet[-100:])
        return eintrag["id"]


def ergebnis_fuer(auftrag_id: str) -> dict | None:
    """Die Ergebniszeile des Helfers zu einem Auftrag (`gesendet`, `verworfen`, `aufgegeben`) — oder None, solange er nichts gemeldet
    hat. Zeilen ohne `id` (ältere Helferstände) gehören keinem Auftrag."""
    if not auftrag_id:
        return None
    try:
        zeilen = (_ordner() / "ergebnisse.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for zeile in reversed(zeilen):
        try:
            e = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("id") == auftrag_id:
            return e
    return None


def auftrag_lesen() -> dict | None:
    """Der Auftrag, den der Helfer gerade abarbeitet (Diagnose/Tests) — der Helfer liest die Datei selbst."""
    return _lesen("auftrag.json", None)


# ---------- Faden und Verlauf (gemeinsam für Wache und Werkzeuge) ----------

def _grenze() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=RUHE_MINUTEN)).isoformat(timespec="seconds")


_PERSON = re.compile(r"p[1-9][0-9]{0,5}")


def person_von(eintrag: dict) -> str | None:
    """Wem gehört ein Eingangseintrag? Fehlt `person` oder ist es None, ist es Eike (bisheriges Verhalten). Alles andere muss
    eine Register-Id sein — ein ungültiger Wert (`eike`, `""`, `p1;`) wirft: Er darf NIE als Eike gedeutet werden."""
    p = eintrag.get("person")
    if p is None:
        return None
    if isinstance(p, str) and _PERSON.fullmatch(p):
        return p
    raise ValueError(f"Ungültige Person im Eingang: {p!r}")


def gruppen(eintraege: list[dict]) -> tuple[dict[str | None, list[dict]], list[dict]]:
    """Der Eingang nach Person gruppiert (None = Eike), dazu die Einträge mit ungültiger Person."""
    gruppen_: dict[str | None, list[dict]] = {}
    ungueltig: list[dict] = []
    for e in eintraege:
        try:
            gruppen_.setdefault(person_von(e), []).append(e)
        except ValueError:
            ungueltig.append(e)
    return gruppen_, ungueltig


def faden_vorhanden(person: str | None = None) -> int | None:
    """Der laufende Snapchat-Faden dieser Person (None = Eike), falls es einen gibt — legt keinen an (wer nicht antwortet,
    hinterlässt nichts). Jede Person hat ihren eigenen Faden: Verläufe vermischen sich nie."""
    try:
        grenze = _grenze()
        for g in pspeicher.standard().gespraeche(limit=500):  # neueste zuerst; viele Fäden anderer verdrängen keinen aktiven
            if g["art"] == ART and g.get("person") == person and g.get("zuletzt") and g["zuletzt"] >= grenze:
                return g["id"]
    except Exception:
        pass
    return None


def faden(person: str | None = None) -> int:
    """Der laufende Snapchat-Faden dieser Person, sonst ein neuer. Kein `gespraech_beenden()` — wie beim Chat bleibt `ende`
    NULL, und `offene_rueckblicke` nimmt sich einen Faden nach Ruhe."""
    return faden_vorhanden(person) or pspeicher.standard().gespraech_beginnen(ART, person=person)


def verlauf(person: str | None = None) -> list[dict]:
    """Die letzten Beiträge des laufenden Fadens dieser Person im Format von `chat.antworten`."""
    g = faden_vorhanden(person)
    if g is None:
        return []
    zuege = [b for b in pspeicher.standard().beitraege(g) if b["rolle"] in ("du", "friday") and b.get("text")]
    return [{"rolle": b["rolle"], "text": b["text"]} for b in zuege[-VERLAUF_ZUEGE:]]


def kontext_vermerken(text: str, person: str | None = None) -> None:
    """Etwas, das Friday selbst getan hat (einen Snap geschickt), festhalten — auf zwei Wegen: als Beitrag von ihr im
    Snapchat-Faden (Rückblick, Langzeitgedächtnis) und in `gesendet.json`, das `GESENDET_STUNDEN` lang gilt, auch wenn
    der Faden längst zu ist. So weiß sie es, wenn Eike den Snap erst Stunden später öffnet und darauf antwortet — auch
    wenn die Bitte im Web-Chat oder per Sprache kam. Ein Fehler hier darf das Senden nie verhindern.
    `person`: Register-Id einer Kontakt-Person — ihr Kontext steht nur in IHREM Faden; `gesendet.json` bleibt
    Eikes Kontext."""
    try:
        pspeicher.standard().beitrag(faden(person), "friday", text)
    except Exception:
        pass
    if person is not None:
        return
    try:
        with _sperre:
            jetzt = time.time()
            liste = [e for e in _lesen("gesendet.json", [])
                     if isinstance(e, dict) and jetzt - e.get("zeit", 0) < GESENDET_STUNDEN * 3600]
            liste.append({"zeit": jetzt, "text": text[:600]})
            _schreiben("gesendet.json", liste[-GESENDET_MAX:])
    except Exception:
        pass


def unbekannte_lesen() -> list[str]:
    """Namen aus `unbekannt_eingang.json`, die der Helfer in der Chatliste gesehen hat, ohne dass sie im Register stehen (nur
    Anzeigenamen, nie Inhalt; ihre Snaps werden nie geöffnet). Bereinigt, gekürzt, ohne Dubletten."""
    roh = _lesen("unbekannt_eingang.json", [])
    if not isinstance(roh, list):
        return []
    namen: list[str] = []
    gesehen: set[str] = set()
    for e in roh:
        roh_name = e.get("anzeige", "") if isinstance(e, dict) else e   # der Helfer hängt reine Namen an (Teil 4c), früher {"anzeige": …}
        anzeige = register._text(roh_name if isinstance(roh_name, str) else "", register.ANZEIGE_MAX)
        k = register.schluessel(anzeige)
        if anzeige and k not in gesehen:
            gesehen.add(k)
            namen.append(anzeige)
    return namen


def unbekannte_entfernen(anzeigen: list[str]) -> None:
    weg = {register.schluessel(a) for a in anzeigen}
    roh = _lesen("unbekannt_eingang.json", [])
    if not isinstance(roh, list):
        return
    rest = [e for e in roh if isinstance(e, dict)
            and register.schluessel(register._text(e.get("anzeige", ""), register.ANZEIGE_MAX)) not in weg]
    _schreiben("unbekannt_eingang.json", rest)


# ---------- Kontakt-Anfragen ----------

ANFRAGE_ZUSTAENDE = ("ausstehend", "angenommen", "gescheitert", "zurueckgezogen")


def _unter_sperre(funktion):
    """Lesen-Ändern-Schreiben auf `anfragen.json` ist EIN Schritt (07.10.2026: ohne Sperre überlebten von 40 parallelen
    Anfragen 3 — die Wache, die Web-Routen und die Werkzeuge laufen in verschiedenen Threads)."""
    @functools.wraps(funktion)
    def innen(*args, **kwargs):
        with _sperre:
            return funktion(*args, **kwargs)
    return innen


@_unter_sperre
def anfrage_legen(snapchat_name: str, person: str | None = None, name: str = "", danach: str = "",
                  titel: str = "") -> dict:
    """Vermerkt eine Kontakt-Anfrage (03.10.2026): Genau eine ausstehende je
    Snapchat-Name (normalisiert verglichen) — kein zweites Anfragen ohne Rückzug."""
    anzeige = (snapchat_name or "").strip()
    if not anzeige:
        raise SnapchatFehler("Ohne Snapchat-Namen frage ich niemanden an.")
    for a in anfragen():
        if register.schluessel(a.get("snapchat", "")) == register.schluessel(anzeige) \
                and a.get("zustand") == "ausstehend":
            raise SnapchatFehler(f"An {anzeige} ist schon eine Anfrage raus — ich frage nicht zweimal.")
    eintrag = {"snapchat": anzeige, "person": person, "gelegt": time.time(), "zustand": "ausstehend"}
    if (danach or "").strip():
        eintrag["danach"] = " ".join(danach.split())[:280]    # erste Nachricht nach der Annahme, von Eike gewollt
    if (name or "").strip():
        eintrag["name"] = " ".join(name.split())[:60]     # wie Eike die Person nennt — für das Anlegen nach der Annahme
    if (titel or "").strip():
        eintrag["titel"] = " ".join(titel.split())[:60]   # Anzeigename im Treffer — so erscheint sie nach der Annahme in der Chatliste
    _schreiben("anfragen.json", [*anfragen(), eintrag])
    return eintrag


def anfragen() -> list[dict]:
    """Alle vermerkten Anfragen (für Wache und Menü)."""
    return [a for a in _lesen("anfragen.json", []) if isinstance(a, dict)]


@_unter_sperre
def anfrage_setzen(snapchat_name: str, zustand: str) -> bool:
    """Zustand wechseln (`angenommen` nach Wachen-Fund). False: keine solche Anfrage."""
    if zustand not in ANFRAGE_ZUSTAENDE:
        raise SnapchatFehler(f'Unbekannter Anfrage-Zustand "{zustand}".')
    liste, gefunden = anfragen(), False
    for a in liste:
        if register.schluessel(a.get("snapchat", "")) == register.schluessel(snapchat_name or ""):
            a["zustand"] = zustand
            gefunden = True
    if gefunden:
        _schreiben("anfragen.json", liste)
    return gefunden


def freundschaft(snapchat_name: str) -> dict | None:
    """Was der Chat auf dem Handy zuletzt über die Freundschaft zeigte: {"stand", "zeit"} — oder None, wenn
    der Chat noch nie angesehen wurde. „befreundet“: gegenseitig (Kamera-Knopf in der Chatliste). „einseitig“: Snapchat meldet (Kreuz in der Liste oder
    Hinweiszeile), dass die Person Friday noch nicht hinzugefügt hat. „kein_hinweis“: Chat offen, aber keine solche Zeile (kein Beweis für eine Annahme)."""
    d = _lesen("freundschaft.json", {})
    e = d.get(register.schluessel(snapchat_name or "")) if isinstance(d, dict) else None
    return e if isinstance(e, dict) and e.get("stand") else None


def stand_fehlschlaege() -> dict[str, dict]:
    """Je Anzeigename (Schlüssel) das letzte Ergebnis eines Stand-Auftrags, wenn der Helfer aufgab (z. B. Chat nicht in der Chatliste):
    {"zeit", "grund"}. Ein späterer Erfolg desselben Namens löscht den Eintrag. Nur die letzten Zeilen werden gelesen."""
    try:
        zeilen = (_ordner() / "ergebnisse.jsonl").read_text(encoding="utf-8").splitlines()[-600:]
    except OSError:
        return {}
    aus: dict[str, dict] = {}
    for zeile in zeilen:
        try:
            e = json.loads(zeile)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("art") != "stand" or not e.get("empfaenger"):
            continue
        k = register.schluessel(str(e["empfaenger"]))
        if e.get("ergebnis") == "aufgegeben":
            aus[k] = {"zeit": float(e.get("zeit") or 0), "grund": str(e.get("grund") or "")}
        else:
            aus.pop(k, None)
    return aus


def freundschaft_satz(snapchat_name: str) -> str:
    """Ein ehrlicher Satz zum Stand — nie „angenommen“ ohne Beweis."""
    e = freundschaft(snapchat_name)
    if not e:
        return "Stand unbekannt — noch nicht auf dem Handy nachgesehen"
    try:
        wann = time.strftime("%d.%m. %H:%M", time.localtime(float(e.get("zeit", 0))))
    except (TypeError, ValueError):
        wann = "?"
    if e["stand"] == "befreundet":
        return f"ist mit Friday befreundet (Chatliste zeigt den Kamera-Knopf, {wann})"
    if e["stand"] == "einseitig":
        return f"hat Friday NOCH NICHT hinzugefügt (Snapchat meldet es, {wann})"
    return (f"Chat offen, Snapchat meldet kein „noch nicht hinzugefügt“ ({wann}) — ob die Person Friday "
            "hinzugefügt hat, ist damit nicht bewiesen")


@_unter_sperre
def anfrage_danach_holen(snapchat_name: str) -> str:
    """Der Text, den Friday nach der Annahme schicken soll — genau einmal (wird dabei geleert)."""
    liste, text = anfragen(), ""
    for a in liste:
        if register.schluessel(a.get("snapchat", "")) == register.schluessel(snapchat_name or "") and a.get("danach"):
            text, a["danach"] = str(a["danach"]), ""
    if text:
        _schreiben("anfragen.json", liste)
    return text


@_unter_sperre
def anfrage_zurueckziehen(snapchat_name: str) -> bool:
    """Vermerk entfernen (Eikes Rückzug). False: nichts da."""
    vorher = anfragen()
    rest = [a for a in vorher
            if register.schluessel(a.get("snapchat", "")) != register.schluessel(snapchat_name or "")]
    if len(rest) == len(vorher):
        return False
    _schreiben("anfragen.json", rest)
    return True


def anfrage_stornieren(snapchat_name: str) -> str:
    """Rückzug mit Wirkung (Gespräch oder Menü, 03.10.2026): Vermerk weg UND ein noch
    wartender Anfrage-Auftrag (Platz + Schlange) raus — sonst tippt das Handy nachts
    trotzdem auf Hinzufügen. Einen schon gesendeten Auftrag holt das nicht zurück."""
    anzeige = (snapchat_name or "").strip()
    if not anzeige:
        return "Wen soll ich zurückziehen? Sag mir den Snapchat-Namen."
    k = register.schluessel(anzeige)
    vermerk = anfrage_zurueckziehen(anzeige)
    raus = 0

    def _passt(pfad: Path) -> bool:
        try:
            auftrag = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(auftrag, dict) and auftrag.get("art") == "anfrage" \
            and register.schluessel(auftrag.get("empfaenger", "")) == k

    platz = _ordner() / "auftrag.json"
    if platz.exists() and _passt(platz):
        platz.unlink(missing_ok=True)
        raus += 1
    for datei in sorted(_schlange().glob("*.json")):
        if _passt(datei):
            datei.unlink(missing_ok=True)
            raus += 1
    if not vermerk and not raus:
        return "Dazu liegt keine Anfrage vor — weder Vermerk noch Auftrag."
    return f"Anfrage an {anzeige} zurückgezogen." + ("" if raus else " Beim Handy lag nichts mehr.")


# ---------- Erwartete Anfragen (11.10.2026, Torge-Fall) ----------
# Eike sagt „Torge darf dich annehmen“ / frägt selbst an: Der Name steht dann auf der Liste, und die Wache lässt das Handy
# regelmäßig unter „Added Me“ nachsehen. Angenommen wird NUR, wer hier steht (oder von Friday selbst angefragt wurde).
ERWARTET_GILT_S = 48 * 3600
ERWARTET_TAKT_S = 600


@_unter_sperre
def erwartet_legen(name: str) -> dict:
    anzeige = " ".join((name or "").split())[:60]
    if not anzeige:
        raise SnapchatFehler("Wessen Anfrage soll ich annehmen, wenn sie kommt? Sag mir den Namen.")
    liste = [e for e in _lesen("erwartet.json", []) if isinstance(e, dict)]
    k = register.schluessel(anzeige)
    for e in liste:
        if register.schluessel(e.get("name", "")) == k:
            e.update({"gelegt": time.time(), "zuletzt": 0, "auftrag": ""})
            _schreiben("erwartet.json", liste)
            return e
    eintrag = {"name": anzeige, "gelegt": time.time(), "zuletzt": 0, "auftrag": ""}
    _schreiben("erwartet.json", [*liste, eintrag][-30:])
    return eintrag


def erwartet_liste() -> list[dict]:
    """Noch gültige erwartete Namen: Eikes Freigaben plus Personen, die Friday selbst angefragt hat und die noch warten."""
    jetzt = time.time()
    aus = [e for e in _lesen("erwartet.json", []) if isinstance(e, dict) and jetzt - float(e.get("gelegt", 0) or 0) < ERWARTET_GILT_S]
    bekannt = {register.schluessel(e.get("name", "")) for e in aus}
    for a in anfragen():
        if a.get("zustand") != "ausstehend" or jetzt - float(a.get("gelegt", 0) or 0) >= ERWARTET_GILT_S:
            continue
        for feld in ("titel", "name"):
            n = " ".join(str(a.get(feld, "")).split())
            if n and register.schluessel(n) not in bekannt:
                bekannt.add(register.schluessel(n))
                aus.append({"name": n, "gelegt": a.get("gelegt", 0), "zuletzt": 0, "auftrag": "", "eigene_anfrage": True})
    return aus


@_unter_sperre
def erwartet_vermerken(name: str, **felder) -> None:
    """Zustand eines erwarteten Namens nachführen (letzter Versuch, Auftrags-Id). Eigene Anfragen stehen nicht in der Datei:
    sie bekommen hier einen Eintrag, damit der Takt gilt."""
    liste = [e for e in _lesen("erwartet.json", []) if isinstance(e, dict)]
    k = register.schluessel(name or "")
    for e in liste:
        if register.schluessel(e.get("name", "")) == k:
            e.update(felder)
            break
    else:
        liste.append({"name": name, "gelegt": time.time(), "zuletzt": 0, "auftrag": "", **felder})
    _schreiben("erwartet.json", liste[-30:])


@_unter_sperre
def erwartet_erledigen(name: str) -> None:
    k = register.schluessel(name or "")
    _schreiben("erwartet.json", [e for e in _lesen("erwartet.json", [])
                                 if isinstance(e, dict) and register.schluessel(e.get("name", "")) != k])


SCHON_BEFREUNDET = "Schon befreundet"      # Anfang des Verwurf-Grundes im Helfer (`snap_helfer.SCHON_BEFREUNDET`)


@_unter_sperre
def anfragen_schon_befreundet() -> list[dict]:
    """Anfragen, bei denen das Handy meldet „steht schon unter Friends & Groups“ (09.10.2026): Es gibt nichts anzufragen,
    die Person ist längst Freund — der Vermerk gilt als angenommen. Zurück: frisch umgestellte Anfragen."""
    try:
        zeilen = (_ordner() / "ergebnisse.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    zeiten: dict[str, float] = {}
    for zeile in zeilen:
        try:
            e = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("art") == "anfrage" and e.get("ergebnis") == "verworfen" \
                and str(e.get("grund", "")).startswith(SCHON_BEFREUNDET) and isinstance(e.get("zeit"), (int, float)):
            k = register.schluessel(e.get("empfaenger", ""))
            zeiten[k] = max(zeiten.get(k, 0.0), e["zeit"])
    liste, neu = anfragen(), []
    for a in liste:
        if a.get("zustand") == "ausstehend" \
                and zeiten.get(register.schluessel(a.get("snapchat", "")), 0.0) > float(a.get("gelegt", 0) or 0):
            a["zustand"] = "angenommen"
            neu.append(dict(a))
    if neu:
        _schreiben("anfragen.json", liste)
    return neu


@_unter_sperre
def anfrage_ergebnisse_spiegeln() -> list[tuple[str, str]]:
    """Trägt verworfene/aufgegebene Helfer-Ergebnisse in die Vermerke ein (03.10.2026):
    Sonst stünde ewig „wartet auf Annahme“, obwohl nichts mehr kommt — und ein später
    gleichnamiger Fremder würde als Annahme gemeldet. Zurück: frisch umgestellte
    (Snapchat-Name, Grund) — die Wache pusht darüber genau einmal."""
    try:
        zeilen = (_ordner() / "ergebnisse.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    fehler: dict[str, tuple[float, str]] = {}
    for zeile in zeilen:
        try:
            e = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("art") == "anfrage" \
                and e.get("ergebnis") in ("verworfen", "aufgegeben") \
                and isinstance(e.get("zeit"), (int, float)):
            k = register.schluessel(e.get("empfaenger", ""))
            if k not in fehler or e["zeit"] > fehler[k][0]:
                fehler[k] = (e["zeit"], str(e.get("grund", ""))[:200])
    if not fehler:
        return []
    liste, neu = anfragen(), []
    for a in liste:
        k = register.schluessel(a.get("snapchat", ""))
        # Nur Fehler NACH dem Vermerk zählen — alte Zeilen dürfen eine erneute
        # Anfrage nicht sofort wieder kippen (Review 03.10.2026).
        # „Kein Hinzufügen-Knopf“ kippt nie (04.10.2026, Kontakt A-Befund): Nach einem
        # Tap ohne Bestätigung heißt fehlender Knopf oft „schon raus“ — der
        # Handy-Befund (normale Chatzeile) zählt mehr als die Vermutung.
        if a.get("zustand") == "ausstehend" and k in fehler \
                and fehler[k][0] > float(a.get("gelegt", 0) or 0) \
                and "hinzufügen-knopf" not in fehler[k][1].lower() \
                and not fehler[k][1].startswith(SCHON_BEFREUNDET):
            a["zustand"] = "gescheitert"
            a["grund"] = fehler[k][1]
            neu.append((a["snapchat"], fehler[k][1]))
    if neu:
        _schreiben("anfragen.json", liste)
    return neu


# ---------- Handy-Suche (Kandidaten für „Füge X hinzu“, 04.10.2026) ----------

SUCH_GILT_S = 1800  # so lange darf Eike mit 1–5 antworten, danach neu suchen
SUCH_MAX = 5        # mehr zeigt Friday nie an
SUCHERGEBNIS_MAX = 20  # so viele Suchbegriffe merkt sich die Ablage


def suche_schluessel(begriff: str) -> str:
    """Normierter Ablageschlüssel eines Suchbegriffs (dieselbe Regel wie Treffer)."""
    return register.schluessel(begriff or "")


def suchergebnis_schreiben(begriff: str, kandidaten: list[dict], ohne_knopf: int = 0) -> dict:
    """Legt das Handy-Ergebnis einer Suche ab: bis `SUCH_MAX` Einträge
    {titel, name, grund} (nur Zeilen MIT Add-Knopf), dazu wie viele Zeilen
    keinen Knopf hatten. Gibt den Eintrag zurück."""
    name = (begriff or "").strip()
    if not name:
        raise SnapchatFehler("Wonach soll das Handy suchen? Sag mir einen Namen.")
    eintrag = {"begriff": name, "zeit": time.time(),
               "kandidaten": [{k: str(v or "")[:80] for k, v in k.items() if k in ("titel", "name", "grund")}
                               for k in (kandidaten or [])][:SUCH_MAX],
               "ohne_knopf": int(ohne_knopf or 0), "gemeldet": False}
    alle = _lesen("suchergebnisse.json", {})
    alle = alle if isinstance(alle, dict) else {}
    alle[suche_schluessel(name)] = eintrag
    _schreiben("suchergebnisse.json", dict(list(alle.items())[-SUCHERGEBNIS_MAX:]))
    return eintrag


def suchauftrag_fuer(begriff: str) -> dict | None:
    """Der noch offene Suchauftrag für genau diesen Begriff (laufend oder wartend), sonst None.
    Damit legt ein erneuter `kontakt_anfragen`-Aufruf keine zweite Handy-Suche an — jede Suche meldet
    sich später als eigener Push „Suche fertig“ (30.10.2026: mehrere Meldungen für denselben Namen)."""
    k = suche_schluessel(begriff)
    dateien = [_ordner() / "auftrag.json", *sorted(_schlange().glob("*.json"))]
    for datei in dateien:
        try:
            eintrag = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(eintrag, dict) and eintrag.get("art") == "suche" \
                and suche_schluessel(str(eintrag.get("empfaenger", ""))) == k:
            return eintrag
    return None


def suchergebnis_gemeldet(begriff: str) -> None:
    """Push zu dieser Suche ist raus — überlebt Neustarts (04.10.2026: der reine
    RAM-Merker schickte nach jedem Rebuild alles erneut)."""
    alle = _lesen("suchergebnisse.json", {})
    if not isinstance(alle, dict):
        return
    k = suche_schluessel(begriff)
    if isinstance(alle.get(k), dict):
        alle[k]["gemeldet"] = True
        _schreiben("suchergebnisse.json", alle)


def suchergebnisse() -> list[dict]:
    """Alle abgelegten Suchergebnisse, neueste zuerst."""
    alle = _lesen("suchergebnisse.json", {})
    if not isinstance(alle, dict):
        return []
    return sorted((e for e in alle.values() if isinstance(e, dict)),
                  key=lambda e: float(e.get("zeit", 0) or 0), reverse=True)


def suchergebnis_lesen(begriff: str = "") -> dict | None:
    """Das frische Ergebnis zu `begriff` — ohne Angabe das neueste. Abgelaufenes
    (`SUCH_GILT_S`) zählt nicht (None): Die Liste auf dem Handy kann sich ändern."""
    if (begriff or "").strip():
        alle = _lesen("suchergebnisse.json", {})
        alle = alle if isinstance(alle, dict) else {}
        schluessel = suche_schluessel(begriff)
        eintrag = alle.get(schluessel)
        if isinstance(eintrag, dict):
            kandidaten = [eintrag]
        else:
            # 11.10.2026 (Torge-Fall): Eike sagt „Torge“, die Suche lief als „Kontakt E“ — das Modell
            # reicht den Kurznamen durch, der exakte Schlüssel verfehlt, 1–5 scheitert endlos.
            kandidaten = sorted((e for k, e in alle.items()
                                 if isinstance(e, dict) and schluessel and (schluessel in k or k in schluessel)),
                                key=lambda e: float(e.get("zeit", 0) or 0), reverse=True)
    else:
        kandidaten = suchergebnisse()
    for e in kandidaten:
        try:
            alter = time.time() - float(e.get("zeit", 0) or 0)
        except (TypeError, ValueError):
            continue
        if 0 <= alter <= SUCH_GILT_S:
            return e
    return None


def _vor(sekunden: float) -> str:
    minuten = int(sekunden // 60)
    if minuten < 1:
        return "gerade eben"
    if minuten < 60:
        return f"vor {minuten} Min."
    return f"vor {minuten // 60} Std."


def anfragen_kontext() -> str:
    """Block für den System-Prompt: Stand aller vermerkten Kontakt-Anfragen (03.10.2026, Kontakt A-Fall):
    ausstehend/angenommen/gescheitert mit Grund — damit Friday nie „kein Zugriff“ behauptet
    und nie „warten wir ab“ sagt, wenn die Anfrage längst gescheitert ist. Leer, wenn nichts."""
    liste = anfragen()
    if not liste:
        return ""
    zeilen = []
    for a in sorted(liste, key=lambda x: float(x.get("gelegt", 0) or 0)):
        name = str(a.get("snapchat", "")).strip() or "?"
        zustand = str(a.get("zustand", "ausstehend"))
        if zustand == "ausstehend":
            zeilen.append(f"- {name}: wartet auf Annahme (raus, eigenes Handy-Konto, Annahme erkenne ich selbst)")
        elif zustand == "angenommen":
            zeilen.append(f"- {name}: angenommen — als Kontakt angelegt (oder von Eike unter Extras → Kontakte), du kannst ihr schreiben")
        elif zustand == "gescheitert":
            grund = str(a.get("grund", "")).strip()[:160]
            zeilen.append(f"- {name}: gescheitert ({grund}) — einfach erneut beauftragen, dann versucht es das Handy neu")
        else:
            zeilen.append(f"- {name}: {zustand}")
    return ("\n\n[KONTAKTANFRAGEN] Stand deiner Anfragen über dein eigenes Handy-Konto "
            "(nicht Eikes Account, Annahme erkennst du selbst):\n" + "\n".join(zeilen))


def gesendet_kontext() -> str:
    """Block für den System-Prompt: was Friday Eike in den letzten `GESENDET_STUNDEN` Stunden als Snap geschickt hat,
    neueste zuerst. Leer, wenn nichts."""
    jetzt = time.time()
    eintraege = [e for e in _lesen("gesendet.json", [])
                 if isinstance(e, dict) and 0 <= jetzt - e.get("zeit", 0) < GESENDET_STUNDEN * 3600]
    if not eintraege:
        return anfragen_kontext()
    zeilen = [f"- {_vor(jetzt - e['zeit'])}: {str(e.get('text', '')).strip('[]. ').replace(' gerade ', ' ')}" for e in reversed(eintraege)]
    return ("\n\n[GESENDETE SNAPS] Das hast du Eike in den letzten Stunden geschickt (neueste zuerst). Du weißt es, "
            "auch wenn er den Snap erst jetzt öffnet oder darauf antwortet:\n" + "\n".join(zeilen)) + anfragen_kontext()


# Dieselben Stunden wie der Helfer (`host-helfer/snap_helfer.py`: WACH_AB/WACH_BIS) — der Helfer läuft außerhalb
# des Containers, eine gemeinsame Stelle gibt es nicht. Bei Änderung dort hier nachziehen.
WACH_AB_H, WACH_BIS_H = 7, 23
HELFER_STILL_S = 1800  # so lange ohne Lebenszeichen gilt er als still (wie `snapchat_wache.HELFER_STILL_NACH_S`)


def helfer_lage() -> str:
    """'wach' | 'nacht' | 'pausiert' | 'still': Kommt ein gerade gelegter Auftrag jetzt raus — oder liegt er?
    Für ehrliche Antworten statt „ist raus“, wenn der Helfer nachts schläft oder sich nicht meldet
    (02.10.2026: Der Kontakt C-Auftrag lag die ganze Nacht auf dem Sendeplatz)."""
    from datetime import time as tageszeit

    if not wachzeiten_rund_um_die_uhr() \
            and not (tageszeit(WACH_AB_H, 0) <= config.jetzt().timetz().replace(tzinfo=None) <= tageszeit(WACH_BIS_H, 0)):
        return "nacht"
    z = zustand()
    if (z.get("pausiert_bis") or 0) > time.time():
        return "pausiert"        # der Schutzschalter des Helfers (5 Fehler in Folge → 10 min Pause)
    if z.get("zeit") and time.time() - z["zeit"] > HELFER_STILL_S:
        return "still"
    return "wach"


def wachzeiten_rund_um_die_uhr() -> bool:
    """Will Eike den Helfer auch nachts (Freigaben-Seite)? Datei wie `kontakte.json`: der Container
    schreibt, der Helfer liest — ohne Neustart."""
    d = _lesen("wachzeiten.json", {})
    return bool(isinstance(d, dict) and d.get("rund_um_die_uhr"))


def wachzeiten_schreiben(rund_um_die_uhr: bool) -> bool:
    _schreiben("wachzeiten.json", {"rund_um_die_uhr": bool(rund_um_die_uhr), "zeit": time.time()})
    return bool(rund_um_die_uhr)


# ---------- Zustand ----------

def zustand() -> dict:
    """Was der Helfer zuletzt gemeldet hat. Meldet er sich nie, ist das ehrlich sichtbar."""
    z = _lesen("zustand.json", None)
    if not isinstance(z, dict):
        return {"verbunden": False, "zeit": None, "letzter_fehler": "Der Helfer hat sich noch nie gemeldet."}
    return z


# ---------- Eingang ----------

_BILD_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg)$")   # png: Screencap-Bilder, jpg: Original aus der Kamerarolle (Ticketannahme)


def bild_lesen(name: str) -> bytes | None:
    """Ein vom Helfer abgelegtes Snap-Bild. Nur Namen aus 32 Hex-Zeichen — nie ein Pfad."""
    if not isinstance(name, str) or not _BILD_NAME.match(name):
        return None
    try:
        return (_ordner() / "bilder" / name).read_bytes()
    except OSError:
        return None


def bild_als_jpeg(png: bytes, qualitaet: int = 90) -> bytes:
    """Das vom Helfer abgelegte PNG als JPEG. Ein Foto als PNG ist mehrere MB groß (und über
    `bilder.MAX_BYTES`, dann wurde es 29.09.2026 stillschweigend NICHT gespeichert); als JPEG bleibt es
    unter 1 MB. Ohne Alphakanal, denn JPEG kennt keinen."""
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(png)) as bild:
        aus = BytesIO()
        bild.convert("RGB").save(aus, "JPEG", quality=qualitaet, optimize=True)
        return aus.getvalue()


def bild_entfernen(name: str) -> None:
    if isinstance(name, str) and _BILD_NAME.match(name):
        (_ordner() / "bilder" / name).unlink(missing_ok=True)


# ---------- Ticket-Annahme (09.10.2026, Spec 2026-10-09-ticket-annahme-design.md) ----------

TICKET_MODI = ("aus", "trocken", "an")
_PERSON_ID = re.compile(r"^(eike|p[1-9][0-9]{0,5})$")


def ticket_modus() -> str:
    """Schalter `ticket_annahme` in `einstellungen.json`: aus (Standard) · trocken (lesen und bewerten, nichts speichern, nichts senden) · an."""
    e = _lesen("einstellungen.json", {})
    wert = e.get("ticket_annahme") if isinstance(e, dict) else None
    return wert if wert in TICKET_MODI else "aus"


def ticket_erwartet() -> set[str]:
    """Personen (Register-Id oder „eike“ im Probelauf), von denen Friday gerade ein Ticketbild erwartet. Schreiber ist später Teil 3."""
    liste = _lesen("ticket_erwartet.json", [])
    return {p for p in liste if isinstance(p, str) and _PERSON_ID.match(p)} if isinstance(liste, list) else set()


def ticket_erwartet_setzen(person: str, an: bool) -> None:
    if not isinstance(person, str) or not _PERSON_ID.match(person):
        raise ValueError("Ungültige Personen-Id für die Ticketerwartung.")
    with _sperre:
        aktuell = ticket_erwartet()
        (aktuell.add if an else aktuell.discard)(person)
        _schreiben("ticket_erwartet.json", sorted(aktuell))


def eingang_neu() -> list[dict]:
    """Ungelesene Nachrichten von Eike, älteste zuerst."""
    liste = _lesen("eingang.json", [])
    return [n for n in liste if isinstance(n, dict) and not n.get("gelesen")]


def eingang_gelesen_markieren(ids: list[str]) -> None:
    """Setzt `gelesen` für genau diese IDs — mehrfacher Aufruf mit denselben IDs ist folgenlos (Idempotenz)."""
    ids = set(ids)
    liste = _lesen("eingang.json", [])
    geaendert = False
    for n in liste:
        if isinstance(n, dict) and n.get("id") in ids and not n.get("gelesen"):
            n["gelesen"] = True
            geaendert = True
    if geaendert:
        _schreiben("eingang.json", liste)
