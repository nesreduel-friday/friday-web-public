"""Was ein Werkzeug WIRKLICH bewirkt hat — maschinenlesbar (P0 der Ehrlichkeit, 12.10.2026).

Jedes Werkzeugergebnis bekommt drei Angaben, die der Kern kennt und das Modell nicht frei wählt:

* `status`: `ok` (erledigt) | `nicht` (NICHT erledigt) | `rueckfrage` (wartet auf Eike, nichts passiert).
* `art`: `vorgabe|skill|versand|antrag|erinnerung|merken|akte|lesen|sonstiges` (feste Zuordnung `ART` je Werkzeugname).
* `quittung`: ein kurzer deutscher Satz ohne Technikwörter („Festgehalten: …“, „Nicht gesendet: …“).

Übergang: Ein Werkzeug darf weiter einen `str` liefern (`normalisieren` erkennt den Status an klaren Anfängen). Die
Wirkungswerkzeuge liefern ausdrücklich eine `Wirkung`: ein `str` mit Zusatzfeldern. So lesen alle bestehenden Aufrufer und Tests
das Ergebnis weiter als Text; nur der Kern fragt `.status/.art/.quittung`. Anzeige und Wächter folgen in P2/P3 — hier wird nur erfasst."""

from __future__ import annotations

import functools
import inspect
import re

STATI = ("ok", "nicht", "rueckfrage")
ARTEN = ("vorgabe", "skill", "versand", "antrag", "erinnerung", "merken", "akte", "lesen", "sonstiges")

# Kopf der Quittung je Art und Status.
KOEPFE = {
    "vorgabe": ("Festgehalten", "Nicht festgehalten", "Noch offen"),
    "skill": ("Festgehalten", "Nicht festgehalten", "Wartet auf Eikes Ja"),
    "versand": ("Auf dem Weg", "Nicht gesendet", "Noch nicht gesendet"),
    "antrag": ("Vorbereitet", "Nicht vorbereitet", "Noch offen"),
    "erinnerung": ("Erinnerung gesetzt", "Keine Erinnerung gesetzt", "Noch offen"),
    "merken": ("Gemerkt", "Nicht gemerkt", "Noch offen"),
    "akte": ("Notiert", "Nicht notiert", "Noch offen"),
    "lesen": ("Nachgesehen", "Nicht nachgesehen", "Noch offen"),
    "sonstiges": ("Erledigt", "Nicht erledigt", "Noch offen"),
}

