"""WhatsApp: lesen nur mit Freigabe, nichts senden — ohne Netz (WAHA nachgestellt)."""

import asyncio
import inspect
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from kern import konnektoren
from kern.dienste import whatsapp
from kern.main import app
from kern.werkzeuge import katalog
from kern.werkzeuge import whatsapp as werkzeug

MAMA = "4915112345678@c.us"
FAMILIE = "120363000000000000@g.us"
FREMD = "4915199999999@c.us"

UEBERSICHT = [
    {"id": MAMA, "name": "Kontakt P"},
    {"id": FAMILIE, "name": "Familie"},
    {"id": FREMD, "name": "Jemand anders"},
]
NACHRICHTEN = [
    {"id": "b", "timestamp": 1758380400, "from": MAMA, "fromMe": False, "body": "Bis später!",
     "hasMedia": False, "_data": {"notifyName": "Kontakt P"}},
    {"id": "a", "timestamp": 1758380000, "from": MAMA, "fromMe": True, "body": "Alles klar",
     "hasMedia": False},
]


class Mit_WhatsApp(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner,
                                                "WAHA_URL": "http://waha:3000",
                                                "WAHA_SCHLUESSEL": "geheim"})
        self.env.start()
        konnektoren.setzen("whatsapp", True)
        self.client = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def _frei(self, *chats):
        whatsapp.frei_setzen([{"id": c, "name": {MAMA: "Kontakt P", FAMILIE: "Familie"}.get(c, "?")}
                              for c in chats])

    def _senden_frei(self, *chats, schreibrecht=True):
        """Lesen für alle genannten, Senden für den ERSTEN — und das Schreibrecht dazu.

        Drei Riegel, die zusammen müssen: Jeder Test schaltet genau einen davon ab
        und prüft, dass nichts hinausgeht."""
        whatsapp.frei_setzen([{"id": c, "name": {MAMA: "Kontakt P", FAMILIE: "Familie"}.get(c, "?"),
                               "senden": i == 0} for i, c in enumerate(chats)])
        konnektoren.schreiben_setzen("whatsapp", schreibrecht)
        whatsapp.sende_zaehler_leeren()
        werkzeug._vorschlag = None

    def _antwort(self, daten, code=200):
        return mock.patch("httpx.AsyncClient.request", mock.AsyncMock(return_value=httpx.Response(
            code, json=daten, request=httpx.Request("GET", "http://waha:3000/"))))


class Riegel(Mit_WhatsApp):
    """Der Kern der Sache: Ohne Häkchen kommt Friday an keinen Chat — und zwar
    schon im Dienst, nicht erst im Werkzeug."""

    def test_ohne_freigabe_kein_chat(self):
        with self._antwort(NACHRICHTEN) as ruf:
            with self.assertRaises(whatsapp.WhatsAppFehler) as f:
                asyncio.run(whatsapp.nachrichten(MAMA))
        self.assertIn("nicht freigegeben", str(f.exception))
        ruf.assert_not_called()                      # gar nicht erst gefragt

    def test_ein_anderer_chat_bleibt_gesperrt(self):
        self._frei(MAMA)
        with self._antwort(NACHRICHTEN) as ruf:
            with self.assertRaises(whatsapp.WhatsAppFehler):
                asyncio.run(whatsapp.nachrichten(FREMD))
        ruf.assert_not_called()

    def test_freigegebener_chat_geht(self):
        self._frei(MAMA)
        with self._antwort(NACHRICHTEN):
            stand = asyncio.run(whatsapp.nachrichten(MAMA))
        self.assertEqual(stand["chat"], "Kontakt P")
        self.assertEqual([n["text"] for n in stand["nachrichten"]], ["Alles klar", "Bis später!"])

    def test_haekchen_wegnehmen_sperrt_sofort(self):
        self._frei(MAMA)
        self.assertTrue(whatsapp.ist_frei(MAMA))
        whatsapp.frei_setzen([])
        self.assertFalse(whatsapp.ist_frei(MAMA))
        with self._antwort(NACHRICHTEN) as ruf:
            with self.assertRaises(whatsapp.WhatsAppFehler):
                asyncio.run(whatsapp.nachrichten(MAMA))
        ruf.assert_not_called()

    def test_liste_liegt_mit_rechten_600(self):
        self._frei(MAMA)
        pfad = Path(self.ordner) / "whatsapp_chats.json"
        self.assertEqual(stat.S_IMODE(pfad.stat().st_mode), 0o600)

    def test_doppelte_und_leere_werden_verworfen(self):
        whatsapp.frei_setzen([{"id": MAMA, "name": "Kontakt P"}, {"id": MAMA, "name": "Kontakt P"},
                              {"id": "", "name": "leer"}])
        self.assertEqual([c["id"] for c in whatsapp.frei()], [MAMA])


