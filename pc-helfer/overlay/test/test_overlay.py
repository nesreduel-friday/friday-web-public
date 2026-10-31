"""Tests für den Friday-Overlay. Ohne Mikro, ohne Lautsprecher, ohne Bildschirm.

Ausführen (im Ordner pc-helfer/overlay):  python -m pytest test -q
Das Wake-Word-Modell (Vosk) kommt aus FRIDAY_VOSK_MODELL; fehlt es, werden nur diese Tests übersprungen.
Das Gespräch wird gegen einen Fake-Kern geprüft, der das Protokoll von `kern/sitzung/live.py` nachbildet.
"""

import asyncio
import base64
import json
import os
import queue
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
import websockets

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parent))

from gespraech import AUS_RATE, Abspielpuffer, Gespraech, Sendetor, kern_adresse, pegel  # noqa: E402
from stand import werkzeug_anzeige  # noqa: E402

FIXTURE = HIER / "fixtures" / "friday.wav"


def _pcm16k(pfad: Path) -> bytes:
    with wave.open(str(pfad)) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    if rate != 16000:
        n = int(len(x) * 16000 / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return x.astype(np.int16).tobytes()


# ---- reine Funktionen ----------------------------------------------------------

def test_pegel_stille_und_laut():
    assert pegel(b"") == 0.0
    assert pegel(b"\x00\x00" * 1600) == 0.0
    laut = np.full(1600, 16000, dtype=np.int16).tobytes()
    assert 0.45 < pegel(laut) < 0.55


def test_kern_adresse_wechselt_das_schema():
    assert kern_adresse("https://hphome.beispiel.ts.net") == "wss://hphome.beispiel.ts.net/ws/live"
    assert kern_adresse("http://127.0.0.1:8124/") == "ws://127.0.0.1:8124/ws/live"


def test_abspielpuffer_holt_in_stuecken_und_leert():
    puffer = Abspielpuffer()
    puffer.hinzu(b"abcdef")
    assert puffer.lies(4) == b"abcd"
    assert puffer.lies(10) == b"ef"
    assert puffer.lies(2) == b""
    puffer.hinzu(b"xyz")
    puffer.leeren()
    assert puffer.lies(3) == b""


# ---- Wake-Word -------------------------------------------------------------------

def _wecker():
    modell = os.environ.get("FRIDAY_VOSK_MODELL", "")
    if not modell or not Path(modell).is_dir():
        pytest.skip("FRIDAY_VOSK_MODELL nicht gesetzt")
    from wecken import Wecker
    return Wecker(modell, "friday")


def _in_bloecke(daten: bytes, n: int = 1600 * 2):
    for i in range(0, len(daten), n):
        yield daten[i:i + n]


def test_wecker_erkennt_friday():
    wecker = _wecker()
    daten = _pcm16k(FIXTURE) + b"\x00" * 32000
    assert any(wecker.hoere(b) for b in _in_bloecke(daten))


def test_wecker_schweigt_bei_stille():
    wecker = _wecker()
    assert not any(wecker.hoere(b) for b in _in_bloecke(b"\x00" * 16000 * 2 * 3))


# ---- Gespräch gegen Fake-Kern ----------------------------------------------------

class FakeKern:
    """Spricht wie `/ws/live`: ready nach start, Stimme nach dem ersten Mikro-Block, Text, dann Ende bei stop."""

    def __init__(self, stimme_bytes: int = 0):
        self.empfangen: list[dict] = []
        self.stimme_bytes = stimme_bytes

    async def handler(self, ws):
        start = json.loads(await ws.recv())
        self.empfangen.append(start)
        await ws.send(json.dumps({"type": "ready", "fortgesetzt": False}))
        if self.stimme_bytes:
            stimme = b"\x01\x00" * (self.stimme_bytes // 2)
            await ws.send(json.dumps({"type": "audio",
                                      "data": base64.b64encode(stimme).decode("ascii")}))
            await ws.send(json.dumps({"type": "text", "role": "friday", "text": "Hallo Eike"}))
            await ws.send(json.dumps({"type": "turn_end"}))
        async for roh in ws:
            if isinstance(roh, bytes):
                continue
            nachricht = json.loads(roh)
            self.empfangen.append(nachricht)
            if nachricht.get("type") == "stop":
                break


async def _mit_fakekern(kern: FakeKern, ablauf):
    async with websockets.serve(kern.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        return await ablauf(f"http://127.0.0.1:{port}")


def test_gespraech_schickt_start_mit_kennung_und_audio():
    async def lauf():
        kern = FakeKern()
        mikro = queue.Queue()
        meldungen = []

        async def ablauf(basis):
            sitzung = Gespraech(basis, "pc-overlay", mikro, Abspielpuffer(),
                                lambda *ev: meldungen.append(ev), leerlauf=0.6)
            task = asyncio.ensure_future(sitzung.laufen())
            await asyncio.sleep(0.1)
            mikro.put(np.full(1600, 9000, dtype=np.int16).tobytes())
            return await asyncio.wait_for(task, 10)

        grund = await _mit_fakekern(kern, ablauf)
        return kern, meldungen, grund

    kern, meldungen, grund = asyncio.run(lauf())
    assert kern.empfangen[0] == {"type": "start", "kennung": "pc-overlay"}
    audio = [m for m in kern.empfangen if m.get("type") == "audio"]
    assert audio, "Mikro-Block kam nicht beim Kern an"
    assert len(base64.b64decode(audio[0]["data"])) == 3200
    assert ("zustand", "hoert") in meldungen
    assert grund == "leerlauf"
    assert any(m.get("type") == "stop" for m in kern.empfangen)


def test_gespraech_stimme_kommt_an_und_mikro_schweigt_waehrend_friday_spricht():
    async def lauf():
        kern = FakeKern(stimme_bytes=AUS_RATE * 2 * 2)       # zwei Sekunden Stimme
        mikro = queue.Queue()
        puffer = Abspielpuffer()
        meldungen = []

        async def ablauf(basis):
            sitzung = Gespraech(basis, "pc-overlay", mikro, puffer,
                                lambda *ev: meldungen.append(ev), leerlauf=5)
            task = asyncio.ensure_future(sitzung.laufen())
            await asyncio.sleep(0.4)               # Friday spricht jetzt
            mikro.put(np.full(1600, 9000, dtype=np.int16).tobytes())
            await asyncio.sleep(0.4)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        await _mit_fakekern(kern, ablauf)
        return kern, meldungen, puffer

    kern, meldungen, puffer = asyncio.run(lauf())
    assert ("text", "friday", "Hallo Eike") in meldungen
    assert ("zustand", "spricht") in meldungen
    from gespraech import _bereit_ton
    assert len(puffer.lies(10**7)) == AUS_RATE * 2 * 2 + len(_bereit_ton())   # Stimme plus Bereit-Ton
    gesendet = [m for m in kern.empfangen if m.get("type") == "audio"]
    assert gesendet == [], "Mikro ging hinaus, während Friday sprach (Halb-Duplex verletzt)"


def test_gespraech_meldet_kernfehler():
    async def lauf():
        class Fehler(FakeKern):
            async def handler(self, ws):
                await ws.recv()
                await ws.send(json.dumps({"type": "fehler", "message": "Kein Gemini-Schlüssel hinterlegt."}))
        kern = Fehler()
        meldungen = []

        async def ablauf(basis):
            sitzung = Gespraech(basis, "pc-overlay", queue.Queue(), Abspielpuffer(),
                                lambda *ev: meldungen.append(ev))
            return await asyncio.wait_for(sitzung.laufen(), 10)

        grund = await _mit_fakekern(kern, ablauf)
        return grund, meldungen

    grund, meldungen = asyncio.run(lauf())
    assert grund == "fehler"
    assert ("fehler", "Kein Gemini-Schlüssel hinterlegt.") in meldungen


# ---- Overlay-Zustand -------------------------------------------------------------

def test_stand_folgt_den_ereignissen():
    from stand import MAX_ZEILEN, Stand, werkzeug_anzeige
    stand = Stand()
    stand.verarbeite(("zustand", "denkt"))
    stand.verarbeite(("werkzeug", "pc_webseite"))
    assert stand.werkzeug == "pc_webseite"
    stand.verarbeite(("zustand", "spricht"))
    assert stand.werkzeug == ""             # sobald gesprochen wird, ist das Werkzeug vorbei
    for i in range(MAX_ZEILEN + 2):
        stand.verarbeite(("text", "du", f"Satz {i}"))
        stand.verarbeite(("zugende",))
    assert len(stand.zeilen) == MAX_ZEILEN
    assert stand.zeilen[-1] == ["du", f"Satz {MAX_ZEILEN + 1}"]
    stand.verarbeite(("fehler", "kaputt"))
    assert stand.zustand == "fehler" and stand.fehler == "kaputt"


def test_werkzeugname_wird_fuer_eike_uebersetzt():
    assert werkzeug_anzeige("pc_webseite") == "am PC"
    assert werkzeug_anzeige("browser_oeffnen") == "im Browser"
    assert werkzeug_anzeige("suche_web") == "arbeitet"


def test_transkript_stuecke_werden_ein_satz():
    from stand import Stand
    stand = Stand()
    for stueck in ["Hallo", " Eike", ",", " wie", " geht", "’s"]:
        stand.verarbeite(("text", "friday", stueck))
    assert stand.zeilen == [["friday", "Hallo Eike, wie geht’s"]]


def test_neue_zeile_bei_rollenwechsel_und_zugende():
    from stand import Stand
    stand = Stand()
    stand.verarbeite(("text", "du", "Wie spät"))
    stand.verarbeite(("text", "du", " ist es?"))
    stand.verarbeite(("text", "friday", "Halb neun."))
    assert [z[0] for z in stand.zeilen] == ["du", "friday"]
    stand.verarbeite(("zugende",))
    stand.verarbeite(("text", "friday", "Ja."))
    assert stand.zeilen[-1] == ["friday", "Ja."]


def test_neue_sitzung_leert_das_transkript():
    from stand import Stand
    stand = Stand()
    stand.verarbeite(("text", "du", "alt"))
    stand.verarbeite(("zustand", "verbinde"))
    assert stand.zeilen == []


def test_fenster_nur_waehrend_des_gespraechs():
    from stand import Stand
    stand = Stand()
    assert not stand.sichtbar
    stand.verarbeite(("zustand", "verbinde"))
    assert stand.sichtbar
    stand.verarbeite(("zustand", "spricht"))
    assert stand.sichtbar
    stand.verarbeite(("zustand", "lauscht"))
    assert not stand.sichtbar
    stand.verarbeite(("fehler", "Gemini weg"))
    assert stand.sichtbar


def test_langer_text_wird_gekuerzt_und_bleibt_am_schluss():
    from stand import MAX_TEXT, Stand
    stand = Stand()
    stand.verarbeite(("text", "friday", "x" * 500 + " Schluss"))
    assert len(stand.zeilen[-1][1]) == MAX_TEXT
    assert stand.zeilen[-1][1].endswith("Schluss")


def test_endesatz_erkannt_unabhaengig_von_schreibweise():
    from gespraech import soll_enden
    assert soll_enden("Danke, reicht!")
    assert soll_enden("ähm also Friday schließen bitte")
    assert soll_enden("tschüss Friday.")
    assert not soll_enden("Wie spät ist es?")
    assert not soll_enden("Danke für die Hilfe")


def test_endesatz_beendet_die_sitzung_nach_fridays_antwort():
    class Kern(FakeKern):
        async def handler(self, ws):
            start = json.loads(await ws.recv())
            self.empfangen.append(start)
            await ws.send(json.dumps({"type": "ready"}))
            await ws.send(json.dumps({"type": "text", "role": "du", "text": "Danke, reicht!"}))
            await ws.send(json.dumps({"type": "text", "role": "friday", "text": "Gern, bis später."}))
            await ws.send(json.dumps({"type": "turn_end"}))
            async for roh in ws:
                if isinstance(roh, str):
                    self.empfangen.append(json.loads(roh))

    async def lauf():
        kern = Kern()

        async def ablauf(basis):
            sitzung = Gespraech(basis, "pc-overlay", queue.Queue(), Abspielpuffer(), lambda *ev: None, leerlauf=60)
            return await asyncio.wait_for(sitzung.laufen(), 10)

        grund = await _mit_fakekern(kern, ablauf)
        return kern, grund

    kern, grund = asyncio.run(lauf())
    assert grund == "ende"
    assert {"type": "stop"} in kern.empfangen


def test_audio_vor_der_verbindung_geht_nach_bereit_raus():
    """Was Eike direkt nach „Friday“ sagt, wartet in der Queue und kommt nach `ready` beim Kern an."""
    async def lauf():
        kern = FakeKern()
        mikro = queue.Queue()
        for _ in range(3):
            mikro.put(np.full(1600, 9000, dtype=np.int16).tobytes())
        meldungen = []

        async def ablauf(basis):
            sitzung = Gespraech(basis, "pc-overlay", mikro, Abspielpuffer(),
                                lambda *ev: meldungen.append(ev), leerlauf=0.8)
            return await asyncio.wait_for(sitzung.laufen(), 10)

        grund = await _mit_fakekern(kern, ablauf)
        return kern, meldungen, grund

    kern, meldungen, grund = asyncio.run(lauf())
    audio = [m for m in kern.empfangen if m.get("type") == "audio"]
    assert len(audio) == 3
    assert grund == "leerlauf"


def test_bereit_ton_liegt_im_abspielpuffer():
    from gespraech import AUS_RATE, _bereit_ton
    ton = _bereit_ton()
    assert len(ton) == int(AUS_RATE * 0.12) * 2
    assert any(b != 0 for b in ton)


def test_abspielpuffer_meldet_leer():
    puffer = Abspielpuffer()
    assert puffer.leer()
    puffer.hinzu(b"ab")
    assert not puffer.leer()
    puffer.lies(2)
    assert puffer.leer()


def test_endesatz_toleriert_andere_schreibweise():
    from gespraech import soll_enden
    for satz in ["Danke, reich.", "Danke reicht!", "reicht, danke", "Friday schließ bitte", "Friday beende das",
                 "Danke schön, danke reicht."]:
        assert soll_enden(satz), satz
    for satz in ["danke für deine Hilfe, das war gut", "Friday, wie spät ist es?", "ich habe genug Zeit",
                 "Danke. Mein Freund reicht mir die Hand"]:
        assert not soll_enden(satz), satz


def test_einzelner_fehltreffer_loest_nicht_aus():
    import wecken
    class Erkenner:
        def __init__(self, texte):
            self.texte = list(texte)
        def AcceptWaveform(self, d):
            return False
        def PartialResult(self):
            import json
            return json.dumps({"partial": self.texte.pop(0) if self.texte else ""})
        def Reset(self):
            pass
        def Result(self):
            return '{"text": ""}'
    w = wecken.Wecker.__new__(wecken.Wecker)
    w._wort = "friday"
    w._folge = 0
    w._rec = Erkenner(["friday", "", "friday", "friday", "friday", "friday"])
    ergebnisse = [w.hoere(b"\x00" * 3200) for _ in range(6)]
    assert ergebnisse == [False, False, False, False, False, True]


def test_verstaerkung_hebt_leise_sprache_und_clippt():
    import numpy as np
    from gespraech import pegel
    leise = (np.full(1600, 300, dtype=np.float32)).astype(np.int16)
    verstaerkt = np.clip(leise.astype(np.float32) * 6.0, -32768, 32767).astype(np.int16)
    assert pegel(verstaerkt.tobytes()) > pegel(leise.tobytes()) * 5
    laut = np.full(1600, 30000, dtype=np.float32)
    geclippt = np.clip(laut * 6.0, -32768, 32767).astype(np.int16)
    assert int(geclippt.max()) == 32767


def _block(amplitude: int) -> bytes:
    return np.full(1600, amplitude, dtype=np.int16).tobytes()


def _durch(tor: Sendetor, amplituden: list[int]) -> list[bool]:
    """Pro Eingangsblock: kam echter Ton heraus (True) oder digitale Stille (False)? Ausgabe um den Vorlauf verzögert."""
    return [any(tor.durch(_block(a), pegel(_block(a)))) for a in amplituden]


def test_tor_laesst_sprache_durch_und_schaltet_gleichmaessigen_laerm_stumm():
    tor = Sendetor(absolut=0.01, faktor=2.0, nachlauf=3, vorlauf=2)
    laerm, sprache = 600, 6000                 # 0.018 (über Untergrenze) und 0.18
    ergebnis = _durch(tor, [laerm] * 120 + [sprache] * 5 + [laerm] * 20)
    assert not any(ergebnis[:118]), "gleichmäßiger Lärm muss stumm bleiben (Grundrauschen steigt mit)"
    assert sum(ergebnis[120:]) >= 5, "Sprache muss ankommen"
    assert tor.stumm > 100


def test_tor_haelt_nach_sprache_offen_und_oeffnet_rueckwirkend():
    tor = Sendetor(absolut=0.01, faktor=2.0, nachlauf=3, vorlauf=2)
    stille = 0
    folge = _durch(tor, [stille] * 30 + [6000] + [stille] * 10)
    # Ausgabe ist um 2 Blöcke verzögert: echter Ton erscheint 2 Blöcke VOR dem Sprachblock (Vorlauf) bis 3 danach (Nachlauf)
    erste = folge.index(True)
    assert 0 < sum(folge) <= 7
    assert not any(folge[erste + 7:])


def test_tor_stille_bleibt_gleich_lang():
    tor = Sendetor()
    for _ in range(10):
        assert len(tor.durch(_block(0), 0.0)) == 3200


def test_gespraech_schickt_aktivitaet_im_start():
    async def lauf():
        kern = FakeKern()
        mikro = queue.Queue()

        async def ablauf(basis):
            sitzung = Gespraech(basis, "pc-overlay", mikro, Abspielpuffer(), lambda *ev: None, leerlauf=0.3,
                                aktivitaet={"start": "low", "pause_ms": 900})
            return await asyncio.wait_for(sitzung.laufen(), 10)

        await _mit_fakekern(kern, ablauf)
        return kern

    kern = asyncio.run(lauf())
    assert kern.empfangen[0] == {"type": "start", "kennung": "pc-overlay",
                                 "aktivitaet": {"start": "low", "pause_ms": 900}}


def test_tor_nach_zuruecksetzen_gibt_nichts_von_vorher_frei():
    tor = Sendetor(absolut=0.01, faktor=2.0, nachlauf=8, vorlauf=2)
    _durch(tor, [0] * 30 + [6000] * 3)        # „Friday spricht“: laute Blöcke im Puffer, Nachlauf läuft
    tor.zuruecksetzen()
    assert not any(_durch(tor, [0] * 15)), "Satzende von Friday darf nicht nachträglich hinausgehen"