# Art je Werkzeugname. Jedes Werkzeug im Katalog steht hier (ein Test hält das fest); was fehlt, gilt als `sonstiges`.
_LESEN = """uhrzeit erinnern wissen rueckblick stundenplan stundenplan_karte hausaufgaben elternbriefe elternbrief schultermine
mails mail mail_termine gmail_mails gmail_mail person_ueber chat_suchen kalender kalender_suchen kalender_frei suchen nachrichten
webseite erinnerungen musik musik_geraete warteschlange_zeigen musik_playlists musik_playlist musik_zuletzt musik_geschmack
musik_ton_pruefen haltestellen_hier abfahrten verbindung fahrten_gemerkt studio_stand studio_wartet wetter morgenbriefing
gefundene_links garantie_suchen garantie_fahrt garantie_antraege garantie_stand notizen_suchen notizseite notizhefte standort
entfernung wo_ist whatsapp_chats whatsapp_lesen benachrichtigungen pc_bildschirm_ansehen pc_status pc_programme pc_fenster
pc_dateien pc_zwischenablage pc_bestaetigungen browser_lesen lernen schulbuecher bild_zeigen verbindung_karte server_status token_verbrauch
medien_suchen gedanken_lesen faeden_lesen vorgaben_lesen skills_lesen skill_testlauf personen_auswaehlen akte_lesen akte_fehlt
anfrage_treffer anfrage_stand snapchat_kontakte regeln_lesen recherche_lesen aufgaben_lesen vorhaben_lesen machbarkeit_lesen token_verbrauch chat_verlauf_lesen chat_verlauf_suchen minecraft_bot_status minecraft_bot_logs minecraft_bot_inventar""".split()
_ANDERE = {
    "vorgabe": "vorgabe_merken vorgabe_aendern vorgabe_widerrufen",
    "skill": "skill_schreiben skill_aendern skill_bestaetigen skill_widerrufen modul_schalten regel_schreiben regel_bestaetigen regel_widerrufen regel_streichen",
    "versand": ("schicke_snap snap_wunsch schicke_nachricht nachricht_an_personen rundnachricht_freigeben whatsapp_antwort whatsapp_senden regel_antwort regel_snap "
                "kontakt_anfragen anfrage_senden anfrage_auswaehlen anfrage_annehmen anfrage_erwarten anfrage_zurueckziehen "
                "freundschaft_pruefen frage_eike"),
    "antrag": ("garantie_vorbereiten garantie_antrag_aus_akte garantie_status_setzen medien_wunsch_stellen termin_wunsch "
               "medien_herunterladen medien_deutsch_holen"),
    "erinnerung": "erinnerung erinnerung_absagen",
    "merken": ("merken vergessen bild_benennen person_korrigieren fahrt_merken fahrt_vergessen gedanke_festhalten faden_anlegen "
               "faden_weiter faden_schliessen faden_vorschlagen"),
    "akte": "akte_erfassen akte_zusammenfassung ticket_lesen ticket_bestaetigen",
    # Ändern etwas, passen aber in keine der Arten oben (Steuern, Löschen, Karten, Termine …) — ausdrücklich, damit nichts fehlt.
    "sonstiges": """eike_fragen frage_beantworten vorhaben_vorschlagen vorhaben_starten machbarkeit_fragen anliegen_erledigt benachrichtigung_stellen ruhezeit_stellen hinweis_geben dashboard_karte oeffnen termin_anlegen dokument_erstellen
termin_bestaetigen recherche_starten recherche_abbrechen aufgaben_starten aufgaben_abbrechen aufgaben_pdf regel_push regel_termin medien_loeschen medien_loeschen_bestaetigen studio_hochladen studio_verwerfen warteschlange musik_geraet_waehlen
musik_jetzt musik_lautstaerke musik_naechster musik_pause musik_playlist_abspielen musik_vorheriger musik_weiter browser_klicken
browser_oeffnen browser_schliessen browser_scrollen browser_tippen browser_zurueck pc_datei_oeffnen pc_fenster_minimieren
pc_fenster_schliessen pc_fenster_wechseln pc_kopieren pc_loeschen pc_meldung pc_ordner_anlegen pc_starten pc_system pc_umbenennen
pc_verschieben pc_webseite minecraft_bot_steuern minecraft_bot_aufgabe""",
}
ART: dict[str, str] = {n: "lesen" for n in _LESEN}
for _art, _namen in _ANDERE.items():
    ART.update({n: _art for n in _namen.split()})


def art_von(name: str) -> str:
    return ART.get(name, "sonstiges")


class Wirkung(str):
    """Der Text fürs Modell plus Status, Art und Quittung. `Wirkung(status, art, quittung, text)`."""

    status: str
    art: str
    quittung: str

    def __new__(cls, status: str, art: str, quittung: str, text: str = ""):
        if status not in STATI:
            raise ValueError(f"unbekannter Status {status!r}")
        if art not in ARTEN:
            raise ValueError(f"unbekannte Art {art!r}")
        self = super().__new__(cls, text)
        self.status, self.art, self.quittung = status, art, quittung
        return self

    @property
    def text(self) -> str:
        return str.__str__(self)

    def __reduce__(self):
        return (Wirkung, (self.status, self.art, self.quittung, self.text))


# ---------------------------------------------------------------- Erkennung

TECHNIK = re.compile(
    r"\bskills?\b|\bvorgaben?\b|\bnr\.|\bentw[uü]rfe?\b|primitiv|werkzeug|\bakten?\b|\bprompt\b|aktiv gesetzt|ist jetzt aktiv"
    r"|\bsystemprompt|\barbeitsweisen?\b|\bversion\b|\bv\d+\b|schreibrecht|\bfunktion\b|\bblock\b|\bkennung\b|\bintern\b",
    re.IGNORECASE)

PRAEFIX = "NICHT ERLEDIGT: "
_NICHT_ANFANG = ("NICHT ERLEDIGT", "NICHTS gespeichert.", "NICHTS gesendet", "NICHTS ", "Das hat so nicht geklappt",
                 "Das hat nicht geklappt", "Noch nichts")
