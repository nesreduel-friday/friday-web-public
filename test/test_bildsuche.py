"""Friday zeigt Bilder: aus dem Gedächtnis oder aus dem Netz (Wikimedia Commons, Openverse) — ohne Netz getestet."""

import asyncio
import base64
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern.dashboard import friday_karten
from kern.dienste import bildsuche
from kern.gedaechtnis import bilder
from kern.main import app
from kern.werkzeuge import bilder as bildwerkzeug
from kern.werkzeuge import katalog

JPG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _commons(seiten):
    return {"query": {"pages": {str(i): s for i, s in enumerate(seiten, 1)}}}


def _seite(titel="File:Eiffelturm bei Nacht.jpg", index=1, mime="image/jpeg", breite=1200, url="https://upload.wikimedia.org/x/thumb.jpg"):
    return {"title": titel, "index": index, "imageinfo": [{
        "thumburl": url, "url": "https://upload.wikimedia.org/x/orig.jpg", "mime": mime, "width": breite, "height": 800,
        "descriptionurl": "https://commons.wikimedia.org/wiki/" + titel.replace(" ", "_"),
        "extmetadata": {"Artist": {"value": '<a href="//x">Anna <b>Muster</b></a>'}, "LicenseShortName": {"value": "CC BY-SA 4.0"}}}]}


def _openverse(ergebnisse):
    return {"results": ergebnisse}


class Suche(unittest.TestCase):
    def _mit(self, commons=None, openverse=None):
        async def abruf(url, fragen=None):
            antwort = commons if "wikimedia" in url else openverse
            if not isinstance(antwort, dict):                   # wie der echte Abruf: keine JSON-Antwort ist ein Fehler
                raise bildsuche.BildFehler("unerwartete Antwort")
            return antwort
        return mock.patch("kern.dienste.bildsuche._json", abruf)

    def test_commons_treffer_sauber(self):
        with self._mit(commons=_commons([_seite()])):
            t = asyncio.run(bildsuche.suchen("Eiffelturm", 3))
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["titel"], t[0]["urheber"], t[0]["lizenz"], t[0]["quelle"]),
                         ("Eiffelturm bei Nacht", "Anna Muster", "CC BY-SA 4.0", "Wikimedia Commons"))
        self.assertTrue(t[0]["link"].startswith("https://commons.wikimedia.org/"))

    def test_nach_suchrang_sortiert_und_unbrauchbares_aussortiert(self):
        seiten = [_seite("File:B.jpg", index=2), _seite("File:A.jpg", index=1), _seite("File:Logo.svg", index=3, mime="image/svg+xml"),
                  _seite("File:Winzig.jpg", index=4, breite=100)]
        with self._mit(commons=_commons(seiten)):
            t = asyncio.run(bildsuche.suchen("x", 5))
        self.assertEqual([x["titel"] for x in t], ["A", "B"])

    def test_anzahl_ist_begrenzt(self):
        seiten = [_seite(f"File:{i}.jpg", index=i) for i in range(1, 9)]
        with self._mit(commons=_commons(seiten)):
            self.assertEqual(len(asyncio.run(bildsuche.suchen("x", 99))), bildsuche.MAX_BILDER)
            self.assertEqual(len(asyncio.run(bildsuche.suchen("x", 0))), 1)

    def test_openverse_wenn_commons_nichts_hat(self):
        ov = [{"title": "Ein Kater", "thumbnail": "https://api.openverse.org/v1/images/1/thumb/", "url": "https://x.org/a.jpg",
               "creator": "Bo", "license": "by", "license_version": "4.0", "foreign_landing_url": "https://x.org/a",
               "filetype": "jpg", "width": 900}]
        with self._mit(commons=_commons([]), openverse=_openverse(ov)):
            t = asyncio.run(bildsuche.suchen("Kater", 2))
        self.assertEqual((t[0]["quelle"], t[0]["lizenz"], t[0]["urheber"]), ("Openverse", "CC BY 4.0", "Bo"))

    def test_nichts_gefunden_und_ausfall_sind_leer_nicht_kaputt(self):
        with self._mit(commons=_commons([]), openverse=_openverse([])):
            self.assertEqual(asyncio.run(bildsuche.suchen("qwertzuiop", 2)), [])
        async def kaputt(url, fragen=None):
            raise bildsuche.BildFehler("aus")
        with mock.patch("kern.dienste.bildsuche._json", kaputt):
            self.assertEqual(asyncio.run(bildsuche.suchen("x", 2)), [])

    def test_leerer_begriff(self):
        self.assertEqual(asyncio.run(bildsuche.suchen("   ", 2)), [])

    def test_nur_https_und_kein_svg_aus_fremden_antworten(self):
        seiten = [_seite(url="http://upload.wikimedia.org/unsicher.jpg")]
        with self._mit(commons=_commons(seiten)):
            self.assertEqual(asyncio.run(bildsuche.suchen("x", 2)), [])


