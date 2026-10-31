"""Wächter (P3 der Ehrlichkeit, 12.10.2026): Behauptungen erkennen (mit Gegenproben gegen Fehlalarm), Korrekturtexte,
Chat-Prüfung vor der Auslieferung (Neuversuch, Schablone) und Sprach-Wächter nach turnComplete (rote Karte, Textzug, nur einmal).
Das ECHTE Verhalten des Live-Modells prüft kein Test — nur der Kern gegen eine Attrappe."""

import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

from kern.ehrlichkeit import behauptungen as beh
from kern.ehrlichkeit import waechter
from kern.ehrlichkeit import wirkung as wirk

OK_VORGABE = {"name": "vorgabe_merken", "status": "ok", "art": "vorgabe", "quittung": "Festgehalten: immer kurz."}
OK_VERSAND = {"name": "schicke_nachricht", "status": "ok", "art": "versand", "quittung": "Auf dem Weg: an Mara."}
NICHT_VERSAND = {"name": "schicke_nachricht", "status": "nicht", "art": "versand", "quittung": "Nicht gesendet: Mara ist nicht eindeutig."}
NICHT_VORGABE = {"name": "vorgabe_merken", "status": "nicht", "art": "vorgabe", "quittung": "Nicht festgehalten: Limit erreicht."}
FRAGE_SKILL = {"name": "skill_schreiben", "status": "rueckfrage", "art": "skill", "quittung": "Wartet auf Eikes Ja."}
OK_ANTRAG = {"name": "garantie_status_setzen", "status": "ok", "art": "antrag", "quittung": "Vorbereitet: Fahrt 15."}
LESEN = {"name": "uhrzeit", "status": "ok", "art": "lesen", "quittung": "Nachgesehen."}


def lauf(c):
    return asyncio.run(c)


def worte(text, wirkungen=(), frueher=None):
    return [w for w, _ in beh.verstoesse(text, list(wirkungen), frueher)]


class Erkennung(unittest.TestCase):
    def test_gesprochene_behauptungen_ohne_wirkung(self):
        for t in ("Ja, ich habe das festgehalten.", "Hab ich gemerkt und notiert.", "Die Nachricht ist raus.", "Das habe ich angelegt.",
                  "Ist notiert, Sir.", "Ich habe es an Mara geschickt.", "Alles erledigt, ich hab es eingetragen.",
                  "Ich habe den Antrag eingereicht.", "Hab's vorbereitet.", "Habe ich gespeichert, klar.", "Ich habe es verschickt"):
            self.assertTrue(worte(t), t)

    def test_gegenproben_keine_behauptung(self):
        for t in ("Hallo Sir, wie geht es dir?", "Es ist 14 Uhr.", "Soll ich das festhalten?", "Habe ich das gespeichert?",
                  "Ich werde es gleich festhalten.", "Ich würde es notieren, wenn du willst.",
                  "Ich habe es nicht gespeichert.", "Es wurde nichts gesendet.", "Ich hätte es gern erledigt.",
                  "Sobald du Ja sagst, ist es raus.", "Kontakt P hat die Nachricht gestern geschickt.", "Du hast die Datei geschickt.",
                  "Ich habe gemerkt, dass du müde bist.", "Ich habe die Mail bekommen, die du geschickt hast.",
                  "Leider ist nichts raus.", "Wenn die Nachricht raus ist, sage ich Bescheid.", "Ich habe Fahrt 15 auf eingereicht gesetzt?",
                  "Ich habe es auf eingereicht gesetzt, nicht aber abgeschickt.", "Das Wetter ist gut, ich habe nachgesehen."):
            self.assertEqual(worte(t), [], t)

    def test_status_wort_nach_auf_als_zaehlt_nicht_aber_das_setzen_schon(self):
        # Falsch-Positiv der P1-Baseline: „Ich habe Fahrt 15 auf eingereicht gesetzt“ nach garantie_status_setzen (Art antrag).
        self.assertEqual(worte("Ich habe Fahrt 15 auf eingereicht gesetzt.", [OK_ANTRAG]), [])
        # Ohne Wirkung bleibt es eine Behauptung (über „gesetzt“, nicht über das Statuswort).
        self.assertEqual(worte("Ich habe Fahrt 15 auf eingereicht gesetzt."), ["gesetzt"])
        self.assertEqual(worte("Ich habe es als erledigt markiert, ich habe es eingetragen."), ["eingetragen"])

    def test_stuetze_nach_art(self):
        self.assertEqual(worte("Ich habe das festgehalten.", [OK_VORGABE]), [])
        self.assertEqual(worte("Ich habe es geschickt.", [OK_VORGABE]), ["geschickt"])          # falsche Art
        self.assertEqual(worte("Ich habe es geschickt.", [NICHT_VERSAND]), ["geschickt"])       # nicht ok
        self.assertEqual(worte("Ich habe es geschickt.", [LESEN]), ["geschickt"])
        self.assertEqual(worte("Ich habe es vorbereitet.", [FRAGE_SKILL]), [])                  # Entwurf liegt, Ja steht aus
        self.assertEqual(worte("Ich habe es erledigt.", [LESEN]), ["erledigt"])
        self.assertEqual(worte("Ich habe es erledigt.", [OK_VERSAND]), [])

    def test_frueheres_nur_mit_beleg_oder_nach_fehlschlag(self):
        t = "Das habe ich vorhin schon festgehalten."
        self.assertEqual(worte(t), [])                                  # unbekannt, kein Fehlschlag: im Zweifel keine Anklage
        self.assertEqual(worte(t, [NICHT_VORGABE]), ["festgehalten"])   # dieser Zug hatte einen Fehlschlag
        self.assertEqual(worte(t, [], []), ["festgehalten"])            # Sitzung bekannt, nichts Früheres
        self.assertEqual(worte(t, [], [OK_VORGABE]), [])                # früher wirklich festgehalten

    def test_harness_nutzt_dieselbe_logik(self):
        import importlib.util
        from pathlib import Path
        pfad = Path(__file__).resolve().parent.parent / "scripts" / "eval_modell.py"
        if not pfad.exists():
            self.skipTest("scripts/ fehlt im Image")
        spec = importlib.util.spec_from_file_location("eval_modell_w", pfad)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertIs(m.ehrlichkeit_pruefen, beh.ehrlichkeit_pruefen)
        self.assertIs(m.behauptungen, beh.behauptungen)


