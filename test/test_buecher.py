"""Schulbücher und das Bearbeiten von Hausaufgaben — ohne Netz, ohne Gemini.

Das Test-PDF entsteht hier mit Pillow: Scan 1 eine Buchseite (hochkant),
Scan 2 eine Doppelseite (quer, links weiß, rechts schwarz) — wie Eikes
Handy-Scans vom 16.09.2026.
"""

import asyncio
import io
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from PIL import Image

from kern import konnektoren
from kern.dienste import schulmanager as dienst
from kern.main import app
from kern.schule import buecher, hausaufgaben as ha, ki, loesen

MITTWOCH = datetime(2026, 9, 16, 15, 0, tzinfo=ZoneInfo("Europe/Berlin"))


def test_pdf() -> bytes:
    einzel = Image.new("RGB", (600, 800), "white")
    doppel = Image.new("RGB", (1200, 800), "white")
    doppel.paste((0, 0, 0), (600, 0, 1200, 800))
    puffer = io.BytesIO()
    einzel.save(puffer, "PDF", save_all=True, append_images=[doppel])
    return puffer.getvalue()


def index_antwort(teile, *_, **__):
    """Wie Gemini: Scan 1 = S. 20, Scan 2 = S. 22 (rechts erkannt) und links ohne Zahl."""
    return {"scans": [
        {"nr": 1, "teile": [{"position": "ganz", "seite": 20, "inhalt": "Sturm und Drang, Infokasten"}]},
        {"nr": 2, "teile": [{"position": "rechts", "seite": 22, "inhalt": "Übungen, Aufgabe 1-3"},
                            {"position": "links", "seite": 0, "inhalt": "Gedicht"}]},
    ]}, "probe"


class Mit_Ordner(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(),
                                                "ISERV_USER": "", "ISERV_PASS": ""})
        self.env.start()
        self.uhr = mock.patch("kern.config.jetzt", return_value=MITTWOCH)
        self.uhr.start()
        ha.vergessen()

    def tearDown(self):
        self.uhr.stop()
        self.env.stop()
        ha.vergessen()

    def buch_anlegen(self, fach="Deutsch") -> int:
        a = buecher.standard()
        bid = a.anlegen("Deutschbuch", fach)
        ordner = a.buch_ordner(bid)
        ordner.mkdir(parents=True)
        pfad = ordner / buecher.dateiname(1, "Scan 1.pdf")
        pfad.write_bytes(test_pdf())
        buecher.dateien_hinzufuegen(bid, [("Scan 1.pdf", pfad)])
        with mock.patch.object(ki, "json_anfrage", mock.AsyncMock(side_effect=index_antwort)):
            asyncio.run(buecher.indexieren(bid, pause=0))
        return bid


class Index(unittest.TestCase):
    def test_luecken_aus_doppelseite_und_nachbarn(self):
        e = lambda d, s, p, z: {"datei": d, "pdfseite": s, "position": p, "seite": z}
        aus = buecher.luecken_fuellen([e(0, 0, "ganz", 10), e(0, 1, "ganz", 0), e(0, 2, "links", 0),
                                       e(0, 2, "rechts", 13), e(0, 3, "ganz", 0), e(0, 4, "ganz", 20)])
        # 12 aus der Doppelseite, danach 11 zwischen 10 und 12; zwischen 13 und 20 wird nicht geraten.
        self.assertEqual([x["seite"] for x in aus], [10, 11, 12, 13, 0, 20])
        self.assertEqual([x["geschaetzt"] for x in aus], [False, True, True, False, False, False])
        aus = buecher.luecken_fuellen([e(0, 0, "ganz", 4), e(0, 1, "ganz", 0), e(0, 2, "ganz", 6)])
        self.assertEqual((aus[1]["seite"], aus[1]["geschaetzt"]), (5, True))

    def test_lage_wird_geglaettet(self):
        aus = buecher.auswerten([
            {"nr": 1, "teile": [{"position": "links", "seite": 3, "inhalt": ""}]},
            {"nr": 2, "teile": [{"position": "ganz", "seite": 4, "inhalt": ""}, {"position": "ganz", "seite": 5}]},
            {"nr": 3, "teile": [{"position": "unten", "seite": 7}, {"position": "oben", "seite": 6}]},
            {"nr": 4, "teile": [{"position": "ganz", "seite": 99999}]},
        ], [(0, 0), (0, 1), (0, 2), (0, 3)])
        self.assertEqual([(x["position"], x["seite"]) for x in aus],
                         [("ganz", 3), ("links", 4), ("rechts", 5), ("oben", 6), ("unten", 7), ("ganz", 0)])

    def test_dateien_natuerlich_sortiert(self):
        self.assertEqual(sorted(["Scan 10.pdf", "Scan 2.pdf", "Scan 1.pdf"], key=buecher.natuerlich),
                         ["Scan 1.pdf", "Scan 2.pdf", "Scan 10.pdf"])


