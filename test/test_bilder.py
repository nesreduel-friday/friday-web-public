"""Bilder: speichern, benennen, zeigen — und nie außerhalb ihres Ordners."""

import asyncio
import base64
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern.dashboard import friday_karten
from kern.gedaechtnis import bilder
from kern.main import app
from kern.protokoll import speicher as pspeicher
from kern.werkzeuge import katalog

PNG = base64.b64encode(open("web/icons/icon-192.png", "rb").read()).decode()


class Bilder(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "AIzaTEST"})
        self.env.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def test_speichern_benennen_zeigen(self):
        g = pspeicher.standard().gespraech_beginnen("chat")
        b = bilder.speichern(g, "image/png", PNG, "Wer ist das?")
        self.assertTrue(bilder.pfad(b).exists())
        self.assertTrue(bilder.benennen(b, "Kontakt Q"))
        self.assertEqual(bilder.mit_namen("Kontakt Q")[0]["id"], b)
        r = self.c.get(f"/api/bilder/{b}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/png")

    @staticmethod
    def _muster(breite=300, hoehe=500, format="PNG", qualitaet=None):
        """Ein Bild mit Struktur (Kreis auf Verlauf), in beliebiger Größe/Kodierung."""
        from io import BytesIO

        from PIL import Image, ImageDraw

        b = Image.new("RGB", (600, 1000))
        d = ImageDraw.Draw(b)
        for y in range(1000):
            d.line([(0, y), (600, y)], fill=(y // 5 % 256, 40, 200 - y // 6))
        d.ellipse((100, 200, 450, 700), fill=(250, 250, 40))
        d.rectangle((300, 600, 560, 900), fill=(10, 10, 10))
        aus = BytesIO()
        b.resize((breite, hoehe)).save(aus, format, **({"quality": qualitaet} if qualitaet else {}))
        return aus.getvalue()

    def test_phash_erkennt_dasselbe_bild_skaliert_und_neu_kodiert(self):
        original = self._muster(600, 1000)
        for anders in (self._muster(378, 672), self._muster(200, 333, "JPEG", 60)):
            self.assertLessEqual(bin(int(bilder.phash(original), 16) ^ int(bilder.phash(anders), 16)).count("1"),
                                 bilder.PHASH_SCHWELLE)

    def test_gleiches_findet_das_alte_bild_und_speichern_verweist_darauf(self):
        alt = bilder.speichern(None, "image/png", base64.b64encode(self._muster(600, 1000)).decode())
        bilder.benennen(alt, "Calvin-Zyklus")
        neu_roh = self._muster(378, 672, "JPEG", 70)
        self.assertEqual(bilder.gleiches(neu_roh)["id"], alt)
        neu = bilder.speichern(None, "image/jpeg", base64.b64encode(neu_roh).decode())
        self.assertIn(alt, bilder.eintrag(neu)["notizen"])  # der Verweis steht auf dem neuen Eintrag
        self.assertNotEqual(neu, alt)

    def test_fremdes_und_einfarbiges_bild_gilt_nicht_als_dasselbe(self):
        from io import BytesIO

        from PIL import Image

        bilder.speichern(None, "image/png", base64.b64encode(self._muster()).decode())
        aus = BytesIO()
        Image.new("RGB", (100, 100), (0, 0, 0)).save(aus, "PNG")
        self.assertIsNone(bilder.phash(aus.getvalue()))  # einfarbig: kein Fingerabdruck
        self.assertIsNone(bilder.gleiches(aus.getvalue()))
        quer = Image.new("RGB", (600, 1000))
        quer.putdata([(x % 256, 0, 0) for y in range(1000) for x in range(600)])
        aus = BytesIO()
        quer.save(aus, "PNG")
        self.assertIsNone(bilder.gleiches(aus.getvalue()))

    def test_kein_ausbruch_aus_dem_ordner(self):
        for boese in ["../../friday.db", "..%2F..%2Fetc%2Fpasswd", "abc", "0" * 31]:
            self.assertIsNone(bilder.pfad(boese), boese)
        self.assertEqual(self.c.get("/api/bilder/..%2F..%2Fetc%2Fpasswd").status_code, 404)

    def test_kaputtes_oder_falsches_bild_abgelehnt(self):
        self.assertIsNone(bilder.speichern(1, "image/gif", PNG))
        self.assertIsNone(bilder.speichern(1, "image/png", "kein base64!!"))

    # ---- Entfernen (Eikes Wunsch 25.09.2026): nur er, nur in der App, nur dieses eine Bild ----

    def test_entfernen_loescht_datei_und_eintrag_und_nur_dieses_bild(self):
        a = bilder.speichern(None, "image/png", PNG, "a")
        b = bilder.speichern(None, "image/png", PNG, "b")
        bilder.benennen(a, "Kontakt Q")
        datei = bilder.pfad(a)
        self.assertTrue(bilder.entfernen(a))
        self.assertFalse(datei.exists())
        self.assertIsNone(bilder.eintrag(a))
        self.assertEqual(bilder.mit_namen("Kontakt Q"), [])
        self.assertTrue(bilder.pfad(b).exists())                       # das andere bleibt
        self.assertEqual([x["id"] for x in bilder.liste()], [b])

    def test_entfernen_zweimal_und_unbekannt(self):
        a = bilder.speichern(None, "image/png", PNG, "a")
        self.assertTrue(bilder.entfernen(a))
        self.assertFalse(bilder.entfernen(a))
        self.assertFalse(bilder.entfernen("0" * 32))

    def test_entfernen_nimmt_nur_echte_kennungen_und_nie_etwas_ausserhalb(self):
        aussen = os.path.join(os.environ["FRIDAY_DATEN"], "wichtig.txt")
        with open(aussen, "w") as f:
            f.write("bleibt")
        for boese in ("../wichtig", "../../wichtig.txt", "wichtig", "", "a" * 31, "A" * 32, "%", None):
            with self.subTest(boese):
                self.assertFalse(bilder.entfernen(boese))
        self.assertTrue(os.path.exists(aussen))

    def test_entfernen_ohne_datei_raeumt_den_eintrag_trotzdem_weg(self):
        a = bilder.speichern(None, "image/png", PNG, "a")
        bilder.pfad(a).unlink()
        self.assertTrue(bilder.entfernen(a))
        self.assertIsNone(bilder.eintrag(a))

    def test_route_entfernt_und_gibt_dann_404(self):
        a = bilder.speichern(None, "image/png", PNG, "a")
        self.assertEqual(self.c.delete(f"/api/bilder/{a}").status_code, 200)
        self.assertEqual(self.c.get(f"/api/bilder/{a}").status_code, 404)
        self.assertEqual(self.c.delete(f"/api/bilder/{a}").status_code, 404)
        for boese in ("nope", "0" * 31, "A" * 32):
            self.assertEqual(self.c.delete(f"/api/bilder/{boese}").status_code, 404, boese)
        # Ein Pfad mit „..“ wird schon vom Server auf eine andere Adresse normalisiert: nie das Bilder-Routing.
        self.assertIn(self.c.delete("/api/bilder/..%2F..%2Fx").status_code, (404, 405))

    def test_route_lehnt_fremde_herkunft_ab(self):
        a = bilder.speichern(None, "image/png", PNG, "a")
        self.assertEqual(self.c.delete(f"/api/bilder/{a}", headers={"Origin": "https://boese.example"}).status_code, 403)
        self.assertTrue(bilder.pfad(a).exists())

    def test_karten_mit_dem_bild_verschwinden_mit_ihm(self):
        a = bilder.speichern(None, "image/png", PNG, "a")
        b = bilder.speichern(None, "image/png", PNG, "b")
        asyncio.run(friday_karten.legen_bild(f"/api/bilder/{a}", "Kontakt Q"))
        asyncio.run(friday_karten.legen_bild(f"/api/bilder/{b}", "Bob"))
        self.c.delete(f"/api/bilder/{a}")
        self.assertEqual([k["titel"] for k in asyncio.run(friday_karten.karten())], ["Bob"])

    def test_das_modell_hat_kein_werkzeug_zum_entfernen(self):
        for e in katalog.gemini_tools()[0]["functionDeclarations"]:
            self.assertNotRegex(e["name"], r"entfern|loesch|lösch|vergess.*bild|bild.*(weg|delete)", e["name"])

    def test_werkzeug_benennt_das_letzte_bild_im_gespraech(self):
        g = pspeicher.standard().gespraech_beginnen("sprache")
        bilder.speichern(g, "image/png", PNG)
        neu = bilder.speichern(g, "image/png", PNG)
        r = asyncio.run(katalog.ausfuehren("bild_benennen", {"name": "Jale"}, gespraech=g))
        self.assertIn("Jale", r["ergebnis"])
        self.assertEqual(bilder.eintrag(neu)["name"], "Jale")

    def test_quelle_wird_gespeichert_und_alte_datenbanken_werden_ergaenzt(self):
        g = pspeicher.standard().gespraech_beginnen("snapchat")
        b = bilder.speichern(g, "image/png", PNG, "Was ist das?", quelle="Snapchat · Eike")
        self.assertEqual(bilder.eintrag(b)["quelle"], "Snapchat · Eike")
        self.assertEqual(bilder.fuer_gespraech(g)[0]["quelle"], "Snapchat · Eike")
        # Eine Datenbank ohne die neuen Spalten (vor dem 29.09.2026) bekommt sie nachgerüstet.
        import sqlite3
        ordner = tempfile.mkdtemp()
        c = sqlite3.connect(os.path.join(ordner, "friday.db"))
        c.execute("CREATE TABLE bilder (id TEXT PRIMARY KEY, gespraech INTEGER, zeit TEXT NOT NULL, "
                  "mime TEXT NOT NULL, name TEXT, beschreibung TEXT, frage TEXT)")
        c.commit()
        c.close()
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": ordner}):
            b2 = bilder.speichern(None, "image/png", PNG, quelle="Snapchat · Eike")
            self.assertEqual(bilder.eintrag(b2)["quelle"], "Snapchat · Eike")

    def test_fehlersatz_wird_nicht_zur_beschreibung(self):
        b = bilder.speichern(None, "image/png", PNG)
        bilder.beschreiben(b, "Das hat gerade nicht geklappt, Sir.")
        self.assertFalse(bilder.eintrag(b)["beschreibung"])
        bilder.beschreiben(b, "Ein Hund am Strand.")
        self.assertEqual(bilder.eintrag(b)["beschreibung"], "Ein Hund am Strand.")

    def test_ergaenzen_haengt_an_und_laesst_die_beschreibung_in_ruhe(self):
        b = bilder.speichern(None, "image/png", PNG)
        bilder.beschreiben(b, "Ein Hund am Strand.")
        self.assertTrue(bilder.ergaenzen(b, "Das ist Toms Hund"))
        self.assertTrue(bilder.ergaenzen(b, "Aufgenommen im Sommer"))
        e = bilder.eintrag(b)
        self.assertEqual(e["beschreibung"], "Ein Hund am Strand.")
        self.assertEqual(e["notizen"], "Das ist Toms Hund\nAufgenommen im Sommer")
        self.assertFalse(bilder.ergaenzen(b, "  "))
        self.assertFalse(bilder.ergaenzen("0" * 32, "x"))

    def test_werkzeug_haengt_info_ans_letzte_bild(self):
        g = pspeicher.standard().gespraech_beginnen("snapchat")
        b = bilder.speichern(g, "image/png", PNG, quelle="Snapchat · Eike")
        r = asyncio.run(katalog.ausfuehren("bild_benennen", {"info": "war auf der Feier von Kontakt E"}, gespraech=g))
        self.assertIn("ergänzt", r["ergebnis"])
        self.assertEqual(bilder.eintrag(b)["notizen"], "war auf der Feier von Kontakt E")
        self.assertIsNone(bilder.eintrag(b)["name"])

    def test_werkzeug_ohne_name_und_info_fragt_nach(self):
        g = pspeicher.standard().gespraech_beginnen("snapchat")
        bilder.speichern(g, "image/png", PNG)
        r = asyncio.run(katalog.ausfuehren("bild_benennen", {}, gespraech=g))
        self.assertIn("brauche einen Namen", r["ergebnis"])

    def test_werkzeug_ohne_bild_ehrlich(self):
        g = pspeicher.standard().gespraech_beginnen("sprache")
        r = asyncio.run(katalog.ausfuehren("bild_benennen", {"name": "X"}, gespraech=g))
        self.assertIn("noch kein Bild", r["ergebnis"])

    def test_modell_kann_gespraech_nicht_unterschieben(self):
        g1 = pspeicher.standard().gespraech_beginnen("chat")
        fremd = bilder.speichern(g1, "image/png", PNG)
        g2 = pspeicher.standard().gespraech_beginnen("chat")
        asyncio.run(katalog.ausfuehren("bild_benennen", {"name": "Y", "gespraech": g1}, gespraech=g2))
        self.assertIsNone(bilder.eintrag(fremd)["name"])

    def test_chat_speichert_bild_mit_fridays_antwort_als_beschreibung(self):
        async def gemini(inhalt, bevorzugt=None):
            return {"candidates": [{"content": {"parts": [{"text": "Ein blauer Ring, Sir."}]}}]}, "m"
        with mock.patch("kern.sitzung.chat._gemini_anfrage", gemini):
            self.c.post("/api/chat", json={"nachrichten": [{"rolle": "du", "text": "Was ist das?",
                                                            "bild": {"mime": "image/png", "data": PNG}}]})
        b = bilder.liste()[0]
        self.assertEqual(b["beschreibung"], "Ein blauer Ring, Sir.")
        self.assertEqual(b["frage"], "Was ist das?")

    def test_benennen_auf_der_seite_geht_ins_langzeitgedaechtnis(self):
        b = bilder.speichern(None, "image/png", PNG)
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as senden:
            self.assertEqual(self.c.post(f"/api/bilder/{b}/name", json={"name": "Kontakt Q"}).status_code, 200)
        self.assertIn("es zeigt Kontakt Q", senden.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
