"""Eikes eigene Angaben: Satz, Notizbuch (sofort), Graph-Episode (mit Vorrang), Status."""
import asyncio
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kern.gedaechtnis import graph, notizen
from kern.gedaechtnis import speicher as gspeicher

U = "11111111-1111-1111-1111-111111111111"


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.einspeisen = mock.AsyncMock(return_value=True)
        self.angekommen = mock.AsyncMock(return_value=False)
        self.patches = [mock.patch.object(graph, "einspeisen", self.einspeisen), mock.patch.object(graph, "angekommen", self.angekommen),
                        mock.patch.object(graph, "pausiert", return_value=False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()


class Saetze(unittest.TestCase):
    def test_name(self):
        self.assertEqual(notizen.satz("name", "Kontakt Bs Mutter", wert="Kontakt H"), "Kontakt Bs Mutter heißt Kontakt H.")

    def test_angabe(self):
        self.assertEqual(notizen.satz("angabe", "Kontakt Bs Mutter", label="Beruf", wert="Lehrerin"), "Beruf von Kontakt Bs Mutter: Lehrerin.")

    def test_frei(self):
        self.assertEqual(notizen.satz("frei", "Kontakt Bs Mutter", text="Sie arbeitet in Musterhausen."), "Zu Kontakt Bs Mutter: Sie arbeitet in Musterhausen.")

    def test_leeres_und_unbekanntes_wird_abgelehnt(self):
        for art, felder in (("name", {"wert": "  "}), ("angabe", {"label": "Beruf", "wert": ""}), ("frei", {"text": ""}), ("x", {})):
            with self.assertRaises(ValueError):
                notizen.satz(art, "Kontakt Bs Mutter", **felder)

    def test_steuerzeichen_und_laenge(self):
        s = notizen.satz("name", "Kontakt Bs Mutter", wert="Kontakt H\nKontakt H\x00" + "x" * 500)
        self.assertNotIn("\n", s)
        self.assertNotIn("\x00", s)
        self.assertLessEqual(len(s), 160)


class Anlegen(Basis):
    def test_notizbuch_sofort_und_vorgemerkte_episode(self):
        n = notizen.anlegen(U, "Kontakt Bs Mutter", "name", wert="Kontakt H")
        eigene = [f for f in gspeicher.standard().fakten() if f["quelle"] == "eike"]
        self.assertEqual([f["inhalt"] for f in eigene], ["Kontakt Bs Mutter heißt Kontakt H."])
        self.assertTrue(eigene[0]["thema"].startswith("Notiz Kontakt Bs Mutter "))
        self.assertEqual((n["status"], n["versuche"]), ("offen", 0))
        self.assertEqual([x["text"] for x in notizen.fuer(U)], ["Kontakt Bs Mutter heißt Kontakt H."])

    def test_zwei_angaben_ueberschreiben_sich_im_notizbuch_nicht(self):
        notizen.anlegen(U, "Kontakt Bs Mutter", "name", wert="Kontakt H Muster")
        notizen.anlegen(U, "Kontakt Bs Mutter", "angabe", label="Beruf", wert="Lehrerin")
        eigene = [f for f in gspeicher.standard().fakten() if f["quelle"] == "eike"]
        self.assertEqual(len(eigene), 2)

    def test_datei_hat_rechte_600_und_zeigt_nur_die_eigene_seite(self):
        notizen.anlegen(U, "Kontakt Bs Mutter", "name", wert="Kontakt H")
        notizen.anlegen("22222222-2222-2222-2222-222222222222", "Kontakt A", "frei", text="Ist Torwart.")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.ordner, "notizen.json")).st_mode), 0o600)
        self.assertEqual(len(notizen.fuer(U)), 1)


class Durchgang(Basis):
    def test_offene_angabe_geht_mit_vorrangvermerk_an_den_graphen(self):
        n = notizen.anlegen(U, "Kontakt Bs Mutter", "name", wert="Kontakt H")
        self.assertEqual(asyncio.run(notizen.durchgang()), 1)
        name, text, beschreibung, zeit = self.einspeisen.await_args.args[:4]
        self.assertEqual(name, n["episode"])
        self.assertIn("Direkte Angabe von Eike — hat Vorrang", text)
        self.assertIn("Kontakt Bs Mutter heißt Kontakt H.", text)
        self.assertIn("direkte Angabe von Eike", self.einspeisen.await_args.kwargs["anweisungen"])
        self.assertEqual(self.einspeisen.await_args.kwargs["gruppe"], "eike")
        self.assertEqual(notizen.fuer(U)[0]["status"], "gesendet")

    def test_pausierter_graph_nichts_senden(self):
        notizen.anlegen(U, "Kontakt Bs Mutter", "name", wert="Kontakt H")
        with mock.patch.object(graph, "pausiert", return_value=True):
            self.assertEqual(asyncio.run(notizen.durchgang()), 0)
        self.einspeisen.assert_not_awaited()
        self.assertEqual(notizen.fuer(U)[0]["status"], "offen")

    def test_graph_nimmt_nicht_an_bleibt_offen(self):
        self.einspeisen.return_value = False
        notizen.anlegen(U, "Kontakt Bs Mutter", "name", wert="Kontakt H")
        self.assertEqual(asyncio.run(notizen.durchgang()), 0)
        self.assertEqual((notizen.fuer(U)[0]["status"], notizen.fuer(U)[0]["versuche"]), ("offen", 0))

    def _gesendet_vor(self, minuten):
        n = notizen.anlegen(U, "Kontakt Bs Mutter", "name", wert="Kontakt H")
        asyncio.run(notizen.durchgang())
        vor = (datetime.now(timezone.utc) - timedelta(minutes=minuten)).isoformat(timespec="seconds")
        notizen._setzen(n["id"], gesendet=vor)
        return n

    def test_angekommen_wird_erkannt(self):
        self._gesendet_vor(15)
        self.angekommen.return_value = True
        asyncio.run(notizen.durchgang())
        self.assertEqual(notizen.fuer(U)[0]["status"], "angekommen")

    def test_noch_nicht_angekommen_wird_nach_45_minuten_noch_einmal_gesendet(self):
        self._gesendet_vor(15)
        asyncio.run(notizen.durchgang())
        self.assertEqual(self.einspeisen.await_count, 1)                    # nach 15 min nur geprüft, nicht neu gesendet
        self._gesendet_vor(50)
        asyncio.run(notizen.durchgang())
        self.assertEqual(notizen.fuer(U)[0]["versuche"], 2)

    def test_nach_drei_versuchen_aufgegeben(self):
        n = notizen.anlegen(U, "Kontakt Bs Mutter", "name", wert="Kontakt H")
        notizen._setzen(n["id"], status="gesendet", versuche=3, gesendet=(datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat())
        asyncio.run(notizen.durchgang())
        self.assertEqual(notizen.fuer(U)[0]["status"], "aufgegeben")

    def test_graphiti_antwortet_nicht_nichts_veraendern(self):
        self._gesendet_vor(90)
        self.angekommen.return_value = None
        asyncio.run(notizen.durchgang())
        self.assertEqual(notizen.fuer(U)[0]["versuche"], 1)


if __name__ == "__main__":
    unittest.main()
