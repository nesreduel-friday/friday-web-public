"""Werkzeug: Module (Hebel) einer Kontakt-Person umlegen — nur Eike (14.10.2026).

Nie nach Fremdtext (`NICHT_NACH_FREMDTEXT`, der Katalog sperrt), nie für Fremde (nicht in `rechte.FREMDEN_ERLAUBT`). Die Person
kommt aus dem Register; ein geratener Name ändert nichts. Garantie „an“ nur mit Garantie-Profil. Jede Änderung steht im Protokoll
(`von: friday`)."""

from __future__ import annotations

from kern.ehrlichkeit.wirkung import wirkt
from kern.personen import PersonenFehler, module, register

NICHTS = "Nichts geändert. {grund}"
ZUSTAND = {"an": True, "ein": True, "aus": False}

ERKLAERUNGEN = [
    {"name": "modul_schalten",
     "description": ("Legt einen Hebel (Modul) einer Kontakt-Person um: plan (aus|frei|voll), garantie (auto|an|aus), "
                     "medien, snap_wuensche, flammen, ueberwachung (an|aus). Nur wenn Eike es in dieser Nachricht ausdrücklich verlangt. "
                     "Die Person muss genau so in seinen Kontakten stehen; bei Zweifel frag zurück."),
     "parameters": {"type": "OBJECT", "properties": {
         "person": {"type": "STRING", "description": "Vor- und Nachname, wie in den Kontakten"},
         "modul": {"type": "STRING", "description": "plan, garantie, medien, snap_wuensche, flammen oder ueberwachung"},
         "wert": {"type": "STRING", "description": "an|aus, bei plan aus|frei|voll, bei garantie auto|an|aus"}},
         "required": ["person", "modul", "wert"]}},
]


def _finden(name: str) -> dict | None:
    k = register.schluessel(name)
    treffer = [p for p in register.liste() if register.schluessel(p["name"]) == k]
    return treffer[0] if k and len(treffer) == 1 else None


@wirkt("skill", ok=("Erledigt:",), detail=lambda a, t: f"{a.get('modul')} → {a.get('wert')}")
def modul_schalten(person: str = "", modul: str = "", wert: str = "") -> str:
    modul, wert = str(modul or "").strip().lower(), str(wert or "").strip().lower()
    p = _finden(str(person or ""))
    if p is None:
        return NICHTS.format(grund="Frag Eike, wen er genau meint: Diese Person gibt es so nicht in den Kontakten "
                                   "(oder der Name ist nicht eindeutig).")
    if modul not in module.MODULE:
        return NICHTS.format(grund=f"Ein Modul „{modul}“ gibt es nicht (plan, garantie, medien, snap_wuensche, flammen, ueberwachung).")
    if modul == "plan":
        neu = wert
    elif modul == "garantie":
        neu = wert
    elif wert in ZUSTAND:
        neu = ZUSTAND[wert]
    else:
        return NICHTS.format(grund="Der Wert muss „an“ oder „aus“ sein.")
    try:
        register.aendern(p["id"], module={modul: neu}, von="friday")
    except PersonenFehler as exc:
        return NICHTS.format(grund=str(exc))
    return f"Erledigt: {modul} bei {p['name']} steht jetzt auf {wert}."


NICHT_NACH_FREMDTEXT = {"modul_schalten"}
WERKZEUGE = {"modul_schalten": modul_schalten}
