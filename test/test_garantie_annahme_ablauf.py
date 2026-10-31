"""Der ganze Ablauf einer Person über chat.antworten: Tafel-Foto → Angaben → Ticket → Nummern → Zusammenfassung → Antrag.

Gemini ist ein Skript (je Aufruf die nächste vorbereitete Antwort); die Werkzeuge, der Zugkontext, die Sperren und die Akte sind echt.
Messdaten (gemessene Fahrt), der Antrag selbst und das Ticket-Lesen sind Attrappen."""
import asyncio
import base64
import os
import tempfile
import unittest
from unittest import mock

from kern.gedaechtnis import graph
from kern.garantie import ablage
from kern.personen import register
from kern.sitzung import chat
from kern.skills import akten, fach

BILD = {"mime": "image/jpeg", "data": base64.b64encode(b"\xff\xd8jpeg").decode()}
VOLL = {"datum": "2026-10-12", "linie": "5", "abfahrt": "07:42", "start": "Hauptbahnhof", "ziel": "Empelde"}
GUT = {"ist_fahrkarte": True, "art": "Deutschlandticket", "bezeichnung": "Deutschlandticket", "herausgeber": "ÜSTRA",
       "gueltig_von": "2026-10-01", "gueltig_bis": "2026-10-31", "inhaber": "Kontakt B Muster", "abo_nummer": "", "kundenkarte": "123456",
       "ticketnummer": "AB12CD34", "preis": None, "form": "digital", "nummer_gedruckt": "", "ist_kundenkarte": False,
       "kundenkarte_handschrift": False, "zweifel": ""}


def lauf(c):
    return asyncio.run(c)


def ruf(name, **args):
    return ({"candidates": [{"content": {"parts": [{"functionCall": {"id": "1", "name": name, "args": args}}]}}]}, "m")


def text(t):
    return ({"candidates": [{"content": {"parts": [{"text": t}]}}]}, "m")


class Skript:
    """Gemini-Attrappe: gibt der Reihe nach die vorbereiteten Antworten und merkt sich, was Friday sah."""

    def __init__(self, *antworten):
        self.antworten, self.eingaben = list(antworten), []

    async def __call__(self, inhalt, **kw):
        self.eingaben.append(inhalt)
        return self.antworten.pop(0)

    def letztes_ergebnis(self) -> str:
        """Die Antwort des zuletzt aufgerufenen Werkzeugs (so, wie Friday sie bekam)."""
        return self.eingaben[-1]["contents"][-1]["parts"][0]["functionResponse"]["response"]["ergebnis"]


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "AIzaTEST",
                                                "GRAPHITI_URL": "http://graphiti:8000/mcp"})
        self.env.start()
        self.a = ablage.standard()
        self.profil = self.a.profil_anlegen({"name": "Kontakt B", "anrede": "Frau", "vorname": "Kontakt B", "nachname": "Muster",
                                             "email": "maya@example.net", "strasse": "Weg 1", "plz": "30449", "ort": "Hannover",
                                             "vollmacht": True})
        self.p = register.anlegen("Kontakt B Muster", snapchat="maya_x")
        register.aendern(self.p["id"], quellen={"garantie": self.profil})
        self.pid = self.p["id"]
        self.antrag = mock.AsyncMock(return_value={"ok": True, "antrag": {"id": 7}})
        from kern.werkzeuge import akte, garantie_annahme, ticket_annahme
        akte._registrieren(); garantie_annahme._registrieren(); ticket_annahme._registrieren()
        ticket_annahme._VORLAEUFIG.clear()
        for m in (mock.patch("kern.werkzeuge.akte.konnektoren.benutzbar", return_value=True),
                  mock.patch("kern.werkzeuge.akte._eike_melden"), mock.patch("kern.werkzeuge.akte._eike_fragen"),
                  mock.patch("kern.werkzeuge.akte.verwaltung"),
                  mock.patch("kern.werkzeuge.akte._fahrt_finden", mock.AsyncMock(return_value=({"reise": "r", "von": "a", "bis": "b"}, ""))),
                  mock.patch("kern.werkzeuge.akte.antrag.verlauf", mock.AsyncMock(return_value={"halte": [{"reference": "b", "verspaetung": 23}]})),
                  mock.patch("kern.werkzeuge.akte.antrag.vorbereiten", self.antrag),
                  mock.patch("kern.werkzeuge.ticket_annahme.garantieticket.auslesen", mock.AsyncMock(return_value=dict(GUT))),
                  mock.patch("kern.werkzeuge.ticket_annahme._eike_anliegen"),
                  mock.patch.object(graph, "suchen", mock.AsyncMock(return_value=[]))):
            m.start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        for i in akten._instanzen.values():
            i._db.close()
        akten._instanzen.clear()
        fach._instanzen.clear()
        self.env.stop()

    def zug(self, skript, *nachrichten):
        with mock.patch.object(chat, "_gemini_anfrage", skript):
            return lauf(chat.antworten(list(nachrichten), person={"id": self.pid}, kanal="Snapchat"))


