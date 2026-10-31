"""Schulmanager: Deutung, Texte, Freigaben — ohne Netz und ohne Anmeldung.

Die Stundendaten kommen aus `test/daten/` (Aufbau echt, Inhalte erfunden). Ein
Test, der sich bei jedem Lauf bei IServ anmeldet, wäre langsam und würde das
Schulkonto belasten; der echte Netzweg wird von Hand geprüft.
"""

import asyncio
import json
import os
import stat
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from kern import konnektoren
from kern.dienste import schulmanager as dienst
from kern.main import app
from kern.werkzeuge import katalog, schule

DATEN = Path(__file__).parent / "daten"
STUNDEN = json.loads((DATEN / "stundenplan.json").read_text(encoding="utf-8"))["data"]
AUFGABEN = json.loads((DATEN / "hausaufgaben.json").read_text(encoding="utf-8"))["data"]
DONNERSTAG = datetime(2026, 9, 3, 7, 30, tzinfo=ZoneInfo("Europe/Berlin"))


class Mit_Ordner(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(),
                                                "ISERV_USER": "", "ISERV_PASS": ""})
        self.env.start()
        self.uhr = mock.patch("kern.config.jetzt", return_value=DONNERSTAG)
        self.uhr.start()
        dienst.vergessen()

    def tearDown(self):
        self.uhr.stop()
        self.env.stop()
        dienst.vergessen()


class Deutung(unittest.TestCase):
    def test_tage(self):
        do = date(2026, 9, 3)
        self.assertEqual(schule.tag_deuten("heute", do), do)
        self.assertEqual(schule.tag_deuten("morgen", do), date(2026, 9, 4))
        self.assertEqual(schule.tag_deuten("am Montag", do), date(2026, 9, 7))    # nie der letzte Montag
        self.assertEqual(schule.tag_deuten("Donnerstag", do), do)                  # heute eingeschlossen
        self.assertEqual(schule.tag_deuten("2026-09-10", do), date(2026, 9, 10))
        self.assertEqual(schule.tag_deuten("quatsch", do), do)


class Texte(Mit_Ordner):
    def test_vertretung_nennt_was_eigentlich_gewesen_waere(self):
        z = schule.stunde_zeile(STUNDEN[2])
        self.assertIn("VERTRETUNG", z)
        self.assertIn("eigentlich Englisch bei Test", z)
        self.assertIn("bei Beispiel in A 2.02", z)

    def test_ausfall_mit_fach_aus_der_urspruenglichen_stunde(self):
        z = schule.stunde_zeile(STUNDEN[3])
        self.assertIn("Chemie", z)      # Kürzel „ch104“ wird zum Fachnamen (25.09.2026)
        self.assertIn("FÄLLT AUS", z)

    def test_tag_mit_zusammenfassung(self):
        with mock.patch.object(dienst, "stundenplan", mock.AsyncMock(return_value=STUNDEN)):
            text = asyncio.run(schule.stundenplan("heute"))
        self.assertTrue(text.startswith("Donnerstag, 03.09.2026 (1 Vertretung):"))
        self.assertEqual(text.count(". Stunde"), 3)

    def test_woche_ohne_leeres_wochenende(self):
        with mock.patch.object(dienst, "stundenplan", mock.AsyncMock(return_value=STUNDEN)) as abruf:
            text = asyncio.run(schule.stundenplan("diese Woche"))
        self.assertEqual(abruf.call_args.args, (date(2026, 8, 31), date(2026, 9, 6)))
        self.assertIn("Freitag, 04.09.2026 (1 fällt aus)", text)
        self.assertNotIn("Samstag", text)

    def test_hausaufgaben_ohne_leere_alte_und_ohne_erfundenen_termin(self):
        with mock.patch.object(dienst, "hausaufgaben", mock.AsyncMock(return_value=AUFGABEN)):
            text = asyncio.run(schule.hausaufgaben(14))
        self.assertEqual(text.count("gestellt am"), 3)
        self.assertNotIn("ch104", text)          # nur Leerzeichen
        self.assertNotIn("Alte Aufgabe", text)   # außerhalb des Zeitraums
        self.assertIn("Blatt mitbringen App installieren", text)
        self.assertNotIn("bis ", text.split("(")[0])

    def test_anmeldefehler_wird_ein_satz(self):
        with mock.patch.object(dienst, "stundenplan", mock.AsyncMock(side_effect=dienst.AnmeldeFehler("abgewiesen"))):
            self.assertIn("abgewiesen", asyncio.run(schule.stundenplan("heute")))