class Holen(unittest.TestCase):
    def setUp(self):
        bildsuche._cache.clear()

    def _abruf(self, daten, mime):
        return mock.patch("kern.dienste.bildsuche._abruf", mock.AsyncMock(return_value=(daten, mime)))

    def test_holen_legt_ab_und_gibt_eine_kennung(self):
        with self._abruf(JPG, "image/jpeg"):
            kennung = asyncio.run(bildsuche.holen("https://upload.wikimedia.org/a.jpg"))
        self.assertRegex(kennung, r"^[0-9a-f]{16}$")
        self.assertEqual(bildsuche.bild(kennung), (JPG, "image/jpeg"))

    def test_gleiche_adresse_gleiche_kennung_und_kein_zweiter_abruf(self):
        with self._abruf(JPG, "image/jpeg") as m:
            a = asyncio.run(bildsuche.holen("https://x.org/a.jpg"))
            b = asyncio.run(bildsuche.holen("https://x.org/a.jpg"))
        self.assertEqual(a, b)
        self.assertEqual(m.await_count, 1)

    def test_kein_bild_oder_zu_gross_wird_verworfen(self):
        for daten, mime in ((b"<html>", "text/html"), (b"<svg/>", "image/svg+xml"), (JPG, ""),
                            (b"x" * (bildsuche.MAX_BYTES + 1), "image/jpeg")):
            with self.subTest(mime), self._abruf(daten, mime):
                self.assertIsNone(asyncio.run(bildsuche.holen("https://x.org/" + mime.replace("/", "") + ".jpg")))

    def test_der_zwischenspeicher_ist_begrenzt(self):
        with self._abruf(JPG, "image/jpeg"):
            for i in range(bildsuche.CACHE_MAX + 5):
                asyncio.run(bildsuche.holen(f"https://x.org/{i}.jpg"))
        self.assertEqual(len(bildsuche._cache), bildsuche.CACHE_MAX)

    def test_unbekannte_kennung(self):
        for k in ("nope", "0" * 16, "../x", ""):
            self.assertIsNone(bildsuche.bild(k))

    def test_nicht_oeffentliche_adressen_werden_nicht_geholt(self):
        with mock.patch("kern.dienste.web.pruefen", mock.AsyncMock(side_effect=bildsuche.web.NichtLesbar("intern"))):
            self.assertIsNone(asyncio.run(bildsuche.holen("https://192.168.1.1/a.jpg")))


