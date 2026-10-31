"""WhatsApp-Verläufe: parsen, speichern, zuordnen, auswerten — ohne Netz."""

import asyncio
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from kern.chats import ablage, auswertung, einlesen, parser
from kern.main import app
from kern.schule import briefe
from kern.werkzeuge import chats as werkzeug
from kern.werkzeuge import katalog

IPHONE = (
    "‎[16.03.22, 21:34:55] Eike: Hallo Kontakt P\n"
    "[16.03.22, 21:35:10] Kontakt P: Hallo mein Schatz, wie war die Schule?\n"
    "[16.03.22, 21:36:00] Eike: Ganz gut. Morgen Mathe-Arbeit\n"
    "Ich hab Angst\n"                                        # Folgezeile
    "[17.03.22, 08:00:00] Kontakt P: Bild weggelassen\n"          # Medien → weg
    "[17.03.22, 08:01:00] Kontakt P: Viel Erfolg!\n"
)
ANDROID = (
    "16.03.22, 21:34 - Nachrichten sind Ende-zu-Ende-verschlüsselt.\n"   # System → weg
    "16.03.22, 21:34 - Kontakt A: Kommst du morgen zum Basketball?\n"
    "16.03.22, 21:40 - Eike: Klar!\n"
)


class Parser(unittest.TestCase):
    def test_iphone(self):
        n = list(parser.nachrichten(io.StringIO(IPHONE)))
        self.assertEqual(len(n), 4)                                  # 2x Medien/keine
        self.assertEqual(n[0], {"zeit": "2022-03-16T21:34:55", "absender": "Eike", "text": "Hallo Kontakt P"})
        self.assertEqual(n[2]["text"], "Ganz gut. Morgen Mathe-Arbeit\nIch hab Angst")   # mehrzeilig
        self.assertTrue(all("weggelassen" not in x["text"] for x in n))

    def test_android_und_system_weg(self):
        n = list(parser.nachrichten(io.StringIO(ANDROID)))
        self.assertEqual([x["absender"] for x in n], ["Kontakt A", "Eike"])

    def test_partner_aus_name(self):
        self.assertEqual(parser.partner_aus_name("WhatsApp Chat mit Kontakt P.txt"), "Kontakt P")
        self.assertEqual(parser.partner_aus_name("chats/_chat.txt"), "_chat")