class Zwischenspeicher(Mit_WhatsApp):
    """Eikes Vorgabe (20.09.2026): Nachrichten gelten eine Stunde als frisch und
    werden beim Abruf durch das Werkzeug erneuert."""

    def setUp(self):
        super().setUp()
        whatsapp.vergessen()
        self._frei(MAMA)

    def tearDown(self):
        whatsapp.vergessen()
        super().tearDown()

    def test_zweiter_abruf_geht_nicht_noch_einmal_ins_netz(self):
        with self._antwort(NACHRICHTEN) as ruf:
            asyncio.run(whatsapp.nachrichten(MAMA))
            stand = asyncio.run(whatsapp.nachrichten(MAMA))
        self.assertEqual(ruf.call_count, 1)
        self.assertEqual([n["text"] for n in stand["nachrichten"]], ["Alles klar", "Bis später!"])

    def test_nach_einer_stunde_wird_neu_geholt(self):
        """Fest auf eine Stunde geprüft, nicht über MERKEN_S selbst gealtert —
        sonst wäre der Test zirkulär und ein geänderter Wert fiele nicht auf."""
        self.assertEqual(whatsapp.MERKEN_S, 3600)
        with self._antwort(NACHRICHTEN) as ruf:
            asyncio.run(whatsapp.nachrichten(MAMA))
            whatsapp._zwischen[MAMA]["zeitpunkt"] -= 3601
            asyncio.run(whatsapp.nachrichten(MAMA))
        self.assertEqual(ruf.call_count, 2)

    def test_kurz_vor_der_stunde_noch_nicht(self):
        with self._antwort(NACHRICHTEN) as ruf:
            asyncio.run(whatsapp.nachrichten(MAMA))
            whatsapp._zwischen[MAMA]["zeitpunkt"] -= 3500
            asyncio.run(whatsapp.nachrichten(MAMA))
        self.assertEqual(ruf.call_count, 1)

    def test_alter_wird_mitgegeben(self):
        with self._antwort(NACHRICHTEN):
            self.assertEqual(asyncio.run(whatsapp.nachrichten(MAMA))["alter_s"], 0)
            whatsapp._zwischen[MAMA]["zeitpunkt"] -= 600
            self.assertGreaterEqual(asyncio.run(whatsapp.nachrichten(MAMA))["alter_s"], 600)

    def test_werkzeug_nennt_das_alter(self):
        with self._antwort(NACHRICHTEN):
            asyncio.run(werkzeug.whatsapp_lesen("Kontakt P"))
            whatsapp._zwischen[MAMA]["zeitpunkt"] -= 900
            text = asyncio.run(werkzeug.whatsapp_lesen("Kontakt P"))
        self.assertIn("Stand vor 15 Minuten", text)

    def test_frisch_erzwingt_einen_neuen_abruf(self):
        with self._antwort(NACHRICHTEN) as ruf:
            asyncio.run(werkzeug.whatsapp_lesen("Kontakt P"))
            asyncio.run(werkzeug.whatsapp_lesen("Kontakt P", frisch=True))
        self.assertEqual(ruf.call_count, 2)

    def test_mehr_nachrichten_gewuenscht_heisst_neu_holen(self):
        with self._antwort(NACHRICHTEN) as ruf:
            asyncio.run(whatsapp.nachrichten(MAMA, 5))
            asyncio.run(whatsapp.nachrichten(MAMA, 30))       # der Stand reicht nicht
        self.assertEqual(ruf.call_count, 2)

    def test_haekchen_weg_verwirft_das_gelesene_sofort(self):
        """Sonst käme ein entzogener Chat noch eine Stunde lang aus dem Speicher."""
        with self._antwort(NACHRICHTEN):
            asyncio.run(whatsapp.nachrichten(MAMA))
        self.assertIn(MAMA, whatsapp._zwischen)
        whatsapp.frei_setzen([])                              # Häkchen weg
        self.assertEqual(whatsapp._zwischen, {})
        with self._antwort(NACHRICHTEN) as ruf:
            with self.assertRaises(whatsapp.WhatsAppFehler):
                asyncio.run(whatsapp.nachrichten(MAMA))
        ruf.assert_not_called()

    def test_nichts_davon_landet_auf_der_platte(self):
        with self._antwort(NACHRICHTEN):
            asyncio.run(whatsapp.nachrichten(MAMA))
        for pfad in Path(self.ordner).rglob("*"):
            if pfad.is_file():
                self.assertNotIn(b"Bis sp", pfad.read_bytes(), pfad)


class Koppeln(Mit_WhatsApp):
    """WAHA dreht den QR alle 20 s weiter; danach steht die Sitzung auf FAILED.
    `POST /start` allein holt sie NICHT zurück (gemessen 20.09.2026) — erst
    stoppen, dann starten."""

    def _folge(self, *antworten):
        return mock.patch("httpx.AsyncClient.request", mock.AsyncMock(side_effect=[
            httpx.Response(c, json=j, request=httpx.Request("POST", "http://waha:3000/"))
            for c, j in antworten]))

    def test_gescheiterte_sitzung_wird_erst_gestoppt(self):
        with self._folge((200, {"status": "FAILED"}),      # status()
                         (201, {}),                        # stop
                         (201, {}),                        # sessions (neu)
                         (200, {"status": "SCAN_QR_CODE"})) as ruf:
            stand = asyncio.run(whatsapp.starten())
        pfade = [r.args[1] if len(r.args) > 1 else r.kwargs.get("url", "") for r in ruf.call_args_list]
        self.assertTrue(any("stop" in str(p) for p in pfade), pfade)
        self.assertEqual(stand["zustand"], "SCAN_QR_CODE")

    def test_laufende_sitzung_wird_nicht_gestoppt(self):
        with self._folge((200, {"status": "SCAN_QR_CODE"}),
                         (201, {}),
                         (200, {"status": "SCAN_QR_CODE"})) as ruf:
            asyncio.run(whatsapp.starten())
        pfade = [str(r.args[1]) if len(r.args) > 1 else str(r.kwargs.get("url", "")) for r in ruf.call_args_list]
        self.assertFalse(any("stop" in p for p in pfade), pfade)

    def test_abgelaufener_qr_sagt_dass_neu_gestartet_werden_muss(self):
        with mock.patch("httpx.AsyncClient.get", mock.AsyncMock(return_value=httpx.Response(
                422, json={"message": "no qr"}, request=httpx.Request("GET", "http://waha:3000/")))):
            with self.assertRaises(whatsapp.WhatsAppFehler) as f:
                asyncio.run(whatsapp.qr_bild())
        self.assertIn("neu gestartet", str(f.exception))

    def test_seite_erneuert_den_qr_von_selbst(self):
        """Ohne das ist der Code beim Scannen tot — genau der Fehler vom 20.09.2026."""
        js = self.client.get("/freigaben.js").text
        code = "\n".join(z for z in js.splitlines() if not z.strip().startswith(("//", "*", "/*")))
        self.assertIn("QR_NEU_MS", code)
        self.assertLessEqual(int(code.split("QR_NEU_MS = ")[1].split(";")[0]), 20000)
        self.assertIn("/api/whatsapp/start", code)          # abgelaufene Sitzung neu starten