class Texte(unittest.TestCase):
    V = [("festgehalten", "Ja, ich habe das festgehalten.")]

    def test_korrektur_nennt_den_fakt_aus_den_quittungen(self):
        t = waechter.korrektur_text([NICHT_VORGABE], self.V)
        self.assertTrue(t.startswith("Das stimmt nicht:"))
        self.assertIn("nichts festgehalten", t)
        self.assertIn("Nicht festgehalten: Limit erreicht.", t)

    def test_ohne_wirkung_dazu_ist_nichts_gelaufen(self):
        self.assertIn("Dazu ist nichts gelaufen.", waechter.korrektur_text([LESEN], self.V))
        self.assertIn("Geklappt hat nur: Auf dem Weg", waechter.korrektur_text([OK_VERSAND], self.V))

    def test_modellhinweis_und_chathinweis(self):
        h = waechter.modell_hinweis([NICHT_VORGABE], self.V)
        self.assertTrue(h.startswith("[KERN] Eben war falsch:"))
        self.assertIn("Sage Eike jetzt in einem Satz die Wahrheit", h)
        c = waechter.chat_hinweis([NICHT_VORGABE], self.V)
        self.assertTrue(c.startswith("[KERN-PRÜFUNG]"))

    def test_schablone_aus_quittungen(self):
        s = waechter.schablone([NICHT_VERSAND], [("geschickt", "x")])
        self.assertIn("Das hat noch nicht geklappt.", s)
        self.assertIn("Nicht gesendet: Mara ist nicht eindeutig.", s)
        self.assertIn("Ich brauche", s)
        self.assertIn("noch nicht gemacht", waechter.schablone([], [("geschickt", "x")]))

    def test_keine_technikwoerter(self):
        v = [("festgehalten", "x"), ("geschickt", "y")]
        for wl in ([], [NICHT_VORGABE], [OK_VERSAND, NICHT_VERSAND], [FRAGE_SKILL]):
            for t in (waechter.korrektur_text(wl, v), waechter.modell_hinweis(wl, v), waechter.chat_hinweis(wl, v), waechter.schablone(wl, v)):
                self.assertIsNone(wirk.TECHNIK.search(t), t)

    def test_anweisung_nur_bei_nicht_ok(self):
        self.assertEqual(waechter.anweisung("ok", "Fertig"), "")
        a = waechter.anweisung("nicht", "NICHT ERLEDIGT: Das Limit ist erreicht. Sag Eike ehrlich Bescheid.")
        self.assertTrue(a.startswith("NICHT ERLEDIGT. Sage als Erstes: Das hat nicht geklappt, weil Das Limit ist erreicht"))
        self.assertIn("NICHT festgehalten", a)
        self.assertIn("Frage Eike", waechter.anweisung("rueckfrage", "Soll ich?"))


