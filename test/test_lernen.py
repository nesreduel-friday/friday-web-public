"""Lernen (Schule → Lernen): zieht Noten, Arbeiten-Muster, Gruppenordner, Goodnotes zusammen."""

import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern.goodnotes import ablage as goodnotesablage
from kern.main import app
from kern.schule import arbeiten, gruppenordner, lernen

WAHL = [{"name": "Biologie", "pruefung": 2, "stunden": 5, "halbjahre": ["12/1", "12/2", "13/1", "13/2"]},
        {"name": "Mathematik", "pruefung": 1, "stunden": 5, "halbjahre": ["12/1", "12/2", "13/1", "13/2"]}]


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.c = TestClient(app)
        r = self.c.put("/api/noten/fachwahl", json={"faecher": WAHL})
        self.assertEqual(r.status_code, 200, r.text)
        self.bio_id = next(f["id"] for f in self.c.get("/api/noten").json()["faecher"] if f["name"] == "Biologie")
        self.mathe_id = next(f["id"] for f in self.c.get("/api/noten").json()["faecher"] if f["name"] == "Mathematik")

    def _arbeit(self, fach_id, person=""):
        a = arbeiten.standard()
        aid = a.anlegen(f"probe-{fach_id}-{person}", [b"seite"], None)
        a.setzen(aid, fach_id=fach_id, person=person, datum="2026-09-01",
                ergebnis={"randnotizen": [], "themen": [], "lernpunkte": [], "zusammenfassung": ""})
        return aid

    def _muster(self, fach_id, person=""):
        arbeiten.standard().muster_setzen(person, fach_id, "", "Ein Muster.", 1)

    def _material(self, fach_id, kurs="Biologie eA"):
        g = gruppenordner.standard()
        [e] = g.abgleichen([{"pfad": f"Gruppenordner/{kurs}/Blatt.pdf", "ordner": False, "groesse": 1,
                            "geaendert": "2026-09-01T00:00:00+00:00"}], "Gruppenordner",
                           [{"id": fach_id, "name": kurs.split(" ")[0]}])
        return e

    def _material_gelesen(self, fach_id, kurs="Biologie eA", wichtiges=None):
        e = self._material(fach_id, kurs)
        gruppenordner.standard().setzen(e["id"], status="gelesen", zusammenfassung="Kurz.",
                                        wichtiges=wichtiges or [], gelesen_groesse=e["groesse"],
                                        gelesen_geaendert=e["geaendert"])
        return e["id"]

    def _heft(self, ordner="Biologie eA"):
        goodnotesablage.standard().hefte_abgleichen(
            [{"pfad": f"GoodNotes/{ordner}/Heft.pdf", "groesse": 1, "geaendert": "2026-09-01T00:00:00+00:00"}],
            "GoodNotes")


class Liste(Basis):
    def test_faecher_ohne_material_zeigen_nullen(self):
        d = self.c.get("/api/lernen").json()
        namen = {f["name"]: f for f in d["faecher"]}
        self.assertEqual((namen["Biologie"]["arbeiten"], namen["Biologie"]["muster"],
                          namen["Biologie"]["material"], namen["Biologie"]["goodnotes"]), (0, 0, 0, 0))

    def test_zaehlt_je_fach_getrennt(self):
        self._arbeit(self.bio_id)
        self._arbeit(self.bio_id, person="Kontakt F")
        self._arbeit(self.mathe_id)
        d = self.c.get("/api/lernen").json()
        namen = {f["name"]: f for f in d["faecher"]}
        self.assertEqual(namen["Biologie"]["arbeiten"], 2)
        self.assertEqual(namen["Mathematik"]["arbeiten"], 1)

    def test_goodnotes_heft_wird_ueber_den_ordnernamen_gefunden(self):
        self._heft(ordner="Biologie eA")
        d = self.c.get("/api/lernen").json()
        namen = {f["name"]: f for f in d["faecher"]}
        self.assertEqual(namen["Biologie"]["goodnotes"], 1)
        self.assertEqual(namen["Mathematik"]["goodnotes"], 0)


class Detail(Basis):
    def test_unbekanntes_fach_gibt_404(self):
        self.assertEqual(self.c.get("/api/lernen/999999").status_code, 404)

    def test_zieht_alles_fuer_ein_fach_zusammen(self):
        self._arbeit(self.bio_id)
        self._arbeit(self.bio_id, person="Kontakt F")
        self._muster(self.bio_id)
        self._material_gelesen(self.bio_id, wichtiges=["Q = m · c · ΔT"])
        self._heft(ordner="Biologie eA")
        d = self.c.get(f"/api/lernen/{self.bio_id}").json()
        self.assertEqual(d["fach"]["name"], "Biologie")
        self.assertIn("semester", d["fach"])   # Notendaten kommen mit
        self.assertEqual(len(d["arbeiten"]), 2)
        self.assertEqual({a["person"] for a in d["arbeiten"]}, {"", "Kontakt F"})
        self.assertEqual(len(d["muster"]), 1)
        self.assertEqual(len(d["material"]), 1)
        self.assertEqual(len(d["goodnotes"]), 1)
        self.assertEqual(len(d["aktuell"]["material"]), 1)
        self.assertEqual(d["aktuell"]["material"][0]["wichtiges"], ["Q = m · c · ΔT"])

    def test_ungelesenes_material_erscheint_nicht_bei_aktuell(self):
        self._material(self.bio_id)   # nie gelesen, Status bleibt "neu"
        d = self.c.get(f"/api/lernen/{self.bio_id}").json()
        self.assertEqual(d["material"][0]["status"], "neu")
        self.assertEqual(d["aktuell"]["material"], [])

    def test_neueste_goodnotes_seite_erscheint_bei_aktuell(self):
        self._heft(ordner="Biologie eA")
        [h] = [x for x in goodnotesablage.standard().hefte() if x["ordner"] == "Biologie eA"]
        with goodnotesablage.standard()._sperre, goodnotesablage.standard()._db:
            sid = goodnotesablage.standard()._db.execute(
                "INSERT INTO seiten (heft, nr, hash, status, datum, text) VALUES (?, 1, 'h', 'fertig', ?, ?)",
                (h["id"], "2026-09-20", "Wärmemenge Q = m · c · ΔT")).lastrowid
        d = self.c.get(f"/api/lernen/{self.bio_id}").json()
        self.assertEqual(len(d["aktuell"]["seiten"]), 1)
        self.assertIn("Wärmemenge", d["aktuell"]["seiten"][0]["text"])

    def test_material_und_muster_aus_anderem_fach_tauchen_nicht_auf(self):
        self._arbeit(self.mathe_id)
        self._muster(self.mathe_id)
        self._material(self.mathe_id, kurs="Mathematik")
        d = self.c.get(f"/api/lernen/{self.bio_id}").json()
        self.assertEqual((d["arbeiten"], d["muster"], d["material"]), ([], [], []))


if __name__ == "__main__":
    unittest.main()
