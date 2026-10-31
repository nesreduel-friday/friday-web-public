"""Gmail über IMAP — nur lesend: das ganze Postfach im Gespräch, dazu die Bescheide der ÜSTRA.

Eikes Wunsch (15.09.2026): Friday soll sehen, wann ein Garantie-Antrag eingegangen
ist und ob er genehmigt wurde. Die Antworten kommen von `info@uestra.de` mit dem
Betreff „ÜSTRA Garantie: Ihr Garantiefall 8851675 vom 19.08.2026“ (gesehen an
Eikes Postfach, Dezember 2025 bis August 2026).

Seit 16.09.2026 ist Gmail ein allgemeiner Mail-Konnektor (Eike: „nicht nur für die
ÜSTRA“): `suchen` und `holen` für die Werkzeuge in `kern/werkzeuge/gmail.py`.
Anders als beim IServ-Postfach wird nichts gespeichert und nichts mit Gemini
vorbereitet — Gmail durchsucht selbst, und ein privates Postfach durch Gemini zu
schicken sprengt das Kontingent. Gelesen wird live, wenn Eike fragt.

Wie beim IServ-Postfach (`iserv_mail.py`):
- Anmeldung mit einem **App-Passwort** von Google (das normale Passwort scheitert an
  der Zwei-Faktor-Anmeldung), genau ein Versuch; abgewiesene Daten nie wiederholt.
- Ordner nur mit EXAMINE (`select(readonly=True)`), Inhalte nur mit `BODY.PEEK[]` —
  nichts wird als gelesen markiert, verschoben, gelöscht oder gesendet.
- Gesucht wird im Ordner „Alle Nachrichten“ (ohne Spam und Papierkorb) mit Gmails
  eigener Suchsyntax (`X-GM-RAW`). Der ÜSTRA-Abgleich fragt nur `from:info@uestra.de` ab.
- Suchwörter gehen als IMAP-Literal mit CHARSET UTF-8 hinaus — Umlaute und
  Anführungszeichen können die Anfrage so nicht zerbrechen.

Blockierend (imaplib). Aufrufer nutzen `asyncio.to_thread`.
"""

from __future__ import annotations

import email
import hashlib
import imaplib
import re
import ssl
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from kern import konnektoren

KONNEKTOR = "gmail"
HOST = "imap.gmail.com"
PORT = 993
ZEITLIMIT = 60
ABSENDER = ("info@uestra.de",)
MAX_MAILS = 200
MAX_TREFFER = 30
MAX_GROESSE = 30_000_000
_KOPF_FELDER = "BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)]"

_abgewiesen: str | None = None


class AnmeldeFehler(RuntimeError):
    """Anmeldung an Gmail nicht möglich — vorlesbar."""


def vergessen() -> None:
    global _abgewiesen
    _abgewiesen = None


def _fingerabdruck(benutzer: str, passwort: str) -> str:
    return hashlib.sha256(f"{benutzer}\0{passwort}".encode()).hexdigest()


def _text(nachricht: email.message.Message) -> str:
    """Der lesbare Text: text/plain, sonst das HTML ohne Tags."""
    teile_plain, teile_html = [], []
    for teil in nachricht.walk():
        if teil.get_content_maintype() == "multipart" or teil.get_filename():
            continue
        roh = teil.get_payload(decode=True) or b""
        text = roh.decode(teil.get_content_charset() or "utf-8", errors="replace")
        (teile_plain if teil.get_content_type() == "text/plain" else teile_html).append(text)
    if teile_plain:
        return "\n".join(teile_plain)
    html = "\n".join(teile_html)
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", html))


def _kopf(wert: str | None) -> str:
    try:
        return str(make_header(decode_header(wert or "")))
    except Exception:
        return wert or ""


def _datum(n: email.message.Message) -> str:
    try:
        return parsedate_to_datetime(n.get("Date")).isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