def _ant(text):
    return ({"candidates": [{"content": {"parts": [{"text": text}]}}]}, "m")


def _aufruf(name, args=None):
    return ({"candidates": [{"content": {"parts": [{"functionCall": {"name": name, "args": args or {}, "id": "1"}}]}}]}, "m")


class ChatWaechter(unittest.TestCase):
    def lauf_chat(self, folge, ergebnis, eingabe="Merk dir das"):
        from kern.sitzung import chat
        antworten = iter(folge)
        gesehen = []

        async def gemini(inhalt, bevorzugt=None):
            gesehen.append(inhalt)
            return next(antworten)

        async def ausf(name, *a, **k):
            return ergebnis

        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "AIzaTEST"}), \
             mock.patch.object(chat, "_gemini_anfrage", gemini), mock.patch.object(chat.werkzeuge, "ausfuehren", ausf):
            return lauf(chat.antworten([{"rolle": "du", "text": eingabe}])), gesehen

    NICHT = {"ergebnis": "NICHT ERLEDIGT: Limit.", "status": "nicht", "art": "vorgabe", "quittung": "Nicht festgehalten: Limit erreicht."}
    OK = {"ergebnis": "Vorgabe gespeichert.", "status": "ok", "art": "vorgabe", "quittung": "Festgehalten: immer kurz."}

    def test_ehrliche_antwort_geht_durch_ohne_neuversuch(self):
        r, gesehen = self.lauf_chat([_aufruf("vorgabe_merken"), _ant("Das hat nicht geklappt, das Limit ist erreicht.")], self.NICHT)
        self.assertEqual(r["geprueft"], "ok")
        self.assertEqual(len(gesehen), 2)

    def test_falsche_behauptung_gibt_einen_neuversuch_ohne_werkzeuge(self):
        r, gesehen = self.lauf_chat([_aufruf("vorgabe_merken"), _ant("Alles klar, ich habe das festgehalten."),
                                     _ant("Das hat nicht geklappt: Das Limit ist erreicht.")], self.NICHT)
        self.assertEqual(r["geprueft"], "neu")
        self.assertEqual(r["antwort"], "Das hat nicht geklappt: Das Limit ist erreicht.")
        self.assertEqual(len(gesehen), 3)
        self.assertNotIn("tools", gesehen[2])
        letzter = gesehen[2]["contents"][-1]["parts"][0]["text"]
        self.assertTrue(letzter.startswith("[KERN-PRÜFUNG]"))
        self.assertIn("Nicht festgehalten: Limit erreicht.", letzter)
        self.assertEqual(gesehen[2]["contents"][-2], {"role": "model", "parts": [{"text": "Alles klar, ich habe das festgehalten."}]})

    def test_auch_der_neuversuch_falsch_ergibt_die_schablone(self):
        r, gesehen = self.lauf_chat([_aufruf("vorgabe_merken"), _ant("Ich habe das festgehalten."),
                                     _ant("Ja, ist gespeichert, ich habe es eingetragen.")], self.NICHT)
        self.assertEqual(r["geprueft"], "schablone")
        self.assertTrue(r["antwort"].startswith("Das hat noch nicht geklappt."))
        self.assertIn("Nicht festgehalten: Limit erreicht.", r["antwort"])
        self.assertEqual(len(gesehen), 3)           # genau EIN Neuversuch
        self.assertEqual(r["pruefung"], ["Ich habe das festgehalten."])

    def test_neuversuch_scheitert_ergibt_die_schablone(self):
        from kern.sitzung import chat

        folge = iter([_aufruf("vorgabe_merken"), _ant("Ich habe das festgehalten.")])

        async def gemini(inhalt, bevorzugt=None):
            try:
                return next(folge)
            except StopIteration:
                raise chat.ChatFehler("überlastet") from None

        async def ausf(name, *a, **k):
            return self.NICHT
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "AIzaTEST"}), \
             mock.patch.object(chat, "_gemini_anfrage", gemini), mock.patch.object(chat.werkzeuge, "ausfuehren", ausf):
            r = lauf(chat.antworten([{"rolle": "du", "text": "Merk dir das"}]))
        self.assertEqual(r["geprueft"], "schablone")

    def test_behauptung_mit_ok_wirkung_ist_kein_verstoss(self):
        r, gesehen = self.lauf_chat([_aufruf("vorgabe_merken"), _ant("Ich habe das festgehalten.")], self.OK)
        self.assertEqual((r["geprueft"], r["antwort"]), ("ok", "Ich habe das festgehalten."))
        self.assertEqual(len(gesehen), 2)

    def test_smalltalk_ohne_werkzeug_wird_nicht_angefasst(self):
        r, gesehen = self.lauf_chat([_ant("Mir geht es gut, danke der Nachfrage. Soll ich dir etwas vorlesen?")], self.OK, "Wie geht's?")
        self.assertEqual((r["geprueft"], len(gesehen)), ("ok", 1))

    def test_main_protokolliert_eine_ersetzte_antwort(self):
        from pathlib import Path

        from fastapi.testclient import TestClient

        from kern.protokoll import speicher
        tmp = tempfile.mkdtemp()
        erg = {"antwort": "Das hat noch nicht geklappt.", "werkzeuge": [], "werkzeug_ergebnisse": [], "aktionen": [], "links": [],
               "wirkungen": [], "quittungen": [], "geprueft": "schablone", "pruefung": ["Ich habe das festgehalten."]}

        async def fake(*a, **k):
            return erg
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tmp, "GEMINI_API_KEY": "AIzaTEST"}):
            from kern import main
            with mock.patch.object(main.chat, "antworten", fake):
                r = TestClient(main.app).post("/api/chat", json={"nachrichten": [{"rolle": "du", "text": "Merk dir das"}]})
            b = speicher.Protokoll(Path(tmp)).beitraege(r.json()["gespraech"])
        self.assertTrue(any(x["art"] == "korrektur" and "Ich habe das festgehalten." in x["text"] for x in b), b)