class Hintergrundlauf(Mit_WhatsApp):
    """Friday sieht stündlich von selbst nach (Eike, 20.09.2026). Das ist mehr
    Bewegung Richtung WhatsApp als ein Abruf auf Frage — deshalb sind Takt,
    Tageszeit und Reihenfolge so gebaut, dass sie nicht nach Skript aussehen."""

    def setUp(self):
        super().setUp()
        whatsapp.vergessen()
        whatsapp._gesehen.clear()
        self._frei(MAMA, FAMILIE)

    def tearDown(self):
        whatsapp.vergessen()
        whatsapp._gesehen.clear()
        super().tearDown()

    def test_holt_alle_freigegebenen_chats_nacheinander(self):
        with self._antwort(NACHRICHTEN) as ruf:
            stand = asyncio.run(whatsapp.durchgang(pause=(0, 0)))
        self.assertEqual(stand["chats"], 2)
        self.assertEqual(ruf.call_count, 2)
        self.assertEqual(stand["fehler"], "")

    def test_zwischen_den_chats_wird_pausiert(self):
        """Ohne Pause wäre es ein Stakkato — genau das fällt auf."""
        self.assertGreaterEqual(whatsapp.PAUSE_CHAT_S[0], 2)
        schlaf = []
        async def merken(s):
            schlaf.append(s)
        with self._antwort(NACHRICHTEN), mock.patch("asyncio.sleep", merken):
            asyncio.run(whatsapp.durchgang())
        self.assertEqual(len(schlaf), 1)                      # zwischen zwei Chats genau einmal
        self.assertGreaterEqual(schlaf[0], whatsapp.PAUSE_CHAT_S[0])

    def test_die_ganze_chatliste_wird_nie_geholt(self):
        with self._antwort(NACHRICHTEN) as ruf:
            asyncio.run(whatsapp.durchgang(pause=(0, 0)))
        for r in ruf.call_args_list:
            pfad = str(r.args[1] if len(r.args) > 1 else r.kwargs.get("url", ""))
            self.assertNotIn("chats/overview", pfad)

    def test_nichts_wird_als_gelesen_markiert(self):
        """sendSeen würde bei Eikes Kontakten blaue Haken auslösen."""
        with self._antwort(NACHRICHTEN) as ruf:
            asyncio.run(whatsapp.durchgang(pause=(0, 0)))
        for r in ruf.call_args_list:
            pfad = str(r.args[1] if len(r.args) > 1 else r.kwargs.get("url", ""))
            self.assertNotIn("Seen", pfad)
            self.assertNotIn("read", pfad.lower())
            self.assertEqual(str(r.args[0] if r.args else r.kwargs.get("method")), "GET")

    def test_nur_freigegebene_chats(self):
        self._frei(MAMA)
        with self._antwort(NACHRICHTEN) as ruf:
            asyncio.run(whatsapp.durchgang(pause=(0, 0)))
        self.assertEqual(ruf.call_count, 1)
        pfad = str(ruf.call_args.args[1])
        self.assertIn(MAMA, pfad)
        self.assertNotIn(FREMD, pfad)

    def test_dienstfehler_bricht_ab_statt_weiterzuklopfen(self):
        # ConnectError wird in _ruf zu einem WhatsAppFehler — das ist der EINE Zweig.
        with mock.patch("httpx.AsyncClient.request",
                        mock.AsyncMock(side_effect=httpx.ConnectError("weg"))) as ruf:
            stand = asyncio.run(whatsapp.durchgang(pause=(0, 0)))
        self.assertEqual(ruf.call_count, 1)                   # nicht auch noch den zweiten Chat
        self.assertTrue(stand["fehler"])

    def test_auch_ein_unerwarteter_fehler_bricht_ab(self):
        """Der zweite Zweig. Die Gegenprobe zeigte am 20.09.2026, dass er ungetestet
        war: ein ConnectError kommt dort nie an, weil _ruf ihn vorher umwandelt."""
        with mock.patch.object(whatsapp, "nachrichten",
                               mock.AsyncMock(side_effect=ValueError("kaputt"))) as ruf:
            stand = asyncio.run(whatsapp.durchgang(pause=(0, 0)))
        self.assertEqual(ruf.call_count, 1)
        self.assertEqual(stand["fehler"], "ValueError")
        self.assertEqual(stand["chats"], 0)

    def test_nachts_wird_nicht_abgerufen(self):
        from datetime import datetime
        for stunde, erwartet in ((4, False), (6, False), (8, True), (22, True), (23, False)):
            with self.subTest(stunde=stunde):
                self.assertEqual(whatsapp.wach(datetime(2026, 9, 20, stunde, 30).astimezone()), erwartet)

    def test_takt_ist_stuendlich_und_gestreut(self):
        self.assertEqual(whatsapp.TAKT_S, 3600)
        self.assertGreaterEqual(whatsapp.STREUUNG_S, 300)     # sonst immer zur selben Minute

    def test_neues_zaehlt_nur_fremde_nachrichten_seit_dem_letzten_lauf(self):
        with self._antwort(NACHRICHTEN):
            asyncio.run(whatsapp.durchgang(pause=(0, 0)))
        whatsapp._gesehen_nachziehen()
        self.assertEqual(whatsapp.neues(), [])                # nichts Neues seit eben

        spaeter = [{"id": "c", "timestamp": 1758390000, "fromMe": False, "body": "Neu!",
                    "hasMedia": False, "_data": {"notifyName": "Kontakt P"}},
                   {"id": "d", "timestamp": 1758390100, "fromMe": True, "body": "von mir",
                    "hasMedia": False}] + NACHRICHTEN
        with self._antwort(spaeter):
            asyncio.run(whatsapp.durchgang(pause=(0, 0)))
        neu = {n["name"]: n["anzahl"] for n in whatsapp.neues()}
        self.assertEqual(neu.get("Kontakt P"), 1)                  # nur die fremde, nicht Eikes eigene

    def test_erster_lauf_meldet_nichts_als_neu(self):
        with self._antwort(NACHRICHTEN):
            asyncio.run(whatsapp.durchgang(pause=(0, 0)))
        self.assertEqual(whatsapp.neues(), [])                # es gibt kein „davor"

    def test_briefing_nennt_zahlen_aber_keinen_inhalt(self):
        from kern.planer import briefing
        from datetime import date
        with self._antwort(NACHRICHTEN):
            asyncio.run(whatsapp.durchgang(pause=(0, 0)))
        whatsapp._gesehen_nachziehen()
        spaeter = [{"id": "c", "timestamp": 1758390000, "fromMe": False,
                    "body": "GEHEIMER INHALT", "hasMedia": False,
                    "_data": {"notifyName": "Kontakt P"}}] + NACHRICHTEN
        with self._antwort(spaeter):
            asyncio.run(whatsapp.durchgang(pause=(0, 0)))
        zeile = asyncio.run(briefing.whatsapp_zeile(date(2026, 9, 21)))
        self.assertIn("Kontakt P", zeile)
        self.assertIn("1", zeile)
        self.assertNotIn("GEHEIMER INHALT", zeile)            # kein Text auf den Sperrbildschirm

    def test_briefing_schweigt_ohne_freigabe(self):
        from kern.planer import briefing
        from datetime import date
        konnektoren.setzen("whatsapp", False)
        self.assertIsNone(asyncio.run(briefing.whatsapp_zeile(date(2026, 9, 21))))


