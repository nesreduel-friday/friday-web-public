"""Noten (Schule → Noten): rechnen, speichern, prüfen — ohne Netz."""

import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern.main import app
from kern.schule import noten


def n(wert, skala="punkte", maximum=15, anteil=None, faktor=1, bereich_id=None, datum="2026-09-10", id_=1):
    return {"id": id_, "skala": skala, "wert": wert, "max": maximum, "anteil": anteil,
            "faktor": faktor, "bereich_id": bereich_id, "datum": datum}


class Rechnen(unittest.TestCase):
    def test_punkte_und_noten_umrechnen(self):
        self.assertAlmostEqual(noten.punkte(n(13)), 13)
        self.assertAlmostEqual(noten.punkte(n(27, maximum=40)), 10.125)
        self.assertAlmostEqual(noten.punkte(n(1, "note")), 14)
        self.assertAlmostEqual(noten.punkte(n(0.67, "note")), 15, places=1)   # 1+
        self.assertAlmostEqual(noten.punkte(n(2.33, "note")), 10, places=1)   # 2-
        self.assertEqual(noten.punkte(n(6, "note")), 0)                       # nie unter 0
        self.assertEqual(noten.note_aus_punkten(11), 2.0)

    def test_eine_note_mit_anteil_gilt_vorlaeufig_und_deckt_nur_ihren_anteil(self):
        g = noten.gruppe([n(13, anteil=20)])
        self.assertAlmostEqual(g["wert"], 13)
        self.assertEqual(g["abgedeckt"], 20)
        self.assertTrue(any("20 %" in h for h in g["hinweise"]))

    def test_rest_gehoert_den_noten_ohne_anteil(self):
        # Präsentation 13 P. zu 20 %, dazu zwei Mitarbeitsnoten (8 und 10 → Schnitt 9) für die übrigen 80 %.
        g = noten.gruppe([n(13, anteil=20), n(8, id_=2), n(10, id_=3)])
        self.assertAlmostEqual(g["wert"], 0.2 * 13 + 0.8 * 9)
        self.assertEqual(g["abgedeckt"], 100)

    def test_faktor_gewichtet_den_rest(self):
        g = noten.gruppe([n(15, faktor=3), n(3, id_=2)])
        self.assertAlmostEqual(g["wert"], (15 * 3 + 3) / 4)

    def test_anteile_ueber_100_werden_gekuerzt(self):
        g = noten.gruppe([n(15, anteil=75), n(5, anteil=75, id_=2)])
        self.assertAlmostEqual(g["wert"], 10)
        self.assertTrue(g["hinweise"])

    def test_bereiche_zaehlen_nach_gewicht_aber_nur_mit_noten(self):
        bereiche = [{"id": 1, "name": "Klausuren", "gewicht": 50}, {"id": 2, "name": "Mündlich", "gewicht": 50}]
        nur_muendlich = noten.fach_rechnen(bereiche, [n(12, bereich_id=2)])
        self.assertAlmostEqual(nur_muendlich["wert"], 12)             # Klausur fehlt noch
        beide = noten.fach_rechnen(bereiche, [n(12, bereich_id=2), n(6, bereich_id=1, id_=2)])
        self.assertAlmostEqual(beide["wert"], 9)
        self.assertEqual(beide["note"], round((17 - 9) / 3, 1))

    def test_ohne_bereiche_ist_es_ein_schnitt(self):
        f = noten.fach_rechnen([], [n(10), n(14, id_=2)])
        self.assertAlmostEqual(f["wert"], 12)
        self.assertIsNone(noten.fach_rechnen([], [])["wert"])

    def test_noten_ohne_bereich_bekommen_den_rest_der_gewichtung(self):
        bereiche = [{"id": 1, "name": "Klausuren", "gewicht": 60}]
        f = noten.fach_rechnen(bereiche, [n(10, bereich_id=1), n(15, id_=2)])
        self.assertAlmostEqual(f["ohne_bereich"]["gewicht"], 40)
        self.assertAlmostEqual(f["wert"], 0.6 * 10 + 0.4 * 15)
        voll = noten.fach_rechnen([{"id": 1, "name": "K", "gewicht": 100}], [n(10, bereich_id=1), n(15, id_=2)])
        self.assertAlmostEqual(voll["wert"], 10)                      # zählt nicht mit …
        self.assertTrue(voll["ohne_bereich"]["hinweise"])             # … und sagt das


