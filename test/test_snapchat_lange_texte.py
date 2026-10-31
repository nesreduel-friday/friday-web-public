"""Lange Antworten: nie mehr still mitten im Satz gekürzt (14.10.2026) — Teilen an Satzgrenzen, harte Obergrenze meldet sich laut."""
import json
import os
import tempfile
import unittest
from unittest import mock

from kern.dienste import snapchat as dienst
from kern.dienste import snapchat_wache as wache
from kern.personen import register


def satz(i):
    return f"[{i}] Das ist der Testsatz Nummer {i}, der ziemlich lang ist und trotzdem vollständig ankommen soll."


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class Teilen(unittest.TestCase):
    def test_kurzer_text_bleibt_ein_teil(self):
        self.assertEqual(dienst.teilen("Hallo du!"), ["Hallo du!"])
        self.assertEqual(dienst.teilen(""), [])
        self.assertEqual(dienst.teilen("   "), [])

    def test_lange_texte_werden_an_satzgrenzen_geteilt_und_nichts_geht_verloren(self):
        text = " ".join(satz(i) for i in range(1, 40))
        teile = dienst.teilen(text, maximal=500)
        self.assertGreater(len(teile), 1)
        self.assertTrue(all(len(t) <= 500 for t in teile))
        self.assertTrue(all(t.rstrip().endswith(".") for t in teile))                   # jeder Teil endet mit einem Satz
        self.assertEqual(" ".join(teile).split(), text.split())                          # kein Wort verloren oder doppelt

    def test_ein_riesiger_satz_ohne_satzzeichen_wird_an_einem_leerzeichen_getrennt(self):
        text = " ".join(f"wort{i}" for i in range(400))
        teile = dienst.teilen(text, maximal=300)
        self.assertTrue(all(len(t) <= 300 for t in teile))
        self.assertEqual(" ".join(teile).split(), text.split())

    def test_ein_wort_laenger_als_das_maximum_wird_hart_getrennt(self):
        teile = dienst.teilen("x" * 650, maximal=300)
        self.assertEqual([len(t) for t in teile], [300, 300, 50])

    def test_absaetze_und_emoji_bleiben_erhalten(self):
        text = "Erster Absatz mit Emoji 😊.\n\nZweiter Absatz."
        self.assertEqual(" ".join(dienst.teilen(text, maximal=30)).split(), text.split())


class Auftrag(Basis):
    def test_lange_antwort_wird_nicht_mehr_gekuerzt(self):
        text = " ".join(satz(i) for i in range(1, 16))                                  # ~1600 Zeichen
        self.assertGreater(len(text), 1000)
        dienst.auftrag_legen("text", text=text)
        gespeichert = [json.loads(p.read_text(encoding="utf-8")) for p in dienst._schlange().glob("*.json")]
        gespeichert += [json.loads(dienst._ordner().joinpath("auftrag.json").read_text(encoding="utf-8"))] if (dienst._ordner() / "auftrag.json").exists() else []
        self.assertEqual(gespeichert[0]["text"], text)

    def test_ueber_dem_maximum_meldet_sich_der_auftrag_laut(self):
        with self.assertRaises(dienst.SnapchatFehler):
            dienst.auftrag_legen("text", text="x " * dienst.AUFTRAG_TEXT_MAX)
        self.assertEqual(list(dienst._schlange().glob("*.json")), [])


class Wache(Basis):
    def test_kurze_antwort_ist_ein_auftrag(self):
        with mock.patch.object(dienst, "auftrag_legen") as m:
            wache._antwort_legen("Hi!", None, 1.0)
        m.assert_called_once_with("text", text="Hi!", ausloeser=1.0)

    def test_sehr_lange_antwort_geht_in_mehreren_teilen_an_die_richtige_person(self):
        text = " ".join(satz(i) for i in range(1, 60))
        with mock.patch.object(dienst, "auftrag_legen") as m:
            wache._antwort_legen(text, "p3", 2.0)
        self.assertGreater(m.call_count, 1)
        self.assertLessEqual(m.call_count, wache.MAX_TEILE)
        self.assertTrue(all(c.kwargs["person"] == "p3" and c.kwargs["ausloeser"] == 2.0 for c in m.call_args_list))
        self.assertEqual(m.call_args_list[0].args, ("text",))

    def test_scheitert_nur_ein_spaeterer_teil_wird_nicht_alles_wiederholt(self):
        text = " ".join(satz(i) for i in range(1, 60))
        aufrufe = []

        def legen(art, **kw):
            aufrufe.append(kw["text"])
            if len(aufrufe) == 2:
                raise dienst.SnapchatFehler("Schlange voll")
        with mock.patch.object(dienst, "auftrag_legen", side_effect=legen):
            wache._antwort_legen(text, None, None)            # wirft nicht: der erste Teil ist schon raus
        self.assertEqual(len(aufrufe), 2)

    def test_scheitert_der_erste_teil_wirft_es_wie_bisher(self):
        with mock.patch.object(dienst, "auftrag_legen", side_effect=dienst.SnapchatFehler("Schlange voll")):
            with self.assertRaises(dienst.SnapchatFehler):
                wache._antwort_legen("Hi!", None, None)

    def test_die_rueckfrage_grenze_passt_zum_neuen_maximum(self):
        self.assertLessEqual(wache.ANTWORT_MAX, dienst.AUFTRAG_TEXT_MAX)