class LesenBleibtLesen(Mit_WhatsApp):
    """Lesen zieht kein Senden nach sich — und ein Lesevorgang hinterlässt keine
    Spur bei der Gegenseite."""

    def test_lesen_allein_erlaubt_kein_senden(self):
        self._frei(MAMA)                                # nur das Lese-Häkchen
        self.assertFalse(whatsapp.darf_senden(MAMA))

    def test_freigabe_speichert_senden_nicht_nebenbei(self):
        whatsapp.frei_setzen([{"id": MAMA, "name": "Kontakt P"}])
        self.assertIs(whatsapp.frei()[0]["senden"], False)

    def test_nur_ausdrueckliches_wahr_zaehlt(self):
        # "ja", 1 oder "true" aus einer fremden Quelle sind KEIN Häkchen.
        for wert in ("ja", 1, "true", "senden", [], {}):
            whatsapp.frei_setzen([{"id": MAMA, "name": "Kontakt P", "senden": wert}])
            self.assertFalse(whatsapp.darf_senden(MAMA), repr(wert))

    def test_es_gibt_keine_sende_route(self):
        # Gesendet wird nur im Gespräch, nach Eikes Ja — nicht per Knopf aus der Seite,
        # und erst recht nicht von irgendwem, der die Adresse kennt.
        pfade = {r.path for r in app.routes if hasattr(r, "path")}
        for p in pfade:
            if p.startswith("/api/whatsapp"):
                self.assertNotIn("send", p)

    def test_keine_lesebestaetigung(self):
        """Nirgends ein Aufruf von `/api/sendSeen`.

        Geprüft wird der PFAD, nicht das Wort: In den Kommentaren steht sehr wohl,
        dass Friday kein sendSeen schickt, und genau das soll dort stehen dürfen.
        Ein echter Aufruf brächte blaue Haken bei Eikes Kontakten, nur weil Friday
        im Hintergrund nachgesehen hat."""
        wurzel = Path(whatsapp.__file__).resolve().parent.parent
        for datei in wurzel.rglob("*.py"):
            self.assertFalse("/api/sendSeen" in datei.read_text(encoding="utf-8"), datei.name)

    def test_chatinhalte_gelten_als_fremder_text(self):
        # Sonst würde Friday aus fremden Worten etwas ins Gedächtnis schreiben.
        self.assertTrue(katalog.liefert_fremdes("whatsapp_lesen"))

    def test_die_liste_der_freigegebenen_chats_ist_nicht_fremd(self):
        """Gespräch 225: Eike diktierte „Hallo“, Gemini rief vorher whatsapp_chats —
        und der Vorschlag behauptete, der Wunsch käme aus fremdem Text."""
        self.assertFalse(katalog.liefert_fremdes("whatsapp_chats"))


