"""Fokus-Personen: Vorrang in Rückblick, Profil-Nachholen und Vorwärmen der Wissensseiten."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.chats import ablage as chatsablage, auswertung
from kern.fokus import ablage
from kern.gedaechtnis import rueckblick, seite
from kern.protokoll import speicher as pspeicher

U = "11111111-1111-1111-1111-111111111111"


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "FRIDAY_HINTERGRUND": "aus"})
        self.env.start()
        self.p = pspeicher.standard()

    def tearDown(self):
        self.env.stop()


class Rueckblick(Basis):
    def _gespraech(self, text, person=None):
        g = self.p.gespraech_beginnen("chat", person=person)
        self.p.beitrag(g, "du", "hallo")
        self.p.zusammenfassen(g, text)
        return g

    def test_einspeisen_nimmt_zuerst_das_gespraech_der_markierten_person(self):
        eike = self._gespraech("Eike plant die Schule.")
        summer = self._gespraech("Eike und Summer Muster reden über Freitag.")
        ablage.markieren(U, "Summer Muster", True)
        with mock.patch.object(pspeicher, "standard", return_value=self.p), \
             mock.patch.object(rueckblick, "zusammenfassen", mock.AsyncMock()), \
             mock.patch.object(rueckblick, "sichern"), mock.patch.object(rueckblick, "_gelegentlich_verdichten", mock.AsyncMock()), \
             mock.patch.object(self.p, "graph_zu_pruefen", return_value=[]), \
             mock.patch.object(self.p, "graph_in_arbeit", return_value=0), \
             mock.patch.object(rueckblick, "einspeisen", mock.AsyncMock()) as ein:
            asyncio.run(rueckblick.durchgang())
        self.assertEqual([c.args[0] for c in ein.await_args_list][:2], [summer, eike])

    def test_ohne_markierte_person_bleibt_die_reihenfolge(self):
        eike = self._gespraech("Eike plant die Schule.")
        summer = self._gespraech("Eike und Summer Muster reden über Freitag.")
        with mock.patch.object(pspeicher, "standard", return_value=self.p), \
             mock.patch.object(rueckblick, "zusammenfassen", mock.AsyncMock()), \
             mock.patch.object(rueckblick, "sichern"), mock.patch.object(rueckblick, "_gelegentlich_verdichten", mock.AsyncMock()), \
             mock.patch.object(self.p, "graph_zu_pruefen", return_value=[]), \
             mock.patch.object(self.p, "graph_in_arbeit", return_value=0), \
             mock.patch.object(rueckblick, "einspeisen", mock.AsyncMock()) as ein:
            asyncio.run(rueckblick.durchgang())
        self.assertEqual([c.args[0] for c in ein.await_args_list][:2], [eike, summer])


class ProfileNachholen(Basis):
    def test_profile_markierter_personen_kommen_zuerst(self):
        ablage.markieren(U, "Summer Muster", True)
        personen = [{"id": 1, "name": "Nele", "profil": {"x": 1}}, {"id": 2, "name": "Summer Muster", "profil": {"x": 1}}]
        self.assertEqual([p["name"] for p in auswertung.fokus_zuerst(personen)], ["Summer Muster", "Nele"])

    def test_waehrend_eikes_gespraeche_offen_sind_zaehlen_nur_markierte(self):
        ablage.markieren(U, "Summer Muster", True)
        personen = [{"id": 1, "name": "Nele", "profil": {"x": 1}}, {"id": 2, "name": "Summer Muster", "profil": {"x": 1}}]
        self.assertEqual([p["name"] for p in auswertung.nur_fokus(personen)], ["Summer Muster"])


class Vorwaermen(Basis):
    def test_markierte_personen_werden_zuerst_vorgebaut(self):
        ablage.markieren(U, "Summer Muster", True)
        reihenfolge = []
        async def seite_(uuid, modell=True):
            reihenfolge.append(uuid)
        async def themen():
            return {"themen": [{"uuid": "t1"}, {"uuid": "t2"}]}
        async def uebersicht():
            return {"du": {"uuid": "du"}}
        with mock.patch.object(seite, "seite", seite_), \
             mock.patch("kern.gedaechtnis.themen.themen", themen), \
             mock.patch.object(seite.wissensgraph, "uebersicht", uebersicht):
            asyncio.run(seite._vorwaermen_runde())
        self.assertEqual(reihenfolge[:3], [U, "du", "t1"])


if __name__ == "__main__":
    unittest.main()
