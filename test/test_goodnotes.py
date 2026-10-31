"""Goodnotes über IServ-WebDAV — ohne Netz, ohne Gemini.

Die PROPFIND-Antwort hat den Aufbau der echten vom 16.09.2026 (sabredav,
ohne ETag). Die Test-PDFs entstehen mit Pillow, jede Seite in eigener Farbe,
damit die Fingerabdrücke sich unterscheiden.
"""

import asyncio
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from kern import konnektoren
from kern.dienste import webdav
from kern.goodnotes import abgleich, ablage
from kern.main import app
from kern.schule import ki, loesen
from kern.werkzeuge import katalog, notizen

PROPFIND = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">
<d:response><d:href>/webdav/GoodNotes/</d:href><d:propstat><d:prop>
<d:getlastmodified>Wed, 02 Sep 2026 07:32:11 GMT</d:getlastmodified><d:resourcetype><d:collection/></d:resourcetype>
</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
<d:response><d:href>/webdav/GoodNotes/.GoodnotesBackupRoot</d:href><d:propstat><d:prop>
<d:getlastmodified>Wed, 06 May 2026 15:23:34 GMT</d:getlastmodified><d:getcontentlength>29</d:getcontentlength>
<d:resourcetype/></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
<d:response><d:href>/webdav/GoodNotes/Biologie%20eA/</d:href><d:propstat><d:prop>
<d:getlastmodified>Mon, 14 Sep 2026 15:07:10 GMT</d:getlastmodified><d:resourcetype><d:collection/></d:resourcetype>
</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
<d:response><d:href>/webdav/GoodNotes/Chemie%20eA/Chemie%20%C3%9Cbung.pdf</d:href><d:propstat><d:prop>
<d:getlastmodified>Wed, 02 Sep 2026 10:00:00 GMT</d:getlastmodified><d:getcontentlength>12313</d:getcontentlength>
<d:resourcetype/></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
</d:multistatus>"""

FARBEN = ["red", "green", "blue", "yellow", "purple", "orange", "white", "black"]


def pdf(farben: list[str]) -> bytes:
    seiten = [Image.new("RGB", (300, 400), f) for f in farben]
    puffer = io.BytesIO()
    seiten[0].save(puffer, "PDF", save_all=True, append_images=seiten[1:])
    return puffer.getvalue()


class Mit_Ordner(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(),
                                                "ISERV_USER": "eike", "ISERV_PASS": "geheim"})
        self.env.start()
        webdav.vergessen()
        abgleich.zustand.update(phase="", fehler=None, ergebnis=None, letzter=None)

    def tearDown(self):
        self.env.stop()
        webdav.vergessen()

    def einschalten(self):
        konnektoren.setzen("schulmanager", True)
        konnektoren.setzen("goodnotes", True)


class WebDav(Mit_Ordner):
    def test_propfind_auslesen(self):
        e = webdav.eintraege(PROPFIND, "GoodNotes")
        self.assertEqual([x["pfad"] for x in e],
                         ["GoodNotes/.GoodnotesBackupRoot", "GoodNotes/Biologie eA", "GoodNotes/Chemie eA/Chemie Übung.pdf"])
        self.assertEqual([x["ordner"] for x in e], [False, True, False])
        self.assertEqual((e[2]["groesse"], e[2]["geaendert"]), (12313, "2026-09-02T10:00:00+00:00"))

    def test_adresse(self):
        with mock.patch.dict(os.environ, {"ISERV_URL": "https://schule.example/iserv/"}):
            self.assertEqual(webdav.pfad_url("GoodNotes/Chemie eA/Ü b.pdf"),
                             "https://schule.example/webdav/GoodNotes/Chemie%20eA/%C3%9C%20b.pdf")

    def test_abgewiesen_wird_nicht_wiederholt(self):
        anfragen = []

        def antwort(request):
            anfragen.append(request)
            return httpx.Response(401)

        echt = httpx.AsyncClient
        with mock.patch.object(webdav.httpx, "AsyncClient",
                               lambda **kw: echt(transport=httpx.MockTransport(antwort), **kw)):
            for _ in range(3):
                with self.assertRaises(webdav.AnmeldeFehler):
                    asyncio.run(webdav.auflisten("GoodNotes"))
        self.assertEqual(len(anfragen), 1)
        self.assertEqual(anfragen[0].method, "PROPFIND")
        self.assertTrue(anfragen[0].headers["authorization"].startswith("Basic "))
        webdav.vergessen()          # neue Zugangsdaten eingetragen → wieder erlaubt
        webdav._zugang()


def abschrift(teile, *_a, **_k):
    """Wie Gemini: eine Abschrift je nr, Text nennt die Heftseite."""
    import re
    kopf = [t["text"] for t in teile if "text" in t]
    seiten = []
    for k in kopf:
        nr, seite = re.match(r"nr (\d+): .* Seite (\d+)", k).groups()
        seiten.append({"nr": int(nr), "art": "mitschrift", "titel": f"Thema {seite}", "datum": "2026-09-1x",
                       "text": f"Fotosynthese Lichtreaktion Seite {seite}" if seite == "2" else f"Enzyme {seite}"})
    return {"seiten": seiten}, "probe"


class MitLauf(Mit_Ordner):
    def lauf(self, dateien: dict[str, tuple[list[str], str]], gemini=None):
        """dateien: pfad → (Farben, geändert). Gibt (Ergebnis, Gemini-Mock) zurück."""
        liste = [{"pfad": p, "ordner": False, "groesse": len(f) * 1000, "geaendert": g}
                 for p, (f, g) in dateien.items()]

        async def laden(pfad, ziel):
            ziel.parent.mkdir(parents=True, exist_ok=True)
            ziel.write_bytes(pdf(dateien[pfad][0]))
            return 1

        gemini = gemini or mock.AsyncMock(side_effect=abschrift)
        with mock.patch.object(webdav, "auflisten", mock.AsyncMock(return_value=liste)), \
                mock.patch.object(webdav, "laden", mock.AsyncMock(side_effect=laden)) as geladen, \
                mock.patch.object(ki, "json_anfrage", gemini):
            ergebnis = asyncio.run(abgleich.durchgang(pause=0))
        return ergebnis, gemini, geladen


class Abgleich(MitLauf):
    def test_nur_neues_wird_geladen_und_gelesen(self):
        dateien = {"GoodNotes/Biologie eA/Biologie.pdf": (FARBEN[:3], "2026-09-14T15:07:10+00:00"),
                   "GoodNotes/Chemie eA/Chemie.pdf": (FARBEN[3:5], "2026-09-10T00:00:00+00:00")}
        e, gemini, _ = self.lauf(dateien)
        self.assertEqual((e["dateien"], e["geladen"], e["neue_seiten"], e["gelesen"]), (2, 2, 5, 5))
        self.assertEqual(gemini.await_count, 1)                        # 5 Seiten, ein Stapel
        self.assertIn("Biologie", gemini.await_args.args[0][0]["text"])  # neuestes Heft zuerst
        a = ablage.standard()
        self.assertEqual(a.stand(), {"fertig": 5, "offen": 0, "fehlerhaft": 0, "gesamt": 5})
        self.assertEqual(a.seite(1, 2)["datum"], "")                   # kaputtes Datum verworfen

        # Unverändert: nichts laden, nichts lesen.
        e, gemini, geladen = self.lauf(dateien)
        self.assertEqual((e["geladen"], e["gelesen"]), (0, 0))
        gemini.assert_not_awaited()
        geladen.assert_not_awaited()

        # Seite vorn eingefügt: nur die neue Seite geht an Gemini, die anderen behalten ihre Abschrift.
        dateien["GoodNotes/Biologie eA/Biologie.pdf"] = (["white"] + FARBEN[:3], "2026-09-16T08:00:00+00:00")
        e, gemini, _ = self.lauf(dateien)
        self.assertEqual((e["geladen"], e["neue_seiten"], e["gelesen"]), (1, 1, 1))
        self.assertEqual(ablage.standard().seite(1, 3)["text"], "Fotosynthese Lichtreaktion Seite 2")

        # Datei verschwindet: als weg markiert, nicht gelöscht, nicht mehr gefunden.
        del dateien["GoodNotes/Chemie eA/Chemie.pdf"]
        self.lauf(dateien)
        self.assertTrue(ablage.standard().heft(2)["weg"])
        self.assertEqual(ablage.standard().suchen("Enzyme", "Chemie"), [])

    def test_kontingent_bricht_ab_und_zaehlt_nicht_als_fehler(self):
        dateien = {f"GoodNotes/Heft{i}.pdf": (FARBEN[:4], f"2026-09-0{i}T00:00:00+00:00") for i in range(1, 4)}
        gemini = mock.AsyncMock(side_effect=ki.KiFehler("Gemini hat nicht geantwortet (x: 429)"))
        e, gemini, _ = self.lauf(dateien, gemini)
        self.assertEqual(e["gelesen"], 0)
        self.assertEqual(gemini.await_count, 1)                        # nach dem ersten Nein Schluss
        self.assertIn("429", abgleich.zustand["fehler"])
        self.assertEqual(ablage.standard().stand()["offen"], 12)

    def test_ausgeschlossene_ordner_werden_nicht_gelesen(self):
        ablage.standard().ausschliessen(["Kontakt B"])
        dateien = {"GoodNotes/Kontakt B/Physik.pdf": (FARBEN[:2], "2026-09-01T00:00:00+00:00"),
                   "GoodNotes/Mathe eA/Mathe.pdf": (FARBEN[2:3], "2026-09-01T00:00:00+00:00")}
        e, gemini, _ = self.lauf(dateien)
        self.assertEqual(e["gelesen"], 1)
        self.assertNotIn("Kontakt B", gemini.await_args.args[0][0]["text"])
        # Fortschritt nur über gelesene Ordner — sonst bliebe er für immer unter 100 %.
        self.assertEqual(ablage.standard().stand(), {"fertig": 1, "offen": 0, "fehlerhaft": 0, "gesamt": 1})

    def test_suche_werkzeuge_und_seite(self):
        self.einschalten()
        self.lauf({"GoodNotes/Biologie eA/Biologie.pdf": (FARBEN[:3], "2026-09-14T15:07:10+00:00")})
        text = notizen.notizen_suchen("Was hatten wir zur Fotosynthese?")
        self.assertIn("Heft 1 „Biologie eA / Biologie“, Seite 2 (Thema 2)", text)
        self.assertIn("«Fotosynthese»", text)
        self.assertIn("nichts", notizen.notizen_suchen("Fotosynthese", fach="Chemie"))
        self.assertIn("Seite 2 von 3", notizen.notizseite(1, 2))
        self.assertIn("keine Seite 9", notizen.notizseite(1, 9))
        self.assertIn("Heft 1 „Biologie eA / Biologie“: 3 Seiten", notizen.notizhefte("Bio"))
        self.assertTrue(katalog.liefert_fremdes("notizen_suchen"))
        self.assertLess(len(abgleich.seitenbild(1, 2)), 200_000)
        ablage.standard().ausschliessen(["Biologie eA"])
        self.assertEqual(ablage.standard().suchen("Fotosynthese"), [])
        self.assertIn("nicht lesen", notizen.notizseite(1, 2))

    def test_werkzeuge_nur_mit_freigabe(self):
        namen = lambda: {e["name"] for t in katalog.gemini_tools() for e in t["functionDeclarations"]}
        self.assertNotIn("notizen_suchen", namen())
        self.einschalten()
        self.assertIn("notizen_suchen", namen())


class Bearbeiten(MitLauf):
    def test_arbeitsblatt_aus_dem_heft(self):
        self.lauf({"GoodNotes/Biologie eA/Biologie.pdf": (FARBEN[:3], "2026-09-14T15:07:10+00:00")})
        antworten = [
            {"content": {"parts": [{"functionCall": {"name": "notizen_suchen", "args": {"begriff": "Fotosynthese"}}}]}},
            {"content": {"parts": [{"functionCall": {"name": "notizseite", "args": {"heft": 1, "seite": 2}}}]}},
            {"content": {"parts": [{"functionCall": {"name": "abgeben", "args": {
                "zusammenfassung": "AB gelöst", "offen": "", "bloecke": [], "genutzte_seiten": [],
                "genutzte_notizen": [{"heft": 1, "seite": 2}]}}}]}},
        ]
        gesendet = []

        async def senden(client, modell, schl, inhalt):
            gesendet.append(inhalt)
            return antworten[len(gesendet) - 1]

        eintrag = {"id": "x", "datum": "2026-09-14", "kurs": "BI281", "fach": "Biologie", "text": "AB Fotosynthese Nr. 3"}
        with mock.patch("kern.config.loesen_schluessel_liste", return_value=["k"]), \
                mock.patch.object(ki, "senden", senden):
            ohne = [e["name"] for e in loesen.werkzeuge(False)[0]["functionDeclarations"]]
            self.assertNotIn("notizseite", ohne)
            e = asyncio.run(loesen.bearbeiten(eintrag, None, Path(tempfile.mkdtemp())))   # Freigabe aus
            self.assertIn("Unbekanntes Werkzeug", str(gesendet[0]["contents"]) + str(gesendet[1]["contents"]))
            gesendet.clear()
            self.einschalten()
            e = asyncio.run(loesen.bearbeiten(eintrag, None, Path(tempfile.mkdtemp())))
        self.assertIn("Heft 1: „Biologie eA / Biologie“", gesendet[0]["contents"][0]["parts"][0]["text"])
        suche = gesendet[1]["contents"][2]["parts"][0]["functionResponse"]["response"]["ergebnis"]
        self.assertEqual((suche[0]["heft"], suche[0]["seite"]), (1, 2))
        self.assertEqual(gesendet[2]["contents"][4]["parts"][-1]["inlineData"]["mimeType"], "image/jpeg")
        self.assertEqual(e["materialien"], [{"art": "notiz", "heft": 1, "titel": "Biologie", "seite": 2,
                                             "inhalt": "Thema 2", "bild_url": "/api/goodnotes/seite/1/2",
                                             "genutzt": True}])


class NeuesteSeiten(MitLauf):
    """`neueste_seiten()` fürs Lernen-Tab: über mehrere Hefte, neuestes Datum zuerst."""

    def _mit_daten(self, seiten_je_heft: dict[str, list[tuple[str, str]]]):
        """seiten_je_heft: pfad -> [(datum, text), ...], eine Seite je Eintrag. Der Heftname
        (ohne Endung) muss über alle Dateien eindeutig sein — die Attrappe erkennt Heft und
        Seite am Kopf jeder Anfrage (wie bei `seiten_lesen()`), egal wie Gemini-Aufrufe
        mehrere Hefte zu einem Stapel bündeln."""
        import re
        nach_name = {p.rsplit("/", 1)[-1].rsplit(".", 1)[0]: s for p, s in seiten_je_heft.items()}

        async def gemini(teile, *_a, **_k):
            antworten = []
            for kopf in (t["text"] for t in teile if "text" in t):
                m = re.match(r"nr (\d+): Heft „([^“]+)“ .*Seite (\d+)", kopf)
                nr, name, seite = m.groups()
                datum, text = nach_name[name][int(seite) - 1]
                antworten.append({"nr": int(nr), "art": "mitschrift", "titel": f"Seite {seite}",
                                  "datum": datum, "text": text})
            return {"seiten": antworten}, "probe"

        dateien = {p: (FARBEN[:len(s)], "2026-09-01T00:00:00+00:00") for p, s in seiten_je_heft.items()}
        return self.lauf(dateien, gemini=mock.AsyncMock(side_effect=gemini))

    def test_ueber_mehrere_hefte_neuestes_datum_zuerst(self):
        self._mit_daten({
            "GoodNotes/Biologie eA/Alt.pdf": [("2026-09-01", "Vor langer Zeit")],
            "GoodNotes/Biologie eA/Neu.pdf": [("2026-09-20", "Ganz frisch")],
        })
        hefte = ablage.standard().hefte()
        seiten = ablage.standard().neueste_seiten([h["id"] for h in hefte], anzahl=5)
        self.assertEqual([s["text"] for s in seiten], ["Ganz frisch", "Vor langer Zeit"])

    def test_ohne_hefte_ids_ist_leer(self):
        self.assertEqual(ablage.standard().neueste_seiten([], anzahl=5), [])

    def test_seiten_ohne_datum_stehen_hinten(self):
        self._mit_daten({"GoodNotes/X/Heft.pdf": [("", "Ohne Datum"), ("2026-09-01", "Mit Datum")]})
        hefte = ablage.standard().hefte()
        seiten = ablage.standard().neueste_seiten([h["id"] for h in hefte], anzahl=5)
        self.assertEqual([s["text"] for s in seiten], ["Mit Datum", "Ohne Datum"])

    def test_anzahl_begrenzt_die_treffer(self):
        self._mit_daten({"GoodNotes/X/Heft.pdf": [("2026-09-01", "a"), ("2026-09-02", "b"), ("2026-09-03", "c")]})
        hefte = ablage.standard().hefte()
        seiten = ablage.standard().neueste_seiten([h["id"] for h in hefte], anzahl=2)
        self.assertEqual(len(seiten), 2)


class Seite(MitLauf):
    def test_schnittstelle(self):
        c = TestClient(app)
        self.assertFalse(c.get("/api/goodnotes").json()["frei"])
        self.assertEqual(c.post("/api/goodnotes/abgleichen").status_code, 403)
        self.assertEqual(c.get("/api/goodnotes/suche?q=x").status_code, 403)
        self.einschalten()
        self.lauf({"GoodNotes/Biologie eA/Biologie.pdf": (FARBEN[:2], "2026-09-14T15:07:10+00:00"),
                   "GoodNotes/Kontakt B/Physik.pdf": (FARBEN[2:3], "2026-09-01T00:00:00+00:00")})
        d = c.get("/api/goodnotes").json()
        self.assertEqual([(o["name"], o["seiten"], o["gelesen"]) for o in d["ordner"]],
                         [("Biologie eA", 2, 2), ("Kontakt B", 1, 1)])
        self.assertEqual(c.post("/api/goodnotes/ordner", json={"ausgeschlossen": ["Kontakt B/"]}).json()["ausgeschlossen"],
                         ["Kontakt B"])
        self.assertTrue([o for o in c.get("/api/goodnotes").json()["ordner"] if o["name"] == "Kontakt B"][0]["ausgeschlossen"])
        self.assertEqual(c.get("/api/goodnotes/seite/2/1").status_code, 404)      # ausgeschlossen
        r = c.get("/api/goodnotes/seite/1/2")
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/jpeg"))
        self.assertEqual(c.get("/api/goodnotes/text/1/2").json()["text"], "Fotosynthese Lichtreaktion Seite 2")
        self.assertEqual(c.get("/api/goodnotes/suche?q=Lichtreaktion").json()["treffer"][0]["nr"], 2)


if __name__ == "__main__":
    unittest.main()
