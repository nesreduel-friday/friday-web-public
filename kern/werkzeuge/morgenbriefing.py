"""Morgenbriefing im Gespräch — Stand nennen, an- und ausschalten, Uhrzeit ändern.

Eikes Wunsch (14.09.2026): Das Briefing ist eine Einstellung, keine Freigabe,
und Friday soll es auf Zuruf schalten können. Deshalb immer verfügbar und —
wie bei den Erinnerungen — ohne Rückfrage: Es betrifft nur Eike und ist sofort
umkehrbar. Aus fremdem Text (Mail, Brief, Webseite) wird trotzdem nichts
geschaltet; nur den Stand nennen geht immer.
"""

from __future__ import annotations

from kern.planer import briefing

ERKLAERUNGEN = [{
    "name": "morgenbriefing",
    "description": ("Das Morgenbriefing: jeden Morgen eine Benachrichtigung mit Wetter, Schultag, Terminen und "
                    "Erinnerungen. Ohne Angaben nennt das Werkzeug nur den Stand. Benutzen, wenn Eike es an- "
                    "oder ausschalten, die Uhrzeit ändern oder wissen will, ob es an ist. Sag danach den "
                    "Stand, den das Werkzeug meldet — nicht, was du erwartest."),
    "parameters": {"type": "OBJECT", "properties": {
        "an": {"type": "BOOLEAN", "description": "true = einschalten, false = ausschalten; weglassen = nicht ändern"},
        "uhrzeit": {"type": "STRING", "description": "Neue Uhrzeit als HH:MM, z.B. 06:45; weglassen = nicht ändern"},
    }},
}]


def morgenbriefing(an: bool | None = None, uhrzeit: str = "", fremde_inhalte: bool = False) -> str:
    if isinstance(an, str):                     # manchmal kommt „true“ als Text
        an = an.strip().lower() in ("true", "ja", "an", "1")
    aendern = an is not None or bool((uhrzeit or "").strip())
    if aendern and fremde_inhalte:
        return ("Der Wunsch stammt aus fremdem Text (Mail, Brief oder Webseite). Daraus schalte ich das "
                "Morgenbriefing nicht um — sag es mir selbst. " + briefing.stand_text())
    if aendern:
        try:
            briefing.einstellen(an=an, uhrzeit_text=(uhrzeit or "").strip() or None)
        except ValueError:
            return "Die Uhrzeit verstehe ich nicht — bitte als HH:MM, z.B. 06:45. Nichts geändert."
    return briefing.stand_text()


morgenbriefing.kennt_fremde_inhalte = True

WERKZEUGE = {"morgenbriefing": morgenbriefing}
