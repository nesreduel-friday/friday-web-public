"""Ablage für die Garantie: gefundene Fahrten, Anträge, Eikes Angaben, sein Ticket.

- `data/garantie.db` (SQLite, WAL):
  `reisen` — verspätete Fahrten mit der Kennung der GVH-Auskunft (`jid`). Ein
  abgeleiteter Zwischenstand; älter als die Antragsfrist, wird er aufgeräumt.
  `antraege` — was Friday vorbereitet hat. Löschen kann Eike nur Entwürfe;
  abgeschickte bleiben stehen, sonst stimmen die Zähler nicht.
  `vorgaenge` — Garantiefälle aus Gmail (kern/garantie/post.py).
- `data/garantie_angaben.json` (600): Name, Adresse, Fahrkarte, Bankverbindung —
  dauerhaft, denn sie ändern sich selten; auch in der nächtlichen Sicherung. Die
  IBAN geht NIE zurück an den Browser, nur verdeckt („DE•• … 1234“).
- `data/garantie/ticket.*` (600): die Fahrkarte als Nachweis.
- Profile (seit 20.09.2026): Eike reicht auch für Freunde ein. Profil 0 ist Eike selbst (die
  Dateien oben, unverändert); jeder Freund ist ein Profil in `data/garantie_profile.json` (600)
  mit eigenen Angaben, eigenem Ticket, eigenen Anträgen und eigenen Höchstzahlen. Jede Funktion
  hier nimmt `profil` und kennt als Vorgabe nur Eike — was nicht ausdrücklich nach einem Freund
  fragt, sieht seine Daten nie.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kern import config

AUFBEWAHREN_TAGE = 15                 # Antragsfrist 14 Tage, einer Luft
ANGABEN = ("anrede", "vorname", "nachname", "email", "strasse", "plz", "ort", "fahrkarte",
           "gueltig_bis", "kontoinhaber", "iban", "abo_nummer", "kundenkarte")
# Die Abo-Kundennummer verlangt das Formular bei Abo-Fahrkarten wie dem
# „Deutschlandticket (Hannover Jugend)“ (gemessen 16.09.2026).
# Die Fahrkarte ist keine Pflichtangabe mehr: Sie kommt aus dem hochgeladenen Ticket.
PFLICHT = ("vorname", "nachname", "email", "strasse", "plz", "ort", "kontoinhaber", "iban")
# Zusätzlich bei Freunden. `auszahlung`: „ich“ = das Geld geht auf Eikes Konto, „eigenes“ = auf das des Freundes.
PROFIL_FELDER = ANGABEN + ("name", "auszahlung", "anteil", "vollmacht", "notiz", "archiviert")

SCHEMA = """
CREATE TABLE IF NOT EXISTS reisen (
    id TEXT PRIMARY KEY, jid TEXT NOT NULL, tag TEXT NOT NULL, linie TEXT, kategorie TEXT, richtung TEXT,
    halt TEXT, plan TEXT, echt TEXT, verspaetung INTEGER, gesehen TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS antraege (
    id INTEGER PRIMARY KEY, erstellt TEXT NOT NULL, reise TEXT, jid TEXT, linie TEXT, richtung TEXT,
    start TEXT, ziel TEXT, start_ref TEXT, ziel_ref TEXT, ab TEXT, an TEXT, verspaetung INTEGER, quelle TEXT,
    faellig_ab TEXT, status TEXT NOT NULL DEFAULT 'offen', hindernisse TEXT, hinweise TEXT, fahrkarte TEXT,
    nachgeprueft INTEGER NOT NULL DEFAULT 0, abgeschickt_am TEXT, daten TEXT);
CREATE TABLE IF NOT EXISTS vorgaenge (
    vorgang TEXT PRIMARY KEY, fahrtdatum TEXT, linie TEXT, start TEXT, ziel TEXT, ab TEXT, an TEXT,
    fahrkarte TEXT, status TEXT, grund TEXT, betrag TEXT, bearbeiter TEXT, eingang_am TEXT,
    letzte_mail TEXT, antrag INTEGER, gemeldet TEXT);
"""
_VORGANG_SPALTEN = ("vorgang", "fahrtdatum", "linie", "start", "ziel", "ab", "an", "fahrkarte", "status", "grund",
                    "betrag", "bearbeiter", "eingang_am", "letzte_mail", "antrag", "gemeldet", "cent",
                    "entschieden_am", "antragstag", "mails", "erledigt")
_JSON = ("hindernisse", "hinweise", "daten")


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kurz(jid: str) -> str:
    """Die HAFAS-Kennung hat 148 Zeichen — für Eike und Friday eine kurze."""
    return hashlib.sha1(jid.encode()).hexdigest()[:10]


def _schreiben_600(ziel: Path, inhalt: bytes) -> None:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_name(ziel.name + ".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(inhalt)
    os.chmod(neu, 0o600)
    os.replace(neu, ziel)


def _iso(wert):
    return wert.isoformat() if isinstance(wert, datetime) else wert


class Ablage:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self.ordner = ordner
        self._sperre = threading.Lock()
        self._db = sqlite3.connect(ordner / "garantie.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        # Später dazugekommene Spalten — vorhandene Datenbanken nachrüsten, nichts neu anlegen.
        for tabelle, neue in (("reisen", (("bereich", "TEXT"), ("gemeldet", "INTEGER NOT NULL DEFAULT 0"),
                                          ("verlauf", "TEXT"))),
                              ("antraege", (("vorgang", "TEXT"), ("entscheidung", "TEXT"),
                                            ("profil", "INTEGER NOT NULL DEFAULT 0"))),
                              ("vorgaenge", (("cent", "INTEGER"), ("entschieden_am", "TEXT"), ("antragstag", "TEXT"),
                                             ("mails", "INTEGER"), ("erledigt", "TEXT")))):
            spalten = {z[1] for z in self._db.execute(f"PRAGMA table_info({tabelle})")}
            for name, art in neue:
                if name not in spalten:
                    self._db.execute(f"ALTER TABLE {tabelle} ADD COLUMN {name} {art}")
        self._db.commit()

    # ---------- Gefundene Fahrten ----------

    def reise_merken(self, f: dict) -> str:
        """Dieselbe Fahrt (gleiche Kennung) wird nicht doppelt gemerkt; es bleibt der
        höchste gesehene Stand der Verspätung."""
        rid = kurz(f["jid"])
        with self._sperre, self._db:
            self._db.execute(
                """INSERT INTO reisen (id, jid, tag, linie, kategorie, richtung, halt, plan, echt, verspaetung, gesehen,
                                       bereich)
                   VALUES (:id, :jid, :tag, :linie, :kategorie, :richtung, :halt, :plan, :echt, :verspaetung, :gesehen,
                           :bereich)
                   ON CONFLICT (id) DO UPDATE SET gesehen = excluded.gesehen,
                     bereich = CASE WHEN reisen.bereich = 'deine Orte' THEN reisen.bereich
                                    ELSE COALESCE(excluded.bereich, reisen.bereich) END,
                     halt = CASE WHEN excluded.verspaetung >= reisen.verspaetung THEN excluded.halt ELSE reisen.halt END,
                     plan = CASE WHEN excluded.verspaetung >= reisen.verspaetung THEN excluded.plan ELSE reisen.plan END,
                     echt = CASE WHEN excluded.verspaetung >= reisen.verspaetung THEN excluded.echt ELSE reisen.echt END,
                     verspaetung = MAX(reisen.verspaetung, excluded.verspaetung)""",
                {"id": rid, "jid": f["jid"], "tag": f["plan"].date().isoformat(), "linie": f["linie"],
                 "kategorie": f.get("kategorie", ""), "richtung": f.get("richtung", ""), "halt": f.get("halt", ""),
                 "plan": _iso(f["plan"]), "echt": _iso(f.get("echt")), "verspaetung": f.get("verspaetung") or 0,
                 "gesehen": _jetzt(), "bereich": f.get("bereich")})
        return rid

    def reise_gemeldet(self, rid: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE reisen SET gemeldet = 1 WHERE id = ?", (rid,))

    def verlauf_merken(self, rid: str, halte: list[dict]) -> None:
        """Den Verlauf mit Echtzeit sichern — die Auskunft vergisst sie nach einiger Zeit (Eike, 16.09.)."""
        with self._sperre, self._db:
            self._db.execute("UPDATE reisen SET verlauf = ? WHERE id = ?", (json.dumps(halte, ensure_ascii=False), rid))

    def gespeicherter_verlauf(self, rid: str) -> list[dict] | None:
        with self._sperre:
            z = self._db.execute("SELECT verlauf FROM reisen WHERE id = ?", (rid,)).fetchone()
        try:
            return json.loads(z["verlauf"]) if z and z["verlauf"] else None
        except ValueError:
            return None

    def reise(self, rid: str) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM reisen WHERE id = ?", (rid,)).fetchone()
        if not z:
            return None
        d = dict(z)
        d.pop("verlauf", None)
        return d

    def reisen(self, mindest: int = 0, tage: int = AUFBEWAHREN_TAGE) -> list[dict]:
        grenze = (config.jetzt().date() - timedelta(days=tage)).isoformat()
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT * FROM reisen WHERE tag >= ? AND verspaetung >= ? ORDER BY plan DESC", (grenze, mindest))
            return [{k: v for k, v in dict(z).items() if k != "verlauf"} for z in zeilen]

    def aufraeumen(self) -> int:
        """Nur der Zwischenstand gefundener Fahrten — Anträge bleiben immer."""
        grenze = (config.jetzt().date() - timedelta(days=AUFBEWAHREN_TAGE)).isoformat()
        with self._sperre, self._db:
            return self._db.execute("DELETE FROM reisen WHERE tag < ?", (grenze,)).rowcount

    # ---------- Anträge ----------

    def antrag_speichern(self, e: dict) -> int:
        felder = {k: (json.dumps(e.get(k) or ([] if k != "daten" else {}), ensure_ascii=False) if k in _JSON
                      else _iso(e.get(k)))
                  for k in ("reise", "jid", "linie", "richtung", "start", "ziel", "start_ref", "ziel_ref", "ab", "an",
                            "verspaetung", "quelle", "faellig_ab", "hindernisse", "hinweise", "fahrkarte", "daten")}
        felder["nachgeprueft"] = int(bool(e.get("nachgeprueft")))
        felder["profil"] = int(e.get("profil") or 0)
        spalten = ", ".join(felder)
        with self._sperre, self._db:
            return self._db.execute(
                f"INSERT INTO antraege (erstellt, {spalten}) VALUES (?, {', '.join('?' * len(felder))})",
                (_jetzt(), *felder.values())).lastrowid

    def antrag_aendern(self, aid: int, **felder) -> None:
        erlaubt = {"verspaetung", "quelle", "hindernisse", "hinweise", "nachgeprueft", "status", "abgeschickt_am",
                   "daten", "fahrkarte", "vorgang", "entscheidung"}
        werte = {k: (json.dumps(v, ensure_ascii=False) if k in _JSON else v) for k, v in felder.items() if k in erlaubt}
        if not werte:
            return
        with self._sperre, self._db:
            self._db.execute(f"UPDATE antraege SET {', '.join(f'{k} = ?' for k in werte)} WHERE id = ?",
                             (*werte.values(), aid))

    def _antrag_lesen(self, z: sqlite3.Row, mit_daten: bool = False) -> dict:
        d = dict(z)
        for k in ("hindernisse", "hinweise"):
            d[k] = json.loads(d[k] or "[]")
        if mit_daten:
            d["daten"] = json.loads(d["daten"] or "{}")
        else:
            # Die signierte Fahrt braucht nur das Abschicken; die Eingangsbestätigung zeigt die App.
            roh = d.pop("daten", None)
            try:
                d["gesendet"] = (json.loads(roh or "{}") or {}).get("gesendet")
            except ValueError:
                d["gesendet"] = None
        return d

    def antrag(self, aid: int, mit_daten: bool = False) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM antraege WHERE id = ?", (aid,)).fetchone()
        return self._antrag_lesen(z, mit_daten) if z else None

    def antraege(self, anzahl: int = 50, profil: int | None = 0) -> list[dict]:
        """Eines Profils (Vorgabe: Eike) — mit `profil=None` die aller."""
        with self._sperre:
            if profil is None:
                zeilen = self._db.execute("SELECT * FROM antraege ORDER BY id DESC LIMIT ?", (int(anzahl),)).fetchall()
            else:
                zeilen = self._db.execute("SELECT * FROM antraege WHERE profil = ? ORDER BY id DESC LIMIT ?",
                                          (int(profil), int(anzahl))).fetchall()
        return [self._antrag_lesen(z) for z in zeilen]

    def antrag_loeschen(self, aid: int) -> bool:
        """Nur Entwürfe: Abgeschickte und solche mit Vorgangsnummer bleiben — sie zählen für die Grenzen."""
        with self._sperre, self._db:
            return self._db.execute("DELETE FROM antraege WHERE id = ? AND status != 'abgeschickt' "
                                    "AND vorgang IS NULL", (aid,)).rowcount > 0

    def antrag_zum_vorgang(self, nummer: str) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM antraege WHERE vorgang = ? ORDER BY id DESC", (nummer,)).fetchone()
        return self._antrag_lesen(z) if z else None

    def antrag_zur_reise(self, rid: str, profil: int = 0) -> dict | None:
        """Eine Fahrt = ein Antrag PRO PERSON (Jarvis: zwei Anträge für denselben Bus von zwei Haltestellen).
        Fahren zwei Freunde im selben verspäteten Bus, hat jeder seinen eigenen."""
        with self._sperre:
            z = self._db.execute("SELECT * FROM antraege WHERE reise = ? AND profil = ? AND status != 'verworfen' "
                                 "ORDER BY id DESC", (rid, int(profil))).fetchone()
        return self._antrag_lesen(z) if z else None

    # ---------- Garantiefälle aus Gmail ----------

    def vorgang(self, nummer: str) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM vorgaenge WHERE vorgang = ?", (nummer,)).fetchone()
        return dict(z) if z else None

    def vorgaenge(self, anzahl: int = 50, profil: int | None = 0) -> list[dict]:
        """Die Fälle eines Profils (Vorgabe: Eike). Ein Fall gehört dem Profil seines Antrags; ohne Antrag
        (auf der Webseite selbst eingereicht) gehört er Eike. `profil=None`: alle."""
        with self._sperre:
            if profil is None:
                zeilen = self._db.execute("SELECT * FROM vorgaenge ORDER BY letzte_mail DESC LIMIT ?", (int(anzahl),))
            else:
                zeilen = self._db.execute(
                    "SELECT v.* FROM vorgaenge v LEFT JOIN antraege a ON a.id = v.antrag "
                    "WHERE COALESCE(a.profil, 0) = ? ORDER BY v.letzte_mail DESC LIMIT ?", (int(profil), int(anzahl)))
            return [dict(z) for z in zeilen]

    def vorgang_speichern(self, fall: dict) -> None:
        """Einen Fall so ablegen, wie post.faelle ihn aus allen Mails zusammengesetzt hat."""
        werte = tuple(fall.get(k) for k in _VORGANG_SPALTEN)
        with self._sperre, self._db:
            self._db.execute(f"INSERT OR REPLACE INTO vorgaenge ({', '.join(_VORGANG_SPALTEN)}) VALUES "
                             f"({', '.join('?' * len(_VORGANG_SPALTEN))})", werte)

    def vorgang_setzen(self, nummer: str, **felder) -> None:
        erlaubt = {k: v for k, v in felder.items() if k in ("antrag", "gemeldet", "erledigt")}
        if erlaubt:
            with self._sperre, self._db:
                self._db.execute(f"UPDATE vorgaenge SET {', '.join(f'{k} = ?' for k in erlaubt)} WHERE vorgang = ?",
                                 (*erlaubt.values(), nummer))

    # ---------- Angaben (Eike = Profil 0, Freunde = Profile) ----------

    def _angabendatei(self) -> Path:
        return self.ordner / "garantie_angaben.json"

    def _profildatei(self) -> Path:
        return self.ordner / "garantie_profile.json"

    def _profile_lesen(self) -> dict:
        try:
            d = json.loads(self._profildatei().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        d = d if isinstance(d, dict) else {}
        d.setdefault("naechste", 1)
        d.setdefault("profile", [])
        return d

    def _profile_schreiben(self, d: dict) -> None:
        _schreiben_600(self._profildatei(), json.dumps(d, ensure_ascii=False, indent=1).encode())

    def profile(self) -> list[dict]:
        """Alle Freunde-Profile, ROH (mit IBAN) — nur für die Ablage und die Prüfungen, nie an den Browser."""
        return self._profile_lesen()["profile"]

    def profil(self, pid: int) -> dict | None:
        return next((p for p in self.profile() if p["id"] == int(pid)), None) if pid else None

    def profil_name(self, pid: int) -> str:
        return "Ich" if not pid else ((self.profil(pid) or {}).get("name") or f"Profil {pid}")

    def profil_anlegen(self, d: dict) -> int:
        with self._sperre:
            alle = self._profile_lesen()
            pid = int(alle["naechste"])
            alle["naechste"] = pid + 1
            p = {"id": pid, "erstellt": _jetzt(), "auszahlung": "ich", "anteil": 0, "vollmacht": False,
                 "archiviert": False, "ausgezahlt": []}
            p.update({k: d[k] for k in PROFIL_FELDER if k in d and d[k] is not None})
            alle["profile"].append(p)
            self._profile_schreiben(alle)
            return pid

    def profil_aendern(self, pid: int, d: dict) -> None:
        with self._sperre:
            alle = self._profile_lesen()
            p = next((x for x in alle["profile"] if x["id"] == int(pid)), None)
            if not p:
                raise LookupError("Dieses Profil gibt es nicht.")
            for k in PROFIL_FELDER:
                if k in d and d[k] is not None:
                    if k == "iban" and not str(d[k]).strip():        # leer = die gespeicherte behalten
                        continue
                    p[k] = d[k]
            self._profile_schreiben(alle)

    def profil_loeschen(self, pid: int) -> bool:
        """Nur, wenn noch kein Antrag dran hängt (Anträge zählen für Höchstzahlen und Abrechnung). Seine hochgeladenen
        Tickets gehen mit — ohne Antrag belegen sie nichts, und ein abgebrochenes Anlegen hinterlässt so nichts."""
        with self._sperre:
            hat_antraege = self._db.execute("SELECT 1 FROM antraege WHERE profil = ? LIMIT 1", (int(pid),)).fetchone()
        if hat_antraege:
            return False
        with self._sperre:
            alle = self._profile_lesen()
            neu = [p for p in alle["profile"] if p["id"] != int(pid)]
            if len(neu) == len(alle["profile"]):
                return False
            alle["profile"] = neu
            self._profile_schreiben(alle)
            tickets = self._alle_tickets()
            self._tickets_schreiben([t for t in tickets if int(t.get("profil", 0)) != int(pid)])
        for t in tickets:
            if int(t.get("profil", 0)) == int(pid) and t.get("datei"):
                (self.ordner / "garantie" / "tickets" / str(t["datei"])).unlink(missing_ok=True)
        return True

    def ausgezahlt_setzen(self, pid: int, vorgang: str, ja: bool) -> None:
        """Eikes Buchführung: Hat der Freund sein Geld zu diesem Fall schon bekommen?"""
        with self._sperre:
            alle = self._profile_lesen()
            p = next((x for x in alle["profile"] if x["id"] == int(pid)), None)
            if not p:
                raise LookupError("Dieses Profil gibt es nicht.")
            liste = [v for v in p.get("ausgezahlt", []) if v != vorgang]
            p["ausgezahlt"] = liste + [vorgang] if ja else liste
            self._profile_schreiben(alle)

    def angaben(self, profil: int = 0, roh: bool = False) -> dict:
        """Die Angaben, wie sie ins Formular gehen. Bei Freunden mit Auszahlung „ich“ stehen als Konto
        Eikes Daten drin — `roh=True` zeigt stattdessen, was am Profil selbst gespeichert ist."""
        if not profil:
            try:
                d = json.loads(self._angabendatei().read_text(encoding="utf-8"))
                return d if isinstance(d, dict) else {}
            except (OSError, ValueError):
                return {}
        p = self.profil(profil) or {}
        d = {k: p[k] for k in ANGABEN if k in p}
        if not roh and p.get("auszahlung", "ich") == "ich":
            eike = self.angaben(0)
            d["kontoinhaber"], d["iban"] = eike.get("kontoinhaber", ""), eike.get("iban", "")
        return d

    def angaben_setzen(self, neu: dict, profil: int = 0) -> None:
        if profil:
            self.profil_aendern(profil, {k: (str(neu[k]).strip() if k != "iban" else str(neu[k]).replace(" ", "").upper())
                                         for k in ANGABEN if k in neu and neu[k] is not None})
            return
        with self._sperre:
            d = self.angaben()
            for k in ANGABEN:
                if k in neu and neu[k] is not None:
                    wert = str(neu[k]).strip()
                    if k == "iban":
                        wert = wert.replace(" ", "").upper()
                        if not wert:                     # leer = die gespeicherte behalten
                            continue
                    d[k] = wert
            _schreiben_600(self._angabendatei(), json.dumps(d, ensure_ascii=False, indent=1).encode())

    def fehlende_angaben(self, profil: int = 0) -> list[str]:
        d = self.angaben(profil)
        return [k for k in PFLICHT if not d.get(k)]

    def angaben_sicher(self, profil: int = 0) -> dict:
        """Für den Browser: alles außer der IBAN, die nur verdeckt."""
        d = self.angaben(profil, roh=True)
        iban = d.pop("iban", "")
        d["iban_verdeckt"] = f"{iban[:2]}•• … {iban[-4:]}" if iban else ""
        d["fehlt"] = self.fehlende_angaben(profil)
        if profil:
            p = self.profil(profil) or {}
            d.update({k: p.get(k, "") for k in ("name", "notiz")}, id=profil, anteil=p.get("anteil") or 0,
                     auszahlung=p.get("auszahlung", "ich"), vollmacht=bool(p.get("vollmacht")),
                     archiviert=bool(p.get("archiviert")))
        return d

    # ---------- Tickets ----------
    # Jeden Monat ein neues (Eike, 15.09.2026) — die alten bleiben: Ein Antrag für den
    # 30.09., vorbereitet am 02.10., braucht noch das Septemberticket.

    def _ticketliste(self) -> Path:
        return self.ordner / "garantie" / "tickets.json"

    def _alle_tickets(self) -> list[dict]:
        """Die Tickets ALLER Profile, neuestes zuerst — nur intern, damit beim Schreiben nichts fremdes verloren geht."""
        try:
            liste = json.loads(self._ticketliste().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            liste = []
        return sorted(liste, key=lambda t: t.get("seit", ""), reverse=True)

    def tickets(self, profil: int = 0) -> list[dict]:
        """Neuestes zuerst — nur die dieses Profils (Vorgabe: Eike)."""
        return [t for t in self._alle_tickets() if int(t.get("profil", 0)) == int(profil)]

    def _tickets_schreiben(self, liste: list[dict]) -> None:
        _schreiben_600(self._ticketliste(), json.dumps(liste, ensure_ascii=False, indent=1).encode())

    def ticket_speichern(self, name: str, mime: str, inhalt: bytes, erkannt: dict | None = None, profil: int = 0) -> dict:
        endung = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
                  "image/heic": "heic", "image/heif": "heif"}[mime]
        # Eikes Tickets behalten ihre alten Kennungen; dasselbe Bild bei einem Freund ist ein eigenes Ticket.
        tid = hashlib.sha1(inhalt + (f"|profil{int(profil)}".encode() if profil else b"")).hexdigest()[:10]
        _schreiben_600(self.ordner / "garantie" / "tickets" / f"{tid}.{endung}", inhalt)
        erkannt = erkannt or {}
        info = {"id": tid, "name": name[:120], "mime": mime, "groesse": len(inhalt), "seit": _jetzt(),
                "datei": f"{tid}.{endung}", "erkannt": bool(erkannt), "profil": int(profil),
                **{k: erkannt.get(k, "") for k in ("art", "bezeichnung", "herausgeber", "gueltig_von", "gueltig_bis",
                                                    "inhaber", "abo_nummer", "kundenkarte", "ticketnummer", "zweifel",
                                                    "form", "nummer_gedruckt")},
                "ist_kundenkarte": bool(erkannt.get("ist_kundenkarte")),
                "kundenkarte_handschrift": bool(erkannt.get("kundenkarte_handschrift")),
                "nummern_geprueft": False, "preis": erkannt.get("preis")}
        with self._sperre:
            alle = self._alle_tickets()
            alt = next((t for t in alle if t.get("id") == tid), None)
            if alt and alt.get("pruefung_quelle") == "person":
                # Dasselbe Bild noch einmal gespeichert (z. B. von der Ticketannahme der Wache): Was die Person bestätigt oder
                # korrigiert hat, bleibt — die Lesewerte überschreiben es nicht (Review 14.10.2026).
                info.update({k: alt[k] for k in ("kundenkarte", "ticketnummer", "abo_nummer", "nummern_geprueft", "pruefung_quelle",
                                                 "pruefung_zeit", "person_korrigiert") if k in alt})
            liste = [t for t in alle if t.get("id") != tid]                     # dasselbe Bild zweimal: einmal
            self._tickets_schreiben(liste + [info])
        return info

    def ticket_korrigieren(self, tid: str, felder: dict) -> dict:
        with self._sperre:
            liste = self._alle_tickets()
            t = next((t for t in liste if t.get("id") == tid), None)
            if not t:
                raise LookupError("Dieses Ticket gibt es nicht.")
            vorher = (t.get("kundenkarte") or "", t.get("ticketnummer") or "")
            for k in ("art", "gueltig_von", "gueltig_bis", "herausgeber", "kundenkarte", "ticketnummer", "inhaber", "form"):
                if k in felder and felder[k] is not None:
                    t[k] = str(felder[k]).strip()
            if "preis" in felder:                                    # leer = kein Preis bekannt
                t["preis"] = float(felder["preis"]) if felder["preis"] not in (None, "", 0) else None
            # „Nummern mit dem Original verglichen“ gilt nur für genau die Nummern, die Eike dabei gesehen hat: Ändert sich eine
            # ohne neues Häkchen, ist der Vergleich hinfällig.
            if felder.get("nummern_geprueft") is not None:
                t["nummern_geprueft"] = bool(felder["nummern_geprueft"])
            elif (t.get("kundenkarte") or "", t.get("ticketnummer") or "") != vorher:
                t["nummern_geprueft"] = False
            t["korrigiert"] = True
            self._tickets_schreiben(liste)
        return t

    def ticket_merken(self, tid: str, **felder) -> None:
        with self._sperre:
            liste = self._alle_tickets()
            for t in liste:
                if t.get("id") == tid:
                    t.update(felder)
            self._tickets_schreiben(liste)

    def ticket(self, profil: int = 0) -> dict | None:
        """Das zuletzt hochgeladene Ticket dieses Profils mit Gültigkeit — eine Kundenkarte ohne Datum ist kein Monatsticket."""
        liste = self.tickets(profil)
        return next((t for t in liste if t.get("gueltig_bis")), liste[0] if liste else None)

    def ticket_fuer(self, tag, profil: int = 0) -> dict | None:
        """Das Ticket dieses Profils, das an diesem Tag galt. Ohne erkannte Gültigkeit keins —
        sonst gälte ein Oktoberticket still für eine Fahrt im September."""
        tag = tag.isoformat() if hasattr(tag, "isoformat") else str(tag)
        for t in self.tickets(profil):
            if t.get("gueltig_von") and t.get("gueltig_bis") and t["gueltig_von"] <= tag[:10] <= t["gueltig_bis"]:
                return t
        return None


_instanzen: dict[str, Ablage] = {}


def standard() -> Ablage:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Ablage(ordner)
    return _instanzen[str(ordner)]


def iban_gueltig(iban: str) -> bool:
    """Prüfsumme nach ISO 13616 (mod 97) — fängt Tippfehler, bevor sie beim GVH landen."""
    iban = (iban or "").replace(" ", "").upper()
    if len(iban) < 15 or len(iban) > 34 or not iban[:2].isalpha() or not iban[2:4].isdigit() or not iban.isalnum():
        return False
    zahl = "".join(str(int(z, 36)) for z in iban[4:] + iban[:4])
    return int(zahl) % 97 == 1
