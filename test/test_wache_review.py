"""Fund-für-Fund-Tests zum Review von Kontakte Teil 4b (30.09.2026): jeder Test würde ohne den Fix rot."""
import asyncio
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kern import konnektoren
from kern.dienste import snapchat as dienst, snapchat_wache as wache
from kern.gedaechtnis import bilder, graph
from kern.personen import freigaben, register, rueckfragen
from kern.sitzung import chat
from test.test_snapchat_wache import alt, echtes_png, ergebnis, lauf, protokoll_freigeben


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GRAPHITI_URL": "http://graphiti:8000/mcp"})
        self.env.start()
        konnektoren.setzen("snapchat", True)
        wache._nachzulegen.clear()
        wache._zurueck.clear()
        wache._gesehen_texte.clear()
        wache._wiederholung_gemeldet.clear()
        self.pid = register.anlegen("Kontakt B", snapchat="maya_x", graph_name="Kontakt B Schulz")["id"]
        p = mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 1})
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        wache._zurueck.clear()
        wache._gesehen_texte.clear()
        wache._wiederholung_gemeldet.clear()
        protokoll_freigeben()
        self.env.stop()

    def _eingang(self, *e):
        dienst._schreiben("eingang.json", list(e))

    def _ungelesen(self):
        return [e for e in dienst._lesen("eingang.json", []) if not e.get("gelesen")]

    def _motor(self, antwort="Hi!"):
        return mock.patch.object(chat, "antworten", mock.AsyncMock(return_value=ergebnis(antwort)))


class WiderrufeneFreigabe(Basis):
    def _freigegeben(self, text):
        with mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 1}):
            a = rueckfragen.stellen(self.pid, "Geburtstag")
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)):
            a = lauf(rueckfragen.freigeben(a["id"], text))
        return a

    def test_widerrufene_freigabe_wird_nie_gesendet_die_person_bekommt_die_absage(self):
        a = self._freigegeben("GEHEIM Oktober")
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=None)):
            lauf(freigaben.widerrufen(self.pid, a["freigabe"]))
        with mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
        legen.assert_called_once()
        self.assertNotIn("GEHEIM", legen.call_args.kwargs["text"])
        self.assertIn("leider nichts sagen", legen.call_args.kwargs["text"])

    def test_zu_langer_freigabetext_wird_nicht_gekuerzt_gesendet(self):
        lang = "Eike hat am 3. Oktober Geburtstag und feiert ab 18 Uhr. " * 8            # deutlich über 280 Zeichen
        self._freigegeben(lang)
        with mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
        text = legen.call_args.kwargs["text"]
        self.assertLessEqual(len(text), 280)
        self.assertNotIn("Eike hat am 3. Oktober", text)                # kein abgeschnittener Teil des Freigabetexts
        self.assertIn("frag mich", text)                                # stattdessen der Hinweis, direkt nachzufragen

    def test_kurzer_freigabetext_geht_vollstaendig_raus(self):
        self._freigegeben("Eike hat im Oktober Geburtstag.")
        with mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
        self.assertIn("Eike hat im Oktober Geburtstag.", legen.call_args.kwargs["text"])

    def test_sehr_alte_unbeantwortete_rueckfragen_werden_nicht_mehr_gesendet(self):
        a = self._freigegeben("Alt")
        d = rueckfragen._lesen()
        d["anfragen"][0]["zeit"] = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(timespec="seconds")
        rueckfragen._speichern(d)
        with mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
        legen.assert_not_called()
        self.assertEqual(rueckfragen.zu_melden(), [])                   # als erledigt geführt

    def test_person_ohne_snapchat_namen_blockiert_die_rueckfrage_antworten_nicht(self):
        register.aendern(self.pid, snapchat="")
        a = self._freigegeben("Text")
        with mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
        legen.assert_not_called()
        self.assertEqual(rueckfragen.zu_melden(), [])


