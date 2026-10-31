"""Klassenarbeiten (Schule → Arbeiten) — ohne Netz: Gemini ist eine Attrappe."""

import asyncio
import io
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from PIL import Image

from kern import konnektoren
from kern.main import app
from kern.schule import arbeiten, noten

JETZT = datetime(2026, 9, 20, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
ALLE = ["12/1", "12/2", "13/1", "13/2"]
WAHL = [
    {"name": "Biologie", "pruefung": 2, "stunden": 5, "halbjahre": ALLE},
    {"name": "Mathematik", "pruefung": 1, "stunden": 5, "halbjahre": ALLE},
    {"name": "Politik", "pruefung": 0, "stunden": 3, "halbjahre": ["12/1", "12/2"]},
    {"name": "Geschichte", "pruefung": 0, "stunden": 3, "halbjahre": ["13/1", "13/2"]},
]
FAECHER = [{"id": 1, "name": "Biologie", "halbjahre": ALLE}, {"id": 2, "name": "Mathematik", "halbjahre": ALLE},
           {"id": 3, "name": "Politik", "halbjahre": ["12/1", "12/2"]}, {"id": 4, "name": "Kunst", "halbjahre": ["13/1"]}]


def jpeg(farbe=(200, 30, 30), groesse=(300, 400), format="JPEG") -> bytes:
    puffer = io.BytesIO()
    Image.new("RGB", groesse, farbe).save(puffer, format)
    return puffer.getvalue()


def pdf(seiten=2) -> bytes:
    bilder = [Image.new("RGB", (300, 400), (i * 40, 90, 200)) for i in range(seiten)]
    puffer = io.BytesIO()
    bilder[0].save(puffer, "PDF", save_all=True, append_images=bilder[1:])
    return puffer.getvalue()


def roh(**extra) -> dict:
    """Wie eine Gemini-Antwort für eine gut lesbare Biologie-Klausur."""
    d = {"person": "Eike", "fach": "Biologie", "datum": "2026-09-09", "titel": "Klausur Nr. 1 – Genetik", "art": "klausur",
         "bewertung": {"vorhanden": True, "punkte": 11, "note": "2", "rohpunkte": 38, "rohpunkte_max": 50},
         "randnotizen": [{"seite": 2, "text": "Kreuzungsschema: Genotypen fehlen", "art": "fehler"},
                         {"seite": 1, "text": "Gut erklärt!", "art": "lob"}, {"seite": 1, "text": "  ", "art": "lob"}],
         "themen": ["Vererbung", "Vererbung", "Meiose"], "lernpunkte": ["Kreuzungsschemata üben", ""],
         "zusammenfassung": "Solide Arbeit."}
    d.update(extra)
    return d


class Erkennen(unittest.TestCase):
    def test_fach_zuordnen(self):
        z = lambda t: arbeiten.fach_zuordnen(t, FAECHER)
        self.assertEqual([z("Biologie"), z("biologie"), z("Bio"), z("Biol."), z("BI"), z("Mathe"), z("Politik-Wirtschaft")], [1, 1, 1, 1, 1, 2, 3])
        self.assertIsNone(z("Chemie"))
        self.assertIsNone(z(""))
        self.assertIsNone(z("B"))                                      # zu kurz für ein Kürzel

    def test_fach_nur_bei_genau_einem_treffer(self):
        zwei = FAECHER + [{"id": 9, "name": "Biologie-Seminar", "halbjahre": ALLE}]
        self.assertEqual(arbeiten.fach_zuordnen("Biologie", zwei), 1)          # genauer Name gewinnt
        self.assertIsNone(arbeiten.fach_zuordnen("Bio", zwei))                 # mehrdeutig

    def test_note_lesen(self):
        for text, erwartet in (("2", 2), ("2+", 2 - 1 / 3), ("4-", 4 + 1 / 3), ("3 −", 3 + 1 / 3), ("2,5", 2.5), ("1+", 1 - 1 / 3)):
            self.assertAlmostEqual(arbeiten.note_lesen(text), erwartet, msg=text)
        for text in ("", "gut", "7", "0", "12"):
            self.assertIsNone(arbeiten.note_lesen(text), text)

    def test_gut_lesbare_arbeit_hat_keine_zweifel(self):
        e = arbeiten.auswerten(roh(), FAECHER, JETZT.date())
        self.assertEqual(e["hinweise"], [])
        self.assertEqual((e["fach_id"], e["halbjahr"], e["datum"], e["skala"], e["wert"], e["max"]),
                         (1, "12/1", "2026-09-09", "punkte", 11, 15))
        self.assertEqual(e["ergebnis"]["themen"], ["Vererbung", "Meiose"])           # ohne Doppelte
        self.assertEqual(e["ergebnis"]["lernpunkte"], ["Kreuzungsschemata üben"])    # ohne Leeres
        self.assertEqual(len(e["ergebnis"]["randnotizen"]), 2)                       # leere Randnotiz fällt weg

    def test_nur_note_ohne_punkte(self):
        e = arbeiten.auswerten(roh(bewertung={"vorhanden": True, "punkte": -1, "note": "2+", "rohpunkte": -1, "rohpunkte_max": -1}),
                               FAECHER, JETZT.date())
        self.assertEqual((e["skala"], round(e["wert"], 2), e["hinweise"]), ("note", 1.67, []))

    def test_zweifel_landen_in_den_hinweisen(self):
        alle = lambda **k: arbeiten.auswerten(roh(**k), FAECHER, JETZT.date())["hinweise"]
        self.assertTrue(any("Fach ist unklar" in h for h in alle(fach="Chemie")))
        self.assertTrue(any("Datum" in h for h in alle(datum="irgendwann")))
        self.assertTrue(any("Datum" in h for h in alle(datum="2027-05-01")))             # in der Zukunft
        self.assertTrue(any("keinem Semester" in h for h in alle(datum="2025-03-01")))
        self.assertTrue(any("nicht belegt" in h for h in alle(fach="Kunst")))            # Kunst nur 13/1
        keine = alle(bewertung={"vorhanden": False, "punkte": -1, "note": "", "rohpunkte": -1, "rohpunkte_max": -1})
        self.assertTrue(any("Keine Note" in h for h in keine))
        roh_nur = alle(bewertung={"vorhanden": True, "punkte": -1, "note": "", "rohpunkte": 38, "rohpunkte_max": 50})
        self.assertTrue(any("Rohpunkte" in h and "38 von 50" in h for h in roh_nur))
        passt_nicht = alle(bewertung={"vorhanden": True, "punkte": 3, "note": "1", "rohpunkte": -1, "rohpunkte_max": -1})
        self.assertTrue(any("passen nicht zusammen" in h for h in passt_nicht))

    def test_muell_von_gemini_bricht_nichts(self):
        for muell in (None, "text", [], {"bewertung": "hm", "randnotizen": "x", "themen": 5}):
            e = arbeiten.auswerten(muell, FAECHER, JETZT.date())
            self.assertTrue(e["hinweise"])
            self.assertEqual(e["ergebnis"]["randnotizen"], [])

    def test_person_lesen(self):
        self.assertEqual(arbeiten.person_lesen("Eike"), "")
        self.assertEqual(arbeiten.person_lesen(" eike "), "")
        self.assertEqual(arbeiten.person_lesen(""), "")
        self.assertEqual(arbeiten.person_lesen(None), "")
        self.assertEqual(arbeiten.person_lesen(" Kontakt F  Meier "), "Kontakt F Meier")

    def test_fremde_arbeit_bekommt_keine_notenbezogenen_hinweise(self):
        # Dieselbe schlecht lesbare Bewertung wie in test_zweifel_landen_in_den_hinweisen —
        # bei einer fremden Arbeit interessiert das nicht, sie geht nie in Eikes Noten.
        e = arbeiten.auswerten(roh(person="Kontakt F", fach="Chemie", datum="irgendwann",
                                   bewertung={"vorhanden": False, "punkte": -1, "note": "", "rohpunkte": -1,
                                              "rohpunkte_max": -1}),
                               FAECHER, JETZT.date())
        self.assertEqual((e["person"], e["hinweise"]), ("Kontakt F", []))

    def test_bereich_waehlen(self):
        b = [{"id": 1, "name": "Klausuren"}, {"id": 2, "name": "Mündlich"}]
        self.assertEqual([arbeiten.bereich_waehlen(b, "klausur"), arbeiten.bereich_waehlen(b, "test"),
                          arbeiten.bereich_waehlen(b, "referat")], [1, 2, 2])
        self.assertEqual(arbeiten.bereich_waehlen([{"id": 5, "name": "Mündlich / Sonstige"}], "klausur"), 5)
        self.assertIsNone(arbeiten.bereich_waehlen([], "klausur"))


class Dateien(unittest.TestCase):
    def test_bilder_werden_zu_jpeg_seiten(self):
        art, seiten, original = arbeiten.uploads_pruefen([("a.png", jpeg(format="PNG")), ("b.jpg", jpeg())])
        self.assertEqual((art, len(seiten), original), ("bilder", 2, None))
        self.assertTrue(all(s.startswith(b"\xff\xd8\xff") for s in seiten))

    def test_grosse_fotos_werden_verkleinert(self):
        _, seiten, _ = arbeiten.uploads_pruefen([("a.jpg", jpeg(groesse=(4000, 3000)))])
        self.assertEqual(max(Image.open(io.BytesIO(seiten[0])).size), arbeiten.SPEICHER_PX)

    def test_pdf_wird_seitenweise_gerendert_und_bleibt_erhalten(self):
        daten = pdf(3)
        art, seiten, original = arbeiten.uploads_pruefen([("arbeit.pdf", daten)])
        self.assertEqual((art, len(seiten), original), ("pdf", 3, daten))

    def test_falsches_wird_abgelehnt(self):
        for dateien, teil in (([], "mindestens"), ([("x.txt", b"hallo")], "kein Foto"),
                              ([("a.pdf", pdf(1)), ("b.jpg", jpeg())], "nicht gemischt"),
                              ([("kaputt.jpg", b"\xff\xd8\xff" + b"nichts")], "nicht öffnen"),
                              ([("a.pdf", pdf(1)), ("b.pdf", pdf(1))], "nicht gemischt"),
                              ([("k.pdf", b"%PDF-1.4 kaputt")], "nicht öffnen")):
            with self.assertRaises(arbeiten.UploadFehler, msg=str(dateien)) as f:
                arbeiten.uploads_pruefen(dateien)
            self.assertIn(teil, str(f.exception))

    def test_zu_viele_seiten(self):
        with self.assertRaises(arbeiten.UploadFehler):
            arbeiten.uploads_pruefen([(f"{i}.jpg", jpeg(groesse=(20, 20))) for i in range(arbeiten.MAX_SEITEN + 1)])

    def test_pdf_aus_seiten(self):
        _, seiten, _ = arbeiten.uploads_pruefen([("a.jpg", jpeg()), ("b.jpg", jpeg())])
        daten = arbeiten.zu_pdf(seiten)
        self.assertTrue(daten.startswith(b"%PDF-"))
        from kern.schule import buecher
        self.assertEqual(buecher.seitenzahl_bytes(daten), 2)

    def test_dateiname_ist_harmlos(self):
        self.assertEqual(arbeiten.dateiname("Biologie", {"datum": "2026-09-09", "titel": "Klausur Nr. 1 – Genetik"}),
                         "Biologie 2026-09-09 Klausur Nr. 1 – Genetik")
        n = arbeiten.dateiname("Bio", {"datum": "", "titel": '../../etc/passwd"; rm <x>'})
        self.assertNotIn("/", n)
        self.assertNotIn('"', n)


class Schnittstelle(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        uhr = mock.patch("kern.config.jetzt", return_value=JETZT)
        uhr.start()
        self.addCleanup(uhr.stop)
        arbeiten._sperre = None
        arbeiten._laeufe.clear()
        self.frei = mock.patch.object(arbeiten.konnektoren, "benutzbar", return_value=False)
        self.frei.start()
        self.addCleanup(self.frei.stop)
        self.c = TestClient(app)
        r = self.c.put("/api/noten/fachwahl", json={"faecher": WAHL})
        self.assertEqual(r.status_code, 200, r.text)

    def hochladen(self, dateien=None):
        dateien = dateien or [("s1.jpg", jpeg()), ("s2.jpg", jpeg((10, 200, 30)))]
        r = self.c.post("/api/arbeiten", files=[("dateien", (n, d, "application/octet-stream")) for n, d in dateien])
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def lesen(self, aid, antwort=None, fehler=None):
        """Die Arbeit „von Gemini lesen“ lassen — mit Attrappe."""
        ki = mock.AsyncMock(return_value=(antwort or roh(), "gemini-test"), side_effect=fehler)
        with mock.patch.object(arbeiten.ki, "json_anfrage", ki):
            asyncio.run(arbeiten.lesen(aid))
        return ki

    def arbeit(self, aid):
        return next(a for a in self.c.get("/api/arbeiten").json()["arbeiten"] if a["id"] == aid)

    def fach(self, name):
        return next(f for f in self.c.get("/api/noten").json()["faecher"] if f["name"] == name)

    def test_leer(self):
        d = self.c.get("/api/arbeiten").json()
        self.assertEqual((d["arbeiten"], d["frei"]), ([], False))
        self.assertEqual([f["name"] for f in d["faecher"]], ["Mathematik", "Biologie", "Geschichte", "Politik"])

    def test_ohne_freigabe_wird_nur_abgelegt(self):
        aid = self.hochladen()
        a = self.arbeit(aid)
        self.assertEqual((a["status"], a["seiten"]), ("pruefen", 2))
        self.assertTrue(any("Freigabe" in h for h in a["hinweise"]))
        self.assertEqual(self.fach("Biologie")["semester"]["12/1"]["eintraege"], [])       # nichts eingetragen

    def test_doppelte_arbeit_wird_abgelehnt(self):
        self.hochladen()
        r = self.c.post("/api/arbeiten", files=[("dateien", ("s1.jpg", jpeg(), "image/jpeg")),
                                                ("dateien", ("s2.jpg", jpeg((10, 200, 30)), "image/jpeg"))])
        self.assertEqual(r.status_code, 400)
        self.assertIn("schon hochgeladen", r.json()["detail"])

    def test_unbrauchbare_dateien_geben_klartext(self):
        r = self.c.post("/api/arbeiten", files=[("dateien", ("x.txt", b"hallo", "text/plain"))])
        self.assertEqual(r.status_code, 400)
        self.assertIn("kein Foto", r.json()["detail"])

    def test_lesen_trägt_die_note_ein_und_merkt_sich_die_randnotizen(self):
        aid = self.hochladen()
        # `lesen()` ruft ki.json_anfrage danach noch einmal für die Muster-Zusammenfassung —
        # der erste Aufruf ist das eigentliche Lesen der Arbeit.
        ki = self.lesen(aid)
        teile = ki.call_args_list[0].args[0]
        self.assertEqual(len([t for t in teile if "inlineData" in t]), 2)                  # beide Seiten gingen mit
        self.assertIn("Mathematik, Biologie, Geschichte, Politik", ki.call_args_list[0].args[1])                        # Eikes Fächer stehen im Auftrag
        a = self.arbeit(aid)
        self.assertEqual((a["status"], a["fach"], a["halbjahr"], a["datum"], a["hinweise"]),
                         ("eingetragen", "Biologie", "12/1", "2026-09-09", []))
        self.assertEqual([r["art"] for r in a["ergebnis"]["randnotizen"]], ["fehler", "lob"])
        self.assertEqual(a["ergebnis"]["lernpunkte"], ["Kreuzungsschemata üben"])
        bio = self.fach("Biologie")["semester"]["12/1"]
        self.assertEqual(len(bio["eintraege"]), 1)
        note = bio["eintraege"][0]
        self.assertEqual((note["titel"], note["wert"], note["max"], note["datum"], note["id"]),
                         ("Klausur Nr. 1 – Genetik", 11, 15, "2026-09-09", a["note_id"]))
        self.assertEqual(next(b["name"] for b in bio["bereiche"] if b["id"] == note["bereich_id"]), "Klausuren")
        self.assertAlmostEqual(bio["wert"], 11)

    def test_test_kommt_in_den_muendlichen_bereich(self):
        aid = self.hochladen()
        self.lesen(aid, roh(art="test", titel="Vokabeltest"))
        bio = self.fach("Biologie")["semester"]["12/1"]
        self.assertEqual(next(b["name"] for b in bio["bereiche"] if b["id"] == bio["eintraege"][0]["bereich_id"]), "Mündlich")

    def test_bei_zweifeln_wird_nichts_eingetragen(self):
        aid = self.hochladen()
        self.lesen(aid, roh(bewertung={"vorhanden": False, "punkte": -1, "note": "", "rohpunkte": -1, "rohpunkte_max": -1}))
        a = self.arbeit(aid)
        self.assertEqual(a["status"], "pruefen")
        self.assertTrue(any("Keine Note" in h for h in a["hinweise"]))
        self.assertEqual(a["fach"], "Biologie")                                            # das Erkannte steht trotzdem da
        self.assertEqual(self.fach("Biologie")["semester"]["12/1"]["eintraege"], [])

    def test_fremde_arbeit_wird_nicht_eingetragen_aber_gelesen(self):
        aid = self.hochladen()
        muster = mock.patch.object(arbeiten, "muster_aktualisieren", mock.AsyncMock())
        muster.start()
        self.addCleanup(muster.stop)
        self.lesen(aid, roh(person="Kontakt F", fach="Chemie"))
        a = self.arbeit(aid)
        self.assertEqual((a["status"], a["person"], a["fach_text"], a["hinweise"]),
                         ("gelesen", "Kontakt F", "Chemie", []))
        self.assertIsNone(a["note_id"])
        self.assertEqual(self.fach("Biologie")["semester"]["12/1"]["eintraege"], [])

    def test_muster_aktualisieren_wird_nach_dem_lesen_aufgerufen(self):
        aid = self.hochladen()
        mathe_id = self.fach("Mathematik")["id"]
        with mock.patch.object(arbeiten, "muster_aktualisieren", mock.AsyncMock()) as muster:
            self.lesen(aid, roh(person="Kontakt F", fach="Mathematik"))
        muster.assert_awaited_once_with("Kontakt F", mathe_id, "Mathematik", "Mathematik")

    def test_fehler_von_gemini_bleiben_an_der_arbeit(self):
        aid = self.hochladen()
        from kern.schule import ki
        self.lesen(aid, fehler=ki.KiFehler("Gemini hat nicht geantwortet (gemini-3.8-flash: 429)"))
        a = self.arbeit(aid)
        self.assertEqual(a["status"], "fehler")
        self.assertIn("429", a["fehler"])

    def test_von_hand_eintragen_und_aendern_gibt_keine_doppelte_note(self):
        aid = self.hochladen()
        bio = self.fach("Biologie")
        r = self.c.put(f"/api/arbeiten/{aid}", json={"fach_id": bio["id"], "datum": "2026-09-09", "titel": "Test Zellen",
                                                     "art": "test", "skala": "punkte", "wert": 9, "max": 15})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["status"], r.json()["halbjahr"]), ("eingetragen", "12/1"))     # Halbjahr aus dem Datum
        r = self.c.put(f"/api/arbeiten/{aid}", json={"fach_id": bio["id"], "datum": "2026-09-09", "titel": "Test Zellen",
                                                     "art": "test", "skala": "note", "wert": 2})
        self.assertEqual(r.status_code, 200, r.text)
        eintraege = self.fach("Biologie")["semester"]["12/1"]["eintraege"]
        self.assertEqual([(e["skala"], e["wert"]) for e in eintraege], [("note", 2)])              # geändert, nicht verdoppelt

    def test_nur_ablegen(self):
        aid = self.hochladen()
        bio = self.fach("Biologie")
        r = self.c.put(f"/api/arbeiten/{aid}", json={"fach_id": bio["id"], "datum": "2026-09-09", "titel": "Referat",
                                                     "art": "referat", "eintragen": False})
        self.assertEqual((r.status_code, r.json()["status"], r.json()["fach"]), (200, "abgelegt", "Biologie"))
        self.assertEqual(self.fach("Biologie")["semester"]["12/1"]["eintraege"], [])
        # ist die Note aber schon eingetragen, geht „nur ablegen“ nicht mehr
        self.c.put(f"/api/arbeiten/{aid}", json={"fach_id": bio["id"], "datum": "2026-09-09", "skala": "punkte", "wert": 9, "max": 15})
        r = self.c.put(f"/api/arbeiten/{aid}", json={"fach_id": bio["id"], "datum": "2026-09-09", "eintragen": False})
        self.assertEqual(r.status_code, 409)

    def test_fehlende_oder_falsche_angaben_geben_klartext(self):
        aid = self.hochladen()
        bio = self.fach("Biologie")
        pol = self.fach("Politik")
        for d, teil in (({"datum": "2026-09-09", "skala": "punkte", "wert": 9, "max": 15}, "Zu welchem Fach"),
                        ({"fach_id": bio["id"], "datum": "2026-09-09"}, "Der Wert"),
                        ({"fach_id": bio["id"], "datum": "kaputt", "skala": "punkte", "wert": 9}, "Datum"),
                        ({"fach_id": pol["id"], "datum": "2027-09-09", "skala": "punkte", "wert": 9}, "nicht belegt"),
                        ({"fach_id": 999, "datum": "2026-09-09", "skala": "punkte", "wert": 9}, "Fach gibt es nicht")):
            r = self.c.put(f"/api/arbeiten/{aid}", json=d)
            self.assertEqual(r.status_code, 422, (d, r.text))
            self.assertIn(teil, r.json()["detail"], d)
        self.assertEqual(self.c.put("/api/arbeiten/999", json={}).status_code, 404)

    def test_seiten_und_vorschau(self):
        aid = self.hochladen()
        gross = self.c.get(f"/api/arbeiten/{aid}/seite/1")
        self.assertEqual((gross.status_code, gross.headers["content-type"]), (200, "image/jpeg"))
        self.assertEqual(self.c.get(f"/api/arbeiten/{aid}/seite/2?klein=1").status_code, 200)
        self.assertEqual(self.c.get(f"/api/arbeiten/{aid}/seite/3").status_code, 404)
        self.assertEqual(self.c.get("/api/arbeiten/999/seite/1").status_code, 404)

    def test_download_als_pdf(self):
        aid = self.hochladen()
        bio = self.fach("Biologie")
        self.c.put(f"/api/arbeiten/{aid}", json={"fach_id": bio["id"], "datum": "2026-09-09", "titel": "Klausur Nr. 1",
                                                 "skala": "punkte", "wert": 9, "max": 15})
        r = self.c.get(f"/api/arbeiten/{aid}/pdf")
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "application/pdf"))
        self.assertTrue(r.content.startswith(b"%PDF-"))
        self.assertIn("attachment", r.headers["content-disposition"])
        self.assertIn("Biologie%202026-09-09%20Klausur%20Nr.%201.pdf", r.headers["content-disposition"])

    def test_pdf_upload_kommt_unveraendert_zurueck(self):
        original = pdf(2)
        aid = self.hochladen([("arbeit.pdf", original)])
        self.assertEqual(self.arbeit(aid)["seiten"], 2)
        self.assertEqual(self.c.get(f"/api/arbeiten/{aid}/pdf").content, original)
        self.assertEqual(self.c.get(f"/api/arbeiten/{aid}/seite/2").status_code, 200)

    def test_loeschen_laesst_die_note_stehen(self):
        aid = self.hochladen()
        self.lesen(aid)
        ordner = arbeiten.standard().arbeit_ordner(aid)
        self.assertTrue(ordner.exists())
        self.assertEqual(self.c.delete(f"/api/arbeiten/{aid}").status_code, 200)
        self.assertFalse(ordner.exists())
        self.assertEqual(self.c.get("/api/arbeiten").json()["arbeiten"], [])
        self.assertEqual(len(self.fach("Biologie")["semester"]["12/1"]["eintraege"]), 1)          # Note bleibt
        self.assertEqual(self.c.delete(f"/api/arbeiten/{aid}").status_code, 404)

    def test_loeschen_kann_die_note_mitnehmen(self):
        aid = self.hochladen()
        self.lesen(aid)
        self.assertEqual(self.c.delete(f"/api/arbeiten/{aid}?note=1").status_code, 200)
        self.assertEqual(self.fach("Biologie")["semester"]["12/1"]["eintraege"], [])

    def test_nochmal_lesen_nicht_bei_eingetragenen(self):
        aid = self.hochladen()
        self.lesen(aid)
        self.assertEqual(self.c.post(f"/api/arbeiten/{aid}/lesen").status_code, 409)
        aid2 = self.hochladen([("x.jpg", jpeg((1, 2, 3)))])
        self.assertEqual(self.c.post(f"/api/arbeiten/{aid2}/lesen").status_code, 403)              # Freigabe aus

    def test_nochmal_lesen_startet_den_lauf_mit_freigabe(self):
        """Der Knopf „Nochmal lesen“ lief in einem Thread ohne Ereignisschleife und gab 500."""
        aid = self.hochladen()
        self.frei.stop()
        with mock.patch.object(arbeiten.konnektoren, "benutzbar", return_value=True), \
                mock.patch.object(arbeiten.ki, "json_anfrage", mock.AsyncMock(return_value=(roh(), "gemini-test"))):
            with TestClient(app) as c:
                r = c.post(f"/api/arbeiten/{aid}/lesen")
                self.assertEqual(r.status_code, 200, r.text)
                import time
                for _ in range(100):
                    a = next(x for x in c.get("/api/arbeiten").json()["arbeiten"] if x["id"] == aid)
                    if a["status"] not in ("neu", "liest", "pruefen"):
                        break
                    time.sleep(0.05)
                self.assertEqual(a["status"], "eingetragen")
        self.frei.start()

    def test_uebersicht_verraet_keine_pfade(self):
        aid = self.hochladen()
        text = self.c.get("/api/arbeiten").text
        self.assertNotIn(str(arbeiten.standard().ordner), text)
        self.assertNotIn("pruefsumme", text)

    def test_lesen_im_hintergrund_ueber_den_upload(self):
        """Mit Freigabe startet der Upload das Lesen selbst (Attrappe statt Gemini)."""
        self.frei.stop()
        with mock.patch.object(arbeiten.konnektoren, "benutzbar", return_value=True), \
                mock.patch.object(arbeiten.ki, "json_anfrage", mock.AsyncMock(return_value=(roh(), "gemini-test"))):
            with TestClient(app) as c:
                r = c.post("/api/arbeiten", files=[("dateien", ("s.jpg", jpeg((9, 9, 9)), "image/jpeg"))])
                self.assertTrue(r.json()["gestartet"])
                for _ in range(100):
                    a = next(x for x in c.get("/api/arbeiten").json()["arbeiten"] if x["id"] == r.json()["id"])
                    if a["status"] not in ("neu", "liest"):
                        break
                    import time
                    time.sleep(0.05)
                self.assertEqual(a["status"], "eingetragen")
        self.frei.start()