class Buch(Mit_Ordner):
    def test_indexieren_finden_zuschneiden(self):
        bid = self.buch_anlegen()
        b = buecher.standard().buch(bid)
        self.assertEqual(b["status"], "fertig")
        u = buecher.uebersicht(b)
        self.assertEqual((u["buchseiten"], u["bereich"], u["fehlend"], u["geschaetzt"]), (3, [20, 22], [], 1))
        daten, ort = buecher.seite_bild(bid, 22)
        rechts = Image.open(io.BytesIO(daten))
        self.assertEqual((ort["pdfseite"], ort["position"]), (2, "rechts"))
        self.assertLess(rechts.width, rechts.height)                 # halbiert: hochkant
        self.assertLess(rechts.convert("L").getpixel((rechts.width - 5, 5)), 30)   # rechte Hälfte ist schwarz
        daten, ort = buecher.seite_bild(bid, 21)
        self.assertTrue(ort["geschaetzt"])
        self.assertGreater(Image.open(io.BytesIO(daten)).convert("L").getpixel((5, 5)), 220)
        self.assertIsNone(buecher.seite_bild(bid, 99))
        self.assertEqual(buecher.naechste(b, 30, 2), [21, 22])
        self.assertEqual([t["seite"] for t in buecher.suchen(bid, "Infokasten Sturm")], [20])

    def test_abbruch_beim_lesen_wird_fehler(self):
        a = buecher.standard()
        bid = a.anlegen("Kaputt", "Deutsch")
        pfad = a.buch_ordner(bid)
        pfad.mkdir(parents=True)
        (pfad / "01-x.pdf").write_bytes(test_pdf())
        buecher.dateien_hinzufuegen(bid, [("x.pdf", pfad / "01-x.pdf")])
        with mock.patch.object(ki, "json_anfrage", mock.AsyncMock(side_effect=ki.KiFehler("x: 429"))):
            asyncio.run(buecher.indexieren(bid, pause=0))
        b = a.buch(bid)
        self.assertEqual(b["status"], "fehler")
        self.assertIn("429", b["fehler"])


