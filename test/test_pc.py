"""PC (Windows): der Helfer auf dem PC und Friday davor — ohne Windows, ohne Netz nach draußen.

Die Dateiaktionen des Helfers laufen auch unter Linux; hier läuft er als echter
HTTP-Server auf 127.0.0.1, und Friday spricht mit ihm wie mit dem PC."""

import asyncio
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from kern import konnektoren
from kern.dienste import pc as pcdienst
from kern.werkzeuge import katalog
from kern.werkzeuge import pc as pcwerkzeug

HELFER_DATEI = Path(__file__).resolve().parent.parent / "pc-helfer" / "friday_pc.py"


def _helfer_laden(daten: str):
    with mock.patch.dict(os.environ, {"FRIDAY_PC_DATEN": daten}):
        spec = importlib.util.spec_from_file_location(f"friday_pc_{id(daten)}", HELFER_DATEI)
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
    return modul


def _run(coro):
    return asyncio.run(coro)


class HelferBasis(unittest.TestCase):
    """Ein Helfer mit eigenem Datenordner und zwei freigegebenen Ordnern."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.daten = os.path.join(self.tmp, "pcdaten")
        self.dl = Path(self.tmp, "dl")
        self.doku = Path(self.tmp, "doku")
        self.geheim = Path(self.tmp, "geheim")
        for p in (self.dl, self.doku, self.geheim):
            p.mkdir()
        (self.dl / "Rechnung.pdf").write_bytes(b"%PDF-1.4 x")
        (self.dl / "setup.exe").write_bytes(b"MZ")
        (self.geheim / "passwoerter.txt").write_text("x")
        os.makedirs(self.daten)
        self.token = "t" * 40
        with open(os.path.join(self.daten, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"token": self.token, "port": 0, "erlaubte_quellen": ["127.0.0.1"], "standard_ordner": False,
                       "ordner": {"Downloads": str(self.dl), "Dokumente": str(self.doku)}}, f)
        self.h = _helfer_laden(self.daten)
        self.k = self.h.konfig()

    def tu(self, art, angaben=None, **kw):
        return self.h.ausfuehren(art, angaben or {}, k=self.k, **kw)


class Pfade(HelferBasis):
    def test_nur_in_freigegebenen_ordnern(self):
        self.assertEqual(self.h.aufloesen("Downloads/Rechnung.pdf", self.k["ordner"])[2],
                         Path(os.path.realpath(self.dl / "Rechnung.pdf")))
        self.assertEqual(self.h.aufloesen("downloads\\Rechnung.pdf", self.k["ordner"])[0], "Downloads")
        for boese in ("Downloads/../geheim/passwoerter.txt", "C:/Windows/system32", "C:\\Windows", "geheim/x",
                      "/etc/passwd", "", "Downloads/Rechnung.pdf:stream", "Downloads/" + "a" * 500):
            with self.subTest(boese):
                with self.assertRaises(self.h.Abgelehnt):
                    self.h.aufloesen(boese, self.k["ordner"])

    def test_verknuepfung_fuehrt_nicht_hinaus(self):
        os.symlink(self.geheim, self.dl / "abkuerzung")
        with self.assertRaises(self.h.Abgelehnt):
            self.h.aufloesen("Downloads/abkuerzung/passwoerter.txt", self.k["ordner"])
        r = self.tu("verschieben", {"von": "Downloads/abkuerzung/passwoerter.txt", "nach": "Dokumente"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["risiko"], "verboten")
        self.assertTrue((self.geheim / "passwoerter.txt").exists())


class Einstufung(HelferBasis):
    def test_verschieben_und_umbenennen_ohne_rueckfrage(self):
        # Eikes Wunsch 07.10.2026: keine Bestätigung mehr; Ordnergrenzen und „überschreibt nie“ bleiben.
        probe = self.tu("verschieben", {"von": "Downloads/Rechnung.pdf", "nach": "Dokumente"}, probe=True)
        self.assertEqual(probe["risiko"], "sicher")
        self.assertIn("Rechnung.pdf", probe["beschreibung"])
        r = self.tu("verschieben", {"von": "Downloads/Rechnung.pdf", "nach": "Dokumente"})
        self.assertTrue(r["ok"], r)
        self.assertTrue((self.doku / "Rechnung.pdf").exists())
        self.assertFalse((self.dl / "Rechnung.pdf").exists())
        r = self.tu("umbenennen", {"pfad": "Dokumente/Rechnung.pdf", "name": "Rechnung2.pdf"})
        self.assertTrue(r["ok"], r)
        self.assertTrue((self.doku / "Rechnung2.pdf").exists())

    def test_verschieben_nur_in_freigegebene_ordner_auch_ohne_rueckfrage(self):
        for nach in ("C:/Windows", "Downloads/../geheim"):
            r = self.tu("verschieben", {"von": "Downloads/Rechnung.pdf", "nach": nach})
            self.assertFalse(r["ok"], nach)
            self.assertTrue((self.dl / "Rechnung.pdf").exists())

    def test_fenster_kurz_ohne_doppelte_gekuerzt_und_begrenzt(self):
        liste = [{"hwnd": i, "titel": "T" * 100, "programm": f"prog{i % 12}.exe", "minimiert": False}
                 for i in range(30)]
        liste.insert(0, {"hwnd": 99, "titel": "Egal", "programm": "", "minimiert": False})   # ohne Programmnamen: weg
        kurz = self.h.fenster_kurz(liste)
        self.assertEqual(len(kurz), 8)
        self.assertEqual(len({f["programm"] for f in kurz}), 8)
        self.assertTrue(all(len(f["titel"]) <= 60 and not f["programm"].endswith(".exe") for f in kurz))
        self.assertEqual(self.h.fenster_kurz([]), [])

    def test_laufzeit_ohne_windows_ist_none(self):
        with mock.patch.object(self.h, "WINDOWS", False):
            self.assertIsNone(self.h.laufzeit_s())

    def test_ueberschreibt_nie(self):
        (self.doku / "Rechnung.pdf").write_text("alt")
        r = self.tu("verschieben", {"von": "Downloads/Rechnung.pdf", "nach": "Dokumente"}, probe=True)
        self.assertFalse(r["ok"])
        self.assertIn("schon", r["text"])
        r = self.tu("kopieren", {"von": "Downloads/Rechnung.pdf", "nach": "Dokumente/Rechnung.pdf"})
        self.assertFalse(r["ok"])
        self.assertEqual((self.doku / "Rechnung.pdf").read_text(), "alt")

    def test_loeschen_in_den_papierkorb_nur_bestaetigt(self):
        r = self.tu("loeschen", {"pfad": "Downloads/Rechnung.pdf"})
        self.assertFalse(r["ok"])
        self.assertTrue((self.dl / "Rechnung.pdf").exists())
        r = self.tu("loeschen", {"pfad": "Downloads/Rechnung.pdf"}, bestaetigt=True)
        self.assertTrue(r["ok"], r)
        self.assertFalse((self.dl / "Rechnung.pdf").exists())
        self.assertEqual(len(list(Path(self.daten, "papierkorb").iterdir())), 1)   # wiederherstellbar

    def test_freigegebenen_ordner_selbst_fasst_er_nicht_an(self):
        for art, angaben in (("loeschen", {"pfad": "Downloads"}), ("verschieben", {"von": "Downloads", "nach": "Dokumente"}),
                             ("umbenennen", {"pfad": "Dokumente", "name": "x"})):
            with self.subTest(art):
                self.assertEqual(self.tu(art, angaben, probe=True)["risiko"], "verboten")

    def test_oeffnen_programme_ohne_rueckfrage(self):
        # Eikes Wunsch 07.10.2026. Der Server fragt nur noch nach fremdem Text (FridayMitHelfer).
        probe = self.tu("oeffnen", {"pfad": "Downloads/setup.exe"}, probe=True)
        self.assertEqual(probe["risiko"], "sicher")
        self.assertIn("AUSFÜHREN", probe["beschreibung"])
        self.assertEqual(self.tu("oeffnen", {"pfad": "Downloads/Rechnung.pdf"}, probe=True)["risiko"], "sicher")

    def test_sicheres_laeuft_sofort(self):
        r = self.tu("ordner_anlegen", {"pfad": "Dokumente/Schule/Physik"})
        self.assertTrue(r["ok"], r)
        self.assertTrue((self.doku / "Schule" / "Physik").is_dir())
        r = self.tu("kopieren", {"von": "Downloads/Rechnung.pdf", "nach": "Dokumente/Schule"})
        self.assertTrue(r["ok"], r)
        self.assertTrue((self.doku / "Schule" / "Rechnung.pdf").exists())

    def test_umbenennen_behaelt_endung_und_prueft_namen(self):
        probe = self.tu("umbenennen", {"pfad": "Downloads/Rechnung.pdf", "name": "Stromrechnung"}, probe=True)
        self.assertEqual(probe["risiko"], "sicher")
        self.assertIn("Stromrechnung.pdf", probe["beschreibung"])
        for name in ("a/b", "..", "con", "x:y", ""):
            with self.subTest(name):
                self.assertFalse(self.tu("umbenennen", {"pfad": "Downloads/Rechnung.pdf", "name": name}, probe=True)["ok"])

    def test_dateien_ansehen_und_suchen(self):
        self.assertIn("Downloads", self.tu("dateien")["text"])
        liste = self.tu("dateien", {"pfad": "Downloads"})["text"]
        self.assertIn("Downloads/Rechnung.pdf", liste)
        self.assertIn("fremder Text", liste)
        self.assertIn("Downloads/setup.exe", self.tu("dateien", {"suche": "*.exe"})["text"])
        self.assertNotIn("passwoerter", self.tu("dateien", {"suche": "pass"})["text"])

    def test_system_und_unbekanntes(self):
        self.assertEqual(self.tu("system", {"aktion": "herunterfahren"}, probe=True)["risiko"], "bestaetigen")
        self.assertEqual(self.tu("system", {"aktion": "sperren"}, probe=True)["risiko"], "sicher")
        self.assertEqual(self.tu("system", {"aktion": "format c:"}, probe=True)["risiko"], "verboten")
        self.assertEqual(self.tu("shell", {"befehl": "del *"})["risiko"], "verboten")


class Browser(unittest.TestCase):
    def setUp(self):
        self.h = _helfer_laden(tempfile.mkdtemp())

    def e(self, **kw):
        return {"tag": "a", "typ": "", "rolle": "", "text": "", "label": "", "href": "", "download": False, "auto": "",
                "name": "", "formPost": False, "formPasswort": False, **kw}

    def test_klicks(self):
        k = self.h.klick_risiko
        self.assertEqual(k(self.e(text="Impressum", href="https://x.de/impressum")), "sicher")
        self.assertEqual(k(self.e(tag="button", text="Weiter")), "sicher")
        for text in ("Jetzt kaufen", "Zahlungspflichtig bestellen", "Nachricht senden", "Konto löschen", "Anmelden",
                     "Zur Kasse", "Buy now"):
            with self.subTest(text):
                self.assertEqual(k(self.e(tag="button", text=text)), "bestaetigen")
        self.assertEqual(k(self.e(tag="button", typ="submit", text="OK", formPost=True)), "bestaetigen")
        self.assertEqual(k(self.e(tag="input", typ="submit", text="Suche")), "sicher")     # GET-Suchformular
        self.assertEqual(k(self.e(text="Datei", download=True)), "bestaetigen")

    def test_tippen(self):
        t = self.h.tipp_risiko
        self.assertEqual(t(self.e(tag="input", typ="search", label="Suche"), absenden=True), "sicher")
        self.assertEqual(t(self.e(tag="input", typ="password"), absenden=False), "verboten")
        self.assertEqual(t(self.e(tag="input", auto="cc-number"), absenden=False), "verboten")
        self.assertEqual(t(self.e(tag="input", name="iban"), absenden=False), "verboten")
        self.assertEqual(t(self.e(tag="input", label="Kennwort"), absenden=False), "verboten")
        self.assertEqual(t(self.e(tag="textarea", formPost=True), absenden=True), "bestaetigen")
        self.assertEqual(t(self.e(tag="input", typ="submit"), absenden=False), "verboten")

    def test_adressen(self):
        self.assertEqual(self.h.adresse_pruefen("spiegel.de"), ("https://spiegel.de", False))
        for intern in ("http://192.168.178.1", "http://localhost:8080", "http://192.0.2.10", "https://hphome.beispiel.ts.net"):
            with self.subTest(intern):
                self.assertTrue(self.h.adresse_pruefen(intern)[1])
        for boese in ("javascript:alert(1)", "file:///C:/x", "data:text/html,x", "https://"):
            with self.subTest(boese):
                with self.assertRaises(self.h.Abgelehnt):
                    self.h.adresse_pruefen(boese)


class LesenFuerDieSeite(HelferBasis):
    """Strukturierte Antworten für die PC-Seite — dieselben Grenzen wie für das Modell."""

    def test_dateien_ohne_pfad_sind_die_freigegebenen_ordner(self):
        d = self.h.dateien_liste("", "", self.k)
        self.assertEqual([e["name"] for e in d["eintraege"]], ["Downloads", "Dokumente"])
        self.assertTrue(all(e["ordner"] for e in d["eintraege"]))
        self.assertEqual(d["pfad"], "")

    def test_ordnerinhalt_ordner_zuerst_ohne_versteckte(self):
        (self.dl / "Bilder").mkdir()
        (self.dl / ".geheim").write_text("x")
        (self.dl / "desktop.ini").write_text("x")
        d = self.h.dateien_liste("Downloads", "", self.k)
        self.assertEqual([e["name"] for e in d["eintraege"]], ["Bilder", "Rechnung.pdf", "setup.exe"])
        pdf = d["eintraege"][1]
        self.assertEqual((pdf["pfad"], pdf["ordner"], pdf["groesse"]), ("Downloads/Rechnung.pdf", False, 10))
        self.assertTrue(pdf["geaendert"])
        self.assertEqual(d["oben"], "")                     # eine Ebene höher: die Liste der Ordner

    def test_unterordner_kennt_seinen_vorgaenger(self):
        (self.dl / "a").mkdir()
        (self.dl / "a" / "b").mkdir()
        self.assertEqual(self.h.dateien_liste("Downloads/a/b", "", self.k)["oben"], "Downloads/a")

    def test_suche_in_allen_freigegebenen_ordnern(self):
        (self.doku / "Rechnung 2.txt").write_text("x")
        d = self.h.dateien_liste("", "rechnung", self.k)
        self.assertEqual(sorted(e["pfad"] for e in d["eintraege"]), ["Dokumente/Rechnung 2.txt", "Downloads/Rechnung.pdf"])

    def test_kein_ausbruch(self):
        os.symlink(self.geheim, self.dl / "abkuerzung")
        for boese in ("..", "Downloads/..", "Downloads/../geheim", "C:/", "C:\\Windows", "/etc", "geheim",
                      "Downloads/abkuerzung", "Downloads/Rechnung.pdf:strom"):
            with self.subTest(boese), self.assertRaises(self.h.Abgelehnt):
                self.h.dateien_liste(boese, "", self.k)

    def test_eine_datei_ist_kein_ordner(self):
        with self.assertRaises(self.h.Abgelehnt):
            self.h.dateien_liste("Downloads/Rechnung.pdf", "", self.k)

    def test_begrenzt(self):
        for i in range(5):
            (self.dl / f"f{i}.txt").write_text("x")
        with mock.patch.object(self.h, "DATEIEN_MAX", 3):
            d = self.h.dateien_liste("Downloads", "", self.k)
        self.assertEqual(len(d["eintraege"]), 3)
        self.assertTrue(d["mehr"])

    def test_fenster_alle(self):
        liste = [{"hwnd": 1, "titel": "T" * 300, "programm": "chrome.exe", "minimiert": False},
                 {"hwnd": 2, "titel": "Notiz", "programm": "notepad.exe", "minimiert": True}] * 40
        with mock.patch.object(self.h, "fenster_liste", return_value=liste):
            f = self.h.fenster_alle()
        self.assertEqual(len(f), self.h.FENSTER_ALLE_MAX)
        self.assertLessEqual(len(f[0]["titel"]), 120)
        self.assertEqual((f[1]["programm"], f[1]["minimiert"]), ("notepad", True))
        self.assertNotIn("hwnd", f[0])

    def test_programme(self):
        apps = [{"name": "Notepad", "id": "a"}, {"name": "Notepad++", "id": "b"}, {"name": "Rechner", "id": "c"}]
        with mock.patch.object(self.h, "startmenue", return_value=apps):
            self.assertEqual([p["name"] for p in self.h.programme_liste("note")], ["Notepad", "Notepad++"])
            self.assertEqual([p["name"] for p in self.h.programme_liste("")], ["Notepad", "Notepad++", "Rechner"])
            self.assertEqual(self.h.programme_liste("gibtesnicht"), [])

    def test_prozesse(self):
        roh = json.dumps([{"Name": "chrome", "Id": 10, "WS": 500 * 1024 ** 2}, {"Name": "lsass", "Id": 4, "WS": 20 * 1024 ** 2},
                          {"Name": "code", "Id": 11, "WS": 900 * 1024 ** 2}])
        with mock.patch.multiple(self.h, WINDOWS=True, _powershell=mock.Mock(return_value=roh)):
            p = self.h.prozesse_liste()
        self.assertEqual([x["name"] for x in p], ["code", "chrome", "lsass"])      # nach Speicher
        self.assertEqual(p[0]["ram_mb"], 900)
        self.assertEqual([x["geschuetzt"] for x in p], [False, False, True])

    def test_prozesse_ein_einzelner_ist_kein_array(self):
        roh = json.dumps({"Name": "chrome", "Id": 10, "WS": 1024 ** 2})           # PowerShell macht aus einem Eintrag ein Objekt
        with mock.patch.multiple(self.h, WINDOWS=True, _powershell=mock.Mock(return_value=roh)):
            self.assertEqual(len(self.h.prozesse_liste()), 1)

    def test_prozesse_ohne_windows_leer(self):
        with mock.patch.object(self.h, "WINDOWS", False):
            self.assertEqual(self.h.prozesse_liste(), [])

    def test_zwischenablage(self):
        with mock.patch.multiple(self.h, WINDOWS=True, _powershell=mock.Mock(return_value="Hallo\r\n")):
            self.assertEqual(self.h.zwischenablage_lesen(), {"text": "Hallo", "gekuerzt": False})
        with mock.patch.multiple(self.h, WINDOWS=True, _powershell=mock.Mock(return_value="x" * 9000)):
            z = self.h.zwischenablage_lesen()
        self.assertEqual((len(z["text"]), z["gekuerzt"]), (self.h.ZWISCHENABLAGE_MAX, True))

    def test_messwerte_haben_immer_dieselben_felder(self):
        m = self.h.messwerte(self.k)
        for feld in ("cpu_prozent", "ram", "laufwerke", "akku", "netz"):
            self.assertIn(feld, m)
        self.assertIsInstance(m["laufwerke"], list)


class ProzesseUndZwischenablage(HelferBasis):
    def _windows(self, ausgabe="", name="chrome"):
        return mock.patch.multiple(self.h, WINDOWS=True, _powershell=mock.Mock(return_value=ausgabe),
                                   _prozessname=mock.Mock(return_value=name))

    def test_beenden_ist_bestaetigen_und_nennt_name_und_nummer(self):
        with self._windows():
            probe = self.tu("prozess_beenden", {"pid": 4711, "name": "chrome"}, probe=True)
        self.assertTrue(probe["ok"], probe)
        self.assertEqual(probe["risiko"], "bestaetigen")
        self.assertIn("chrome", probe["beschreibung"])
        self.assertIn("4711", probe["beschreibung"])

    def test_beenden_nur_mit_bestaetigung_und_passendem_fingerabdruck(self):
        with self._windows() as m:
            probe = self.tu("prozess_beenden", {"pid": 4711, "name": "chrome"}, probe=True)
            self.assertFalse(self.tu("prozess_beenden", {"pid": 4711, "name": "chrome"})["ok"])
            self.assertFalse(self.tu("prozess_beenden", {"pid": 4711, "name": "chrome"}, bestaetigt=True, erwartet="anders")["ok"])
            self.h._powershell.assert_not_called()
            r = self.tu("prozess_beenden", {"pid": 4711, "name": "chrome"}, bestaetigt=True, erwartet=probe["fingerabdruck"])
            self.assertTrue(r["ok"], r)
            self.assertIn("4711", self.h._powershell.call_args.args[0])

    def test_beenden_verweigert_systemprozesse_den_helfer_und_falsche_namen(self):
        faelle = [({"pid": 4, "name": "System"}, "System"), ({"pid": 5, "name": "lsass"}, "lsass"),
                  ({"pid": 6, "name": "pythonw"}, "pythonw"), ({"pid": os.getpid(), "name": "irgendwas"}, "irgendwas"),
                  ({"pid": 4711, "name": "chrome"}, "notepad"),            # unter der Nummer läuft etwas anderes
                  ({"pid": "abc", "name": "chrome"}, "chrome"), ({"pid": -1, "name": "chrome"}, "chrome"),
                  ({"pid": 4711, "name": ""}, "chrome"), ({}, "chrome")]
        for angaben, laeuft in faelle:
            with self.subTest(angaben), self._windows(name=laeuft):
                r = self.tu("prozess_beenden", angaben, probe=True)
                self.assertFalse(r["ok"], r)
                self.assertEqual(r["risiko"], "verboten")

    def test_beenden_wenn_der_prozess_weg_ist(self):
        with self._windows(name=""):
            r = self.tu("prozess_beenden", {"pid": 4711, "name": "chrome"}, probe=True)
        self.assertFalse(r["ok"])
        self.assertIn("läuft nicht mehr", r["text"])

    def test_zwischenablage_setzen_ohne_einschleusen(self):
        boese = "x'; Start-Process calc; '"
        with self._windows() as m:
            probe = self.tu("zwischenablage", {"text": boese}, probe=True)
            self.assertEqual(probe["risiko"], "sicher")
            r = self.tu("zwischenablage", {"text": boese})
            befehl = self.h._powershell.call_args.args[0]
        self.assertTrue(r["ok"], r)
        self.assertNotIn("Start-Process", befehl)             # der Text reist als Base64, nie als Befehl
        self.assertIn("Set-Clipboard", befehl)

    def test_zwischenablage_grenzen(self):
        for text in ("", "   ", "x" * 20001):
            with self.subTest(len(text)), self._windows():
                self.assertFalse(self.tu("zwischenablage", {"text": text}, probe=True)["ok"])

    def test_beide_aktionen_stehen_im_status(self):
        self.assertIn("prozess_beenden", self.h.PLAENE)
        self.assertIn("zwischenablage", self.h.PLAENE)


class Medien(HelferBasis):
    """Die Medien-Sitzung von Windows (YouTube im Browser, VLC, …) — PowerShell wird ersetzt, Windows nicht nötig."""

    ROH = json.dumps({"app": "Chrome", "titel": "Ein Video", "kuenstler": "Ein Kanal", "status": "Playing",
                      "position": 61.5, "dauer": 300.0, "bild": ""})

    def _windows(self, ausgabe):
        return mock.patch.multiple(self.h, WINDOWS=True, _powershell=mock.Mock(return_value=ausgabe))

    def test_lesen(self):
        with self._windows(self.ROH):
            m = self.h.medien_lesen()
        self.assertEqual((m["app"], m["titel"], m["kuenstler"], m["laeuft"]), ("Chrome", "Ein Video", "Ein Kanal", True))
        self.assertEqual((m["position_s"], m["dauer_s"]), (61, 300))
        self.assertEqual(len(m["bild_id"]), 16)
        self.assertEqual(m["bild"], "")

    def test_pausiert_und_gleiche_kennung_fuer_gleiches_stueck(self):
        with self._windows(self.ROH.replace("Playing", "Paused")):
            a = self.h.medien_lesen()
        with self._windows(self.ROH):
            b = self.h.medien_lesen()
        self.assertFalse(a["laeuft"])
        self.assertEqual(a["bild_id"], b["bild_id"])

    def test_app_namen(self):
        for roh, name in (("Chrome", "Chrome"), ("MSEdge", "Edge"), ("firefox.exe", "Firefox"), ("vlc.exe", "VLC"),
                          ("Spotify.exe", "Spotify"), ("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "Spotify"),
                          ("Irgendwas.exe", "Irgendwas")):
            with self.subTest(roh):
                self.assertEqual(self.h.app_name(roh), name)

    def test_keine_sitzung_oder_kaputte_ausgabe_ist_none(self):
        for ausgabe in ("{}", "", "kein json", "[1]"):
            with self.subTest(ausgabe), self._windows(ausgabe):
                self.assertIsNone(self.h.medien_lesen())

    def test_ohne_windows_nichts(self):
        with mock.patch.object(self.h, "WINDOWS", False):
            self.assertIsNone(self.h.medien_lesen())

    def test_bild_nur_auf_wunsch(self):
        with self._windows(self.ROH):
            self.h.medien_lesen()
            self.h.medien_lesen(mit_bild=True)
            skripte = [c.args[0] for c in self.h._powershell.call_args_list]
        self.assertIn("$mitBild = $false", skripte[0])
        self.assertIn("$mitBild = $true", skripte[1])

    def test_steuern_ist_sicher_und_nutzt_die_sitzung(self):
        for befehl, methode in (("pause", "TryPauseAsync"), ("weiter", "TryPlayAsync"),
                                ("naechster", "TrySkipNextAsync"), ("vorheriger", "TrySkipPreviousAsync")):
            with self.subTest(befehl), self._windows("ok"):
                probe = self.tu("medien", {"befehl": befehl}, probe=True)
                self.assertEqual(probe["risiko"], "sicher")
                r = self.tu("medien", {"befehl": befehl})
                self.assertTrue(r["ok"], r)
                self.assertIn(methode, self.h._powershell.call_args.args[0])

    def test_steuern_ohne_sitzung_sagt_es(self):
        with self._windows("keine"):
            r = self.tu("medien", {"befehl": "pause"})
        self.assertFalse(r["ok"])
        self.assertIn("nichts", r["text"].lower())

    def test_unbekannter_befehl_wird_abgelehnt(self):
        for befehl in ("lauter", "", "pause; calc", None):
            with self.subTest(befehl):
                r = self.tu("medien", {"befehl": befehl})
                self.assertFalse(r["ok"])
                self.assertEqual(r["risiko"], "verboten")


class Tuer(HelferBasis):
    def test_schluessel_und_absender(self):
        t = self.h.Tuer()
        self.assertTrue(t.herein("127.0.0.1", f"Bearer {self.token}", self.k))
        self.assertFalse(t.herein("127.0.0.1", "Bearer falsch", self.k))
        self.assertFalse(t.herein("192.168.178.51", f"Bearer {self.token}", self.k))   # richtiger Schlüssel, falscher Absender
        self.assertFalse(t.herein("127.0.0.1", "", self.k))
        self.assertFalse(t.herein("127.0.0.1", "Bearer ", {**self.k, "token": ""}))    # ohne Schlüssel nie

    def test_nach_vielen_fehlversuchen_zu(self):
        t = self.h.Tuer()
        for _ in range(20):
            t.herein("127.0.0.1", "Bearer falsch", self.k)
        self.assertFalse(t.herein("127.0.0.1", f"Bearer {self.token}", self.k))


class FridayMitHelfer(HelferBasis):
    """Friday ↔ echter Helfer über HTTP: Werkzeug → Rückfrage → Eikes Tipp → ausgeführt."""

    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        with mock.patch.dict(os.environ, {"FRIDAY_PC_DATEN": self.daten}):
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.h.Handler)
        self.faden = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.faden.start()
        # Der Helfer liest seine Einstellungen bei jeder Anfrage — aus seinem Datenordner.
        self.h.DATEN = Path(self.daten)
        konnektoren.setzen("pc", True)
        konnektoren.schreiben_setzen("pc", True)
        konnektoren.zugang_setzen("pc", f"127.0.0.1:{self.server.server_address[1]}", self.token)
        pcdienst._offen.clear()
        self.app = TestClient(__import__("kern.main", fromlist=["app"]).app)

    def tearDown(self):
        self.server.shutdown()
        self.env.stop()

    def test_heikles_wartet_auf_den_tipp_in_der_app(self):
        aktionen = []
        # Löschen bleibt heikel (Verschieben/Umbenennen/Fenster schließen seit 07.10.2026 nicht mehr).
        text = _run(katalog.ausfuehren("pc_loeschen", {"pfad": "Downloads/Rechnung.pdf",
                                                       "bestaetigt": True, "zustimmung": True},
                                       zustimmung=True, aktionen=aktionen))["ergebnis"]
        self.assertIn("NOCH NICHT", text)
        self.assertTrue((self.dl / "Rechnung.pdf").exists(), "ohne Tipp darf nichts passieren — auch nicht mit Ja")
        self.assertEqual(aktionen[0]["typ"], "pc")
        offen = self.app.get("/api/pc/offen").json()
        self.assertEqual(len(offen["offen"]), 1)
        self.assertNotIn("fingerabdruck", offen["offen"][0])
        rid = offen["offen"][0]["id"]
        r = self.app.post(f"/api/pc/offen/{rid}", json={"ja": True}).json()
        self.assertTrue(r["ok"], r)
        self.assertFalse((self.dl / "Rechnung.pdf").exists())
        # genau einmal
        self.assertFalse(self.app.post(f"/api/pc/offen/{rid}", json={"ja": True}).json()["ok"])
        self.assertEqual(pcdienst.verlauf(1)[0]["wie"], "bestaetigt")

    def test_ablehnen_und_fremde_herkunft(self):
        _run(katalog.ausfuehren("pc_loeschen", {"pfad": "Downloads/Rechnung.pdf"}, aktionen=[]))
        rid = pcdienst.offene()[0]["id"]
        r = self.app.post(f"/api/pc/offen/{rid}", json={"ja": True}, headers={"Origin": "https://boese.example"})
        self.assertEqual(r.status_code, 403)
        self.assertTrue((self.dl / "Rechnung.pdf").exists())
        self.assertTrue(self.app.post(f"/api/pc/offen/{rid}", json={"ja": False}).json()["ok"])
        self.assertTrue((self.dl / "Rechnung.pdf").exists())
        self.assertEqual(pcdienst.offene(), [])

    def test_schreibrecht_weg_dann_gilt_auch_der_tipp_nicht(self):
        _run(katalog.ausfuehren("pc_loeschen", {"pfad": "Downloads/Rechnung.pdf"}, aktionen=[]))
        rid = pcdienst.offene()[0]["id"]
        konnektoren.schreiben_setzen("pc", False)
        self.assertFalse(self.app.post(f"/api/pc/offen/{rid}", json={"ja": True}).json()["ok"])
        self.assertTrue((self.dl / "Rechnung.pdf").exists())

    def test_sicheres_laeuft_direkt(self):
        text = _run(katalog.ausfuehren("pc_ordner_anlegen", {"pfad": "Dokumente/Neu"}))["ergebnis"]
        self.assertIn("angelegt", text)
        self.assertTrue((self.doku / "Neu").is_dir())
        self.assertEqual(pcdienst.offene(), [])

    def test_herunterfahren_knopf_legt_rueckfrage_an(self):
        # Der echte shutdown-Befehl ist Windows-spezifisch (siehe Moduldoku oben) — hier nur
        # geprüft, dass der Knopf die Rückfrage anlegt, nicht dass der Testhelfer ihn ausführt.
        r = self.app.post("/api/pc/herunterfahren").json()
        self.assertTrue(r["ok"], r)
        offen = self.app.get("/api/pc/offen").json()["offen"]
        self.assertEqual(len(offen), 1)
        self.assertIn("herunterfahren", offen[0]["beschreibung"].lower())

    def test_energiesparen_legt_rueckfrage_an_und_fuehrt_nichts_aus(self):
        r = self.app.post("/api/pc/energiesparen").json()
        self.assertTrue(r["ok"], r)
        offen = self.app.get("/api/pc/offen").json()["offen"]
        self.assertTrue(any("energiespar" in o["beschreibung"].lower() for o in offen), offen)

    def test_lesen_und_status(self):
        self.assertIn("Downloads/Rechnung.pdf", _run(katalog.ausfuehren("pc_dateien", {"pfad": "Downloads"}))["ergebnis"])
        self.assertTrue(katalog.liefert_fremdes("pc_dateien"))
        st = self.app.get("/api/pc/status").json()
        self.assertTrue(st["ok"], st)
        self.assertIn("Downloads", st["ordner"])
        self.assertEqual(st["helfer_neu"], "")           # Testhelfer und mitgebrachter Helfer sind dieselbe Datei

    def test_status_hat_laufzeit_und_fenster(self):
        st = self.app.get("/api/pc/status").json()
        self.assertIn("laufzeit_s", st)
        self.assertIsInstance(st["fenster"], list)

    def test_medien_ohne_windows_und_bei_altem_helfer(self):
        self.assertIsNone(_run(pcdienst.medien()))                 # der Testhelfer läuft unter Linux: keine Sitzung
        self.assertIsNone(_run(pcdienst.medien(mit_bild=True)))

    def test_helfer_version_kommt_aus_der_datei(self):
        self.assertEqual(pcdienst.helfer_version(), self.h.VERSION)

    def test_seite_liest_dateien_strukturiert(self):
        d = self.app.get("/api/pc/lesen/dateien", params={"pfad": "Downloads"}).json()
        self.assertTrue(d["ok"], d)
        self.assertIn("Downloads/Rechnung.pdf", [e["pfad"] for e in d["eintraege"]])
        wurzeln = self.app.get("/api/pc/lesen/dateien").json()
        self.assertEqual([e["name"] for e in wurzeln["eintraege"]], ["Downloads", "Dokumente"])

    def test_seite_kein_ausbruch_und_kein_unbekanntes(self):
        r = self.app.get("/api/pc/lesen/dateien", params={"pfad": "Downloads/../geheim"}).json()
        self.assertFalse(r["ok"])
        self.assertEqual(self.app.get("/api/pc/lesen/passwoerter").status_code, 404)

    def test_seite_liest_messwerte_und_fenster(self):
        m = self.app.get("/api/pc/lesen/messwerte").json()
        self.assertTrue(m["ok"], m)
        self.assertIn("laufwerke", m)
        self.assertTrue(self.app.get("/api/pc/lesen/fenster").json()["ok"])

    def test_lesen_braucht_die_freigabe(self):
        konnektoren.setzen("pc", False)
        self.assertEqual(self.app.get("/api/pc/lesen/messwerte").status_code, 403)

    def test_tun_sicheres_laeuft_sofort(self):
        r = self.app.post("/api/pc/tun", json={"art": "ordner_anlegen", "angaben": {"pfad": "Dokumente/NeuVonDerSeite"}}).json()
        self.assertTrue(r["ok"], r)
        self.assertTrue((self.doku / "NeuVonDerSeite").is_dir())
        self.assertEqual(pcdienst.verlauf(1)[0]["art"], "ordner_anlegen")

    def test_tun_heikles_wird_zur_rueckfrage_und_nichts_passiert(self):
        r = self.app.post("/api/pc/tun", json={"art": "loeschen", "angaben": {"pfad": "Downloads/Rechnung.pdf"}}).json()
        self.assertTrue(r["ok"] and r["rueckfrage"], r)
        self.assertTrue((self.dl / "Rechnung.pdf").exists())
        offen = self.app.get("/api/pc/offen").json()["offen"]
        self.assertEqual(len(offen), 1)
        self.assertIn("Rechnung.pdf", offen[0]["beschreibung"])

    def test_tun_verbotenes_wird_gemeldet(self):
        r = self.app.post("/api/pc/tun", json={"art": "verschieben", "angaben": {"von": "Downloads/../geheim/passwoerter.txt",
                                                                                  "nach": "Dokumente"}}).json()
        self.assertFalse(r["ok"])
        self.assertTrue((self.geheim / "passwoerter.txt").exists())

    def test_tun_nur_erlaubte_arten(self):
        for art in ("browser_oeffnen", "status", "dateien", "aktualisieren", "", "system; calc"):
            with self.subTest(art):
                self.assertEqual(self.app.post("/api/pc/tun", json={"art": art, "angaben": {}}).status_code, 400)

    def test_tun_braucht_schreibfreigabe_und_zu_grosse_angaben_gehen_nicht(self):
        konnektoren.schreiben_setzen("pc", False)
        self.assertEqual(self.app.post("/api/pc/tun", json={"art": "meldung", "angaben": {"text": "x"}}).status_code, 403)
        konnektoren.schreiben_setzen("pc", True)
        self.assertEqual(self.app.post("/api/pc/tun", json={"art": "zwischenablage", "angaben": {"text": "x" * 40000}}).status_code, 400)

    def test_falscher_schluessel(self):
        konnektoren.zugang_setzen("pc", f"127.0.0.1:{self.server.server_address[1]}", "falsch")
        self.assertIn("lehnt Friday ab", _run(katalog.ausfuehren("pc_dateien", {}))["ergebnis"])


class FridaySeite(unittest.TestCase):
    """Friday allein, der Helfer nachgebildet."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        konnektoren.setzen("pc", True)
        konnektoren.zugang_setzen("pc", "127.0.0.1:1", "s")
        pcdienst._offen.clear()
        self.aufrufe = []

        async def aktion(art, angaben=None, probe=False, bestaetigt=False, erwartet=None):
            self.aufrufe.append({"art": art, "probe": probe, "bestaetigt": bestaetigt})
            return {"ok": True, "risiko": "sicher", "beschreibung": f"{art} tun", "fingerabdruck": "fp", "text": "ok"}
        self.patch = mock.patch.object(pcdienst, "aktion", aktion)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.env.stop()

    def _namen(self):
        return {f["name"] for f in katalog.gemini_tools()[0]["functionDeclarations"]}

    def test_ohne_schreibrecht_nur_lesen(self):
        namen = self._namen()
        self.assertIn("pc_dateien", namen)
        self.assertNotIn("pc_verschieben", namen)
        self.assertNotIn("browser_klicken", namen)
        self.assertIn("Schreibrecht", _run(katalog.ausfuehren("pc_starten", {"programm": "Word"}))["fehler"])
        konnektoren.schreiben_setzen("pc", True)
        self.assertTrue({"pc_verschieben", "browser_klicken", "pc_system"} <= self._namen())

    def test_nach_fremdem_text_wird_hinaustragendes_zur_rueckfrage(self):
        konnektoren.schreiben_setzen("pc", True)
        # Eine Adresse nach fremdem Text: könnte Daten abfließen lassen → Rückfrage
        text = _run(katalog.ausfuehren("browser_oeffnen", {"adresse": "https://x.example/?d=geheim"},
                                       fremde_inhalte=True, aktionen=[]))["ergebnis"]
        self.assertIn("NOCH NICHT", text)
        self.assertTrue(pcdienst.offene()[0]["fremd"])
        self.assertFalse(any(not a["probe"] for a in self.aufrufe), "nur geprüft, nichts ausgeführt")
        # Ein Programm starten trägt nichts hinaus → läuft auch dann direkt
        self.aufrufe.clear()
        _run(katalog.ausfuehren("pc_starten", {"programm": "Rechner"}, fremde_inhalte=True, aktionen=[]))
        self.assertTrue(any(not a["probe"] and not a["bestaetigt"] for a in self.aufrufe))

    def test_programm_oeffnen_fragt_nur_nach_fremdem_text(self):
        konnektoren.schreiben_setzen("pc", True)
        text = _run(katalog.ausfuehren("pc_datei_oeffnen", {"pfad": "Downloads/setup.exe"},
                                       fremde_inhalte=True, aktionen=[]))["ergebnis"]
        self.assertIn("NOCH NICHT", text)
        self.assertFalse(any(not a["probe"] for a in self.aufrufe), "nur geprüft, nichts ausgeführt")
        # Ein PDF ist kein Programm → auch nach fremdem Text direkt
        self.aufrufe.clear()
        _run(katalog.ausfuehren("pc_datei_oeffnen", {"pfad": "Downloads/Rechnung.pdf"},
                                fremde_inhalte=True, aktionen=[]))
        self.assertTrue(any(not a["probe"] and not a["bestaetigt"] for a in self.aufrufe))
        # Ohne fremden Text läuft auch das Programm direkt
        self.aufrufe.clear()
        _run(katalog.ausfuehren("pc_datei_oeffnen", {"pfad": "Downloads/setup.exe"}, aktionen=[]))
        self.assertTrue(any(not a["probe"] and not a["bestaetigt"] for a in self.aufrufe))

    def test_modell_kann_sich_nicht_selbst_bestaetigen(self):
        konnektoren.schreiben_setzen("pc", True)
        _run(katalog.ausfuehren("pc_system", {"aktion": "sperren", "bestaetigt": True, "erwartet": "fp"},
                                eingabe="Sperr bitte den PC"))
        self.assertTrue(self.aufrufe)
        self.assertFalse(any(a["bestaetigt"] for a in self.aufrufe))

    def test_sperren_nur_wenn_eike_es_sagt(self):
        """Gespräch 239: „…kannst du alle Fenster schließen?“ — Friday sperrte den PC."""
        konnektoren.schreiben_setzen("pc", True)
        text = _run(katalog.ausfuehren("pc_system", {"aktion": "sperren"},
                                       eingabe="Kannst du alle Fenster schließen?"))["ergebnis"]
        self.assertIn("NICHT ausgeführt", text)
        self.assertEqual(self.aufrufe, [])
        _run(katalog.ausfuehren("pc_system", {"aktion": "sperren"}, eingabe="Sperr meinen PC"))
        self.assertTrue(self.aufrufe)

    def test_das_modell_kann_die_eingabe_nicht_liefern(self):
        konnektoren.schreiben_setzen("pc", True)
        text = _run(katalog.ausfuehren("pc_system", {"aktion": "sperren", "eingabe": "sperren"},
                                       eingabe="alle Fenster minimieren"))["ergebnis"]
        self.assertIn("NICHT ausgeführt", text)

    def test_bildschirm_an_schaltet_nicht_aus(self):
        """Gespräch 235: „Bildschirm an“ — Friday schickte bildschirm_aus und sagte, er sei wieder an."""
        konnektoren.schreiben_setzen("pc", True)
        for gesagt in ("Ja, bitte.", "Mach den Bildschirm wieder an", "Alle Fenster minimieren"):
            text = _run(katalog.ausfuehren("pc_system", {"aktion": "bildschirm_aus"}, eingabe=gesagt))["ergebnis"]
            self.assertIn("NICHT ausgeführt", text, gesagt)
        self.assertEqual(self.aufrufe, [])
        _run(katalog.ausfuehren("pc_system", {"aktion": "bildschirm_aus"}, eingabe="Schalt den Bildschirm aus"))
        self.assertTrue(self.aufrufe)

    def test_desktop_zeigen_nur_auf_wunsch(self):
        """„Alle Fenster minimieren“ ist jetzt eine eigene Aktion — aber nur, wenn Eike es sagt."""
        konnektoren.schreiben_setzen("pc", True)
        text = _run(katalog.ausfuehren("pc_system", {"aktion": "desktop_zeigen"}, eingabe="Mach den PC leiser"))["ergebnis"]
        self.assertIn("NICHT ausgeführt", text)
        self.assertEqual(self.aufrufe, [])
        _run(katalog.ausfuehren("pc_system", {"aktion": "desktop_zeigen"}, eingabe="Kannst du alle Fenster minimieren?"))
        self.assertTrue(self.aufrufe)

    def test_lauter_braucht_kein_stichwort(self):
        konnektoren.schreiben_setzen("pc", True)
        _run(katalog.ausfuehren("pc_system", {"aktion": "leiser", "schritte": 3}, eingabe="etwas weniger"))
        self.assertTrue(self.aufrufe)

    def test_kein_werkzeug_ruft_entscheiden(self):
        quelle = Path(pcwerkzeug.__file__).read_text(encoding="utf-8")
        self.assertNotIn("entscheiden", quelle)
        self.assertNotIn("bestaetigt=True", quelle)