class Muster(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        self.a = arbeiten.standard()

    def _arbeit(self, person="", fach_id=1, fach_text="Biologie", datum="2026-09-01", lernpunkte=None,
                fehler_text=""):
        summe = f"{person}-{fach_text}-{datum}-{fehler_text}"
        aid = self.a.anlegen(summe, [jpeg()], None)
        randnotizen = [{"seite": 1, "text": fehler_text, "art": "fehler"}] if fehler_text else []
        self.a.setzen(aid, person=person, fach_id=fach_id, fach_text=fach_text, datum=datum,
                      ergebnis={"randnotizen": randnotizen, "themen": [], "lernpunkte": lernpunkte or [],
                                "zusammenfassung": ""})
        return aid

    def test_muster_quellen_findet_nur_dieselbe_person_und_dasselbe_fach(self):
        self._arbeit(person="Kontakt F", fach_id=1, fehler_text="Vorzeichenfehler")
        self._arbeit(person="Kontakt F", fach_id=2, fehler_text="anderes Fach")
        self._arbeit(person="Kontakt B", fach_id=1, fehler_text="andere Person")
        gefunden = arbeiten._muster_quellen("Kontakt F", 1, "Biologie")
        self.assertEqual(len(gefunden), 1)
        self.assertEqual(gefunden[0]["ergebnis"]["randnotizen"][0]["text"], "Vorzeichenfehler")

    def test_muster_quellen_ohne_inhalt_wird_uebersprungen(self):
        self._arbeit(person="Kontakt F", fach_id=1)   # weder Lernpunkte noch Fehler noch Zusammenfassung
        self.assertEqual(arbeiten._muster_quellen("Kontakt F", 1, "Biologie"), [])

    def test_muster_eingabe_sortiert_nach_datum_und_zieht_nur_fehler_raus(self):
        self._arbeit(person="Kontakt F", fach_id=1, datum="2026-09-10", fehler_text="B zuerst gelesen")
        self._arbeit(person="Kontakt F", fach_id=1, datum="2026-09-01", lernpunkte=["A zuerst gelesen"])
        text = arbeiten._muster_eingabe(arbeiten._muster_quellen("Kontakt F", 1, "Biologie"))
        self.assertLess(text.index("A zuerst gelesen"), text.index("B zuerst gelesen"))
        # Lob-Randnotizen gehören nicht zur Fehlerauswertung.
        self._arbeit(person="Kontakt F", fach_id=1, datum="2026-09-15")
        self.a.setzen(3, ergebnis={"randnotizen": [{"seite": 1, "text": "Gut gemacht!", "art": "lob"}],
                                   "themen": [], "lernpunkte": [], "zusammenfassung": ""})
        text2 = arbeiten._muster_eingabe(arbeiten._muster_quellen("Kontakt F", 1, "Biologie"))
        self.assertNotIn("Gut gemacht!", text2)

    def test_ohne_quellen_tut_muster_aktualisieren_nichts(self):
        with mock.patch.object(arbeiten.ki, "json_anfrage", mock.AsyncMock()) as ki:
            asyncio.run(arbeiten.muster_aktualisieren("Kontakt F", 1, "Biologie", "Biologie"))
        ki.assert_not_awaited()
        self.assertEqual(self.a.muster_liste(), [])

    def test_muster_aktualisieren_speichert_und_speist_ins_gedaechtnis_ein(self):
        self._arbeit(person="Kontakt F", fach_id=1, fehler_text="Vorzeichenfehler")
        ki = mock.AsyncMock(return_value=({"muster": "Wiederholt Vorzeichenfehler."}, "gemini-test"))
        graph = mock.AsyncMock(return_value=True)
        with mock.patch.object(arbeiten.ki, "json_anfrage", ki), \
             mock.patch("kern.gedaechtnis.graph.einspeisen", graph):
            asyncio.run(arbeiten.muster_aktualisieren("Kontakt F", 1, "Biologie", "Biologie"))
        muster = self.a.muster_liste()
        self.assertEqual(len(muster), 1)
        self.assertEqual((muster[0]["person"], muster[0]["fach_id"], muster[0]["text"], muster[0]["anzahl"]),
                         ("Kontakt F", 1, "Wiederholt Vorzeichenfehler.", 1))
        graph.assert_awaited_once()
        args, kwargs = graph.call_args
        self.assertIn("Kontakt F", args[1])
        self.assertIn("Wiederholt Vorzeichenfehler.", args[1])
        from kern.gedaechtnis import graph as graphmodul
        self.assertEqual(kwargs["anweisungen"], graphmodul.MUSTER_ANWEISUNGEN)

    def test_muster_fuer_eike_nutzt_die_normalen_anweisungen(self):
        self._arbeit(person="", fach_id=1, fehler_text="Kommafehler")
        ki = mock.AsyncMock(return_value=({"muster": "Kommafehler."}, "gemini-test"))
        graph = mock.AsyncMock(return_value=True)
        with mock.patch.object(arbeiten.ki, "json_anfrage", ki), \
             mock.patch("kern.gedaechtnis.graph.einspeisen", graph):
            asyncio.run(arbeiten.muster_aktualisieren("", 1, "Biologie", "Biologie"))
        from kern.gedaechtnis import graph as graphmodul
        self.assertEqual(graph.call_args.kwargs["anweisungen"], graphmodul.ANWEISUNGEN)

    def test_zweite_aktualisierung_ersetzt_die_erste_statt_zu_verdoppeln(self):
        self._arbeit(person="Kontakt F", fach_id=1, fehler_text="Vorzeichenfehler")
        ki = mock.AsyncMock(return_value=({"muster": "Erste Fassung."}, "gemini-test"))
        with mock.patch.object(arbeiten.ki, "json_anfrage", ki), mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock()):
            asyncio.run(arbeiten.muster_aktualisieren("Kontakt F", 1, "Biologie", "Biologie"))
        ki2 = mock.AsyncMock(return_value=({"muster": "Zweite Fassung."}, "gemini-test"))
        with mock.patch.object(arbeiten.ki, "json_anfrage", ki2), mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock()):
            asyncio.run(arbeiten.muster_aktualisieren("Kontakt F", 1, "Biologie", "Biologie"))
        muster = self.a.muster_liste()
        self.assertEqual(len(muster), 1)
        self.assertEqual(muster[0]["text"], "Zweite Fassung.")

    def test_muster_ohne_fach_id_wird_ueber_fach_text_gefunden(self):
        self._arbeit(person="Kontakt F", fach_id=None, fach_text="Chemie", fehler_text="Formeln vertauscht")
        ki = mock.AsyncMock(return_value=({"muster": "Formeln vertauscht."}, "gemini-test"))
        with mock.patch.object(arbeiten.ki, "json_anfrage", ki), mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock()):
            asyncio.run(arbeiten.muster_aktualisieren("Kontakt F", None, "Chemie", "Chemie"))
        muster = self.a.muster_liste()
        self.assertEqual((muster[0]["fach_id"], muster[0]["fach_text"]), (None, "Chemie"))

    def test_muster_liste_zeigt_eike_zuerst(self):
        self._arbeit(person="Kontakt F", fach_id=1, fehler_text="x")
        self._arbeit(person="", fach_id=2, fehler_text="y")
        self.a.muster_setzen("Kontakt F", 1, "Biologie", "Text Kontakt F", 1)
        self.a.muster_setzen("", 2, "Mathematik", "Text Eike", 1)
        namen = [m["person"] for m in self.a.muster_liste()]
        self.assertEqual(namen[0], "")


class MusterRoute(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        self.c = TestClient(app)
        r = self.c.put("/api/noten/fachwahl", json={"faecher": WAHL})
        self.assertEqual(r.status_code, 200, r.text)

    def test_route_zeigt_muster_mit_fachnamen(self):
        bio = next(f for f in self.c.get("/api/noten").json()["faecher"] if f["name"] == "Biologie")
        arbeiten.standard().muster_setzen("Kontakt F", bio["id"], "Biologie", "Wiederholt Vorzeichenfehler.", 3)
        d = self.c.get("/api/arbeiten/muster").json()
        self.assertEqual(len(d["muster"]), 1)
        m = d["muster"][0]
        self.assertEqual((m["person"], m["fach"], m["text"], m["anzahl"]),
                         ("Kontakt F", "Biologie", "Wiederholt Vorzeichenfehler.", 3))


if __name__ == "__main__":
    unittest.main()