class Einstieg(Basis):
    def test_friday_bekommt_skill_werkzeuge_und_das_foto(self):
        s = Skript(text("Wohin wolltest du fahren?"))
        r = self.zug(s, {"rolle": "du", "text": "ich will was einreichen", "bild": BILD})
        self.assertEqual(r["antwort"], "Wohin wolltest du fahren?")
        e = s.eingaben[0]
        system = e["systemInstruction"]["parts"][0]["text"]
        self.assertIn("garantie_annahme", system)
        self.assertIn("Anzeigetafel", system)
        tools = {f["name"] for g in e["tools"] for f in g["functionDeclarations"]}
        self.assertTrue({"akte_erfassen", "akte_zusammenfassung", "ticket_lesen", "ticket_bestaetigen",
                         "garantie_antrag_aus_akte"} <= tools, tools)
        self.assertTrue(any("inlineData" in t for t in e["contents"][0]["parts"]))        # Friday SIEHT das Foto

    def test_ohne_profil_gibt_es_den_ablauf_nicht(self):
        register.aendern(self.pid, quellen={})
        s = Skript(text("Hi!"))
        self.zug(s, {"rolle": "du", "text": "ich will was einreichen"})
        tools = {f["name"] for g in s.eingaben[0]["tools"] for f in g["functionDeclarations"]}
        self.assertNotIn("ticket_lesen", tools)

    def test_hebel_aus_nimmt_alle_werkzeuge_weg(self):
        register.aendern(self.pid, module={"garantie": "aus"})
        s = Skript(text("Hi"))
        self.zug(s, {"rolle": "du", "text": "ich will was einreichen"})
        tools = {f["name"] for g in s.eingaben[0]["tools"] for f in g["functionDeclarations"]}
        self.assertFalse(tools & {"ticket_lesen", "akte_zusammenfassung", "garantie_antrag_aus_akte"})


