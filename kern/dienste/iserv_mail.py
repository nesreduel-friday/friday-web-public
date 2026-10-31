"""IServ-Postfach über IMAP — nur lesend.

IServ bietet IMAP mit denselben Zugangsdaten (geprüft 12.09.2026: Port 993,
Anmeldung klappt, 10 Ordner, 2248 Mails). IMAP statt Weboberfläche: Standard,
stabil, und das Nur-Lesen lässt sich erzwingen:

  - Ordner nur mit EXAMINE öffnen (`select(readonly=True)`),
  - Inhalte nur mit `BODY.PEEK[]` holen — das setzt das Gelesen-Flag nicht.

Friday kann so nichts markieren, verschieben, löschen oder senden.

Wie beim Schulmanager: genau ein Anmeldeversuch pro Verbindung; wurden die
Daten abgewiesen, probiert Friday sie nicht noch einmal, bis neue eingetragen
sind — sonst sperrt IServ das Konto.

Blockierend (imaplib). Aufrufer nutzen `asyncio.to_thread`.
"""

from __future__ import annotations

import hashlib
import imaplib
import os
import re
import ssl
from urllib.parse import urlsplit

from kern import konnektoren

KONNEKTOR = "iserv_mail"
PORT = 993
ZEITLIMIT = 60
MAX_MAIL_BYTES = 30 * 1024 * 1024    # größere Mails: nur der Kopf

_abgewiesen: str | None = None


class AnmeldeFehler(RuntimeError):
    """Anmeldung am Postfach nicht möglich — vorlesbar."""


def host() -> str:
    return (os.getenv("ISERV_IMAP", "").strip()
            or urlsplit(os.getenv("ISERV_URL", "https://schule.example/iserv")).hostname or "")


def zugang() -> tuple[str, str]:
    z = konnektoren.zugang(KONNEKTOR)
    if not z:
        raise AnmeldeFehler("Für das IServ-Postfach sind keine Zugangsdaten eingetragen.")
    return z


def vergessen() -> None:
    global _abgewiesen
    _abgewiesen = None


def _fingerabdruck(benutzer: str, passwort: str) -> str:
    return hashlib.sha256(f"{benutzer}\0{passwort}".encode()).hexdigest()


def _ordnername(name: str) -> str:
    """Für IMAP-Befehle immer in Anführungszeichen ("INBOX/to me")."""
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Verbindung:
    """`with Verbindung() as v:` — angemeldet, nur lesend, danach abgemeldet."""

    def __enter__(self) -> "Verbindung":
        global _abgewiesen
        benutzer, passwort = zugang()
        fp = _fingerabdruck(benutzer, passwort)
        if _abgewiesen == fp:
            raise AnmeldeFehler("IServ hat diese Zugangsdaten am Postfach abgewiesen. Bitte neu eintragen — "
                                "ich probiere sie nicht noch einmal, sonst sperrt IServ das Konto.")
        self.m = imaplib.IMAP4_SSL(host(), PORT, ssl_context=ssl.create_default_context(), timeout=ZEITLIMIT)
        try:
            self.m.login(benutzer, passwort)
        except imaplib.IMAP4.error as exc:
            _abgewiesen = fp
            try:
                self.m.shutdown()
            except Exception:
                pass
            raise AnmeldeFehler(f"IServ hat die Anmeldung am Postfach abgewiesen ({str(exc)[:80]}). "
                                "Ich versuche es bewusst nicht noch einmal.") from None
        return self

    def __exit__(self, *_):
        try:
            self.m.logout()
        except Exception:
            pass

    def ordner(self) -> list[str]:
        typ, zeilen = self.m.list()
        namen = []
        for z in zeilen or []:
            text = z.decode(errors="replace") if isinstance(z, bytes) else str(z)
            m = re.match(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', text)
            if not m or "\\noselect" in m.group("flags").lower():
                continue
            name = m.group("name").strip()
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            namen.append(name)
        return namen

    def _oeffnen(self, ordner: str) -> int:
        typ, _ = self.m.select(_ordnername(ordner), readonly=True)   # EXAMINE: nur lesen
        if typ != "OK":
            raise RuntimeError(f"Ordner {ordner} lässt sich nicht öffnen")
        uv = (self.m.response("UIDVALIDITY")[1] or [b"0"])[0]
        return int(uv or 0)

    def uids(self, ordner: str) -> tuple[int, list[int]]:
        uv = self._oeffnen(ordner)
        typ, d = self.m.uid("SEARCH", None, "ALL")
        return uv, [int(x) for x in (d[0] or b"").split()] if typ == "OK" else []

    def holen(self, ordner: str, uids: list[int]) -> list[dict]:
        """Mails nach UID: {"uid", "gelesen", "groesse", "roh", "nur_kopf"}.
        Einzeln geholt — so ist die Zuordnung UID ↔ Inhalt eindeutig."""
        if not uids:
            return []
        self._oeffnen(ordner)
        ergebnis = []
        for uid in uids:
            typ, d = self.m.uid("FETCH", str(uid), "(FLAGS RFC822.SIZE)")
            kopf = b" ".join(x if isinstance(x, bytes) else x[0] for x in d or [] if x)
            if typ != "OK" or not kopf:
                continue
            g = re.search(rb"RFC822\.SIZE (\d+)", kopf)
            groesse = int(g.group(1)) if g else 0
            nur_kopf = groesse > MAX_MAIL_BYTES
            typ, d = self.m.uid("FETCH", str(uid), "(BODY.PEEK[HEADER])" if nur_kopf else "(BODY.PEEK[])")
            roh = next((x[1] for x in d or [] if isinstance(x, tuple) and len(x) > 1), None)
            if typ != "OK" or roh is None:
                continue
            ergebnis.append({"uid": uid, "gelesen": b"\\Seen" in kopf, "groesse": groesse,
                             "roh": roh, "nur_kopf": nur_kopf})
        return ergebnis


def pruefen() -> str:
    """Einmal anmelden und zählen — für den Knopf auf der Freigaben-Seite."""
    with Verbindung() as v:
        ordner = v.ordner()
        return f"Angemeldet am Postfach. {len(ordner)} Ordner."