class Werkzeug(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        bildsuche._cache.clear()

    def tearDown(self):
        self.env.stop()

    def _treffer(self, n=2):
        return [{"titel": f"Turm {i}", "urheber": "Anna", "lizenz": "CC BY 4.0", "quelle": "Wikimedia Commons",
                 "link": "https://commons.wikimedia.org/wiki/Turm", "bild": f"https://upload.wikimedia.org/{i}.jpg"} for i in range(n)]

    def test_internet_zeigt_und_meldet_die_bilder_an_die_app(self):
        aktionen = []
        with mock.patch("kern.dienste.bildsuche.kandidaten", mock.AsyncMock(return_value=self._treffer())), \
             mock.patch("kern.dienste.bildsuche.pruefen", mock.AsyncMock(return_value=None)), \
             mock.patch("kern.dienste.bildsuche.holen", mock.AsyncMock(side_effect=["a" * 16, "b" * 16])):
            text = asyncio.run(bildwerkzeug.bild_zeigen("Eiffelturm", "internet", 2, aktionen=aktionen))
        self.assertEqual(aktionen[0]["typ"], "bild")
        self.assertEqual([b["url"] for b in aktionen[0]["bilder"]], ["/api/netzbild/" + "a" * 16, "/api/netzbild/" + "b" * 16])
        self.assertEqual(aktionen[0]["bilder"][0]["quelle"], "Wikimedia Commons")
        self.assertIn("Turm 0", text)
        self.assertIn("Internet", text)                         # daran erkennt der Kern fremden Text

    def test_internet_ohne_treffer(self):
        aktionen = []
        with mock.patch("kern.dienste.bildsuche.kandidaten", mock.AsyncMock(return_value=[])):
            text = asyncio.run(bildwerkzeug.bild_zeigen("qwertz", "internet", aktionen=aktionen))
        self.assertEqual(aktionen, [])
        self.assertIn("kein", text.lower())

    def test_internet_ein_bild_das_nicht_laedt_wird_uebersprungen(self):
        aktionen = []
        with mock.patch("kern.dienste.bildsuche.kandidaten", mock.AsyncMock(return_value=self._treffer())), \
             mock.patch("kern.dienste.bildsuche.pruefen", mock.AsyncMock(return_value=None)), \
             mock.patch("kern.dienste.bildsuche.holen", mock.AsyncMock(side_effect=[None, "b" * 16])):
            asyncio.run(bildwerkzeug.bild_zeigen("x", "internet", 2, aktionen=aktionen))
        self.assertEqual(len(aktionen[0]["bilder"]), 1)

    def test_gedaechtnis_findet_nach_namen_und_beschreibung(self):
        b1 = bilder.speichern(None, "image/png", base64.b64encode(PNG).decode(), "Wer ist das?")
        bilder.benennen(b1, "Kontakt Q")
        b2 = bilder.speichern(None, "image/png", base64.b64encode(PNG).decode(), "")
        bilder.beschreiben(b2, "Ein roter Kater auf dem Sofa")
        aktionen = []
        text = asyncio.run(bildwerkzeug.bild_zeigen("Kontakt Q", "gedaechtnis", aktionen=aktionen))
        self.assertEqual(aktionen[0]["bilder"][0]["url"], f"/api/bilder/{b1}")
        self.assertEqual(aktionen[0]["bilder"][0]["titel"], "Kontakt Q")
        self.assertNotIn("Internet", text)
        aktionen = []
        asyncio.run(bildwerkzeug.bild_zeigen("kater", "gedaechtnis", aktionen=aktionen))
        self.assertEqual(aktionen[0]["bilder"][0]["url"], f"/api/bilder/{b2}")

    def test_gedaechtnis_ohne_treffer_sagt_es_ehrlich(self):
        aktionen = []
        text = asyncio.run(bildwerkzeug.bild_zeigen("Niemand", "gedaechtnis", aktionen=aktionen))
        self.assertEqual(aktionen, [])
        self.assertIn("kein Bild", text)

    def test_falsche_quelle_und_leerer_begriff(self):
        self.assertIn("gedaechtnis", asyncio.run(bildwerkzeug.bild_zeigen("x", "irgendwo")))
        self.assertIn("Was", asyncio.run(bildwerkzeug.bild_zeigen("  ", "internet")))

    def test_ist_im_katalog_und_fremd_nur_bei_internet(self):
        namen = [e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]]
        self.assertIn("bild_zeigen", namen)
        self.assertTrue(katalog.liefert_fremdes("bild_zeigen", {"ergebnis": "Gezeigt (Quelle: Internet): Turm"}))
        self.assertFalse(katalog.liefert_fremdes("bild_zeigen", {"ergebnis": "Gezeigt (aus dem Gedächtnis): Kontakt Q"}))


