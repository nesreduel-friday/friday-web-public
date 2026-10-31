"""Das Personen-Register: wer außer Eike mit Friday schreiben darf, mit Persona und Rechten.

`data/personen.json` (600, atomar geschrieben). Profil 0 ist Eike und steht NICHT hier — er hat alles wie
bisher. Personen werden pausiert, nie gelöscht (das Gedächtnis löscht nie), Ids werden nie wiederverwendet.
Eine kaputte Datei liest sich als leer, wird aber nie überschrieben: Schreiben scheitert dann mit einem Fehler,
statt die Personen zu verlieren."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone

from kern import config
from kern.personen import PersonenFehler, module, rechte

DATEI = "personen.json"
NAME_MAX = 40
PERSONA_MAX = 2000
ANZEIGE_MAX = 80
# Anzeigenamen, die nie einer Person gehören: „ME“ ist das Label eigener Nachrichten im Snapchat-Chat,
# „Eike“ sein eigener Kontakt. Sonst würde Eikes Text einer Person zugeschrieben.
RESERVIERT = {"eike", "me", "friday"}
_FELDER = {"name", "snapchat", "persona", "werkzeuge", "proaktiv", "pausiert", "senden_erlaubt",
           "quellen", "module"}
_QUELLEN = ("whatsapp", "garantie")  # Verweise in andere Töpfe (04.10.2026): Chat-id, Profil-id


def _pruefe_quellen(quellen) -> dict:
    """Nur die zwei bekannten Quellen, sonst nichts — ein fremder Schlüssel öffnet keine Ablage."""
    if not isinstance(quellen, dict):
        raise PersonenFehler("Die Quellen müssen ein Objekt sein.")
    raus = {}
    for k, v in quellen.items():
        if k not in _QUELLEN:
            raise PersonenFehler(f"Unbekannte Quelle „{k}“.")
        if k == "whatsapp":
            if not isinstance(v, str) or not v.strip():
                raise PersonenFehler("Der WhatsApp-Chat fehlt.")
            raus[k] = v.strip()
        else:
            try:
                raus[k] = int(v)
            except (TypeError, ValueError):
                raise PersonenFehler("Das Garantie-Profil fehlt.") from None
    return raus
_sperre = threading.RLock()


def _pfad():
    return config.daten_ordner() / DATEI


def _lesen() -> dict:
    """Der Inhalt; fehlt die Datei, ein leeres Register. Kaputt: PersonenFehler beim Schreiben (siehe `_speichern`)."""
    try:
        d = json.loads(_pfad().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"naechste": 1, "personen": {}}
    except (OSError, ValueError):
        return {"kaputt": True, "naechste": 1, "personen": {}}
    if not isinstance(d, dict) or not isinstance(d.get("personen"), dict):
        return {"kaputt": True, "naechste": 1, "personen": {}}
    d.setdefault("naechste", len(d["personen"]) + 1)
    return d


def _speichern(d: dict) -> None:
    if d.get("kaputt"):
        raise PersonenFehler("Das Personen-Register ist unlesbar (data/personen.json). Ich ändere nichts, bis es repariert ist.")
    ziel = _pfad()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.chmod(neu, 0o600)
    os.replace(neu, ziel)


_UNSICHTBAR = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}  # Steuer-, Format- (Zero-Width, Bidi), Zeilen-/Absatztrenner


def _unsichtbar(c: str) -> bool:
    # Variation Selectors (U+FE00–FE0F, U+E0100–E01EF) sind Markierungen (Mn), ändern aber nichts Sichtbares.
    return unicodedata.category(c) in _UNSICHTBAR or "\ufe00" <= c <= "\ufe0f" or "\U000e0100" <= c <= "\U000e01ef"


def _sichtbar(s: str, zeilen: bool = False) -> str:
    """Ohne unsichtbare und Zeilen trennende Zeichen; der Zeilenumbruch bleibt nur, wo gewünscht (Persona)."""
    return "".join(c for c in s if not (_unsichtbar(c) and not (zeilen and c == "\n")))


def _text(wert, maximal: int, zeilen: bool = False) -> str:
    """Ohne Steuer- und Formatzeichen (Zeilenumbruch nur, wo gewünscht), getrimmt, gekürzt. Name und Persona gehen
    später in den Prompt: Ein Zeilentrenner darin wäre eine eigene Zeile im Prompt."""
    return _sichtbar(wert if isinstance(wert, str) else "", zeilen).strip()[:maximal]


def schluessel(anzeige: str) -> str:
    """Vergleichsform eines Anzeigenamens: NFKC (Vollbreite u. ä.), ohne unsichtbare Zeichen, klein, Leerzeichen
    zusammengefasst. Nur diese Form entscheidet, ob zwei Namen „derselbe“ sind oder ein Name reserviert ist."""
    s = _sichtbar(unicodedata.normalize("NFKC", anzeige or ""))
    return " ".join(s.split()).casefold()


def _pruefe_name(wert) -> str:
    """Der Name ist der Sprecher in Gesprächen: „Eike“, „Friday“ und „ME“ dürften nie einer Person gehören."""
    name = _text(wert, NAME_MAX)
    if not name:
        raise PersonenFehler("Der Name fehlt.")
    if schluessel(name) in RESERVIERT:
        raise PersonenFehler(f"„{name}“ kann keiner Person gehören (das bin ich oder Eike selbst).")
    return name


def _pruefe_snapchat(d: dict, pid: str | None, anzeige: str) -> None:
    if not anzeige:
        return
    k = schluessel(anzeige)
    if k in RESERVIERT:
        raise PersonenFehler(f"„{anzeige}“ kann keiner Person gehören (das bin ich oder Eike selbst).")
    for andere, p in d["personen"].items():
        if andere != pid and schluessel(p.get("kanaele", {}).get("snapchat", "")) == k:
            raise PersonenFehler(f"„{anzeige}“ gehört schon {p.get('name', andere)}.")


def _hoechste_bekannte_id() -> int:
    """Die größte Register-Nummer (`p7` → 7), die Gespräche oder Bilder schon tragen. Kurz und schreibgeschützt gelesen
    und wieder geschlossen (kein dauerhafter Datenbank-Handle für eine seltene Prüfung)."""
    db = config.daten_ordner() / "friday.db"
    if not db.exists():
        return 0
    nummern = [0]
    try:
        with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)) as c:
            for tabelle in ("gespraeche", "bilder"):
                try:
                    for (person,) in c.execute(f"SELECT person FROM {tabelle} WHERE person IS NOT NULL"):
                        if re.fullmatch(r"p[0-9]+", person or ""):
                            nummern.append(int(person[1:]))
                except sqlite3.OperationalError:
                    continue  # Tabelle oder Spalte gibt es (noch) nicht
    except sqlite3.Error:
        return 0
    return max(nummern)


def _pruefe_identitaet(d: dict, graph_name: str) -> None:
    """Ein Eintrag je bekannter Person: Erinnerungen und Bilder hängen an `graph_name`. Ältere Einträge ohne das
    Feld zählen mit ihrem Namen."""
    k = schluessel(graph_name)
    if k in RESERVIERT:
        raise PersonenFehler(f"„{graph_name}“ ist kein Kontakt (das bin ich oder Eike selbst).")
    for p in d["personen"].values():
        if schluessel(p.get("graph_name") or p.get("name", "")) == k:
            raise PersonenFehler(f"„{graph_name}“ ist schon als Kontakt angelegt ({p.get('name', '')}).")


def liste() -> list[dict]:
    d = _lesen()
    return [dict(p) for _, p in sorted(d["personen"].items(), key=lambda kv: int(kv[0][1:]))]


def holen(pid: str) -> dict | None:
    p = _lesen()["personen"].get(pid)
    return dict(p) if p else None


def anlegen(name: str, snapchat: str = "", persona: str = "", werkzeuge: list[str] | None = None,
            proaktiv: bool = False, graph_name: str = "") -> dict:
    name = _pruefe_name(name)
    graph_name = _text(graph_name, NAME_MAX) or name
    anzeige = _text(snapchat, ANZEIGE_MAX)
    liste_ = rechte.pruefen(list(rechte.STANDARD) if werkzeuge is None else werkzeuge)
    with _sperre:
        d = _lesen()
        _pruefe_snapchat(d, None, anzeige)
        _pruefe_identitaet(d, graph_name)
        # Nie unter einer Id, die Protokoll oder Bilder schon kennen — sonst erbte eine neue Person nach einem
        # Verlust der Registerdatei die Gruppe und Gespräche einer früheren.
        nummer = max(int(d["naechste"]), _hoechste_bekannte_id() + 1)
        pid = f"p{nummer}"
        d["naechste"] = nummer + 1
        person = {"id": pid, "name": name, "graph_name": graph_name, "kanaele": {"snapchat": anzeige} if anzeige else {},
                  "persona": _text(persona, PERSONA_MAX, zeilen=True), "werkzeuge": liste_,
                  "module": dict(module.STANDARD),
                   "proaktiv": bool(proaktiv), "pausiert": False, "senden_erlaubt": False, "quellen": {},
                  "angelegt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        d["personen"][pid] = person
        _speichern(d)
        from kern.personen import unbekannt  # hier, weil `unbekannt` selbst `register` importiert
        unbekannt.entfernen("snapchat", anzeige)
        return dict(person)


def aendern(pid: str, **felder) -> dict:
    von = felder.pop("von", "app")          # Herkunft einer Modul-Änderung fürs Protokoll
    if von not in ("app", "friday", "migration"):
        raise PersonenFehler("Unbekannte Herkunft der Änderung.")
    unbekannt_ = set(felder) - _FELDER
    if unbekannt_:
        raise PersonenFehler(f"Nicht änderbar: {', '.join(sorted(unbekannt_))}.")
    with _sperre:
        d = _lesen()
        p = d["personen"].get(pid)
        if not p:
            raise PersonenFehler("Diese Person gibt es nicht.")
        if "name" in felder:
            p["name"] = _pruefe_name(felder["name"])
        if "snapchat" in felder:
            anzeige = _text(felder["snapchat"], ANZEIGE_MAX)
            _pruefe_snapchat(d, pid, anzeige)
            p["kanaele"] = {"snapchat": anzeige} if anzeige else {}
        if "persona" in felder:
            p["persona"] = _text(felder["persona"], PERSONA_MAX, zeilen=True)
        if "werkzeuge" in felder:
            p["werkzeuge"] = rechte.pruefen(felder["werkzeuge"])
            # Ältere Schreiber nennen Plan-Werkzeuge in `werkzeuge`: Das setzt die Plan-Stufe (ohne Plan-Werkzeuge bleibt sie).
            if "module" not in felder and isinstance(p.get("module"), dict) \
                    and set(p["werkzeuge"]) & set(module.PLAN_WERKZEUGE["voll"]):
                geplant = module.von_person({"werkzeuge": p["werkzeuge"]})["plan"]
                felder = {**felder, "module": {"plan": geplant}}
        for schalter in ("proaktiv", "pausiert"):
            if schalter in felder:
                p[schalter] = bool(felder[schalter])
        if "senden_erlaubt" in felder:
            p["senden_erlaubt"] = bool(felder["senden_erlaubt"])
        if "quellen" in felder:
            p["quellen"] = _pruefe_quellen(felder["quellen"])
        geaendert: list[tuple[str, object, object]] = []
        if "module" in felder:
            alt = module.von_person(p)
            neu = module.zusammenfuehren(p, felder["module"])
            p["module"] = neu
            geaendert = [(k, alt[k], neu[k]) for k in module.MODULE if alt[k] != neu[k]]
        if "module" in p:                      # `werkzeuge` immer aus den Modulen halten (ältere Leser lesen es noch)
            p["werkzeuge"] = module.werkzeuge_fuer(p)
        _speichern(d)
        for k, a_, n_ in geaendert:
            module.protokollieren(pid, k, a_, n_, von)
        return dict(p)


def nach_kanal(kanal: str, anzeige: str) -> dict | None:
    """Die Person zu einem Anzeigenamen (auch pausierte — wer antwortet, prüft `pausiert` selbst)."""
    k = schluessel(anzeige)
    if not k or kanal != "snapchat":
        return None
    for p in _lesen()["personen"].values():
        if schluessel(p.get("kanaele", {}).get("snapchat", "")) == k:
            return dict(p)
    return None


def freigaben(pid: str) -> list[dict]:
    p = _lesen()["personen"].get(pid)
    return [dict(f) for f in (p or {}).get("freigaben", [])]


def freigabe_anlegen(pid: str, text: str, anlass: str = "") -> dict:
    """Reserviert eine Freigabe (Status `wartet`) — die Id ist Teil des Episodennamens im Graphen. Ids zählen hoch und
    werden nie wiederverwendet, denn Freigaben werden nie entfernt (Widerruf ändert nur den Status)."""
    text = _text(text, PERSONA_MAX, zeilen=True)
    if not text:
        raise PersonenFehler("Der Text der Freigabe fehlt.")
    with _sperre:
        d = _lesen()
        p = d["personen"].get(pid)
        if not p:
            raise PersonenFehler("Diese Person gibt es nicht.")
        alle = p.setdefault("freigaben", [])
        fid = f"f{len(alle) + 1}"
        f = {"id": fid, "text": text, "anlass": _text(anlass, 200), "episode": f"freigabe-{pid}-{fid}",
             "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": "wartet", "geloescht": False}
        alle.append(f)
        _speichern(d)
        return dict(f)


def freigabe_setzen(pid: str, fid: str, **felder) -> dict:
    erlaubt = {"status", "geloescht", "widerrufen_zeit", "loeschen"}
    if set(felder) - erlaubt:
        raise PersonenFehler("Nicht änderbar.")
    with _sperre:
        d = _lesen()
        f = next((x for x in (d["personen"].get(pid) or {}).get("freigaben", []) if x["id"] == fid), None)
        if not f:
            raise PersonenFehler("Diese Freigabe gibt es nicht.")
        f.update(felder)
        _speichern(d)
        return dict(f)