class Absender(Mit_WhatsApp):
    """Gespräch 218/221: vor jeder Nachricht von Kontakt F stand „?“, Friday hielt seine
    Worte für Eikes. Die GOWS-Engine trägt den Namen in `_data.Info.PushName`."""

    def _lesen(self, chat, daten):
        whatsapp.frei_setzen([{"id": chat, "name": "Kontakt F Muster"}])
        with self._antwort(daten):
            return asyncio.run(whatsapp.nachrichten(chat, 20))["nachrichten"]

    def test_pushname_aus_info(self):
        n = self._lesen(FAMILIE, [{"id": "x", "timestamp": 1758380000, "fromMe": False, "body": "Hi",
                                   "_data": {"Info": {"PushName": "Abbas"}}}])
        self.assertEqual(n[0]["von"], "Abbas")

    def test_im_einzelchat_ist_es_der_partner(self):
        n = self._lesen(MAMA, [{"id": "x", "timestamp": 1758380000, "fromMe": False, "body": "Hi",
                                "_data": {"Info": {"PushName": ""}}}])
        self.assertEqual(n[0]["von"], "Kontakt F Muster")

    def test_sortiert_nach_zeit_nicht_nach_text(self):
        """„30.08.“ steht als Text hinter „01.09.“ — die neueste Nachricht wäre mittendrin."""
        aug, sep = 1756548000, 1756720800           # 30.08.2025, 01.09.2025
        n = self._lesen(MAMA, [{"id": "s", "timestamp": sep, "fromMe": True, "body": "neu"},
                               {"id": "a", "timestamp": aug, "fromMe": True, "body": "alt"}])
        self.assertEqual([x["text"] for x in n], ["alt", "neu"])

    def test_sendewerkzeuge_bringen_nichts_fremdes_herein(self):
        for name in ("whatsapp_antwort", "whatsapp_senden"):
            self.assertFalse(katalog.liefert_fremdes(name), name)