class Pruefung(unittest.TestCase):
    """Gespräch 475: Affenfüße, eine Statue und ein Vogelbuch statt menschlicher Füße — Friday zeigte, ohne zu sehen."""
    def setUp(self):
        bildsuche._gezeigt.clear()

    def _t(self, n):
        return [{"titel": f"T{i}", "urheber": "", "lizenz": "CC0", "quelle": "Commons", "link": "",
                 "bild": f"https://upload.wikimedia.org/{i}.jpg"} for i in range(n)]

    def test_nur_passende_werden_gezeigt_und_beschrieben(self):
        urteil = {"a" * 16: {"passt": False, "qualitaet": 2, "was": "Ein Affe"},
                  "b" * 16: {"passt": True, "qualitaet": 9, "was": "Nackte menschliche Füße im Sand"}}
        aktionen = []
        with mock.patch("kern.dienste.bildsuche.kandidaten", mock.AsyncMock(return_value=self._t(2))), \
             mock.patch("kern.dienste.bildsuche.holen", mock.AsyncMock(side_effect=["a" * 16, "b" * 16])), \
             mock.patch("kern.dienste.bildsuche.pruefen", mock.AsyncMock(return_value=urteil)):
            text = asyncio.run(bildwerkzeug.bild_zeigen("human feet", "internet", 1, aktionen=aktionen))
        self.assertEqual([b["url"] for b in aktionen[0]["bilder"]], ["/api/netzbild/" + "b" * 16])
        self.assertIn("Nackte menschliche Füße im Sand", text)
        self.assertNotIn("Affe", text)

    def test_kein_treffer_sagt_was_auf_den_kandidaten_war(self):
        urteil = {"a" * 16: {"passt": False, "qualitaet": 1, "was": "Eine Statue"}}
        aktionen = []
        with mock.patch("kern.dienste.bildsuche.kandidaten", mock.AsyncMock(return_value=self._t(1))), \
             mock.patch("kern.dienste.bildsuche.holen", mock.AsyncMock(return_value="a" * 16)), \
             mock.patch("kern.dienste.bildsuche.pruefen", mock.AsyncMock(return_value=urteil)):
            text = asyncio.run(bildwerkzeug.bild_zeigen("human feet", "internet", aktionen=aktionen))
        self.assertEqual(aktionen, [])
        self.assertIn("Eine Statue", text)
        self.assertIn("keins zeigt es wirklich", text)

    def test_ohne_pruefung_wird_nichts_beschrieben(self):
        with mock.patch("kern.dienste.bildsuche.kandidaten", mock.AsyncMock(return_value=self._t(1))), \
             mock.patch("kern.dienste.bildsuche.holen", mock.AsyncMock(return_value="a" * 16)), \
             mock.patch("kern.dienste.bildsuche.pruefen", mock.AsyncMock(return_value=None)):
            text = asyncio.run(bildwerkzeug.bild_zeigen("x", "internet", aktionen=[]))
        self.assertIn("NICHT angesehen", text)

    def test_schon_gezeigtes_kommt_nicht_wieder(self):
        a = self._t(3)
        async def commons(b):
            return a
        async def leer(b):
            return []
        bildsuche.gezeigt_merken(a[0]["bild"])
        with mock.patch.object(bildsuche, "_commons", commons), mock.patch.object(bildsuche, "_openverse", leer):
            t = asyncio.run(bildsuche.kandidaten("x"))
        self.assertEqual([k["titel"] for k in t], ["T1", "T2"])

    def test_beide_quellen_gemischt_und_einfachere_variante(self):
        aufrufe = []
        async def commons(b):
            aufrufe.append(("c", b))
            return [{"titel": "C", "bild": "https://c/1.jpg"}] if b == "toenails" else []
        async def openverse(b):
            aufrufe.append(("o", b))
            return [{"titel": "O", "bild": "https://o/1.jpg"}] if b == "toenails" else []
        with mock.patch.object(bildsuche, "_commons", commons), mock.patch.object(bildsuche, "_openverse", openverse):
            t = asyncio.run(bildsuche.kandidaten("rainbow painted toenails"))
        self.assertEqual([k["titel"] for k in t], ["O", "C"])           # abwechselnd, Openverse zuerst
        self.assertIn(("c", "painted toenails"), aufrufe)               # erst die einfacheren Formen versucht


