import asyncio, json, os, tempfile, time, unittest
from unittest import mock
from pathlib import Path

from kern import konnektoren
from kern.dienste import snapchat as dienst
from kern.personen import register


def lauf(coro):
    return asyncio.run(coro) if asyncio.iscoroutine(coro) else coro


class KonnektorTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_freigabetext_verspricht_kein_ja_mehr_fuer_nachrichten(self):
        """Review 07.10.2026: Der Schalter „Schreiben“ hieß noch „Kontakt-Personen nach deinem Ja“ — Nachrichten gehen jetzt direkt."""
        text = konnektoren.KONNEKTOREN["snapchat"]["schreiben_text"]
        self.assertNotIn("nach deinem Ja", text)
        self.assertIn("direkt", text)

    def test_eintrag_hat_pflichtfelder(self):
        eintrag = konnektoren.KONNEKTOREN["snapchat"]
        for feld in ("name", "beschreibung", "zugang", "hinweis", "werkzeuge"):
            self.assertIn(feld, eintrag)
        self.assertTrue(eintrag["ohne_zugang"])

    def test_ohne_freigabe_nicht_benutzbar(self):
        konnektoren.setzen("snapchat", False)
        self.assertFalse(konnektoren.benutzbar("snapchat"))

    def test_mit_freigabe_benutzbar_ohne_zugangsdaten(self):
        konnektoren.setzen("snapchat", True)
        self.assertTrue(konnektoren.benutzbar("snapchat"))


class DienstTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_auftrag_legen_und_lesen(self):
        dienst.auftrag_legen("snap", text="Na, alles klar?", bild_pfad="/tmp/x.jpg")
        auftrag = dienst.auftrag_lesen()
        self.assertEqual(auftrag["art"], "snap")
        self.assertEqual(auftrag["bild_pfad"], "/tmp/x.jpg")

    def test_zweiter_auftrag_wartet_in_der_schlange_statt_zu_scheitern(self):
        dienst.auftrag_legen("text", text="Hallo")
        dienst.auftrag_legen("text", text="Nochmal")
        self.assertEqual(dienst.auftrag_lesen()["text"], "Hallo")     # der laufende bleibt der laufende
        self.assertEqual(dienst.auftraege_anzahl(), 2)

    def test_schlange_ist_begrenzt(self):
        dienst.auftrag_legen("text", text="laufend")
        for i in range(dienst.QUEUE_MAX):
            dienst.auftrag_legen("text", text=f"wartet {i}")
        with self.assertRaises(dienst.SnapchatFehler):
            dienst.auftrag_legen("text", text="zu viel")
        self.assertEqual(dienst.auftraege_anzahl(), dienst.QUEUE_MAX + 1)

    def test_snap_wartet_sieht_laufenden_und_wartende(self):
        self.assertFalse(dienst.snap_wartet())
        dienst.auftrag_legen("text", text="Hallo")
        self.assertFalse(dienst.snap_wartet())
        dienst.auftrag_legen("snap", bild_pfad="snapchat/ausgang/a.jpg")     # wartet hinter dem Text
        self.assertTrue(dienst.snap_wartet())

    def test_faden_und_kontext_stehen_im_snapchat_faden(self):
        from kern.protokoll import speicher as pspeicher
        self.assertIsNone(dienst.faden_vorhanden())
        dienst.kontext_vermerken("[Ich habe Eike gerade einen Snap geschickt: Fuchs.]")
        g = dienst.faden_vorhanden()
        self.assertIsNotNone(g)
        self.assertEqual(dienst.verlauf(), [{"rolle": "friday", "text": "[Ich habe Eike gerade einen Snap geschickt: Fuchs.]"}])
        self.assertEqual(dienst.faden(), g)          # derselbe Faden, kein neuer
        for k in list(pspeicher._instanzen):
            pspeicher._instanzen.pop(k)._db.close()

    def test_gesendete_snaps_gelten_zwoelf_stunden_auch_ohne_faden(self):
        from kern.protokoll import speicher as pspeicher
        dienst.kontext_vermerken("[Ich habe Eike gerade einen Snap geschickt: ein Fuchs; Text darauf: „Morgen“.]")
        jetzt = time.time()
        from datetime import datetime, timedelta, timezone
        spaeter = datetime.now(timezone.utc) + timedelta(hours=5)
        with mock.patch.object(dienst.time, "time", return_value=jetzt + 5 * 3600), \
             mock.patch.object(dienst, "datetime") as m_datum:                              # Faden längst zu
            m_datum.now.return_value = spaeter
            block = dienst.gesendet_kontext()
            self.assertIn("vor 5 Std.", block)
            self.assertIn("ein Fuchs", block)
            self.assertIn("Morgen", block)
            self.assertEqual(dienst.verlauf(), [])                  # der 25-Minuten-Verlauf ist weg, der Block bleibt
        with mock.patch.object(dienst.time, "time", return_value=jetzt + 13 * 3600):
            self.assertEqual(dienst.gesendet_kontext(), "")          # nach 12 Stunden darf sie es vergessen haben
        for k in list(pspeicher._instanzen):
            pspeicher._instanzen.pop(k)._db.close()

    def test_gesendet_kontext_zeigt_neueste_zuerst_und_ist_begrenzt(self):
        from kern.protokoll import speicher as pspeicher
        for i in range(dienst.GESENDET_MAX + 5):
            dienst.kontext_vermerken(f"[Snap Nummer {i}]")
        block = dienst.gesendet_kontext()
        self.assertLess(block.index(f"Snap Nummer {dienst.GESENDET_MAX + 4}"), block.index("Snap Nummer 10"))
        self.assertNotIn("Snap Nummer 0\n", block + "\n")
        for k in list(pspeicher._instanzen):
            pspeicher._instanzen.pop(k)._db.close()

    def test_ohne_snaps_ist_der_block_leer(self):
        self.assertEqual(dienst.gesendet_kontext(), "")

    def test_kontext_wird_nach_der_ruhezeit_vergessen(self):
        from datetime import datetime, timedelta, timezone
        from kern.protokoll import speicher as pspeicher
        dienst.kontext_vermerken("[Snap geschickt]")
        spaeter = datetime.now(timezone.utc) + timedelta(minutes=dienst.RUHE_MINUTEN + 5)
        with mock.patch.object(dienst, "datetime") as m_datum:
            m_datum.now.return_value = spaeter
            self.assertEqual(dienst.verlauf(), [])
        for k in list(pspeicher._instanzen):
            pspeicher._instanzen.pop(k)._db.close()

    def test_unbekannte_art_scheitert(self):
        with self.assertRaises(dienst.SnapchatFehler):
            dienst.auftrag_legen("video")

    def test_bild_als_jpeg_haelt_auch_ein_grosses_foto_unter_der_bildgrenze(self):
        # Ein Foto als PNG (RGBA, 1080x2016) ist mehrere MB groß und wurde am 29.09.2026 von
        # `bilder.speichern` wegen MAX_BYTES stillschweigend nicht übernommen.
        from io import BytesIO
        from PIL import Image
        from kern.gedaechtnis import bilder
        rauschen = Image.frombytes("RGBA", (1080, 2016), os.urandom(1080 * 2016 * 4))
        aus = BytesIO()
        rauschen.save(aus, "PNG")
        png = aus.getvalue()
        self.assertGreater(len(png), bilder.MAX_BYTES)
        jpeg = dienst.bild_als_jpeg(png)
        self.assertEqual(jpeg[:2], b"\xff\xd8")
        self.assertLess(len(jpeg), bilder.MAX_BYTES)

    def test_bild_lesen_nur_gueltige_namen(self):
        ordner = dienst._ordner() / "bilder"
        ordner.mkdir(parents=True, exist_ok=True)
        name = "a" * 32 + ".png"
        (ordner / name).write_bytes(b"png")
        self.assertEqual(dienst.bild_lesen(name), b"png")
        self.assertIsNone(dienst.bild_lesen("../konnektoren.json"))
        self.assertIsNone(dienst.bild_lesen("x.png"))
        dienst.bild_entfernen(name)
        self.assertIsNone(dienst.bild_lesen(name))

    def test_sende_grenze_greift(self):
        # Die echte Grenze ist praktisch aufgehoben (9999) — der Mechanismus wird mit einer kleinen geprüft.
        with mock.patch.object(dienst, "SENDE_GRENZE", 3):
            for i in range(3):
                dienst.auftrag_legen("text", text=f"Nr {i}")
                # Auftrag „abholen" simulieren, damit der nächste nicht am Einzel-Auftrag-Limit scheitert
                (Path(self.ordner) / "snapchat" / "auftrag.json").unlink()
            with self.assertRaises(dienst.SnapchatFehler):
                dienst.auftrag_legen("text", text="Zu viel")

    def test_zustand_ohne_helfer_ehrlich(self):
        z = dienst.zustand()
        self.assertFalse(z["verbunden"])

    def test_eingang_gelesen_markieren_ist_idempotent(self):
        (Path(self.ordner) / "snapchat").mkdir(parents=True)
        (Path(self.ordner) / "snapchat" / "eingang.json").write_text(
            '[{"id": "a1", "text": "Hi", "zeit": 1.0, "gelesen": false}]', encoding="utf-8")
        dienst.eingang_gelesen_markieren(["a1"])
        dienst.eingang_gelesen_markieren(["a1"])  # zweimal, darf nicht knallen
        self.assertEqual(dienst.eingang_neu(), [])