def fach_mit(id_, name, pruefung, werte, halbjahre=None):
    """Ein Fach für die Abitur-Rechnung: Kursnoten je Semester (None = noch keine Note)."""
    halbjahre = halbjahre or ("12/1", "12/2", "13/1", "13/2")[:len(werte)]
    return {"id": id_, "name": name, "pruefung": pruefung, "halbjahre": list(halbjahre),
            "semester": {h: {"wert": w} for h, w in zip(halbjahre, werte)}}


class Abitur(unittest.TestCase):
    def test_note_aus_gesamtpunkten_wie_in_der_offiziellen_tabelle(self):
        for punkte, note in ((900, 1.0), (823, 1.0), (822, 1.1), (805, 1.1), (804, 1.2), (732, 1.6), (715, 1.6),
                             (714, 1.7), (301, 3.9), (300, 4.0)):
            self.assertEqual(noten.abiturnote(punkte), note, punkte)
        self.assertIsNone(noten.abiturnote(299))

    def test_kaufmaennisch_runden(self):
        self.assertEqual([noten.runden(x) for x in (10.5, 10.49, 11.5, 0.5)], [11, 10, 12, 1])

    def test_alles_zwoelf_punkte(self):
        faecher = [fach_mit(i, f"F{i}", i if i <= 5 else 0, [12] * 4) for i in range(1, 8)]
        a = noten.abitur_rechnen(faecher, {"pruefungen": {str(i): 12 for i in range(1, 6)}})
        self.assertEqual((a["block1"], a["block2"], a["gesamt"], a["note"]), (480, 240, 720, 1.6))
        self.assertTrue(a["bestanden"])

    def test_erhoehtes_niveau_zaehlt_doppelt(self):
        faecher = [fach_mit(1, "LK", 1, [15] * 4), fach_mit(2, "Grund", 0, [6] * 4)]
        a = noten.abitur_rechnen(faecher, {})
        self.assertAlmostEqual(a["schnitt"], (2 * 15 + 6) / 3)
        self.assertEqual(a["block1"], noten.runden((2 * 15 + 6) / 3 * 40))          # 12 · 40 = 480

    def test_kursnoten_werden_ganzzahlig_gerundet(self):
        a = noten.abitur_rechnen([fach_mit(1, "A", 0, [10.5, 9.4])], {})
        self.assertEqual([k["punkte"] for k in a["faecher"][0]["kurse"].values()], [11, 9])

    def test_abgewaehlter_kurs_zaehlt_nicht(self):
        f = [fach_mit(1, "A", 0, [4, 14])]
        self.assertEqual(noten.abitur_rechnen(f, {})["schnitt"], 9)
        a = noten.abitur_rechnen(f, {"ausgeschlossen": ["1/12/1"]})
        self.assertEqual(a["schnitt"], 14)
        self.assertFalse(a["faecher"][0]["kurse"]["12/1"]["zaehlt"])

    def test_noch_keine_noten_heisst_keine_schaetzung(self):
        a = noten.abitur_rechnen([fach_mit(1, "A", 1, [None] * 4)], {})
        self.assertEqual((a["block1"], a["block2"], a["gesamt"], a["note"]), (None, None, None, None))

    def test_pruefung_ohne_ergebnis_nimmt_den_kursschnitt_an(self):
        faecher = [fach_mit(1, "A", 1, [10, 10, 12, 12]), fach_mit(2, "B", 2, [None] * 4)]
        a = noten.abitur_rechnen(faecher, {"pruefungen": {"2": 5}})
        p = {x["nr"]: x for x in a["pruefungen"]}
        self.assertEqual((p[1]["wert"], p[1]["quelle"]), (11, "schnitt"))
        self.assertEqual((p[2]["wert"], p[2]["quelle"]), (5, "eingabe"))
        self.assertIsNone(p[3]["wert"])
        self.assertEqual(a["block2"], noten.runden((11 + 5) / 2 * 20))                # fehlende: Schnitt der bekannten
        self.assertTrue(any("hochgerechnet" in h for h in a["hinweise"]))

    def test_grenzen_und_hinweise(self):
        faecher = [fach_mit(i, f"F{i}", i, [2] * 4) for i in range(1, 6)]
        a = noten.abitur_rechnen(faecher, {})
        self.assertFalse(a["bestanden"])
        self.assertIsNone(a["note"])
        self.assertTrue(any("Block I" in h for h in a["hinweise"]))
        self.assertTrue(any("nicht bestanden" in h for h in a["hinweise"]))

    def test_sehr_fruehe_schaetzung_wird_als_grob_gekennzeichnet(self):
        wenig = noten.abitur_rechnen([fach_mit(i, f"F{i}", 0, [12] + [None] * 3) for i in range(1, 5)], {})
        self.assertTrue(any("sehr grob" in h for h in wenig["hinweise"]))                   # 4 von 16 Kursen
        genug = noten.abitur_rechnen([fach_mit(i, f"F{i}", 0, [12, 12, None, None]) for i in range(1, 5)], {})
        self.assertFalse(any("sehr grob" in h for h in genug["hinweise"]))                   # 8 von 16

    def test_einstellung_pruefen(self):
        ok = noten.abitur_pruefen({"pruefungen": {"1": 12.4, "2": "", "3": None}, "ausgeschlossen": ["3/12/1", "3/12/1"]})
        self.assertEqual(ok, {"pruefungen": {"1": 12}, "ausgeschlossen": ["3/12/1"]})
        for falsch in ({"pruefungen": {"6": 5}}, {"pruefungen": {"1": 16}}, {"pruefungen": {"1": "x"}},
                       {"ausgeschlossen": ["quatsch"]}, {"ausgeschlossen": ["1/11/1"]}):
            with self.assertRaises(ValueError, msg=str(falsch)):
                noten.abitur_pruefen(falsch)


