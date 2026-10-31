"""Gmail-Werkzeuge — Eikes privates Postfach im Gespräch, nur lesend.

Anders als beim IServ-Postfach (`post.py`) gibt es keine Ablage und keine
Vorbereitung mit Gemini: Gmail sucht selbst, gelesen wird live
(`kern/dienste/gmail.py`). Die Nummer einer Mail ist ihre UID im Ordner
„Alle Nachrichten“. Friday kann nichts senden, verschieben, löschen oder als
gelesen markieren.

Mails sind fremde Inhalte (FREMDE_WERKZEUGE): was Friday danach „merken"
soll, wird zum Vorschlag, den Eike bestätigt.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from email.utils import parseaddr

from kern import config
from kern.dienste import gmail as dienst
from kern.postfach.abgleich import mail_zerlegen

KONNEKTOR = "gmail"
MAX_MAIL_ZEICHEN = 4000

ERKLAERUNGEN = [
    {
        "name": "gmail_mails",
        "description": ("Sucht und listet Mails aus Eikes privatem Gmail-Postfach, neueste zuerst: Nummer, Datum, "
                        "Absender, Betreff. Ohne Suche: der Posteingang. Benutzen bei Fragen nach E-Mails, "
                        "Bestellungen, Rechnungen, Newslettern oder Post außerhalb der Schule (die Schulpost steht "
                        "im IServ-Postfach, Werkzeug mails)."),
        "parameters": {"type": "OBJECT", "properties": {
            "suche": {"type": "STRING", "description": ("Gmail-Suchsyntax, z.B. 'from:amazon', 'subject:Rechnung', "
                                                        "'newer_than:7d', 'has:attachment', 'in:sent', "
                                                        "'label:Arbeit' oder einfache Suchwörter")},
            "anzahl": {"type": "INTEGER", "description": "Wie viele (Standard 10, höchstens 30)"},
            "nur_ungelesen": {"type": "BOOLEAN", "description": "Nur ungelesene"},
        }},
    },
    {
        "name": "gmail_mail",
        "description": "Eine Gmail-Mail ganz: Absender, Empfänger, Datum, Betreff, Anhänge und der Text.",
        "parameters": {"type": "OBJECT", "properties": {
            "nummer": {"type": "INTEGER", "description": "Die Nummer aus gmail_mails"},
        }, "required": ["nummer"]},
    },
]


def _wann(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso).astimezone(config.jetzt().tzinfo)
        return f"{t:%d.%m.%Y %H:%M}"
    except (TypeError, ValueError):
        return "?"


def _person(kopf: str) -> str:
    name, adresse = parseaddr(kopf or "")
    return name or adresse or "?"


def _fehler(exc: Exception) -> str:
    if isinstance(exc, dienst.AnmeldeFehler):
        return str(exc)
    return f"Gmail antwortet gerade nicht ({type(exc).__name__})."


def _suchen(anfrage: str, anzahl: int) -> list[dict]:
    with dienst.Verbindung() as v:
        return v.suchen(anfrage, anzahl)


def _holen(uid: int) -> dict | None:
    with dienst.Verbindung() as v:
        return v.holen(uid)


async def gmail_mails(suche: str = "", anzahl: int = 10, nur_ungelesen: bool = False) -> str:
    anzahl = max(1, min(int(anzahl), dienst.MAX_TREFFER))
    anfrage = " ".join(str(suche or "").split()) or "in:inbox"
    if nur_ungelesen:
        anfrage += " is:unread"
    try:
        liste = await asyncio.to_thread(_suchen, anfrage, anzahl)
    except Exception as exc:
        return _fehler(exc)
    if not liste:
        return f"In Gmail finde ich dazu nichts (Suche: {anfrage})."
    zeilen = []
    for m in liste:
        zeile = (f"Gmail {m['uid']} · {_wann(m['datum'])} · von {_person(m['von'])} · "
                 f"„{m['betreff'] or 'ohne Betreff'}“")
        if not m["gelesen"]:
            zeile += " (ungelesen)"
        zeilen.append(zeile)
    return "\n".join(zeilen) + "\n(Mit gmail_mail und der Nummer bekommst du die ganze Mail.)"


async def gmail_mail(nummer: int) -> str:
    try:
        m = await asyncio.to_thread(_holen, int(nummer))
    except Exception as exc:
        return _fehler(exc)
    if not m:
        return f"Eine Gmail-Mail mit der Nummer {nummer} finde ich nicht."
    t = mail_zerlegen(m["roh"])
    teile = [f"Von {t['von']} an {t['an']}, {_wann(t['datum'])}\nBetreff: {t['betreff'] or 'ohne Betreff'}"
             + ("" if m["gelesen"] else "\n(in Gmail ungelesen — bleibt es auch)")]
    if t["anhaenge"]:
        teile.append("Anhänge: " + ", ".join(x["name"] for x in t["anhaenge"]))
    text = "(Mail zu groß — nur der Kopf ist gelesen.)" if m["nur_kopf"] else (t["text"] or "(kein Text)")
    teile.append("Text:\n" + (text if len(text) <= MAX_MAIL_ZEICHEN else text[:MAX_MAIL_ZEICHEN] + " …"))
    return "\n\n".join(teile)


WERKZEUGE = {"gmail_mails": gmail_mails, "gmail_mail": gmail_mail}
FREMDE_WERKZEUGE = set(WERKZEUGE)