class Einrichten(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "PC_FRIDAY_IP": "192.0.2.10"})
        self.env.start()
        self.app = TestClient(__import__("kern.main", fromlist=["app"]).app)

    def tearDown(self):
        self.env.stop()

    def test_befehl_code_einmal_und_schluessel_bleibt(self):
        r = self.app.post("/api/pc/einrichten", json={"adresse": "192.0.2.11:8765"}).json()
        self.assertIn("irm '", r["befehl"])
        code = r["befehl"].split("/api/pc/installieren/")[1].split("'")[0]
        schluessel = konnektoren.zugang("pc")[1]
        skript = self.app.get(f"/api/pc/installieren/{code}")
        self.assertEqual(skript.status_code, 200)
        self.assertIn(schluessel, skript.text)
        self.assertIn("192.0.2.10,127.0.0.1", skript.text)
        self.assertNotRegex(skript.text, r"__[A-Z]+__")
        self.assertTrue(skript.text.isascii(), "PowerShell 5.1 liest Umlaute falsch")
        self.assertEqual(self.app.get(f"/api/pc/installieren/{code}").status_code, 404)   # nur einmal
        # erneut einrichten behält den Schlüssel, „neu“ ersetzt ihn
        self.app.post("/api/pc/einrichten", json={"adresse": "192.0.2.11:8765"})
        self.assertEqual(konnektoren.zugang("pc")[1], schluessel)
        self.app.post("/api/pc/einrichten", json={"adresse": "192.0.2.11:8765", "neu": True})
        self.assertNotEqual(konnektoren.zugang("pc")[1], schluessel)

    def test_der_ausgelieferte_helfer_ist_der_aus_dem_repo(self):
        import base64
        import re
        code = self.app.post("/api/pc/einrichten", json={"adresse": "1.2.3.4"}).json()["befehl"] \
            .split("/api/pc/installieren/")[1].split("'")[0]
        skript = self.app.get(f"/api/pc/installieren/{code}").text
        b64 = re.search(r"FromBase64String\('([A-Za-z0-9+/=]+)'\)", skript).group(1)
        self.assertEqual(base64.b64decode(b64), HELFER_DATEI.read_bytes())

    def test_fremde_herkunft_kann_nicht_einrichten(self):
        r = self.app.post("/api/pc/einrichten", json={"adresse": "1.2.3.4"}, headers={"Origin": "https://boese.example"})
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(konnektoren.zugang("pc"))

    def test_schnellknoepfe_nur_sicheres(self):
        konnektoren.setzen("pc", True)
        konnektoren.schreiben_setzen("pc", True)
        konnektoren.zugang_setzen("pc", "127.0.0.1:1", "s")
        self.assertEqual(self.app.post("/api/pc/schnell", json={"aktion": "herunterfahren"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