class Ablegen(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _zip(self):
        p = Path(os.environ["FRIDAY_DATEN"]) / "export.zip"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("WhatsApp Chat mit Kontakt P.txt", IPHONE)
            z.writestr("WhatsApp Chat mit Kontakt A.txt", ANDROID)
            z.writestr("__MACOSX/ignore.txt", "müll")
        return p

    def test_einlesen_und_suche(self):
        chats = einlesen.einlesen(self._zip(), "export.zip")
        self.assertEqual({c["partner"] for c in chats}, {"Kontakt P", "Kontakt A"})
        a = ablage.standard()
        self.assertEqual(a.stand()["chats"], 2)
        treffer = a.suchen("mathe")
        self.assertEqual(len(treffer), 1)
        self.assertIn("Mathe-Arbeit", treffer[0]["text"])
        self.assertEqual(a.suchen("uberraschung"), [])           # ohne Umlaut, kein Treffer

    def test_zuordnen_und_offene(self):
        einlesen.einlesen(self._zip(), "export.zip")
        a = ablage.standard()
        offen = {o["absender"] for o in a.offene_absender()}
        self.assertEqual(offen, {"Eike", "Kontakt P", "Kontakt A"})
        ich = a.person_anlegen("Eike", ich=True)
        mama = a.person_anlegen("Kontakt P", "Mutter")
        a.zuordnen("Eike", ich)
        a.zuordnen("Kontakt P", mama)
        self.assertEqual({o["absender"] for o in a.offene_absender()}, {"Kontakt A"})
        self.assertEqual(a.suchen("hallo", person=mama)[0]["absender"], "Kontakt P")

    def test_abschnitte_pro_monat(self):
        einlesen.einlesen(self._zip(), "export.zip")
        a = ablage.standard()
        self.assertTrue(all(len(x) >= 0 for x in [a.offene_abschnitte()]))
        self.assertEqual(len(a.offene_abschnitte()), 2)          # ein Monat je Chat


class Auswerten(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "k"})
        self.env.start()
        p = Path(os.environ["FRIDAY_DATEN"]) / "mama.txt"
        p.write_text(IPHONE, encoding="utf-8")
        einlesen.einlesen(p, "WhatsApp Chat mit Kontakt P.txt")
        a = ablage.standard()
        self.ich = a.person_anlegen("Eike", ich=True)
        self.mama = a.person_anlegen("Kontakt P", "Mutter")
        a.zuordnen("Eike", self.ich)
        a.zuordnen("Kontakt P", self.mama)
        self.abschnitt = mock.AsyncMock(return_value=(
            {"kurz": "Schule und Mathe-Angst.", "themen": ["Schule"], "ereignisse": [],
             "ueber_eike": ["Eike hatte Angst vor der Mathe-Arbeit."],
             "ueber_partner": ["Kontakt P ist fürsorglich."]}, "gemini-test"))
        self.profil = mock.AsyncMock(return_value=(
            {"beziehung": "Eikes Mutter, enges Verhältnis.", "ueber_person": ["Kontakt P ist fürsorglich."],
             "ueber_eike": ["Eike wird vor Prüfungen nervös."], "themen": ["Schule"], "ereignisse": [],
             "geburtstag": ""}, "gemini-test"))
        self.graph = mock.patch.object(auswertung.graph, "einspeisen", mock.AsyncMock(return_value=True))
        self.graph.start()

    def tearDown(self):
        self.graph.stop()
        self.env.stop()

    def test_durchgang_wertet_aus_und_baut_profil_mit_vorschlaegen(self):
        rufe = {"abschnitt": self.abschnitt, "profil": self.profil}

        async def gemini(teile, anweisung=None, schema=None):
            return await (rufe["profil"] if "Personenprofil" in anweisung else rufe["abschnitt"])(teile)

        with mock.patch.object(briefe, "_gemini", gemini):
            asyncio.run(auswertung.durchgang())
        a = ablage.standard()
        self.assertEqual(a.stand()["abschnitte"].get("fertig"), 1)
        mama = next(p for p in a.personen() if p["id"] == self.mama)
        self.assertIn("enges Verhältnis", mama["profil"]["beziehung"])
        # Aussagen sind Vorschläge, keine Fakten
        from kern.gedaechtnis import speicher as gsp
        vorschlaege = {v["thema"]: v["inhalt"] for v in gsp.standard().vorschlaege()}
        self.assertIn("Kontakt P", vorschlaege)
        self.assertIn("Eike", vorschlaege)
        self.assertEqual(gsp.standard().fakten(), [])            # nichts direkt gemerkt

    def test_werkzeug_person_ueber(self):
        a = ablage.standard()
        a.abschnitt_fertig(a.offene_abschnitte()[0], {"kurz": "x", "ueber_partner": ["Kontakt P ist fürsorglich."],
                                                      "ueber_eike": [], "themen": [], "ereignisse": []}, "t")
        with mock.patch.object(briefe, "_gemini", self.profil):
            asyncio.run(auswertung.profil_bauen(self.mama))
        text = asyncio.run(werkzeug.person_ueber("Kontakt P"))
        self.assertIn("Eikes Mutter", text)
        self.assertIn("Kontakt P ist fürsorglich", text)
        self.assertFalse(katalog.liefert_fremdes("person_ueber"))   # Nachschlagen darf „sag Kontakt A …“ nicht sperren
        self.assertTrue(katalog.liefert_fremdes("chat_suchen"))

    def test_nachholen_schickt_dieselbe_episode_nicht_dauernd_neu(self):
        # 05.10.2026: Graphitis Schlange ist langsam; „noch nicht angekommen“ hieß bisher „verloren“ — das Profil von
        # Kontakt R wurde in 20 Minuten 16-mal geschickt und fraß die Kapazität.
        auswertung._nachgeholt.clear()
        a = ablage.standard()
        mama = next(x for x in a.personen() if x["id"] == self.mama)
        a.profil_setzen(self.mama, dict(mama["profil"] or {"beziehung": "x"}), "t")
        with mock.patch.object(auswertung.graph, "angekommen", mock.AsyncMock(return_value=False)), \
             mock.patch.object(auswertung.graph, "einspeisen", mock.AsyncMock(return_value=True)) as ein:
            erst = asyncio.run(auswertung.graph_nachholen())
            nochmal = asyncio.run(auswertung.graph_nachholen())
        self.assertEqual((erst, nochmal), (1, 0))
        self.assertEqual(ein.await_count, 1)

    def test_nachholen_wartet_solange_eikes_eigene_gespraeche_offen_sind(self):
        # Eikes Entscheidung 05.10.2026: bei knappem Tageslimit zuerst seine Gespräche, WhatsApp-Profile danach.
        auswertung._nachgeholt.clear()
        a = ablage.standard()
        mama = next(x for x in a.personen() if x["id"] == self.mama)
        a.profil_setzen(self.mama, dict(mama["profil"] or {"beziehung": "x"}), "t")
        with mock.patch.object(auswertung.graph, "angekommen", mock.AsyncMock(return_value=False)), \
             mock.patch.object(auswertung.graph, "einspeisen", mock.AsyncMock(return_value=True)) as ein, \
             mock.patch("kern.protokoll.speicher.Protokoll.graph_eigene_offen", return_value=True):
            self.assertEqual(asyncio.run(auswertung.graph_nachholen()), 0)
        ein.assert_not_awaited()

    def test_nachholen_nach_der_wartezeit_schickt_wieder(self):
        auswertung._nachgeholt.clear()
        a = ablage.standard()
        mama = next(x for x in a.personen() if x["id"] == self.mama)
        a.profil_setzen(self.mama, dict(mama["profil"] or {"beziehung": "x"}), "t")
        with mock.patch.object(auswertung.graph, "angekommen", mock.AsyncMock(return_value=False)), \
             mock.patch.object(auswertung.graph, "einspeisen", mock.AsyncMock(return_value=True)) as ein:
            asyncio.run(auswertung.graph_nachholen())
            for k in auswertung._nachgeholt:
                auswertung._nachgeholt[k] -= auswertung.NACHHOLEN_WARTEZEIT_S + 1
            asyncio.run(auswertung.graph_nachholen())
        self.assertEqual(ein.await_count, 2)

    def test_nachholen_ohne_antwort_von_graphiti_schickt_nichts(self):
        auswertung._nachgeholt.clear()
        a = ablage.standard()
        mama = next(x for x in a.personen() if x["id"] == self.mama)
        a.profil_setzen(self.mama, dict(mama["profil"] or {"beziehung": "x"}), "t")
        with mock.patch.object(auswertung.graph, "angekommen", mock.AsyncMock(return_value=None)), \
             mock.patch.object(auswertung.graph, "einspeisen", mock.AsyncMock(return_value=True)) as ein:
            self.assertEqual(asyncio.run(auswertung.graph_nachholen()), 0)
        ein.assert_not_awaited()

    def test_verlorene_episode_wird_nachgeholt(self):
        auswertung._nachgeholt.clear()
        # Kontakt M-Pyttel-Fall 04.10.2026: Profil da, Graph-Episode im 429 untergegangen.
        a = ablage.standard()
        mama = next(x for x in a.personen() if x["id"] == self.mama)
        profil = dict(mama["profil"] or {"beziehung": "Eikes Mutter.", "ueber_person": [],
                                         "ueber_eike": [], "themen": [], "ereignisse": []})
        a.profil_setzen(self.mama, profil, "t")
        with mock.patch.object(auswertung.graph, "angekommen", mock.AsyncMock(return_value=False)), \
             mock.patch.object(auswertung.graph, "einspeisen", mock.AsyncMock(return_value=True)) as ein:
            geschafft = asyncio.run(auswertung.graph_nachholen())
        self.assertEqual(geschafft, 1)
        self.assertIn("whatsapp-", ein.await_args.args[0])

    def test_angekommene_episode_bleibt_einmalig(self):
        auswertung._nachgeholt.clear()
        a = ablage.standard()
        mama = next(x for x in a.personen() if x["id"] == self.mama)
        a.profil_setzen(self.mama, dict(mama["profil"] or {"beziehung": "x"}), "t")
        with mock.patch.object(auswertung.graph, "angekommen", mock.AsyncMock(return_value=True)), \
             mock.patch.object(auswertung.graph, "einspeisen", mock.AsyncMock()) as ein:
            geschafft = asyncio.run(auswertung.graph_nachholen())
        self.assertEqual(geschafft, 0)
        ein.assert_not_awaited()


