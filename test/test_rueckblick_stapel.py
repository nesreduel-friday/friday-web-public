"""Rückblick-Durchgang: mehrere Gespräche je Takt an den Graphen, ohne dessen Schlange zu überfüllen."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kern.gedaechtnis import graph, rueckblick
from kern.protokoll import speicher as pspeicher


def _ohne_nebenwirkungen():
    return [mock.patch.object(rueckblick, "zusammenfassen", mock.AsyncMock()),
            mock.patch.object(rueckblick, "sichern"),
            mock.patch.object(rueckblick, "_gelegentlich_verdichten", mock.AsyncMock())]


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.p = pspeicher.standard()
        self.patches = _ohne_nebenwirkungen() + [mock.patch.object(pspeicher, "standard", return_value=self.p)]
        for m in self.patches:
            m.start()

    def tearDown(self):
        for m in self.patches:
            m.stop()
        self.env.stop()


class Einspeisen(Basis):
    def test_mehrere_gespraeche_je_durchgang_aber_nicht_alle(self):
        with mock.patch.object(self.p, "graph_offen", return_value=list(range(1, 20))), \
             mock.patch.object(self.p, "graph_zu_pruefen", return_value=[]), \
             mock.patch.object(self.p, "graph_in_arbeit", return_value=0), \
             mock.patch.object(rueckblick, "einspeisen", mock.AsyncMock()) as ein:
            asyncio.run(rueckblick.durchgang())
        self.assertEqual([c.args[0] for c in ein.await_args_list], list(range(1, rueckblick.EINSPEISEN_STAPEL + 1)))

    def test_haelt_die_schlange_kurz(self):
        # Graphitis Schlange liegt im Arbeitsspeicher und wird je Gespräch ≈ 1 min abgearbeitet; liegt zu viel darin,
        # sähe „nachsehen“ nach 10 min ein Gespräch als verloren an und schickte es doppelt.
        voll = rueckblick.IN_ARBEIT_MAX
        with mock.patch.object(self.p, "graph_offen", return_value=[1, 2, 3, 4, 5]), \
             mock.patch.object(self.p, "graph_zu_pruefen", return_value=[]), \
             mock.patch.object(self.p, "graph_in_arbeit", side_effect=[voll - 2, voll - 1, voll, voll]), \
             mock.patch.object(rueckblick, "einspeisen", mock.AsyncMock()) as ein:
            asyncio.run(rueckblick.durchgang())
        self.assertEqual([c.args[0] for c in ein.await_args_list], [1, 2])

    def test_ein_fehler_stoppt_die_uebrigen_nicht(self):
        with mock.patch.object(self.p, "graph_offen", return_value=[1, 2, 3]), \
             mock.patch.object(self.p, "graph_zu_pruefen", return_value=[]), \
             mock.patch.object(self.p, "graph_in_arbeit", return_value=0), \
             mock.patch.object(rueckblick, "einspeisen", mock.AsyncMock(side_effect=[RuntimeError("x"), None, None])) as ein:
            asyncio.run(rueckblick.durchgang())
        self.assertEqual(ein.await_count, 3)


class Nachsehen(Basis):
    def test_mehrere_je_durchgang(self):
        with mock.patch.object(self.p, "graph_offen", return_value=[]), \
             mock.patch.object(self.p, "graph_zu_pruefen", return_value=list(range(1, 30))), \
             mock.patch.object(rueckblick, "nachsehen", mock.AsyncMock()) as nach:
            asyncio.run(rueckblick.durchgang())
        self.assertEqual([c.args[0] for c in nach.await_args_list], list(range(1, rueckblick.NACHSEHEN_STAPEL + 1)))

    def test_erneut_senden_nur_wenn_in_der_schlange_platz_ist(self):
        g = self.p.gespraech_beginnen("chat")
        self.p.beitrag(g, "du", "hallo")
        self.p.graph_markieren(g, "gesendet", neuer_versuch=True)
        with mock.patch.object(graph, "angekommen", mock.AsyncMock(return_value=False)), \
             mock.patch.object(self.p, "graph_in_arbeit", return_value=rueckblick.IN_ARBEIT_MAX), \
             mock.patch.object(rueckblick, "einspeisen", mock.AsyncMock()) as ein:
            asyncio.run(rueckblick.nachsehen(g))
        ein.assert_not_awaited()
        with mock.patch.object(graph, "angekommen", mock.AsyncMock(return_value=False)), \
             mock.patch.object(self.p, "graph_in_arbeit", return_value=0), \
             mock.patch.object(rueckblick, "einspeisen", mock.AsyncMock()) as ein:
            asyncio.run(rueckblick.nachsehen(g))
        ein.assert_awaited_once_with(g)


class InArbeit(Basis):
    def test_zaehlt_nur_junge_gesendete(self):
        a = self.p.gespraech_beginnen("chat")
        b = self.p.gespraech_beginnen("chat")
        c = self.p.gespraech_beginnen("chat")
        self.p.graph_markieren(a, "gesendet", neuer_versuch=True)                 # jetzt
        self.p.graph_markieren(b, "gesendet", neuer_versuch=True)
        alt = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds")
        self.p._schreiben("UPDATE gespraeche SET graph_gesendet = ? WHERE id = ?", (alt, b))   # vor 30 min
        self.p.graph_markieren(c, "angekommen")
        self.assertEqual(self.p.graph_in_arbeit(), 1)


class Reihenfolge(Basis):
    """Eikes eigene Gespräche zuerst (Eikes Entscheidung 05.10.2026): bei nur ≈ 40 Episoden am Tag (Gemini-Tageslimit)
    sollen die Gespräche mit Friday vor denen anderer Personen im Graphen landen."""

    def _gespraech(self, person=None):
        g = self.p.gespraech_beginnen("chat", person=person)
        self.p.beitrag(g, "du", "hallo")
        self.p.zusammenfassen(g, "ein Satz")
        return g

    def test_graph_offen_zeigt_eikes_gespraeche_zuerst(self):
        fremd1 = self._gespraech(person="p1")
        eike1 = self._gespraech()
        fremd2 = self._gespraech(person="p2")
        eike2 = self._gespraech()
        self.assertEqual(self.p.graph_offen(), [eike1, eike2, fremd1, fremd2])

    def test_graph_zu_pruefen_zeigt_eikes_gespraeche_zuerst(self):
        alt = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds")
        ids = [self._gespraech(person="p1"), self._gespraech()]
        for g in ids:
            self.p.graph_markieren(g, "gesendet", neuer_versuch=True)
            self.p._schreiben("UPDATE gespraeche SET graph_gesendet = ? WHERE id = ?", (alt, g))
        self.assertEqual(self.p.graph_zu_pruefen(), [ids[1], ids[0]])

    def test_eigene_offen_zaehlt_nur_eikes_unfertige(self):
        fremd = self._gespraech(person="p1")
        self.assertFalse(self.p.graph_eigene_offen())
        eike = self._gespraech()
        self.assertTrue(self.p.graph_eigene_offen())
        self.p.graph_markieren(eike, "angekommen")
        self.assertFalse(self.p.graph_eigene_offen())
        self.p.graph_markieren(fremd, "gesendet", neuer_versuch=True)
        self.assertFalse(self.p.graph_eigene_offen())


if __name__ == "__main__":
    unittest.main()
