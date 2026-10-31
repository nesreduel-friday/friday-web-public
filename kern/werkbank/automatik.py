"""Werkbank-Automatik (28.10.2026): Ist Eikes Hebel an, arbeitet Friday die Vorhaben-Liste von selbst ab.

Bewusste Ausnahme von „Gestartet wird nur auf Eikes Wort“ — erlaubt nur durch den Hebel, den allein Eikes App über
`POST /api/werkbank/einstellungen` umlegt (Origin-Prüfung in `main.frisch_halten`, kein Werkzeug). Grenzen:
- Standard aus. `werkbank_auto` aus|an, `werkbank_auto_backend` claude|opencode in `data/einstellungen.json`
  (dieselbe Sperre und dasselbe atomare Schreiben wie `denken_backend`).
- Gestartet wird über denselben Weg wie Knopf und Werkzeug (`starten.starten_dict(..., auto=True)`); dort sitzen alle
  Prüfungen, dazu das Tageslimit `AUTO_TAGESLIMIT` nur für automatische Starts. Manuelle Starts verbrauchen es nicht.
- Nur das älteste Vorhaben mit Status vorgeschlagen/freigegeben, nicht aus fremdem Text, Quelle Gespräch oder Störung
  (nie Denkrunde), bei OpenCode nur Art test/idee; nur wenn gerade nichts läuft. Ein Fehlstart (Helfer weg/stumm) zählt
  nicht und wird beim nächsten Takt neu versucht; ein gescheiterter Lauf (`fehler`) ist nicht startbar, also kein Dauerfeuer.
- Live bringt die Automatik nichts: das geht weiter nur per Knopf.
"""

from __future__ import annotations

import asyncio
import logging

from kern.werkbank import opencode, selbst, speicher, starten

log = logging.getLogger("friday.werkbank")

AUTO = ("aus", "an")
BACKENDS = starten.BACKENDS
AUTO_TAGESLIMIT = starten.AUTO_TAGESLIMIT
TAKT_S = 300


def einstellungen() -> dict:
    from kern.planer import briefing
    d = briefing._lesen(briefing._einstellungsdatei())
    auto, backend = d.get("werkbank_auto"), d.get("werkbank_auto_backend")
    return {"werkbank_auto": auto if auto in AUTO else "aus",
            "werkbank_auto_backend": backend if backend in BACKENDS else "claude"}


def einstellen(auto: str | None = None, backend: str | None = None) -> dict:
    """Nur für die Route (Eikes Hebel in der App)."""
    if auto is not None and auto not in AUTO:
        raise ValueError(f"Unbekannter Wert: {auto}")
    if backend is not None and backend not in BACKENDS:
        raise ValueError(f"Unbekanntes Backend: {backend}")
    from kern.planer import briefing
    with briefing._sperre:
        d = briefing._lesen(briefing._einstellungsdatei())
        if auto is not None:
            d["werkbank_auto"] = auto
        if backend is not None:
            d["werkbank_auto_backend"] = backend
        briefing._schreiben(briefing._einstellungsdatei(), d)
    return einstellungen()


def _sandbox_da() -> bool:
    """Grobe Probe für OpenCode: Den Austauschordner legt Eike beim Einrichten an (AGENTS.md, Betrieb)."""
    try:
        return opencode._wurzel().is_dir()
    except OSError:
        return False


def backend_fuer(v: dict, eingestellt: str) -> str:
    """Welches Backend dieses Vorhaben baut (30.10.2026).

    OpenCode läuft in der Sandbox und ist für Erkundung, Testläufe und Ideen gedacht — seine Ergebnisse
    kommen nie live (das können nur Claude und DeepSeek). Wer OpenCode einstellt, meint also die billige
    Werkbank, nicht den Verzicht auf echte Bauten: Für Wunsch und Fehler springt DeepSeek ein, statt dass
    die Automatik still nichts tut."""
    if eingestellt != "opencode":
        return eingestellt
    if v.get("art") in starten.OPENCODE_ARTEN and _sandbox_da():
        return "opencode"
    return "deepseek"


def kandidat(fach: speicher.Fach, eingestellt: str) -> tuple[dict | None, str]:
    """(ältestes Vorhaben, das die Automatik nehmen darf; Backend dafür). (None, "") = keins."""
    for v in fach.offene():
        if v["status"] in starten.STARTBAR and not v["fremd"] and v["quelle"] in starten.auto_quellen():
            return v, backend_fuer(v, eingestellt)
    return None, ""


def _melden(v: dict) -> None:
    """Eine kurze Meldung beim automatischen Start; das Ergebnis meldet `lauf` wie immer (gleicher Push-Tag → ersetzt sie)."""
    try:
        from kern.dienste import benachrichtigungen
        benachrichtigungen.senden("werkbank", "Friday: automatisch angefangen",
                                  f"Ich habe automatisch angefangen: „{str(v.get('titel') or '')[:60]}“.",
                                  url="/#studio/hphome", tag=f"werkbank-{v['id']}")
    except Exception:                                  # noqa: BLE001
        log.exception("Werkbank: Meldung zum automatischen Start von %s nicht gesendet", v.get("id"))


def einmal(fach: speicher.Fach | None = None) -> dict | None:
    """Ein Takt. None = nichts versucht; sonst das Ergebnis von `starten_dict`."""
    fach = fach or speicher.standard()
    selbst.einmal(fach)                                # Hebel „selbst übernehmen“: fertige, grüne Ergebnisse (eigene Grenzen)
    e = einstellungen()
    if e["werkbank_auto"] != "an":
        return None
    if fach.laufende() or fach.auto_heute() >= AUTO_TAGESLIMIT:
        return None
    v, backend = kandidat(fach, e["werkbank_auto_backend"])
    if not v:
        return None
    d = starten.starten_dict(v["id"], backend, auto=True)
    if d["ok"]:
        log.info("Werkbank: Automatik hat Vorhaben %s gestartet (%s)", v["id"], backend)
        _melden(v)
    else:
        log.info("Werkbank: Automatik konnte Vorhaben %s nicht starten: %s", v["id"], d["text"][:200])
    return d


async def dauerlauf() -> None:
    while True:
        try:
            await asyncio.to_thread(einmal)
        except Exception:                              # noqa: BLE001
            log.exception("Werkbank: Automatik-Takt fehlgeschlagen")
        await asyncio.sleep(TAKT_S)