class Pruefen(unittest.TestCase):
    OK = {"titel": "T", "datum": "2026-09-18", "wert": 5, "halbjahr": "12/1"}

    def test_gueltige_note(self):
        e = noten.eintrag_pruefen({**self.OK, "titel": "  Präsentation ", "wert": 13, "anteil": 20})
        self.assertEqual((e["titel"], e["wert"], e["max"], e["anteil"], e["halbjahr"]), ("Präsentation", 13, 15, 20, "12/1"))

    def test_ungueltiges_wird_abgelehnt(self):
        for aenderung in ({"titel": " "}, {"datum": "18.09.2026"}, {"datum": "2026-02-30"}, {"wert": 16},
                          {"wert": -1}, {"wert": "x"}, {"wert": None}, {"anteil": 0}, {"anteil": 101},
                          {"faktor": 0}, {"skala": "quatsch"}, {"skala": "note", "wert": 7},
                          {"max": 0}, {"wert": float("nan")}, {"halbjahr": "11/1"}, {"halbjahr": ""}):
            with self.assertRaises(ValueError, msg=str(aenderung)):
                noten.eintrag_pruefen({**self.OK, **aenderung})

    def test_punkte_von_anderem_maximum(self):
        e = noten.eintrag_pruefen({**self.OK, "wert": 27, "max": 40})
        self.assertEqual(e["max"], 40)

    def test_bereichsnamen_muessen_verschieden_sein(self):
        with self.assertRaises(ValueError):
            noten.bereiche_pruefen([{"name": "A"}, {"name": "a"}])

    def test_fachwahl(self):
        ok = noten.fachwahl_pruefen([{"name": " Mathematik ", "pruefung": 1, "stunden": 5, "halbjahre": ["13/2", "12/1"]}])
        self.assertEqual((ok[0]["name"], ok[0]["halbjahre"]), ("Mathematik", ["12/1", "13/2"]))     # in Reihenfolge
        for falsch in ([{"name": "A", "halbjahre": ["12/3"]}], [{"name": ""}], [{"name": "A", "pruefung": 6}],
                       [{"name": "A"}, {"name": "a"}], [{"name": "A", "pruefung": 2}, {"name": "B", "pruefung": 2}],
                       [{"name": "A", "stunden": 99}]):
            with self.assertRaises(ValueError, msg=str(falsch)):
                noten.fachwahl_pruefen(falsch)

    def test_vorgeschlagene_bereiche(self):
        self.assertEqual([b["name"] for b in noten.standard_bereiche("Biologie")], ["Klausuren", "Mündlich"])
        self.assertEqual(len(noten.standard_bereiche("Sport")), 1)


