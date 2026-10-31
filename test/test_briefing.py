"""Wetter und Morgenbriefing — ohne Netz.

Die Open-Meteo-Antworten haben den Aufbau der echten vom 14.09.2026; die fünf
„Holtensen“ sind die echten Treffer der Ortssuche.

Seit Eikes Entscheidung vom 14.09.2026: Wetter ist ein Konnektor, das
Morgenbriefing eine Einstellung, die Friday auch selbst schalten kann.
"""

import asyncio
import os
import stat
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient

from kern import erinnerungen, konnektoren
from kern.dienste import icloud, push, schulmanager, spotify, wetter
from kern.gedaechtnis import graph
from kern.main import app
from kern.planer import briefing
from kern.werkzeuge import katalog
from kern.werkzeuge import morgenbriefing as briefingwerkzeug
from kern.werkzeuge import wetter as wetterwerkzeug

BERLIN = ZoneInfo("Europe/Berlin")
MONTAG = datetime(2026, 9, 14, 6, 40, tzinfo=BERLIN)


def _ort(name, gemeinde, kreis="Region Hannover", breite=52.2, laenge=9.6):
    return {"name": name, "latitude": breite, "longitude": laenge, "admin1": "Niedersachsen",
            "admin3": kreis, "admin4": gemeinde}


HOLTENSEN = [_ort("Holtensen bei Wunstorf", "Barsinghausen"), _ort("Holtensen bei Weetzen", "Wennigsen (Deister)"),
             _ort("Holtensen", "Musterhausen"), _ort("Holtensen", "Hameln", "Landkreis Hameln-Pyrmont"),
             _ort("Holtensen", "Einbeck", "Landkreis Northeim")]
LUEDERSEN = [_ort("Musterdorf", "Musterhausen")]

_stunden = [f"2026-09-{t}T{h:02d}:00" for t in (14, 15) for h in range(24)]
_regen = [0] * 24 + [0] * 24
_regen[24 + 3] = 90          # 3 Uhr nachts zählt nicht
for h in range(15, 24):
    _regen[24 + h] = 70
VORHERSAGE = {
    "daily": {"time": ["2026-09-14", "2026-09-15"], "weather_code": [3, 61],
              "temperature_2m_max": [19.6, 17.2], "temperature_2m_min": [10.7, 12.1],
              "precipitation_probability_max": [3, 80], "precipitation_sum": [0.0, 4.2]},
    "hourly": {"time": _stunden, "precipitation_probability": _regen},
}


def _netz(orte=HOLTENSEN, vorhersage=VORHERSAGE):
    async def get(self, url, params=None):
        daten = ({"results": orte} if orte else {"generationtime_ms": 0.4}) if url == wetter.GEO_URL else vorhersage
        return httpx.Response(200, json=daten, request=httpx.Request("GET", url))
    return mock.patch("httpx.AsyncClient.get", get)


class Mit_Uhr(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "FRIDAY_ORT": "",
                                                "BRIEFING_UHRZEIT": ""})
        self.env.start()
        self.uhr = mock.patch("kern.config.jetzt", return_value=MONTAG)
        self.uhr.start()
        wetter._orte.clear()

    def tearDown(self):
        self.uhr.stop()
        self.env.stop()


# ---------- Wetter ----------

class Ortswahl(unittest.TestCase):
    def test_ohne_zusatz_gewinnt_der_exakte_name(self):
        self.assertEqual(wetter.auswaehlen("Holtensen", HOLTENSEN)["admin4"], "Musterhausen")

    def test_gemeinde_grenzt_ein(self):
        self.assertEqual(wetter.auswaehlen("Holtensen, Hameln", HOLTENSEN)["admin4"], "Hameln")

    def test_gemeinde_auch_wenn_der_name_nicht_exakt_ist(self):
        # Heißt bei Open-Meteo „Holtensen bei Weetzen“ — darf nicht durchfallen.
        self.assertEqual(wetter.auswaehlen("Holtensen, Wennigsen", HOLTENSEN)["admin4"], "Wennigsen (Deister)")

    def test_falsche_gemeinde_ergibt_nichts_statt_irgendwas(self):
        self.assertIsNone(wetter.auswaehlen("Holtensen, Lübeck", HOLTENSEN))
        self.assertIsNone(wetter.auswaehlen("Irgendwo", []))

    def test_bezeichnung_nennt_die_gemeinde(self):
        self.assertEqual(wetter._bezeichnung(LUEDERSEN[0]), "Musterdorf, Musterhausen")


