"""Fokus-Personen: Quellen für „Neues“. Nur lesen, nur freigegebene Chats, nie senden.

Jede Quelle liefert `{"eintraege": [...], "zeiger": {...}, "stand": str, "fehler": str}`. Der Zeiger wird erst nach dem Ablegen
vom Sammler weitergeschoben (bei einem Fehler im Sammler gehen keine Nachrichten verloren).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time

from kern import konnektoren
from kern.dienste import gmail, whatsapp
from kern.postfach.abgleich import mail_zerlegen

log = logging.getLogger("friday.fokus.quellen")

ERSTER_LAUF_TAGE = 7
NACHRICHTEN_JE_CHAT = 50


def _woerter(text: str) -> list[str]:
    return re.findall(r"[a-zäöüß0-9]+", (text or "").casefold())


def _vorname(name: str) -> str:
    w = [x for x in _woerter(name) if len(x) >= 2]
    return w[0] if w else ""


def bekannte_namen() -> set[str]:
    """Namen aller Personen, die Friday kennt (Chat-Auswertung, Kontakte, Fokus, freigegebene Einzelchats) — zum Erkennen von
    Namensgleichheit („Kontakt F Muster“ und „Kontakt F Meier“)."""
    namen: set[str] = set()
    try:
        from kern.chats import ablage as chatsablage
        namen |= {" ".join(_woerter(str(p.get("name") or ""))) for p in chatsablage.standard().personen() if not p.get("ich")}
    except Exception:  # noqa: BLE001
        pass
    try:
        from kern.personen import register
        namen |= {" ".join(_woerter(str(p.get("name") or ""))) for p in register.liste()}
    except Exception:  # noqa: BLE001
        pass
    try:
        from kern.fokus import ablage as fokusablage
        namen |= {" ".join(_woerter(n)) for n in fokusablage.namen()}
    except Exception:  # noqa: BLE001
        pass
    try:
        namen |= {" ".join(_woerter(str(c.get("name") or ""))) for c in whatsapp.frei() if str(c.get("id", "")).endswith("@c.us")}
    except Exception:  # noqa: BLE001
        pass
    return {n for n in namen if n}


def vorname_mehrdeutig(name: str, bekannte: set[str] | None = None) -> bool:
    """Gibt es unter den bekannten Personen jemand ANDEREN mit demselben Vornamen? Ein Name nur mit Vornamen („Kontakt F“) und ein
    Name mit Nachnamen („Kontakt F Muster“) gelten als dieselbe Person; mehrdeutig wird es erst bei zwei verschiedenen Nachnamen."""
    voll = " ".join(_woerter(name))
    vor = _vorname(name)
    if not vor:
        return False
    nachnamen = {n.split()[-1] for n in (bekannte if bekannte is not None else bekannte_namen())
                 if _vorname(n) == vor and len(n.split()) > 1}
    eigener = voll.split()[-1] if len(voll.split()) > 1 else None
    if eigener:
        return bool(nachnamen - {eigener})
    return len(nachnamen) >= 2


def von_person(von: str, name: str, mehrdeutig: bool = False, bekannt: tuple[str, ...] = ()) -> bool:
    """Stammt eine Gruppennachricht von dieser Person? Der Vorname muss stimmen; hat der Absender einen Nachnamen, muss auch er
    stimmen („Summer Meier“ ist nicht „Summer Muster“). Zeigt WhatsApp nur den Vornamen und gibt es noch jemand mit diesem Vornamen
    (`mehrdeutig`), zählt die Nachricht nur, wenn Eike den Absender dieser Person zugeordnet hat (`bekannt`)."""
    absender, gesucht = _woerter(von), [w for w in _woerter(name) if len(w) >= 2]
    if not absender or not gesucht or absender[0] != gesucht[0]:
        return False
    if len(absender) > 1:
        return gesucht[-1] in absender
    return (not mehrdeutig) or " ".join(absender) in {" ".join(_woerter(b)) for b in bekannt}


def _einzelchat(name: str) -> tuple[dict | None, str]:
    treffer = [c for c in whatsapp.finden(name) if c["id"].endswith("@c.us")]
    if len(treffer) > 1:
        return None, "WhatsApp: mehrere Chats passen — nichts gelesen"
    return (treffer[0], "") if treffer else (None, "")


async def whatsapp_lesen(person: dict, jetzt_stempel: int | None = None,
                         pause: tuple[float, float] | None = None) -> dict:
    name = person["name"]
    zeiger_alt = dict((person.get("quellen", {}).get("whatsapp") or {}).get("chats") or {})
    jetzt = jetzt_stempel if jetzt_stempel is not None else int(time.time())
    erster = jetzt - ERSTER_LAUF_TAGE * 86400
    einzel, hinweis = _einzelchat(name)
    gruppen = [c for c in whatsapp.frei() if c["id"].endswith("@g.us")]
    chats = ([einzel] if einzel else []) + gruppen
    ergebnis = {"eintraege": [], "zeiger": {"chats": {}}, "stand": "", "fehler": "", "unklar": []}
    mehrdeutig = vorname_mehrdeutig(name)
    zuordnung = (person.get("quellen", {}).get("whatsapp") or {}).get("zuordnung") or {}
    unklar: dict[tuple[str, str], dict] = {}
    if hinweis:
        ergebnis["stand"] = hinweis
    elif not chats:
        ergebnis["stand"] = "WhatsApp: kein freigegebener Chat"
        return ergebnis
    unten, oben = pause if pause is not None else whatsapp.PAUSE_CHAT_S
    for i, chat in enumerate(chats):
        try:
            stand = await whatsapp.nachrichten(chat["id"], NACHRICHTEN_JE_CHAT, frisch=True)
        except Exception as exc:  # noqa: BLE001 — Dienst weg, Sitzung getrennt …: melden, die anderen Quellen laufen weiter
            ergebnis["fehler"] = str(exc)[:200] or type(exc).__name__
            break
        liste = stand.get("nachrichten") or []
        grenze = max(zeiger_alt.get(chat["id"], 0), 0) or erster
        gruppe = chat["id"].endswith("@g.us")
        for n in liste:
            if n["stempel"] <= grenze:
                continue
            if gruppe and not von_person(n["von"], name, mehrdeutig, tuple(zuordnung.get(chat["id"], ()))):
                if mehrdeutig and von_person(n["von"], name):
                    u = unklar.setdefault((chat["id"], n["von"]), {"chat_id": chat["id"], "chat": chat.get("name") or chat["id"],
                                                                    "absender": n["von"], "anzahl": 0})
                    u["anzahl"] += 1                      # könnte die Person sein, ist aber wegen Namensgleichheit unklar
                continue
            ergebnis["eintraege"].append({"zeit": n["zeit"], "von": n["von"], "text": n["text"], "chat": chat.get("name") or chat["id"]})
        if liste:
            ergebnis["zeiger"]["chats"][chat["id"]] = max(n["stempel"] for n in liste)
        if i + 1 < len(chats):
            await asyncio.sleep(random.uniform(unten, oben))
    ergebnis["unklar"] = list(unklar.values())
    if not ergebnis["stand"]:
        ergebnis["stand"] = "WhatsApp: " + ", ".join(c.get("name") or c["id"] for c in chats)
    return ergebnis


# ---------- Mail (Gmail, nur lesen, Mails bleiben ungelesen) ----------

MAILS_JE_LAUF = 5
MAIL_ZEICHEN = 600
MAIL_TAGE = 14


def _anfrage(name: str) -> str:
    sauber = " ".join(name.replace('"', "").split())
    return f'(from:"{sauber}" OR to:"{sauber}") newer_than:{MAIL_TAGE}d'


def _suchen(anfrage: str, anzahl: int) -> list[dict]:
    with gmail.Verbindung() as v:
        return v.suchen(anfrage, anzahl)


def _holen(uid: int) -> dict | None:
    with gmail.Verbindung() as v:
        return v.holen(uid)           # PEEK: die Mail bleibt in Gmail ungelesen


async def mail_lesen(person: dict) -> dict:
    ergebnis = {"eintraege": [], "zeiger": {}, "stand": "", "fehler": ""}
    if not konnektoren.aktiv("gmail"):
        ergebnis["stand"] = "Mail: Konnektor aus"
        return ergebnis
    letzte = int((person.get("quellen", {}).get("mail") or {}).get("letzte_uid") or 0)
    try:
        koepfe = await asyncio.to_thread(_suchen, _anfrage(person["name"]), 10)
        neue = sorted((k for k in koepfe if k["uid"] > letzte), key=lambda k: k["uid"], reverse=True)[:MAILS_JE_LAUF]
        for k in neue:
            roh = await asyncio.to_thread(_holen, k["uid"])
            text = mail_zerlegen(roh["roh"])["text"] if roh else ""
            ergebnis["eintraege"].append({"zeit": k["datum"], "von": k["von"], "betreff": k["betreff"],
                                          "text": " ".join(text.split())[:MAIL_ZEICHEN]})
    except Exception as exc:  # noqa: BLE001 — Anmeldung, Netz …: melden, nichts weiterschieben
        ergebnis["fehler"] = str(exc)[:200] or type(exc).__name__
        ergebnis["eintraege"] = []
        return ergebnis
    ergebnis["zeiger"] = {"letzte_uid": max([letzte] + [k["uid"] for k in koepfe])}
    ergebnis["stand"] = "Mail: Gmail"
    return ergebnis
