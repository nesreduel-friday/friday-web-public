"""Hochgeladene WhatsApp-Exporte einlesen — .txt oder .zip.

Blockierend (Datei-IO, große Dateien); der Aufrufer nutzt `asyncio.to_thread`.
Aus einer ZIP werden alle enthaltenen `.txt` gelesen. Der Chatname wird aus
dem Dateinamen geraten. Nachrichten kommen in Blöcken in die Ablage, damit
auch ein 200-MB-Verlauf nicht komplett in den Speicher muss.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

from kern.chats import ablage, parser

log = logging.getLogger("friday.chats")

BLOCK = 5000
# Grenzen gegen eine ZIP-Bombe und eine volllaufende Platte. Ein 200-MB-Verlauf
# bleibt möglich; Bilder und Videos im Export werden nie entpackt (nur .txt).
MAX_UPLOAD = 500 * 1024 * 1024
MAX_TEXT_GESAMT = 400 * 1024 * 1024
MAX_TXT_DATEIEN = 200


class ZuGross(Exception):
    """Die Datei überschreitet eine der Grenzen — mit einem Satz, den die App zeigen kann."""


def _einen_verlauf(a: ablage.Ablage, dateiname: str, zeilen) -> dict:
    partner = parser.partner_aus_name(dateiname)
    cid = a.chat_anlegen(dateiname, partner)
    puffer, gesamt = [], 0
    for n in parser.nachrichten(zeilen):
        puffer.append(n)
        if len(puffer) >= BLOCK:
            a.nachrichten_schreiben(cid, puffer)
            gesamt += len(puffer)
            puffer = []
    if puffer:
        a.nachrichten_schreiben(cid, puffer)
        gesamt += len(puffer)
    chat = a.chat_abschliessen(cid)
    log.info("Verlauf '%s' eingelesen: %s Nachrichten", partner, gesamt)
    return chat


def einlesen(pfad: Path, name: str) -> list[dict]:
    """Liest eine hochgeladene Datei ein und gibt die angelegten Chats zurück."""
    a = ablage.standard()
    chats = []
    if name.lower().endswith(".zip"):
        with zipfile.ZipFile(pfad) as z:
            texte = [i for i in z.infolist() if i.filename.lower().endswith(".txt")
                     and not i.filename.startswith("__MACOSX")]
            if len(texte) > MAX_TXT_DATEIEN:
                raise ZuGross(f"Die ZIP enthält mehr als {MAX_TXT_DATEIEN} Textdateien.")
            if sum(i.file_size for i in texte) > MAX_TEXT_GESAMT:
                raise ZuGross("Die Textdateien in der ZIP sind entpackt größer als 400 MB.")
            for eintrag in (i.filename for i in texte):
                with z.open(eintrag) as f:
                    text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
                    chats.append(_einen_verlauf(a, eintrag, text))
    else:
        with open(pfad, encoding="utf-8", errors="replace") as f:
            chats.append(_einen_verlauf(a, name, f))
    return chats
