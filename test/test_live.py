"""Sprachsitzung: Nachrichtenformat und Wiederaufbau — gegen eine Attrappe,
die sich wie Gemini Live verhält und nach dem ersten Zug abbricht."""

import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import websockets
from fastapi.testclient import TestClient

from kern.protokoll import speicher
from kern import umgebung
from kern.sitzung import live


class FakeGemini:
    def __init__(self, modus: str = "abbruch"):
        self.modus = modus
        self.werkzeug_antwort = None
        self.verbindungen, self.kopfzeilen, self.pfade = [], [], []
        self.loop = asyncio.new_event_loop()
        self._bereit = threading.Event()
        self._faden = threading.Thread(target=self._laufen, daemon=True)
        self._faden.start()
        assert self._bereit.wait(5)

    def _laufen(self):
        asyncio.set_event_loop(self.loop)

        async def starten():
            self.server = await websockets.serve(self._handler, "127.0.0.1", 0)
            self.port = self.server.sockets[0].getsockname()[1]
            self._bereit.set()

        self.loop.run_until_complete(starten())
        self.loop.run_forever()

    async def _handler(self, ws):
        nr = len(self.verbindungen)
        nachrichten = []
        self.verbindungen.append(nachrichten)
        self.kopfzeilen.append(ws.request.headers.get("x-goog-api-key"))
        self.pfade.append(ws.request.path)
        nachrichten.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"setupComplete": {}}))
        if nr == 0:
            await ws.send(json.dumps({"sessionResumptionUpdate": {"newHandle": "h-1", "resumable": True}}))
        try:
            async for roh in ws:
                m = json.loads(roh)
                nachrichten.append(m)
                if self.modus == "werkzeug" and "clientContent" in m:
                    await ws.send(json.dumps({"toolCall": {"functionCalls": [
                        {"name": "uhrzeit", "args": {}, "id": "fc_1"}]}}))
                    continue
                if self.modus == "werkzeug" and "toolResponse" in m:
                    self.werkzeug_antwort = m["toolResponse"]
                    await ws.send(json.dumps({"serverContent": {"outputTranscription": {"text": "Es ist spät."}}}))
                    await ws.send(json.dumps({"serverContent": {"turnComplete": True}}))
                    continue
                if self.modus == "behauptung" and "clientContent" in m:
                    erster = sum(1 for x in nachrichten if "clientContent" in x) == 1
                    text = "Ja, ich habe es geschickt, die Nachricht ist raus." if erster else "Nein, es ist nichts gesendet worden."
                    await ws.send(json.dumps({"serverContent": {"outputTranscription": {"text": text}}}))
                    await ws.send(json.dumps({"serverContent": {"turnComplete": True}}))
                    continue
                if "clientContent" in m:
                    await ws.send(json.dumps({"serverContent": {"outputTranscription": {"text": "Hallo Sir."}}}))
                    await ws.send(json.dumps({"serverContent": {"turnComplete": True}}))
                    if nr == 0:
                        await ws.close(1011, "Testabbruch")
                        return
        except websockets.exceptions.ConnectionClosed:
            pass

    def stoppen(self):
        # Sauber herunterfahren: Server schließen, Schleife anhalten, Schleife
        # schließen. Sonst meldet Python ResourceWarnings, und solches Rauschen
        # in der Testausgabe verdeckt irgendwann einen echten Fehler.
        async def schliessen():
            self.server.close()
            await self.server.wait_closed()
        try:
            asyncio.run_coroutine_threadsafe(schliessen(), self.loop).result(5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._faden.join(5)
        self.loop.close()


class Nachrichten(unittest.TestCase):
    def test_audio_im_neuen_format(self):
        # 3.1-flash-live-preview: "realtime_input.media_chunks is deprecated. Use audio"
        m = live.audio_nachricht("AAAA")["realtimeInput"]
        self.assertEqual(m["audio"]["mimeType"], "audio/pcm;rate=16000")
        self.assertNotIn("mediaChunks", m)

    def test_setup_ohne_und_mit_handle(self):
        self.assertEqual(live.setup_nachricht("x")["setup"]["sessionResumption"], {})
        s = live.setup_nachricht("x", "h")["setup"]
        self.assertEqual(s["sessionResumption"], {"handle": "h"})
        self.assertIn("inputAudioTranscription", s)
        self.assertIn("outputAudioTranscription", s)

    def test_aktivitaet_nur_bekannte_werte(self):
        a = live.aktivitaet_config({"start": "low", "ende": "HIGH", "vorlauf_ms": 300, "pause_ms": 99999,
                                    "model": "boese", "x": 1})
        self.assertEqual(a, {"automaticActivityDetection": {
            "startOfSpeechSensitivity": "START_SENSITIVITY_LOW", "endOfSpeechSensitivity": "END_SENSITIVITY_HIGH",
            "prefixPaddingMs": 300, "silenceDurationMs": 3000}})
        for leer in (None, {}, "low", {"start": "mittel", "pause_ms": "viel", "vorlauf_ms": True}):
            self.assertIsNone(live.aktivitaet_config(leer))

    def test_setup_traegt_aktivitaet_nur_wenn_gesetzt(self):
        self.assertNotIn("realtimeInputConfig", live.setup_nachricht("x")["setup"])
        a = live.aktivitaet_config({"start": "low"})
        self.assertEqual(live.setup_nachricht("x", None, a)["setup"]["realtimeInputConfig"], a)


class Wiederaufbau(unittest.TestCase):
    def test_abbruch_wird_mit_handle_fortgesetzt(self):
        fake = FakeGemini()
        tmp = tempfile.mkdtemp()
        umgebung = {"GEMINI_API_KEY": "AIzaTESTschluessel", "FRIDAY_DATEN": tmp,
                    "GEMINI_WS_URL": f"ws://127.0.0.1:{fake.port}/ws"}
        try:
            with mock.patch.dict(os.environ, umgebung):
                from kern.main import app
                with TestClient(app).websocket_connect("/ws/live") as ws:
                    ws.send_json({"type": "start"})
                    self.assertEqual(ws.receive_json(), {"type": "ready", "fortgesetzt": False})
                    ws.send_json({"type": "text", "text": "Hallo Friday"})
                    folge = []
                    while not (folge and folge[-1]["type"] == "ready"):
                        folge.append(ws.receive_json())
                    ws.send_json({"type": "stop"})
        finally:
            fake.stoppen()

        self.assertEqual([m["type"] for m in folge], ["text", "turn_end", "reconnecting", "ready"])
        self.assertEqual(folge[0], {"type": "text", "role": "friday", "text": "Hallo Sir."})
        self.assertTrue(folge[2]["fortsetzung"])
        self.assertTrue(folge[3]["fortgesetzt"])

        erste, zweite = fake.verbindungen[0], fake.verbindungen[1]
        self.assertEqual(erste[0]["setup"]["sessionResumption"], {})
        self.assertEqual(zweite[0]["setup"]["sessionResumption"], {"handle": "h-1"})

        # Vor Text mitten im Gespräch kommt audioStreamEnd — sonst bleibt der Zug stumm.
        ende = next(i for i, m in enumerate(erste) if m.get("realtimeInput", {}).get("audioStreamEnd"))
        text = next(i for i, m in enumerate(erste) if "clientContent" in m)
        self.assertLess(ende, text)

        # Schlüssel als Header, nie in der URL.
        self.assertEqual(fake.kopfzeilen[0], "AIzaTESTschluessel")
        self.assertNotIn("key=", fake.pfade[0])

        beitraege = [(b["rolle"], b["text"]) for b in speicher.Protokoll(Path(tmp)).beitraege(1)]
        self.assertEqual(beitraege, [("du", "Hallo Friday"), ("friday", "Hallo Sir.")])


class Werkzeug(unittest.TestCase):
    def test_uhrzeit_wird_ausgefuehrt_und_mit_id_beantwortet(self):
        fake = FakeGemini(modus="werkzeug")
        umgebung = {"GEMINI_API_KEY": "AIzaTEST", "FRIDAY_DATEN": tempfile.mkdtemp(),
                    "GEMINI_WS_URL": f"ws://127.0.0.1:{fake.port}/ws"}
        try:
            with mock.patch.dict(os.environ, umgebung):
                from kern.main import app
                with TestClient(app).websocket_connect("/ws/live") as ws:
                    ws.send_json({"type": "start"})
                    ws.receive_json()
                    ws.send_json({"type": "text", "text": "Wie spät ist es?"})
                    folge = []
                    while not (folge and folge[-1]["type"] == "turn_end"):
                        folge.append(ws.receive_json())
                    ws.send_json({"type": "stop"})
        finally:
            fake.stoppen()
        self.assertIn({"type": "werkzeug", "name": "uhrzeit"}, folge)
        self.assertIn("uhrzeit", json.dumps(fake.verbindungen[0][0]["setup"]["tools"]))
        antwort = fake.werkzeug_antwort["functionResponses"][0]
        self.assertEqual(antwort["id"], "fc_1")
        self.assertIn(" Uhr am ", antwort["response"]["ergebnis"])


class Schleife(unittest.TestCase):
    """Gespräch 223: 45-mal `musik_geschmack` in einem Zug, ohne ein Wort."""

    def test_derselbe_aufruf_wird_nach_drei_mal_abgesagt(self):
        zaehler = {}
        for _ in range(live.MAX_GLEICHE_AUFRUFE):
            self.assertIsNone(live.schleife(zaehler, "musik_geschmack", {}))
        self.assertIn("nicht noch einmal", live.schleife(zaehler, "musik_geschmack", {}).lower())

    def test_andere_angaben_zaehlen_getrennt(self):
        zaehler = {}
        for i in range(live.MAX_GLEICHE_AUFRUFE + 2):
            self.assertIsNone(live.schleife(zaehler, "suchen", {"anfrage": str(i)}))

    def test_obergrenze_je_zug(self):
        zaehler = {}
        for i in range(live.MAX_AUFRUFE_JE_ZUG):
            self.assertIsNone(live.schleife(zaehler, "suchen", {"anfrage": str(i)}))
        self.assertIsNotNone(live.schleife(zaehler, "suchen", {"anfrage": "noch eine"}))


class AmPc(unittest.TestCase):
    """`oeffnen` gehört aufs Handy. Sitzt Eike am PC, nimmt der Kern es nicht (06.10.2026: Spotify)."""

    def setUp(self):
        umgebung.vergessen()

    def tearDown(self):
        umgebung.vergessen()

    def test_overlay_ist_am_pc(self):
        from kern.sitzung import live
        umgebung.melden({"kennung": "pc-overlay", "art": "overlay", "geraet": "PC", "system": "Windows"})
        self.assertTrue(live._am_pc("pc-overlay"))

    def test_handy_ist_nicht_am_pc(self):
        from kern.sitzung import live
        umgebung.melden({"kennung": "abc123", "art": "installiert", "geraet": "Handy", "system": "iOS"})
        self.assertFalse(live._am_pc("abc123"))
        self.assertFalse(live._am_pc("unbekannt"))


class BildschirmFuerDasModell(unittest.TestCase):
    """Friday sieht den Bildschirm nur, wenn er gebraucht wird — das Bild geht in die Sitzung, nie in den Browser."""

    def test_bild_landet_in_aktionen_und_nicht_im_text(self):
        import asyncio as _a
        from kern.werkzeuge import pc as werkzeug_pc
        from kern.dienste import pc as dienst_pc
        original = dienst_pc.bildschirm_jpeg

        async def falsch():
            return b"\xff\xd8fake"
        dienst_pc.bildschirm_jpeg = falsch
        try:
            aktionen = []
            text = _a.run(werkzeug_pc.pc_bildschirm_ansehen(aktionen=aktionen))
        finally:
            dienst_pc.bildschirm_jpeg = original
        self.assertEqual(aktionen[0]["typ"], "bild_modell")
        self.assertEqual(aktionen[0]["mime"], "image/jpeg")
        self.assertNotIn("fake", text)

    def test_ohne_pc_kommt_eine_ehrliche_antwort(self):
        import asyncio as _a
        from kern.werkzeuge import pc as werkzeug_pc
        from kern.dienste import pc as dienst_pc
        original = dienst_pc.bildschirm_jpeg

        async def aus():
            raise OSError("PC aus")
        dienst_pc.bildschirm_jpeg = aus
        try:
            aktionen = []
            text = _a.run(werkzeug_pc.pc_bildschirm_ansehen(aktionen=aktionen))
        finally:
            dienst_pc.bildschirm_jpeg = original
        self.assertEqual(aktionen, [])
        self.assertIn("nicht sehen", text)

    def test_bildschirm_ist_fremder_text_und_braucht_kein_schreibrecht(self):
        from kern.werkzeuge import pc as werkzeug_pc
        self.assertIn("pc_bildschirm_ansehen", werkzeug_pc.FREMDE_WERKZEUGE)
        self.assertNotIn("pc_bildschirm_ansehen", werkzeug_pc.NUR_MIT_SCHREIBRECHT)

    def test_bildkontext_wartet_nicht_auf_antwort(self):
        from kern.sitzung import live
        m = live.bildkontext_nachricht("image/jpeg", "AAA")
        self.assertFalse(m["clientContent"]["turnComplete"])
        self.assertEqual(m["clientContent"]["turns"][0]["parts"][1]["inlineData"]["data"], "AAA")


class WebseitenOhneRueckfrage(unittest.TestCase):
    def test_webseite_oeffnen_fragt_nach_fremdem_text_nicht_mehr(self):
        from kern.werkzeuge import pc as werkzeug_pc
        self.assertFalse(werkzeug_pc._traegt_hinaus("pc_webseite", {"adresse": "http://hphome:8096/"}))
        self.assertFalse(werkzeug_pc._traegt_hinaus("browser_oeffnen", {"adresse": "https://example.org/"}))
        self.assertTrue(werkzeug_pc._traegt_hinaus("pc_webseite", {"adresse": "https://x.example/?d=geheim"}))
        self.assertTrue(werkzeug_pc._traegt_hinaus("browser_tippen", {"text": "hallo"}))


if __name__ == "__main__":
    unittest.main()


class Zustimmung(unittest.TestCase):
    """Gespräch 439: Kurzes „ja“ kam per Transkription an, aber der ToolCall lief
    erst nach turnComplete — mit zurückgesetzter Zustimmung. Das Ja überlebt
    jetzt die Turn-Grenze, bis es verbraucht ist, Eike ablehnt oder Zeit abläuft."""

    def test_ja_setzt_mit_zeit_nein_loescht(self):
        zustand: dict = {}
        live.zustimmung_merken(zustand, "ja")
        self.assertTrue(zustand["zustimmung"])
        self.assertIn("zustimmung_zeit", zustand)
        live.zustimmung_merken(zustand, "warte mal kurz")
        self.assertFalse(zustand["zustimmung"])
        self.assertNotIn("zustimmung_zeit", zustand)

    def test_neutraler_text_aendert_nichts(self):
        zustand: dict = {}
        live.zustimmung_merken(zustand, "ja")
        live.zustimmung_merken(zustand, "hmm")
        self.assertTrue(live.zustimmung_gilt(zustand))

    def test_verfallene_zustimmung_gilt_nicht(self):
        zustand: dict = {}
        live.zustimmung_merken(zustand, "ja")
        zustand["zustimmung_zeit"] -= live.ZUSTIMMUNG_GILT_S + 10
        self.assertFalse(live.zustimmung_gilt(zustand))
        self.assertFalse(zustand["zustimmung"])

    def test_verbrauchtes_ja_ist_weg(self):
        from kern.werkzeuge import katalog
        self.assertTrue(katalog.braucht_zustimmung("anfrage_senden"))
        self.assertTrue(katalog.braucht_zustimmung("schicke_nachricht"))      # nur für `trotzdem` (heikler Inhalt); sonst Direktversand
        self.assertFalse(katalog.braucht_zustimmung("uhrzeit"))
        self.assertFalse(katalog.braucht_zustimmung("gibt_es_nicht"))

    def test_ablehnung_erkennt_nein_und_warte(self):
        from kern.werkzeuge import katalog
        self.assertTrue(katalog.ablehnung_erkannt("nein, lass es"))
        self.assertTrue(katalog.ablehnung_erkannt("warte einen Moment"))
        self.assertFalse(katalog.ablehnung_erkannt("ja, mach es jetzt"))


class EingabeFuerWerkzeuge(unittest.TestCase):
    """30.10.2026: Eine Handy-Suche („Füge Titus hinzu“) startet nur auf Eikes Wort. Der ToolCall darf
    nach `turnComplete` kommen — dann ist `eingabe` geleert und Eikes Auftrag ginge verloren."""

    def test_laufende_eingabe_hat_vorrang(self):
        self.assertEqual(live.eingabe_fuer_werkzeuge({"eingabe": "Füge Mara hinzu",
                                                      "eingabe_letzter_zug": "Füge Titus hinzu"}),
                         "Füge Mara hinzu")

    def test_nach_dem_turnende_gilt_die_letzte_aeusserung(self):
        self.assertEqual(live.eingabe_fuer_werkzeuge({"eingabe": "", "eingabe_letzter_zug": "Füge Titus hinzu"}),
                         "Füge Titus hinzu")

    def test_ohne_aeusserung_leer(self):
        self.assertEqual(live.eingabe_fuer_werkzeuge({}), "")