class Ablauf(Basis):
    def test_von_der_tafel_bis_zum_vorbereiteten_antrag(self):
        # Zug 1: Foto der Tafel → Friday hält alles fest und merkt, dass das Ticket fehlt
        s1 = Skript(ruf("akte_erfassen", skill="garantie_annahme", felder=VOLL), ruf("akte_fehlt", skill="garantie_annahme"),
                    text("Schickst du mir ein Foto deines Tickets?"))
        self.zug(s1, {"rolle": "du", "text": "Bus kam zu spät", "bild": BILD})
        self.assertIn("kein gültiges Ticket", s1.letztes_ergebnis())
        # Zug 2: Ticketfoto → ticket_lesen, Friday liest die Nummern vor, gespeichert ist noch nichts
        s2 = Skript(ruf("ticket_lesen"), text("Ich lese AB12CD34 und 123456 — stimmt das?"))
        self.zug(s2, {"rolle": "du", "text": "hier", "bild": BILD})
        self.assertIn("AB12CD34", s2.letztes_ergebnis())
        self.assertEqual(self.a.tickets(self.profil), [])
        # Zug 3: Person bestätigt → ticket_bestaetigen und gleich die Zusammenfassung
        s3 = Skript(ruf("ticket_bestaetigen", ticketnummer="AB12CD34", kundenkarte="123456"),
                    ruf("akte_zusammenfassung", skill="garantie_annahme"), text("Fahrt am 12.10.2026: Linie 5 um 07:42 von Hauptbahnhof nach Empelde. Stimmt das so?"))
        self.zug(s3, {"rolle": "du", "text": "ja stimmt"})
        t = self.a.tickets(self.profil)[0]
        self.assertTrue(t["nummern_geprueft"])
        self.assertEqual(t["pruefung_quelle"], "person")
        self.assertTrue(s3.letztes_ergebnis().startswith("Zusammenfassung"))
        # Zug 4: neue Nachricht („ja“) → Antrag wird vorbereitet
        s4 = Skript(ruf("garantie_antrag_aus_akte", skill="garantie_annahme"), text("Eike gibt ihn frei."))
        self.zug(s4, {"rolle": "du", "text": "ja, alles richtig"})
        self.assertIn("Der Antrag ist vorbereitet", s4.letztes_ergebnis())
        self.antrag.assert_awaited_once()

    def test_antrag_ohne_vorherige_zusammenfassung_wird_abgelehnt(self):
        s = Skript(ruf("akte_erfassen", skill="garantie_annahme", felder=VOLL), ruf("garantie_antrag_aus_akte", skill="garantie_annahme"),
                   text("Moment."))
        self.zug(s, {"rolle": "du", "text": "reich das ein"})
        self.assertIn("Zeig der Person erst die Zusammenfassung", s.letztes_ergebnis())
        self.antrag.assert_not_awaited()

    def test_ticketfoto_aus_einem_frueheren_zug_ist_nicht_lesbar(self):
        s = Skript(ruf("ticket_lesen"), text("Schick mir bitte ein Foto."))
        self.zug(s, {"rolle": "du", "text": "ich habs dir vorhin geschickt"})
        self.assertIn("kein Bild", s.letztes_ergebnis())

    def test_zweite_fahrt_hat_eine_eigene_akte(self):
        s = Skript(ruf("akte_erfassen", skill="garantie_annahme", felder=VOLL),
                   ruf("akte_erfassen", skill="garantie_annahme", vorgang="fahrt2", felder={**VOLL, "linie": "6"}), text("ok"))
        self.zug(s, {"rolle": "du", "text": "zwei Fahrten"})
        st = akten.standard()
        a1, a2 = st.finden("garantie_annahme", self.pid, "v1"), st.finden("garantie_annahme", self.pid, "fahrt2")
        self.assertEqual((st.werte(a1["id"])["linie"], st.werte(a2["id"])["linie"]), ("5", "6"))


