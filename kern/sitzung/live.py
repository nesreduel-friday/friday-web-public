"""Sprachsitzung: Browser <-> Kern <-> Gemini Live.

Der Aufbau stammt aus seminar-ki, wo er zuverlässig lief: roher
WebSocket-Proxy, ein eigenes kleines Protokoll zum Browser, und nur dieses
Modul kennt das Gemini-Format.

Neu gegenüber seminar-ki, alles gegen die echte API geprüft (11.09.2026):
- Audio als `realtimeInput.audio`. `mediaChunks` lehnt
  3.1-flash-live-preview ab: "realtime_input.media_chunks is deprecated".
- Sitzungsfortsetzung. Bricht die Verbindung ab, geht es mit dem letzten
  Handle weiter, und Friday weiß noch, worum es ging. Ohne Handle, oder
  wenn die Fortsetzung scheitert, wie in seminar-ki: frische Sitzung.
- Text und Bilder mitten im Gespräch. Vorher `audioStreamEnd`, sonst bleibt
  der Zug stumm (Lehre aus dem alten Friday, core/voice.py).
- Der Schlüssel geht als Header, nicht in der URL — so kann er in keiner
  Fehlermeldung auftauchen.
- Werkzeuge: Gemini schickt `toolCall`, der Kern führt aus und antwortet mit
  `toolResponse` samt der id des Aufrufs (Muster aus dem alten Friday).

Browser -> Kern:  start | audio | ptt_end | text | bild | stop
Kern -> Browser:  ready | audio | text | turn_end | interrupted | link |
                  werkzeug | quittung | korrektur | oeffnen | reconnecting | fehler
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from kern import config, umgebung
from kern.chatstrang import speicher as chatstrang
from kern.dienste import web
from kern.ehrlichkeit import gegenlesen, waechter
from kern.verbrauch import tokens as tokenzaehler
from kern.ehrlichkeit import wirkung as wirk
from kern.gedaechtnis import bilder
from kern.gedaechtnis import speicher as gspeicher
from kern.gehirn import persona
from kern.protokoll import speicher
from kern.werkzeuge import auswahl
from kern.werkzeuge import katalog as werkzeuge

from urllib.parse import urlparse

log = logging.getLogger("friday.live")

MAX_VERSUCHE = 5
# Sprache (13.10.2026): Die Werkzeuge stehen im Setup, aber ein Wiederverbinden mit Handle darf andere setzen (geprüft). Start: Kern + diese
# Gruppen + `werkzeuge_laden`; holt das Modell mehr, verbindet der Kern mit der erweiterten Auswahl neu und gibt Eikes Frage noch einmal mit.
START_GRUPPEN = ("musik", "kalender", "wetter")
MAX_GLEICHE_AUFRUFE = 3          # derselbe Aufruf je Zug (dreimal „nächster Titel“ geht noch)
MAX_AUFRUFE_JE_ZUG = 20
BILD_TYPEN = {"image/jpeg", "image/png", "image/webp"}
MAX_BILD_B64 = 8_000_000
ZUSTIMMUNG_GILT_S = 600  # so lange gilt Eikes Ja im Sprachkanal (wie VORSCHLAG_GILT_S der Werkzeuge)


def zustimmung_merken(zustand: dict, text: str) -> None:
    """Eikes Ja/Nein aus Transkription oder getipptem Text merken (Gespräch 439:
    kurzes „ja“ kam an, aber der ToolCall lief erst nach turnComplete — mit
    zurückgesetzter Zustimmung). NEIN löscht, JA setzt mit Zeit, sonst bleibt
    alles — die Zustimmung überlebt die Turn-Grenze, bis sie verbraucht ist."""
    import time as _zeit
    if werkzeuge.ablehnung_erkannt(text):
        zustand["zustimmung"] = False
        zustand.pop("zustimmung_zeit", None)
    elif werkzeuge.zustimmung_erkannt(text):
        zustand["zustimmung"] = True
        zustand["zustimmung_zeit"] = _zeit.time()


def eingabe_fuer_werkzeuge(zustand: dict) -> str:
    """Eikes Äußerung für einen Werkzeugaufruf: die laufende, sonst die zuletzt abgeschlossene.
    Ein ToolCall darf NACH `turnComplete` kommen (Gespräch 439) — dann ist `eingabe` schon geleert, und
    eine Prüfung wie `sendepruefung.anfrage_erkannt` sähe Eikes Auftrag nicht (30.10.2026)."""
    return zustand.get("eingabe") or zustand.get("eingabe_letzter_zug", "")


def zustimmung_gilt(zustand: dict) -> bool:
    """Darf ein zustimmungspflichtiges Tool jetzt laufen? Verfallenes wird
    dabei gleich weggeräumt."""
    import time as _zeit
    if not zustand.get("zustimmung"):
        return False
    try:
        alter = _zeit.time() - float(zustand.get("zustimmung_zeit", 0) or 0)
    except (TypeError, ValueError):
        alter = ZUSTIMMUNG_GILT_S + 1
    if not 0 <= alter <= ZUSTIMMUNG_GILT_S:
        zustand["zustimmung"] = False
        zustand.pop("zustimmung_zeit", None)
        return False
    return True


def origin_erlaubt(origin: str | None) -> bool:
    """Prüft, ob der WebSocket-Aufruf von Friday selbst stammt — Schutz gegen
    Cross-Site WebSocket Hijacking (CSWSH).

    Erlaubt:
    - kein Origin / None (Test-Clients, native Apps)
    - "testserver" (Starlette TestClient)
    - Fridays Basis-URL (config.basis_url())
    - Lokale Adressen (localhost, 127.0.0.1, ::1)
    """
    if not origin or origin == "testserver":
        return True
    try:
        p = urlparse(origin)
        host = (p.hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "::1", "testserver"):
            return True
        basis_host = (urlparse(config.basis_url()).hostname or "").lower()
        if basis_host and host == basis_host:
            return True
        return False
    except Exception:
        return False


# ---------- Gemini-Nachrichten (rein, ohne Seiteneffekte — gut testbar) ----------

_EMPFINDLICHKEIT = {"low": "LOW", "high": "HIGH"}


def aktivitaet_config(roh) -> dict | None:
    """Sprach-Aktivierung (VAD) aus dem Start des Clients, streng geprüft: nur bekannte Schlüssel, Werte begrenzt.
    Der Client darf Gemini nichts anderes ins Setup schreiben. Ungültiges/Leeres → None (Gemini-Standard)."""
    if not isinstance(roh, dict):
        return None
    aktiv: dict = {}
    for schluessel, name in (("start", "startOfSpeechSensitivity"), ("ende", "endOfSpeechSensitivity")):
        wert = _EMPFINDLICHKEIT.get(str(roh.get(schluessel, "")).lower())
        if wert:
            aktiv[name] = f"{'START' if schluessel == 'start' else 'END'}_SENSITIVITY_{wert}"
    for schluessel, name, grenze in (("vorlauf_ms", "prefixPaddingMs", 1000), ("pause_ms", "silenceDurationMs", 3000)):
        wert = roh.get(schluessel)
        if isinstance(wert, (int, float)) and not isinstance(wert, bool):
            aktiv[name] = max(0, min(int(wert), grenze))
    return {"automaticActivityDetection": aktiv} if aktiv else None


def _auswahl_an() -> bool:
    return os.environ.get("WERKZEUG_AUSWAHL", "an") != "aus"


def _tools(gruppen: set[str] | None) -> list[dict]:
    """None = alle Werkzeuge (wie bisher); sonst Kern + Gruppen + `werkzeuge_laden`."""
    if gruppen is None or not _auswahl_an():
        return werkzeuge.gemini_tools()
    t = werkzeuge.gemini_tools(module=auswahl.module_von(gruppen))
    t[0]["functionDeclarations"].append(auswahl.meta_erklaerung())
    return t


def setup_nachricht(anweisung: str, handle: str | None = None, aktivitaet: dict | None = None,
                    gruppen: set[str] | None = None) -> dict:
    setup = _setup_nachricht(anweisung, handle, gruppen)
    if aktivitaet:
        setup["setup"]["realtimeInputConfig"] = aktivitaet
    return setup


def _setup_nachricht(anweisung: str, handle: str | None, gruppen: set[str] | None = None) -> dict:
    return {"setup": {
        "model": f"models/{config.stimmmodell()}",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": config.stimme()}},
                "languageCode": "de-DE",
            },
        },
        "systemInstruction": {"parts": [{"text": anweisung}]},
        "tools": _tools(gruppen),
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        # Leeres Objekt = Fortsetzung erlauben; mit Handle = fortsetzen.
        "sessionResumption": {"handle": handle} if handle else {},
    }}


def audio_nachricht(daten: str) -> dict:
    return {"realtimeInput": {"audio": {"mimeType": "audio/pcm;rate=16000", "data": daten}}}


def stream_ende() -> dict:
    return {"realtimeInput": {"audioStreamEnd": True}}


def bildkontext_nachricht(mime: str, daten: str) -> dict:
    """Ein Bild in die laufende Sitzung legen, OHNE dass Gemini gleich antwortet (turnComplete false)."""
    return {"clientContent": {"turns": [{"role": "user", "parts": [
        {"text": "[Eikes Bildschirm gerade:]"}, {"inlineData": {"mimeType": mime, "data": daten}}]}],
        "turnComplete": False}}


def inhalt_nachricht(teile: list[dict]) -> dict:
    return {"clientContent": {"turns": [{"role": "user", "parts": teile}], "turnComplete": True}}


# ---------- Mitschrift ----------

class _Mitschrift:
    """Transkripte kommen stückweise; pro Zug gesammelt ins Protokoll."""

    def __init__(self, proto: speicher.Protokoll, gespraech: int):
        self.proto, self.gespraech = proto, gespraech
        self.puffer = {"du": "", "friday": ""}

    def dazu(self, rolle: str, text: str) -> None:
        self.puffer[rolle] += text

    def abschliessen(self) -> None:
        for rolle in ("du", "friday"):
            self.proto.beitrag(self.gespraech, rolle, self.puffer[rolle])
            _strang(rolle, self.puffer[rolle], self.gespraech)
            self.puffer[rolle] = ""


def _strang(rolle: str, text: str, gespraech: int) -> None:
    """Die Runde auch in den Chat-Strang (quelle „live“): so sieht jedes Gerät das Gespräch, und der Chat behält es beim Neuöffnen.
    Leeres wird übersprungen; ein Fehler dort darf das Gespräch nie stören."""
    if not (text or "").strip():
        return
    try:
        chatstrang.standard().anhaengen(rolle, text.strip()[:chatstrang.TEXT_MAX], quelle="live", gespraech=gespraech)
    except Exception:                                  # noqa: BLE001
        log.exception("Chat-Strang: Live-Zeile nicht abgelegt")


# ---------- Sitzung ----------

async def live_sitzung(browser: WebSocket) -> None:
    await browser.accept()
    if not config.gemini_schluessel():
        await browser.send_json({"type": "fehler", "message": "Kein Gemini-Schlüssel hinterlegt."})
        await browser.close()
        return
    try:
        start = await browser.receive_json()
        if start.get("type") != "start":
            await browser.close()
            return
        proto = speicher.standard()
        gespraech = proto.gespraech_beginnen("sprache")
        mit = _Mitschrift(proto, gespraech)
        # fremd: der laufende Zug enthält einen Link oder ein Bild. Dann wird
        # aus merken ein Vorschlag (kern/werkzeuge/gedaechtnis.py).
        # Die Kennung sagt, an welcher Oberfläche Eike sitzt — sonst rät Friday
        # bei „Wo bist du?" die zuletzt gemeldete (13.09.2026: dreimal anders).
        zustand = {"handle": None, "zuege": 0, "fremd": False, "gruppen": set(START_GRUPPEN),
                   "kennung": str(start.get("kennung") or "")[:64],
                   "aktivitaet": aktivitaet_config(start.get("aktivitaet"))}
        versuche = 0
        try:
            while True:
                zuege_vorher = zustand["zuege"]
                try:
                    grund = await _verbindung(browser, zustand, mit, proto, gespraech)
                except (websockets.exceptions.WebSocketException, OSError, asyncio.TimeoutError) as exc:
                    log.warning("Gemini weggebrochen: %s", config.schwaerzen(str(exc))[:160])
                    grund = "abbruch"
                if grund == "ende":
                    break
                mit.abschliessen()
                # Hat die Verbindung wenigstens einen Zug geschafft, fängt die
                # Zählung neu an — nur Abbrüche in Folge sollen aufgeben.
                versuche = 0 if zustand["zuege"] > zuege_vorher else versuche + 1
                if versuche >= MAX_VERSUCHE:
                    await browser.send_json({"type": "fehler", "message":
                        "Die Verbindung zu Gemini bricht wiederholt ab. Bitte neu starten."})
                    break
                await browser.send_json({"type": "reconnecting", "fortsetzung": bool(zustand["handle"])})
                await asyncio.sleep(0.3 * (versuche + 1))
        finally:
            mit.abschliessen()
            proto.gespraech_beenden(gespraech)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("Sprachsitzung abgebrochen")
        try:
            await browser.send_json({"type": "fehler",
                                     "message": config.schwaerzen(f"{type(exc).__name__}: {exc}")[:300]})
        except Exception:
            pass


def _anweisung(gespraech: int | None, kennung: str, gruppen: set[str] | None = None) -> str:
    """Der Systemprompt der Sprachsitzung. Ein Anliegen kommt nur hier hinein, also beim Start und beim Wiederverbinden: Mitten in
    der Sitzung lässt sich der Prompt nicht ändern (Gemini Live)."""
    from kern.anliegen import block as anliegenblock
    text, _ = anliegenblock.bauen_sicher("eike", gespraech)
    from kern.fragen import block as fragenblock
    from kern.vorgaben import block as vorgabenblock
    return persona.live_prompt(config.jetzt(), gspeicher.kontext_sicher(), umgebung.beschreibung(kennung),
                               anliegen="\n\n".join(t for t in (text, fragenblock.bauen_sicher()) if t),
                               vorgaben=vorgabenblock.bauen_sicher(), gruppen=gruppen if _auswahl_an() else None)


async def _verbindung(browser, zustand, mit, proto, gespraech) -> str:
    handle = zustand["handle"]
    async with websockets.connect(
        config.gemini_ws_url(), max_size=None, ping_interval=20,
        additional_headers={"x-goog-api-key": config.gemini_schluessel()},
    ) as gem:
        gruppen = zustand.get("gruppen")
        anweisung = _anweisung(gespraech, zustand.get("kennung", ""), gruppen)
        await gem.send(json.dumps(setup_nachricht(anweisung, handle, zustand.get("aktivitaet"), gruppen)))
        try:
            antwort = json.loads(await asyncio.wait_for(gem.recv(), timeout=30))
        except websockets.exceptions.ConnectionClosed:
            zustand["handle"] = None      # Fortsetzung verweigert -> nächstes Mal frisch
            raise
        if "setupComplete" not in antwort:
            zustand["handle"] = None
            return "abbruch"
        await browser.send_json({"type": "ready", "fortgesetzt": bool(handle)})
        if (weiter := zustand.pop("weiter", None)) is not None:
            # Nach `werkzeuge_laden`: Die neue Verbindung kennt Eikes laufende Äußerung nicht — sie steht hier noch einmal.
            frage = (f"Eike hat gerade gesagt: „{weiter}“. " if weiter else
                     "Du kennst Eikes letzte Frage nicht mehr: Frag ihn kurz, was er wollte, und rate nichts. ")
            await gem.send(json.dumps(inhalt_nachricht([{"text": "[Die nachgeladenen Werkzeuge sind jetzt da. " + frage +
                                                                 "Nutze sie, ohne das Nachladen zu erwähnen.]"}])))

        weg = asyncio.Event()
        unten = asyncio.create_task(_von_gemini(gem, browser, zustand, mit, weg))
        try:
            while True:
                # Auf beides gleichzeitig warten: Browser ODER Wegfall der
                # Gemini-Verbindung — sonst fiele der Abbruch erst beim
                # nächsten Senden auf (Muster aus seminar-ki).
                naechste = asyncio.create_task(browser.receive_json())
                abbruch = asyncio.create_task(weg.wait())
                fertig, _ = await asyncio.wait({naechste, abbruch}, return_when=asyncio.FIRST_COMPLETED)
                if abbruch in fertig:
                    naechste.cancel()
                    return "abbruch"
                abbruch.cancel()
                m = naechste.result()
                art = m.get("type")
                try:
                    if art == "audio":
                        await gem.send(json.dumps(audio_nachricht(str(m.get("data", "")))))
                    elif art == "ptt_end":
                        await gem.send(json.dumps(stream_ende()))
                    elif art == "text":
                        await _text(gem, browser, str(m.get("text", "")), proto, gespraech, zustand)
                    elif art == "bild":
                        await _bild(gem, browser, m, proto, gespraech, zustand)
                    elif art == "stop":
                        return "ende"
                except websockets.exceptions.ConnectionClosed:
                    return "abbruch"
        finally:
            unten.cancel()
            await asyncio.gather(unten, return_exceptions=True)


async def _von_gemini(gem, browser, zustand, mit, weg: asyncio.Event) -> None:
    try:
        async for roh in gem:
            m = json.loads(roh)
            if m.get("usageMetadata"):
                tokenzaehler.zaehlen(config.stimmmodell(), m, "live")
            update = m.get("sessionResumptionUpdate") or {}
            if update.get("newHandle") and update.get("resumable") is not False:
                zustand["handle"] = update["newHandle"]
            if "goAway" in m:
                return  # Gemini kündigt das Ende an: mit Handle neu verbinden, bevor es abreißt.
            if aufruf := m.get("toolCall"):
                alle = aufruf.get("functionCalls", [])
                meta = [fc for fc in alle if fc.get("name") == auswahl.META]
                if meta:
                    aufruf = {**aufruf, "functionCalls": [fc for fc in alle if fc.get("name") != auswahl.META]}
                    if await _nachladen(gem, zustand, mit, meta, aufruf["functionCalls"]):
                        return              # neu verbinden: mit den zusätzlichen Werkzeugen (Handle bleibt, die Sitzung läuft weiter)
                    if not aufruf["functionCalls"]:
                        continue
                fremd, verbraucht = await _werkzeuge_ausfuehren(gem, browser, aufruf, mit,
                                                                zustand.get("fremd", False),
                                                                zustimmung_gilt(zustand),
                                                                zustand.setdefault("aufrufe", {}),
                                                                eingabe_fuer_werkzeuge(zustand),
                                                                am_pc=_am_pc(zustand.get("kennung", "")),
                                                                wirkungen=zustand.setdefault("wirkungen", []),
                                                                rueckfragen=zustand.setdefault("rueckfragen", {}))
                zustand["fremd"] = fremd
                if verbraucht:   # ein Ja = ein Aufruf — danach ist sie weg
                    zustand["zustimmung"] = False
                    zustand.pop("zustimmung_zeit", None)
                continue

            sc = m.get("serverContent") or {}
            for teil in (sc.get("modelTurn") or {}).get("parts", []):
                daten = teil.get("inlineData") or {}
                if str(daten.get("mimeType", "")).startswith("audio/pcm"):
                    await browser.send_json({"type": "audio", "data": daten["data"]})
            if text := (sc.get("inputTranscription") or {}).get("text"):
                # Gesagtes "ja" ist die Erlaubnis für schreibende Werkzeuge —
                # gesetzt vom Kern, nie vom Modell.
                zustimmung_merken(zustand, text)
                zustand.pop("eingabe_letzter_zug", None)   # neue Äußerung: die alte gilt nicht mehr
                zustand["eingabe"] = zustand.get("eingabe", "") + text
                mit.dazu("du", text)
                await browser.send_json({"type": "text", "role": "du", "text": text})
            if text := (sc.get("outputTranscription") or {}).get("text"):
                mit.dazu("friday", text)
                zustand["gesagt"] = zustand.get("gesagt", "") + text     # der ganze Zug, auch was vor einem Werkzeugaufruf gesagt wurde
                await browser.send_json({"type": "text", "role": "friday", "text": text})
            if sc.get("interrupted"):
                mit.abschliessen()
                await browser.send_json({"type": "interrupted"})
            if sc.get("turnComplete"):
                zustand["zuege"] += 1
                zustand["fremd"] = False
                # Kein Zustimmungs-Reset an der Turn-Grenze (Gespräch 439): Der
                # ToolCall kann NACH turnComplete kommen — ein Ja gilt, bis es
                # verbraucht ist, Eike ablehnt oder die Zeit abläuft.
                zustand["aufrufe"] = {}
                wirkungen_zug, gesagt = zustand.get("wirkungen", []), zustand.pop("gesagt", "")
                zustand["wirkungen"] = []
                # Eikes Wort für späte ToolCalls behalten (siehe `eingabe_fuer_werkzeuge`) — eine neue
                # Äußerung räumt es beim Eintreffen weg.
                zustand["eingabe_letzter_zug"] = zustand.get("eingabe", "")
                zustand["eingabe"] = ""
                # Was Friday zu einem gezeigten Bild sagt, wird seine Beschreibung.
                if bild_id := zustand.pop("bild_offen", None):
                    try:
                        bilder.beschreiben(bild_id, mit.puffer["friday"])
                    except Exception:
                        log.exception("Bildbeschreibung nicht gespeichert")
                mit.abschliessen()
                await browser.send_json({"type": "turn_end"})
                await _waechter(gem, browser, zustand, mit, gesagt, wirkungen_zug)
            if fehler := m.get("error"):
                await browser.send_json({"type": "fehler", "message": json.dumps(fehler)[:300]})
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        weg.set()


async def _waechter(gem, browser, zustand: dict, mit: _Mitschrift, gesagt: str, wirkungen: list[dict]) -> None:
    """Nach dem Zug: Hat Friday etwas als erledigt gesagt, was nicht passiert ist? (P3, 12.10.2026) Gesprochenes lässt sich nicht
    zurücknehmen, also sofort eine rote Karte mit dem Fakt aus den Quittungen UND ein Textzug an das Modell, der die Wahrheit sagen
    lässt. Höchstens einmal je Zug (der Zug nach der Korrektur löst keine weitere aus) und `MAX_KORREKTUREN` je Sitzung."""
    frueher = zustand.setdefault("wirkungen_frueher", [])
    verstoesse = waechter.pruefen(gesagt, wirkungen, frueher)
    frueher += [w for w in wirkungen if w.get("status") == "ok"]
    del frueher[:-50]
    war_korrektur = zustand.pop("korrektur_offen", False)
    if not verstoesse:
        return
    text = waechter.korrektur_text(wirkungen, verstoesse)
    log.warning("Wächter: Behauptung ohne Wirkung (%s)", ", ".join(w for w, _ in verstoesse))
    await browser.send_json({"type": "korrektur", "text": text})
    mit.proto.beitrag(mit.gespraech, "system", text, "korrektur")
    if war_korrektur or zustand.get("korrekturen", 0) >= waechter.MAX_KORREKTUREN:
        return
    zustand["korrekturen"] = zustand.get("korrekturen", 0) + 1
    zustand["korrektur_offen"] = True
    try:
        await gem.send(json.dumps(stream_ende()))
        await gem.send(json.dumps(inhalt_nachricht([{"text": waechter.modell_hinweis(wirkungen, verstoesse)}])))
    except websockets.exceptions.ConnectionClosed:
        zustand["korrektur_offen"] = False


def schleife(aufrufe: dict, name: str, args: dict) -> str | None:
    """Läuft Gemini im Kreis? Dann eine Absage statt des Werkzeugs.

    Gemessen 20.09.2026 (Gespräch 223): Auf „spiel Musik auf meinem Echo“ rief
    Gemini `musik_geschmack` 45-mal in 45 Sekunden, mit denselben Angaben, und
    sagte nichts. Im Chat begrenzt die Rundenzahl das; im Sprechen gab es keine
    Grenze. `aufrufe` zählt je Zug und wird bei `turnComplete` geleert."""
    schluessel = name + json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
    aufrufe[schluessel] = aufrufe.get(schluessel, 0) + 1
    aufrufe["*"] = aufrufe.get("*", 0) + 1
    if aufrufe[schluessel] > MAX_GLEICHE_AUFRUFE:
        return (f"{name} lief in diesem Zug schon mit genau diesen Angaben — das Ergebnis liegt "
                "oben. Nicht noch einmal aufrufen: Antworte Eike jetzt mit dem, was du hast.")
    if aufrufe["*"] > MAX_AUFRUFE_JE_ZUG:
        return ("Zu viele Werkzeugaufrufe in diesem Zug. Keine weiteren — antworte Eike jetzt "
                "mit dem, was du hast, oder frag ihn, was genau er möchte.")
    return None


def _am_pc(kennung: str) -> bool:
    """Sitzt Eike gerade am PC? Dann gehört „öffne …“ nicht zu `oeffnen` (das ist für Handy und Tablet)."""
    eintrag = umgebung.fuer(kennung)
    return bool(eintrag and eintrag.get("geraet") == "PC")


def _antwort_fuers_modell(ergebnis: dict) -> dict:
    """Was das Modell zu einem Werkzeug sieht. Hat es NICHT erledigt, steht vorn eine zwingende Anweisung: `response` ist ein freies
    Objekt, ein eigenes Feld ist zulässig; der Text beginnt zusätzlich mit „NICHT ERLEDIGT: “ (`wirkung.mit_praefix`)."""
    antwort = wirk.fuers_modell(ergebnis)
    text = antwort.get("ergebnis") if "ergebnis" in antwort else antwort.get("fehler", "")
    if anweisung := waechter.anweisung(str(ergebnis.get("status") or "ok"), str(text or "")):
        return {"anweisung": anweisung, **antwort}
    return antwort


async def _nachladen(gem, zustand: dict, mit: _Mitschrift, meta: list[dict], andere: list[dict]) -> bool:
    """`werkzeuge_laden` in der Sprache: gültige neue Gruppen merken, den Aufruf beantworten und True (= neu verbinden) zurückgeben.
    Gibt es nichts Neues oder nur Unbekanntes, bekommt das Modell die Auskunft und die Sitzung läuft weiter (False)."""
    gesamt = zustand.setdefault("gruppen", set(START_GRUPPEN))
    neu: set[str] = set()
    antworten = []
    for fc in meta:
        gewuenscht = [g for g in (fc.get("args") or {}).get("gruppen") or [] if isinstance(g, str) and g in auswahl.GRUPPEN]
        neu |= set(gewuenscht) - gesamt
        text = ("Geladen: " + ", ".join(gewuenscht) + ". Du kannst die Werkzeuge dazu jetzt aufrufen.") if gewuenscht else (
            "Diese Gruppe gibt es nicht. Gültig: " + ", ".join(auswahl.GRUPPEN) + ".")
        if gewuenscht and not neu:
            text = "Diese Werkzeuge hast du schon. Ruf sie direkt auf."
        antworten.append({"id": fc.get("id"), "name": auswahl.META, "response": {"ergebnis": text}})
    if neu and not andere:
        gesamt |= neu
        zustand["weiter"] = (zustand.get("eingabe") or mit.puffer["du"]).strip()[:500]
        log.info("Sprache: Werkzeuge nachgeladen (%s)", ", ".join(sorted(neu)))
        return True        # keine Antwort an das alte Gespräch nötig: die neue Verbindung beginnt mit Eikes Frage
    if neu:                # gleichzeitig andere Aufrufe: erst diese beantworten, die Gruppe gilt ab dem nächsten Verbinden
        gesamt |= neu
    await gem.send(json.dumps({"toolResponse": {"functionResponses": antworten}}))
    return False


async def _werkzeuge_ausfuehren(gem, browser, aufruf: dict, mit: _Mitschrift, fremd: bool = False,
                                zustimmung: bool = False, aufrufe: dict | None = None,
                                eingabe: str = "", am_pc: bool = False, wirkungen: list | None = None,
                                rueckfragen: dict | None = None) -> tuple[bool, bool]:
    """Gemini fragt Werkzeuge an. Gemini entscheidet, WAS aufgerufen wird —
    ausgeführt wird nur, was in kern/werkzeuge steht. Zurück: (fremde Inhalte
    jetzt im Zug, Zustimmung verbraucht) — ein Ja gilt für genau einen Aufruf."""
    mit.abschliessen()
    antworten = []
    bilder_fuers_modell: list[dict] = []
    verbraucht = False
    aufrufe = {} if aufrufe is None else aufrufe
    for fc in aufruf.get("functionCalls", []):
        name = str(fc.get("name", ""))
        aktionen: list[dict] = []
        if absage := schleife(aufrufe, name, fc.get("args") or {}):
            log.warning("Werkzeug-Schleife abgefangen: %s", name)
            antworten.append({"id": fc.get("id"), "name": name, "response": {"fehler": absage}})
            continue
        if name == "oeffnen" and am_pc:
            # Im Code, nicht nur im Prompt: Am PC öffnet `oeffnen` nichts, das gehört zu pc_starten/pc_webseite.
            log.info("oeffnen am PC abgewiesen")
            antworten.append({"id": fc.get("id"), "name": name, "response": {
                "fehler": "Eike sitzt am PC. Webseiten öffnest du mit pc_webseite, Programme mit pc_starten. "
                          "oeffnen ist nur für sein Handy oder Tablet."}})
            continue
        args = fc.get("args") or {}
        rueckfrage, bestaetigt = None, False
        if rueckfragen is not None:
            # Gegenlesen (P4): Vorgaben, Arbeitsweisen und Nachrichten an Personen laufen im Sprachkanal erst nach „Ich halte fest: … —
            # richtig?“ und Eikes Ja in einer späteren Äußerung, mit genau den vorgelesenen Angaben.
            args, rueckfrage = gegenlesen.pruefen(name, args, zustimmung, rueckfragen)
            if rueckfrage is not None:
                zustimmung, verbraucht = False, True      # ein Ja VOR der Rückfrage zählt nie
            elif gegenlesen.betrifft(name, args):
                bestaetigt = True                         # Eikes Ja auf das Vorgelesene: gilt für genau diesen Aufruf
        if rueckfrage is not None:
            ergebnis = {"ergebnis": rueckfrage.text, "status": "rueckfrage", "art": rueckfrage.art, "quittung": rueckfrage.quittung}
        else:
            ergebnis = await werkzeuge.ausfuehren(name, args, fremde_inhalte=fremd,
                                                  gespraech=mit.gespraech, zustimmung=zustimmung,
                                                  aktionen=aktionen, eingabe=eingabe)
        if bestaetigt:
            zustimmung, verbraucht = False, True
        if zustimmung and werkzeuge.braucht_zustimmung(name):
            zustimmung = False   # verbraucht — der nächste Aufruf braucht ein neues Ja
            verbraucht = True
        fremd = fremd or werkzeuge.liefert_fremdes(name, ergebnis)
        await browser.send_json({"type": "werkzeug", "name": name})
        for a in aktionen:            # z.B. „oeffnen“: die App zeigt den Knopf; „pc“: eine Rückfrage wartet
            if a["typ"] == "bild_modell":
                bilder_fuers_modell.append(a)     # das Modell sieht es, der Browser nicht
                continue
            await browser.send_json({"type": a["typ"], **{k: v for k, v in a.items() if k != "typ"}})
        mit.proto.beitrag(mit.gespraech, "system",
                          f"Werkzeug {name}: {ergebnis.get('ergebnis') or ergebnis.get('fehler')}", "werkzeug")
        if "status" in ergebnis:
            eine = {"name": name, **{k: ergebnis.get(k, "") for k in ("status", "art", "quittung")}}
            if wirkungen is not None:
                wirkungen.append(eine)
            # Die Quittung baut der Kern (nicht das Modell): als Fakt-Zeile an den Browser und ins Protokoll.
            for q in wirk.quittungen([eine]):
                await browser.send_json({"type": "quittung", **q})
                mit.proto.beitrag(mit.gespraech, "system", q["text"], "quittung")
        antworten.append({"id": fc.get("id"), "name": name, "response": _antwort_fuers_modell(ergebnis)})
    await gem.send(json.dumps({"toolResponse": {"functionResponses": antworten}}))
    for bild in bilder_fuers_modell:
        await gem.send(json.dumps(bildkontext_nachricht(bild["mime"], bild["data"])))
    return fremd, verbraucht


async def _text(gem, browser, text: str, proto, gespraech, zustand: dict | None = None) -> None:
    text = text.strip()[:12000]
    if not text:
        return
    proto.beitrag(gespraech, "du", text)
    _strang("du", text, gespraech)
    await gem.send(json.dumps(stream_ende()))
    teile = [{"text": text}]
    links = web.links_finden(text)[:3]
    if zustand is not None:
        zustand["fremd"] = bool(links)
        zustimmung_merken(zustand, text)
        zustand["eingabe"] = text
    for url in links:
        await browser.send_json({"type": "link", "url": url, "zustand": "lese"})
    if links:
        for e in await web.links_lesen(text):
            await browser.send_json({"type": "link", "url": e["url"],
                                     "zustand": "gelesen" if e["ok"] else "fehler",
                                     "titel": e.get("titel", ""), "grund": e.get("grund", "")})
            proto.beitrag(gespraech, "system",
                          f"Link {'gelesen' if e['ok'] else 'nicht gelesen'}: {e['url']}", "link")
            teile.append({"text": e["kontext"]})
    await gem.send(json.dumps(inhalt_nachricht(teile)))


async def _bild(gem, browser, m: dict, proto, gespraech, zustand: dict | None = None) -> None:
    mime, daten = str(m.get("mime", "")), str(m.get("data", ""))
    if mime not in BILD_TYPEN or not daten or len(daten) > MAX_BILD_B64:
        await browser.send_json({"type": "fehler", "message": "Dieses Bild kann ich nicht annehmen (Typ oder Größe)."})
        return
    text = str(m.get("text", "")).strip()[:2000] or "Schau dir dieses Bild an und sag kurz, was du siehst."
    proto.beitrag(gespraech, "du", f"[Bild] {text}", "bild")
    _strang("du", f"[Bild] {text}", gespraech)
    if zustand is not None:
        zustand["fremd"] = True
        try:
            zustand["bild_offen"] = bilder.speichern(gespraech, mime, daten, text)
        except Exception:
            log.exception("Bild nicht gespeichert")
    await gem.send(json.dumps(stream_ende()))
    await gem.send(json.dumps(inhalt_nachricht([{"inlineData": {"mimeType": mime, "data": daten}},
                                               {"text": text}])))
