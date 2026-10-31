"""Personen-Blick: Eine Person, egal wo sie gespeichert ist (04.10.2026, Eikes Auftrag).

Chat-Auswertung, Kontakte-Register und Garantie-Profile führen Menschen getrennt. Eike will das nie
sehen: „Kontakt A ist einfach Kontakt A.“ Dieser Blick sammelt alle Einträge, fasst gleiche
Menschen von selbst zusammen (gleicher Name, gleicher Vorname + ähnlicher Nachname) und beantwortet
Fragen zu einem Namen aus allen Quellen zugleich. Nur lesend; IBAN, Mail und Kundenkarte kommen nie
hier heraus (Garantie liefert Name und Anschrift)."""

from __future__ import annotations

import difflib
import logging
import re

from kern.chats import ablage as chatablage
from kern.garantie import ablage as garantieablage
from kern.personen import register

log = logging.getLogger("friday.personen")

WORT_SCHWELLE = 0.84      # Rechtschreib-Nähe zweier Wörter (Kontakt M/Pyttel = 0.91)
VORSCHLAG_SCHWELLE = 0.7  # für „meintest du“ bei Spracherkennungs-Fehlern


def _worte(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9äöüß]+", register.schluessel(text)) if w]


def _aehnlich(a: str, b: str, schwelle: float = WORT_SCHWELLE) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= schwelle


def _eintraege() -> list[dict]:
    """Alle Einträge aus allen Quellen: {quelle, name, daten}. Kaputte Quelle = leer, nie ein Absturz."""
    aus: list[dict] = []
    try:
        for p in chatablage.standard().personen():
            if not p.get("ich") and p.get("name"):
                aus.append({"quelle": "chats", "name": p["name"], "daten": p})
    except Exception:  # noqa: BLE001
        log.warning("Personen-Blick: Chat-Verläufe nicht lesbar", exc_info=True)
    try:
        for p in register.liste():
            aus.append({"quelle": "register", "name": p.get("name", ""), "daten": p})
    except Exception:  # noqa: BLE001
        log.warning("Personen-Blick: Register nicht lesbar", exc_info=True)
    try:
        for p in garantieablage.standard().profile():
            if p.get("archiviert"):
                continue
            voll = " ".join(x for x in (p.get("vorname", ""), p.get("nachname", "")) if x) or p.get("name", "")
            if voll:
                aus.append({"quelle": "garantie", "name": voll, "daten": p})
    except Exception:  # noqa: BLE001
        log.warning("Personen-Blick: Garantie nicht lesbar", exc_info=True)
    return aus


def _gleich(a: dict, b: dict) -> bool:
    """Derselbe Mensch? Gleicher voller Name, oder gleicher Vorname + ähnlicher Nachname,
    oder ein Eintrag nur mit Vornamen, der zu genau diesem Vornamen passt. Nie nur der Vorname
    bei zwei vollen Namen (zwei „Kontakt M“ sind zwei Menschen, bis etwas anderes dafür spricht)."""
    wa, wb = _worte(a["name"]), _worte(b["name"])
    if not wa or not wb:
        return False
    if wa == wb:
        return True
    if wa[0] != wb[0] and not _aehnlich(wa[0], wb[0], 0.9):
        return False
    if len(wa) == 1 or len(wb) == 1:
        return True
    return _aehnlich(wa[-1], wb[-1])


def gruppen() -> list[dict]:
    """Gleiche Menschen zusammengefasst: [{name, eintraege:[…]}]. Ein reiner Vorname-Eintrag hängt
    sich nur an, wenn es genau eine passende Gruppe gibt — sonst bleibt er für sich."""
    voll, kurz = [], []
    for e in _eintraege():
        (voll if len(_worte(e["name"])) > 1 else kurz).append(e)
    gr: list[list[dict]] = []
    for e in voll:
        for g in gr:
            if any(_gleich(e, x) for x in g):
                g.append(e)
                break
        else:
            gr.append([e])
    for e in kurz:
        passend = [g for g in gr if any(_gleich(e, x) for x in g)]
        if len(passend) == 1:
            passend[0].append(e)
        else:
            gr.append([e])
    ergebnis = []
    for g in gr:
        namen = sorted((x["name"] for x in g), key=lambda n: -len(_worte(n)))
        ergebnis.append({"name": namen[0], "eintraege": g})
    return ergebnis


