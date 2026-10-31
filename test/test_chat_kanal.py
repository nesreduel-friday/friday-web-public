import asyncio
import unittest
from unittest import mock

from kern.gehirn import persona
from kern.sitzung import chat


def lauf(coro):
    return asyncio.run(coro)


ANTWORT = ({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, "m")


class KanalTest(unittest.TestCase):
    def test_kanal_ersetzt_die_umgebung_im_system_prompt(self):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=ANTWORT)) as m, \
             mock.patch.object(chat.umgebung, "beschreibung", return_value="[UMGEBUNG] GeraetXYZ") as m_umg:
            lauf(chat.antworten([{"rolle": "du", "text": "Hi"}], kanal="[KANAL] Snapchat"))
        system = m.await_args.args[0]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("[KANAL] Snapchat", system)
        self.assertNotIn("GeraetXYZ", system)
        m_umg.assert_not_called()

    def test_ohne_kanal_bleibt_alles_wie_im_web(self):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=ANTWORT)) as m, \
             mock.patch.object(chat.umgebung, "beschreibung", return_value="[UMGEBUNG] GeraetXYZ"):
            lauf(chat.antworten([{"rolle": "du", "text": "Hi"}]))
        self.assertIn("GeraetXYZ", m.await_args.args[0]["systemInstruction"]["parts"][0]["text"])

    def test_snapchat_kanal_nennt_die_grenzen(self):
        self.assertIn("Snapchat", persona.SNAPCHAT_KANAL)
        self.assertIn("Markdown", persona.SNAPCHAT_KANAL)
        self.assertIn("Snap", persona.SNAPCHAT_KANAL)


class OffenerVorschlagTest(unittest.TestCase):
    """07.10.2026: Nach „ja“ rief Gemini das Sende-Werkzeug nicht auf und erfand eine Absage — der Verlauf trägt nur
    Text, vom wartenden Vorschlag wusste das Modell nichts. Für Nachrichten entfällt das Ja (schicke_nachricht);
    Kontaktanfragen und WhatsApp brauchen es noch."""

    def _system(self, text):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=ANTWORT)) as m:
            lauf(chat.antworten([{"rolle": "du", "text": text}], kanal=persona.SNAPCHAT_KANAL))
        return m.await_args.args[0]["systemInstruction"]["parts"][0]["text"]

    def setUp(self):
        from kern.werkzeuge import snapchat as wsnap
        self.wsnap = wsnap
        self.addCleanup(setattr, wsnap, "_anfrage_vorschlag", None)

    def _anfrage(self, alter=0):
        self.wsnap._anfrage_vorschlag = {"name": "Kontakt S", "snapchat": "kontakt_s", "pid": "",
                                         "zeit": self.wsnap.uhr.monotonic() - alter}

    def test_ja_mit_wartender_anfrage_nennt_anfrage_senden(self):
        self._anfrage()
        system = self._system("ja mach")
        self.assertIn("[ANFRAGE WARTET]", system)
        self.assertIn("Kontakt S", system)

    def test_ohne_ja_kein_hinweis(self):
        self._anfrage()
        self.assertNotIn("[ANFRAGE WARTET]", self._system("wie spät ist es"))

    def test_ohne_vorschlag_kein_hinweis(self):
        self.wsnap._anfrage_vorschlag = None
        self.assertNotIn("[ANFRAGE WARTET]", self._system("ja mach"))

    def test_abgelaufener_vorschlag_kein_hinweis(self):
        self._anfrage(alter=self.wsnap.VORSCHLAG_GILT_S + 1)
        self.assertNotIn("[ANFRAGE WARTET]", self._system("ja mach"))


class WeitereVorschlaegeTest(OffenerVorschlagTest):
    """Dieselbe Lücke bei Kontaktanfrage und WhatsApp."""

    def setUp(self):
        super().setUp()
        from kern.werkzeuge import whatsapp as wwa
        self.wwa = wwa
        self.addCleanup(setattr, wwa, "_vorschlag", None)
        self.addCleanup(setattr, self.wsnap, "_anfrage_vorschlag", None)


    def test_whatsapp_wartet(self):
        self.wwa._vorschlag = {"id": "x", "name": "Kontakt P", "text": "Bin gleich da", "zeit": self.wwa.uhr.monotonic()}
        system = self._system("ja mach")
        self.assertIn("[VORSCHLAG WARTET]", system)
        self.assertIn("Kontakt P", system)
        self.wwa._vorschlag["zeit"] -= self.wwa.VORSCHLAG_GILT_S + 1
        self.assertNotIn("[VORSCHLAG WARTET]", self._system("ja mach"))


class RollenmodellTest(unittest.TestCase):
    """07.10.2026: Friday schrieb „deine Arbeiten“ an einen Dritten — Rollenmodell + Absender/Empfänger-Zeile je Zug."""

    def setUp(self):
        import os
        import tempfile
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)

    def _system(self, **kw):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=ANTWORT)) as m:
            lauf(chat.antworten([{"rolle": "du", "text": "Hi"}], **kw))
        return m.await_args.args[0]["systemInstruction"]["parts"][0]["text"]

    def test_eike_bekommt_rollenmodell_und_zugzeile(self):
        s = self._system(kanal=persona.SNAPCHAT_KANAL)
        self.assertIn("dritten Person", s)
        self.assertIn("nie als Eike", s)
        self.assertIn("Absender dieses Zuges: Eike. Deine Antwort geht an: Eike.", s)

    def test_rollenmodell_steht_auch_im_sprachprompt(self):
        from kern import config
        self.assertIn("nie als Eike", persona.live_prompt(config.jetzt()))

    def test_person_bekommt_zugzeile_mit_name_aber_nicht_eikes_rolle(self):
        from kern.personen import register
        p = register.anlegen("Kontakt B", snapchat="maya_x")
        s = self._system(kanal="Snapchat", person=p)
        self.assertIn("Absender dieses Zuges: Kontakt B (Kontakt, nicht Eike). Deine Antwort geht an: Kontakt B.", s)
        self.assertNotIn("nie als Eike", s)

    def test_kein_inhalt_erfinden_regel_in_persona_und_werkzeug(self):
        """Live 07.10.2026: „schick Test eine Nachricht“ ohne Inhalt → Friday erfand „Testnachricht von Friday“."""
        self.assertIn("erfinde keinen Inhalt", persona.ROLLE_AN_DRITTE)
        from kern.werkzeuge import snapchat as wsnap
        beschreibung = next(e["description"] for e in wsnap.ERKLAERUNGEN if e["name"] == "schicke_nachricht")
        self.assertIn("erfinde keinen Inhalt", beschreibung)
