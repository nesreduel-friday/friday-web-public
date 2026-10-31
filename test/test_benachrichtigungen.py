"""Benachrichtigungen: eine Stelle, ein Schalter je Art, eine Ruhezeit — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from kern.dienste import benachrichtigungen as b
from kern.main import app
from kern.werkzeuge import benachrichtigung as werkzeug

BERLIN = ZoneInfo("Europe/Berlin")


def _zeit(stunde, minute=0):
    return datetime(2026, 9, 20, stunde, minute, tzinfo=BERLIN)


class Mit_Ordner(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.push = mock.patch("kern.dienste.push.senden",
                               return_value={"gesendet": 1, "geraete": 1, "abgemeldet": 0})
        self.gesendet = self.push.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.push.stop()
        self.env.stop()


class Schalter(Mit_Ordner):
    def test_standard_kommt_aus_der_liste_nicht_aus_wahr(self):
        for a in b.ARTEN:
            self.assertIs(b.an(a["id"]), a["standard"])

    def test_ausgeschaltet_geht_nichts_raus(self):
        b.stellen("briefing", False)
        ergebnis = b.senden("briefing", "Guten Morgen", "Text")
        self.assertEqual(self.gesendet.call_count, 0)
        self.assertEqual(ergebnis["unterdrueckt"], "art_aus")

    def test_eine_art_abschalten_laesst_die_anderen_in_ruhe(self):
        b.stellen("briefing", False)
        b.senden("studio", "Video fertig", "Text")
        self.assertEqual(self.gesendet.call_count, 1)

    def test_unbekannte_art_wird_nicht_still_verschickt(self):
        # Ein Tippfehler im Aufruf ginge sonst an allen Schaltern vorbei.
        with self.assertRaises(b.UnbekannteArt):
            b.senden("briefng", "Titel", "Text")
        self.assertEqual(self.gesendet.call_count, 0)

    def test_unbekannte_art_laesst_sich_nicht_stellen(self):
        with self.assertRaises(b.UnbekannteArt):
            b.stellen("gibtsnicht", True)

    def test_die_einstellung_ueberlebt_einen_neustart(self):
        b.stellen("studio", False)
        self.assertFalse(b.an("studio"))
        self.assertIn("benachrichtigungen.json", os.listdir(self.ordner))
        self.assertFalse(b.an("studio"))

    def test_datei_liegt_mit_rechten_600(self):
        b.stellen("studio", False)
        rechte = (Path(self.ordner) / "benachrichtigungen.json").stat().st_mode & 0o777
        self.assertEqual(rechte, 0o600)


class Ruhezeit(Mit_Ordner):
    def test_aus_heisst_nie_still(self):
        self.assertFalse(b.still(_zeit(3)))

    def test_ueber_mitternacht_hinweg(self):
        # 22:30–06:30 ist EIN Zeitraum. Mit „ab <= h < bis" wäre er nachts leer.
        b.ruhe_stellen(ein=True, ab="22:30", bis="06:30")
        for stunde, minute in ((23, 0), (2, 0), (6, 0), (22, 30)):
            self.assertTrue(b.still(_zeit(stunde, minute)), f"{stunde}:{minute}")
        for stunde, minute in ((6, 30), (12, 0), (22, 29)):
            self.assertFalse(b.still(_zeit(stunde, minute)), f"{stunde}:{minute}")

    def test_ein_tagsueber_liegender_zeitraum_geht_auch(self):
        b.ruhe_stellen(ein=True, ab="09:00", bis="12:00")
        self.assertTrue(b.still(_zeit(10)))
        self.assertFalse(b.still(_zeit(13)))
        self.assertFalse(b.still(_zeit(3)))

    def test_in_der_ruhezeit_bleibt_es_still(self):
        b.ruhe_stellen(ein=True, ab="22:30", bis="06:30")
        with mock.patch("kern.config.jetzt", return_value=_zeit(23, 30)):
            ergebnis = b.senden("studio", "Video fertig", "Text")
        self.assertEqual(self.gesendet.call_count, 0)
        self.assertEqual(ergebnis["unterdrueckt"], "ruhezeit")

    def test_was_eike_selbst_gestellt_hat_kommt_trotzdem(self):
        # Wer sich um 23:40 wecken lässt, will um 23:40 geweckt werden.
        b.ruhe_stellen(ein=True, ab="22:30", bis="06:30")
        with mock.patch("kern.config.jetzt", return_value=_zeit(23, 40)):
            b.senden("erinnerungen", "Müll rausbringen", "Text", dringend=True)
        self.assertEqual(self.gesendet.call_count, 1)

    def test_dringend_hilft_nicht_gegen_einen_ausgeschalteten_schalter(self):
        b.stellen("erinnerungen", False)
        b.senden("erinnerungen", "Titel", "Text", dringend=True)
        self.assertEqual(self.gesendet.call_count, 0)

    def test_unsinnige_uhrzeit_faellt_auf_den_alten_wert_zurueck(self):
        b.ruhe_stellen(ein=True, ab="22:30", bis="06:30")
        self.assertEqual(b.ruhe_stellen(ab="viertel nach")["ab"], "22:30")

    def test_gleiche_zeiten_heissen_keine_ruhe(self):
        # Sonst wäre je nach Lesart 24 Stunden oder gar nicht still.
        b.ruhe_stellen(ein=True, ab="08:00", bis="08:00")
        self.assertFalse(b.still(_zeit(8)))


class AllesGehtDurchEineTuer(Mit_Ordner):
    """Kein Teil von Friday darf am Schalterwerk vorbei senden."""

    # Die Testnachricht prüft die Technik und muss deshalb direkt auf push gehen.
    AUSNAHMEN = {"kern/dienste/push.py", "kern/dienste/benachrichtigungen.py", "kern/main.py"}

    def test_niemand_ruft_push_senden_direkt(self):
        wurzel = Path(b.__file__).resolve().parent.parent
        for datei in wurzel.rglob("*.py"):
            kurz = f"kern/{datei.relative_to(wurzel)}"
            if kurz in self.AUSNAHMEN:
                continue
            with self.subTest(kurz):
                self.assertFalse("push.senden" in datei.read_text(encoding="utf-8"))

    def test_in_main_nur_die_testnachricht(self):
        text = (Path(b.__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
        self.assertEqual(text.count("push.senden"), 2)     # sofort und verzögert
        self.assertIn("push_test", text)

    def test_jede_benutzte_art_gibt_es_wirklich(self):
        wurzel = Path(b.__file__).resolve().parent.parent
        import re
        muster = re.compile(r'benachrichtigungen\.senden,\s*"([a-z_]+)"')
        gefunden = set()
        for datei in wurzel.rglob("*.py"):
            gefunden |= set(muster.findall(datei.read_text(encoding="utf-8")))
        self.assertTrue(gefunden)                          # sonst prüft der Test nichts
        self.assertTrue(gefunden <= b.KENNT, gefunden - b.KENNT)


class Werkzeuge(Mit_Ordner):
    def test_sagt_was_an_und_was_aus_ist(self):
        b.stellen("studio", False)
        text = asyncio.run(werkzeug.benachrichtigungen())
        self.assertIn("Morgenbriefing", text.split("Aus:")[0])
        self.assertIn("Studio", text.split("Aus:")[1])

    def test_sagt_es_wenn_kein_geraet_angemeldet_ist(self):
        # Sonst schaltet Friday munter Arten um, während gar nichts ankommt.
        text = asyncio.run(werkzeug.benachrichtigungen())
        self.assertIn("kein Gerät", text)

    def test_schaltet_eine_art_aus(self):
        asyncio.run(werkzeug.benachrichtigung_stellen("briefing", False))
        self.assertFalse(b.an("briefing"))

    def test_versteht_auch_den_anzeigenamen(self):
        asyncio.run(werkzeug.benachrichtigung_stellen("Morgenbriefing", False))
        self.assertFalse(b.an("briefing"))

    def test_erfundene_art_aendert_nichts_und_nennt_die_echten(self):
        text = asyncio.run(werkzeug.benachrichtigung_stellen("Bratkartoffeln", False))
        self.assertIn("briefing", text)
        self.assertTrue(all(b.an(a["id"]) is a["standard"] for a in b.ARTEN))

    def test_mehrdeutiges_wort_schaltet_nichts(self):
        # „Garanti" passt auf „Garantie-Anträge" UND „Verspätungen für die Garantie".
        # Im Zweifel nichts tun und nachfragen, statt das Falsche abzuschalten.
        text = asyncio.run(werkzeug.benachrichtigung_stellen("Garanti", False))
        self.assertTrue(b.an("garantie"))
        self.assertTrue(b.an("verspaetungen"))
        self.assertIn("kenne ich nicht", text)

    def test_ein_genauer_treffer_gewinnt_gegen_den_teiltreffer(self):
        # „garantie" steckt auch in der Beschreibung der Verspätungen — die Kennung zählt.
        asyncio.run(werkzeug.benachrichtigung_stellen("garantie", False))
        self.assertFalse(b.an("garantie"))
        self.assertTrue(b.an("verspaetungen"))

    def test_ruhezeit_stellen(self):
        text = asyncio.run(werkzeug.ruhezeit_stellen(an=True, ab="23:00", bis="07:00"))
        self.assertEqual(b.ruhe(), {"an": True, "ab": "23:00", "bis": "07:00"})
        self.assertIn("Erinnerungen", text)               # der Vorbehalt wird gesagt


class Schnittstelle(Mit_Ordner):
    def test_stand_abfragen(self):
        d = self.client.get("/api/einstellungen/benachrichtigungen").json()
        self.assertEqual({a["id"] for a in d["arten"]}, b.KENNT)
        self.assertIn("ruhe", d)

    def test_art_umschalten(self):
        d = self.client.post("/api/einstellungen/benachrichtigungen",
                             json={"art": "studio", "an": False}).json()
        self.assertFalse(next(a for a in d["arten"] if a["id"] == "studio")["an"])

    def test_unbekannte_art_wird_abgewiesen(self):
        antwort = self.client.post("/api/einstellungen/benachrichtigungen",
                                   json={"art": "gibtsnicht", "an": False})
        self.assertEqual(antwort.status_code, 400)

    def test_art_ohne_an_wird_abgewiesen(self):
        antwort = self.client.post("/api/einstellungen/benachrichtigungen", json={"art": "studio"})
        self.assertEqual(antwort.status_code, 400)

    def test_ruhezeit_setzen(self):
        d = self.client.post("/api/einstellungen/benachrichtigungen",
                             json={"ruhe_an": True, "ruhe_ab": "22:00", "ruhe_bis": "06:00"}).json()
        self.assertEqual(d["ruhe"], {"an": True, "ab": "22:00", "bis": "06:00"})


class Seite(Mit_Ordner):
    """Die Einstellungen-Seite. Ein $('…') ins Leere bricht die ganze Datei still ab —
    beim Laden gibt es dann keine Fehlermeldung, nur eine Seite, auf der nichts geht."""

    def setUp(self):
        super().setUp()
        self.web = Path(b.__file__).resolve().parent.parent.parent / "web"

    def test_skript_wird_eingebunden(self):
        antwort = self.client.get("/index.html")
        self.assertRegex(antwort.text, r'<script src="/benachrichtigungen\.js\?v=\d+"></script>')

    def test_jede_benutzte_kennung_gibt_es_im_html(self):
        import re
        skript = (self.web / "benachrichtigungen.js").read_text(encoding="utf-8")
        html = (self.web / "index.html").read_text(encoding="utf-8")
        kennungen = set(re.findall(r"\$\('([\w-]+)'\)", skript))
        self.assertTrue(kennungen)
        for k in kennungen:
            with self.subTest(k):
                self.assertIn(f'id="{k}"', html)



class Dashboard_Meldung(Mit_Ordner):
    """Jede Meldung, die aufs Handy darf oder nur an der Ruhezeit scheitert, steht auch auf der Übersicht.
    Eine ausgeschaltete Art nie — sonst zeigte die Übersicht, was Eike abgestellt hat."""

    def _karten(self):
        import asyncio
        from kern.dashboard import meldungen
        return asyncio.run(meldungen.karten())

    def test_gesendet_landet_auf_der_uebersicht(self):
        b.senden("studio", "Video fertig", "Text", url="/#studio")
        self.assertEqual([k["titel"] for k in self._karten()], ["Video fertig"])

    def test_art_aus_bleibt_ohne_karte(self):
        b.stellen("studio", False)
        b.senden("studio", "Video fertig", "Text")
        self.assertEqual(self._karten(), [])

    def test_ruhezeit_unterdrueckt_push_aber_nicht_die_karte(self):
        with mock.patch("kern.dienste.benachrichtigungen.still", return_value=True):
            ergebnis = b.senden("studio", "Video fertig", "Text")
        self.assertEqual(ergebnis["unterdrueckt"], "ruhezeit")
        self.assertEqual(len(self._karten()), 1)

    def test_ein_kaputter_ablagepfad_stoppt_den_push_nicht(self):
        with mock.patch("kern.dashboard.meldungen.ablegen", side_effect=OSError("voll")):
            ergebnis = b.senden("studio", "Video fertig", "Text")
        self.assertEqual(ergebnis["unterdrueckt"], "")
        self.assertEqual(self.gesendet.call_count, 1)


if __name__ == "__main__":
    unittest.main()