class Stundenzeiten(Mit_Ordner):
    """Friday behauptete am 14.09.2026, die Uhrzeiten der Stunden seien aus dem
    Stundenplan nicht auslesbar — und erfand daraufhin welche. Tatsächlich liefert
    `schedules/get-class-hours` sie (gemessen: 1. Stunde 08:10–08:55,
    6. Stunde 12:45–13:30). Der Stundenplan wird damit angereichert."""

    ZEITEN = [{"id": 32536, "number": "1", "from": "08:10:00", "until": "08:55:00"},
              {"id": 32541, "number": "6", "from": "12:45:00", "until": "13:30:00"}]

    def test_dienst_traegt_die_zeiten_nach(self):
        stunden = [{"date": "2026-09-14", "classHour": {"id": 32536, "number": "1"},
                    "type": "regularLesson", "actualLesson": {"subjectLabel": "BI281"}}]

        async def lauf():
            with mock.patch.object(dienst, "schueler", mock.AsyncMock(return_value={"id": 1})), \
                 mock.patch.object(dienst, "abfrage", mock.AsyncMock(side_effect=[stunden, self.ZEITEN])):
                dienst._stundenzeiten = None
                return await dienst.stundenplan(date(2026, 9, 14), date(2026, 9, 14))

        ergebnis = asyncio.run(lauf())
        self.assertEqual(ergebnis[0]["classHour"]["from"], "08:10")
        self.assertEqual(ergebnis[0]["classHour"]["until"], "08:55")

    def test_zeile_zeigt_die_uhrzeit(self):
        zeile = schule.stunde_zeile({"type": "regularLesson",
                                     "classHour": {"number": "6", "from": "12:45", "until": "13:30"},
                                     "actualLesson": {"subjectLabel": "MA281"}})
        self.assertIn("6. Stunde (12:45–13:30)", zeile)

    def test_ohne_zeiten_keine_erfundene_uhrzeit(self):
        zeile = schule.stunde_zeile({"type": "regularLesson",
                                     "classHour": {"number": "3"},
                                     "actualLesson": {"subjectLabel": "CH281"}})
        self.assertIn("3. Stunde", zeile)
        self.assertNotIn("(", zeile.split(":")[0])        # keine Klammer mit Zeit davor


class KeineDatenIstKeinFreierTag(Mit_Ordner):
    """Am 14.09.2026 meldete das Werkzeug für den 17.11. „kein Unterricht
    eingetragen" — dabei lagen für die ganze Woche keine Daten vor. Friday las
    das als „frei" und dachte sich daraufhin einen Stundenplan aus, samt einer
    Lehrkraft „Peters" und einem Raum „B 1.15". Beide gibt es an der Schule nicht."""

    def test_leere_woche_meldet_fehlende_daten(self):
        text = schule.tages_text([], date(2026, 11, 17))
        self.assertIn("keine Stundenplandaten", text)
        self.assertIn("NICHT", text)                      # nicht als „frei" missverstehen
        self.assertNotIn("kein Unterricht eingetragen", text)

    def test_einzelner_freier_tag_bleibt_kein_unterricht(self):
        """Gibt es in der Woche Unterricht, nur an diesem Tag nicht, ist der Tag frei.

        Den freien Tag aus den Daten suchen, nicht raten: Ein einfaches
        Weiterzählen landete auf einem Tag, der sehr wohl Unterricht hat."""
        belegt = {s["date"] for s in STUNDEN}
        erster = date.fromisoformat(min(belegt))
        montag = date.fromordinal(erster.toordinal() - erster.weekday())
        frei = None
        for i in range(7):
            tag = date.fromordinal(montag.toordinal() + i)
            if tag.isoformat() not in belegt:
                frei = tag
                break
        self.assertIsNotNone(frei, "Testdaten haben an jedem Wochentag Unterricht")
        text = schule.tages_text(STUNDEN, frei)
        self.assertIn("kein Unterricht eingetragen", text)
        self.assertNotIn("keine Stundenplandaten", text)

    def test_alles_faellt_aus_heisst_nein_auf_schule(self):
        """Gespräch 429: „Habe ich Montag Schule?“ — Ja, aber alles fiel aus."""
        tag = date(2026, 10, 5)
        stunden = [{"date": tag.isoformat(), "type": "cancelledLesson", "classHour": {"number": n}} for n in range(1, 7)]
        text = schule.tages_text(stunden, tag)
        self.assertIn("ALLE 6 Stunden fallen aus", text)
        self.assertIn("mit Nein", text)
        teil = schule.tages_text(stunden[:2], tag)               # nur zwei eingetragen: nichts behaupten
        self.assertNotIn("mit Nein", teil)