_FRAGE = re.compile(r"\bfrag (?:eike|ihn|sie)\b|\bfrag nach seinem ja\b|\bsoll ich\b[^.!]*\?|\bfrag (?:kurz|zuerst)\b", re.IGNORECASE)
_ANWEISUNG = ("NICHTS", "Sag ", "Versuche", "Behaupte", "Frag", "Es ist NICHTS", "Noch nichts", "Gesendet wurde")
_GRUND_KOPF = re.compile(r"^(?:NICHT ERLEDIGT:\s*)?(?:NICHTS (?:gespeichert|gesendet|festgehalten|geändert)\.?\s*)?"
                         r"(?:Das hat (?:so )?nicht geklappt:\s*)?", re.IGNORECASE)


def _zeilenanfang(text: str, anfaenge) -> bool:
    return any(z.startswith(a) for z in text.splitlines() for a in anfaenge)


def _trifft(text: str, regel) -> bool:
    if callable(regel):
        return bool(regel(text))
    return bool(regel) and _zeilenanfang(text, regel)


def status_aus_text(text: str, art: str = "sonstiges") -> str:
    """Status eines einfachen Werkzeugtextes. Lesende Werkzeuge melden hier nie „nicht“: „Noch nichts erfasst“ ist dort eine Auskunft."""
    if art == "lesen":
        return "ok"
    t = text.lstrip()
    frage = bool(_FRAGE.search(t))
    if t.startswith(_NICHT_ANFANG):
        return "rueckfrage" if frage else "nicht"
    return "rueckfrage" if frage else "ok"


def _fragt(text: str) -> bool:
    """Fragt der Text Eike (oder die Person) etwas? Nur für ausdrücklich beschriebene Werkzeuge: dort zählt auch ein Fragezeichen am Ende."""
    return bool(_FRAGE.search(text)) or text.rstrip().endswith("?")


