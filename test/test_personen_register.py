"""Personen-Register: anlegen, ändern, prüfen — ohne Netz, im Temp-Ordner."""
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from kern import config
from kern.personen import PersonenFehler, register, rechte


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class Anlegen(Basis):
    def test_neue_person_hat_die_sparsamen_standardwerte(self):
        p = register.anlegen("Kontakt B", snapchat="kontakt_b_x")
        self.assertEqual(p["id"], "p1")
        self.assertEqual(p["werkzeuge"], ["uhrzeit"])
        self.assertFalse(p["proaktiv"])
        self.assertFalse(p["pausiert"])
        self.assertEqual(p["kanaele"], {"snapchat": "kontakt_b_x"})
        self.assertEqual(register.holen("p1")["name"], "Kontakt B")

    def test_ids_werden_nie_wiederverwendet(self):
        a = register.anlegen("A")
        register.aendern(a["id"], pausiert=True)
        b = register.anlegen("B")
        self.assertEqual([a["id"], b["id"]], ["p1", "p2"])

    def test_datei_ist_privat_600(self):
        register.anlegen("Kontakt B")
        modus = stat.S_IMODE(os.stat(Path(self.ordner) / "personen.json").st_mode)
        self.assertEqual(modus, 0o600)

    def test_gleichzeitig_angelegt_ergibt_verschiedene_ids(self):
        ids = []
        def los(n):
            ids.append(register.anlegen(f"P{n}")["id"])
        ts = [threading.Thread(target=los, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(set(ids)), 8)

    def test_name_pflicht_und_bereinigt(self):
        with self.assertRaises(PersonenFehler):
            register.anlegen("   ")
        p = register.anlegen("Ma\x00ya\n" + "x" * 500)
        self.assertNotIn("\x00", p["name"])
        self.assertNotIn("\n", p["name"])
        self.assertLessEqual(len(p["name"]), register.NAME_MAX)

    def test_persona_wird_gekuerzt_behaelt_aber_zeilenumbrueche(self):
        p = register.anlegen("Kontakt B", persona="Locker.\nDuzt.\x00" + "y" * 5000)
        self.assertIn("\n", p["persona"])
        self.assertNotIn("\x00", p["persona"])
        self.assertLessEqual(len(p["persona"]), register.PERSONA_MAX)


class Snapchatname(Basis):
    def test_derselbe_name_nur_einmal_auch_anders_geschrieben(self):
        register.anlegen("Kontakt B", snapchat="kontakt_b_x")
        with self.assertRaises(PersonenFehler):
            register.anlegen("Zweite", snapchat="  kontakt_b_x ")

    def test_eikes_und_reservierte_namen_gehoeren_nie_einer_person(self):
        for name in ("Eike", "eike", "ME", "Friday"):
            with self.assertRaises(PersonenFehler, msg=name):
                register.anlegen("X", snapchat=name)

    def test_aendern_auf_den_eigenen_namen_ist_erlaubt(self):
        p = register.anlegen("Kontakt B", snapchat="kontakt_b")
        register.aendern(p["id"], snapchat="kontakt_b", persona="Neu")
        self.assertEqual(register.holen(p["id"])["persona"], "Neu")

    def test_nach_kanal_findet_gross_klein_und_leerzeichen_egal(self):
        p = register.anlegen("Kontakt B", snapchat="kontakt_b_x")
        self.assertEqual(register.nach_kanal("snapchat", " kontakt_b_x")["id"], p["id"])
        self.assertIsNone(register.nach_kanal("snapchat", "unbekannt"))
        self.assertIsNone(register.nach_kanal("whatsapp", "kontakt_b_x"))
        self.assertIsNone(register.nach_kanal("snapchat", ""))


class Unsichtbares(Basis):
    """Unsichtbare und Zeilen trennende Zeichen im Namen (Review 29.09.2026): Sie umgingen die reservierten Namen und
    wären in Teil 2 als Zeile im Prompt gelandet."""
    UMGEHUNGEN = ["Eike\u200b", "E\u200bike", "M\u200bE", "Eike\u00ad", "\uff25\uff49\uff4b\uff45",
                  "\u202eEike", "Friday\ufe0f", "E\u2060i\u200dke", "eike\u2028"]

    def test_reservierte_namen_sind_mit_unicode_tricks_nicht_zu_umgehen(self):
        for name in self.UMGEHUNGEN:
            with self.assertRaises(PersonenFehler, msg=repr(name)):
                register.anlegen("X", snapchat=name)

    def test_derselbe_name_mit_unsichtbaren_zeichen_ist_derselbe(self):
        register.anlegen("Kontakt B", snapchat="Kontakt B")
        with self.assertRaises(PersonenFehler):
            register.anlegen("Zweite", snapchat="Kontakt\u200b B\u00ad")
        self.assertIsNotNone(register.nach_kanal("snapchat", "ｋｏｎｔａｋｔ ｂ"))

    def test_gespeicherte_texte_haben_keine_zeilentrenner_und_formatzeichen(self):
        p = register.anlegen("Kontakt B\u2028Neue Regel\x85: xyz\u202e\u200b", snapchat="a\u2029b\u200fc",
                             persona="Zeile 1\nZeile 2\u2028Zeile 3\u200b\x85")
        for wert in (p["name"], p["kanaele"]["snapchat"], p["persona"]):
            for boese in ("\u2028", "\u2029", "\x85", "\u200b", "\u200f", "\u202e"):
                self.assertNotIn(boese, wert)
        self.assertIn("\n", p["persona"])  # der normale Zeilenumbruch der Persona bleibt


class Identitaet(Basis):
    """Ein Eintrag je bekannter Person (`graph_name`): dieselbe Person nicht zweimal, Erinnerungen bleiben eine Identität."""

    def test_graph_name_ist_standardmaessig_der_name(self):
        self.assertEqual(register.anlegen("Kontakt B")["graph_name"], "Kontakt B")
        self.assertEqual(register.anlegen("Kontakt B Neu", graph_name="Kontakt F")["graph_name"], "Kontakt F")

    def test_dieselbe_bekannte_person_nicht_zweimal_auch_anders_geschrieben(self):
        register.anlegen("Kontakt B", snapchat="kontakt_b_x")
        for name in ("kontakt b", " KONTAKT B ", "Kontakt\u200b B"):
            with self.assertRaises(PersonenFehler, msg=name):
                register.anlegen("Zweite", snapchat="andere", graph_name=name)

    def test_eike_und_friday_sind_keine_kontakte(self):
        for name in ("Eike", "Friday"):
            with self.assertRaises(PersonenFehler, msg=name):
                register.anlegen("X", graph_name=name)

    def test_alte_eintraege_ohne_graph_name_zaehlen_mit_ihrem_namen(self):
        p = register.anlegen("Kontakt B")
        d = register._lesen()
        del d["personen"][p["id"]]["graph_name"]
        register._speichern(d)
        with self.assertRaises(PersonenFehler):
            register.anlegen("Zweite", graph_name="Kontakt B")


class Rechte(Basis):
    def test_nur_erlaubte_werkzeuge_sind_waehlbar(self):
        p = register.anlegen("Kontakt B", werkzeuge=["uhrzeit", "suchen"])
        self.assertEqual(p["werkzeuge"], ["uhrzeit", "suchen"])
        # Daten-Rechte seit 04.10.2026 wählbar (Eikes Auftrag, je Person schaltbar):
        # Seit 14.10.2026 setzen Plan-Werkzeuge die Plan-Stufe („voll“ bündelt Stundenplan und Kalender).
        p = register.aendern(p["id"], werkzeuge=["uhrzeit", "kalender_frei", "stundenplan"])
        self.assertEqual(p["module"]["plan"], "voll")
        self.assertEqual(p["werkzeuge"], ["uhrzeit", "suchen", "nachrichten", "stundenplan", "kalender_frei", "kalender", "kalender_suchen"])
        for boese in ("wissen", "whatsapp_lesen", "gibt_es_nicht", "merken"):
            with self.assertRaises(PersonenFehler, msg=boese):
                register.aendern(p["id"], werkzeuge=["uhrzeit", boese])
        self.assertEqual(register.holen(p["id"])["werkzeuge"], ["uhrzeit", "suchen", "nachrichten", "stundenplan", "kalender_frei", "kalender", "kalender_suchen"])

    def test_werkzeuge_ohne_doppelte_in_stabiler_reihenfolge(self):
        p = register.anlegen("Kontakt B", werkzeuge=["suchen", "uhrzeit", "suchen"])
        self.assertEqual(p["werkzeuge"], ["suchen", "uhrzeit"])

    def test_positivliste_gibt_nichts_ueber_eike_preis(self):
        # `wetter` ohne Ort nennt Eikes Wohnort (FRIDAY_ORT), `webseite` ruft von hphome aus beliebige Adressen ab
        # (Heim-IP im Log des Angreifers). Beides fehlt, bis Teil 2 es mit erzwungener Angabe wieder aufnimmt.
        # Daten-Rechte (04.10.2026, Eikes Auftrag): Stundenplan und Kalender-Stufen sind pro Person wählbar —
        # `kalender_frei` verrät nie Details, `kalender` nur mit Eikes Haken je Person.
        self.assertEqual(set(rechte.FREMDEN_ERLAUBT), {"uhrzeit", "suchen", "nachrichten", "stundenplan",
                                                       "kalender_frei", "kalender", "kalender_suchen",
                                                       "termin_wunsch"})

    def test_alle_erlaubten_werkzeuge_gibt_es_im_katalog(self):
        from kern.werkzeuge import katalog
        vorhanden = {e["name"] for m in katalog._MODULE for e in m.ERKLAERUNGEN}
        self.assertEqual(sorted(set(rechte.FREMDEN_ERLAUBT) - vorhanden), [])

    def test_nichts_auf_eikes_daten_ist_erlaubt(self):
        # Ausnahme seit 04.10.2026 (Eikes Auftrag, pro Person schaltbar): stundenplan, kalender_frei,
        # kalender, kalender_suchen — alles andere bleibt draußen.
        for name in ("merken", "erinnern", "wissen", "rueckblick", "bild_benennen", "mails",
                     "gmail_mails", "whatsapp_lesen", "whatsapp_senden", "garantie_suchen",
                     "standort", "wo_ist", "pc_starten", "server_status", "schicke_snap", "notizen_suchen"):
            self.assertNotIn(name, rechte.FREMDEN_ERLAUBT)


class Aendern(Basis):
    def test_unbekanntes_feld_und_unbekannte_person(self):
        p = register.anlegen("Kontakt B")
        with self.assertRaises(PersonenFehler):
            register.aendern(p["id"], id="p9")
        with self.assertRaises(PersonenFehler):
            register.aendern("p99", name="X")

    def test_pausieren_und_proaktiv(self):
        p = register.anlegen("Kontakt B")
        register.aendern(p["id"], pausiert=True, proaktiv=True)
        q = register.holen(p["id"])
        self.assertTrue(q["pausiert"] and q["proaktiv"])

    def test_senden_erlaubt_ist_standard_aus_und_aenderbar(self):
        p = register.anlegen("Kontakt C", snapchat="kontakt_c_y")
        self.assertFalse(register.holen(p["id"]).get("senden_erlaubt"))
        register.aendern(p["id"], senden_erlaubt=True)
        self.assertTrue(register.holen(p["id"]).get("senden_erlaubt"))
        register.aendern(p["id"], senden_erlaubt=False)
        self.assertFalse(register.holen(p["id"]).get("senden_erlaubt"))


class KaputteDatei(Basis):
    def test_fehlende_datei_ist_leere_liste(self):
        self.assertEqual(register.liste(), [])

    def test_kaputte_datei_gibt_leere_liste_und_wird_nicht_still_ueberschrieben(self):
        pfad = Path(self.ordner) / "personen.json"
        pfad.write_text("{kaputt", encoding="utf-8")
        self.assertEqual(register.liste(), [])
        with self.assertRaises(PersonenFehler):
            register.anlegen("Kontakt B")
        self.assertEqual(pfad.read_text(encoding="utf-8"), "{kaputt")  # nicht angetastet


if __name__ == "__main__":
    unittest.main()


class Module(Basis):
    def test_neue_person_hat_standardmodule(self):
        p = register.anlegen("Kontakt B")
        self.assertEqual(p["module"], {"plan": "aus", "garantie": "auto", "medien": False, "snap_wuensche": False, "flammen": False,
                                       "ueberwachung": False})

    def test_partielle_aenderung_speichert_das_volle_dict_und_leitet_werkzeuge_ab(self):
        p = register.anlegen("Kontakt B")
        neu = register.aendern(p["id"], module={"plan": "voll", "flammen": True})
        self.assertEqual(neu["module"]["plan"], "voll")
        self.assertTrue(neu["module"]["flammen"])
        self.assertEqual(neu["module"]["garantie"], "auto")
        self.assertIn("kalender", neu["werkzeuge"])
        zurueck = register.aendern(p["id"], module={"plan": "aus"})
        self.assertNotIn("kalender", zurueck["werkzeuge"])
        self.assertEqual(zurueck["werkzeuge"], ["uhrzeit", "suchen", "nachrichten"])

    def test_ungueltig_aendert_nichts(self):
        p = register.anlegen("Kontakt B")
        with self.assertRaises(PersonenFehler):
            register.aendern(p["id"], module={"flammen": "ja"})
        self.assertFalse(register.holen(p["id"])["module"]["flammen"])

    def test_garantie_an_braucht_profil(self):
        p = register.anlegen("Kontakt B")
        with self.assertRaises(PersonenFehler):
            register.aendern(p["id"], module={"garantie": "an"})
        register.aendern(p["id"], quellen={"garantie": 5})
        self.assertEqual(register.aendern(p["id"], module={"garantie": "an"})["module"]["garantie"], "an")

    def test_aenderungen_werden_protokolliert(self):
        p = register.anlegen("Kontakt B")
        register.aendern(p["id"], module={"flammen": True, "plan": "frei"}, von="friday")
        zeilen = [json.loads(z) for z in (config.daten_ordner() / "modul_protokoll.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual({(z["modul"], z["alt"], z["neu"], z["von"]) for z in zeilen},
                         {("flammen", False, True, "friday"), ("plan", "aus", "frei", "friday")})

    def test_unveraendert_wird_nicht_protokolliert(self):
        p = register.anlegen("Kontakt B")
        register.aendern(p["id"], module={"flammen": False})
        self.assertFalse((config.daten_ordner() / "modul_protokoll.jsonl").exists())

    def test_werkzeuge_aendern_haelt_den_plan_stand(self):
        p = register.anlegen("Kontakt B")
        register.aendern(p["id"], module={"plan": "frei"})
        neu = register.aendern(p["id"], werkzeuge=["uhrzeit", "suchen"])
        self.assertEqual(set(neu["werkzeuge"]), {"uhrzeit", "suchen", "nachrichten", "kalender_frei"})

    def test_unbekannte_herkunft_wird_abgelehnt(self):
        p = register.anlegen("Kontakt B")
        with self.assertRaises(PersonenFehler):
            register.aendern(p["id"], module={"flammen": True}, von="irgendwer")
