"""Fund-für-Fund-Tests zum Review von Kontakte Teil 3 (29.09.2026): jeder Test würde ohne den Fix rot."""
import asyncio
import base64
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from kern.gedaechtnis import bilder, graph, rueckblick
from kern.personen import PersonenFehler, freigaben, register
from kern.protokoll import speicher as pspeicher
from kern.werkzeuge import gedaechtnis as gwerkzeug

PNG = base64.b64encode(open("web/icons/icon-192.png", "rb").read()).decode()


def lauf(coro):
    return asyncio.run(coro)


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner, "GRAPHITI_URL": "http://graphiti:8000/mcp"})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class GraphLoeschen(Basis):
    def test_fehlerantwort_von_delete_episode_ist_kein_erfolg(self):
        antworten = [{"episodes": [{"name": "n", "uuid": "u"}]}, {"error": "DB weg"}]
        with mock.patch.object(graph, "_aufruf", mock.AsyncMock(side_effect=antworten)):
            self.assertIs(lauf(graph.episode_loeschen("n", "p1")), False)

    def test_fehlerantwort_von_get_episodes_ist_ein_fehler_nicht_noch_nicht_da(self):
        with mock.patch.object(graph, "_aufruf", mock.AsyncMock(return_value={"error": "nicht initialisiert"})):
            self.assertIs(lauf(graph.episode_loeschen("n", "p1")), False)

    def test_eikes_gruppe_wird_nie_geloescht(self):
        with mock.patch.object(graph, "_aufruf", mock.AsyncMock()) as a:
            with self.assertRaises(ValueError):
                lauf(graph.episode_loeschen("n", "eike"))
        a.assert_not_called()

    def test_gruppen_ids_nur_ascii_ziffern_ohne_null(self):
        for g in ("p١", "p0", "p01"):
            with self.assertRaises(ValueError, msg=repr(g)):
                graph.pruefe_gruppe(g)
        self.assertEqual(graph.pruefe_gruppe("p10"), "p10")

    def test_suche_sieht_mehr_als_200_episoden(self):
        with mock.patch.object(graph, "_aufruf", mock.AsyncMock(return_value={"episodes": []})) as a:
            lauf(graph.episode_loeschen("n", "p1"))
        self.assertGreaterEqual(a.await_args.args[1]["max_episodes"], 1000)


class FreigabenNachlauf(Basis):
    def setUp(self):
        super().setUp()
        self.pid = register.anlegen("Kontakt B", snapchat="maya_x")["id"]

    def _aktive(self):
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)):
            return lauf(freigaben.freigeben(self.pid, "Eike mag Schach."))

    def _alt_machen(self, feld="zeit", tage=30):
        d = register._lesen()
        d["personen"][self.pid]["freigaben"][0][feld] = (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat(timespec="seconds")
        register._speichern(d)

    def test_frist_zaehlt_ab_dem_widerruf_und_ein_fehler_gibt_nie_auf(self):
        f = self._aktive()
        self._alt_machen("zeit", 30)                                   # Freigabe ist alt und verarbeitet
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=False)):
            lauf(freigaben.widerrufen(self.pid, f["id"]))              # Löschen scheitert mit Fehler
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=True)) as weg:
            self.assertEqual(lauf(freigaben.nachlauf()), 1)            # wird trotz Alter weiter versucht
        weg.assert_awaited_once()

    def test_noch_nicht_da_gibt_erst_sieben_tage_nach_dem_widerruf_auf(self):
        f = self._aktive()
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=None)):
            lauf(freigaben.widerrufen(self.pid, f["id"]))
            self.assertEqual(lauf(freigaben.nachlauf()), 0)
        self._alt_machen("widerrufen_zeit", 8)
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock()) as weg:
            self.assertEqual(lauf(freigaben.nachlauf()), 0)
        weg.assert_not_awaited()

    def test_phantom_nach_fehlgeschlagener_freigabe_wird_vorsorglich_geloescht(self):
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=False)):
            with self.assertRaises(PersonenFehler):
                lauf(freigaben.freigeben(self.pid, "Vielleicht doch angekommen."))
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=True)) as weg:
            self.assertEqual(lauf(freigaben.nachlauf()), 1)
        weg.assert_awaited_once_with("freigabe-%s-f1" % self.pid, self.pid)
        self.assertTrue(register.freigaben(self.pid)[0]["geloescht"])

    def test_suche_ist_gesperrt_solange_ein_widerruf_im_graphen_offen_ist(self):
        f = self._aktive()
        self.assertFalse(freigaben.suche_gesperrt(self.pid))
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=None)):
            lauf(freigaben.widerrufen(self.pid, f["id"]))
        self.assertTrue(freigaben.suche_gesperrt(self.pid))
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=True)):
            lauf(freigaben.nachlauf())
        self.assertFalse(freigaben.suche_gesperrt(self.pid))
        self.assertTrue(freigaben.suche_gesperrt("p99") in (True, False))  # unbekannte Person wirft nicht

    def test_freigabe_hat_eigene_anweisungen_die_auch_fakten_ueber_dritte_zulassen(self):
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)) as ein:
            lauf(freigaben.freigeben(self.pid, "Kontakt E wohnt in Linden."))
        anw = ein.await_args.kwargs["anweisungen"]
        self.assertIn("Kontakt B", anw)
        self.assertIn("so, wie sie im Text stehen", anw)


