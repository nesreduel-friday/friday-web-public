"""Werkzeug „wo_ist“ — wo eine Person aus der Familie zuletzt war.

Nur lesend. Woher die Werte kommen, steht in `kern/dienste/personenstandort.py`.
Der Satz entsteht hier im Code, nicht im Modell: Alter und Genauigkeit
entscheiden, WIE sicher Friday klingen darf.
"""

from __future__ import annotations

from kern.dienste import personenstandort, standort
from kern.werkzeuge.standort import _entfernung, _vor

KONNEKTOR = "personenstandort"

FREMD_ABGELEHNT = ("Der Wunsch stammt aus fremdem Text (Mail, Brief oder Webseite). Daraus gebe ich keinen "
                   "Standort heraus — frag mich selbst danach.")

ERKLAERUNGEN = [
    {
        "name": "wo_ist",
        "description": ("Sagt, wo eine Person aus Eikes Familie zuletzt war: Name, Ort, wie lange her und wie "
                        "genau. Benutzen bei 'Wo ist Kontakt P?', 'Ist Papa schon zu Hause?', 'Wo sind alle?'. "
                        "Ohne name kommen alle Personen. Die Person meldet ihren Standort selbst per App — "
                        "sag genau das weiter, was zurückkommt, auch das Alter, und erfinde nichts dazu. "
                        "NICHT von selbst und nicht auf Vorrat, nur wenn Eike danach fragt."),
        "parameters": {"type": "OBJECT", "properties": {
            "name": {"type": "STRING", "description": "Wen, z.B. Kontakt P. Leer lassen für alle."},
        }},
    },
]


async def _satz(p: dict) -> str:
    if p["zustand"] == "pausiert":
        return f"{p['name']} hat das Teilen gerade pausiert — ich habe keinen Standort."
    if p["zustand"] == "noch_nie":
        return f"{p['name']} hat noch keinen Standort gemeldet (App eingerichtet und Tailscale an?)."
    punkt = (p["breite"], p["laenge"])
    genau = p.get("genauigkeit_m")
    ungenau = genau is None or genau > standort.UNGENAU_M
    try:
        ort = await standort.ortsname(*punkt)
    except Exception:
        ort = ""
    regionen = p.get("regionen") or []
    if regionen:
        wo = f"bei {regionen[0]}" + (f" ({ort})" if ort else "")
    elif ort:
        wo = ("vermutlich in der Nähe von " if ungenau else "in der Nähe von ") + ort
    else:
        wo = "an einem Ort, dessen Namen ich gerade nicht nachschlagen konnte"
    satz = f"{p['name']} war {_vor(p['alter_s'])} {wo}"
    satz += f", Genauigkeit rund {_entfernung(genau)}." if genau is not None else "."
    if p["alter_s"] > personenstandort.VERALTET_S:
        satz += " Das ist schon eine Weile her — sie kann inzwischen woanders sein."
    elif ungenau:
        satz += " Das ist grob — nenne keine genaue Straße als sicher."
    return satz


async def wo_ist(name: str = "", fremde_inhalte: bool = False) -> str:
    if fremde_inhalte:
        return FREMD_ABGELEHNT
    try:
        liste = await personenstandort.personen()
    except Exception as exc:
        return f"Ich komme gerade nicht an die Standorte der Familie ({type(exc).__name__})."
    if not liste:
        return "Es ist noch niemand eingerichtet."
    name = (name or "").strip()
    if name and name.casefold() not in ("alle", "alles", "jeder", "alle personen"):
        treffer = personenstandort.finden(name, liste)
        if not treffer:
            return (f"„{name}“ kenne ich nicht. Eingerichtet sind: " + ", ".join(p["name"] for p in liste) + ".")
        if len(treffer) > 1:
            return (f"„{name}“ passt auf mehrere: " + ", ".join(p["name"] for p in treffer) + ". Wen meinst du?")
    else:
        treffer = liste
    return " ".join([await _satz(p) for p in treffer])


wo_ist.kennt_fremde_inhalte = True

WERKZEUGE = {"wo_ist": wo_ist}