def _kuerzen(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip(" ,;:—-") + "…"


def grund_satz(text: str, n: int = 110) -> str:
    """Der erste Satz einer Absage ohne Anweisungen ans Modell („Sag Eike ehrlich …“) — für die Quittung."""
    rest = _GRUND_KOPF.sub("", " ".join(str(text).split()), count=1)
    for satz in re.split(r"(?<=[.!?])\s+", rest):
        satz = satz.strip()
        if satz and not satz.startswith(_ANWEISUNG):
            return _kuerzen(satz.rstrip("."), n)
    return ""


def quittung_bauen(art: str, status: str, detail: str = "", kopf: str | None = None) -> str:
    """„Festgehalten: …“ — ohne Technikwörter: trifft der Zusatz eines, entfällt er."""
    ok, nicht, frage = KOEPFE.get(art, KOEPFE["sonstiges"])
    k = kopf or {"ok": ok, "nicht": nicht, "rueckfrage": frage}[status]
    detail = _kuerzen(str(detail or "").strip().rstrip("."), 110)
    if detail and TECHNIK.search(detail):
        detail = ""
    if not detail:
        return f"{k}."
    return f"{k}: {detail}" + ("" if detail[-1] in "?!" else ".")


def normalisieren(name: str, rueckgabe) -> tuple:
    """(Text fürs Modell, Wirkung). `rueckgabe`: eine `Wirkung`, ein `str` oder ein Dict (`{'fehler': …}`/`{'ergebnis': …}`).
    Alles andere (Listen, Zahlen) ist ein gelungenes Ergebnis ohne Text-Erkennung."""
    art = art_von(name)
    if isinstance(rueckgabe, Wirkung):
        return rueckgabe.text, rueckgabe
    if isinstance(rueckgabe, dict) and rueckgabe.get("fehler"):
        text = str(rueckgabe["fehler"])
        return text, Wirkung("nicht", art, quittung_bauen(art, "nicht"), text)
    if isinstance(rueckgabe, str):
        status = status_aus_text(rueckgabe, art)
        detail = grund_satz(rueckgabe) if status != "ok" else ""
        return rueckgabe, Wirkung(status, art, quittung_bauen(art, status, detail), rueckgabe)
    return rueckgabe, Wirkung("ok", art, quittung_bauen(art, "ok"), "" if rueckgabe is None else str(rueckgabe))


def fehler_wirkung(name: str, text: str = "") -> Wirkung:
    """Ein Abbruch im Kern (nicht erlaubt, nicht freigegeben, Ausnahme): nicht erledigt, ohne den technischen Grund in der Quittung."""
    art = art_von(name)
    return Wirkung("nicht", art, quittung_bauen(art, "nicht"), text)


def fuers_modell(antwort: dict) -> dict:
    """Aus dem Ergebnis von `katalog.ausfuehren` nur das, was das Modell sieht: `ergebnis` oder `fehler` — nie Status/Quittung."""
    return {k: antwort[k] for k in ("ergebnis", "fehler") if k in antwort}


def quittungen(wirkungen: list[dict]) -> list[dict]:
    """Was Eike als Fakt-Zeile sieht (P2, 12.10.2026): `[{text, status}]` je Wirkung, vom Kern gebaut, nie vom Modell.

    Jede Wirkung (ok UND nicht/rueckfrage) bekommt eine Zeile. Lesende Werkzeuge nur, wenn sie scheitern (sonst Rauschen bei jedem
    Nachschlagen); ein bloßes „Erledigt.“ (Musik weiter, Fenster schließen …) sagt nichts und entfällt. Gleiche Zeilen hintereinander
    erscheinen einmal."""
    aus: list[dict] = []
    for w in wirkungen or []:
        if not isinstance(w, dict):
            continue
        status, text = str(w.get("status") or ""), str(w.get("quittung") or "").strip()
        if not text or status not in STATI:
            continue
        if status == "ok" and (w.get("art") == "lesen" or text == f"{KOEPFE['sonstiges'][0]}."):
            continue
        zeile = {"text": text, "status": status}
        if not aus or aus[-1] != zeile:
            aus.append(zeile)
    return aus


def mit_praefix(text, status: str):
    """Bei status != ok beginnt der Text einheitlich mit „NICHT ERLEDIGT: “ (nicht doppelt)."""
    if status == "ok" or not isinstance(text, str) or text.startswith("NICHT ERLEDIGT"):
        return text
    return PRAEFIX + text


# ---------------------------------------------------------------- Dekorator für die Wirkungswerkzeuge

def wirkt(art: str, *, ok=(), rueckfrage=(), nicht_praefix: str = "", kopf_ok: str | None = None, detail=None,
          detail_frage=None):
    """Macht aus dem Text eines Werkzeugs eine ausdrückliche `Wirkung` mit `art` und Quittung.

    `ok`: Zeilenanfänge (oder eine Funktion `text -> bool`), an denen ein Erfolg erkennbar ist — ohne Treffer ist es „nicht“
    (oder „rueckfrage“, wenn der Text Eike etwas fragt). `rueckfrage`: dasselbe für Rückfragen, die der Standard nicht erkennt.
    `nicht_praefix`: steht vor jedem Nicht-Erfolg, wenn er nicht schon damit beginnt (z. B. „NICHTS gesendet. …“).
    `detail(args, text)`: der Zusatz der Erfolgs-Quittung aus den Aufrufangaben (`args`: Parametername -> Wert).
    `kopf_ok`: anderer Kopf der Erfolgs-Quittung als der der Art („Geändert“)."""
    if art not in ARTEN:
        raise ValueError(art)

    def deko(f):
        sig = inspect.signature(f)

        def auswerten(a, k, r):
            if not isinstance(r, str) or isinstance(r, Wirkung):
                return r
            text = r
            if ok and _trifft(text, ok):
                status = "ok"
            elif rueckfrage and _trifft(text, rueckfrage):
                status = "rueckfrage"
            elif ok or rueckfrage:
                status = "rueckfrage" if _fragt(text) else "nicht"
            else:
                status = status_aus_text(text, art)
            if status != "ok" and nicht_praefix and not text.startswith(nicht_praefix):
                text = nicht_praefix + text
            try:
                args = dict(sig.bind_partial(*a, **k).arguments)
            except TypeError:
                args = {}
            zusatz = ""
            if status == "ok" and detail:
                try:
                    zusatz = detail(args, text) or ""
                except Exception:                      # noqa: BLE001 — eine Quittung darf nie den Aufruf kippen
                    zusatz = ""
            elif status != "ok":
                zusatz = (detail_frage(args, text) if detail_frage else "") or grund_satz(text)
            return Wirkung(status, art, quittung_bauen(art, status, zusatz, kopf_ok if status == "ok" else None), text)

        if inspect.iscoroutinefunction(f):
            @functools.wraps(f)
            async def innen(*a, **k):
                return auswerten(a, k, await f(*a, **k))
        else:
            @functools.wraps(f)
            def innen(*a, **k):
                return auswerten(a, k, f(*a, **k))
        return innen
    return deko