class Vorhersage(Mit_Uhr):
    def test_tageswerte_und_regenbeginn(self):
        with _netz(LUEDERSEN):
            v = asyncio.run(wetter.vorhersage("Musterdorf", 2))
        self.assertEqual(v["ort"], "Musterdorf, Musterhausen")
        heute, morgen = v["tage"]
        self.assertEqual((heute["wetter"], heute["regen_ab"]), ("bedeckt", ""))
        self.assertEqual((morgen["wetter"], morgen["regen_ab"]), ("leichter Regen", "15 Uhr"))

    def test_kurz(self):
        with _netz(LUEDERSEN):
            v = asyncio.run(wetter.vorhersage("Musterdorf", 2))
        self.assertEqual(wetter.kurz(v["tage"][0]), "bedeckt, 11 bis 20 °C")
        self.assertEqual(wetter.kurz(v["tage"][1]),
                         "leichter Regen, 12 bis 17 °C, Regen wahrscheinlich ab 15 Uhr (4 mm)")

    def test_unbekannter_ort(self):
        with _netz(orte=[]), self.assertRaises(wetter.WetterFehler) as f:
            asyncio.run(wetter.vorhersage("Nirgendwohausen"))
        self.assertIn("Nirgendwohausen", str(f.exception))

    def test_netzfehler_wird_klartext(self):
        with mock.patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("weg")), \
                self.assertRaises(wetter.WetterFehler):
            asyncio.run(wetter.vorhersage("Musterdorf"))


