"""Eine alte, schon beantwortete Nachricht bleibt im Chat stehen (Snapchat hat sie nicht gelöscht): Der Helfer darf sie nicht
bei jedem Blick als neu lesen — sonst antwortet Friday 25 Mal auf dasselbe „Ja“ (Vorfall 14./15.10.2026, „Sir?“)."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host-helfer"))
import snap_helfer  # noqa: E402


def chat(*zeilen):
    """Ein Chat-Bildschirm: je Eintrag (Absender-Label, Text) — „ME“ sind Fridays eigene Nachrichten."""
    knoten = "".join(f'<node text="{label}" bounds="[0,0][1,1]"/><node text="{text}" bounds="[0,0][1,1]"/>' for label, text in zeilen)
    return f"<hierarchy>{knoten}</hierarchy>"


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.sperre = mock.patch.object(snap_helfer.subprocess, "run", side_effect=AssertionError("kein echtes adb"))
        self.sperre.start()
        self.addCleanup(self.sperre.stop)
        self.addCleanup(self.env.stop)

    def eingang(self, *eintraege):
        snap_helfer._schreiben("eingang.json", [
            {"id": f"x{i}", "text": t, "art": "text", "zeit": zeit, "gelesen": True, **({"person": p} if p else {})}
            for i, (t, zeit, p) in enumerate(eintraege)])

    def lesen(self, dump):
        with mock.patch.object(snap_helfer, "schritt_ausfuehren"), mock.patch.object(snap_helfer, "kontakt_chat_oeffnen"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=dump), \
             mock.patch.object(snap_helfer, "_chat_tab_oeffnen"), mock.patch.object(snap_helfer, "snaps_abholen"), \
             mock.patch.object(snap_helfer, "symbole_vermerken"), mock.patch.object(snap_helfer, "chat_bilder_holen"):
            return snap_helfer._eingang_pruefen_ablauf("")[0]


class Lage(unittest.TestCase):
    def test_texte_ueber_und_unter_der_letzten_eigenen_nachricht(self):
        dump = chat(("EIKE", "Ja"), ("ME", "Sir?"), ("EIKE", "Nein"))
        self.assertEqual(snap_helfer.fremde_texte_mit_lage(dump), [("Ja", True), ("Nein", False)])

    def test_ohne_eigene_nachricht_ist_nichts_darueber(self):
        self.assertEqual(snap_helfer.fremde_texte_mit_lage(chat(("EIKE", "Hey"), ("EIKE", "Hey"))), [("Hey", False), ("Hey", False)])

    def test_fremde_texte_bleibt_wie_es_war(self):
        self.assertEqual(snap_helfer.fremde_texte(chat(("EIKE", "Ja"), ("ME", "Sir?"), ("EIKE", "Nein"))), ["Ja", "Nein"])


class Eike(Basis):
    def test_alte_schon_gelesene_nachricht_ueber_fridays_antwort_ist_nicht_neu(self):
        self.eingang(("Ja", time.time() - 600, None))
        self.assertEqual(self.lesen(chat(("EIKE", "Ja"), ("ME", "Sir?"))), [])

    def test_immer_wieder_derselbe_blick_bleibt_leer(self):
        self.eingang(("Ja", time.time() - 600, None))
        dump = chat(("EIKE", "Ja"), ("ME", "Sir?"))
        for _ in range(4):
            self.assertEqual(self.lesen(dump), [])

    def test_noch_nie_gelesene_nachricht_ueber_der_antwort_geht_nicht_verloren(self):
        # Friday hat geantwortet, während „Dringend“ schon im Chat stand, aber noch nicht eingelesen war.
        self.eingang(("Ja", time.time() - 600, None))
        self.assertEqual(self.lesen(chat(("EIKE", "Dringend"), ("ME", "Sir?"))), ["Dringend"])

    def test_neue_nachricht_unter_der_antwort_ist_neu_auch_bei_gleichem_text(self):
        self.eingang(("Ja", time.time() - 600, None))
        self.assertEqual(self.lesen(chat(("EIKE", "Ja"), ("ME", "Sir?"), ("EIKE", "Ja"))), ["Ja"])

    def test_gleichlautende_nachricht_zweimal_ohne_antwort_dazwischen_bleibt_zweimal_neu(self):
        self.eingang(("Hey", time.time() - 600, None))
        self.assertEqual(self.lesen(chat(("EIKE", "Hey"))), ["Hey"])

    def test_sehr_alte_gleichlautende_nachricht_zaehlt_nicht_als_gelesen(self):
        self.eingang(("Ja", time.time() - 3 * 86400, None))
        self.assertEqual(self.lesen(chat(("EIKE", "Ja"), ("ME", "Sir?"))), ["Ja"])

    def test_was_eine_andere_person_geschrieben_hat_zaehlt_nicht_als_von_eike_gelesen(self):
        self.eingang(("Ja", time.time() - 600, "p3"))
        self.assertEqual(self.lesen(chat(("EIKE", "Ja"), ("ME", "Sir?"))), ["Ja"])


class Kontakt(Basis):
    def test_auch_bei_anderen_kontakten_wird_nichts_doppelt_gelesen(self):
        self.eingang(("Hallo", time.time() - 600, "p3"))
        snap_helfer.kontakt_zustand_schreiben("Kontakt C", {"sichtbar": ["Hallo"], "bilder": []})
        dump = chat(("KONTAKT C", "Hallo"), ("ME", "Hi!"))
        with mock.patch.object(snap_helfer, "freundschaft_vermerken"):
            n = snap_helfer._kontakt_mitlesen("Kontakt C", "p3", dump)
        self.assertEqual(n, 0)

    def test_neues_bei_einem_kontakt_wird_gelesen(self):
        self.eingang(("Hallo", time.time() - 600, "p3"))
        snap_helfer.kontakt_zustand_schreiben("Kontakt C", {"sichtbar": ["Hallo"], "bilder": []})
        dump = chat(("KONTAKT C", "Hallo"), ("ME", "Hi!"), ("KONTAKT C", "Wie gehts"))
        with mock.patch.object(snap_helfer, "freundschaft_vermerken"):
            n = snap_helfer._kontakt_mitlesen("Kontakt C", "p3", dump)
        self.assertEqual(n, 1)