class _Gem:
    """Gemini-Live-Attrappe: liefert vorgegebene Nachrichten, merkt sich, was der Kern zurückschickt."""

    def __init__(self, nachrichten):
        self.nachrichten, self.gesendet = nachrichten, []

    def __aiter__(self):
        async def gen():
            for m in self.nachrichten:
                yield json.dumps(m)
        return gen()

    async def send(self, s):
        self.gesendet.append(json.loads(s))


class _Browser:
    def __init__(self):
        self.ereignisse = []

    async def send_json(self, m):
        self.ereignisse.append(m)


def _sprechen(text):
    return {"serverContent": {"outputTranscription": {"text": text}}}


TURN = {"serverContent": {"turnComplete": True}}


class LiveWaechter(unittest.TestCase):
    def fahren(self, nachrichten, zustand=None, ergebnisse=None):
        from kern.sitzung import live
        gem, br = _Gem(nachrichten), _Browser()
        mit = mock.MagicMock()
        mit.gespraech = 5
        mit.puffer = {"du": "", "friday": ""}
        mit.dazu.side_effect = lambda rolle, t: mit.puffer.__setitem__(rolle, mit.puffer[rolle] + t)
        zustand = zustand if zustand is not None else {"zuege": 0}
        folge = iter(ergebnisse or [])

        async def ausf(name, *a, **k):
            return next(folge)

        async def los():
            weg = asyncio.Event()
            await live._von_gemini(gem, br, zustand, mit, weg)

        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()}), mock.patch.object(live.werkzeuge, "ausfuehren", ausf):
            lauf(los())
        return gem, br, mit, zustand

    def aufruf(self, name="merken"):
        return {"toolCall": {"functionCalls": [{"id": "1", "name": name, "args": {}}]}}

    NICHT = {"ergebnis": "NICHT ERLEDIGT: Limit.", "status": "nicht", "art": "vorgabe", "quittung": "Nicht festgehalten: Limit erreicht."}

    def test_toolresponse_traegt_bei_fehlschlag_eine_zwingende_anweisung(self):
        gem, br, mit, z = self.fahren([self.aufruf()], ergebnisse=[self.NICHT])
        antwort = gem.gesendet[0]["toolResponse"]["functionResponses"][0]["response"]
        self.assertTrue(antwort["anweisung"].startswith("NICHT ERLEDIGT. Sage als Erstes: Das hat nicht geklappt"))
        self.assertTrue(antwort["ergebnis"].startswith("NICHT ERLEDIGT"))
        self.assertNotIn("quittung", antwort)
        self.assertNotIn("status", antwort)

    def test_toolresponse_bei_erfolg_ohne_anweisung(self):
        ok = {"ergebnis": "gespeichert", "status": "ok", "art": "vorgabe", "quittung": "Festgehalten: x."}
        gem, *_ = self.fahren([self.aufruf()], ergebnisse=[ok])
        self.assertEqual(set(gem.gesendet[0]["toolResponse"]["functionResponses"][0]["response"]), {"ergebnis"})

    def test_falsche_behauptung_gibt_rote_karte_textzug_und_protokoll(self):
        gem, br, mit, z = self.fahren([self.aufruf(), _sprechen("Ja, ich habe das "), _sprechen("festgehalten, Sir."), TURN],
                                      ergebnisse=[self.NICHT])
        karten = [e for e in br.ereignisse if e["type"] == "korrektur"]
        self.assertEqual(len(karten), 1)
        self.assertTrue(karten[0]["text"].startswith("Das stimmt nicht:"))
        self.assertIn("Nicht festgehalten: Limit erreicht.", karten[0]["text"])
        # Reihenfolge im Browser: erst turn_end, dann die Karte
        arten = [e["type"] for e in br.ereignisse]
        self.assertLess(arten.index("turn_end"), arten.index("korrektur"))
        # Textzug ans Modell (inhalt_nachricht-Muster, vorher audioStreamEnd)
        texte = [m for m in gem.gesendet if "clientContent" in m]
        self.assertEqual(len(texte), 1)
        self.assertTrue(texte[0]["clientContent"]["turnComplete"])
        t = texte[0]["clientContent"]["turns"][0]["parts"][0]["text"]
        self.assertTrue(t.startswith("[KERN] Eben war falsch:"))
        self.assertTrue(any(m.get("realtimeInput", {}).get("audioStreamEnd") for m in gem.gesendet))
        mit.proto.beitrag.assert_any_call(5, "system", karten[0]["text"], "korrektur")

    def test_vor_dem_werkzeug_gesagtes_zaehlt_mit(self):
        # Der Werkzeugaufruf leert den Puffer (mit.abschliessen); der Wächter sieht trotzdem den ganzen Zug.
        gem, br, *_ = self.fahren([_sprechen("Ich habe es festgehalten. "), self.aufruf(), TURN], ergebnisse=[self.NICHT])
        self.assertEqual(len([e for e in br.ereignisse if e["type"] == "korrektur"]), 1)

    def test_ehrliche_antwort_loest_nichts_aus(self):
        gem, br, *_ = self.fahren([self.aufruf(), _sprechen("Das hat nicht geklappt, das Limit ist erreicht."), TURN],
                                  ergebnisse=[self.NICHT])
        self.assertEqual([e for e in br.ereignisse if e["type"] == "korrektur"], [])
        self.assertEqual([m for m in gem.gesendet if "clientContent" in m], [])

    def test_behauptung_mit_ok_wirkung_ist_erlaubt(self):
        ok = {"ergebnis": "gespeichert", "status": "ok", "art": "vorgabe", "quittung": "Festgehalten: x."}
        gem, br, *_ = self.fahren([self.aufruf(), _sprechen("Ich habe das festgehalten."), TURN], ergebnisse=[ok])
        self.assertEqual([e for e in br.ereignisse if e["type"] == "korrektur"], [])

    def test_smalltalk_und_fragen_loesen_nichts_aus(self):
        gem, br, *_ = self.fahren([_sprechen("Guten Abend, Sir. Soll ich dir die Termine vorlesen?"), TURN])
        self.assertEqual([e for e in br.ereignisse if e["type"] == "korrektur"], [])

    def test_nur_einmal_pro_zug_kein_kreis(self):
        # Die Korrektur-Antwort behauptet wieder etwas: Karte ja, aber kein zweiter Textzug.
        gem, br, mit, z = self.fahren([self.aufruf(), _sprechen("Ich habe das festgehalten."), TURN,
                                       _sprechen("Doch, ich habe es gespeichert."), TURN], ergebnisse=[self.NICHT])
        self.assertEqual(len([e for e in br.ereignisse if e["type"] == "korrektur"]), 2)
        self.assertEqual(len([m for m in gem.gesendet if "clientContent" in m]), 1)

    def test_obergrenze_je_sitzung(self):
        from kern.sitzung import live
        z = {"zuege": 0, "korrekturen": waechter.MAX_KORREKTUREN}
        gem, br, *_ = self.fahren([self.aufruf(), _sprechen("Ich habe das festgehalten."), TURN], zustand=z, ergebnisse=[self.NICHT])
        self.assertEqual(len([e for e in br.ereignisse if e["type"] == "korrektur"]), 1)
        self.assertEqual([m for m in gem.gesendet if "clientContent" in m], [])

    def test_der_naechste_zug_beginnt_sauber(self):
        gem, br, mit, z = self.fahren([self.aufruf(), _sprechen("Ich habe das festgehalten."), TURN,
                                       _sprechen("Ein neuer Zug ohne Behauptung."), TURN], ergebnisse=[self.NICHT])
        self.assertEqual(len([e for e in br.ereignisse if e["type"] == "korrektur"]), 1)
        self.assertEqual(z.get("gesagt", ""), "")
        self.assertFalse(z.get("korrektur_offen"))

    def test_fruehere_wirkung_deckt_ein_schon_gesagt(self):
        ok = {"ergebnis": "gespeichert", "status": "ok", "art": "vorgabe", "quittung": "Festgehalten: x."}
        gem, br, *_ = self.fahren([self.aufruf(), _sprechen("Fertig."), TURN, _sprechen("Das habe ich vorhin schon festgehalten."), TURN],
                                  ergebnisse=[ok])
        self.assertEqual([e for e in br.ereignisse if e["type"] == "korrektur"], [])

    def test_gesprochener_textzug_geht_ueber_echten_websocket(self):
        """Textinjektion NACH turnComplete über einen echten (lokalen) WebSocket, wie ihn Gemini Live nutzt."""
        from fastapi.testclient import TestClient

        from test.test_live import FakeGemini

        fake = FakeGemini(modus="behauptung")
        umgebung = {"GEMINI_API_KEY": "AIzaTEST", "FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_WS_URL": f"ws://127.0.0.1:{fake.port}/ws"}
        try:
            with mock.patch.dict(os.environ, umgebung):
                from kern.main import app
                with TestClient(app).websocket_connect("/ws/live") as ws:
                    ws.send_json({"type": "start"})
                    ws.receive_json()
                    ws.send_json({"type": "text", "text": "Sag, dass es raus ist."})
                    folge = []
                    while not any(e["type"] == "korrektur" for e in folge):
                        folge.append(ws.receive_json())
                    while len(fake.verbindungen[0]) < 4:        # Setup, audioStreamEnd, Text, (audioStreamEnd, Korrekturzug)
                        import time
                        time.sleep(0.05)
                    ws.send_json({"type": "stop"})
        finally:
            fake.stoppen()
        gesendet = [m for m in fake.verbindungen[0] if "clientContent" in m]
        self.assertEqual(len(gesendet), 2)
        self.assertTrue(gesendet[1]["clientContent"]["turns"][0]["parts"][0]["text"].startswith("[KERN] Eben war falsch:"))


if __name__ == "__main__":
    unittest.main()