class Zustellung(Basis):
    """Die Zusammenfassung gilt erst als gezeigt, wenn die Antwort sie nennt — und ein Neuversuch ist kein neuer Zug."""

    def vorbereiten(self, felder=VOLL):
        """Angaben und Ticket sind da; die Zusammenfassung ist gebaut (aber noch nicht zugestellt)."""
        self.zug(Skript(ruf("akte_erfassen", skill="garantie_annahme", felder=felder), text("ok")), {"rolle": "du", "text": "Daten"})
        self.zug(Skript(ruf("ticket_lesen"), text("stimmt das?")), {"rolle": "du", "text": "Ticket", "bild": BILD})
        self.zug(Skript(ruf("ticket_bestaetigen", ticketnummer="AB12CD34", kundenkarte="123456"), text("danke")),
                 {"rolle": "du", "text": "ja stimmt"})

    def test_nennt_friday_die_zusammenfassung_nicht_haengt_der_server_sie_an(self):
        self.vorbereiten()
        r = self.zug(Skript(ruf("akte_zusammenfassung", skill="garantie_annahme"), text("Alles klar, ich kümmere mich.")),
                     {"rolle": "du", "text": "wie weit?"})
        for stueck in ("Alles klar, ich kümmere mich.", "Linie 5", "07:42", "Hauptbahnhof", "Empelde", "Stimmt das so?"):
            self.assertIn(stueck, r["antwort"])
        s = Skript(ruf("garantie_antrag_aus_akte", skill="garantie_annahme"), text("Eike gibt ihn frei."))
        self.zug(s, {"rolle": "du", "text": "ja klar"})
        self.assertIn("Der Antrag ist vorbereitet", s.letztes_ergebnis())          # die Person HAT sie gesehen und geantwortet

    def test_gemini_fehler_nach_der_zusammenfassung_gibt_den_antrag_nicht_frei(self):
        self.vorbereiten()

        class Kaputt(Skript):
            async def __call__(self, inhalt, **kw):
                if not self.antworten:
                    raise chat.ChatFehler("Gemini weg")
                return await super().__call__(inhalt, **kw)
        with self.assertRaises(chat.ChatFehler):
            self.zug(Kaputt(ruf("akte_zusammenfassung", skill="garantie_annahme")), {"rolle": "du", "text": "wie weit?"})
        s = Skript(ruf("garantie_antrag_aus_akte", skill="garantie_annahme"), text("..."))
        self.zug(s, {"rolle": "du", "text": "hallo?"})
        self.assertIn("Zeig der Person erst die Zusammenfassung", s.letztes_ergebnis())
        self.antrag.assert_not_awaited()

    def test_im_selben_zug_zeigen_und_antrag_ist_gesperrt(self):
        self.vorbereiten()
        s = Skript(ruf("akte_zusammenfassung", skill="garantie_annahme"), ruf("garantie_antrag_aus_akte", skill="garantie_annahme"),
                   text("Fahrt am 12.10.2026: Linie 5 um 07:42 von Hauptbahnhof nach Empelde. Stimmt das so?"))
        self.zug(s, {"rolle": "du", "text": "mach mal"})
        self.assertIn("Zeig der Person erst die Zusammenfassung", s.letztes_ergebnis())
        self.antrag.assert_not_awaited()

    def test_neuversuch_mit_denselben_nachrichten_ist_kein_neuer_zug(self):
        self.vorbereiten()
        nachricht = {"rolle": "du", "text": "wie weit?"}
        self.zug(Skript(ruf("akte_zusammenfassung", skill="garantie_annahme"),
                        text("Fahrt am 12.10.2026: Linie 5 um 07:42 von Hauptbahnhof nach Empelde. Stimmt das?")), nachricht)
        s = Skript(ruf("garantie_antrag_aus_akte", skill="garantie_annahme"), text("..."))
        self.zug(s, dict(nachricht))                                  # die Wache verarbeitet dieselbe Nachricht noch einmal
        self.assertIn("gerade erst gezeigt", s.letztes_ergebnis())
        self.antrag.assert_not_awaited()


class OhneLinie(Basis):
    """„Ich bin von Hauptbahnhof nach Empelde gefahren“ — Linie und Uhrzeit kennt die Person nicht, das System sucht die Fahrt."""

    def test_von_start_nach_ziel_ohne_linie_bis_zum_antrag(self):
        KURZ = {"datum": "2026-10-12", "start": "Hauptbahnhof", "ziel": "Empelde"}
        fund = [{"reise": "r", "von": "a", "bis": "b", "linie": "5", "uhr": "07:42", "plan": None}]
        with mock.patch("kern.werkzeuge.garantie_annahme.fahrten_suchen", mock.AsyncMock(return_value=fund)):
            self.zug(Skript(ruf("akte_erfassen", skill="garantie_annahme", felder=KURZ), text("ok")), {"rolle": "du", "text": "Daten"})
            self.zug(Skript(ruf("ticket_lesen"), text("stimmt das?")), {"rolle": "du", "text": "Ticket", "bild": BILD})
            self.zug(Skript(ruf("ticket_bestaetigen", ticketnummer="AB12CD34", kundenkarte="123456"), text("danke")),
                     {"rolle": "du", "text": "ja stimmt"})
            s = Skript(ruf("akte_zusammenfassung", skill="garantie_annahme"),
                       text("Fahrt am 12.10.2026: Linie 5 um 07:42 von Hauptbahnhof nach Empelde. Stimmt das so?"))
            self.zug(s, {"rolle": "du", "text": "wie weit sind wir?"})
            self.assertTrue(s.letztes_ergebnis().startswith("Zusammenfassung"), s.letztes_ergebnis())
            s4 = Skript(ruf("garantie_antrag_aus_akte", skill="garantie_annahme"), text("Eike gibt ihn frei."))
            self.zug(s4, {"rolle": "du", "text": "ja, genau die"})
        self.assertIn("Der Antrag ist vorbereitet", s4.letztes_ergebnis())
        self.antrag.assert_awaited_once()
