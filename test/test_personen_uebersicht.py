"""Kontakte-Übersicht: Snapchat-Ampel, letzter Austausch, Arbeitsweisen (13.10.2026)."""
import unittest

from kern.personen import uebersicht

JETZT = 1_800_000_000.0
TAG = 86400


def status(person=None, **kw):
    p = {"id": "p1", "name": "Kontakt B Kontakt H", "kanaele": {"snapchat": "Kontakt B Kontakt H"}, "pausiert": False, **(person or {})}
    a = dict(stand=None, fehlschlag=None, anfrage=None, anschluss_an=True, schreibrecht=True, jetzt=JETZT, austausch=None)
    a.update(kw)
    return uebersicht.snapchat_status(p, **a)


class Ampel(unittest.TestCase):
    def test_befreundet_ist_gruen_nur_mit_beweis(self):
        s = status(stand={"stand": "befreundet", "zeit": JETZT - 3600})
        self.assertEqual(s["stufe"], "gruen")

    def test_austausch_in_beide_richtungen_belegt_die_freundschaft(self):
        # 14.10.2026: „Freundschaft nicht bewiesen“ stand bei Personen, mit denen Friday längst hin und her schreibt.
        beide = {"person": JETZT - 7200, "friday": JETZT - 3600}
        kein = {"stand": "kein_hinweis", "zeit": JETZT - 600}
        s = status(stand=kein, austausch=beide)
        self.assertEqual(s["stufe"], "gruen")
        self.assertEqual(s["titel"], "Kann schreiben")
        self.assertIn("beide Richtungen", s["grund"])

    def test_einseitiger_austausch_belegt_nichts(self):
        kein = {"stand": "kein_hinweis", "zeit": JETZT - 600}
        for austausch in (None, {"person": None, "friday": JETZT - 3600}, {"person": JETZT - 3600, "friday": None}):
            self.assertEqual(status(stand=kein, austausch=austausch)["stufe"], "gelb", austausch)

    def test_snapchat_warnung_schlaegt_den_austausch(self):
        beide = {"person": JETZT - 7200, "friday": JETZT - 3600}
        self.assertEqual(status(stand={"stand": "einseitig", "zeit": JETZT}, austausch=beide)["stufe"], "gelb")

    def test_austausch_belegt_auch_ohne_geprueften_stand_nicht(self):
        beide = {"person": JETZT - 7200, "friday": JETZT - 3600}
        self.assertEqual(status(stand=None, austausch=beide)["stufe"], "gelb")     # ohne Blick ins Handy bleibt es unklar

    def test_ohne_schreibrecht_bleibt_es_gelb(self):
        beide = {"person": JETZT - 7200, "friday": JETZT - 3600}
        s = status(stand={"stand": "kein_hinweis", "zeit": JETZT}, austausch=beide, schreibrecht=False)
        self.assertEqual(s["stufe"], "gelb")

    def test_unklares_ist_nie_gruen(self):
        for stand in (None, {"stand": "kein_hinweis", "zeit": JETZT}, {"stand": "einseitig", "zeit": JETZT}):
            self.assertEqual(status(stand=stand)["stufe"], "gelb")

    def test_ohne_schreibrecht_nicht_gruen(self):
        s = status(stand={"stand": "befreundet", "zeit": JETZT}, schreibrecht=False)
        self.assertEqual(s["stufe"], "gelb")

    def test_pausiert_und_ohne_name_und_anschluss_aus(self):
        self.assertEqual(status({"pausiert": True})["stufe"], "rot")
        self.assertEqual(status({"kanaele": {}})["stufe"], "grau")
        self.assertEqual(status(anschluss_an=False)["stufe"], "rot")

    def test_neuerer_fehlschlag_schlaegt_alten_erfolg(self):
        s = status(stand={"stand": "befreundet", "zeit": JETZT - 5 * TAG}, fehlschlag={"zeit": JETZT - TAG, "grund": "x"})
        self.assertEqual(s["stufe"], "rot")
        s = status(stand={"stand": "befreundet", "zeit": JETZT - TAG}, fehlschlag={"zeit": JETZT - 5 * TAG, "grund": "x"})
        self.assertEqual(s["stufe"], "gruen")

    def test_alte_pruefung_wird_gesagt(self):
        s = status(stand={"stand": "befreundet", "zeit": JETZT - 30 * TAG})
        self.assertIn("älter", s["grund"])

    def test_anfragen(self):
        self.assertEqual(status(anfrage={"zustand": "angenommen"})["titel"], "Anfrage angenommen")
        self.assertEqual(status(anfrage={"zustand": "offen"})["titel"], "Anfrage wartet")


class Austausch(unittest.TestCase):
    def test_letzte_nachricht_und_von_wem(self):
        a = uebersicht.letzter_austausch({"du": "2026-10-13T10:00:00", "friday": "2026-10-13T10:05:00"}, JETZT)
        self.assertEqual(a["von"], "friday")
        self.assertLess(a["person"], a["friday"])

    def test_nichts(self):
        self.assertEqual(uebersicht.letzter_austausch(None, JETZT)["zeit"], None)


class Rechte(unittest.TestCase):
    def test_gruppiert_und_markiert_eikes_daten(self):
        d = {w["name"]: w for w in uebersicht.rechte_gruppiert()}
        self.assertEqual(d["uhrzeit"]["gruppe"], "Allgemein")
        self.assertTrue(d["kalender"]["eikes_daten"])
        self.assertFalse(d["kalender_frei"]["eikes_daten"])

    def test_jedes_recht_hat_eine_gruppe(self):
        from kern.personen import rechte
        self.assertEqual(set(rechte.FREMDEN_ERLAUBT), set(rechte.GRUPPEN))


if __name__ == "__main__":
    unittest.main()
