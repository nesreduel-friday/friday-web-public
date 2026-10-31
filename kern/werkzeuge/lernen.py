"""Lernen im Gespräch: „Was kommt in der Arbeit dran?“, „was hatten wir zuletzt in Mathe?“.

Liest nur, was `kern/schule/lernen.py` schon zusammenzieht (Noten, Arbeiten-Muster, Gruppenordner,
Goodnotes) — kein Gemini, nichts neu gelesen. Die Inhalte stammen aus Kursmaterial und Heften, also von
Dritten: FREMDE_WERKZEUGE. Am Konnektor Gruppenordner, weil dort das aktuelle Kursmaterial herkommt.
"""

from __future__ import annotations

from kern.schule import lernen as daten

KONNEKTOR = "iserv_gruppenordner"
FREMDE_WERKZEUGE = ("lernen",)
MAX_SEITENTEXT = 300
MAX_WICHTIGES = 6

ERKLAERUNGEN = [
    {
        "name": "lernen",
        "description": ("Überblick zu EINEM Schulfach aus allem, was Friday hat: was gerade im Unterricht dran ist "
                        "(neuestes Kursmaterial mit Formeln und Definitionen, neueste Heftseiten), Noten, "
                        "wiederkehrende Fehler aus Arbeiten, Arbeiten und Kursmaterial. Benutzen bei „was kommt in "
                        "der Arbeit dran“, „was machen wir gerade in …“, „wie stehe ich in …“. Ohne Fach: die Liste "
                        "der Fächer mit Zahlen. Kennt keine Prüfungstermine — die stehen nicht im Schulmanager."),
        "parameters": {"type": "OBJECT", "properties": {
            "fach": {"type": "STRING", "description": "Fach, z. B. Mathematik oder Chemie (leer = Fächerliste)"},
        }},
    },
]


def _datum(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}" if len(iso or "") >= 10 else ""


def _finden(name: str, faecher: list[dict]) -> dict | None:
    klein = (name or "").strip().casefold()
    if not klein:
        return None
    return (next((f for f in faecher if f["name"].casefold() == klein), None)
            or next((f for f in faecher if klein in f["name"].casefold() or f["name"].casefold().startswith(klein[:4])), None))


def _zaehlung(f: dict) -> str:
    teile = [f"{f[k]} {w}" for k, w in (("arbeiten", "Arbeiten"), ("muster", "Muster"),
                                         ("material", "Material"), ("goodnotes", "Hefte")) if f.get(k)]
    return ", ".join(teile) or "noch nichts"


def lernen(fach: str = "") -> str:
    faecher = daten.liste()
    if not faecher:
        return "Es sind noch keine Fächer eingerichtet (erst unter Schule → Noten anlegen)."
    gesucht = _finden(fach, faecher)
    if not gesucht:
        liste = "\n".join(f"- {f['name']}: {_zaehlung(f)}" for f in faecher)
        kopf = f"Ein Fach „{fach}“ kenne ich nicht. Es gibt:" if (fach or "").strip() else "Meine Fächer:"
        return f"{kopf}\n{liste}"
    d = daten.fach(gesucht["id"])
    if not d:
        return f"Zu „{gesucht['name']}“ finde ich nichts."
    zeilen = [f"{d['fach']['name']} — {_zaehlung(gesucht)}."]

    aktuell = d.get("aktuell") or {}
    if aktuell.get("material") or aktuell.get("seiten"):
        zeilen.append("Aktuell im Unterricht (neueste zuerst):")
        for m in aktuell.get("material", []):
            zeilen.append(f"- Material „{m['dateiname']}“: {m.get('zusammenfassung') or 'ohne Zusammenfassung'}")
            for w in (m.get("wichtiges") or [])[:MAX_WICHTIGES]:
                zeilen.append(f"    · {w}")
        for s in aktuell.get("seiten", []):
            kopf = ", ".join(x for x in (s.get("name"), _datum(s.get("datum", ""))) if x)
            text = " ".join((s.get("text") or "").split())
            if len(text) > MAX_SEITENTEXT:
                text = text[:MAX_SEITENTEXT] + " …"
            zeilen.append(f"- Heftseite „{s.get('titel') or s.get('name')}“ ({kopf}): {text}")

    noten = [f"{hj}: Schnitt {s['wert']}" for hj, s in (d["fach"].get("semester") or {}).items()
             if s.get("eintraege") and s.get("wert") is not None]
    if noten:
        zeilen.append("Noten: " + "; ".join(noten))
    if d["muster"]:
        zeilen.append("Wiederkehrende Fehler:")
        zeilen += [f"- {m.get('person') or 'Eike'}: {m['text']} (aus {m['anzahl']} Arbeiten)" for m in d["muster"]]
    if d["arbeiten"]:
        zeilen.append("Arbeiten: " + "; ".join(
            f"{a.get('titel') or 'ohne Titel'} ({', '.join(x for x in (a.get('person'), _datum(a.get('datum', ''))) if x)})"
            for a in d["arbeiten"]))
    if d["material"]:
        zeilen.append("Kursmaterial: " + ", ".join(m["dateiname"] for m in d["material"][:15])
                      + (f" … und {len(d['material']) - 15} weitere" if len(d["material"]) > 15 else ""))
    if d["goodnotes"]:
        zeilen.append("Goodnotes-Hefte: " + ", ".join(h["name"] for h in d["goodnotes"]))
    if len(zeilen) == 1:
        zeilen.append("Dazu liegt noch nichts vor.")
    return "\n".join(zeilen)


WERKZEUGE = {"lernen": lernen}