ALLE = ["12/1", "12/2", "13/1", "13/2"]
WAHL = [
    {"name": "Mathematik", "pruefung": 1, "stunden": 5, "halbjahre": ALLE},
    {"name": "Biologie", "pruefung": 2, "stunden": 5, "halbjahre": ALLE},
    {"name": "Englisch", "pruefung": 4, "stunden": 3, "halbjahre": ALLE},
    {"name": "Politik", "pruefung": 0, "stunden": 3, "halbjahre": ["12/1", "12/2"]},
    {"name": "Seminarfach", "pruefung": 0, "stunden": 2, "halbjahre": ["12/1", "12/2", "13/1"]},
]


class Schnittstelle(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        self.c = TestClient(app)

    def wahl(self, faecher=WAHL):
        r = self.c.put("/api/noten/fachwahl", json={"faecher": faecher})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def fach(self, name, d=None):
        return next(f for f in (d or self.c.get("/api/noten").json())["faecher"] if f["name"] == name)

    def bereich(self, name, hj, bereich):
        return next(b for b in self.fach(name)["semester"][hj]["bereiche"] if b["name"] == bereich)

    def note(self, name, **extra):
        f = self.fach(name)
        r = self.c.post(f"/api/noten/faecher/{f['id']}/eintraege",
                        json={"titel": "T", "datum": "2026-09-18", "wert": 9, "halbjahr": "12/1", **extra})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def test_leer(self):
        d = self.c.get("/api/noten").json()
        self.assertEqual((d["faecher"], d["halbjahre"]), ([], ALLE))

    def test_fachwahl_legt_faecher_mit_vorgeschlagenen_bereichen_an(self):
        d = self.wahl()
        self.assertEqual([f["name"] for f in d["faecher"]], ["Mathematik", "Biologie", "Englisch", "Politik", "Seminarfach"])
        for h in ALLE:
            self.assertEqual([b["name"] for b in self.fach("Biologie", d)["semester"][h]["bereiche"]], ["Klausuren", "Mündlich"])
        self.assertEqual([b["name"] for b in self.fach("Seminarfach", d)["semester"]["12/1"]["bereiche"]], ["Mündlich / Sonstige"])
        self.assertEqual(self.fach("Seminarfach", d)["halbjahre"], ["12/1", "12/2", "13/1"])
        self.assertEqual(sorted(self.fach("Politik", d)["semester"]), ["12/1", "12/2"])

    def test_wochenstunden_je_semester(self):
        d = self.wahl()
        # Mathe 5 + Bio 5 + Englisch 3 + Politik 3 + Seminarfach 2 (nur bis 13/1)
        self.assertEqual(d["stunden"], {"12/1": 18, "12/2": 18, "13/1": 15, "13/2": 13})

    def test_das_beispiel_praesentation_in_biologie(self):
        self.wahl()
        muendlich = self.bereich("Biologie", "12/1", "Mündlich")
        self.note("Biologie", titel="Präsentation", wert=13, max=15, anteil=20, bereich_id=muendlich["id"],
                  notiz="Frau X war zufrieden")
        s = self.fach("Biologie")["semester"]
        self.assertAlmostEqual(s["12/1"]["wert"], 13)
        self.assertIsNone(s["12/2"]["wert"])                       # anderes Semester bleibt leer
        m = next(b for b in s["12/1"]["bereiche"] if b["name"] == "Mündlich")
        self.assertEqual((m["anzahl"], m["abgedeckt"]), (1, 20))
        self.assertEqual(s["12/1"]["eintraege"][0]["notiz"], "Frau X war zufrieden")

    def test_note_nur_in_belegtem_semester(self):
        self.wahl()
        f = self.fach("Politik")
        r = self.c.post(f"/api/noten/faecher/{f['id']}/eintraege",
                        json={"titel": "T", "datum": "2026-09-18", "wert": 9, "halbjahr": "13/1"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("nicht belegt", r.json()["detail"])

    def test_aendern_und_loeschen(self):
        self.wahl()
        nid = self.note("Biologie")
        f = self.fach("Biologie")
        self.assertEqual(self.c.put(f"/api/noten/eintraege/{nid}", json={
            "titel": "Test", "datum": "2026-09-01", "wert": 11, "halbjahr": "12/2"}).status_code, 200)
        s = self.fach("Biologie")["semester"]
        self.assertEqual(s["12/1"]["eintraege"], [])
        self.assertEqual(s["12/2"]["eintraege"][0]["wert"], 11)
        self.assertEqual(self.c.delete(f"/api/noten/eintraege/{nid}").status_code, 200)
        self.assertEqual(self.c.delete(f"/api/noten/eintraege/{nid}").status_code, 404)

    def test_fachwahl_aendern_haelt_ids_und_noten(self):
        self.wahl()
        alt = self.fach("Biologie")
        self.note("Biologie")
        neu = [dict(f, id=self.fach(f["name"])["id"]) for f in WAHL]
        neu[1]["stunden"] = 4
        d = self.wahl(neu)
        self.assertEqual(self.fach("Biologie", d)["id"], alt["id"])
        self.assertEqual(self.fach("Biologie", d)["stunden"], 4)
        self.assertEqual(len(self.fach("Biologie", d)["semester"]["12/1"]["eintraege"]), 1)

    def test_semester_mit_noten_laesst_sich_nicht_abwaehlen(self):
        self.wahl()
        self.note("Biologie")
        neu = [dict(f, id=self.fach(f["name"])["id"]) for f in WAHL]
        neu[1]["halbjahre"] = ["12/2", "13/1", "13/2"]
        r = self.c.put("/api/noten/fachwahl", json={"faecher": neu})
        self.assertEqual(r.status_code, 422)
        self.assertIn("12/1", r.json()["detail"])
        self.assertEqual(len(self.fach("Biologie")["semester"]["12/1"]["eintraege"]), 1)   # nichts ging verloren

    def test_fach_aus_der_wahl_nehmen_loescht_es_mit_noten(self):
        self.wahl()
        self.note("Biologie")
        neu = [dict(f, id=self.fach(f["name"])["id"]) for f in WAHL if f["name"] != "Biologie"]
        d = self.wahl(neu)
        self.assertNotIn("Biologie", [f["name"] for f in d["faecher"]])

    def bereiche_setzen(self, name, hj, bereiche, **extra):
        f = self.fach(name)
        return self.c.put(f"/api/noten/faecher/{f['id']}", json={"halbjahr": hj, "bereiche": bereiche, **extra})

    def test_bereiche_gelten_je_semester(self):
        self.wahl()
        k, m = self.fach("Biologie")["semester"]["12/1"]["bereiche"]
        r = self.bereiche_setzen("Biologie", "12/1", [{"id": k["id"], "name": "Klausuren", "gewicht": 70},
                                                      {"id": m["id"], "name": "Mündlich", "gewicht": 30}])
        self.assertEqual(r.status_code, 200, r.text)
        f = self.fach("Biologie")
        self.assertEqual([b["gewicht"] for b in f["semester"]["12/1"]["bereiche"]], [70, 30])
        self.assertEqual([b["gewicht"] for b in f["semester"]["12/2"]["bereiche"]], [50, 50])     # unberührt

    def test_bereiche_fuer_alle_semester_uebernehmen(self):
        self.wahl()
        k, m = self.fach("Biologie")["semester"]["12/1"]["bereiche"]
        self.note("Biologie", halbjahr="12/2", bereich_id=self.bereich("Biologie", "12/2", "Mündlich")["id"])
        r = self.bereiche_setzen("Biologie", "12/1", [{"id": k["id"], "name": "Klausuren", "gewicht": 40},
                                                      {"id": m["id"], "name": "Mündlich", "gewicht": 40},
                                                      {"id": None, "name": "Referat", "gewicht": 20}], fuer_alle=True)
        self.assertEqual(r.status_code, 200, r.text)
        f = self.fach("Biologie")
        for h in ALLE:
            self.assertEqual([(b["name"], b["gewicht"]) for b in f["semester"][h]["bereiche"]],
                             [("Klausuren", 40), ("Mündlich", 40), ("Referat", 20)])
        # die Note in 12/2 hängt weiter an „Mündlich“ dort — gleicher Bereich, nur neue Gewichtung
        self.assertEqual(f["semester"]["12/2"]["eintraege"][0]["bereich_id"], self.bereich("Biologie", "12/2", "Mündlich")["id"])

    def test_bereich_loeschen_laesst_noten_ohne_bereich_stehen(self):
        self.wahl()
        k, m = self.fach("Biologie")["semester"]["12/1"]["bereiche"]
        self.note("Biologie", bereich_id=k["id"])
        r = self.bereiche_setzen("Biologie", "12/1", [{"id": m["id"], "name": "Mündlich", "gewicht": 100}])
        self.assertEqual(r.status_code, 200, r.text)
        f = self.fach("Biologie")
        self.assertEqual([b["name"] for b in f["semester"]["12/1"]["bereiche"]], ["Mündlich"])
        self.assertIsNone(f["semester"]["12/1"]["eintraege"][0]["bereich_id"])
        self.assertEqual(len(f["semester"]["12/2"]["bereiche"]), 2)

    def test_ziel_und_notiz_gelten_fuers_fach(self):
        self.wahl()
        f = self.fach("Biologie")
        self.assertEqual(self.c.put(f"/api/noten/faecher/{f['id']}", json={"ziel": 13, "notiz": "LK"}).status_code, 200)
        f = self.fach("Biologie")
        self.assertEqual((f["ziel"], f["notiz"]), (13, "LK"))
        self.assertEqual(len(f["semester"]["12/1"]["bereiche"]), 2)                              # Bereiche unverändert

    def test_bereiche_in_nicht_belegtem_semester_werden_abgelehnt(self):
        self.wahl()
        self.assertEqual(self.bereiche_setzen("Politik", "13/1", []).status_code, 422)

    def test_neues_semester_uebernimmt_die_bereiche_eines_anderen(self):
        self.wahl()
        k, m = self.fach("Politik")["semester"]["12/1"]["bereiche"]
        self.bereiche_setzen("Politik", "12/1", [{"id": k["id"], "name": "Klausuren", "gewicht": 60},
                                                 {"id": m["id"], "name": "Mündlich", "gewicht": 40}], fuer_alle=True)
        neu = [dict(f, id=self.fach(f["name"])["id"]) for f in WAHL]
        next(f for f in neu if f["name"] == "Politik")["halbjahre"] = ["12/1", "12/2", "13/1"]
        d = self.wahl(neu)
        self.assertEqual([b["gewicht"] for b in self.fach("Politik", d)["semester"]["13/1"]["bereiche"]], [60, 40])

    def test_bereich_eines_anderen_fachs_oder_semesters_wird_abgelehnt(self):
        self.wahl()
        f = self.fach("Biologie")
        for fremd in (self.bereich("Mathematik", "12/1", "Mündlich")["id"], self.bereich("Biologie", "12/2", "Mündlich")["id"]):
            r = self.c.post(f"/api/noten/faecher/{f['id']}/eintraege", json={
                "titel": "T", "datum": "2026-09-01", "wert": 5, "halbjahr": "12/1", "bereich_id": fremd})
            self.assertEqual(r.status_code, 422)

    def test_erste_fassung_mit_gemeinsamen_bereichen_wird_umgezogen(self):
        import sqlite3
        from kern.config import daten_ordner
        db = sqlite3.connect(daten_ordner() / "noten.db")
        db.executescript(noten.TABELLE.replace("    halbjahr  TEXT NOT NULL DEFAULT '',   -- jedes Semester hat seine eigene Zusammensetzung\n", ""))
        db.executescript("""
            INSERT INTO faecher (id, name, halbjahre, erstellt) VALUES (1, 'Biologie', '12/1,12/2', 'x');
            INSERT INTO bereiche (id, fach_id, name, gewicht) VALUES (1, 1, 'Mündlich', 100);
            INSERT INTO noten (fach_id, halbjahr, bereich_id, titel, datum, wert, erstellt)
                VALUES (1, '12/2', 1, 'Referat', '2026-09-18', 12, 'x');""")
        db.commit()
        db.close()
        noten._instanzen.clear()
        f = self.fach("Biologie")
        self.assertEqual([len(f["semester"][h]["bereiche"]) for h in ("12/1", "12/2")], [1, 1])
        n = f["semester"]["12/2"]["eintraege"][0]
        self.assertEqual(n["bereich_id"], f["semester"]["12/2"]["bereiche"][0]["id"])           # zur Kopie ihres Semesters
        self.assertEqual(f["semester"]["12/2"]["wert"], 12)

    def test_fehler_kommen_als_klartext(self):
        r = self.c.put("/api/noten/fachwahl", json={"faecher": [{"name": " "}]})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Name", r.json()["detail"])
        self.wahl()
        f = self.fach("Biologie")
        r = self.c.post(f"/api/noten/faecher/{f['id']}/eintraege",
                        json={"titel": "T", "datum": "2026-09-01", "wert": 99, "halbjahr": "12/1"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.c.post("/api/noten/faecher/999/eintraege", json={}).status_code, 404)

    def test_abitur_ueber_die_schnittstelle(self):
        self.wahl()
        for name, punkte in (("Mathematik", 12), ("Biologie", 10)):
            self.note(name, wert=punkte, bereich_id=self.bereich(name, "12/1", "Mündlich")["id"])
        d = self.c.get("/api/noten").json()["abitur"]
        self.assertEqual(d["kurse_mit_note"], 2)                                      # nur 12/1 hat Noten
        mathe = next(f for f in d["faecher"] if f["name"] == "Mathematik")
        self.assertEqual((mathe["gewicht"], mathe["kurse"]["12/1"]["punkte"], mathe["kurse"]["12/2"]["punkte"]), (2, 12, None))
        r = self.c.put("/api/noten/abitur", json={"pruefungen": {"1": 14}, "ausgeschlossen": [f"{mathe['id']}/12/1"]})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["abitur"]
        self.assertEqual(d["einstellung"], {"pruefungen": {"1": 14}, "ausgeschlossen": [f"{mathe['id']}/12/1"]})
        self.assertEqual(d["kurse_mit_note"], 1)                                      # Mathe 12/1 abgewählt
        self.assertEqual(self.c.get("/api/noten").json()["abitur"]["einstellung"], d["einstellung"])   # bleibt gespeichert
        self.assertEqual(self.c.put("/api/noten/abitur", json={"pruefungen": {"9": 3}}).status_code, 422)

    def test_alte_leere_datenbank_wird_ersetzt(self):
        import sqlite3
        from kern.config import daten_ordner
        db = sqlite3.connect(daten_ordner() / "noten.db")
        db.executescript("CREATE TABLE faecher (id INTEGER PRIMARY KEY, name TEXT, halbjahr TEXT);"
                         "CREATE TABLE bereiche (id INTEGER PRIMARY KEY, fach_id INTEGER);"
                         "CREATE TABLE noten (id INTEGER PRIMARY KEY, fach_id INTEGER);")
        db.close()
        noten._instanzen.clear()
        self.assertEqual(self.wahl()["halbjahre"], ALLE)


if __name__ == "__main__":
    unittest.main()
