"""Die personenfähigen Primitive: die EINZIGE Liste von Werkzeugen, die ein Skill einer Person öffnen kann (Positivliste im Code).

Ein Primitiv ist klein und generisch, hängt immer an genau einer Person (die Id kommt vom Kern, `kennt_kontakt`, nie vom Modell)
und kennt nie Eikes Daten. Alles, was nach außen wirkt, deklariert Optionen mit `werte` von streng nach locker (`mit_freigabe`
vor `selbst`); der Standard ist der strengste Wert. Ein Skill-Text kann hier nichts hinzufügen.

Eintragen (Schritt 2+): in `PERSONENFAEHIG` oder per `registrieren(...)` aus dem Modul des Werkzeugs; das Werkzeug selbst steht wie
jedes andere in seinem `kern/werkzeuge/*.py`-Modul (`ERKLAERUNGEN`, `WERKZEUGE`, `kennt_kontakt = True`). Zusätzlich beim Eintrag:
`GATES` nennt die Pflicht-Prüfungen, die das Primitiv selbst erzwingt (Dokumentation, nicht Konfiguration)."""

from __future__ import annotations

# name -> {"beschreibung", "optionen": {name: {"werte": [streng ... locker], "standard"}}, "gates": [Text]}
PERSONENFAEHIG: dict[str, dict] = {}


def registrieren(name: str, beschreibung: str, optionen: dict | None = None, gates: list[str] | None = None) -> None:
    opt = {}
    for o, d in (optionen or {}).items():
        werte = list(d["werte"])
        if not werte:
            raise ValueError(f"Option {o} braucht Werte.")
        opt[o] = {"werte": werte, "standard": d.get("standard", werte[0]), "beschreibung": d.get("beschreibung", ""),
                  "pruefen": d.get("pruefen")}  # pruefen(wert) -> Absage oder "" (live, z. B. Schreibrecht)
        if opt[o]["standard"] not in werte:
            raise ValueError(f"Standard von {o} nicht erlaubt.")
    PERSONENFAEHIG[name] = {"beschreibung": beschreibung, "optionen": opt, "gates": list(gates or [])}


def namen() -> frozenset[str]:
    return frozenset(PERSONENFAEHIG)