class PersonImProtokoll(Basis):
    def test_person_muss_leer_oder_eine_register_id_sein(self):
        p = pspeicher.standard()
        for boese in ("", " ", "eike", "p1;", "P1", "p0", "1", "p1\n"):
            with self.assertRaises(ValueError, msg=repr(boese)):
                p.gespraech_beginnen("snapchat", person=boese)
        self.assertIsNone(p.kopf(p.gespraech_beginnen("chat", person=None))["person"])
        self.assertEqual(p.kopf(p.gespraech_beginnen("snapchat", person="p12"))["person"], "p12")


class Herkunft(Basis):
    def setUp(self):
        super().setUp()
        self.pid = register.anlegen("Kontakt B", snapchat="maya_x", graph_name="Kontakt B Schulz")["id"]
        self.p = pspeicher.standard()
        self.g = self.p.gespraech_beginnen("snapchat", person=self.pid)
        self.p.beitrag(self.g, "du", "Ich mag Schach")
        self.p.beitrag(self.g, "friday", "Schön!")

    def test_zusammenfassung_nennt_die_person_nicht_eike(self):
        antwort = ({"candidates": [{"content": {"parts": [{"text": "Kontakt B mag Schach."}]}}]}, "m")
        with mock.patch("kern.sitzung.chat._gemini_anfrage", mock.AsyncMock(return_value=antwort)) as m:
            lauf(rueckblick.zusammenfassen(self.g))
        inhalt = m.await_args.args[0]
        system = inhalt["systemInstruction"]["parts"][0]["text"]
        text = inhalt["contents"][0]["parts"][0]["text"]
        self.assertIn("Kontakt B Schulz", system)
        self.assertNotIn("zwischen Eike", system)
        self.assertIn("Kontakt B Schulz: Ich mag Schach", text)
        self.assertNotIn("Eike:", text)

    def test_zusammenfassung_fuer_eikes_gespraeche_bleibt_wie_sie_war(self):
        eigen = self.p.gespraech_beginnen("chat")
        self.p.beitrag(eigen, "du", "Hallo")
        antwort = ({"candidates": [{"content": {"parts": [{"text": "Begrüßung."}]}}]}, "m")
        with mock.patch("kern.sitzung.chat._gemini_anfrage", mock.AsyncMock(return_value=antwort)) as m:
            lauf(rueckblick.zusammenfassen(eigen))
        self.assertIn("zwischen Eike und seiner Assistentin", m.await_args.args[0]["systemInstruction"]["parts"][0]["text"])
        self.assertIn("Eike: Hallo", m.await_args.args[0]["contents"][0]["parts"][0]["text"])

    def test_sprecher_im_graphen_ist_der_graph_name(self):
        self.p.zusammenfassen(self.g, "z")
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as senden:
            lauf(rueckblick.einspeisen(self.g))
        self.assertIn("Kontakt B Schulz: Ich mag Schach", senden.await_args.args[1])
        self.assertIn("Kontakt B Schulz", senden.await_args.kwargs["anweisungen"])

    def test_rueckblick_werkzeug_nennt_die_person_und_gilt_als_fremder_text(self):
        from kern.werkzeuge import katalog
        text = gwerkzeug.rueckblick("Schach", 7)
        self.assertIn("[Gespräch von Kontakt B", text)
        self.assertNotIn("dabei du:", text)
        self.assertTrue(katalog.liefert_fremdes("rueckblick", {"ergebnis": text}))
        eigen = self.p.gespraech_beginnen("chat")
        self.p.beitrag(eigen, "du", "Ich mag Tee")
        self.assertFalse(katalog.liefert_fremdes("rueckblick", {"ergebnis": "[RÜCKBLICK] - 29.09. (Chat): Tee"}))

    def test_name_darf_nicht_reserviert_sein(self):
        for name in ("Eike", "Friday", "ME"):
            with self.assertRaises(PersonenFehler, msg=name):
                register.anlegen(name)
            with self.assertRaises(PersonenFehler, msg=name):
                register.aendern(self.pid, name=name)