class SendenNurMitAllemDrum(Mit_WhatsApp):
    """Drei Riegel und ein Ja. Jeder Test nimmt genau einen weg."""

    def _senden(self, antwort=None):
        """Patcht den Netzaufruf und sagt, ob und womit gerufen wurde."""
        return mock.patch("httpx.AsyncClient.request", mock.AsyncMock(return_value=httpx.Response(
            201, json=antwort or {"id": "true_123"},
            request=httpx.Request("POST", "http://waha:3000/api/sendText"))))

    def test_der_ganze_weg_funktioniert(self):
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            frage = asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Bin gleich da"))
            self.assertIn("Bin gleich da", frage)
            self.assertEqual(ruf.call_count, 0)          # der Vorschlag sendet nichts
            antwort = asyncio.run(werkzeug.whatsapp_senden(zustimmung=True))
        self.assertIn("Gesendet an Kontakt P", antwort)
        self.assertEqual(ruf.call_count, 1)
        self.assertEqual(ruf.call_args.args[0], "POST")
        self.assertTrue(ruf.call_args.args[1].endswith("/api/sendText"))
        self.assertEqual(ruf.call_args.kwargs["json"],
                         {"session": "default", "chatId": MAMA, "text": "Bin gleich da 🤖"})

    def test_wirkungen_status_art_quittung(self):
        """P0 12.10.2026: Vorschlag = Rückfrage, Senden = ok mit Quittung, Absage/Fehlschlag = nicht."""
        from test.test_wirkung import pruefe_wirkung
        self._senden_frei(MAMA)
        aus = lambda n, a=None, **k: asyncio.run(katalog.ausfuehren(n, a or {}, **k))      # noqa: E731
        pruefe_wirkung(self, aus("whatsapp_senden"), "nicht", "versand", "kein Vorschlag")
        with self._senden():
            pruefe_wirkung(self, aus("whatsapp_antwort", {"chat": "Kontakt P", "text": "Bin gleich da"}), "rueckfrage", "versand", "Vorschlag")
            pruefe_wirkung(self, aus("whatsapp_senden"), "nicht", "versand", "ohne Ja")
            r = aus("whatsapp_senden", zustimmung=True)
        pruefe_wirkung(self, r, "ok", "versand", "gesendet")
        self.assertEqual(r["quittung"], "Auf dem Weg: Nachricht an Kontakt P.")
        with mock.patch("httpx.AsyncClient.request", mock.AsyncMock(side_effect=RuntimeError("aus"))):
            aus("whatsapp_antwort", {"chat": "Kontakt P", "text": "Bin gleich da"})
            pruefe_wirkung(self, aus("whatsapp_senden", zustimmung=True), "nicht", "versand", "Fehlschlag")

    def test_ohne_zustimmung_geht_nichts_raus(self):
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Bin gleich da"))
            antwort = asyncio.run(werkzeug.whatsapp_senden())
        self.assertEqual(ruf.call_count, 0)
        self.assertIn("zustimmst", antwort)

    def test_ohne_zweites_haekchen_kein_vorschlag(self):
        self._senden_frei(FAMILIE, MAMA)                 # Senden nur für Familie
        with self._senden() as ruf:
            antwort = asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Hallo"))
            asyncio.run(werkzeug.whatsapp_senden(zustimmung=True))
        self.assertEqual(ruf.call_count, 0)
        self.assertIn("zweites Häkchen", antwort)

    def test_ohne_schreibrecht_kein_vorschlag(self):
        self._senden_frei(MAMA, schreibrecht=False)
        with self._senden() as ruf:
            antwort = asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Hallo"))
            asyncio.run(werkzeug.whatsapp_senden(zustimmung=True))
        self.assertEqual(ruf.call_count, 0)
        self.assertIn("Schreibrecht", antwort)

    def test_ohne_schreibrecht_sieht_gemini_die_werkzeuge_nicht(self):
        self._senden_frei(MAMA, schreibrecht=False)
        namen = {f["name"] for f in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("whatsapp_antwort", namen)
        self.assertIn("whatsapp_lesen", namen)
        konnektoren.schreiben_setzen("whatsapp", True)
        namen = {f["name"] for f in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertIn("whatsapp_antwort", namen)

    def test_der_dienst_sperrt_auch_ohne_werkzeug(self):
        # Der Riegel sitzt im Dienst: Auch ein Aufruf am Werkzeug vorbei kommt nicht durch.
        self._frei(MAMA)                                 # nur lesen
        konnektoren.schreiben_setzen("whatsapp", True)
        with self._senden() as ruf:
            with self.assertRaises(whatsapp.WhatsAppFehler):
                asyncio.run(whatsapp.senden(MAMA, "Hallo"))
        self.assertEqual(ruf.call_count, 0)

    def test_ein_nicht_freigegebener_chat_bleibt_zu(self):
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            with self.assertRaises(whatsapp.WhatsAppFehler):
                asyncio.run(whatsapp.senden(FREMD, "Hallo"))
        self.assertEqual(ruf.call_count, 0)

    def test_das_modell_kann_sich_die_zustimmung_nicht_selbst_geben(self):
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Hallo"))
            # So sähe der Versuch aus: zustimmung als erfundene Angabe mitschicken.
            asyncio.run(katalog.ausfuehren("whatsapp_senden", {"zustimmung": True}))
        self.assertEqual(ruf.call_count, 0)

    def test_leerer_text_wird_hier_abgefangen(self):
        # Gemessen 20.09.2026: WAHA schickt auch ohne Text los, statt 400 zu antworten.
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            antwort = asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "   "))
            asyncio.run(werkzeug.whatsapp_senden(zustimmung=True))
        self.assertEqual(ruf.call_count, 0)
        self.assertIn("Ohne Text", antwort)

    def test_zu_langer_text_wird_abgelehnt(self):
        self._senden_frei(MAMA)
        antwort = asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "a" * (whatsapp.MAX_TEXT + 1)))
        self.assertIn("zu lang", antwort)

    def test_hoechstens_acht_in_der_stunde(self):
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            for _ in range(whatsapp.SENDE_GRENZE):
                asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Hallo"))
                asyncio.run(werkzeug.whatsapp_senden(zustimmung=True))
            self.assertEqual(ruf.call_count, whatsapp.SENDE_GRENZE)
            antwort = asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Und noch eine"))
            self.assertEqual(ruf.call_count, whatsapp.SENDE_GRENZE)
        self.assertIn("dieser Stunde", antwort)

    def test_der_dienst_prueft_das_schreibrecht_selbst(self):
        """Nicht nur das Werkzeug: Wer den Dienst direkt ruft, kommt auch nicht durch.

        Aufgefallen in der Gegenprobe — der Riegel im Dienst war ungetestet, weil
        jeder Test vorher schon am Werkzeug hängen blieb."""
        self._senden_frei(MAMA, schreibrecht=False)
        with self._senden() as ruf:
            with self.assertRaises(whatsapp.WhatsAppFehler) as fehler:
                asyncio.run(whatsapp.senden(MAMA, "Hallo"))
        self.assertEqual(ruf.call_count, 0)
        self.assertIn("Schreibrecht", str(fehler.exception))

    def test_der_dienst_haelt_auch_die_stundengrenze_selbst(self):
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            for _ in range(whatsapp.SENDE_GRENZE):
                asyncio.run(whatsapp.senden(MAMA, "Hallo"))
            with self.assertRaises(whatsapp.WhatsAppFehler):
                asyncio.run(whatsapp.senden(MAMA, "Und noch eine"))
        self.assertEqual(ruf.call_count, whatsapp.SENDE_GRENZE)

    def test_ein_gescheiterter_zweiter_versuch_loescht_den_ersten(self):
        """„Schreib an Kontakt P X" — „nee, lieber an Papa Y", und Papa ist gesperrt.
        Ohne das Löschen läge X noch bereit, und ein „ja" schickte die ALTE
        Nachricht an die alte Adresse."""
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Erste Fassung"))
            asyncio.run(werkzeug.whatsapp_antwort("Jemand anders", "Zweite Fassung"))
            antwort = asyncio.run(werkzeug.whatsapp_senden(zustimmung=True))
        self.assertEqual(ruf.call_count, 0)
        self.assertIn("keine vorbereitete", antwort)

    def test_ein_alter_vorschlag_verfaellt(self):
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Hallo"))
            werkzeug._vorschlag["zeit"] -= werkzeug.VORSCHLAG_GILT_S + 1
            antwort = asyncio.run(werkzeug.whatsapp_senden(zustimmung=True))
        self.assertEqual(ruf.call_count, 0)
        self.assertIn("zu alt", antwort)

    def test_ein_neuer_vorschlag_ersetzt_den_alten(self):
        # Sonst ginge bei „nein, schreib lieber ..." die ERSTE Fassung raus.
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Erste Fassung"))
            asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Zweite Fassung"))
            asyncio.run(werkzeug.whatsapp_senden(zustimmung=True))
        self.assertEqual(ruf.call_args.kwargs["json"]["text"], "Zweite Fassung 🤖")

    def test_ja_und_derselbe_vorschlag_nochmal_sendet(self):
        """Gespräch 230: sechsmal „Ja“, weil Gemini jedes Mal whatsapp_antwort statt
        whatsapp_senden rief. Derselbe Wortlaut, derselbe Chat, Eikes Ja: raus damit."""
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Bin gleich da"))
            antwort = asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Bin gleich da", zustimmung=True))
        self.assertEqual(ruf.call_count, 1)
        self.assertIn("Gesendet", antwort)

    def test_ja_aber_anderer_wortlaut_fragt_neu(self):
        # Den neuen Text hat Eike noch nicht gehört — sein Ja galt dem alten.
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Bin gleich da"))
            antwort = asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Komme später", zustimmung=True))
        self.assertEqual(ruf.call_count, 0)
        self.assertIn("Noch nichts gesendet", antwort)

    def test_derselbe_vorschlag_ohne_ja_sendet_nicht(self):
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Bin gleich da"))
            asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Bin gleich da"))
        self.assertEqual(ruf.call_count, 0)

    def test_ja_beim_ersten_vorschlag_sendet_nicht(self):
        # Ein Ja VOR dem Vorlesen ist kein Ja zum Wortlaut.
        self._senden_frei(MAMA)
        with self._senden() as ruf:
            antwort = asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Bin gleich da", zustimmung=True))
        self.assertEqual(ruf.call_count, 0)
        self.assertIn("Noch nichts gesendet", antwort)

    def test_aus_fremdem_text_wird_es_dazugesagt(self):
        self._senden_frei(MAMA)
        antwort = asyncio.run(werkzeug.whatsapp_antwort("Kontakt P", "Hallo", fremde_inhalte=True))
        self.assertIn("fremdem Text", antwort)

    def test_nach_dem_senden_wird_der_chat_neu_gelesen(self):
        # Sonst fehlte Fridays eigene Nachricht bis zu eine Stunde lang im Stand.
        self._senden_frei(MAMA)
        with self._antwort(NACHRICHTEN):
            asyncio.run(whatsapp.nachrichten(MAMA, 20))
        with self._senden():
            asyncio.run(whatsapp.senden(MAMA, "Hallo"))
        with self._antwort(NACHRICHTEN) as ruf:
            asyncio.run(whatsapp.nachrichten(MAMA, 20))
        self.assertEqual(ruf.call_count, 1)