class WetterWerkzeug(Mit_Uhr):
    def test_morgen(self):
        with _netz(LUEDERSEN):
            text = asyncio.run(wetterwerkzeug.wetter("Musterdorf", "morgen"))
        self.assertIn("Musterdorf, Musterhausen", text)
        self.assertIn("Dienstag, 15.09.: leichter Regen", text)
        self.assertNotIn("Montag", text)

    def test_ohne_ort_gilt_der_wohnort(self):
        with mock.patch.dict(os.environ, {"FRIDAY_ORT": "Musterdorf, Musterhausen"}), _netz(LUEDERSEN):
            self.assertIn("Musterdorf, Musterhausen", asyncio.run(wetterwerkzeug.wetter()))

    def test_grenzen(self):
        self.assertIn("vergangene", asyncio.run(wetterwerkzeug.wetter("Musterdorf", "gestern")))
        self.assertIn("sieben Tage", asyncio.run(wetterwerkzeug.wetter("Musterdorf", "2026-09-30")))

    def test_nur_mit_freigabe(self):
        namen = lambda: {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("wetter", namen())                               # Default-Deny
        self.assertIn("nicht freigegeben", asyncio.run(katalog.ausfuehren("wetter", {}))["fehler"])
        konnektoren.setzen("wetter", True)
        self.assertIn("wetter", namen())                                  # ohne Zugangsdaten nutzbar


# ---------- Briefing: die Zeilen ----------

def _stunde(nr, typ="regularLesson", fach="Mathe", von="", bis="", tag="2026-09-14"):
    stunde = {"date": tag, "type": typ, "classHour": {"id": 1000 + int(nr), "number": str(nr)},
              "actualLesson": {"subject": {"name": fach}}}
    if von:
        stunde["classHour"].update({"from": von, "until": bis})
    return stunde


class Zeilen(Mit_Uhr):
    def setUp(self):
        super().setUp()
        self.frei = {"schulmanager", "icloud_kalender", "wetter"}
        self.freigabe = mock.patch.object(konnektoren, "benutzbar", side_effect=lambda k: k in self.frei)
        self.freigabe.start()

    def tearDown(self):
        self.freigabe.stop()
        super().tearDown()

    def _schule(self, stunden, tag=date(2026, 9, 14)):
        with mock.patch.object(schulmanager, "stundenplan", mock.AsyncMock(return_value=stunden)):
            return asyncio.run(briefing.schule_zeile(tag))

    def test_schultag_mit_ausfall_und_vertretung(self):
        zeile = self._schule([_stunde(3, "cancelledLesson", "Chemie", "09:55:00", "10:40:00"),
                              _stunde(1, von="08:10:00", bis="08:55:00"),
                              _stunde(2, "substitution", "Bio", "08:55:00", "09:40:00")])
        self.assertEqual(zeile, "Schule: 1.–2. Stunde, 08:10–09:40; fällt aus: Chemie; 1 Vertretung")

    def test_zweistellige_stunden_werden_als_zahl_sortiert(self):
        self.assertEqual(self._schule([_stunde(10), _stunde(9)]), "Schule: 9.–10. Stunde")

    def test_ohne_zeiten_wird_keine_erfunden(self):
        self.assertEqual(self._schule([_stunde(4)]), "Schule: 4. Stunde")

    def test_leere_woche_ist_keine_aussage_ueber_den_tag(self):
        self.assertIn("das heißt nicht, dass frei ist", self._schule([]))

    def test_freier_tag_in_voller_woche(self):
        self.assertEqual(self._schule([_stunde(1, tag="2026-09-15")]), "Schule: kein Unterricht eingetragen.")

    def test_wochenende_ohne_unterricht_schweigt(self):
        self.assertIsNone(self._schule([_stunde(1)], tag=date(2026, 9, 19)))

    def test_nur_ausfall_eingetragen_heisst_nicht_frei(self):
        # Echter Fall 15.09.2026: nur die 3. und 4. Stunde im Plan, beide Ausfall.
        # „Alle Stunden fallen aus" hätte einen freien Tag behauptet.
        zeile = self._schule([_stunde(4, "cancelledLesson"), _stunde(3, "cancelledLesson")])
        self.assertEqual(zeile, "Schule: eingetragen ist nur die 3.–4. Stunde (Mathe) — fällt aus.")

    def test_ohne_freigabe_keine_schulzeile(self):
        self.frei.clear()
        with mock.patch.object(schulmanager, "stundenplan", mock.AsyncMock()) as plan:
            self.assertIsNone(asyncio.run(briefing.schule_zeile(date(2026, 9, 14))))
        plan.assert_not_called()

    def test_ohne_wetterfreigabe_keine_wetterzeile_und_kein_abruf(self):
        self.frei.discard("wetter")
        with mock.patch.object(wetter, "vorhersage", mock.AsyncMock()) as abruf:
            self.assertIsNone(asyncio.run(briefing.wetter_zeile(date(2026, 9, 14))))
        abruf.assert_not_called()

    def test_termine(self):
        termine = [{"titel": "Müll", "ganztags": True, "beginn": "2026-09-14"},
                   {"titel": "Therapie", "ganztags": False, "beginn": "2026-09-14T14:00:00+00:00"}]
        with mock.patch.object(icloud, "termine", return_value=termine):
            self.assertEqual(asyncio.run(briefing.kalender_zeile(date(2026, 9, 14))), "Termine: Müll, 16:00 Therapie")
        viele = [dict(termine[0], titel=f"T{i}") for i in range(5)]
        with mock.patch.object(icloud, "termine", return_value=viele):
            self.assertIn("(+2 weitere)", asyncio.run(briefing.kalender_zeile(date(2026, 9, 14))))

    def test_nur_die_erinnerungen_von_heute(self):
        a = erinnerungen.standard()
        a.anlegen("Wasserpest einpacken", "", datetime(2026, 9, 14, 19, 0, tzinfo=BERLIN))
        a.anlegen("Morgen", "", datetime(2026, 9, 15, 7, 0, tzinfo=BERLIN))
        self.assertEqual(asyncio.run(briefing.erinnerungen_zeile(date(2026, 9, 14))),
                         "Erinnerungen: 19:00 Wasserpest einpacken")

    def test_eine_quelle_faellt_aus_der_rest_kommt(self):
        with mock.patch.object(wetter, "vorhersage", mock.AsyncMock(side_effect=RuntimeError("weg"))), \
                mock.patch.object(schulmanager, "stundenplan", mock.AsyncMock(return_value=[_stunde(1)])), \
                mock.patch.object(icloud, "termine", return_value=[]):
            titel, text = asyncio.run(briefing.briefing(date(2026, 9, 14)))
        self.assertEqual(titel, "Guten Morgen — Montag, 14.09.")
        self.assertEqual(text.splitlines(), ["Wetter: gerade nicht abrufbar.", "Schule: 1. Stunde", "Termine: keine."])

    def test_wetterfehler_im_klartext(self):
        self.frei = {"wetter"}
        fehler = wetter.WetterFehler("Den Ort „X“ finde ich nicht.")
        with mock.patch.object(wetter, "vorhersage", mock.AsyncMock(side_effect=fehler)):
            _, text = asyncio.run(briefing.briefing(date(2026, 9, 14)))
        self.assertEqual(text, "Wetter: Den Ort „X“ finde ich nicht.")

    def test_nichts_freigegeben_ergibt_ehrlichen_satz(self):
        self.frei = set()
        _, text = asyncio.run(briefing.briefing(date(2026, 9, 14)))
        self.assertIn("nichts davon ist freigegeben", text)


# ---------- Briefing: Einstellung ----------

class Einstellung(Mit_Uhr):
    def test_standardmaessig_aus(self):
        self.assertFalse(briefing.ist_an())
        self.assertEqual(briefing.uhrzeit(), briefing.STANDARD_UHRZEIT)

    def test_schalten_und_uhrzeit_in_600er_datei(self):
        briefing.einstellen(an=True, uhrzeit_text="6:45")
        self.assertTrue(briefing.ist_an())
        self.assertEqual(briefing.uhrzeit().strftime("%H:%M"), "06:45")
        self.assertEqual(stat.S_IMODE(briefing._einstellungsdatei().stat().st_mode), 0o600)
        briefing.einstellen(an=False)                                      # Uhrzeit bleibt
        self.assertFalse(briefing.ist_an())
        self.assertEqual(briefing.uhrzeit().strftime("%H:%M"), "06:45")

    def test_unlesbare_uhrzeit_aendert_nichts(self):
        briefing.einstellen(an=True)
        for falsch in ("7 Uhr", "25:00", "06:61", ""):
            with self.subTest(falsch), self.assertRaises(ValueError):
                briefing.einstellen(an=False, uhrzeit_text=falsch)
        self.assertTrue(briefing.ist_an())                                 # auch „an“ nicht angefasst

    def test_einstellung_geht_vor_umgebung(self):
        with mock.patch.dict(os.environ, {"BRIEFING_UHRZEIT": "07:15"}):
            self.assertEqual(briefing.uhrzeit().strftime("%H:%M"), "07:15")
            briefing.einstellen(uhrzeit_text="05:50")
            self.assertEqual(briefing.uhrzeit().strftime("%H:%M"), "05:50")


class Werkzeug(Mit_Uhr):
    def setUp(self):
        super().setUp()
        self.geraete = mock.patch.object(push, "geraete", return_value=[{"geraet": "iPhone"}])
        self.geraete.start()

    def tearDown(self):
        self.geraete.stop()
        super().tearDown()

    def test_immer_da(self):
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertIn("morgenbriefing", namen)

    def test_ohne_angaben_nur_der_stand(self):
        self.assertEqual(briefingwerkzeug.morgenbriefing(), "Das Morgenbriefing ist aus, Uhrzeit 06:30.")
        self.assertFalse(briefing.ist_an())

    def test_einschalten_mit_uhrzeit(self):
        antwort = briefingwerkzeug.morgenbriefing(an=True, uhrzeit="6:45")
        self.assertIn("ist an, Uhrzeit 06:45", antwort)
        self.assertIn("Wetter fehlt", antwort)                            # Wetter-Konnektor ist aus
        konnektoren.setzen("wetter", True)
        self.assertNotIn("Wetter fehlt", briefingwerkzeug.morgenbriefing())

    def test_an_als_text(self):
        self.assertIn("ist an", briefingwerkzeug.morgenbriefing(an="true"))

    def test_aus_fremdem_text_wird_nicht_geschaltet(self):
        antwort = asyncio.run(katalog.ausfuehren("morgenbriefing", {"an": True, "fremde_inhalte": False},
                                                 fremde_inhalte=True))["ergebnis"]
        self.assertIn("fremdem Text", antwort)
        self.assertFalse(briefing.ist_an())

    def test_falsche_uhrzeit(self):
        self.assertIn("Nichts geändert", briefingwerkzeug.morgenbriefing(an=True, uhrzeit="halb sieben"))
        self.assertFalse(briefing.ist_an())

    def test_warnt_ohne_geraet(self):
        with mock.patch.object(push, "geraete", return_value=[]):
            self.assertIn("kein Gerät", briefingwerkzeug.morgenbriefing(an=True))


# ---------- Briefing: wann ----------

class Zeitplan(Mit_Uhr):
    def test_fenster(self):
        self.assertFalse(briefing.faellig(MONTAG.replace(hour=6, minute=29), {}))
        self.assertTrue(briefing.faellig(MONTAG.replace(hour=6, minute=30), {}))
        self.assertTrue(briefing.faellig(MONTAG.replace(hour=8, minute=29), {}))
        self.assertFalse(briefing.faellig(MONTAG.replace(hour=8, minute=30), {}))   # am Mittag ist es keins

    def test_einmal_am_tag(self):
        self.assertFalse(briefing.faellig(MONTAG, {"datum": "2026-09-14", "geschickt": True}))
        self.assertTrue(briefing.faellig(MONTAG, {"datum": "2026-09-13", "geschickt": True}))

    def test_nach_fehlschlag_warten_und_begrenzen(self):
        s = {"datum": "2026-09-14", "geschickt": False, "versuche": 1, "versucht": MONTAG.isoformat()}
        self.assertFalse(briefing.faellig(MONTAG + timedelta(minutes=5), s))
        self.assertTrue(briefing.faellig(MONTAG + timedelta(minutes=10), s))
        self.assertFalse(briefing.faellig(MONTAG + timedelta(minutes=30), dict(s, versuche=3)))

    def test_uhrzeit_deuten(self):
        self.assertEqual(briefing.uhrzeit_deuten("7:15").strftime("%H:%M"), "07:15")
        self.assertEqual(briefing.uhrzeit_deuten("06.30").strftime("%H:%M"), "06:30")
        with mock.patch.dict(os.environ, {"BRIEFING_UHRZEIT": "7 Uhr"}):
            self.assertEqual(briefing.uhrzeit(), briefing.STANDARD_UHRZEIT)


class Versand(Mit_Uhr):
    def setUp(self):
        super().setUp()
        self.inhalt = mock.patch.object(briefing, "briefing", mock.AsyncMock(return_value=("Titel", "Text")))
        self.inhalt.start()
        self.geraete = mock.patch.object(push, "geraete", return_value=[{"geraet": "iPhone"}])
        self.geraete.start()

    def tearDown(self):
        self.geraete.stop()
        self.inhalt.stop()
        super().tearDown()

    def _senden(self, gesendet=1):
        return mock.patch.object(push, "senden", return_value={"gesendet": gesendet, "grund": "403: nein"})

    def test_aus_schickt_nichts(self):
        with self._senden() as senden:
            self.assertIn("aus", asyncio.run(briefing.einmal(MONTAG)))
        senden.assert_not_called()

    def test_genau_einmal_und_stand_ist_600(self):
        briefing.einstellen(an=True)
        with self._senden() as senden:
            self.assertEqual(asyncio.run(briefing.einmal(MONTAG)), "Morgenbriefing geschickt.")
            asyncio.run(briefing.einmal(MONTAG + timedelta(minutes=1)))
        senden.assert_called_once()
        self.assertEqual(senden.call_args.args[:2], ("Titel", "Text"))
        self.assertEqual(stat.S_IMODE(briefing._datei().stat().st_mode), 0o600)

    def test_richtet_sich_nach_der_eingestellten_uhrzeit(self):
        briefing.einstellen(an=True, uhrzeit_text="07:00")
        with self._senden() as senden:
            asyncio.run(briefing.einmal(MONTAG))                           # 06:40 — noch nicht
            senden.assert_not_called()
            asyncio.run(briefing.einmal(MONTAG.replace(hour=7)))
            senden.assert_called_once()

    def test_ohne_geraet_wird_kein_versuch_verbraucht(self):
        briefing.einstellen(an=True)
        with mock.patch.object(push, "geraete", return_value=[]), self._senden() as senden:
            self.assertIn("Kein Gerät", asyncio.run(briefing.einmal(MONTAG)))
        senden.assert_not_called()
        self.assertEqual(briefing.stand(), {})

    def test_fehlschlag_wird_wiederholt_aber_begrenzt(self):
        briefing.einstellen(an=True)
        with self._senden(gesendet=0) as senden:
            for minuten in (0, 5, 10, 20, 30, 40):
                asyncio.run(briefing.einmal(MONTAG + timedelta(minutes=minuten)))
        self.assertEqual(senden.call_count, briefing.MAX_VERSUCHE)      # 0, 10, 20 — nicht 5, nicht 30


# ---------- Seite und Schnittstelle ----------

class Schnittstelle(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "BRIEFING_UHRZEIT": ""})
        self.env.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def test_wetter_ist_ein_konnektor_das_briefing_nicht(self):
        ids = {k["id"]: k for k in konnektoren.uebersicht()}
        self.assertNotIn("morgenbriefing", ids)
        self.assertFalse(ids["wetter"]["aktiv"])
        self.assertTrue(ids["wetter"]["ohneZugang"])

    def test_einstellung_lesen_und_setzen(self):
        d = self.c.get("/api/einstellungen/morgenbriefing").json()
        self.assertEqual((d["an"], d["uhrzeit"]), (False, "06:30"))
        self.assertEqual(d["teile"], {"wetter": False, "schule": False, "termine": False})
        d = self.c.post("/api/einstellungen/morgenbriefing", json={"an": True, "uhrzeit": "07:05"}).json()
        self.assertEqual((d["an"], d["uhrzeit"]), (True, "07:05"))
        d = self.c.post("/api/einstellungen/morgenbriefing", json={"an": False}).json()
        self.assertEqual((d["an"], d["uhrzeit"]), (False, "07:05"))

    def test_falsche_uhrzeit_ist_400(self):
        r = self.c.post("/api/einstellungen/morgenbriefing", json={"uhrzeit": "halb"})
        self.assertEqual(r.status_code, 400)

    def test_vorschau(self):
        with mock.patch.object(briefing, "briefing", mock.AsyncMock(return_value=("Guten Morgen", "Wetter: klar"))):
            text = self.c.get("/api/morgenbriefing/vorschau").json()["text"]
        self.assertEqual(text, "Guten Morgen\nWetter: klar")

    def test_block_in_den_einstellungen(self):
        seite = self.c.get("/").text
        for id_ in ("b-an", "b-uhrzeit", "b-vorschau", "b-ausgabe"):
            self.assertIn(f'id="{id_}"', seite, id_)
        r = self.c.get("/briefing.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r.headers["content-type"])


class Dateirechte(unittest.TestCase):
    """Alle Zustandsdateien in data/ sind 600 — diese beiden waren bis 14.09.2026 644."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _rechte(self, pfad):
        return stat.S_IMODE(pfad.stat().st_mode)

    def test_graphpause(self):
        graph.pause_setzen(datetime(2026, 9, 15, 7, 5, tzinfo=timezone.utc))
        self.assertEqual(self._rechte(graph._pausendatei()), 0o600)
        self.assertTrue(graph.pause_bis().startswith("2026-09-15T07:05"))

    def test_spotify_geraete(self):
        spotify.erlaubte_setzen([{"id": "abc", "name": "PC"}])
        self.assertEqual(self._rechte(spotify._geraetedatei()), 0o600)
        self.assertEqual(spotify.erlaubte(), [{"id": "abc", "name": "PC"}])


if __name__ == "__main__":
    unittest.main()
