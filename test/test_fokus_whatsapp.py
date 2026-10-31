"""Fokus-Personen, Quelle WhatsApp: nur freigegebene Chats, Zeiger, Gruppen filtern auf die Person."""
import asyncio
import unittest
from unittest import mock

from kern.fokus import quellen

JETZT = 1_760_000_000


def person(zeiger=None):
    return {"uuid": "u", "name": "Summer Muster", "quellen": {"whatsapp": {"chats": dict(zeiger or {})}, "mail": {"letzte_uid": 0}}}


def nachricht(i, stempel, von, text, von_mir=False):
    return {"id": str(i), "zeit": "x", "stempel": stempel, "von": von, "von_mir": von_mir, "text": text, "medien": False}


class Namen(unittest.TestCase):
    def test_in_gruppen_zaehlt_nur_die_person(self):
        self.assertTrue(quellen.von_person("Summer", "Summer Muster"))
        self.assertTrue(quellen.von_person("Summer Muster", "Summer Muster"))
        self.assertFalse(quellen.von_person("Summer Meier", "Summer Muster"))      # gleicher Vorname, anderer Mensch
        self.assertFalse(quellen.von_person("Kontakt", "Summer Muster"))
        self.assertFalse(quellen.von_person("", "Summer Muster"))


class Namensgleichheit(unittest.TestCase):
    def test_vorname_mehrdeutig_wenn_ein_anderer_denselben_hat(self):
        self.assertTrue(quellen.vorname_mehrdeutig("Kontakt Muster", {"kontakt muster", "kontakt meier"}))
        self.assertFalse(quellen.vorname_mehrdeutig("Kontakt Muster", {"kontakt muster", "summer mustermann"}))
        self.assertFalse(quellen.vorname_mehrdeutig("Kontakt Muster", set()))

    def test_nur_vorname_und_voller_name_sind_dieselbe_person(self):
        # Im Graphen heißt die Person „Kontakt“, ihr Chat „Kontakt Muster“ — das ist kein Namensstreit.
        self.assertFalse(quellen.vorname_mehrdeutig("Kontakt", {"kontakt", "kontakt muster"}))
        self.assertFalse(quellen.vorname_mehrdeutig("Kontakt Muster", {"kontakt muster", "kontakt"}))
        self.assertTrue(quellen.vorname_mehrdeutig("Kontakt", {"kontakt muster", "kontakt meier"}))
        self.assertTrue(quellen.vorname_mehrdeutig("Kontakt Muster", {"kontakt muster", "kontakt meier", "kontakt"}))

    def test_nur_vorname_in_der_gruppe_ist_bei_namensgleichheit_unklar(self):
        self.assertFalse(quellen.von_person("Kontakt", "Kontakt Muster", mehrdeutig=True))
        self.assertTrue(quellen.von_person("Kontakt", "Kontakt Muster", mehrdeutig=False))

    def test_mit_nachnamen_oder_zuordnung_ist_es_klar(self):
        self.assertTrue(quellen.von_person("Kontakt Muster", "Kontakt Muster", mehrdeutig=True))
        self.assertFalse(quellen.von_person("Kontakt Meier", "Kontakt Muster", mehrdeutig=True))
        self.assertTrue(quellen.von_person("Kontakt", "Kontakt Muster", mehrdeutig=True, bekannt=("kontakt",)))
        self.assertFalse(quellen.von_person("Kontakt", "Kontakt Muster", mehrdeutig=True, bekannt=("kontakt a",)))


