"""Namensabgleich gegen das Register (P4 der Ehrlichkeit, 12.10.2026): Spracheingabe verhört Namen („Gap“ statt Kontakt M).

Nie blind den ersten Treffer: Ein Name ist nur `exakt`, wenn er als ganzer Name, Anzeigename oder als EINDEUTIGES Wort einer
einzigen Person im Register steht. Alles andere, was nach Klang, Schreibung oder Kurzform passt, ist `unsicher` — der Aufrufer fragt
dann „Meinst du Kontakt M?“. Passt nichts, ist er `unbekannt` (das Werkzeug sagt das selbst).
Ähnlichkeit: eine einfache Klangform (b/p, d/t, g/k, v/w/f, ph, ck, ie, Umlaute, Doppelbuchstaben), Kurzform (Anfang eines
Namenswortes, ab 3 Buchstaben: „Gap“ → Kontakt M) und `difflib`. Liest nur das Register (`personen.json`), schreibt nichts."""

from __future__ import annotations

import difflib
import re
import unicodedata

from kern.personen import register

SCHWELLE = 0.72          # Schreib-Nähe (difflib) auf der Klangform
MIN_KURZ = 3             # Kurzform ab so vielen Buchstaben


def klangform(wort: str) -> str:
    s = unicodedata.normalize("NFKD", (wort or "").casefold().replace("ß", "ss"))
    s = "".join(c for c in s if c.isalpha())
    for a, b in (("ph", "f"), ("ck", "k"), ("ch", "k"), ("ie", "i"), ("ei", "ai"), ("ey", "ai"), ("y", "i"), ("dt", "t"), ("tz", "ts"),
                 ("z", "ts"), ("v", "f"), ("w", "f"), ("b", "p"), ("d", "t"), ("g", "k"), ("q", "k"), ("x", "ks")):
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)
    return s[:1] + s[1:].replace("h", "")


def _worte(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9äöüß]+", register.schluessel(text)) if w]


def _felder(p: dict) -> list[str]:
    return [p.get("name", ""), p.get("graph_name", ""), (p.get("kanaele") or {}).get("snapchat", "")]


def _aehnlich_wort(a: str, b: str) -> bool:
    """Klingt/schreibt sich ähnlich, oder `a` ist eine Kurzform von `b`."""
    ka, kb = klangform(a), klangform(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if len(ka) >= MIN_KURZ and kb.startswith(ka):
        return True
    return min(len(ka), len(kb)) >= 4 and difflib.SequenceMatcher(None, ka, kb).ratio() >= SCHWELLE


def deuten(eingabe: str) -> dict:
    """`{stand: exakt|unsicher|unbekannt, personen: [Profil, …]}` — bei `exakt` genau eine Person, bei `unsicher` die Kandidaten
    (beste zuerst, höchstens 3)."""
    k = register.schluessel(eingabe)
    if not k:
        return {"stand": "unbekannt", "personen": []}
    personen = register.liste()
    ganz = [p for p in personen if k in {register.schluessel(f) for f in _felder(p) if f}]
    if len(ganz) == 1:
        return {"stand": "exakt", "personen": ganz}
    if len(ganz) > 1:
        return {"stand": "unsicher", "personen": ganz[:3]}
    worte = [w for w in _worte(eingabe) if len(w) >= 3]
    if len(worte) == 1:
        treffer = [p for p in personen if worte[0] in {w for f in _felder(p) for w in _worte(f)}]
        if len(treffer) == 1:
            return {"stand": "exakt", "personen": treffer}
        if len(treffer) > 1:
            return {"stand": "unsicher", "personen": treffer[:3]}
    bewertet = []
    for p in personen:
        pool = {w for f in _felder(p) for w in _worte(f)} | {register.schluessel(f) for f in _felder(p) if f}
        wert = 0.0
        for e in (worte or _worte(eingabe)):
            for w in pool:
                if _aehnlich_wort(e, w):
                    wert = max(wert, 1.0 if klangform(e) == klangform(w) else difflib.SequenceMatcher(
                        None, klangform(e), klangform(w)).ratio())
        if wert:
            bewertet.append((wert, p))
    bewertet.sort(key=lambda t: -t[0])
    if not bewertet:
        return {"stand": "unbekannt", "personen": []}
    return {"stand": "unsicher", "personen": [p for _, p in bewertet[:3]]}


def frage(personen: list[dict]) -> str:
    """„Meinst du Kontakt M?“ / „Meinst du Kontakt M oder Gabi Peters?“"""
    namen = [p.get("name", "") for p in personen if p.get("name")]
    return "Meinst du " + " oder ".join(namen[:3]) + "?"


_WORT = re.compile(r"[A-ZÄÖÜ][a-zäöüß]{2,}")


# Gewöhnliche Wörter aus Skill- und Vorgabetexten, die nur zufällig nach einem Namen klingen („Serien“ ≈ „Speery“, 13.10.2026:
# Friday las bei jeder Fassung „Meinst du Kontakt C Speery?“ vor, Eike konnte es nicht abstellen).
ALLTAGSWOERTER = frozenset(w.lower() for w in (
    "Serie", "Serien", "Film", "Filme", "Filmen", "Fassung", "Fassungen", "Deutsch", "Englisch", "Deutsche", "Englische",
    "Staffel", "Staffeln", "Folge", "Folgen", "Herunterladen", "Download", "Hinweis", "Dauer", "Stunde", "Stunden",
    "Woche", "Wochen", "Tag", "Tage", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"))


def verdaechtige(text: str) -> list[tuple[str, dict]]:
    """Großgeschriebene Wörter im Text, die einem Namen im Register ÄHNELN, ohne einer zu sein: [(Wort, Person)]. Für das
    Gegenlesen („Gap“ in einer Vorgabe → Kontakt M?). Exakte Namenswörter sind unverdächtig."""
    personen = register.liste()
    bekannt = {w for p in personen for f in _felder(p) for w in _worte(f)}
    aus, gesehen = [], set()
    for m in _WORT.finditer(text or ""):
        wort = m.group(0)
        if register.schluessel(wort) in bekannt or wort in gesehen or wort.lower() in ALLTAGSWOERTER:
            continue
        for p in personen:
            if any(_aehnlich_wort(wort, w) for f in _felder(p) for w in _worte(f) if len(w) >= 3):
                aus.append((wort, p))
                gesehen.add(wort)
                break
    return aus[:3]