class AuftragsIdTest(unittest.TestCase):
    """07.10.2026: jeder Auftrag trägt eine ID; ein ausdrücklich „einmaliger“ Auftrag (schicke_nachricht) wird nicht doppelt gelegt."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.p = register.anlegen("Kontakt C", snapchat="kontakt_c_y")["id"]

    def tearDown(self):
        self.env.stop()

    def test_auftrag_hat_id_und_einmaliger_wird_nicht_doppelt_gelegt(self):
        dienst.auftrag_legen("text", text="Hallo", person=self.p, einmalig=True)
        self.assertRegex(dienst.auftrag_lesen()["id"], r"^[0-9a-f]{32}$")
        with self.assertRaises(dienst.SnapchatFehler) as ctx:
            dienst.auftrag_legen("text", text="Hallo", person=self.p, einmalig=True)
        self.assertIn("schon geschickt", str(ctx.exception))
        dienst.auftrag_legen("text", text="Etwas anderes", person=self.p, einmalig=True)   # anderer Text: erlaubt

    def test_auftrag_legen_gibt_die_id_des_auftrags_zurueck(self):
        auftrag_id = dienst.auftrag_legen("text", text="Hallo")
        self.assertRegex(auftrag_id, r"^[0-9a-f]{32}$")

    def test_ergebnis_fuer_findet_die_zeile_mit_der_id(self):
        import json
        ordner = dienst._ordner()
        (ordner / "ergebnisse.jsonl").write_text(
            json.dumps({"id": "aaa", "ergebnis": "gesendet", "zeit": 1.0}) + "\n"
            + json.dumps({"id": "bbb", "ergebnis": "aufgegeben", "zeit": 2.0, "grund": "kein Snap"}) + "\n"
            + json.dumps({"ergebnis": "gesendet", "zeit": 3.0}) + "\n", encoding="utf-8")
        self.assertEqual(dienst.ergebnis_fuer("bbb")["ergebnis"], "aufgegeben")
        self.assertIsNone(dienst.ergebnis_fuer("ccc"))
        self.assertIsNone(dienst.ergebnis_fuer(""))

    def test_ergebnis_fuer_ohne_datei_ist_none(self):
        self.assertIsNone(dienst.ergebnis_fuer("aaa"))

    def test_gespraechsantworten_sind_keine_dubletten(self):
        dienst.auftrag_legen("text", text="Moin", person=self.p)
        dienst.auftrag_legen("text", text="Moin", person=self.p)      # ohne einmalig: wie bisher

    def test_dublette_gilt_nur_kurz(self):
        dienst.auftrag_legen("text", text="Hallo", person=self.p, einmalig=True)
        with mock.patch.object(dienst.time, "time", return_value=time.time() + dienst.DUBLETTE_S + 1):
            dienst.auftrag_legen("text", text="Hallo", person=self.p, einmalig=True)

    def test_gescheiterter_auftrag_hinterlaesst_keinen_dublettenvermerk(self):
        dienst.auftrag_legen("text", text="laufend", person=self.p)
        for i in range(dienst.QUEUE_MAX):
            dienst.auftrag_legen("text", text=f"wartet {i}", person=self.p)
        with self.assertRaises(dienst.SnapchatFehler) as ctx:
            dienst.auftrag_legen("text", text="Hallo", person=self.p, einmalig=True)
        self.assertNotIn("schon geschickt", str(ctx.exception))        # Schlange voll, nichts gelegt
        (dienst._ordner() / "auftrag.json").unlink()
        for d in dienst._schlange().glob("*.json"):
            d.unlink()
        dienst.auftrag_legen("text", text="Hallo", person=self.p, einmalig=True)     # darf jetzt raus

    def test_dublette_ignoriert_satzzeichen_und_grossschreibung(self):
        """Live 07.10.2026: Das Modell formulierte dieselbe Bitte beim zweiten Mal nur mit Punkt am Ende — kein Grund für einen zweiten Versand."""
        dienst.auftrag_legen("text", text="Eike lässt dir ausrichten: Wir sehen uns morgen", person=self.p, einmalig=True)
        with self.assertRaises(dienst.SnapchatFehler):
            dienst.auftrag_legen("text", text="eike lässt dir ausrichten — wir sehen uns  morgen.", person=self.p, einmalig=True)

    def test_ausloeser_wird_mitgeschrieben(self):
        dienst.auftrag_legen("text", text="Hallo", person=self.p, ausloeser=1234.5)
        self.assertEqual(dienst.auftrag_lesen()["ausloeser"], 1234.5)

    def test_ohne_ausloeser_kein_feld(self):
        dienst.auftrag_legen("text", text="Hallo", person=self.p)
        self.assertNotIn("ausloeser", dienst.auftrag_lesen())

    def test_gleicher_text_an_andere_person_ist_keine_dublette(self):
        q = register.anlegen("Kontakt B", snapchat="maya_x")["id"]
        dienst.auftrag_legen("text", text="Hallo", person=self.p, einmalig=True)
        dienst.auftrag_legen("text", text="Hallo", person=q, einmalig=True)


class AnfragenParallelTest(unittest.TestCase):
    """07.10.2026: Lesen-Ändern-Schreiben auf anfragen.json lief ohne Sperre — parallele Anfragen überschrieben sich."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_parallele_anfragen_gehen_nicht_verloren(self):
        import threading
        fehler = []

        def los(n):
            try:
                dienst.anfrage_legen(f"person{n}")
            except Exception as exc:        # pragma: no cover - nur bei Fehlern
                fehler.append(exc)
        ts = [threading.Thread(target=los, args=(i,)) for i in range(40)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(fehler, [])
        self.assertEqual(len(dienst.anfragen()), 40)

    def test_fremde_haben_keine_sende_werkzeuge(self):
        from kern.personen import rechte
        for n in ("kontakt_anfragen", "anfrage_senden", "anfrage_auswaehlen", "schicke_nachricht", "schicke_snap"):
            self.assertNotIn(n, rechte.FREMDEN_ERLAUBT)


class EmpfaengerTest(unittest.TestCase):
    """Aufträge tragen ihren Empfänger; die Kontaktliste für den Helfer wird vom Dienst geschrieben."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.p = register.anlegen("Kontakt B", snapchat="maya_x")["id"]

    def tearDown(self):
        self.env.stop()

    def _auftrag(self):
        return json.loads((dienst._ordner() / "auftrag.json").read_text(encoding="utf-8"))

    def test_ohne_person_geht_der_auftrag_an_eike(self):
        dienst.auftrag_legen("text", text="Hi")
        a = self._auftrag()
        self.assertEqual((a["empfaenger"], a["person"]), ("Eike", None))

    def test_mit_person_geht_er_an_ihren_snapchat_namen(self):
        dienst.auftrag_legen("text", text="Hi", person=self.p)
        a = self._auftrag()
        self.assertEqual((a["empfaenger"], a["person"]), ("maya_x", self.p))

    def test_eikes_name_ist_einstellbar(self):
        with mock.patch.dict(os.environ, {"SNAPCHAT_EIKE_NAME": "Eike D"}):
            dienst.auftrag_legen("text", text="Hi")
        self.assertEqual(self._auftrag()["empfaenger"], "Eike D")

    def test_kontaktliste_wird_geschrieben_nur_mit_aktiven_personen_mit_snapchat_namen(self):
        register.anlegen("Ohne Snapchat")
        pausiert = register.anlegen("Kontakt F", snapchat="kontakt_f")["id"]
        register.aendern(pausiert, pausiert=True)
        dienst.auftrag_legen("text", text="Hi")
        d = json.loads((dienst._ordner() / "kontakte.json").read_text(encoding="utf-8"))
        self.assertEqual([(k["anzeige"], k["person"]) for k in d["kontakte"]], [("Eike", None), ("maya_x", self.p)])

    def test_unbekannte_pausierte_und_snapchatlose_personen_bekommen_keinen_auftrag(self):
        ohne = register.anlegen("Ohne Snapchat")["id"]
        pausiert = register.anlegen("Kontakt F", snapchat="kontakt_f")["id"]
        register.aendern(pausiert, pausiert=True)
        for pid in ("p99", ohne, pausiert, "eike", "p1;", ""):
            with self.assertRaises(dienst.SnapchatFehler, msg=pid):
                dienst.auftrag_legen("text", text="Hi", person=pid)
        self.assertEqual(dienst.auftraege_anzahl(), 0)              # nichts liegt vor dem Handy

    def test_kontaktliste_wird_nur_bei_aenderung_neu_geschrieben(self):
        dienst.kontakte_schreiben()
        datei = dienst._ordner() / "kontakte.json"
        erste = datei.read_bytes()
        with mock.patch("time.time", return_value=time.time() + 100):
            dienst.kontakte_schreiben()                        # nichts geändert: dieselbe Datei, kein neuer Zeitstempel
        self.assertEqual(datei.read_bytes(), erste)
        register.aendern(self.p, pausiert=True)
        dienst.kontakte_schreiben()
        self.assertNotEqual(datei.read_bytes(), erste)
        self.assertEqual([k["anzeige"] for k in json.loads(datei.read_text(encoding="utf-8"))["kontakte"]], ["Eike"])

    def test_bestehende_aufrufe_ohne_person_bleiben_gueltig(self):
        dienst.auftrag_legen("snap", text="Na?", bild_pfad="snapchat/ausgang/a.jpg")
        self.assertEqual(self._auftrag()["empfaenger"], "Eike")


class FaedenJePersonTest(unittest.TestCase):
    """Jede Person und Eike haben je einen eigenen Faden und Verlauf — nichts vermischt sich."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.a = register.anlegen("Kontakt B", snapchat="maya_x")["id"]
        self.b = register.anlegen("Kontakt F", snapchat="paul_y")["id"]

    def tearDown(self):
        from kern.protokoll import speicher as pspeicher
        for k in list(pspeicher._instanzen):
            try:
                pspeicher._instanzen.pop(k)._db.close()
            except Exception:
                pass
        self.env.stop()

    def _beitraege(self, person, du, friday):
        from kern.protokoll import speicher as pspeicher
        g = dienst.faden(person)
        p = pspeicher.standard()
        p.beitrag(g, "du", du)
        p.beitrag(g, "friday", friday)
        return g

    def test_gespraeche_tragen_die_person(self):
        from kern.protokoll import speicher as pspeicher
        g = dienst.faden(self.a)
        self.assertEqual(pspeicher.standard().kopf(g)["person"], self.a)
        self.assertEqual([x["person"] for x in pspeicher.standard().gespraeche()], [self.a])

    def test_drei_getrennte_faeden_und_verlaeufe(self):
        ge = self._beitraege(None, "Frage von Eike", "Antwort an Eike")
        ga = self._beitraege(self.a, "Frage von Kontakt B", "Antwort an Kontakt B")
        gb = self._beitraege(self.b, "Frage von Kontakt F", "Antwort an Kontakt F")
        self.assertEqual(len({ge, ga, gb}), 3)
        self.assertEqual(dienst.faden_vorhanden(), ge)
        self.assertEqual(dienst.faden_vorhanden(self.a), ga)
        self.assertEqual(dienst.faden_vorhanden(self.b), gb)
        self.assertEqual([b["text"] for b in dienst.verlauf(self.a)], ["Frage von Kontakt B", "Antwort an Kontakt B"])
        self.assertEqual([b["text"] for b in dienst.verlauf()], ["Frage von Eike", "Antwort an Eike"])
        for text in ("Kontakt B", "Kontakt F"):
            self.assertNotIn(text, " ".join(b["text"] for b in dienst.verlauf()))

    def test_ohne_faden_legt_faden_vorhanden_keinen_an_und_faden_haelt_ihn(self):
        from kern.protokoll import speicher as pspeicher
        self.assertIsNone(dienst.faden_vorhanden(self.a))
        g = dienst.faden(self.a)
        pspeicher.standard().beitrag(g, "du", "Hallo")            # erst mit einem Beitrag ist der Faden „vorhanden“
        self.assertEqual(dienst.faden(self.a), g)
        self.assertIsNone(dienst.faden_vorhanden(self.b))

    def test_eine_aktive_person_wird_nicht_von_vielen_anderen_verdraengt(self):
        ga = self._beitraege(self.a, "alt", "alt")
        from kern.protokoll import speicher as pspeicher
        p = pspeicher.standard()
        for i in range(120):                                  # viele neuere, getrennte Fäden anderer (faden() würde einen wiederverwenden)
            g = p.gespraech_beginnen(dienst.ART, person=self.b if i % 2 else None)
            p.beitrag(g, "du", f"x{i}")
        self.assertEqual(dienst.faden_vorhanden(self.a), ga)


class PersonVonTest(unittest.TestCase):
    def test_fehlend_oder_none_ist_eike_gueltige_ids_bleiben(self):
        self.assertIsNone(dienst.person_von({}))
        self.assertIsNone(dienst.person_von({"person": None}))
        self.assertEqual(dienst.person_von({"person": "p12"}), "p12")

    def test_ungueltige_werte_sind_nie_eike(self):
        for boese in ("", "eike", "Eike", "p1;", "p0", "p", 5, ["p1"], " p1", "p1\n", "p1234567"):
            with self.assertRaises(ValueError, msg=repr(boese)):
                dienst.person_von({"person": boese})

    def test_gruppen_nach_person_und_ungueltige_getrennt(self):
        e = [{"id": "1"}, {"id": "2", "person": "p1"}, {"id": "3", "person": None}, {"id": "4", "person": "p1"},
             {"id": "5", "person": "eike"}, {"id": "6", "person": "p2"}]
        gruppen, ungueltig = dienst.gruppen(e)
        self.assertEqual({k: [x["id"] for x in v] for k, v in gruppen.items()},
                         {None: ["1", "3"], "p1": ["2", "4"], "p2": ["6"]})
        self.assertEqual([x["id"] for x in ungueltig], ["5"])


class UnbekannteTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_lesen_bereinigt_dedupliziert_und_ignoriert_kaputtes(self):
        dienst._schreiben("unbekannt_eingang.json", [{"anzeige": "Fremder"}, {"anzeige": " fremder "}, {"anzeige": "A\u200bB\n"},
                                                    {"anzeige": ""}, 5, {"x": 1}, {"anzeige": "x" * 500}])
        namen = dienst.unbekannte_lesen()
        self.assertEqual(namen[0], "Fremder")
        self.assertEqual(len(namen), 3)                                  # Fremder, AB, der gekürzte lange Name
        for n in namen:
            self.assertNotIn("\u200b", n)
            self.assertNotIn("\n", n)
            self.assertLessEqual(len(n), 80)

    def test_lesen_versteht_auch_die_namensliste_des_helfers(self):
        """Der Helfer hängt reine Namen an (Teil 4c); ältere Einträge waren {"anzeige": …}."""
        dienst._schreiben("unbekannt_eingang.json", ["Fremder Nutzer", {"anzeige": "Zweiter"}, "fremder  nutzer", 5, None])
        self.assertEqual(dienst.unbekannte_lesen(), ["Fremder Nutzer", "Zweiter"])

    def test_datei_fehlt_oder_kaputt_gibt_leere_liste(self):
        self.assertEqual(dienst.unbekannte_lesen(), [])
        (dienst._ordner() / "unbekannt_eingang.json").write_text("{kaputt", encoding="utf-8")
        self.assertEqual(dienst.unbekannte_lesen(), [])

    def test_entfernen_nimmt_genau_diese_namen_heraus(self):
        dienst._schreiben("unbekannt_eingang.json", [{"anzeige": "A"}, {"anzeige": "B"}, {"anzeige": "C"}])
        dienst.unbekannte_entfernen(["a", "C"])
        self.assertEqual(dienst.unbekannte_lesen(), ["B"])


class HelferLageTest(unittest.TestCase):
    """Kommt ein Auftrag jetzt raus — oder liegt er bis morgens? (02.10.2026: Der Kontakt C-Auftrag lag die
    ganze Nacht auf dem Sendeplatz, weil der Helfer 7–23 Uhr arbeitet und niemand es sagte.)"""

    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _berlin(self, stunde, minute=0):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, 10, 2, stunde, minute, tzinfo=ZoneInfo("Europe/Berlin"))

    def test_tagsueber_mit_lebenszeichen_wach(self):
        dienst._schreiben("zustand.json", {"verbunden": True, "zeit": time.time()})
        with mock.patch("kern.config.jetzt", return_value=self._berlin(22)):
            self.assertEqual(dienst.helfer_lage(), "wach")

    def test_nachts_schlaeft_auch_mit_frischern_stand(self):
        dienst._schreiben("zustand.json", {"verbunden": True, "zeit": time.time()})
        with mock.patch("kern.config.jetzt", return_value=self._berlin(23, 30)):
            self.assertEqual(dienst.helfer_lage(), "nacht")

    def test_pausiert_wenn_der_helfer_pausiert_bis_in_der_zukunft_meldet(self):
        dienst._schreiben("zustand.json", {"verbunden": False, "zeit": time.time(), "pausiert_bis": time.time() + 300})
        with mock.patch("kern.config.jetzt", return_value=self._berlin(12)):
            self.assertEqual(dienst.helfer_lage(), "pausiert")

    def test_abgelaufene_pause_zaehlt_nicht(self):
        dienst._schreiben("zustand.json", {"verbunden": True, "zeit": time.time(), "pausiert_bis": time.time() - 5})
        with mock.patch("kern.config.jetzt", return_value=self._berlin(12)):
            self.assertEqual(dienst.helfer_lage(), "wach")

    def test_tagsueber_ohne_lebenszeichen_still(self):
        dienst._schreiben("zustand.json", {"verbunden": True, "zeit": time.time() - 3600})
        with mock.patch("kern.config.jetzt", return_value=self._berlin(12)):
            self.assertEqual(dienst.helfer_lage(), "still")


class WachzeitenTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _berlin(self, stunde, minute=0):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, 10, 2, stunde, minute, tzinfo=ZoneInfo("Europe/Berlin"))

    def test_standard_kein_rund_um_die_uhr(self):
        self.assertFalse(dienst.wachzeiten_rund_um_die_uhr())
        dienst.wachzeiten_schreiben(True)
        self.assertTrue(dienst.wachzeiten_rund_um_die_uhr())
        dienst.wachzeiten_schreiben(False)
        self.assertFalse(dienst.wachzeiten_rund_um_die_uhr())

    def test_lage_nachts_aber_wach_bei_schalter(self):
        dienst._schreiben("zustand.json", {"verbunden": True, "zeit": time.time()})
        with mock.patch("kern.config.jetzt", return_value=self._berlin(23, 30)):
            self.assertEqual(dienst.helfer_lage(), "nacht")
            dienst.wachzeiten_schreiben(True)
            self.assertEqual(dienst.helfer_lage(), "wach")


class WachzeitenRoutenTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        from fastapi.testclient import TestClient
        from kern.main import app
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def test_lesen_und_schalten(self):
        self.assertFalse(self.c.get("/api/snapchat/wachzeiten").json()["rund_um_die_uhr"])
        r = self.c.post("/api/snapchat/wachzeiten", json={"aktiv": True}).json()
        self.assertTrue(r["ok"] and r["rund_um_die_uhr"])
        self.assertTrue(self.c.get("/api/snapchat/wachzeiten").json()["rund_um_die_uhr"])