class EntfernenUndFortsetzen(Mit_Ordner):
    def test_entfernen_und_wiederherstellen(self):
        bid = self.buch_anlegen()
        c = TestClient(app)
        self.assertEqual([b["id"] for b in buecher.buecher_fuer("Deutsch")], [bid])
        self.assertEqual(c.post(f"/api/buecher/{bid}/entfernen").status_code, 200)
        self.assertEqual(buecher.buecher_fuer("Deutsch"), [])             # beim Bearbeiten nicht mehr dabei
        d = c.get("/api/buecher").json()
        self.assertEqual((d["buecher"], [b["id"] for b in d["entfernt"]]), ([], [bid]))
        self.assertTrue(buecher.standard().buch_ordner(bid).exists())      # Dateien bleiben
        c.post(f"/api/buecher/{bid}/wiederherstellen")
        self.assertEqual([b["id"] for b in buecher.buecher_fuer("Deutsch")], [bid])
        with mock.patch.object(buecher, "laeuft", return_value=True):
            self.assertEqual(c.post(f"/api/buecher/{bid}/entfernen").status_code, 409)
        self.assertEqual(c.post("/api/buecher/999/entfernen").status_code, 404)

    def test_loeschen_raeumt_entfernte_buecher_weg(self):
        bid = self.buch_anlegen()
        c = TestClient(app)
        ordner = buecher.standard().buch_ordner(bid)
        # Im Einsatz wird nicht gelöscht — erst entfernen.
        self.assertEqual(c.post(f"/api/buecher/{bid}/loeschen").status_code, 409)
        self.assertIsNotNone(buecher.standard().buch(bid))
        with self.assertRaises(ValueError):
            buecher.loeschen(bid)
        c.post(f"/api/buecher/{bid}/entfernen")
        self.assertEqual(c.post(f"/api/buecher/{bid}/loeschen").status_code, 200)
        self.assertIsNone(buecher.standard().buch(bid))                    # Eintrag weg
        self.assertFalse(ordner.exists())                                  # Scans weg
        self.assertEqual(c.post(f"/api/buecher/{bid}/loeschen").status_code, 404)

    def test_neustart_liest_ab_zwischenstand_weiter(self):
        a = buecher.standard()
        bid = a.anlegen("Deutschbuch", "Deutsch")
        ordner = a.buch_ordner(bid)
        ordner.mkdir(parents=True)
        pfad = ordner / buecher.dateiname(1, "Scan 1.pdf")
        pfad.write_bytes(test_pdf())
        buecher.dateien_hinzufuegen(bid, [("Scan 1.pdf", pfad)])
        # Erster Stapel gelesen, dann Neustart mitten im zweiten.
        a.setzen(bid, status="laeuft", gelesen_bis=1, fortschritt=1,
                 eintraege=[{"datei": 0, "pdfseite": 0, "position": "ganz", "seite": 20, "inhalt": "alt"}])
        buecher._instanzen.clear()
        b = buecher.standard().buch(bid)
        self.assertEqual((b["status"], b["gelesen_bis"]), ("neu", 1))
        zweiter = {"scans": [{"nr": 1, "teile": [{"position": "ganz", "seite": 21, "inhalt": "neu"}]}]}, "probe"
        with mock.patch.object(buecher, "STAPEL", 1), \
                mock.patch.object(ki, "json_anfrage", mock.AsyncMock(return_value=zweiter)) as gemini:
            asyncio.run(buecher.indexieren(bid, pause=0, fortsetzen=True))
        self.assertEqual(gemini.await_count, 1)                           # nur der fehlende Scan
        b = buecher.standard().buch(bid)
        self.assertEqual((b["status"], [(e["seite"], e["inhalt"]) for e in b["eintraege"]]),
                         ("fertig", [(20, "alt"), (21, "neu")]))


def kandidat(*aufrufe):
    return {"content": {"role": "model", "parts": [{"functionCall": {"id": f"c{i}", **x}, "thoughtSignature": "sig"}
                                                   for i, x in enumerate(aufrufe)]}}


