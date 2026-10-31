"""Garantie-Verwaltung (11.10.2026): feste Status, Verfolgung bis zum Bescheid, Nachfassen.

Bisher war der Stand eines Antrags Freitext in zwei Tabellen (`antraege.status` offen/abgeschickt/verworfen, dazu
`vorgaenge.status` aus den Gmail-Bescheiden) und nirgends stand, was als Nächstes zu tun ist. Dieses Modul legt eine
**Phase** über den Bestand — ohne ihn anzufassen: `antraege`, `vorgaenge`, `bahnantraege` bleiben, wie sie sind (ein Test
vergleicht Zeile für Zeile). Die Phase steht in eigenen Tabellen derselben `garantie.db`:

- `garantie_phase`: eine Zeile je Vorgang (`A12` = ÜSTRA-Antrag, `V8854083` = Fall ohne Antrag in Friday, `B3` = Bahn-Antrag)
  mit Phase und dem Zeitpunkt, seit dem sie gilt.
- `garantie_historie`: jeder Übergang mit Zeitpunkt, von, nach, Grund — auch abgelehnte Versuche (`ok = 0`).
- `garantie_erinnert`: welcher Schritt schon gemeldet wurde (je Vorgang, Schritt und Phasen-Eintritt genau einmal).
- `garantie_meta`: ob die Grundlinie gelegt ist.

Die Phase wird aus dem Bestand abgeleitet (`abgleichen`) und von Eike per Werkzeug oder App ergänzt (`setzen`). Beides läuft
durch dieselbe Übergangsprüfung. Was hier NIE geschieht: abschicken, mailen, jemand anderem als Eike etwas melden.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from kern import config
from kern.garantie import ablage, antrag, bahnablage, post

log = logging.getLogger("friday.garantie")

BERLIN = ZoneInfo(config.ZEITZONE)

PHASEN = ("offen", "vorbereitet", "freigegeben_wartet", "abgeschickt", "eingang_bestaetigt", "rueckfrage_offen",
          "bescheid_positiv", "bescheid_abgelehnt", "widerspruch", "erledigt", "verworfen")
PHASE_TEXT = {"offen": "Entwurf", "vorbereitet": "Vorbereitet", "freigegeben_wartet": "Freigegeben, wartet aufs Abschicken",
              "abgeschickt": "Abgeschickt", "eingang_bestaetigt": "Eingang bestätigt", "rueckfrage_offen": "Rückfrage offen",
              "bescheid_positiv": "Genehmigt", "bescheid_abgelehnt": "Abgelehnt", "widerspruch": "Widerspruch eingelegt",
              "erledigt": "Erledigt", "verworfen": "Verworfen"}
# Erlaubte Übergänge. Nach „abgeschickt“ darf vieles folgen (eine Eingangsmail kann ausbleiben); zurück zu offen/vorbereitet nie.
# „abgeschickt“ ist von außerhalb des Abschickens nicht erreichbar: nur `abschicken.py` / die App setzen den Antrag auf abgeschickt.
UEBERGAENGE = {
    "offen": ("vorbereitet", "verworfen"),
    "vorbereitet": ("offen", "freigegeben_wartet", "abgeschickt", "verworfen"),
    "freigegeben_wartet": ("vorbereitet", "abgeschickt", "verworfen"),
    "abgeschickt": ("eingang_bestaetigt", "rueckfrage_offen", "bescheid_positiv", "bescheid_abgelehnt"),
    "eingang_bestaetigt": ("rueckfrage_offen", "bescheid_positiv", "bescheid_abgelehnt"),
    "rueckfrage_offen": ("eingang_bestaetigt", "bescheid_positiv", "bescheid_abgelehnt", "erledigt"),
    "bescheid_positiv": ("erledigt",),
    "bescheid_abgelehnt": ("widerspruch", "erledigt"),
    "widerspruch": ("bescheid_positiv", "bescheid_abgelehnt", "erledigt"),
    "erledigt": (),
    "verworfen": ("offen",),
}
RANG = {"verworfen": 0, "offen": 0, "vorbereitet": 1, "freigegeben_wartet": 2, "abgeschickt": 3, "eingang_bestaetigt": 4,
        "rueckfrage_offen": 5, "bescheid_positiv": 6, "bescheid_abgelehnt": 6, "widerspruch": 7, "erledigt": 8}
OFFEN_NICHT = ("erledigt", "verworfen")
# Was Eike per Werkzeug melden darf. „abgeschickt“ gehört nicht dazu: Abgeschickt wird nur per Knopf mit Bestätigung.
MELDBAR = ("eingang_bestaetigt", "rueckfrage_offen", "bescheid_positiv", "bescheid_abgelehnt", "widerspruch", "erledigt")

# Fristen und Wartezeiten in Tagen. Antragsfrist: `antrag.FRIST_TAGE`. Die Widerspruchsfrist ist KEINE Vorgabe der ÜSTRA (die
# Garantie ist ein freiwilliges Versprechen, kein Verwaltungsakt) — 30 Tage sind Eikes Entscheidungsfenster, nicht Recht.
FRIST_VORLAUF_TAGE = 3            # so viele Tage vor Ablauf der Antragsfrist
EINGANG_TAGE = 3                  # abgeschickt, aber keine Eingangsbestätigung
BESCHEID_TAGE = 21                # Eingang bestätigt, aber kein Bescheid (ÜSTRA)
BAHN_BESCHEID_TAGE = 30           # Bahn: eingereicht, kein Bescheid
RUECKFRAGE_TAGE = 5               # Rückfrage unbeantwortet — die ÜSTRA setzt 14 Tage
RUECKFRAGE_FRIST_TAGE = 14
WIDERSPRUCH_TAGE = 30
WIDERSPRUCH_NACHFASSEN_TAGE = 21
GELD_TAGE = 10                    # genehmigt, aber noch nicht als „Geld ist da“ gemeldet
ABSCHICKEN_TAGE = 1
MAX_UEBERFAELLIG_TAGE = 14        # länger Überfälliges wird angezeigt, aber nicht mehr gepusht
MAX_JE_LAUF = 3                   # höchstens so viele neue Erinnerungen je Durchgang

NACHFASSEN = ("eingang_nachfassen", "bescheid_nachfassen", "widerspruch_nachfassen")
AKTIVE_STAENDE = ("bereit", "unvollständig", "wartet", "wird nachgeprüft")

SCHEMA = """
CREATE TABLE IF NOT EXISTS garantie_phase (
    ref TEXT PRIMARY KEY, phase TEXT NOT NULL, seit TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS garantie_historie (
    id INTEGER PRIMARY KEY, ref TEXT NOT NULL, zeit TEXT NOT NULL, von TEXT, nach TEXT NOT NULL, grund TEXT,
    ok INTEGER NOT NULL DEFAULT 1, quelle TEXT);
CREATE INDEX IF NOT EXISTS garantie_historie_ref ON garantie_historie(ref, id);
CREATE TABLE IF NOT EXISTS garantie_erinnert (
    ref TEXT NOT NULL, schritt TEXT NOT NULL, seit TEXT NOT NULL, zeit TEXT NOT NULL, PRIMARY KEY (ref, schritt, seit));
CREATE TABLE IF NOT EXISTS garantie_meta (schluessel TEXT PRIMARY KEY, wert TEXT);
"""
_sperre = threading.RLock()


def folgephasen(von: str) -> list[dict]:
    """Was Eike von hier aus per Knopf setzen darf: die erlaubten Folgephasen ohne „abgeschickt“ (das bleibt dem Abschicken)."""
    return [{"phase": p, "text": PHASE_TEXT[p]} for p in UEBERGAENGE.get(von, ()) if p != "abgeschickt"]


def uebergang_erlaubt(von: str | None, nach: str) -> bool:
    return nach in UEBERGAENGE.get(von or "", ())


# ---------- Ablage ----------

def _a() -> ablage.Ablage:
    a = ablage.standard()
    if not getattr(a, "_verwaltung_bereit", False):
        with a._sperre, a._db:
            a._db.executescript(SCHEMA)
        a._verwaltung_bereit = True
    return a


def _jetzt() -> datetime:
    return config.jetzt()


def _dt(wert) -> datetime | None:
    if isinstance(wert, datetime):
        z = wert
    else:
        try:
            z = datetime.fromisoformat(str(wert).replace("Z", "+00:00")) if wert else None
        except ValueError:
            return None
    if z is None:
        return None
    return z.replace(tzinfo=BERLIN) if z.tzinfo is None else z.astimezone(BERLIN)


def _iso(z: datetime) -> str:
    return z.astimezone(BERLIN).isoformat(timespec="seconds")


def _zeile(ref: str) -> dict | None:
    a = _a()
    with a._sperre:
        z = a._db.execute("SELECT * FROM garantie_phase WHERE ref = ?", (ref,)).fetchone()
    return dict(z) if z else None


def phase(ref: str) -> str | None:
    z = _zeile(ref)
    return z["phase"] if z else None


def historie(ref: str) -> list[dict]:
    """Alle Übergänge eines Vorgangs, ältester zuerst — auch die abgelehnten Versuche (`ok = 0`)."""
    a = _a()
    with a._sperre:
        return [dict(z) for z in a._db.execute("SELECT * FROM garantie_historie WHERE ref = ? ORDER BY id", (ref,))]


def _protokoll(ref: str, von: str | None, nach: str, grund: str, ok: bool, quelle: str, jetzt: datetime) -> None:
    a = _a()
    with a._sperre, a._db:
        a._db.execute("INSERT INTO garantie_historie (ref, zeit, von, nach, grund, ok, quelle) VALUES (?,?,?,?,?,?,?)",
                      (ref, _iso(jetzt), von, nach, (grund or "")[:300], int(ok), quelle))


def _wechseln(ref: str, von: str | None, nach: str, seit: datetime, grund: str, quelle: str, jetzt: datetime) -> None:
    a = _a()
    with a._sperre, a._db:
        a._db.execute("INSERT INTO garantie_phase (ref, phase, seit) VALUES (?,?,?) "
                      "ON CONFLICT (ref) DO UPDATE SET phase = excluded.phase, seit = excluded.seit",
                      (ref, nach, _iso(seit)))
    _protokoll(ref, von, nach, grund, True, quelle, jetzt)


def vermerken(ref: str, grund: str, quelle: str) -> bool:
    """Eine Aktion OHNE Phasenwechsel in die Historie schreiben (z. B. „Antrag aus Akte vorbereitet, wartet auf Eikes OK“).
    Die Phase bleibt, wie sie ist; unbekannte Vorgänge werden nicht angelegt."""
    with _sperre:
        z = _zeile(ref)
        if z is None:
            return False
        _protokoll(ref, z["phase"], z["phase"], grund, True, quelle, _jetzt())
        return True


def _abgelehnt_schon_protokolliert(ref: str, von: str, nach: str) -> bool:
    h = historie(ref)
    return bool(h) and (h[-1]["von"], h[-1]["nach"], h[-1]["ok"]) == (von, nach, 0)


def erinnert(ref: str, schritt: str, seit: str | None = None) -> bool:
    a = _a()
    z = _zeile(ref)
    with a._sperre:
        return a._db.execute("SELECT 1 FROM garantie_erinnert WHERE ref = ? AND schritt = ? AND seit = ?",
                             (ref, schritt, seit or (z or {}).get("seit") or "")).fetchone() is not None


def _erinnert_merken(ref: str, schritt: str, seit: str, jetzt: datetime) -> None:
    a = _a()
    with a._sperre, a._db:
        a._db.execute("INSERT OR IGNORE INTO garantie_erinnert (ref, schritt, seit, zeit) VALUES (?,?,?,?)",
                      (ref, schritt, seit, _iso(jetzt)))


def _meta(schluessel: str) -> str | None:
    a = _a()
    with a._sperre:
        z = a._db.execute("SELECT wert FROM garantie_meta WHERE schluessel = ?", (schluessel,)).fetchone()
    return z["wert"] if z else None


def _meta_setzen(schluessel: str, wert: str) -> None:
    a = _a()
    with a._sperre, a._db:
        a._db.execute("INSERT OR REPLACE INTO garantie_meta (schluessel, wert) VALUES (?,?)", (schluessel, wert))


# ---------- Ableitung aus dem Bestand ----------

_GMAIL_PHASE = {"eingang": "eingang_bestaetigt", "genehmigt": "bescheid_positiv", "abgelehnt": "bescheid_abgelehnt",
                "rueckfrage": "rueckfrage_offen", "erledigt": "eingang_bestaetigt", "ohne_bescheid": "eingang_bestaetigt"}


def _fahrt_text(linie: str, start: str, ziel: str, tag) -> str:
    teile = []
    if linie:
        teile.append(f"Linie {linie}")
    if start or ziel:
        teile.append(f"{start or '?'} → {ziel or '?'}")
    text = " ".join(teile) or "Fahrt"
    if tag:
        text += f" am {tag:%d.%m.}"
    return text


def _einheiten(jetzt: datetime) -> list[dict]:
    """Alle Vorgänge des Bestands mit der Phase, die aus ihnen folgt. Liest nur."""
    a = _a()
    faelle = {v["vorgang"]: v for v in a.vorgaenge(100000, None)}
    einh: list[dict] = []
    belegt: set = set()
    for e in a.antraege(100000, None):
        v = faelle.get(e.get("vorgang") or "")
        an = _dt(e.get("an"))
        u = {"ref": f"A{e['id']}", "art": "antrag", "profil": int(e.get("profil") or 0), "vorgang": e.get("vorgang") or "",
             "fahrt_an": an, "stand": "", "titel": _fahrt_text(e.get("linie") or "", e.get("start") or "", e.get("ziel") or "", an),
             "entschieden": None}
        if v:
            belegt.add(v["vorgang"])
        if e["status"] == "verworfen":
            u["phase"], u["seit_bestand"] = "verworfen", _dt(e.get("erstellt"))
        elif e["status"] == "offen":
            try:
                u["stand"] = antrag.bewerten(e)["stand"]
            except Exception:                                  # noqa: BLE001 — eine unlesbare Bewertung darf nichts kippen
                u["stand"] = ""
            u["phase"] = "vorbereitet" if u["stand"] == "bereit" else "offen"
            u["seit_bestand"] = _dt(e.get("erstellt"))
        else:                                                  # abgeschickt
            stand = post.anzeige_stand(v) if v else (e.get("entscheidung") or "")
            u["phase"] = _GMAIL_PHASE.get(stand, "abgeschickt")
            if u["phase"] == "bescheid_positiv" and u["profil"] and e.get("vorgang") in (
                    (a.profil(u["profil"]) or {}).get("ausgezahlt") or []):
                u["phase"] = "erledigt"                        # Eikes Buchführung: der Freund hat sein Geld schon bekommen
            if v and u["phase"] in ("bescheid_positiv", "bescheid_abgelehnt", "erledigt"):
                u["seit_bestand"] = _dt(v.get("entschieden_am") or v.get("letzte_mail"))
            elif v and u["phase"] == "rueckfrage_offen":
                u["seit_bestand"] = _dt(v.get("letzte_mail"))
            elif v and u["phase"] == "eingang_bestaetigt":
                u["seit_bestand"] = _dt(v.get("eingang_am") or v.get("letzte_mail"))
            else:
                u["seit_bestand"] = _dt(e.get("abgeschickt_am") or e.get("erstellt"))
        einh.append(u)
    for nummer, v in faelle.items():
        if nummer in belegt or v.get("antrag"):
            continue
        stand = post.anzeige_stand(v)
        ph = _GMAIL_PHASE.get(stand)
        if not ph:
            continue
        tag = _dt(f"{v['fahrtdatum']}T00:00:00") if v.get("fahrtdatum") else None
        einh.append({"ref": f"V{nummer}", "art": "vorgang", "profil": 0, "vorgang": nummer, "fahrt_an": tag, "stand": "",
                     "titel": _fahrt_text(v.get("linie") or "", v.get("start") or "", v.get("ziel") or "", tag),
                     "phase": ph, "entschieden": None,
                     "seit_bestand": _dt((v.get("entschieden_am") if ph in ("bescheid_positiv", "bescheid_abgelehnt") else None)
                                         or v.get("eingang_am") or v.get("letzte_mail"))})
    for b in bahnablage.antraege(None, 100000):
        ph = {"genehmigt": "bescheid_positiv", "abgelehnt": "bescheid_abgelehnt"}.get(b["status"], "abgeschickt")
        seit = _dt(b.get("entschieden_am")) if ph != "abgeschickt" else _dt(b.get("eingereicht_am") or b.get("erstellt"))
        einh.append({"ref": f"B{b['id']}", "art": "bahn", "profil": int(b.get("profil") or 0), "vorgang": b.get("vorgang") or "",
                     "fahrt_an": _dt(b.get("bis") or b.get("von")), "stand": "", "phase": ph,
                     "titel": f"Bahn-Fahrgastrechte {b.get('ticket') or b.get('art') or ''}".strip(),
                     "seit_bestand": seit or _dt(b.get("erstellt"))})
    for u in einh:
        if not u.get("seit_bestand") or u["seit_bestand"] > jetzt:
            u["seit_bestand"] = jetzt
    return einh


def _abgleichen(jetzt: datetime) -> tuple[dict, list[dict]]:
    with _sperre:
        a = _a()
        erster = _meta("grundlinie") is None
        einh = _einheiten(jetzt)
        neu = geaendert = abgelehnt = 0
        for u in einh:
            z = _zeile(u["ref"])
            if z is None:
                _wechseln(u["ref"], None, u["phase"], u["seit_bestand"], "Übernahme aus dem bisherigen Stand", "bestand", jetzt)
                neu += 1
                u["gespeichert"] = {"phase": u["phase"], "seit": _iso(u["seit_bestand"])}
                continue
            u["gespeichert"] = {"phase": z["phase"], "seit": z["seit"]}
            if z["phase"] == u["phase"]:
                continue
            if z["phase"] in OFFEN_NICHT:
                continue                                       # beendet (erledigt/verworfen) öffnet nur Eike wieder, nie der Bestand
            # Ging der Antrag über die App raus, ohne dass die Phase mitzog, hängt sie auf offen/vorbereitet fest. Der Bestand
            # darf dann (nur er, nicht Eike) direkt in die tatsächliche Phase nachziehen (27.10.2026).
            nachholen = z["phase"] in ("offen", "vorbereitet", "freigegeben_wartet") and RANG[u["phase"]] >= RANG["abgeschickt"]
            if nachholen or uebergang_erlaubt(z["phase"], u["phase"]):
                if u["seit_bestand"] < (_dt(z["seit"]) or u["seit_bestand"]):
                    continue                                   # alte Nachricht, die Eike schon überholt hat (z. B. Widerspruch)
                _wechseln(u["ref"], z["phase"], u["phase"], u["seit_bestand"], "Stand aus Friday/Gmail", "bestand", jetzt)
                u["gespeichert"] = {"phase": u["phase"], "seit": _iso(u["seit_bestand"])}
                geaendert += 1
            elif RANG[u["phase"]] < RANG[z["phase"]]:
                continue                                       # der Bestand hinkt hinterher (z. B. Eike meldete vor der Mail)
            elif not _abgelehnt_schon_protokolliert(u["ref"], z["phase"], u["phase"]):
                _protokoll(u["ref"], z["phase"], u["phase"], "Übergang nicht vorgesehen — abgelehnt", False, "bestand", jetzt)
                abgelehnt += 1
        if erster:
            _meta_setzen("grundlinie", _iso(jetzt))
            # Der Altbestand meldet sich nicht auf einen Schlag: Was jetzt schon fällig ist, gilt als gesehen.
            # Es bleibt in `faellig`/`offene` sichtbar, wird aber nicht gepusht.
            for f in _faellige(einh, jetzt):
                _erinnert_merken(f["ref"], f["schritt"], f["seit"], jetzt)
        return {"neu": neu, "geaendert": geaendert, "abgelehnt": abgelehnt, "erster": erster}, einh


def abgleichen(jetzt: datetime | None = None) -> dict:
    """Phasen aus dem Bestand nachziehen. Idempotent: ein zweiter Lauf ändert und protokolliert nichts."""
    return _abgleichen(jetzt or _jetzt())[0]


# ---------- Setzen (Eike) ----------

def _bahn_id(ref: str) -> int:
    return int(ref[1:])


def setzen(ref: str, nach: str, grund: str = "", quelle: str = "eike", jetzt: datetime | None = None,
           betrag_cent: int | None = None) -> str:
    """Eine Phase setzen — nur über erlaubte Übergänge. Unzulässiges wird protokolliert (`ok = 0`) und wirft `ValueError`."""
    jetzt = jetzt or _jetzt()
    if nach not in PHASEN:
        raise ValueError(f"„{nach}“ ist kein bekannter Status.")
    with _sperre:
        _abgleichen(jetzt)
        z = _zeile(ref)
        if z is None:
            raise LookupError("Diesen Vorgang kenne ich nicht.")
        von = z["phase"]
        if von == nach:
            return nach
        if not uebergang_erlaubt(von, nach):
            _protokoll(ref, von, nach, grund or "Übergang nicht vorgesehen", False, quelle, jetzt)
            erlaubt = ", ".join(PHASE_TEXT[p] for p in UEBERGAENGE[von]) or "keiner mehr"
            raise ValueError(f"Von „{PHASE_TEXT[von]}“ nach „{PHASE_TEXT[nach]}“ ist nicht vorgesehen "
                             f"(erlaubt von hier: {erlaubt}).")
        if ref.startswith("B") and nach in ("bescheid_positiv", "bescheid_abgelehnt"):
            from kern.garantie import fahrgastrechte
            if nach == "bescheid_positiv":
                if betrag_cent is None or betrag_cent < 0:
                    raise ValueError("Bei der Bahn brauche ich dazu den erhaltenen Betrag.")
                fahrgastrechte.bescheid(_bahn_id(ref), "genehmigt", betrag_cent, grund or "")
            else:
                fahrgastrechte.bescheid(_bahn_id(ref), "abgelehnt", None, grund or "")
        if von == "rueckfrage_offen" and not ref.startswith("B"):
            nummer = ref[1:] if ref.startswith("V") else ((ablage.standard().antrag(int(ref[1:])) or {}).get("vorgang") or "")
            v = ablage.standard().vorgang(nummer) if nummer else None
            if v and v.get("status") == "rueckfrage":          # derselbe Haken wie „Rückfrage erledigt“ in der App
                ablage.standard().vorgang_setzen(nummer, erledigt=v.get("letzte_mail") or "?")
        _wechseln(ref, von, nach, jetzt, grund or "", quelle, jetzt)
        return nach


def alle_abschliessen(grund: str = "", jetzt: datetime | None = None) -> dict:
    """Sammelabschluss (Knopf „Alle abhaken“): jeder offene Vorgang wird beendet, ohne Mail und ohne Absenden. Noch nie
    Eingereichtes (offen/vorbereitet/wartet) wird „verworfen“, alles Übrige „erledigt“. Die Übergangsprüfung wird hier
    bewusst übergangen — es ist Eikes ausdrücklicher Schlussstrich —, jeder Vorgang bekommt aber eine Historienzeile."""
    jetzt = jetzt or _jetzt()
    grund = (grund or "Sammelabschluss durch Eike")[:200]
    zaehler = {"erledigt": 0, "verworfen": 0}
    with _sperre:
        einh = _abgleichen(jetzt)[1]
        for f in _offene(einh, jetzt):
            von = f["phase"]
            nach = "verworfen" if von in ("offen", "vorbereitet", "freigegeben_wartet") else "erledigt"
            _wechseln(f["ref"], von, nach, jetzt, grund, "eike", jetzt)
            zaehler[nach] += 1
    return zaehler


# ---------- Nächster Schritt ----------

def _schritt(u: dict, ph: str, seit: datetime, jetzt: datetime) -> dict | None:
    """Der nächste Schritt dieses Vorgangs: Name, ab wann fällig (None = nie mehr / unbekannt), Frist."""
    def tage(n):
        return seit + timedelta(days=n)

    if ph in ("offen", "vorbereitet"):
        an = u.get("fahrt_an")
        if not an or (ph == "offen" and u.get("stand") not in AKTIVE_STAENDE):
            return None                                        # nicht beantragbar (Limit, kein Anspruch): nichts zu erinnern
        frist = an.date() + timedelta(days=antrag.FRIST_TAGE)
        if jetzt.date() > frist:
            return {"schritt": "frist_vorbei", "faellig_ab": None, "frist": frist}
        return {"schritt": "antragsfrist", "faellig_ab": datetime.combine(frist - timedelta(days=FRIST_VORLAUF_TAGE),
                                                                           datetime.min.time(), BERLIN), "frist": frist}
    if ph == "freigegeben_wartet":
        return {"schritt": "abschicken_pruefen", "faellig_ab": tage(ABSCHICKEN_TAGE), "frist": None}
    if ph == "abgeschickt":
        if u["art"] == "bahn":
            return {"schritt": "bescheid_nachfassen", "faellig_ab": tage(BAHN_BESCHEID_TAGE), "frist": None}
        return {"schritt": "eingang_nachfassen", "faellig_ab": tage(EINGANG_TAGE), "frist": None}
    if ph == "eingang_bestaetigt":
        return {"schritt": "bescheid_nachfassen", "faellig_ab": tage(BESCHEID_TAGE), "frist": None}
    if ph == "rueckfrage_offen":
        return {"schritt": "rueckfrage_antworten", "faellig_ab": tage(RUECKFRAGE_TAGE),
                "frist": (seit + timedelta(days=RUECKFRAGE_FRIST_TAGE)).date()}
    if ph == "bescheid_abgelehnt":
        frist = (seit + timedelta(days=WIDERSPRUCH_TAGE)).date()
        if u["art"] == "bahn" and u.get("fahrt_an"):           # Bahn: nie über die Verjährung hinaus (`fristen`)
            from kern.garantie import fahrgastrechte
            try:
                frist = min(frist, date.fromisoformat(fahrgastrechte.fristen(u["fahrt_an"].date())["spaetestens"]))
            except (ValueError, KeyError):
                pass
        if jetzt.date() > frist:
            return {"schritt": "frist_vorbei", "faellig_ab": None, "frist": frist}
        return {"schritt": "widerspruch_pruefen", "faellig_ab": tage(1), "frist": frist}
    if ph == "widerspruch":
        return {"schritt": "widerspruch_nachfassen", "faellig_ab": tage(WIDERSPRUCH_NACHFASSEN_TAGE), "frist": None}
    if ph == "bescheid_positiv":
        return {"schritt": "geld_pruefen", "faellig_ab": tage(GELD_TAGE), "frist": None}
    return None


SCHRITT_TEXT = {
    "antragsfrist": "Die Antragsfrist läuft am {frist} ab — der Antrag ist noch nicht abgeschickt.",
    "frist_vorbei": "Die Antragsfrist ist abgelaufen.",
    "abschicken_pruefen": "Freigegeben, aber noch nicht abgeschickt.",
    "eingang_nachfassen": "Seit {tage} Tagen keine Eingangsbestätigung der ÜSTRA.",
    "bescheid_nachfassen": "Seit {tage} Tagen kein Bescheid.",
    "rueckfrage_antworten": "Eine Rückfrage wartet auf deine Antwort, Frist bis {frist}.",
    "widerspruch_pruefen": "Abgelehnt — ein Widerspruch wäre möglich bis {frist}.",
    "widerspruch_nachfassen": "Der Widerspruch ist seit {tage} Tagen ohne Antwort.",
    "geld_pruefen": "Seit {tage} Tagen genehmigt — ist das Geld da?",
}


def _tag(d) -> str:
    return f"{d:%d.%m.}" if d else ""


def _beschreiben(u: dict, ph: str, seit_roh: str, s: dict, jetzt: datetime) -> dict:
    seit = _dt(seit_roh) or jetzt
    tage = max(0, (jetzt.date() - seit.date()).days)
    faellig = bool(s["faellig_ab"] and s["faellig_ab"] <= jetzt)
    a = ablage.standard()
    return {"ref": u["ref"], "art": u["art"], "profil": u["profil"], "person": a.profil_name(u["profil"]),
            "vorgang": u["vorgang"], "titel": u["titel"], "phase": ph, "phase_text": PHASE_TEXT[ph], "seit": seit_roh,
            "schritt": s["schritt"], "schritt_text": SCHRITT_TEXT[s["schritt"]].format(tage=tage, frist=_tag(s["frist"])),
            "frist": s["frist"].isoformat() if s["frist"] else "",
            "frist_tage": (s["frist"] - jetzt.date()).days if s["frist"] else None,
            "faellig_ab": _iso(s["faellig_ab"]) if s["faellig_ab"] else "", "faellig": faellig,
            "ueberfaellig_tage": max(0, (jetzt - s["faellig_ab"]).days) if faellig else 0,
            "folgephasen": folgephasen(ph)}


def _offene(einh: list[dict], jetzt: datetime) -> list[dict]:
    aus = []
    for u in einh:
        g = u.get("gespeichert") or {}
        ph = g.get("phase") or u["phase"]
        if ph in OFFEN_NICHT:
            continue
        seit = _dt(g.get("seit")) or u["seit_bestand"]
        s = _schritt(u, ph, seit, jetzt)
        if s is None or s["schritt"] == "frist_vorbei":
            continue
        aus.append(_beschreiben(u, ph, g.get("seit") or _iso(seit), s, jetzt))
    aus.sort(key=lambda f: (not f["faellig"], f["frist"] or "9999", f["faellig_ab"] or "9999", f["ref"]))
    return aus


def _faellige(einh: list[dict], jetzt: datetime) -> list[dict]:
    return [f for f in _offene(einh, jetzt) if f["faellig"]]


def offene(jetzt: datetime | None = None, profil: int | None = None) -> list[dict]:
    """Alle offenen Vorgänge (nicht erledigt/verworfen) mit Phase, nächstem Schritt und Frist — fällige zuerst.
    `profil=0`: nur Eikes eigene; `None`: alle."""
    jetzt = jetzt or _jetzt()
    einh = _abgleichen(jetzt)[1]
    return [f for f in _offene(einh, jetzt) if profil is None or f["profil"] == profil]


def faellig(jetzt: datetime | None = None, profil: int | None = None) -> list[dict]:
    """Je offenem Vorgang der nächste Schritt, sofern er jetzt fällig ist (nach Frist, dann Alter)."""
    return [f for f in offene(jetzt, profil) if f["faellig"]]


# ---------- Erinnern (nur an Eike) ----------

def _formulieren(f: dict) -> tuple[str, str]:
    if f["profil"]:
        # Freunde-Daten gehen nie in Push oder Anliegen (LEHREN 20.09.2026): kein Name, keine Nummer.
        return "Garantie: Schritt fällig", "Bei einem Fall aus deinen Profilen ist ein Schritt fällig. Details unter Extras → Garantie."
    titel = f"Garantie: {f['titel']}"[:80]
    text = f["schritt_text"]
    if f["vorgang"]:
        text = f"Fall {f['vorgang']}: {text}"
    if f["schritt"] in NACHFASSEN:
        text += " Nachfassen fällig — soll ich einen Entwurf vorbereiten?"
    return titel, text[:300]


def _zustellen(f: dict) -> None:
    """Nur an Eike: ein Anliegen (Friday erwähnt es im Gespräch) und ein Push in Eikes Art „Garantie-Anträge“."""
    from kern.anliegen import fach
    from kern.dienste import benachrichtigungen
    titel, text = _formulieren(f)
    knapp = f["frist_tage"] is not None and f["frist_tage"] <= 2
    fach.standard().anlegen("eike", "garantie-verwaltung", titel, text, "hoch" if knapp else "normal", gueltig_stunden=72)
    try:
        benachrichtigungen.senden("garantie", titel, text, "/#studio/garantie", f"garantie-schritt-{f['ref']}-{f['schritt']}")
    except Exception:                                          # noqa: BLE001 — das Anliegen steht; der Push ist Beiwerk
        log.exception("Garantie-Erinnerung: Push gescheitert")


def erinnern(jetzt: datetime | None = None) -> int:
    """Jeden fälligen Schritt genau einmal an Eike melden (höchstens `MAX_JE_LAUF` je Durchgang). Sendet nichts an Dritte,
    schickt nichts ab, mailt nichts. Gemerkt wird erst nach gelungener Zustellung."""
    jetzt = jetzt or _jetzt()
    gesendet = 0
    for f in faellig(jetzt):
        if gesendet >= MAX_JE_LAUF:
            break
        if f["ueberfaellig_tage"] > MAX_UEBERFAELLIG_TAGE or erinnert(f["ref"], f["schritt"], f["seit"]):
            continue
        try:
            _zustellen(f)
        except Exception:                                      # noqa: BLE001
            log.exception("Garantie-Erinnerung nicht zugestellt (%s)", f["schritt"])
            continue
        _erinnert_merken(f["ref"], f["schritt"], f["seit"], jetzt)
        gesendet += 1
    return gesendet


def nachfass_entwurf(ref: str, jetzt: datetime | None = None) -> str:
    """Ein fester Text zum Nachfassen — nur Text, wird nirgends hingeschickt. Enthält keine Angaben zur Person
    (kein Name, keine Adresse, keine IBAN); Eike ergänzt Anrede und Gruß selbst."""
    jetzt = jetzt or _jetzt()
    f = next((x for x in offene(jetzt) if x["ref"] == ref), None)
    if not f:
        raise LookupError("Zu diesem Vorgang gibt es nichts nachzufassen.")
    seit = _dt(f["seit"]) or jetzt
    bezug = f"meinen Garantieantrag{' (Vorgangsnummer ' + f['vorgang'] + ')' if f['vorgang'] else ''} zur Fahrt: {f['titel']}"
    frage = {"eingang_nachfassen": "Bitte bestätigen Sie mir den Eingang meines Antrags.",
             "widerspruch_nachfassen": "Bitte teilen Sie mir den Stand meines Widerspruchs mit."}.get(
        f["schritt"], "Bitte teilen Sie mir den Bearbeitungsstand mit.")
    return (f"Sehr geehrte Damen und Herren,\n\nzu {bezug} habe ich seit dem {seit:%d.%m.%Y} keine Rückmeldung erhalten. "
            f"{frage}\n\nMit freundlichen Grüßen")


# ---------- Eingabe von Eike: welcher Vorgang ist gemeint? ----------

def aufloesen(angabe: str) -> str:
    """„111111“ (Vorgangsnummer), „A12“, „V111111“ oder „B3“ → die Kennung. LookupError, wenn es nichts Passendes gibt."""
    t = (angabe or "").strip().upper()
    a = ablage.standard()
    if re.fullmatch(r"\d{6,9}", t):
        e = a.antrag_zum_vorgang(t)
        if e:
            return f"A{e['id']}"
        if a.vorgang(t):
            return f"V{t}"
    elif re.fullmatch(r"[AVB]\d+", t):
        return t
    raise LookupError(f"Zu „{angabe}“ kenne ich keinen Vorgang.")