def _gruppe_passt(g: dict, frage: list[str]) -> bool:
    """Jedes Wort der Frage steht (oder klingt) in einem Namen der Gruppe — „Kontakt A“, „Kontakt A“, „Kontakt A“."""
    namenswoerter = [w for e in g["eintraege"] for w in _worte(e["name"])]
    kanal = [w for e in g["eintraege"] if e["quelle"] == "register"
             for w in _worte((e["daten"].get("kanaele") or {}).get("snapchat", "") + " " + e["daten"].get("graph_name", ""))]
    pool = namenswoerter + kanal
    return bool(frage) and all(any(_aehnlich(f, w) for w in pool) for f in frage)


def finden(name: str) -> tuple[list[dict], list[str]]:
    """(passende Gruppen, Vorschläge). Vorschläge nur, wenn nichts exakt passt."""
    frage = _worte(name)
    alle = gruppen()
    treffer = [g for g in alle if _gruppe_passt(g, frage)]
    if treffer:
        return treffer, []
    ziel = register.schluessel(name)
    namen = {register.schluessel(g["name"]): g["name"] for g in alle}
    nah = difflib.get_close_matches(ziel, list(namen), n=3, cutoff=VORSCHLAG_SCHWELLE)
    erste = {w: g["name"] for g in alle for w in _worte(g["name"])}
    nah += [k for k in difflib.get_close_matches(ziel, list(erste), n=3, cutoff=VORSCHLAG_SCHWELLE)
            if erste[k] not in {namen.get(x) for x in nah}]
    vorschlaege = []
    for k in nah:
        n = namen.get(k) or erste.get(k)
        if n and n not in vorschlaege:
            vorschlaege.append(n)
    return [], vorschlaege[:3]


def _chat_zeilen(daten: dict) -> list[str]:
    profil = daten.get("profil")
    if not profil:
        return ["Verlauf noch nicht ausgewertet."]
    teile = []
    if profil.get("beziehung"):
        teile.append(f"{daten.get('rolle') or 'Kontakt'}: {profil['beziehung']}")
    if profil.get("ueber_person"):
        teile.append("Über die Person: " + "; ".join(profil["ueber_person"]))
    if profil.get("themen"):
        teile.append("Gemeinsame Themen: " + ", ".join(profil["themen"]))
    if str(profil.get("geburtstag", "")).strip():
        teile.append("Geburtstag: " + str(profil["geburtstag"]).strip())
    ereignisse = "; ".join(f"{e.get('datum', '')} {e.get('was', '')}".strip() for e in profil.get("ereignisse", []))
    if ereignisse:
        teile.append("Wichtige Ereignisse: " + ereignisse)
    return teile


def _register_zeilen(daten: dict) -> list[str]:
    teile = []
    snap = (daten.get("kanaele") or {}).get("snapchat")
    if snap:
        teile.append(f"Snapchat-Name: {snap}" + (" (pausiert)" if daten.get("pausiert") else ""))
    return teile


def _garantie_zeilen(daten: dict) -> list[str]:
    ort = " ".join(x for x in (str(daten.get("plz", "") or "").strip(), str(daten.get("ort", "") or "").strip()) if x)
    anschrift = ", ".join(x for x in (str(daten.get("strasse", "") or "").strip(), ort) if x)
    teile = []
    if anschrift:
        teile.append(f"Wohnt laut Fahrkarten-Profil: {anschrift}")
    if daten.get("fahrkarte"):
        teile.append(f"Fahrkarte: {daten['fahrkarte']}" + (f" bis {daten['gueltig_bis']}" if daten.get("gueltig_bis") else ""))
    return teile


def beschreiben(g: dict) -> str:
    """Alles, was irgendwo über diese Person steht — ohne zu sagen, woher (Eike will keine Quellen hören)."""
    zeilen = [g["name"]]
    for e in g["eintraege"]:
        if e["quelle"] == "chats":
            zeilen += _chat_zeilen(e["daten"])
        elif e["quelle"] == "register":
            zeilen += _register_zeilen(e["daten"])
        elif e["quelle"] == "garantie":
            zeilen += _garantie_zeilen(e["daten"])
    abweichend = sorted({e["name"] for e in g["eintraege"]} - {g["name"]})
    if abweichend:
        zeilen.append("Schreibweisen in den Daten: " + ", ".join(abweichend))
    return "\n".join(zeilen)


def graph_name(g: dict) -> str:
    """Name für die Gedächtnis-Suche: der hinterlegte Graph-Name, sonst der volle Name."""
    for e in g["eintraege"]:
        if e["quelle"] == "register" and e["daten"].get("graph_name"):
            return e["daten"]["graph_name"]
    return g["name"]