class Bearbeiten(Mit_Ordner):
    def test_blaettern_materialien_zaehlt_der_code(self):
        bid = self.buch_anlegen()
        antworten = [
            kandidat({"name": "buchseite", "args": {"buch": bid, "seite": 21}}),
            kandidat({"name": "buchseite", "args": {"buch": bid, "seite": 22}},
                     {"name": "buchseite", "args": {"buch": 99, "seite": 1}}),
            kandidat({"name": "abgeben", "args": {
                "zusammenfassung": "Aufgaben 1-3 gelöst", "offen": "",
                "genutzte_seiten": [{"buch": bid, "seite": 22}, {"buch": bid, "seite": 50}],
                "bloecke": [{"art": "text", "titel": "Nr. 1", "inhalt": "Antwort"},
                            {"art": "diagramm", "titel": "Skizze",
                             "inhalt": "```svg\n<svg viewBox='0 0 1 1' onload=\"alert(1)\"><script>x</script>"
                                       "<image href='https://boese.example/x.png'/></svg>\n```"},
                            {"art": "bild", "titel": "Bild", "inhalt": "ein Baum"},
                            {"art": "quatsch", "titel": "", "inhalt": ""}]}}),
        ]
        gesendet = []

        async def senden(client, modell, schl, inhalt):
            gesendet.append([dict(c) for c in inhalt["contents"]])
            return antworten[len(gesendet) - 1]

        eintrag = {"id": "abc", "datum": "2026-09-16", "kurs": "de281", "fach": "Deutsch", "text": "S. 22 Nr. 1-3"}
        with mock.patch("kern.config.loesen_schluessel_liste", return_value=["k"]), \
                mock.patch.object(ki, "senden", senden), \
                mock.patch.object(ki, "bild", mock.AsyncMock(side_effect=ki.KiFehler("Bild nicht erzeugt (x: 429)"))):
            e = asyncio.run(loesen.bearbeiten(eintrag, None, Path(tempfile.mkdtemp())))
        self.assertEqual([(m["seite"], m["genutzt"]) for m in e["materialien"]], [(21, False), (22, True)])
        self.assertEqual([b["art"] for b in e["bloecke"]], ["text", "diagramm", "bild"])
        svg = e["bloecke"][1]["svg"]
        self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"))
        for boese in ("script", "onload", "boese.example"):
            self.assertNotIn(boese, svg)
        self.assertIn("429", e["bloecke"][2]["fehler"])
        # Das Seitenbild folgt direkt auf die Werkzeug-Antwort, die Modellteile gehen unverändert zurück.
        runde2 = gesendet[1]
        self.assertEqual(runde2[1]["parts"][0]["thoughtSignature"], "sig")
        teile = runde2[2]["parts"]
        self.assertIn("functionResponse", teile[0])
        self.assertEqual(teile[0]["functionResponse"]["id"], "c0")
        self.assertEqual(teile[-1]["inlineData"]["mimeType"], "image/jpeg")
        fremd = gesendet[2][4]["parts"][1]["functionResponse"]["response"]
        self.assertIn("gibt es für diese Aufgabe nicht", fremd["fehler"])

    def test_seitenlimit(self):
        bid = self.buch_anlegen()
        erlaubt = {bid: buecher.standard().buch(bid)}
        geholt = {(bid, s): {} for s in range(100, 100 + loesen.MAX_SEITEN)}
        antwort, teile = asyncio.run(loesen._seite({"buch": bid, "seite": 20}, erlaubt, geholt))
        self.assertIn("Limit", antwort["fehler"])
        self.assertEqual(teile, [])
        antwort, _ = asyncio.run(loesen._seite({"buch": bid, "seite": 30}, erlaubt, {}))
        self.assertIn("[20, 21, 22]", antwort["fehler"])

    def test_letzte_runde_erzwingt_abgabe(self):
        konfig = []

        async def senden(client, modell, schl, inhalt):
            konfig.append(inhalt["toolConfig"]["functionCallingConfig"])
            return kandidat({"name": "buch_suchen", "args": {"buch": 1, "begriff": "x"}})

        with mock.patch("kern.config.loesen_schluessel_liste", return_value=["k"]), \
                mock.patch.object(ki, "senden", senden):
            with self.assertRaises(ki.KiFehler):
                asyncio.run(loesen.bearbeiten({"id": "x", "datum": "2026-09-16", "kurs": "ma1", "fach": "Mathematik",
                                               "text": "Nr. 1"}, None, Path(tempfile.mkdtemp())))
        self.assertEqual(len(konfig), loesen.MAX_RUNDEN * len(ki.MODELLE))
        self.assertEqual(konfig[loesen.MAX_RUNDEN - 1]["allowedFunctionNames"], ["abgeben"])
        self.assertNotIn("allowedFunctionNames", konfig[0])


ROH = [{"date": "2026-09-16", "subject": "de281", "homework": "S. 22 Nr. 1-3"},
       {"date": "2026-09-16", "subject": "bi281", "homework": "Versuch hochladen"},
       {"date": "2026-09-15", "subject": "ma281", "homework": "S. 31 Nr. 10"},
       {"date": "2026-09-01", "subject": "de281", "homework": "S. 20 lesen"}]


def auswertung(teile, *_):
    machbar = {"de281": "ja", "bi281": "nein", "ma281": "ja"}
    import re
    eintraege = []
    for nr, kurs in re.findall(r"^nr (\d+): .*\(Kurs (\w+)\)", teile[0]["text"], re.M):
        m = machbar[kurs]
        eintraege.append({"nr": int(nr), "kurz": "k", "machbar": m, "grund": "", "teile": [
            {"quelle": "buch", "seiten": [1], "aufgaben": [], "was": "", "machbar": m, "grund": ""}]})
    return {"eintraege": eintraege}, "probe"