class Freigabe(Mit_Ordner):
    def _namen(self):
        return {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}

    def test_aus_ist_der_standard_und_dann_unsichtbar(self):
        konnektoren.zugang_setzen("schulmanager", "eike", "geheim")
        self.assertNotIn("stundenplan", self._namen())
        r = asyncio.run(katalog.ausfuehren("stundenplan", {"tag": "heute"}))
        self.assertIn("nicht freigegeben", r["fehler"])

    def test_an_aber_ohne_zugang_bleibt_unsichtbar(self):
        konnektoren.setzen("schulmanager", True)
        self.assertNotIn("stundenplan", self._namen())

    def test_an_mit_zugang(self):
        konnektoren.setzen("schulmanager", True)
        konnektoren.zugang_setzen("schulmanager", "eike", "geheim")
        self.assertTrue({"stundenplan", "hausaufgaben", "uhrzeit"} <= self._namen())

    def test_abgewiesene_daten_werden_nicht_nochmal_probiert(self):
        konnektoren.zugang_setzen("schulmanager", "eike", "falsch")
        dienst._abgewiesen = dienst._fingerabdruck("eike", "falsch")
        with mock.patch("httpx.AsyncClient", side_effect=AssertionError("keine Anmeldung!")):
            with self.assertRaises(dienst.AnmeldeFehler):
                asyncio.run(dienst.anmeldung())
        dienst.vergessen()
        self.assertIsNone(dienst._abgewiesen)


class Seite(Mit_Ordner):
    def setUp(self):
        super().setUp()
        self.c = TestClient(app)

    def test_liste_schalter_und_zugang(self):
        k = self.c.get("/api/konnektoren").json()[0]
        self.assertEqual((k["id"], k["aktiv"], k["benutzer"]), ("schulmanager", False, None))
        self.c.post("/api/konnektoren/schulmanager", json={"aktiv": True})
        self.c.post("/api/konnektoren/schulmanager/zugang", json={"benutzer": "eike.m", "passwort": "GanzGeheim1"})
        roh = self.c.get("/api/konnektoren").text
        self.assertNotIn("GanzGeheim1", roh)                 # das Passwort kommt nie zurück
        k = json.loads(roh)[0]
        self.assertEqual((k["aktiv"], k["benutzer"]), (True, "eike.m"))
        datei = Path(os.environ["FRIDAY_DATEN"]) / "zugaenge.json"
        self.assertEqual(stat.S_IMODE(datei.stat().st_mode), 0o600)
        self.c.delete("/api/konnektoren/schulmanager/zugang")
        self.assertIsNone(self.c.get("/api/konnektoren").json()[0]["benutzer"])

    def test_unbekannter_konnektor(self):
        self.assertEqual(self.c.post("/api/konnektoren/gibtsnicht", json={"aktiv": True}).status_code, 404)

    def test_pruefen_meldet_und_vermerkt(self):
        with mock.patch.object(dienst, "anmeldung", mock.AsyncMock(return_value={"benutzer": {"firstname": "Eike"}})):
            r = self.c.post("/api/konnektoren/schulmanager/pruefen").json()
        self.assertTrue(r["ok"])
        self.assertIn("Eike", r["text"])
        self.assertTrue(self.c.get("/api/konnektoren").json()[0]["pruefung"]["ok"])

    def test_pruefen_ohne_zugang(self):
        r = self.c.post("/api/konnektoren/schulmanager/pruefen").json()
        self.assertFalse(r["ok"])
        self.assertIn("Zugangsdaten", r["text"])


