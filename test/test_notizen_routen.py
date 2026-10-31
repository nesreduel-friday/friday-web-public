"""Eikes Angaben zu einem Eintrag: Route, Seite, Rückblick-Takt."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern.gedaechtnis import graph, notizen, rueckblick
from kern.gedaechtnis import speicher as gspeicher
from kern.main import app

U = "11111111-1111-1111-1111-111111111111"


class Routen(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.c = TestClient(app)
        self.g = [mock.patch("kern.gedaechtnis.wissensgraph.eingerichtet", return_value=True),
                  mock.patch("kern.gedaechtnis.wissensgraph._abfrage", mock.AsyncMock(return_value=[["Kontakt Bs Mutter"]]))]
        for p in self.g:
            p.start()

    def tearDown(self):
        for p in self.g:
            p.stop()
        self.env.stop()

    def test_name_wird_notiz_im_notizbuch_und_vorgemerkte_episode(self):
        r = self.c.post(f"/api/gedaechtnis/seite/{U}/notiz", json={"art": "name", "wert": "Kontakt H"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["notiz"]["text"], "Kontakt Bs Mutter heißt Kontakt H.")
        self.assertEqual([f["inhalt"] for f in gspeicher.standard().fakten() if f["quelle"] == "eike"], ["Kontakt Bs Mutter heißt Kontakt H."])
        self.assertEqual(notizen.fuer(U)[0]["status"], "offen")

    def test_angabe_und_freitext(self):
        self.assertEqual(self.c.post(f"/api/gedaechtnis/seite/{U}/notiz", json={"art": "angabe", "label": "Beruf", "wert": "Lehrerin"}).status_code, 200)
        self.assertEqual(self.c.post(f"/api/gedaechtnis/seite/{U}/notiz", json={"art": "frei", "text": "Arbeitet in Musterhausen."}).status_code, 200)
        self.assertEqual([n["text"] for n in notizen.fuer(U)], ["Zu Kontakt Bs Mutter: Arbeitet in Musterhausen.", "Beruf von Kontakt Bs Mutter: Lehrerin."])

    def test_leeres_unbekanntes_und_kaputte_uuid(self):
        self.assertEqual(self.c.post(f"/api/gedaechtnis/seite/{U}/notiz", json={"art": "name", "wert": " "}).status_code, 422)
        self.assertEqual(self.c.post(f"/api/gedaechtnis/seite/{U}/notiz", json={"art": "löschen"}).status_code, 422)
        self.assertEqual(self.c.post("/api/gedaechtnis/seite/kaputt/notiz", json={"art": "name", "wert": "x"}).status_code, 422)
        with mock.patch("kern.gedaechtnis.wissensgraph._abfrage", mock.AsyncMock(return_value=[])):
            self.assertEqual(self.c.post(f"/api/gedaechtnis/seite/{U}/notiz", json={"art": "name", "wert": "x"}).status_code, 404)

    def test_seite_nennt_die_angaben_mit_stand(self):
        notizen.anlegen(U, "Kontakt Bs Mutter", "name", wert="Kontakt H")
        with mock.patch("kern.gedaechtnis.seite.seite", mock.AsyncMock(return_value={"name": "Kontakt Bs Mutter", "gruppen": []})):
            d = self.c.get(f"/api/gedaechtnis/seite/{U}").json()
        self.assertEqual([(n["text"], n["status"]) for n in d["notizen"]], [("Kontakt Bs Mutter heißt Kontakt H.", "offen")])


class Takt(unittest.TestCase):
    def test_der_rueckblick_sendet_eikes_angaben_zuerst(self):
        reihenfolge = []
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()}), \
             mock.patch.object(rueckblick.wissensnotizen, "durchgang", mock.AsyncMock(side_effect=lambda: reihenfolge.append("notizen"))), \
             mock.patch.object(rueckblick, "zusammenfassen", mock.AsyncMock(side_effect=lambda g: reihenfolge.append("zusammenfassen"))), \
             mock.patch.object(rueckblick, "sichern"), mock.patch.object(rueckblick, "_gelegentlich_verdichten", mock.AsyncMock()), \
             mock.patch.object(graph, "pausiert", return_value=False), mock.patch.object(graph, "pause_vorzeitig_beenden", mock.AsyncMock(return_value=False)), \
             mock.patch("kern.protokoll.speicher.Protokoll.offene_rueckblicke", return_value=[1]):
            asyncio.run(rueckblick.durchgang())
        self.assertEqual(reihenfolge[0], "notizen")

    def test_pausierter_graph_sendet_keine_angaben(self):
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()}), \
             mock.patch.object(rueckblick.wissensnotizen, "durchgang", mock.AsyncMock()) as d, \
             mock.patch.object(rueckblick, "zusammenfassen", mock.AsyncMock()), mock.patch.object(rueckblick, "sichern"), \
             mock.patch.object(rueckblick, "_gelegentlich_verdichten", mock.AsyncMock()), \
             mock.patch.object(graph, "pausiert", return_value=True), mock.patch.object(graph, "pause_vorzeitig_beenden", mock.AsyncMock(return_value=False)):
            asyncio.run(rueckblick.durchgang())
        d.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