class Verbindung:
    """`with Verbindung() as v:` — angemeldet, nur lesend, danach abgemeldet."""

    def __enter__(self) -> "Verbindung":
        global _abgewiesen
        z = konnektoren.zugang(KONNEKTOR)
        if not z:
            raise AnmeldeFehler("Für Gmail ist noch kein Zugang eingetragen.")
        benutzer, passwort = z
        fp = _fingerabdruck(benutzer, passwort)
        if _abgewiesen == fp:
            raise AnmeldeFehler("Google hat diesen Zugang abgewiesen. Bitte ein neues App-Passwort eintragen — "
                                "ich probiere das alte nicht noch einmal.")
        self.m = imaplib.IMAP4_SSL(HOST, PORT, ssl_context=ssl.create_default_context(), timeout=ZEITLIMIT)
        try:
            self.m.login(benutzer, passwort.replace(" ", ""))    # Google zeigt App-Passwörter mit Leerzeichen
        except imaplib.IMAP4.error as exc:
            _abgewiesen = fp
            try:
                self.m.shutdown()
            except Exception:
                pass
            raise AnmeldeFehler(f"Google hat die Anmeldung abgewiesen ({str(exc)[:80]}). Nötig ist ein "
                                "App-Passwort, nicht das normale Passwort.") from None
        return self

    def __exit__(self, *_):
        try:
            self.m.logout()
        except Exception:
            pass

    def _alle_mails(self) -> str:
        """Der Ordner „Alle Nachrichten“ — sein Name ist sprachabhängig, sein Merkmal \\All nicht."""
        _, zeilen = self.m.list()
        for z in zeilen or []:
            text = z.decode(errors="replace") if isinstance(z, bytes) else str(z)
            if "\\All" in text:
                name = re.search(r'"([^"]+)"\s*$|(\S+)\s*$', text)
                return (name.group(1) or name.group(2)) if name else "INBOX"
        return "INBOX"

    def _oeffnen(self) -> None:
        if getattr(self, "_offen", False):
            return
        ordner = self._alle_mails()
        typ, _ = self.m.select(f'"{ordner}"', readonly=True)          # EXAMINE: nur lesen
        if typ != "OK":
            raise RuntimeError("Der Gmail-Ordner lässt sich nicht öffnen")
        self._offen = True

    def suchen(self, anfrage: str, anzahl: int = 10) -> list[dict]:
        """Gmail-Suche (`from:… is:unread newer_than:7d …`), neueste zuerst — nur die Köpfe:
        {uid, datum, von, an, betreff, gelesen, groesse}."""
        self._oeffnen()
        anfrage = " ".join((anfrage or "").split())[:300] or "in:inbox"
        self.m.literal = anfrage.encode()
        typ, d = self.m.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
        uids = [int(x) for x in (d[0] or b"").split()] if typ == "OK" and d else []
        uids = sorted(uids, reverse=True)[:max(1, min(int(anzahl), MAX_TREFFER))]
        if not uids:
            return []
        typ, d = self.m.uid("FETCH", ",".join(map(str, uids)), f"(UID FLAGS RFC822.SIZE {_KOPF_FELDER})")
        mails = {}
        for x in d or []:
            if not (isinstance(x, tuple) and len(x) > 1):
                continue
            meta = x[0].decode(errors="replace")
            uid = re.search(r"UID (\d+)", meta)
            if not uid:
                continue
            groesse = re.search(r"RFC822\.SIZE (\d+)", meta)
            n = email.message_from_bytes(x[1])
            mails[int(uid.group(1))] = {
                "uid": int(uid.group(1)), "datum": _datum(n), "von": _kopf(n.get("From")), "an": _kopf(n.get("To")),
                "betreff": _kopf(n.get("Subject")), "gelesen": "\\Seen" in meta,
                "groesse": int(groesse.group(1)) if groesse else 0}
        return [mails[u] for u in uids if u in mails]

    def holen(self, uid: int) -> dict | None:
        """Eine Mail roh: {uid, roh, gelesen, groesse} — mit PEEK, sie bleibt ungelesen.
        Zu große Mails nur mit Kopf (`nur_kopf`)."""
        self._oeffnen()
        typ, d = self.m.uid("FETCH", str(int(uid)), "(UID FLAGS RFC822.SIZE)")
        meta = next((x.decode(errors="replace") for x in d or [] if isinstance(x, bytes) and b"UID" in x), "")
        if typ != "OK" or not meta:
            return None
        groesse = int((re.search(r"RFC822\.SIZE (\d+)", meta) or [0, 0])[1])
        nur_kopf = groesse > MAX_GROESSE
        typ, d = self.m.uid("FETCH", str(int(uid)), "(BODY.PEEK[HEADER])" if nur_kopf else "(BODY.PEEK[])")
        roh = next((x[1] for x in d or [] if isinstance(x, tuple) and len(x) > 1), None)
        if typ != "OK" or roh is None:
            return None
        return {"uid": int(uid), "roh": roh, "gelesen": "\\Seen" in meta, "groesse": groesse, "nur_kopf": nur_kopf}

    def uestra_mails(self, seit: str = "") -> list[dict]:
        """Mails der ÜSTRA, neueste zuerst: {uid, datum, von, betreff, text}.
        `seit` als JJJJ/MM/TT grenzt ein (Gmail-Suchsyntax)."""
        self._oeffnen()
        suche = "(" + " OR ".join(f"from:{a}" for a in ABSENDER) + ")" + (f" after:{seit}" if seit else "")
        typ, d = self.m.uid("SEARCH", "X-GM-RAW", f'"{suche}"')
        uids = [int(x) for x in (d[0] or b"").split()] if typ == "OK" else []
        mails = []
        for uid in sorted(uids, reverse=True)[:MAX_MAILS]:
            typ, d = self.m.uid("FETCH", str(uid), "(BODY.PEEK[])")    # PEEK: kein Gelesen-Flag
            roh = next((x[1] for x in d or [] if isinstance(x, tuple) and len(x) > 1), None)
            if typ != "OK" or roh is None:
                continue
            n = email.message_from_bytes(roh)
            datum = _datum(n)
            von = _kopf(n.get("From"))
            if not any(a in von.lower() for a in ABSENDER):
                continue                                              # doppelte Absicherung
            # An wen ging die Mail? Mit einem Alias pro Person (name+lea@gmail.com) verrät das, zu wem ein Bescheid gehört.
            kopf_an = " ".join(_kopf(n.get(h)) for h in ("To", "Delivered-To", "X-Original-To", "Cc"))
            adressen = sorted({x.lower() for x in re.findall(r"[\w.+\-]+@[\w\-.]+\.[A-Za-z]{2,}", kopf_an)})
            mails.append({"uid": uid, "datum": datum, "von": von, "betreff": _kopf(n.get("Subject")),
                          "an": adressen, "text": _text(n)[:20000]})
        return mails


def pruefen() -> str:
    """Einmal anmelden und zählen — für den Knopf auf der Freigaben-Seite."""
    with Verbindung() as v:
        ungelesen = len(v.suchen("in:inbox is:unread", MAX_TREFFER))
        return (f"Angemeldet an Gmail. {ungelesen if ungelesen < MAX_TREFFER else 'Über 30'} ungelesene Mails im "
                f"Posteingang, {len(v.uestra_mails())} Mails der ÜSTRA.")