class Route(unittest.TestCase):
    def setUp(self):
        bildsuche._cache.clear()
        self.client = TestClient(app)

    def test_kopie_wird_mit_richtigem_typ_ausgeliefert(self):
        bildsuche._cache["0123456789abcdef"] = (JPG, "image/jpeg")
        r = self.client.get("/api/netzbild/0123456789abcdef")
        self.assertEqual((r.status_code, r.headers["content-type"], r.content), (200, "image/jpeg", JPG))
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")

    def test_unbekannt_und_schlechte_kennungen(self):
        for k in ("0123456789abcdef", "nope", "0123456789ABCDEF", "0" * 17):
            self.assertEqual(self.client.get("/api/netzbild/" + k).status_code, 404, k)


class Karten(unittest.TestCase):
    def test_eigene_bilder_gehen_auf_karten_fremde_nicht(self):
        gut = ["/api/netzbild/0123456789abcdef", "/api/bilder/" + "a" * 32]
        for u in gut:
            self.assertEqual(asyncio.run(friday_karten._bild_pruefen(u)), u)
        for u in ("/api/bilder/12", "/api/netzbild/xyz", "/api/x/0123456789abcdef", "http://x.org/a.jpg", "javascript:alert(1)"):
            with self.subTest(u), self.assertRaises(friday_karten.KarteFehler):
                asyncio.run(friday_karten._bild_pruefen(u))