class KeinFremderDienst(Mit_Ordner):
    """Vorschau und Anmeldeprüfung hatten den Schulmanager als Auffangfall.

    Dadurch zeigte „Wie läuft der Kanal?" beim Studio den STUNDENPLAN, und
    „Anmeldung prüfen" meldete dort „Angemeldet als Eike. Der Schulmanager
    antwortet." — und vermerkte das auch noch als Prüfergebnis des falschen
    Dienstes (gemeldet von Eike, 14.09.2026). Fremde Daten unter falscher
    Überschrift sind schlimmer als ein fehlender Knopf."""

    def setUp(self):
        super().setUp()
        self.c = TestClient(app)

    def _ohne_schulmanager(self):
        """Jeder Aufruf beim Schulmanager wäre hier ein Durchfall auf den
        falschen Dienst — also lassen wir ihn laut scheitern."""
        return mock.patch.object(dienst, "anmeldung",
                                 mock.AsyncMock(side_effect=AssertionError("Schulmanager gerufen")))

    def test_pruefen_faellt_nicht_auf_den_schulmanager_durch(self):
        for kid in ("studio", "gvh"):
            with self.subTest(kid), self._ohne_schulmanager():
                r = self.c.post(f"/api/konnektoren/{kid}/pruefen").json()
            self.assertNotIn("Schulmanager", r["text"], kid)
            self.assertNotIn("Eike", r["text"], kid)

    def test_vorschau_zeigt_nie_den_stundenplan_eines_anderen(self):
        konnektoren.setzen("gvh", True)
        with self._ohne_schulmanager():
            r = self.c.get("/api/konnektoren/gvh/vorschau")
        self.assertNotIn("Stunde:", r.text)
        self.assertNotIn("Hausaufgaben", r.text)

    def test_unbekannter_dienst_sagt_es_ehrlich(self):
        konnektoren.KONNEKTOREN["probe"] = {
            "name": "Probe", "beschreibung": "", "zugang": "", "hinweis": "",
            "werkzeuge": {}, "ohne_zugang": True, "hilfe_url": "https://example.com",
        }
        try:
            konnektoren.setzen("probe", True)
            with self._ohne_schulmanager():
                r = self.c.get("/api/konnektoren/probe/vorschau")
            self.assertEqual(r.status_code, 501)
            self.assertIn("keine Vorschau", r.json()["detail"])
        finally:
            konnektoren.KONNEKTOREN.pop("probe", None)


class IServMeldung(unittest.TestCase):
    def test_grund_von_der_anmeldeseite(self):
        html = '<form><div class="alert alert-danger" role="alert">\n <b>Falsches</b> Passwort.</div><input name="_password"></form>'
        self.assertEqual(dienst.iserv_meldung(html), "Falsches Passwort.")
        self.assertEqual(dienst.iserv_meldung('<form><input name="_password"></form>'), "")



# ---------- Elternbriefe: verarbeitet beim Ankommen ----------
import io        # noqa: E402
import zipfile   # noqa: E402

from kern.schule import ablage, briefe   # noqa: E402

BRIEFE = [  # Aufbau echt (letters/get-letters), Inhalte erfunden
    {"id": 7, "title": "Wandertag Klasse 10", "sentDate": "2026-09-01T08:00:00.000Z", "createdAt": "2026-08-31",
     "studentStatuses": [{"id": 1, "studentId": 5, "readTimestamp": None, "sentTimestamp": "2026-09-01"}]},
    {"id": 3, "title": "Elternabend", "sentDate": "2026-08-20T08:00:00.000Z", "createdAt": "2026-08-19",
     "studentStatuses": [{"id": 2, "studentId": 5, "readTimestamp": "2026-08-21", "sentTimestamp": "2026-08-20"}]},
]


def _docx(text):
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
                                        "<w:p><w:r><w:t>Zeile 2</w:t></w:r></w:p></w:body></w:document>")
    return puffer.getvalue()