class KopieAnEike(Basis):
    def setUp(self):
        super().setUp()
        self.pid = register.anlegen("Kontakt B", snapchat="maya_x")["id"]
        self.p = pspeicher.standard()
        self.g = self.p.gespraech_beginnen("snapchat", person=self.pid)
        self.p.beitrag(self.g, "du", "Hi")
        self.p.zusammenfassen(self.g, "z")
        self.p.graph_markieren(self.g, "angekommen")

    def test_kopie_wird_gepruft_und_erst_dann_fertig(self):
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)):
            lauf(rueckblick.kopie_an_eike(self.g))
        self.assertEqual(self.p.eike_kopie_zu_pruefen(), [self.g])
        with mock.patch("kern.gedaechtnis.graph.angekommen", mock.AsyncMock(return_value=True)) as da:
            lauf(rueckblick.kopie_nachsehen(self.g))
        self.assertEqual(da.await_args.args[0], f"gespraech-{self.g}-von-{self.pid}")
        self.assertNotIn("gruppe", da.await_args.kwargs)     # in Eikes Gruppe
        self.assertEqual(self.p.eike_kopie_zu_pruefen(), [])
        self.assertEqual(self.p.eike_kopie_offen(), [])
        self.assertEqual(self.p.kopf(self.g)["graph_eike_kopie"], 2)

    def test_verworfene_kopie_wird_neu_gesendet_dann_aufgegeben(self):
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as ein, \
             mock.patch("kern.gedaechtnis.graph.angekommen", mock.AsyncMock(return_value=False)):
            for _ in range(rueckblick.MAX_VERSUCHE):
                lauf(rueckblick.kopie_an_eike(self.g))
                lauf(rueckblick.kopie_nachsehen(self.g))
        self.assertEqual(ein.await_count, rueckblick.MAX_VERSUCHE)
        self.assertEqual(self.p.kopf(self.g)["graph_eike_kopie"], 3)   # aufgegeben
        self.assertEqual(self.p.eike_kopie_offen(), [])

    def test_dauerhaft_abgelehnte_kopie_blockiert_die_warteschlange_nicht(self):
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=False)):
            for _ in range(rueckblick.MAX_VERSUCHE):
                lauf(rueckblick.kopie_an_eike(self.g))
        self.assertEqual(self.p.eike_kopie_offen(), [])
        self.assertEqual(self.p.kopf(self.g)["graph_eike_kopie"], 3)

    def test_person_nicht_im_register_wird_aufgegeben_statt_zu_blockieren(self):
        g = self.p.gespraech_beginnen("snapchat", person="p99")
        self.p.beitrag(g, "du", "x")
        self.p.zusammenfassen(g, "z")
        self.p.graph_markieren(g, "angekommen")
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as ein:
            lauf(rueckblick.kopie_an_eike(g))
        ein.assert_not_awaited()
        self.assertNotIn(g, self.p.eike_kopie_offen())


class IdVergabeUndSicherung(Basis):
    def test_ids_beginnen_hinter_den_ids_die_das_protokoll_kennt(self):
        pspeicher.standard().gespraech_beginnen("snapchat", person="p7")   # Register-Datei fehlt, Protokoll kennt p7
        self.assertEqual(register.anlegen("Neu")["id"], "p8")

    def test_ids_beginnen_hinter_den_ids_die_die_bilder_kennen(self):
        bilder.speichern(None, "image/png", PNG, person="p9")
        self.assertEqual(register.anlegen("Neu")["id"], "p10")

    def test_register_wird_nachts_mit_gesichert_600(self):
        register.anlegen("Kontakt B")
        jetzt = datetime(2026, 9, 29, 4, 0, tzinfo=__import__("zoneinfo").ZoneInfo("Europe/Berlin"))
        with mock.patch("kern.config.jetzt", return_value=jetzt):
            rueckblick.sichern()
        kopie = Path(self.ordner) / "sicherung" / "personen-2026-09-29.json"
        self.assertTrue(kopie.exists())
        self.assertEqual(oct(kopie.stat().st_mode & 0o777), "0o600")


class BilderWiedererkennung(Basis):
    def test_wiedererkennen_bleibt_innerhalb_der_person(self):
        from io import BytesIO

        from PIL import Image, ImageDraw
        b = Image.new("RGB", (600, 1000))
        d = ImageDraw.Draw(b)
        for y in range(1000):
            d.line([(0, y), (600, y)], fill=(y // 5 % 256, 40, 200 - y // 6))
        d.ellipse((100, 200, 450, 700), fill=(250, 250, 40))
        aus = BytesIO()
        b.save(aus, "PNG")
        roh = aus.getvalue()
        eikes = bilder.speichern(None, "image/png", base64.b64encode(roh).decode())
        self.assertEqual(bilder.gleiches(roh)["id"], eikes)                        # Eike sieht alles
        self.assertIsNone(bilder.gleiches(roh, person="p1"))                       # Kontakt B nicht Eikes Bilder
        maya = bilder.speichern(None, "image/png", base64.b64encode(roh).decode(), person="p1")
        self.assertEqual(bilder.gleiches(roh, ausser=maya, person="p1"), None)     # nur ihr eigenes Bild, sonst keins
        self.assertNotIn(eikes, bilder.eintrag(maya)["notizen"] or "")             # kein Verweis auf Eikes Bild


if __name__ == "__main__":
    unittest.main()
