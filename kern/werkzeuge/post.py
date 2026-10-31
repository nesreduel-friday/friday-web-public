"""Mail-Werkzeuge — das IServ-Postfach im Gespräch.

Wie bei den Elternbriefen: vorbereitet wird im Hintergrund
(`kern/postfach/abgleich.py`), hier wird nur kurz nach Neuem geschaut und aus
der Ablage geantwortet. Ältere Mails bereitet `mail` vor, sobald Friday sie
öffnet. Rein lesend — Friday kann keine Mails senden, verschieben oder löschen.

Mails sind fremde Inhalte (FREMDE_WERKZEUGE): was Friday danach „merken"
soll, wird zum Vorschlag, den Eike bestätigt.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr

from kern import config
from kern.gehirn.persona import WOCHENTAGE
from kern.postfach import ablage, abgleich

KONNEKTOR = "iserv_mail"
MAX_MAIL_ZEICHEN = 4000

ERKLAERUNGEN = [
    {
        "name": "mails",
        "description": ("Durchsucht und listet Mails aus Eikes IServ-Postfach, neueste zuerst: Nummer, Datum, "
                        "Absender, Betreff und — wenn vorbereitet — eine Kurzfassung. Benutzen bei Fragen nach "
                        "E-Mails, Nachrichten von Lehrern oder der Schule, oder um etwas in der Post zu finden."),
        "parameters": {"type": "OBJECT", "properties": {
            "suche": {"type": "STRING", "description": "Suchwörter (Betreff, Absender, Text, Zusammenfassung)"},
            "anzahl": {"type": "INTEGER", "description": "Wie viele (Standard 10)"},
            "ordner": {"type": "STRING", "description": "Nur dieser Ordner, z.B. INBOX, Sent, 11.3"},
            "nur_ungelesen": {"type": "BOOLEAN", "description": "Nur ungelesene"},
        }},
    },
    {
        "name": "mail",
        "description": ("Eine Mail ganz: Zusammenfassung inklusive Anhänge, Termine, Fristen, was zu tun ist, "
                        "ob eine Antwort erwartet wird, und der Text."),
        "parameters": {"type": "OBJECT", "properties": {
            "nummer": {"type": "INTEGER", "description": "Die Nummer aus mails"},
        }, "required": ["nummer"]},
    },
    {
        "name": "mail_termine",
        "description": "Termine und Fristen aus vorbereiteten Mails, die als Nächstes anstehen.",
        "parameters": {"type": "OBJECT", "properties": {
            "tage": {"type": "INTEGER", "description": "Wie viele Tage voraus (Standard 60)"},
        }},
    },
]


def _tag(iso) -> date | None:
    try:
        return date.fromisoformat(str(iso or "")[:10])
    except ValueError:
        return None


def _wann(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso).astimezone(config.jetzt().tzinfo)
        return f"{t:%d.%m.%Y %H:%M}"
    except (TypeError, ValueError):
        return "?"


def _person(kopf: str) -> str:
    name, adresse = parseaddr(kopf or "")
    return name or adresse or "?"


async def mails(suche: str = "", anzahl: int = 10, ordner: str = "", nur_ungelesen: bool = False) -> str:
    anzahl = max(1, min(int(anzahl), 30))
    await abgleich.auffrischen()
    liste = ablage.standard().liste(anzahl, suche=suche, ordner=ordner, nur_ungelesen=bool(nur_ungelesen))
    if not liste:
        return "Dazu finde ich keine Mails." if (suche or ordner or nur_ungelesen) else \
            "Ich habe noch keine Mails — der erste Abgleich läuft vielleicht noch."
    zeilen = []
    for m in liste:
        zeile = f"Mail {m['id']} · {_wann(m['datum'])} · von {_person(m['von'])} · „{m['betreff'] or 'ohne Betreff'}“"
        if not m["gelesen"]:
            zeile += " (ungelesen)"
        if m["anhaenge"]:
            zeile += f" [{len(m['anhaenge'])} Anhang/Anhänge]"
        e = m.get("ergebnis")
        if e:
            zeile += f" — {e.get('kurz', '').strip()}"
            if e.get("wichtig"):
                zeile += " WICHTIG."
        zeilen.append(zeile)
    return "\n".join(zeilen) + "\n(Mit mail und der Nummer bekommst du alle Einzelheiten.)"


async def mail(nummer: int) -> str:
    a = ablage.standard()
    m = a.eintrag(int(nummer))
    if not m:
        return f"Eine Mail mit der Nummer {nummer} finde ich nicht."
    if not m.get("ergebnis") and m["versuche"] < ablage.MAX_VERSUCHE:
        await abgleich.verarbeiten(m["id"])     # ältere Mail: jetzt vorbereiten
        m = a.eintrag(m["id"]) or m
    teile = [f"Von {m['von']} an {m['an']}, {_wann(m['datum'])} (Ordner {m['ordner']})\n"
             f"Betreff: {m['betreff'] or 'ohne Betreff'}" + ("" if m["gelesen"] else "\n(im Postfach ungelesen)")]
    e = m.get("ergebnis")
    if e:
        teile.append(f"Zusammenfassung: {e.get('inhalt') or e.get('kurz') or ''}".strip())
        for titel, schluessel in (("Termine", "termine"), ("Fristen", "fristen")):
            eintraege = sorted((_tag(x.get("datum")), x.get("was", "")) for x in e.get(schluessel) or []
                               if isinstance(x, dict) and _tag(x.get("datum")))
            if eintraege:
                teile.append(f"{titel}: " + "; ".join(f"{t:%d.%m.%Y} {was}" for t, was in eintraege))
        if e.get("zu_tun"):
            teile.append("Zu tun: " + "; ".join(e["zu_tun"]))
        if (e.get("kosten") or "").strip():
            teile.append(f"Kosten: {e['kosten'].strip()}")
        if e.get("antwort_erwartet"):
            teile.append("Der Absender erwartet eine Antwort.")
    if m["anhaenge"]:
        teile.append("Anhänge: " + ", ".join(x["name"] + (" (nicht lesbar)" if x.get("ungelesen") else "")
                                              for x in m["anhaenge"]))
    text = m["text"] or "(kein Text)"
    teile.append("Text:\n" + (text if len(text) <= MAX_MAIL_ZEICHEN else text[:MAX_MAIL_ZEICHEN] + " …"))
    return "\n\n".join(teile)


async def mail_termine(tage: int = 60) -> str:
    tage = max(1, min(int(tage), 365))
    await abgleich.auffrischen()
    ab, bis = config.jetzt().date(), config.jetzt().date() + timedelta(days=tage)
    seit = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    kommend = []
    for m in ablage.standard().liste(2000, seit=seit, nur_verarbeitet=True):
        for art, schluessel in (("Termin", "termine"), ("Frist", "fristen")):
            for x in (m["ergebnis"] or {}).get(schluessel) or []:
                t = _tag((x or {}).get("datum"))
                if t and ab <= t <= bis:
                    kommend.append((t, art, x.get("was", ""), m["betreff"], m["id"]))
    if not kommend:
        return f"In den nächsten {tage} Tagen stehen aus den Mails keine Termine oder Fristen an."
    return "\n".join(f"{WOCHENTAGE[t.weekday()]} {t:%d.%m.}: {art} — {was} (aus „{betreff}“, Mail {mid})"
                     for t, art, was, betreff, mid in sorted(kommend))


WERKZEUGE = {"mails": mails, "mail": mail, "mail_termine": mail_termine}
FREMDE_WERKZEUGE = set(WERKZEUGE)