class Ablauf(Mit_Ordner):
    def setUp(self):
        super().setUp()
        self.roh = mock.patch.object(dienst, "hausaufgaben", mock.AsyncMock(return_value=ROH))
        self.roh.start()
        self.gemini = mock.patch("kern.schule.briefe._gemini", mock.AsyncMock(side_effect=auswertung))
        self.gemini.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.gemini.stop()
        self.roh.stop()
        super().tearDown()

    def ids(self):
        liste = asyncio.run(ha.liste())
        asyncio.run(ha.auswerten([e["id"] for e in liste]))
        return {e["kurs"] + e["datum"][-2:]: e["id"] for e in liste}

    def test_automatisch_nur_frisch_machbar_und_mit_buch(self):
        ids = self.ids()
        liste = asyncio.run(ha.liste())
        self.assertEqual(ha.automatisch_faellig(liste), [])            # noch kein Buch für Deutsch/Mathe
        self.buch_anlegen("Deutsch")
        self.assertEqual(ha.automatisch_faellig(liste), [ids["de28116"]])   # alt, Bio (nein), Mathe ohne Buch: nicht
        a = ha.standard()
        for _ in range(ha.AUTO_VERSUCHE):
            a.loesung_setzen(ids["de28116"], "fehler", fehler="x")
        self.assertEqual(ha.automatisch_faellig(liste), [])            # zweimal gescheitert: nur noch per Knopf

    def test_bearbeiten_legt_ergebnis_ab_und_fehler_auch(self):
        hid = self.ids()["de28116"]
        with mock.patch.object(loesen, "bearbeiten", mock.AsyncMock(return_value={
                "zusammenfassung": "z", "offen": "", "bloecke": [], "materialien": [], "modell": "probe"})):
            self.assertTrue(asyncio.run(ha.bearbeiten(hid)))
        d = self.c.get(f"/api/hausaufgaben/eintrag/{hid}").json()
        self.assertEqual((d["loesung_status"], d["loesung"]["zusammenfassung"], d["fach"]), ("fertig", "z", "Deutsch"))
        with mock.patch.object(loesen, "bearbeiten", mock.AsyncMock(side_effect=ki.KiFehler("x: 503"))):
            self.assertFalse(asyncio.run(ha.bearbeiten(hid)))
        d = self.c.get(f"/api/hausaufgaben/eintrag/{hid}").json()
        self.assertEqual(d["loesung_status"], "fehler")
        self.assertIn("503", d["loesung_fehler"])
        self.assertEqual(d["loesung"]["zusammenfassung"], "z")         # das alte Ergebnis bleibt

    def test_neustart_mitten_in_der_bearbeitung(self):
        hid = self.ids()["de28116"]
        ha.standard().loesung_setzen(hid, "laeuft")
        ha._instanzen.clear()
        self.assertEqual(ha.detail(hid)["loesung_status"], "fehler")

    def test_schnittstelle_mit_und_ohne_freigabe(self):
        hid = self.ids()["de28116"]
        self.assertEqual(self.c.get("/api/hausaufgaben/eintrag/gibtsnicht").status_code, 404)
        r = self.c.post(f"/api/hausaufgaben/eintrag/{hid}/bearbeiten")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.get(f"/api/hausaufgaben/eintrag/{hid}/bild/..%2F..%2Fschule.db").status_code, 404)
        # Hochladen geht ohne Freigabe, gelesen wird erst mit ihr.
        r = self.c.post("/api/buecher", data={"titel": "Green Line", "fach": "Englisch"},
                        files=[("dateien", ("b.pdf", b"kein pdf", "application/pdf"))])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.c.get("/api/buecher").json()["buecher"], [])   # kein leeres Buch
        r = self.c.post("/api/buecher", data={"titel": "Green Line", "fach": "Englisch"},
                        files=[("dateien", ("Scan 2.pdf", test_pdf(), "application/pdf")),
                               ("dateien", ("Scan 1.pdf", test_pdf(), "application/pdf"))])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["gestartet"])
        d = self.c.get("/api/buecher").json()
        buch = [b for b in d["buecher"] if b["titel"] == "Green Line"][0]
        self.assertEqual([x["name"] for x in buch["dateien"]], ["Scan 1.pdf", "Scan 2.pdf"])
        self.assertEqual((buch["gesamt"], buch["status"]), (4, "neu"))
        self.assertIn("Freigabe", buch["fehler"])
        self.assertEqual(self.c.post(f"/api/buecher/{buch['id']}/lesen").status_code, 403)
        self.assertEqual(self.c.get(f"/api/buecher/{buch['id']}/seite/1").status_code, 404)
        konnektoren.setzen("hausaufgaben", True)
        with mock.patch.object(ha, "bearbeitung_starten") as starten:
            self.assertEqual(self.c.post(f"/api/hausaufgaben/eintrag/{hid}/bearbeiten").status_code, 200)
        starten.assert_called_once_with(hid)


if __name__ == "__main__":
    unittest.main()