VOLL = {  # Aufbau echt (poqa findByPk), Inhalte erfunden
    7: {"id": 7, "title": "Wandertag Klasse 10", "sentDate": "2026-09-01T08:00:00Z",
        "answerDeadline": "2026-09-08T00:00:00Z", "text": "<p>Liebe Eltern,</p><p>am&nbsp;Freitag &amp; Samstag</p>",
        "attachments": [{"filename": "Info.pdf", "contentType": "application/pdf", "file": "f1"},
                        {"filename": "../Anmeldung.docx", "contentType": briefe.DOCX, "file": "f2"},
                        {"filename": "Film.mp4", "contentType": "video/mp4", "file": "f3"}]},
    3: {"id": 3, "title": "Elternabend", "sentDate": "2026-08-20T08:00:00Z", "text": "<p>Am 24.09.</p>",
        "attachments": []},
}
ERGEBNIS = {"kurz": "Wandertag zum Deister am 11.09.", "inhalt": "Die Klasse wandert zum Deister.",
            "termine": [{"datum": "2026-09-11", "was": "Wandertag, 8 Uhr am Bahnhof"}],
            "fristen": [{"datum": "2026-09-07", "was": "Einverständnis abgeben"}, {"datum": "2026-08-01", "was": "alt"}],
            "zu_tun": ["Einverständnis unterschreiben"], "kosten": "5 Euro Fahrt"}


class Elternbriefe(Mit_Ordner):
    def setUp(self):
        super().setUp()
        briefe._zuletzt = 0.0
        konnektoren.setzen("schulmanager", True)
        konnektoren.zugang_setzen("schulmanager", "eike", "geheim")
        self.gemini = mock.AsyncMock(return_value=(ERGEBNIS, "gemini-test"))
        dateien = {"f1": b"%PDF-1.4 test", "f2": _docx("Anmeldung bis Montag"), "f3": b"\x00"}
        self.flicken = [
            mock.patch.object(dienst, "elternbriefe", mock.AsyncMock(return_value=BRIEFE)),
            mock.patch.object(dienst, "elternbrief", mock.AsyncMock(side_effect=lambda bid, mit_dateien=False: VOLL.get(bid))),
            mock.patch.object(dienst, "anhang_laden", mock.AsyncMock(side_effect=lambda f: dateien[f])),
            mock.patch.object(briefe, "_gemini", self.gemini)]
        for f in self.flicken:
            f.start()

    def tearDown(self):
        for f in self.flicken:
            f.stop()
        super().tearDown()

    def test_durchgang_verarbeitet_brief_mit_allen_anhaengen(self):
        self.assertEqual(asyncio.run(briefe.durchgang()), 2)
        b = ablage.standard().eintrag(7)
        self.assertEqual((b["status"], b["modell"]), ("verarbeitet", "gemini-test"))
        teile = self.gemini.call_args_list[0].args[0]          # neuester zuerst: Brief 7
        self.assertIn("Liebe Eltern,\nam Freitag & Samstag", teile[0]["text"])
        self.assertTrue(any(t.get("inlineData", {}).get("mimeType") == "application/pdf" for t in teile))
        self.assertTrue(any("Anmeldung bis Montag" in t.get("text", "") for t in teile))   # Word als Text
        self.assertTrue(any("Film.mp4" in t.get("text", "") and "nicht gelesen" in t["text"] for t in teile))
        word = b["anhaenge"][1]
        self.assertNotIn("..", word["datei"])
        self.assertTrue((Path(os.environ["FRIDAY_DATEN"]) / word["datei"]).exists())
        self.assertTrue(b["anhaenge"][2]["ungelesen"])

    def test_nichts_wird_doppelt_verarbeitet(self):
        asyncio.run(briefe.durchgang())
        asyncio.run(briefe.durchgang())
        self.assertEqual(self.gemini.await_count, 2)

    def test_fehler_hoechstens_drei_versuche(self):
        self.gemini.side_effect = RuntimeError("429")
        for _ in range(4):
            asyncio.run(briefe.durchgang())
        b = ablage.standard().eintrag(7)
        self.assertEqual((b["status"], b["versuche"]), ("fehler", 3))
        self.assertEqual(ablage.standard().offen(), [])

    def test_beim_fragen_hoechstens_alle_fuenf_minuten_nachsehen(self):
        asyncio.run(briefe.auffrischen())
        asyncio.run(briefe.auffrischen())
        self.assertEqual(dienst.elternbriefe.await_count, 1)

    def test_ausgeschaltet_wird_nicht_nachgesehen(self):
        konnektoren.setzen("schulmanager", False)
        asyncio.run(briefe.auffrischen())
        self.assertEqual(dienst.elternbriefe.await_count, 0)

    def test_liste_mit_kurzfassung_und_naechster_frist(self):
        zeile = asyncio.run(schule.elternbriefe()).splitlines()[0]
        self.assertIn("Brief 7: „Wandertag Klasse 10“ vom 01.09.2026 (ungelesen) — Wandertag zum Deister", zeile)
        self.assertIn("Nächste Frist: 07.09. (Einverständnis abgeben)", zeile)
        self.assertIn("Brief 7", asyncio.run(schule.elternbriefe(suche="deister")))     # sucht im Inhalt
        self.assertIn("zu „Zirkus“", asyncio.run(schule.elternbriefe(suche="Zirkus")))

    def test_brief_ganz(self):
        text = asyncio.run(schule.elternbrief(7))
        for teil in ("Zusammenfassung: Die Klasse wandert zum Deister.", "Termine: 11.09.2026 Wandertag",
                     "Fristen: 01.08.2026 alt; 07.09.2026 Einverständnis abgeben; 08.09.2026 Antwort im Schulmanager",
                     "Zu tun: Einverständnis unterschreiben", "Kosten: 5 Euro Fahrt", "Film.mp4 (nicht lesbar)",
                     "Originaltext:\nLiebe Eltern,"):
            self.assertIn(teil, text)

    def test_schultermine_nur_kommende_und_sortiert(self):
        zeilen = asyncio.run(schule.schultermine()).splitlines()
        self.assertTrue(zeilen[0].startswith("Montag 07.09.: Frist — Einverständnis abgeben"))
        self.assertEqual(len(zeilen), 5)              # je Brief Frist + Termin, dazu Antwortfrist von Brief 7
        self.assertFalse(any("alt" in z for z in zeilen))

    def test_nummer_kommt_vom_modell_als_text(self):
        r = asyncio.run(katalog.ausfuehren("elternbrief", {"nummer": "99"}))
        self.assertIn("Nummer 99", r["ergebnis"])

    def test_word_text(self):
        self.assertEqual(briefe.docx_text(_docx("Hallo")), "Hallo\nZeile 2")