class FreigabenSeiteTest(unittest.TestCase):
    def test_nacht_schalter_ist_verdrahtet(self):
        js = (Path(__file__).resolve().parent.parent / "web" / "freigaben.js").read_text(encoding="utf-8")
        self.assertIn("k.id === 'snapchat'", js)
        self.assertIn("/api/snapchat/wachzeiten", js)
        self.assertIn("Rund um die Uhr", js)
        self.assertNotIn("innerHTML", js)


class AnfrageTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_anfrage_runde(self):
        e = dienst.anfrage_legen("mara.v", person=None)
        self.assertEqual(e["zustand"], "ausstehend")
        self.assertIn("mara.v", [a["snapchat"] for a in dienst.anfragen()])
        self.assertTrue(dienst.anfrage_setzen("mara.v", "angenommen"))
        self.assertTrue(dienst.anfrage_zurueckziehen("mara.v"))
        self.assertEqual(dienst.anfragen(), [])

    def test_zweite_anfrage_gleicher_name_wird_abgelehnt(self):
        dienst.anfrage_legen("mara.v")
        with self.assertRaises(dienst.SnapchatFehler):
            dienst.anfrage_legen("MARA.V")

    def test_leerer_name_wird_abgelehnt(self):
        with self.assertRaises(dienst.SnapchatFehler):
            dienst.anfrage_legen("  ")

    def test_anfrage_auftrag_braucht_eindeutigen_namen(self):
        with self.assertRaises(dienst.SnapchatFehler):
            dienst.auftrag_legen("anfrage", snapchat="  ")

    def test_anfrage_auftrag_liegt_mit_empfaenger(self):
        dienst.auftrag_legen("anfrage", snapchat="mara.v")
        auftrag = dienst.auftrag_lesen()
        self.assertEqual(auftrag["art"], "anfrage")
        self.assertEqual(auftrag["empfaenger"], "mara.v")

    def test_stornieren_raeumt_vermerk_platz_und_schlange(self):
        dienst.anfrage_legen("mara.v")
        dienst.auftrag_legen("anfrage", snapchat="mara.v")
        dienst.auftrag_legen("anfrage", snapchat="mara.v")
        text = dienst.anfrage_stornieren("mara.v")
        self.assertIn("zurückgezogen", text)
        self.assertEqual(dienst.anfragen(), [])
        self.assertIsNone(dienst.auftrag_lesen())
        self.assertEqual(dienst.auftraege_anzahl(), 0)

    def test_stornieren_ohne_alles_meldet_das(self):
        self.assertIn("keine", dienst.anfrage_stornieren("mara.v").lower())

    def test_verworfen_wird_ehrlich_gescheitert_gespiegelt(self):
        import json as jsonmod
        import time as zeitmod
        e = dienst.anfrage_legen("mara.v")
        ordner = dienst._ordner()
        with open(ordner / "ergebnisse.jsonl", "w", encoding="utf-8") as f:
            f.write(jsonmod.dumps({"zeit": zeitmod.time(), "art": "anfrage", "empfaenger": "mara.v",
                                   "ergebnis": "verworfen", "grund": "mehrdeutig"}) + "\n")
        neu = dienst.anfrage_ergebnisse_spiegeln()
        self.assertEqual([n for n, _ in neu], ["mara.v"])
        self.assertEqual(dienst.anfragen()[0]["zustand"], "gescheitert")
        self.assertEqual(dienst.anfrage_ergebnisse_spiegeln(), [])   # kein zweites Mal

    def test_kein_knopf_kippt_nicht_wahrscheinlich_schon_raus(self):
        # Kontakt A-Befund 04.10.2026: Nach Tap ohne Bestätigung heißt fehlender
        # Knopf oft „schon raus“ — der Handy-Befund zählt mehr.
        import json as jsonmod
        import time as zeitmod
        dienst.anfrage_legen("mara.v")
        ordner = dienst._ordner()
        with open(ordner / "ergebnisse.jsonl", "w", encoding="utf-8") as f:
            f.write(jsonmod.dumps({"zeit": zeitmod.time(), "art": "anfrage", "empfaenger": "mara.v",
                                    "ergebnis": "verworfen",
                                    "grund": "Bei „mara.v“ gibt es keinen Hinzufügen-Knopf — nichts angetippt."}) + "\n")
        self.assertEqual(dienst.anfrage_ergebnisse_spiegeln(), [])
        self.assertEqual(dienst.anfragen()[0]["zustand"], "ausstehend")

    def test_alte_fehlerzeilen_vergiften_keine_neue_anfrage(self):
        import json as jsonmod
        import time as zeitmod
        e = dienst.anfrage_legen("mara.v")
        ordner = dienst._ordner()
        with open(ordner / "ergebnisse.jsonl", "w", encoding="utf-8") as f:
            f.write(jsonmod.dumps({"zeit": e["gelegt"] - 100, "art": "anfrage", "empfaenger": "mara.v",
                                   "ergebnis": "verworfen", "grund": "alt"}) + "\n")
        self.assertEqual(dienst.anfrage_ergebnisse_spiegeln(), [])
        self.assertEqual(dienst.anfragen()[0]["zustand"], "ausstehend")

    def test_anfragen_kontext_nennt_zustand_und_grund(self):
        self.assertEqual(dienst.anfragen_kontext(), "")
        dienst.anfrage_legen("mara.v")
        ausstehend = dienst.anfragen_kontext()
        self.assertIn("mara.v", ausstehend)
        self.assertIn("wartet auf Annahme", ausstehend)
        self.assertIn("eigenes", ausstehend)
        dienst.anfrage_setzen("mara.v", "gescheitert")
        self.assertIn("gescheitert", dienst.anfragen_kontext())
        dienst.anfrage_setzen("mara.v", "angenommen")
        self.assertIn("angenommen", dienst.anfragen_kontext())

    def test_gesendet_kontext_traegt_anfragen_mit(self):
        dienst.anfrage_legen("mara.v")
        self.assertIn("mara.v", dienst.gesendet_kontext())

    def test_suchauftrag_legt_suchbegriff_als_empfaenger(self):
        dienst.auftrag_legen("suche", snapchat="Kontakt S")
        auftrag = dienst.auftrag_lesen()
        self.assertEqual(auftrag["art"], "suche")
        self.assertEqual(auftrag["empfaenger"], "Kontakt S")

    def test_suchauftrag_ohne_name_wird_abgelehnt(self):
        with self.assertRaises(dienst.SnapchatFehler):
            dienst.auftrag_legen("suche", snapchat="  ")

    def test_offener_suchauftrag_wird_gefunden_auch_aus_der_schlange(self):
        # 30.10.2026: Eine zweite Suche für denselben Namen legte einen zweiten Auftrag an — und jeder
        # meldete sich später als eigener Push „Suche fertig“.
        self.assertIsNone(dienst.suchauftrag_fuer("Titus"))
        dienst.auftrag_legen("suche", snapchat="Titus")
        self.assertIsNotNone(dienst.suchauftrag_fuer("titus"))
        dienst.auftrag_legen("suche", snapchat="Mara")     # Platz belegt → Mara wartet in der Schlange
        gefunden = dienst.suchauftrag_fuer("Mara")
        self.assertEqual(gefunden["empfaenger"], "Mara")
        self.assertIsNone(dienst.suchauftrag_fuer("Niemand"))

    def test_suchergebnis_runde_mit_verfall(self):
        self.assertIsNone(dienst.suchergebnis_lesen("Kontakt S"))
        dienst.suchergebnis_schreiben("Kontakt S", [{"titel": "Kontakt S", "name": "titus_1", "grund": ""}])
        gefunden = dienst.suchergebnis_lesen("Kontakt S")
        self.assertEqual(len(gefunden["kandidaten"]), 1)
        self.assertEqual(dienst.suchergebnis_lesen()["begriff"], "Kontakt S")   # neueste ohne Angabe
        import time as zeitmod
        alle = dienst._lesen("suchergebnisse.json", {})
        alle[dienst.suche_schluessel("Kontakt S")]["zeit"] -= dienst.SUCH_GILT_S + 10
        dienst._schreiben("suchergebnisse.json", alle)
        self.assertIsNone(dienst.suchergebnis_lesen("Kontakt S"))

    def test_suchergebnis_kurzname_trifft_vollen_namen(self):
        # 11.10.2026 (Kontakt E-Fall): gesucht als „Kontakt E“, Eike und Modell sagen „Kontakt E“.
        dienst.suchergebnis_schreiben("Kontakt E", [{"titel": "Kontakt E", "name": "kontakt_e", "grund": ""}])
        self.assertEqual(dienst.suchergebnis_lesen("Kontakt E")["begriff"], "Kontakt E")
        self.assertIsNone(dienst.suchergebnis_lesen("Kontakt B"))

    def test_erwartet_gilt_48_stunden_und_nimmt_eigene_anfragen_mit(self):
        dienst.erwartet_legen("Kontakt E")
        self.assertEqual([e["name"] for e in dienst.erwartet_liste()], ["Kontakt E"])
        dienst.anfrage_legen("mara.v", None, name="Mara", titel="Mara V")
        self.assertEqual({e["name"] for e in dienst.erwartet_liste()}, {"Kontakt E", "Mara V", "Mara"})
        alle = dienst._lesen("erwartet.json", [])
        alle[0]["gelegt"] -= dienst.ERWARTET_GILT_S + 5
        dienst._schreiben("erwartet.json", alle)
        self.assertNotIn("Kontakt E", [e["name"] for e in dienst.erwartet_liste()])

    def test_annehmen_auftrag_zaehlt_nicht_gegen_die_sendegrenze(self):
        dienst.auftrag_legen("annehmen", snapchat="Kontakt E")
        self.assertEqual(dienst._lesen("versendet.json", []), [])

    def test_suchergebnis_hoechstens_fuenf(self):
        viele = [{"titel": f"T{i}", "name": f"t_{i}", "grund": ""} for i in range(9)]
        eintrag = dienst.suchergebnis_schreiben("Kontakt S", viele)
        self.assertEqual(len(eintrag["kandidaten"]), dienst.SUCH_MAX)