class Lesen(unittest.TestCase):
    def _lauf(self, p, frei, treffer, antworten, jetzt=JETZT):
        nachr = mock.AsyncMock(side_effect=lambda chat_id, anzahl, frisch=False: {"nachrichten": antworten[chat_id]})
        with mock.patch.object(quellen.whatsapp, "finden", return_value=treffer), \
             mock.patch.object(quellen.whatsapp, "frei", return_value=frei), \
             mock.patch.object(quellen.whatsapp, "nachrichten", nachr):
            r = asyncio.run(quellen.whatsapp_lesen(p, jetzt_stempel=jetzt, pause=(0, 0)))
        return r, nachr

    def test_einzelchat_neue_nachrichten_ueber_dem_zeiger(self):
        chat = {"id": "49@c.us", "name": "Summer Muster"}
        r, _ = self._lauf(person({"49@c.us": JETZT - 100}), [chat], [chat],
                          {"49@c.us": [nachricht(1, JETZT - 200, "Summer", "alt"), nachricht(2, JETZT - 50, "Summer", "Freitag Info"),
                                       nachricht(3, JETZT - 10, "Eike", "ok", von_mir=True)]})
        self.assertEqual([e["text"] for e in r["eintraege"]], ["Freitag Info", "ok"])
        self.assertEqual(r["zeiger"]["chats"]["49@c.us"], JETZT - 10)

    def test_erster_lauf_liest_nur_die_letzten_7_tage(self):
        chat = {"id": "49@c.us", "name": "Summer Muster"}
        r, _ = self._lauf(person(), [chat], [chat],
                          {"49@c.us": [nachricht(1, JETZT - 8 * 86400, "Summer", "zu alt"), nachricht(2, JETZT - 86400, "Summer", "gestern")]})
        self.assertEqual([e["text"] for e in r["eintraege"]], ["gestern"])

    def test_gruppe_nur_nachrichten_der_person(self):
        gruppe = {"id": "12@g.us", "name": "JG12"}
        r, _ = self._lauf(person({"12@g.us": 0}), [gruppe], [],
                          {"12@g.us": [nachricht(1, JETZT - 5, "Summer", "Ich bin am Mittwoch dabei"),
                                       nachricht(2, JETZT - 4, "Summer Meier", "andere Person"),
                                       nachricht(3, JETZT - 3, "Kontakt", "Hallo")]})
        self.assertEqual([e["text"] for e in r["eintraege"]], ["Ich bin am Mittwoch dabei"])
        self.assertEqual(r["zeiger"]["chats"]["12@g.us"], JETZT - 3)     # der Zeiger folgt dem ganzen Chat, nicht nur der Person

    def _gruppe_mit_namensgleichheit(self, zuordnung=None):
        gruppe = {"id": "12@g.us", "name": "JG12"}
        p = person({"12@g.us": 0})
        p["name"] = "Kontakt Muster"
        if zuordnung:
            p["quellen"]["whatsapp"]["zuordnung"] = zuordnung
        with mock.patch.object(quellen, "bekannte_namen", return_value={"kontakt muster", "kontakt meier"}):
            return self._lauf(p, [gruppe], [], {"12@g.us": [nachricht(1, JETZT - 5, "Kontakt", "Ich komme am Freitag"),
                                                          nachricht(2, JETZT - 4, "Kontakt Muster", "Mit Nachname klar"),
                                                          nachricht(3, JETZT - 3, "Nele", "Hallo")]})

    def test_gruppe_bei_namensgleichheit_nur_mit_nachname_und_unklares_wird_gemeldet(self):
        r, _ = self._gruppe_mit_namensgleichheit()
        self.assertEqual([e["text"] for e in r["eintraege"]], ["Mit Nachname klar"])
        self.assertEqual([(u["absender"], u["anzahl"], u["chat"]) for u in r["unklar"]], [("Kontakt", 1, "JG12")])

    def test_zugeordneter_absender_zaehlt_dann(self):
        r, _ = self._gruppe_mit_namensgleichheit({"12@g.us": ["Kontakt"]})
        self.assertEqual([e["text"] for e in r["eintraege"]], ["Ich komme am Freitag", "Mit Nachname klar"])
        self.assertEqual(r["unklar"], [])

    def test_mehrere_passende_einzelchats_kein_chat_und_hinweis(self):
        a, b = {"id": "1@c.us", "name": "Summer Muster"}, {"id": "2@c.us", "name": "Summer Muster 2"}
        r, nachr = self._lauf(person(), [a, b], [a, b], {})
        self.assertEqual(r["eintraege"], [])
        self.assertIn("mehrere Chats passen", r["stand"])
        nachr.assert_not_awaited()

    def test_ohne_freigegebenen_chat_wird_nichts_abgefragt(self):
        r, nachr = self._lauf(person(), [], [], {})
        self.assertEqual(r["eintraege"], [])
        self.assertIn("kein freigegebener Chat", r["stand"])
        nachr.assert_not_awaited()

    def test_fehler_im_dienst_wird_gemeldet_und_stoppt_nichts_anderes(self):
        chat = {"id": "49@c.us", "name": "Summer Muster"}
        async def kaputt(chat_id, anzahl, frisch=False):
            raise quellen.whatsapp.WhatsAppFehler("WAHA antwortet nicht")
        with mock.patch.object(quellen.whatsapp, "finden", return_value=[chat]), \
             mock.patch.object(quellen.whatsapp, "frei", return_value=[chat]), \
             mock.patch.object(quellen.whatsapp, "nachrichten", kaputt):
            r = asyncio.run(quellen.whatsapp_lesen(person(), jetzt_stempel=JETZT, pause=(0, 0)))
        self.assertEqual(r["eintraege"], [])
        self.assertIn("WAHA", r["fehler"])
        self.assertEqual(r["zeiger"]["chats"], {})                      # kein Zeiger bei Fehler

    def test_modul_kennt_kein_senden(self):
        import inspect
        text = inspect.getsource(quellen)
        self.assertNotIn(".senden(", text)
        self.assertNotIn("sendSeen", text)


if __name__ == "__main__":
    unittest.main()