class Bildkarte(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.client = TestClient(app)
        self.url = "/api/netzbild/0123456789abcdef"

    def tearDown(self):
        self.env.stop()

    def test_karte_mit_bild_und_quelle(self):
        k = asyncio.run(friday_karten.legen_bild(self.url, "Eiffelturm", "Wikimedia Commons", "CC BY 4.0", "Anna",
                                                 "https://commons.wikimedia.org/wiki/x"))
        self.assertEqual((k["art"], k["bild"], k["titel"]), ("bild", self.url, "Eiffelturm"))
        offen = asyncio.run(friday_karten.karten())[0]
        self.assertEqual((offen["art"], offen["bild"], offen["quelle"], offen["lizenz"], offen["urheber"]),
                         ("bild", self.url, "Wikimedia Commons", "CC BY 4.0", "Anna"))
        self.assertEqual(offen["link"], "https://commons.wikimedia.org/wiki/x")

    def test_nur_fridays_eigene_bilder_und_nur_https_links(self):
        for u in ("https://fremd.example/a.jpg", "http://x.org/a.jpg", "/api/netzbild/xyz", "", "javascript:alert(1)"):
            with self.subTest(u), self.assertRaises(friday_karten.KarteFehler):
                asyncio.run(friday_karten.legen_bild(u, "x"))
        k = asyncio.run(friday_karten.legen_bild(self.url, "x", link="javascript:alert(1)"))
        self.assertEqual(asyncio.run(friday_karten.karten())[0]["link"], "")
        self.assertEqual(k["art"], "bild")

    def test_ohne_titel_geht_nicht_und_lebensdauer_ist_begrenzt(self):
        with self.assertRaises(friday_karten.KarteFehler):
            asyncio.run(friday_karten.legen_bild(self.url, "  "))
        k = asyncio.run(friday_karten.legen_bild(self.url, "x", minuten=99999))
        self.assertLessEqual((__import__("datetime").datetime.fromisoformat(k["bis"]) - __import__("datetime").datetime.fromisoformat(k["zeit"])).total_seconds(), friday_karten.MINUTEN_MAX * 60 + 1)

    def test_werkzeug_legt_auf_wunsch_eine_karte(self):
        treffer = [{"titel": "Turm", "urheber": "Anna", "lizenz": "CC BY 4.0", "quelle": "Wikimedia Commons",
                    "link": "https://commons.wikimedia.org/wiki/Turm", "bild": "https://upload.wikimedia.org/1.jpg"}]
        aktionen = []
        with mock.patch("kern.dienste.bildsuche.kandidaten", mock.AsyncMock(return_value=treffer)), \
             mock.patch("kern.dienste.bildsuche.pruefen", mock.AsyncMock(return_value=None)), \
             mock.patch("kern.dienste.bildsuche.holen", mock.AsyncMock(return_value="a" * 16)):
            text = asyncio.run(bildwerkzeug.bild_zeigen("Eiffelturm", "internet", 1, auf_uebersicht=True, minuten=30, aktionen=aktionen))
        k = asyncio.run(friday_karten.karten())
        self.assertEqual((len(k), k[0]["art"], k[0]["bild"], k[0]["titel"]), (1, "bild", "/api/netzbild/" + "a" * 16, "Turm"))
        self.assertIn("Übersicht", text)
        self.assertEqual(len(aktionen[0]["bilder"]), 1)                 # und im Gespräch steht es trotzdem

    def test_werkzeug_ohne_wunsch_legt_keine_karte(self):
        treffer = [{"titel": "Turm", "urheber": "", "lizenz": "", "quelle": "Commons", "link": "", "bild": "https://upload.wikimedia.org/1.jpg"}]
        with mock.patch("kern.dienste.bildsuche.kandidaten", mock.AsyncMock(return_value=treffer)), \
             mock.patch("kern.dienste.bildsuche.pruefen", mock.AsyncMock(return_value=None)), \
             mock.patch("kern.dienste.bildsuche.holen", mock.AsyncMock(return_value="a" * 16)):
            asyncio.run(bildwerkzeug.bild_zeigen("Eiffelturm", aktionen=[]))
        self.assertEqual(asyncio.run(friday_karten.karten()), [])

    def test_route_vom_knopf_im_gespraech(self):
        r = self.client.post("/api/dashboard/bild", json={"url": self.url, "titel": "Turm", "quelle": "Commons", "lizenz": "CC0"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(asyncio.run(friday_karten.karten())[0]["titel"], "Turm")
        for schlecht in ({"url": "https://fremd.example/a.jpg", "titel": "x"}, {"url": self.url, "titel": ""}, {"titel": "x"}):
            with self.subTest(schlecht):
                self.assertIn(self.client.post("/api/dashboard/bild", json=schlecht).status_code, (400, 422))


class Nachgebessert(unittest.TestCase):
    """Fehler aus den Gesprächen vom 25.09.2026 (21:04–21:13)."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        bildsuche._cache.clear()
        bildsuche._meta.clear()

    def tearDown(self):
        self.env.stop()

    def test_quelle_mit_umlaut_oder_anderer_schreibweise_wird_verstanden(self):
        """Gespräch 301: „Zeig das Bild aus deinem Gedächtnis“ → 'Die Quelle muss gedaechtnis oder internet sein.'"""
        b = bilder.speichern(None, "image/png", base64.b64encode(PNG).decode(), "")
        bilder.benennen(b, "Kontakt Q")
        for q in ("gedaechtnis", "Gedächtnis", "GEDÄCHTNIS", "aus dem Gedächtnis", "gedachtnis", "erinnerung"):
            with self.subTest(q):
                aktionen = []
                asyncio.run(bildwerkzeug.bild_zeigen("Kontakt Q", q, aktionen=aktionen))
                self.assertEqual(aktionen[0]["bilder"][0]["url"], f"/api/bilder/{b}")
        for q in ("internet", "Internet", "im Netz", "web"):
            self.assertEqual(bildwerkzeug._quelle(q), "internet")
        self.assertIsNone(bildwerkzeug._quelle("irgendwo"))

    def test_beschreibung_verbietet_das_netz_fuer_bekannte_personen(self):
        beschreibung = bildwerkzeug.ERKLAERUNGEN[0]["description"]
        self.assertIn("Freunde", beschreibung)
        self.assertIn("gedaechtnis", beschreibung)

    def test_gedaechtnissuche_findet_ueber_einzelne_woerter(self):
        """Gespräch 299: „Kontakt Bs Hund“ fand nichts, das Bild hieß „Mila (Hund Kontakt H)“."""
        b = bilder.speichern(None, "image/png", base64.b64encode(PNG).decode(), "")
        bilder.benennen(b, "Mila (Hund Kontakt H)")
        andere = bilder.speichern(None, "image/png", base64.b64encode(PNG).decode(), "")
        bilder.benennen(andere, "Nora")
        self.assertEqual([x["id"] for x in bilder.suchen("Kontakt Bs Hund")], [b])
        self.assertEqual([x["id"] for x in bilder.suchen("Kontakt H Mila")], [b])
        self.assertEqual(bilder.suchen("Katze auf dem Dach"), [])          # kein einziges Wort passt
        self.assertEqual(bilder.suchen("die der und"), [])                # Füllwörter zählen nicht

    def test_ganzer_name_geht_vor_einzelnen_woertern(self):
        a = bilder.speichern(None, "image/png", base64.b64encode(PNG).decode(), "")
        bilder.benennen(a, "Kontakt Q")
        b = bilder.speichern(None, "image/png", base64.b64encode(PNG).decode(), "")
        bilder.benennen(b, "Kontakt Q und Eike")
        self.assertEqual({x["id"] for x in bilder.suchen("Kontakt Q")}, {a, b})

    def test_karte_mit_eigenem_bild_wird_zur_bildkarte_mit_quelle(self):
        """Gespräch 299: „Kannst du es mir als Karte zeigen?“ — die Karte lag OHNE Bild da."""
        kennung = "0123456789abcdef"
        bildsuche._cache[kennung] = (JPG, "image/jpeg")
        bildsuche.merken(kennung, {"titel": "Maine Coon", "quelle": "Wikimedia Commons", "lizenz": "CC BY-SA 3.0",
                                   "urheber": "Anna", "link": "https://commons.wikimedia.org/wiki/x"})
        k = asyncio.run(friday_karten.legen("Maine Coon Katze", "Ein Kater", "/api/netzbild/" + kennung, 20))
        self.assertEqual((k["art"], k["bild"], k["titel"]), ("bild", "/api/netzbild/" + kennung, "Maine Coon Katze"))
        offen = asyncio.run(friday_karten.karten())[0]
        self.assertEqual((offen["quelle"], offen["lizenz"], offen["text"]), ("Wikimedia Commons", "CC BY-SA 3.0", "Ein Kater"))

    def test_karte_mit_gedaechtnisbild_und_ohne_bild_bleibt_wie_sie_war(self):
        k = asyncio.run(friday_karten.legen("Mila", "", "/api/bilder/" + "a" * 32))
        self.assertEqual((k["art"], k["bild"]), ("bild", "/api/bilder/" + "a" * 32))
        offen = asyncio.run(friday_karten.karten())[0]
        self.assertEqual(offen["quelle"], "Gedächtnis")
        k2 = asyncio.run(friday_karten.legen("Nur Text", "hallo"))
        self.assertEqual(k2["art"], "friday")

    def test_das_werkzeug_nennt_die_adresse_fuer_karten(self):
        treffer = [{"titel": "Turm", "urheber": "Anna", "lizenz": "CC BY 4.0", "quelle": "Wikimedia Commons",
                    "link": "https://commons.wikimedia.org/wiki/Turm", "bild": "https://upload.wikimedia.org/1.jpg"}]
        with mock.patch("kern.dienste.bildsuche.kandidaten", mock.AsyncMock(return_value=treffer)), \
             mock.patch("kern.dienste.bildsuche.pruefen", mock.AsyncMock(return_value=None)), \
             mock.patch("kern.dienste.bildsuche.holen", mock.AsyncMock(return_value="a" * 16)):
            text = asyncio.run(bildwerkzeug.bild_zeigen("Eiffelturm", aktionen=[]))
        self.assertIn("/api/netzbild/" + "a" * 16, text)
        self.assertEqual(bildsuche.meta("a" * 16)["quelle"], "Wikimedia Commons")

    def test_meta_ist_wie_der_zwischenspeicher_begrenzt(self):
        for i in range(bildsuche.CACHE_MAX + 5):
            bildsuche.merken(f"{i:016x}", {"titel": "x"})
        self.assertEqual(len(bildsuche._meta), bildsuche.CACHE_MAX)
        self.assertIsNone(bildsuche.meta("nope"))


class Gedaechtnissuche(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_suchen_ist_nicht_case_sensitiv_und_nimmt_keine_platzhalter_vom_modell(self):
        b = bilder.speichern(None, "image/png", base64.b64encode(PNG).decode(), "")
        bilder.benennen(b, "Oma Erna")
        self.assertEqual([x["id"] for x in bilder.suchen("erna")], [b])
        self.assertEqual(bilder.suchen("%"), [])                  # ein Prozentzeichen ist kein Joker
        self.assertEqual(bilder.suchen(""), [])


if __name__ == "__main__":
    unittest.main()
