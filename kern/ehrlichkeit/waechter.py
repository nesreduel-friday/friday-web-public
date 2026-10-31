"""Wächter: Texte, die der Kern an Eike und ans Modell schickt, wenn Friday etwas behauptet, was nicht passiert ist (P3, 12.10.2026).

Nur Text, keine Netz- oder Zustandslogik — `sitzung/live.py` (Sprache) und `sitzung/chat.py` (Chat) rufen es auf. Die Quittungen
(`wirkung.quittungen`) sind die Quelle der Wahrheit; hier steht nichts, was das Modell erfinden könnte. Alle Sätze ohne Technikwörter."""

from __future__ import annotations

from kern.ehrlichkeit import behauptungen as beh
from kern.ehrlichkeit import wirkung as wirk

# Behauptungswort -> Partizip für „Es wurde nichts …“
_PARTIZIP = {"raus": "gesendet", "gesetzt": "umgesetzt", "angelegt": "angelegt"}
MAX_KORREKTUREN = 5          # je Sitzung: danach schweigt der Wächter (Schutz gegen Schleifen)


def pruefen(text: str, wirkungen: list[dict], frueher: list[dict] | None = None) -> list[tuple[str, str]]:
    return beh.verstoesse(text, wirkungen, frueher)


def _nichts(verstoesse: list[tuple[str, str]]) -> str:
    teile: list[str] = []
    for wort, _ in verstoesse:
        p = _PARTIZIP.get(wort, wort)
        if p not in teile:
            teile.append(p)
    return " und ".join(f"nichts {p}" for p in teile[:3]) or "nichts"


def fakten(wirkungen: list[dict], verstoesse: list[tuple[str, str]] = ()) -> str:
    """Was WIRKLICH war, aus den Quittungen: erst die Absagen/Rückfragen, sonst „Dazu ist nichts gelaufen.“, sonst was geklappt hat."""
    echte = [w for w in (wirkungen or []) if isinstance(w, dict) and w.get("art") != "lesen"]
    nicht = wirk.quittungen([w for w in echte if w.get("status") != "ok"])
    if nicht:
        return " ".join(q["text"] for q in nicht[:3])
    if echte:
        ok = wirk.quittungen(echte)
        if ok:
            return "Geklappt hat nur: " + " ".join(q["text"] for q in ok[:2])
    return "Dazu ist nichts gelaufen."


def korrektur_text(wirkungen: list[dict], verstoesse: list[tuple[str, str]]) -> str:
    """Die rote Karte im Browser."""
    return f"Das stimmt nicht: Es wurde {_nichts(verstoesse)}. {fakten(wirkungen, verstoesse)}"


def modell_hinweis(wirkungen: list[dict], verstoesse: list[tuple[str, str]]) -> str:
    """Textzug an das Live-Modell, nachdem es etwas Falsches gesagt hat."""
    satz = verstoesse[0][1][:160]
    return (f"[KERN] Eben war falsch: Du hast gesagt „{satz}“ — aber es wurde {_nichts(verstoesse)}. {fakten(wirkungen, verstoesse)} "
            "Sage Eike jetzt in einem Satz die Wahrheit, ohne dich lange zu entschuldigen.")


def chat_hinweis(wirkungen: list[dict], verstoesse: list[tuple[str, str]]) -> str:
    """Hinweis für den einen Neuversuch im Chat (ohne Werkzeuge)."""
    satz = verstoesse[0][1][:160]
    return (f"[KERN-PRÜFUNG] Deine Antwort behauptet „{satz}“ — aber es wurde {_nichts(verstoesse)}. {fakten(wirkungen, verstoesse)} "
            "Antworte neu, in höchstens drei Sätzen: Sage ehrlich, was nicht geklappt hat, und was du von Eike brauchst. "
            "Behaupte nichts als erledigt.")


def schablone(wirkungen: list[dict], verstoesse: list[tuple[str, str]] = ()) -> str:
    """Feste Antwort aus den Quittungen, wenn auch der Neuversuch etwas Falsches behauptet."""
    f = fakten(wirkungen, verstoesse)
    if f == "Dazu ist nichts gelaufen.":
        return "Das habe ich noch nicht gemacht. Sag mir bitte genau, was ich tun soll."
    return f"Das hat noch nicht geklappt. {f} Ich brauche von dir noch eine genauere Angabe, dann versuche ich es erneut."


def anweisung(status: str, text: str) -> str:
    """Kurze zwingende Anweisung, die im Sprachkanal MIT dem Ergebnis eines Werkzeugs geht, das NICHT erledigt hat. Leer bei ok."""
    if status == "ok":
        return ""
    grund = wirk.grund_satz(text) or "es fehlt noch etwas"
    if status == "rueckfrage":
        if text.startswith("RÜCKFRAGE:"):
            return ""                       # das Gegenlesen (gegenlesen.py) trägt seine Anweisung selbst
        return ("NOCH NICHT ERLEDIGT. Frage Eike als Erstes, was dir fehlt, und sage NICHT, dass etwas festgehalten, geschickt, "
                "gesendet oder erledigt ist.")
    return (f"NICHT ERLEDIGT. Sage als Erstes: Das hat nicht geklappt, weil {grund}. "
            "Sage NICHT festgehalten, geschickt, gesendet, erledigt oder ist raus.")