class Seite(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def test_hochladen_und_auswerten_erst_nach_zuordnung(self):
        r = self.c.post("/api/chats/hochladen", files={"datei": ("WhatsApp Chat mit Kontakt P.txt", IPHONE, "text/plain")})
        self.assertTrue(r.json()["ok"])
        self.assertGreaterEqual(r.json()["nachrichten"], 4)
        # solange Absender offen sind: 409
        self.assertEqual(self.c.post("/api/chats/auswerten").status_code, 409)
        d = self.c.get("/api/chats").json()
        self.assertEqual(d["stand"]["chats"], 1)
        ich = self.c.post("/api/chats/personen", json={"name": "Eike", "ich": True}).json()["id"]
        mama = self.c.post("/api/chats/personen", json={"name": "Kontakt P", "rolle": "Mutter"}).json()["id"]
        for absender, pid in (("Eike", ich), ("Kontakt P", mama)):
            self.c.post("/api/chats/zuordnen", json={"absender": absender, "person": pid})
        with mock.patch.object(chats_auswertung(), "durchgang", mock.AsyncMock(return_value=1)):
            self.assertEqual(self.c.post("/api/chats/auswerten").status_code, 200)

    def test_status_laeuft_bleibt_nach_neuoeffnen(self):
        self.c.post("/api/chats/hochladen", files={"datei": ("WhatsApp Chat mit Kontakt P.txt", IPHONE, "text/plain")})
        # Solange Absender offen: nicht laufen, sondern zuordnen verlangen.
        a1 = self.c.get("/api/chats").json()["auswertung"]
        self.assertFalse(a1["laeuft"])
        self.assertIn("zuordnen", a1["text"])
        ich = self.c.post("/api/chats/personen", json={"name": "Eike", "ich": True}).json()["id"]
        mama = self.c.post("/api/chats/personen", json={"name": "Kontakt P"}).json()["id"]
        for absender, pid in (("Eike", ich), ("Kontakt P", mama)):
            self.c.post("/api/chats/zuordnen", json={"absender": absender, "person": pid})
        # Jetzt gibt es offene Abschnitte → der Status kommt aus dem Server, nicht aus dem Browser.
        a2 = self.c.get("/api/chats").json()["auswertung"]
        self.assertTrue(a2["laeuft"])
        self.assertIn("wertet die Verläufe aus", a2["text"])

    def test_falscher_dateityp(self):
        r = self.c.post("/api/chats/hochladen", files={"datei": ("foto.jpg", b"\xff\xd8", "image/jpeg")})
        self.assertEqual(r.status_code, 400)


def chats_auswertung():
    from kern import main
    return main.chat_auswertung


class Automatisch(unittest.TestCase):
    """Eike soll nicht 21-mal von Hand zuordnen müssen."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.a = ablage.standard()

    def tearDown(self):
        self.env.stop()

    def _chat(self, name, absender):
        cid = self.a.chat_anlegen(f"WhatsApp Chat mit {name}.txt", name)
        self.a.nachrichten_schreiben(cid, [{"zeit": "2026-09-01T10:00:00", "absender": s, "text": "hallo"}
                                           for s in absender])
        self.a.chat_abschliessen(cid)
        return cid

    def test_erkennt_eike_und_legt_den_rest_an(self):
        self._chat("Kontakt P", ["Eike", "Kontakt P"])
        self._chat("Kontakt A", ["Eike", "Kontakt A 🏀"])
        r = self.a.zuordnen_automatisch()
        self.assertEqual(r["ich"], "Eike")               # in beiden Chats -> das ist Eike
        self.assertEqual(r["offen"], 0)                  # nichts bleibt liegen
        self.assertEqual(r["neu"], 2)                    # Kontakt P und Kontakt A neu angelegt
        namen = {p["name"]: p["ich"] for p in self.a.personen()}
        self.assertEqual(namen.get("Eike"), 1)
        self.assertIn("Kontakt A", namen)                      # Emoji ist aus dem Namen raus
        self.assertNotIn("Kontakt A 🏀", namen)

    def test_erkennt_bekannte_person_in_anderer_schreibweise(self):
        self._chat("Kontakt P", ["Eike", "Kontakt P"])
        self._chat("Kontakt A", ["Eike", "Kontakt A"])
        self.a.zuordnen_automatisch()
        vorher = len(self.a.personen())
        self._chat("Tim2", ["Eike", "Kontakt A 🏀"])           # derselbe Kontakt A, anders geschrieben
        r = self.a.zuordnen_automatisch()
        self.assertEqual(r["neu"], 0)                    # keine zweite Kontakt A-Person
        self.assertEqual(len(self.a.personen()), vorher)

    def test_bei_nur_einem_chat_wird_nicht_geraten(self):
        self._chat("Kontakt P", ["Eike", "Kontakt P"])
        r = self.a.zuordnen_automatisch()
        self.assertIsNone(r["ich"])                      # wer von beiden Eike ist, ist offen
        self.assertEqual(r["zugeordnet"], 0)
        self.assertEqual(r["offen"], 2)
        self.assertEqual(self.a.personen(), [])

    def test_steht_ich_schon_fest_reicht_ein_chat(self):
        ich = self.a.person_anlegen("Eike", ich=True)
        self.a.zuordnen("Eike", ich)
        self._chat("Kontakt P", ["Eike", "Kontakt P"])
        r = self.a.zuordnen_automatisch()
        self.assertEqual((r["neu"], r["offen"]), (1, 0))

    def test_nichts_offen_nichts_zu_tun(self):
        self.assertEqual(self.a.zuordnen_automatisch()["zugeordnet"], 0)


class AutomatischUeberDieSeite(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def test_upload_ordnet_gleich_zu(self):
        ich = ablage.standard().person_anlegen("Eike", ich=True)
        ablage.standard().zuordnen("Eike", ich)
        r = self.c.post("/api/chats/hochladen",
                        files={"datei": ("WhatsApp Chat mit Kontakt P.txt", IPHONE, "text/plain")}).json()
        self.assertEqual(r["zuordnung"]["offen"], 0)
        self.assertEqual(r["zuordnung"]["neu"], 1)                 # Kontakt P angelegt
        self.assertEqual(self.c.get("/api/chats").json()["offen"], [])


class Chatnamen(unittest.TestCase):
    """iPhone-Exporte heißen alle `_chat` — der Name muss aus dem Inhalt kommen."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.a = ablage.standard()
        self.ich = self.a.person_anlegen("Eike", ich=True)
        self.a.zuordnen("Eike", self.ich)

    def tearDown(self):
        self.env.stop()

    def _chat(self, partner, absender):
        cid = self.a.chat_anlegen("_chat.txt", partner)
        self.a.nachrichten_schreiben(cid, [{"zeit": "2026-09-01T10:00:00", "absender": s, "text": "hi"}
                                           for s in absender])
        self.a.chat_abschliessen(cid)
        return cid

    def test_zweier_chat_bekommt_den_namen_des_partners(self):
        cid = self._chat("_chat", ["Eike", "Kontakt P"])
        self.assertEqual(self.a.namen_nachtragen(), 1)
        c = self.a.chat(cid)
        self.assertEqual(c["partner"], "Kontakt P")
        self.assertEqual(c["gruppe"], 0)

    def test_gruppe_wird_als_gruppe_benannt(self):
        cid = self._chat("_chat", ["Eike", "Kontakt A 🏀", "Mia", "Jan"])
        self.a.namen_nachtragen()
        c = self.a.chat(cid)
        self.assertTrue(c["partner"].startswith("Gruppe: "))
        self.assertIn("Kontakt A", c["partner"])
        self.assertEqual(c["gruppe"], 1)

    def test_vorhandene_namen_bleiben(self):
        cid = self._chat("Oma", ["Eike", "Oma"])
        self.assertEqual(self.a.namen_nachtragen(), 0)
        self.assertEqual(self.a.chat(cid)["partner"], "Oma")

    def test_ohne_ich_person_wird_nicht_geraten(self):
        leer = ablage.Ablage(Path(tempfile.mkdtemp()))
        cid = leer.chat_anlegen("_chat.txt", "_chat")
        leer.nachrichten_schreiben(cid, [{"zeit": "2026-09-01T10:00:00", "absender": s, "text": "hi"}
                                         for s in ("A", "B")])
        leer.chat_abschliessen(cid)
        self.assertEqual(leer.namen_nachtragen(), 0)
        self.assertEqual(leer.chat(cid)["partner"], "_chat")