class Werkzeuge(Mit_WhatsApp):
    def test_ohne_freigabe_sagt_es_das_und_nennt_keine_fremden_namen(self):
        text = asyncio.run(werkzeug.whatsapp_lesen("Kontakt P"))
        self.assertIn("kein Chat freigegeben", text)
        self.assertNotIn("Jemand anders", text)

    def test_nicht_freigegebener_chat_nennt_nur_freigegebene(self):
        """Die Fehlanzeige darf nur FREIGEGEBENE Namen aufzählen. Eikes eigenes
        Suchwort zurückzugeben ist in Ordnung — das kennt er ja."""
        self._frei(MAMA)
        with self._antwort(UEBERSICHT) as ruf:
            text = asyncio.run(werkzeug.whatsapp_lesen("Chef"))
        self.assertIn("finde ich nicht", text)
        self.assertIn("Freigegeben sind: Kontakt P", text)
        self.assertNotIn("Jemand anders", text)          # fremder Chatname bleibt draußen
        self.assertNotIn("Familie", text)                # nicht freigegeben, also kein Wort davon
        ruf.assert_not_called()                          # und WAHA wird gar nicht erst gefragt

    def test_liest_einen_freigegebenen_chat(self):
        self._frei(MAMA)
        with self._antwort(NACHRICHTEN):
            text = asyncio.run(werkzeug.whatsapp_lesen("Kontakt P"))
        self.assertIn("Chat „Kontakt P“", text)
        self.assertIn("Bis später!", text)
        self.assertIn("Eike: Alles klar", text)

    def test_chatliste_nennt_nur_freigegebene(self):
        self._frei(MAMA, FAMILIE)
        text = asyncio.run(werkzeug.whatsapp_chats())
        self.assertIn("Kontakt P", text)
        self.assertIn("Familie", text)
        self.assertNotIn("Jemand anders", text)

    def test_dienstfehler_wird_vorlesbar_weitergegeben(self):
        self._frei(MAMA)
        with mock.patch("httpx.AsyncClient.request",
                        mock.AsyncMock(side_effect=httpx.ConnectError("weg"))):
            text = asyncio.run(werkzeug.whatsapp_lesen("Kontakt P"))
        self.assertIn("antwortet gerade nicht", text)

    def test_teilname_findet_den_chat(self):
        self._frei(FAMILIE)
        with self._antwort([]):
            text = asyncio.run(werkzeug.whatsapp_lesen("famil"))
        self.assertIn("Familie", text)


