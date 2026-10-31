"""Die Gedächtnis-Seite: sehen, merken, vergessen, entscheiden."""

import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern.gedaechtnis import speicher as gspeicher
from kern.main import app
from kern.protokoll import speicher as pspeicher


class GedaechtnisSeite(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def test_themen_route_liefert_die_karten(self):
        karten = {"eingerichtet": True, "erreichbar": True, "themen": [{"name": "Therapie"}]}
        with mock.patch("kern.gedaechtnis.themen.themen", mock.AsyncMock(return_value=karten)):
            r = self.c.get("/api/gedaechtnis/themen")
        self.assertEqual((r.status_code, r.json()["themen"][0]["name"]), (200, "Therapie"))

    def test_antwort_route_reicht_die_frage_weiter_und_kuerzt_sie(self):
        antwort = {"frage": "x", "antwort": "a", "quelle": "modell", "belege": []}
        with mock.patch("kern.gedaechtnis.antwort.antworten", mock.AsyncMock(return_value=antwort)) as m:
            r = self.c.get("/api/gedaechtnis/antwort", params={"frage": "Therapie"})
            self.assertEqual((r.status_code, r.json()["antwort"]), (200, "a"))
            m.assert_awaited_once_with("Therapie")
            self.assertEqual(self.c.get("/api/gedaechtnis/antwort", params={"frage": "x" * 600}).status_code, 422)
        self.assertEqual(self.c.get("/api/gedaechtnis/antwort").status_code, 422)   # ohne Frage

    def test_treffer_route_ist_schnell_und_ohne_modell(self):
        with mock.patch("kern.gedaechtnis.antwort._treffer", mock.AsyncMock(return_value=([{"name": "Therapie-Termin"}], "u1"))) as m:
            r = self.c.get("/api/gedaechtnis/treffer", params={"frage": " Therapie  "})
        self.assertEqual((r.status_code, r.json()["oeffnen"]), (200, "u1"))
        m.assert_awaited_once_with("Therapie")

    def test_seite_route_und_404(self):
        u = "11111111-1111-1111-1111-111111111111"
        with mock.patch("kern.gedaechtnis.seite.seite", mock.AsyncMock(return_value={"name": "Musterdorf", "gruppen": []})):
            r = self.c.get(f"/api/gedaechtnis/seite/{u}")
        self.assertEqual((r.status_code, r.json()["name"]), (200, "Musterdorf"))
        with mock.patch("kern.gedaechtnis.seite.seite", mock.AsyncMock(return_value={"name": "x"})) as m:
            self.c.get(f"/api/gedaechtnis/seite/{u}", params={"modell": 0})
            m.assert_awaited_once_with(u, modell=False)                       # sofort, ohne auf das Modell zu warten
        with mock.patch("kern.gedaechtnis.seite.seite", mock.AsyncMock(return_value=None)):
            self.assertEqual(self.c.get(f"/api/gedaechtnis/seite/{u}").status_code, 404)

    def test_von_hand_merken_und_sehen(self):
        self.assertEqual(self.c.post("/api/gedaechtnis/fakten", json={"thema": "Kaffee", "inhalt": "schwarz"}).status_code, 200)
        d = self.c.get("/api/gedaechtnis").json()
        self.assertEqual(d["fakten"][0]["inhalt"], "schwarz")
        self.assertEqual(d["fakten"][0]["quelle"], "eike")

    def test_vergessen_und_verlauf(self):
        self.c.post("/api/gedaechtnis/fakten", json={"thema": "Farbe", "inhalt": "blau"})
        self.c.post("/api/gedaechtnis/fakten", json={"thema": "Farbe", "inhalt": "grün"})
        self.c.post("/api/gedaechtnis/vergessen", json={"thema": "Farbe"})
        self.assertEqual(self.c.get("/api/gedaechtnis").json()["fakten"], [])
        verlauf = self.c.get("/api/gedaechtnis/historie", params={"thema": "Farbe"}).json()
        self.assertEqual([v["inhalt"] for v in verlauf], ["blau", "grün"])
        self.assertIsNotNone(verlauf[1]["bis"])            # vergessen, aber nachlesbar

    def test_vorschlag_bestaetigen_und_verwerfen(self):
        g = gspeicher.standard()
        ja = g.vorschlagen("Essen", "Pizza", herkunft="Rückblick")
        nein = g.vorschlagen("Hobby", "Stricken", herkunft="Webseite")
        self.assertEqual(len(self.c.get("/api/gedaechtnis").json()["vorschlaege"]), 2)
        self.c.post(f"/api/gedaechtnis/vorschlaege/{ja}", json={"entscheidung": "ja"})
        self.c.post(f"/api/gedaechtnis/vorschlaege/{nein}", json={"entscheidung": "nein"})
        d = self.c.get("/api/gedaechtnis").json()
        self.assertEqual(d["vorschlaege"], [])
        self.assertEqual([f["thema"] for f in d["fakten"]], ["Essen"])
        self.assertEqual(self.c.post(f"/api/gedaechtnis/vorschlaege/{ja}", json={"entscheidung": "ja"}).status_code, 404)

    def test_gespraeche_aufklappen(self):
        p = pspeicher.standard()
        gid = p.gespraech_beginnen("chat")
        p.beitrag(gid, "du", "Hallo Friday")
        p.beitrag(gid, "friday", "Guten Tag, Sir.")
        g = self.c.get("/api/gedaechtnis").json()["gespraeche"][0]
        self.assertEqual(g["beitraege"], 2)
        self.assertEqual([b["text"] for b in self.c.get(f"/api/gespraeche/{gid}").json()],
                         ["Hallo Friday", "Guten Tag, Sir."])

    def test_leere_eingaben_abgelehnt(self):
        self.assertEqual(self.c.post("/api/gedaechtnis/fakten", json={"thema": "", "inhalt": "x"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()


class PersonenGespraeche(unittest.TestCase):
    """Personen-Fäden stehen mit Name statt als „Du“ da (02.10.2026)."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def test_person_name_statt_du(self):
        from kern.personen import register
        pid = register.anlegen("Kontakt C", snapchat="kontakt_c_y")["id"]
        g = pspeicher.standard().gespraech_beginnen("snapchat", person=pid)
        pspeicher.standard().beitrag(g, "du", "Hey")
        eigen = pspeicher.standard().gespraech_beginnen("chat")
        pspeicher.standard().beitrag(eigen, "du", "Hallo")
        gespraeche = {x["id"]: x for x in self.c.get("/api/gedaechtnis").json()["gespraeche"]}
        self.assertEqual(gespraeche[g]["person"], "Kontakt C")
        self.assertIsNone(gespraeche[eigen]["person"])