class FremderTextBenenntKeineBilder(Basis):
    def test_nachricht_einer_person_ist_kein_beleg_fuer_einen_namen(self):
        name = "c" * 32 + ".png"
        self._eingang(alt("Das ist Eike", id="t", art="text", person=self.pid), alt("[Snap]", id="s", art="snap", bild=name, fehler=None, person=self.pid))
        auswertung = {"beschreibung": "Ein Mann.", "personen": 1, "wer": "Eike"}       # das Modell ließ sich vom Text leiten
        with self._motor("Schönes Bild!"), mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(dienst, "bild_lesen", return_value=echtes_png()), mock.patch.object(dienst, "bild_entfernen"), \
             mock.patch.object(wache.bildauswertung, "auswerten", mock.AsyncMock(return_value=auswertung)) as aus:
            lauf(wache.einmal())
        self.assertEqual(aus.await_args.args[3], "")                                     # der fremde Text geht nicht in die Auswertung
        eigene = bilder.liste(person=self.pid)
        self.assertEqual(len(eigene), 1)
        e = bilder.eintrag(eigene[0]["id"])
        self.assertEqual(e["name"], "Kontakt B Schulz")                                       # nur die Absender-Regel greift
        self.assertNotIn("laut Nachricht", e.get("notizen") or "")
        self.assertEqual(bilder.mit_namen("Eike"), [])

    def test_bei_eike_bleibt_seine_nachricht_der_beleg(self):
        name = "d" * 32 + ".png"
        self._eingang(alt("Das ist Kontakt E", id="t", art="text"), alt("[Snap]", id="s", art="snap", bild=name, fehler=None))
        auswertung = {"beschreibung": "Ein Mann.", "personen": 1, "wer": "Kontakt E"}
        with self._motor(), mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(dienst, "bild_lesen", return_value=echtes_png()), mock.patch.object(dienst, "bild_entfernen"), \
             mock.patch.object(wache.bildauswertung, "auswerten", mock.AsyncMock(return_value=auswertung)) as aus:
            lauf(wache.einmal())
        self.assertEqual(aus.await_args.args[3], "Das ist Kontakt E")
        self.assertEqual(bilder.mit_namen("Kontakt E")[0]["name"], "Kontakt E")


class KeineGruppeBlockiertAlle(Basis):
    def test_person_ohne_snapchat_namen_wird_ohne_motor_als_gelesen_markiert_und_eike_kommt_dran(self):
        register.aendern(self.pid, snapchat="")
        self._eingang(alt("Hallo", id="m", person=self.pid, zeit=time.time() - 120), alt("Hi Friday", id="e", zeit=time.time() - 60))
        with self._motor("Hallo Sir") as m, mock.patch.object(dienst, "auftrag_legen") as legen:
            for _ in range(4):
                lauf(wache.einmal())
        self.assertEqual(self._ungelesen(), [])
        self.assertEqual(m.await_count, 1)                                      # nur Eikes Zug ging an den Motor
        self.assertNotIn("person", legen.call_args.kwargs)

    def test_scheiternde_gruppe_wird_zurueckgestellt_die_naechste_kommt_dran(self):
        p2 = register.anlegen("Kontakt F", snapchat="paul_y")["id"]
        self._eingang(alt("Hallo", id="m", person=self.pid, zeit=time.time() - 120), alt("Hi", id="p", person=p2, zeit=time.time() - 60))
        async def antworten(nachrichten, **kw):
            if kw.get("person", {}).get("id") == self.pid:
                raise RuntimeError("Motor kaputt")
            return ergebnis("Antwort")
        with mock.patch.object(chat, "antworten", side_effect=antworten), mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())                                                # Kontakt B scheitert (jüngerer Fehler: kein Notfall) ...
            lauf(wache.einmal())                                                # ... und Kontakt F kommt trotzdem dran
        self.assertEqual([c.kwargs["person"] for c in legen.call_args_list], [p2])
        self.assertEqual([e["id"] for e in self._ungelesen()], ["m"])

    def test_zurueckgestellte_gruppe_wird_nach_der_frist_erneut_versucht(self):
        self._eingang(alt("Hallo", id="m", person=self.pid))
        with mock.patch.object(chat, "antworten", mock.AsyncMock(side_effect=RuntimeError("kaputt"))) as m:
            lauf(wache.einmal())
            lauf(wache.einmal())
            self.assertEqual(m.await_count, 1)                                  # in der Frist: kein zweiter Aufruf
            with mock.patch.object(wache.time, "time", return_value=time.time() + wache.RUECKSTELLUNG_S + 1):
                lauf(wache.einmal())
            self.assertEqual(m.await_count, 2)                                  # danach wieder


class BuendelfensterHungertNiemanden(Basis):
    def test_eine_dauernd_schreibende_person_haelt_eike_nicht_auf(self):
        jetzt = time.time()
        self._eingang(alt("alt", id="m1", person=self.pid, zeit=jetzt - 100), alt("frisch", id="m2", person=self.pid, zeit=jetzt - 2),
                      alt("Hi Friday", id="e", zeit=jetzt - 60))
        with self._motor("Hallo Sir") as m, mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
        m.assert_awaited_once()                                                 # Eike kam dran, Kontakt B wartet auf ihr Bündelfenster
        self.assertNotIn("person", legen.call_args.kwargs)
        self.assertEqual(sorted(e["id"] for e in self._ungelesen()), ["m1", "m2"])

    def test_eintrag_mit_kaputter_zeit_legt_die_wache_nicht_lahm(self):
        self._eingang({"id": "x", "art": "text", "text": "Hi", "zeit": "gestern", "gelesen": False})
        with self._motor("Hallo") as m, mock.patch.object(dienst, "auftrag_legen"):
            lauf(wache.einmal())
        m.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
