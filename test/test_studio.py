"""Studio — der YouTube-Kanal: Endpunkte, Pfadschutz, keine Themenwiederholung."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from kern import konnektoren
from kern.main import app
from kern.studio import planer
from kern.werkzeuge import katalog


class Mit_Ordner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.tmp})
        self.env.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def _fertig(self, kennung="probe", **felder):
        ordner = Path(self.tmp) / "studio" / "fertig"
        ordner.mkdir(parents=True, exist_ok=True)
        d = {"thema": "Why Test", "titel": "Why Test", "status": "fertig",
             "video": f"{kennung}.mp4", "schluessel": {"GEMINI_API_KEY": "GEHEIM"}, **felder}
        (ordner / f"{kennung}.json").write_text(json.dumps(d), encoding="utf-8")
        (ordner / f"{kennung}.mp4").write_bytes(b"nicht wirklich ein Video")
        return d


class InArbeit(Mit_Ordner):
    """Ohne die laufenden Aufträge sähe Eike minutenlang eine leere Seite und
    wüsste nicht, ob überhaupt etwas passiert (sein Wunsch, 14.09.2026)."""

    def _laeuft(self, kennung="laeuft1", **felder):
        ordner = Path(self.tmp) / "studio" / "auftraege"
        ordner.mkdir(parents=True, exist_ok=True)
        d = {"thema": "Why Goats", "titel": "Why Goats", "status": "laeuft",
             "schritt": "sucht Bildmaterial", "fortschritt": 50,
             "schluessel": {"PEXELS_API_KEY": "GEHEIM"}, **felder}
        (ordner / f"{kennung}.json").write_text(json.dumps(d), encoding="utf-8")

    def test_laufender_auftrag_erscheint_mit_fortschritt(self):
        self._laeuft()
        d = self.c.get("/api/studio/wartend").json()
        self.assertEqual(len(d["inArbeit"]), 1)
        self.assertEqual(d["inArbeit"][0]["fortschritt"], 50)
        self.assertEqual(d["inArbeit"][0]["schritt"], "sucht Bildmaterial")

    def test_auch_hier_gehen_keine_schluessel_raus(self):
        self._laeuft()
        self.assertNotIn("GEHEIM", self.c.get("/api/studio/wartend").text)

    def test_halb_geschriebene_datei_stuerzt_nicht_ab(self):
        ordner = Path(self.tmp) / "studio" / "auftraege"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "kaputt.json").write_text('{"thema": "abgeschnitten', encoding="utf-8")
        self._laeuft()
        d = self.c.get("/api/studio/wartend").json()
        self.assertEqual(len(d["inArbeit"]), 1)      # der heile Auftrag bleibt sichtbar

    def test_ohne_alles_beide_listen_leer(self):
        d = self.c.get("/api/studio/wartend").json()
        self.assertEqual((d["videos"], d["inArbeit"]), ([], []))


class Endpunkte(Mit_Ordner):
    def test_wartende_videos_werden_gelistet(self):
        self._fertig("abc")
        d = self.c.get("/api/studio/wartend").json()
        self.assertEqual(len(d["videos"]), 1)
        self.assertEqual(d["videos"][0]["kennung"], "abc")

    def test_schluessel_gehen_nie_an_den_browser(self):
        self._fertig("abc")
        roh = self.c.get("/api/studio/wartend").text
        self.assertNotIn("GEHEIM", roh)
        self.assertNotIn("schluessel", roh)

    def test_arbeiter_legt_keine_schluessel_in_die_ergebnisdatei(self):
        """Der Browser-Test allein genügt nicht: Am 14.09.2026 filterte der
        Endpunkt die Schlüssel heraus, während sie auf der Platte in einer für
        ALLE lesbaren Datei standen. Geprüft wird deshalb, was der ARBEITER
        wirklich ablegt — nicht, was der Weg nach außen durchlässt."""
        import importlib.util
        import stat as dateirechte

        pfad = Path(__file__).resolve().parent.parent / "studio" / "arbeiter.py"
        spec = importlib.util.spec_from_file_location("studio_arbeiter", pfad)
        arbeiter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(arbeiter)

        arbeiter.ORDNER = Path(self.tmp) / "studio"
        arbeiter.AUFTRAEGE = arbeiter.ORDNER / "auftraege"
        arbeiter.FERTIG = arbeiter.ORDNER / "fertig"
        arbeiter.AUFTRAEGE.mkdir(parents=True, exist_ok=True)

        auftrag = arbeiter.AUFTRAEGE / "probe.json"
        # Leeres Thema: Der Auftrag scheitert sofort, es wird nichts gerendert —
        # geschrieben wird die Ergebnisdatei trotzdem, und genau die zählt hier.
        auftrag.write_text(json.dumps({"thema": "", "schluessel": {"PEXELS_API_KEY": "GEHEIM"}}),
                           encoding="utf-8")
        arbeiter._abarbeiten(auftrag)

        ergebnis = arbeiter.FERTIG / "probe.json"
        self.assertTrue(ergebnis.exists(), "der Arbeiter hat kein Ergebnis abgelegt")
        roh = ergebnis.read_text(encoding="utf-8")
        self.assertNotIn("GEHEIM", roh)
        self.assertNotIn("schluessel", roh)
        self.assertEqual(dateirechte.S_IMODE(ergebnis.stat().st_mode), 0o600)

    def test_video_wird_ausgeliefert(self):
        self._fertig("abc")
        r = self.c.get("/api/studio/video/abc")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "video/mp4")

    def test_unbekanntes_video(self):
        self.assertEqual(self.c.get("/api/studio/video/gibtsnicht").status_code, 404)

    def test_kein_ausbruch_aus_dem_ordner(self):
        """Der Name kommt aus der Adresszeile — ohne Filter läge /etc/passwd frei."""
        for boese in ("../../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", "abc/../../geheim"):
            with self.subTest(boese):
                r = self.c.get(f"/api/studio/video/{boese}")
                self.assertIn(r.status_code, (400, 404), boese)


class HochladenKnopf(Mit_Ordner):
    """Eikes Knopf in der Werkstatt — dieselbe Prüfung wie beim Zuruf an Friday
    (studio_hochladen), nur mit Klick statt Wort. 22.09.2026."""

    def _frei(self, schreiben=True):
        konnektoren.setzen("studio", True)
        konnektoren.zugang_setzen("studio", "kennung", "geheim")
        if schreiben:
            konnektoren.schreiben_setzen("studio", True)

    def test_ohne_schreibrecht_abgelehnt(self):
        self._frei(schreiben=False)
        self._fertig("abc")
        r = self.c.post("/api/studio/hochladen", json={"kennung": "abc"})
        self.assertEqual(r.status_code, 403)

    def test_unbekannte_kennung(self):
        self._frei()
        self._fertig("abc")
        r = self.c.post("/api/studio/hochladen", json={"kennung": "gibtsnicht"}).json()
        self.assertFalse(r["ok"])

    def test_erfolgreich_setzt_hochgeladen_und_url(self):
        self._frei()
        self._fertig("abc")
        with mock.patch("kern.main.studio.hochladen",
                        new=mock.AsyncMock(return_value={"url": "https://youtu.be/xyz"})) as hoch:
            r = self.c.post("/api/studio/hochladen", json={"kennung": "abc"}).json()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["url"], "https://youtu.be/xyz")
        hoch.assert_awaited_once()
        gespeichert = json.loads((Path(self.tmp) / "studio" / "fertig" / "abc.json").read_text())
        self.assertTrue(gespeichert["hochgeladen"])
        self.assertEqual(gespeichert["url"], "https://youtu.be/xyz")
        # ein zweites Mal wartet das Video nicht mehr
        r2 = self.c.post("/api/studio/hochladen", json={"kennung": "abc"}).json()
        self.assertFalse(r2["ok"])


class KeineWiederholung(unittest.TestCase):
    """Auf dem Kanal gibt es vier Tauben-Videos, drei über Bienen und zwei über
    Seeotter. Jarvis' Prüfung verglich nur Wortüberlappung und ließ das durch."""

    def test_gleiches_thema_faellt_auf(self):
        self.assertTrue(planer.schon_dagewesen(
            "Why pigeons never crash into windows",
            ["Why pigeons actually remember your face"]))

    def test_gleicher_gegenstand_anderer_blickwinkel_faellt_auch_auf(self):
        self.assertTrue(planer.schon_dagewesen(
            "How octopuses open jars from the inside",
            ["Why octopuses punch fish for no reason"]))

    def test_neues_thema_geht_durch(self):
        self.assertFalse(planer.schon_dagewesen(
            "Why lightning strikes the same tower twice",
            ["Why pigeons bob their heads", "Why sea turtles cry real tears"]))

    def test_leeres_thema_gilt_als_verbraucht(self):
        self.assertTrue(planer.schon_dagewesen("", ["irgendwas"]))


class Freigabe(Mit_Ordner):
    def test_werkzeuge_erst_nach_freigabe(self):
        namen = lambda: {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("studio_stand", namen())
        konnektoren.setzen("studio", True)
        konnektoren.zugang_setzen("studio", "kennung", "geheim")
        self.assertIn("studio_stand", namen())

    def test_hochladen_braucht_zusaetzlich_das_schreibrecht(self):
        konnektoren.setzen("studio", True)
        konnektoren.zugang_setzen("studio", "kennung", "geheim")
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("studio_hochladen", namen)
        konnektoren.schreiben_setzen("studio", True)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertIn("studio_hochladen", namen)


if __name__ == "__main__":
    unittest.main()