class Freigabe(Mit_WhatsApp):
    def test_werkzeuge_erst_mit_dem_konnektor(self):
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertIn("whatsapp_lesen", namen)
        konnektoren.setzen("whatsapp", False)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("whatsapp_lesen", namen)
        r = asyncio.run(katalog.ausfuehren("whatsapp_lesen", {"chat": "Kontakt P"}))
        self.assertIn("nicht freigegeben", r["fehler"])

    def test_routen_sind_ohne_konnektor_zu(self):
        konnektoren.setzen("whatsapp", False)
        for pfad in ("/api/whatsapp/status", "/api/whatsapp/chats", "/api/whatsapp/qr"):
            self.assertEqual(self.client.get(pfad).status_code, 403, pfad)
        self.assertEqual(self.client.post("/api/whatsapp/start", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/whatsapp/chats", json={"chats": []}).status_code, 403)

    def test_hat_einen_hilfe_link(self):
        self.assertTrue(konnektoren.hilfe_url("whatsapp"))

    def test_hinweis_nennt_das_sperrrisiko(self):
        # Eike soll wissen, worauf er sich einlässt — das steht auf der Freigaben-Seite.
        hinweis = konnektoren.KONNEKTOREN["whatsapp"]["hinweis"]
        self.assertIn("Nutzungsbedingungen", hinweis)
        self.assertIn("Sperre", hinweis)


class Sitzung(Mit_WhatsApp):
    def test_status_ohne_sitzung_ist_kein_fehler(self):
        with self._antwort({"error": "not found"}, code=404):
            st = asyncio.run(whatsapp.status())
        self.assertEqual(st["zustand"], "STOPPED")

    def test_status_verbunden(self):
        with self._antwort({"status": "WORKING", "me": {"pushName": "Eike"}}):
            st = asyncio.run(whatsapp.status())
        self.assertEqual((st["zustand"], st["text"], st["nummer"]), ("WORKING", "verbunden", "Eike"))

    def test_schluessel_geht_als_kopfzeile_mit(self):
        with self._antwort({"status": "WORKING"}) as ruf:
            asyncio.run(whatsapp.status())
        self.assertEqual(ruf.call_args.kwargs["headers"]["X-Api-Key"], "geheim")

    def test_alle_chats_vermerken_die_freigabe(self):
        self._frei(MAMA)
        with self._antwort(UEBERSICHT):
            liste = asyncio.run(whatsapp.alle_chats())
        nach_id = {c["id"]: c for c in liste}
        self.assertTrue(nach_id[MAMA]["frei"])
        self.assertFalse(nach_id[FREMD]["frei"])
        self.assertTrue(nach_id[FAMILIE]["gruppe"])

    def test_qr_nur_als_bild(self):
        with mock.patch("httpx.AsyncClient.get", mock.AsyncMock(return_value=httpx.Response(
                200, content=b"\\x89PNG-Daten", request=httpx.Request("GET", "http://waha:3000/")))):
            self.assertEqual(asyncio.run(whatsapp.qr_bild()), b"\\x89PNG-Daten")



class ZeichenTest(Mit_WhatsApp):
    """Fridays Kennzeichen 🤖 am Ende jeder WhatsApp-Nachricht (15.10.2026)."""

    def test_mit_zeichen_haengt_hinten_an_und_nur_einmal(self):
        self.assertEqual(whatsapp.mit_zeichen("Hallo"), "Hallo 🤖")
        self.assertEqual(whatsapp.mit_zeichen("Hallo 🤖"), "Hallo 🤖")
        self.assertEqual(whatsapp.mit_zeichen("  Hallo  "), "Hallo 🤖")

    def test_von_friday_erkennt_das_zeichen_nur_am_ende(self):
        self.assertTrue(whatsapp.von_friday({"text": "ok 🤖", "von_mir": True}))
        self.assertFalse(whatsapp.von_friday({"text": "ok", "von_mir": True}))
        self.assertFalse(whatsapp.von_friday({"text": "🤖 ok", "von_mir": False}))

    def test_senden_schickt_den_text_mit_zeichen(self):
        self._senden_frei(MAMA)
        with mock.patch("httpx.AsyncClient.request", mock.AsyncMock(return_value=httpx.Response(
                201, json={"id": "x"}, request=httpx.Request("POST", "http://waha:3000/api/sendText")))) as ruf:
            erg = asyncio.run(whatsapp.senden(MAMA, "Bin gleich da"))
        self.assertEqual(ruf.call_args.kwargs["json"]["text"], "Bin gleich da 🤖")
        self.assertEqual(erg["text"], "Bin gleich da 🤖")

    def test_das_zeichen_zaehlt_in_die_laenge(self):
        self._senden_frei(MAMA)
        with mock.patch("httpx.AsyncClient.request", mock.AsyncMock(return_value=httpx.Response(
                201, json={"id": "x"}, request=httpx.Request("POST", "http://waha:3000/api/sendText")))) as ruf:
            with self.assertRaises(whatsapp.WhatsAppFehler):
                asyncio.run(whatsapp.senden(MAMA, "x" * whatsapp.MAX_TEXT))
            asyncio.run(whatsapp.senden(MAMA, "x" * (whatsapp.MAX_TEXT - 3)))
        self.assertEqual(ruf.call_count, 1)

if __name__ == "__main__":
    unittest.main()