class HintergrundSchluessel(unittest.TestCase):
    """Dritter Schlüssel zuerst, Graphitis als Reserve, Eikes Gesprächsschlüssel nie — außer als Notnagel."""

    def test_reihenfolge(self):
        # FRIDAY_DATEN auf einen leeren Ordner: der Schlüssel-Pool (eigene Google-Projekte,
        # data/gemini_pool.json) soll die feste Reihenfolge hier nicht durch echte Einträge stören.
        with tempfile.TemporaryDirectory() as leer:
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k1", "GRAPHITI_GEMINI_API_KEY": "k2",
                                              "HINTERGRUND_GEMINI_API_KEY": "k3", "FRIDAY_DATEN": leer}):
                self.assertEqual(briefe_modul().config.hintergrund_schluessel_liste(), ["k3", "k2"])
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k1", "GRAPHITI_GEMINI_API_KEY": "",
                                              "HINTERGRUND_GEMINI_API_KEY": "", "FRIDAY_DATEN": leer}):
                self.assertEqual(briefe_modul().config.hintergrund_schluessel_liste(), ["k1"])

    def test_reserve_erst_wenn_der_eigene_erschoepft_ist(self):
        import httpx
        gesehen = []

        def antwort(request):
            gesehen.append((request.headers["x-goog-api-key"], request.url.path.rsplit("/", 1)[-1]))
            if request.headers["x-goog-api-key"] == "k3":
                return httpx.Response(429)
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"kurz": "ok"}'}]}}]})

        echt = httpx.AsyncClient
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k1", "GRAPHITI_GEMINI_API_KEY": "k2",
                                          "HINTERGRUND_GEMINI_API_KEY": "k3"}), \
             mock.patch("httpx.AsyncClient", lambda **kw: echt(transport=httpx.MockTransport(antwort), **kw)):
            ergebnis, modell = asyncio.run(briefe_modul()._gemini([{"text": "x"}]))
        self.assertEqual([k for k, _ in gesehen], ["k3", "k3", "k2"])   # beide Modelle auf k3, dann Reserve
        self.assertEqual((ergebnis, modell), ({"kurz": "ok"}, "gemini-3.5-flash (Reserve)"))


def briefe_modul():
    from kern.schule import briefe
    return briefe
