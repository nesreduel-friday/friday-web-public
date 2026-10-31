"""Fokus-Personen, Quelle Mail: nur mit Konnektor, nur neue UIDs, kurze Texte, Mails bleiben ungelesen."""
import asyncio
import unittest
from unittest import mock

from kern.fokus import quellen


def person(letzte=0):
    return {"uuid": "u", "name": "Summer Muster", "quellen": {"whatsapp": {"chats": {}}, "mail": {"letzte_uid": letzte}}}


KOEPFE = [{"uid": 12, "datum": "2026-10-05", "von": "Summer <s@x.de>", "an": "Eike", "betreff": "Treffen", "gelesen": False, "groesse": 10},
          {"uid": 11, "datum": "2026-10-04", "von": "Summer <s@x.de>", "an": "Eike", "betreff": "Alt", "gelesen": True, "groesse": 10}]


class Mail(unittest.TestCase):
    def _lauf(self, p, aktiv=True, koepfe=KOEPFE, text="Hallo Eike, am Freitag um 16 Uhr?"):
        gefragt = []
        def suchen(anfrage, anzahl):
            gefragt.append(anfrage)
            return koepfe
        def holen(uid):
            return {"uid": uid, "roh": b"x", "gelesen": False, "groesse": 10, "nur_kopf": False}
        with mock.patch.object(quellen.konnektoren, "aktiv", return_value=aktiv), \
             mock.patch.object(quellen, "_suchen", side_effect=suchen), \
             mock.patch.object(quellen, "_holen", side_effect=holen), \
             mock.patch.object(quellen, "mail_zerlegen", return_value={"text": text, "betreff": "Treffen", "von": "Summer", "datum": ""}):
            return asyncio.run(quellen.mail_lesen(p)), gefragt

    def test_nur_neue_uids(self):
        r, gefragt = self._lauf(person(letzte=11))
        self.assertEqual([e["betreff"] for e in r["eintraege"]], ["Treffen"])
        self.assertEqual(r["zeiger"], {"letzte_uid": 12})
        self.assertIn('from:"Summer Muster"', gefragt[0])
        self.assertIn("newer_than:14d", gefragt[0])

    def test_ohne_konnektor_leer_und_keine_anfrage(self):
        r, gefragt = self._lauf(person(), aktiv=False)
        self.assertEqual((r["eintraege"], gefragt), ([], []))
        self.assertIn("Konnektor aus", r["stand"])

    def test_text_wird_gekuerzt(self):
        r, _ = self._lauf(person(), text="x" * 5000)
        self.assertEqual(len(r["eintraege"][0]["text"]), quellen.MAIL_ZEICHEN)

    def test_hoechstens_5_mails_je_lauf(self):
        viele = [{"uid": 100 + i, "datum": "d", "von": "S", "an": "E", "betreff": f"B{i}", "gelesen": False, "groesse": 1} for i in range(9)]
        r, _ = self._lauf(person(), koepfe=viele)
        self.assertEqual(len(r["eintraege"]), quellen.MAILS_JE_LAUF)

    def test_anfragezeichen_im_namen_brechen_die_suche_nicht(self):
        self.assertEqual(quellen._anfrage('Su"mmer'), '(from:"Summer" OR to:"Summer") newer_than:14d')

    def test_anmeldefehler_wird_gemeldet_ohne_zugangsdaten(self):
        def suchen(anfrage, anzahl):
            raise quellen.gmail.AnmeldeFehler("Gmail-Anmeldung abgelehnt")
        with mock.patch.object(quellen.konnektoren, "aktiv", return_value=True), mock.patch.object(quellen, "_suchen", side_effect=suchen):
            r = asyncio.run(quellen.mail_lesen(person()))
        self.assertEqual(r["eintraege"], [])
        self.assertIn("abgelehnt", r["fehler"])
        self.assertEqual(r["zeiger"], {})                               # kein Zeiger bei Fehler


if __name__ == "__main__":
    unittest.main()
