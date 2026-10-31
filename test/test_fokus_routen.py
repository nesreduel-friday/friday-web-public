"""Fokus-Personen: Routen."""
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern.fokus import ablage
from kern.main import app

U = "11111111-1111-1111-1111-111111111111"


class Routen(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def _graph(self, zeilen):
        return [mock.patch("kern.gedaechtnis.wissensgraph.eingerichtet", return_value=True),
                mock.patch("kern.gedaechtnis.wissensgraph._abfrage", mock.AsyncMock(return_value=zeilen))]

    def _mit(self, zeilen):
        gs = self._graph(zeilen)
        for g in gs:
            g.start()
        self.addCleanup(lambda: [g.stop() for g in gs])

    def test_markieren_und_entfernen(self):
        self._mit([["Summer Muster", ["Entity", "Person"]]])
        self.assertEqual(self.c.put(f"/api/fokus/{U}", json={"an": True}).json(), {"an": True})
        self.assertEqual([p["name"] for p in ablage.liste()], ["Summer Muster"])
        self.assertEqual(self.c.put(f"/api/fokus/{U}", json={"an": False}).json(), {"an": False})
        self.assertEqual(ablage.liste(), [])

    def test_nur_personen_lassen_sich_markieren(self):
        self._mit([["Musterdorf", ["Entity", "Location"]]])
        self.assertEqual(self.c.put(f"/api/fokus/{U}", json={"an": True}).status_code, 404)
        self.assertEqual(ablage.liste(), [])

    def test_unbekannte_und_kaputte_uuid(self):
        self._mit([])
        self.assertEqual(self.c.put(f"/api/fokus/{U}", json={"an": True}).status_code, 404)
        self.assertEqual(self.c.put("/api/fokus/kaputt", json={"an": True}).status_code, 422)

    def test_entfernen_geht_auch_wenn_der_graph_nicht_antwortet(self):
        ablage.markieren(U, "Summer", True)
        with mock.patch("kern.gedaechtnis.wissensgraph.eingerichtet", return_value=True), \
             mock.patch("kern.gedaechtnis.wissensgraph._abfrage", mock.AsyncMock(side_effect=RuntimeError("weg"))):
            self.assertEqual(self.c.put(f"/api/fokus/{U}", json={"an": False}).json(), {"an": False})
        self.assertEqual(ablage.liste(), [])

    def test_liste_mit_neu_und_quellenstand_juengste_zuerst(self):
        ablage.markieren(U, "Summer Muster", True)
        ablage.markieren("22222222-2222-2222-2222-222222222222", "Nele", True)
        ablage.neu_ablegen("22222222-2222-2222-2222-222222222222", "mail", ["Termin am Montag"])
        with mock.patch("kern.fokus.sammler.whatsapp.finden", return_value=[]), \
             mock.patch("kern.fokus.sammler.konnektoren.aktiv", return_value=False):
            d = self.c.get("/api/fokus").json()
        self.assertEqual([p["name"] for p in d["personen"]], ["Nele", "Summer Muster"])
        self.assertEqual(d["personen"][0]["neu"][0]["text"], "Termin am Montag")
        self.assertEqual(d["personen"][1]["quellen"]["whatsapp"], "WhatsApp: kein freigegebener Chat")

    def test_liste_zeigt_die_beziehung_aus_der_gemerkten_seite(self):
        ablage.markieren(U, "Summer Muster", True)
        with mock.patch("kern.gedaechtnis.seite.gemerkt", return_value={"untertitel": "Freundin und Mitschülerin"}), \
             mock.patch("kern.fokus.sammler.whatsapp.finden", return_value=[]), \
             mock.patch("kern.fokus.sammler.konnektoren.aktiv", return_value=False):
            d = self.c.get("/api/fokus").json()
        self.assertEqual(d["personen"][0]["untertitel"], "Freundin und Mitschülerin")

    def test_zuordnung_nur_fuer_freigegebene_gruppen_und_markierte_personen(self):
        ablage.markieren(U, "Kontakt F Muster", True)
        with mock.patch("kern.main.whatsappdienst.ist_frei", side_effect=lambda cid: cid == "12@g.us"):
            ok = self.c.put(f"/api/fokus/{U}/zuordnung", json={"chat_id": "12@g.us", "absender": "Kontakt F"})
            fremd = self.c.put(f"/api/fokus/{U}/zuordnung", json={"chat_id": "99@g.us", "absender": "Kontakt F"})
            einzel = self.c.put(f"/api/fokus/{U}/zuordnung", json={"chat_id": "12@c.us", "absender": "Kontakt F"})
            niemand = self.c.put("/api/fokus/22222222-2222-2222-2222-222222222222/zuordnung", json={"chat_id": "12@g.us", "absender": "Kontakt F"})
        self.assertEqual((ok.status_code, fremd.status_code, einzel.status_code, niemand.status_code), (200, 422, 422, 404))
        self.assertEqual(ablage.zeiger(U, "whatsapp")["zuordnung"], {"12@g.us": ["Kontakt F"]})

    def test_liste_zeigt_unklare_absender_und_zuordnungen(self):
        ablage.markieren(U, "Kontakt F Muster", True)
        ablage.unklar_setzen(U, [{"chat_id": "12@g.us", "chat": "JG12", "absender": "Kontakt F", "anzahl": 2}])
        ablage.zuordnung_setzen(U, "12@g.us", "Kontakt A", True)
        with mock.patch("kern.main.whatsappdienst.frei", return_value=[{"id": "12@g.us", "name": "JG12"}]), \
             mock.patch("kern.fokus.sammler.whatsapp.finden", return_value=[]), \
             mock.patch("kern.fokus.sammler.konnektoren.aktiv", return_value=False):
            d = self.c.get("/api/fokus").json()["personen"][0]
        self.assertEqual(d["unklar"][0]["absender"], "Kontakt F")
        self.assertEqual(d["zuordnung"], [{"chat_id": "12@g.us", "chat": "JG12", "absender": "Kontakt A"}])

    def test_jetzt_nachsehen_mit_sperre(self):
        ablage.markieren(U, "Summer", True)
        from kern.fokus import sammler
        sammler._zuletzt_jetzt.clear()
        with mock.patch("kern.fokus.sammler.sammle_person", mock.AsyncMock(return_value={"neu": 2, "grund": ""})), \
             mock.patch("kern.fokus.sammler.whatsapp.finden", return_value=[]), \
             mock.patch("kern.fokus.sammler.konnektoren.aktiv", return_value=False):
            erst = self.c.post(f"/api/fokus/{U}/jetzt")
            zweit = self.c.post(f"/api/fokus/{U}/jetzt")
        self.assertEqual((erst.status_code, erst.json()["neu"]), (200, 2))
        self.assertEqual(zweit.status_code, 429)

    def test_jetzt_bei_nicht_markierter_person_404(self):
        self.assertEqual(self.c.post(f"/api/fokus/{U}/jetzt").status_code, 404)


class Start(unittest.TestCase):
    def test_dauerlaeufe_sind_in_main_registriert(self):
        # 05.10.2026: `wissensseite.vorwaermen()` lief nie, weil die Registrierung nicht in main.py stand — still ignoriert.
        import pathlib
        text = (pathlib.Path(__file__).resolve().parent.parent / "kern" / "main.py").read_text(encoding="utf-8")
        for name, aufruf in (("wissensseiten", "wissensseite.vorwaermen()"), ("fokus", "fokussammler.dauerlauf()")):
            self.assertIn(f'_starten("{name}", {aufruf})', text)


if __name__ == "__main__":
    unittest.main()
