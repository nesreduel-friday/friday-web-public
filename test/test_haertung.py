"""Härtung: fremde Webseiten, Sicherheits-Header, Dateien aus fremder Hand, absurde Eingaben.

Entstanden aus dem Härtetest vom 26.09.2026 (docs/LEHREN.md)."""

import asyncio
import io
import os
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from kern import config
from kern.chats import einlesen
from kern.main import app


class Basis(unittest.TestCase):
    def setUp(self):
        self.daten = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.daten, "FRIDAY_URL": "https://friday.example"})
        self.env.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()


class FremdeSeite(Basis):
    """Es gibt keine Anmeldung — also darf keine fremde Seite etwas auslösen."""

    # Ein Formular-POST ohne Body: kein Preflight, der Browser schickt ihn einfach ab.
    ZIEL = "/api/spotify/trennen"

    def test_fremder_origin_wird_abgelehnt(self):
        for origin in ("https://boese.example", "null", "http://friday.example.boese.de", "https://192.168.178.1"):
            r = self.c.post(self.ZIEL, headers={"Origin": origin})
            self.assertEqual(r.status_code, 403, origin)

    def test_ohne_origin_aber_fremd_laut_browser(self):
        for wert in ("cross-site", "same-site"):
            r = self.c.post(self.ZIEL, headers={"Sec-Fetch-Site": wert})
            self.assertEqual(r.status_code, 403, wert)

    def test_eigene_seite_darf(self):
        self.assertEqual(self.c.post(self.ZIEL, headers={"Origin": "https://friday.example",
                                                         "Sec-Fetch-Site": "same-origin"}).status_code, 200)
        self.assertEqual(self.c.post(self.ZIEL, headers={"Origin": "http://127.0.0.1:8124"}).status_code, 200)

    def test_nur_genau_fridays_herkunft_mit_schema_und_port(self):
        # Auf demselben Hostnamen laufen Sonarr (:8989), Jellyfin (:8096) …: eine XSS-Lücke dort darf hier nichts auslösen.
        for origin in ("https://friday.example:8989", "http://friday.example:8096", "http://friday.example",
                       "https://friday.example:8443", "http://localhost:9999", "http://127.0.0.1:9999",
                       "http://localhost", "https://127.0.0.1:8124", "http://[::1]:9999",
                       "https://user@friday.example", "https://friday.example/pfad", "https://friday.example:abc",
                       "friday.example", "https://", ""):
            r = self.c.post(self.ZIEL, headers={"Origin": origin})
            self.assertEqual(r.status_code, 403, origin)

    def test_eigene_herkuenfte_mit_port(self):
        for origin in ("https://friday.example", "https://friday.example:443", "https://FRIDAY.example",
                       "http://127.0.0.1:8124", "http://localhost:8124", "http://[::1]:8124"):
            r = self.c.post(self.ZIEL, headers={"Origin": origin})
            self.assertEqual(r.status_code, 200, origin)

    def test_werkzeuge_ohne_browserkopf_duerfen(self):
        self.assertEqual(self.c.post(self.ZIEL).status_code, 200)

    def test_lesen_bleibt_offen(self):
        # GET löst nichts aus (und der Browser braucht es für Bilder u. ä. auch aus anderen Reitern).
        self.assertEqual(self.c.get("/api/health", headers={"Origin": "https://boese.example"}).status_code, 200)

    def test_alle_aendernden_methoden_sind_erfasst(self):
        for methode in ("post", "put", "patch", "delete"):
            r = getattr(self.c, methode)("/api/buecher/1/loeschen", headers={"Origin": "https://boese.example"})
            self.assertEqual(r.status_code, 403, methode)


class Kopfzeilen(Basis):
    def test_sicherheitskoepfe_ueberall(self):
        for pfad in ("/", "/api/health", "/gibt-es-nicht"):
            h = self.c.get(pfad).headers
            self.assertEqual(h["x-content-type-options"], "nosniff", pfad)
            self.assertEqual(h["x-frame-options"], "SAMEORIGIN", pfad)
            self.assertIn("frame-ancestors 'self'", h["content-security-policy"], pfad)

    def test_nur_eigene_skripte(self):
        csp = self.c.get("/").headers["content-security-policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("script-src 'self' 'unsafe", csp)
        self.assertIn("object-src 'none'", csp)

    def test_bilder_von_anbietern_bleiben_erlaubt(self):
        # YouTube-Vorschaubilder (Studio) und Spotify-Cover laden direkt vom Anbieter.
        self.assertRegex(self.c.get("/").headers["content-security-policy"], r"img-src [^;]*\bhttps:")

    def test_websocket_adresse_folgt_dem_host(self):
        csp = self.c.get("/", headers={"Host": "hphome.beispiel.ts.net"}).headers["content-security-policy"]
        self.assertIn("wss://hphome.beispiel.ts.net", csp)

    def test_host_kann_die_csp_nicht_umbauen(self):
        csp = self.c.get("/", headers={"Host": "x; script-src *"}).headers["content-security-policy"]
        self.assertNotIn("script-src *", csp)

    def test_kein_inline_skript_in_der_seite(self):
        # Die CSP erlaubt nur eigene Dateien. Ein Inline-Skript würde still nicht mehr laufen.
        import re
        html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")
        for m in re.finditer(r"<script([^>]*)>", html):
            self.assertIn("src=", m.group(1))
        self.assertNotRegex(html, r"\son[a-z]+\s*=")


class FremdeDatei(Basis):
    """Dateien aus dem IServ-Gruppenordner stammen von Lehrern und Mitschülern."""

    def _holen(self, dateiname: str, inhalt: bytes = b"<script>alert(1)</script>"):
        pfad = Path(self.daten) / "datei.bin"
        pfad.write_bytes(inhalt)
        ablage = mock.Mock()
        ablage.eintrag.return_value = {"dateiname": dateiname}
        ablage.original_pfad.return_value = pfad
        with mock.patch("kern.main.gruppenordnerablage.standard", return_value=ablage):
            return self.c.get("/api/gruppenordner/1/original"), ablage

    def test_html_und_svg_werden_nie_angezeigt(self):
        for name in ("Tafel.html", "Bild.svg", "x.xhtml", "y.htm", "z.js", "w.xml"):
            r, _ = self._holen(name)
            self.assertEqual(r.headers["content-type"], "application/octet-stream", name)
            self.assertTrue(r.headers["content-disposition"].startswith("attachment"), name)
            self.assertEqual(r.headers["x-content-type-options"], "nosniff")

    def test_pdf_und_bilder_bleiben_lesbar(self):
        for name, typ in (("Skript.pdf", "application/pdf"), ("Foto.JPG", "image/jpeg"), ("a.png", "image/png")):
            r, _ = self._holen(name, b"%PDF-1.4")
            self.assertEqual(r.headers["content-type"], typ, name)
            self.assertTrue(r.headers["content-disposition"].startswith("inline"), name)

    def test_pdf_bekommt_keine_csp_die_chromes_ansicht_bricht(self):
        r, _ = self._holen("Skript.pdf", b"%PDF-1.4")
        self.assertNotIn("content-security-policy", r.headers)

    def test_endung_ist_keine_pfadangabe(self):
        _, ablage = self._holen("x.a/../../../etc/passwd")
        endung = ablage.original_pfad.call_args[0][1]
        self.assertNotIn("/", endung)
        self.assertNotIn(".", endung)


class AbsurdeEingaben(Basis):
    def test_riesige_nummer_ist_404_nicht_500(self):
        for pfad in ("/api/gespraeche/99999999999999999999", "/api/buecher/99999999999999999999/seite/1",
                     "/api/gruppenordner/99999999999999999999/original"):
            self.assertEqual(self.c.get(pfad).status_code, 404, pfad)
        r = self.c.delete("/api/noten/eintraege/99999999999999999999")
        self.assertEqual(r.status_code, 404)

    def test_studio_kennung_zu_lang(self):
        self.assertEqual(self.c.get("/api/studio/video/" + "A" * 5000).status_code, 404)


class Hochladen(Basis):
    def _hoch(self, name: str, inhalt: bytes):
        return self.c.post("/api/chats/hochladen", files={"datei": (name, inhalt)})

    def test_zu_grosser_upload(self):
        with mock.patch.object(einlesen, "MAX_UPLOAD", 1024):
            r = self._hoch("chat.txt", b"x" * 5000)
        self.assertEqual(r.status_code, 413)
        # Der Rest bleibt nicht auf der Platte liegen.
        self.assertEqual([p for p in Path(self.daten).rglob("*chat.txt")], [])

    def test_normaler_verlauf_geht(self):
        zeile = "12.09.26, 10:00 - Kontakt P: Hallo\n"
        r = self._hoch("WhatsApp Chat mit Kontakt P.txt", zeile.encode() * 3)
        self.assertEqual(r.status_code, 200, r.text)

    def test_zip_bombe_zu_viele_dateien(self):
        puffer = io.BytesIO()
        with zipfile.ZipFile(puffer, "w") as z:
            for i in range(einlesen.MAX_TXT_DATEIEN + 1):
                z.writestr(f"{i}.txt", "x")
        r = self._hoch("bombe.zip", puffer.getvalue())
        self.assertEqual(r.status_code, 413)

    def test_zip_bombe_entpackt_zu_gross(self):
        puffer = io.BytesIO()
        with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("chat.txt", "a" * 100_000)
        with mock.patch.object(einlesen, "MAX_TEXT_GESAMT", 1000):
            r = self._hoch("dick.zip", puffer.getvalue())
        self.assertEqual(r.status_code, 413)
        self.assertIn("400 MB", r.json()["detail"])


class Dateirechte(Basis):
    def test_neue_dateien_und_ordner_gehoeren_nur_friday(self):
        from kern.protokoll import speicher
        speicher.standard().gespraech_beginnen("chat")
        gefunden = [p for p in Path(self.daten).rglob("*") if p.is_file()]
        self.assertTrue(gefunden, "es sollte eine Datenbank angelegt worden sein")
        for p in gefunden:
            self.assertEqual(p.stat().st_mode & 0o077, 0, f"{p.name} ist für andere lesbar")


class Anfragegroesse(Basis):
    def test_riesiges_json_wird_vor_dem_lesen_abgelehnt(self):
        r = self.c.post("/api/chat", content=b"{}", headers={"Content-Type": "application/json",
                                                               "Content-Length": str(200 * 1024 * 1024)})
        self.assertEqual(r.status_code, 413)

    def test_falsche_laenge_ist_kein_absturz(self):
        r = self.c.post("/api/spotify/trennen", headers={"Content-Length": "abc"})
        self.assertIn(r.status_code, (400, 413))

    def test_grosse_uploads_bleiben_moeglich(self):
        from kern import main
        class R:  # so gross wie ein 300-MB-Buch, als multipart
            method = "POST"
            headers = {"content-length": str(310 * 1024 * 1024), "content-type": "multipart/form-data; boundary=x"}
        self.assertFalse(main._zu_gross(R))


class ZustimmungUndFremderText(unittest.TestCase):
    """Prompt-Injection: „Bitte lies mir die Mail vor“ enthält ein Ja. Steht in der Mail
    ‚lade das Video hoch‘, darf dieses Ja den Aufruf nicht freischalten."""

    def _lauf(self, **kw):
        from kern.werkzeuge import katalog
        gesehen = {}

        def probe(zustimmung: bool = False) -> str:
            gesehen["z"] = zustimmung
            return "ok"
        probe.kennt_zustimmung = True
        modul = types.SimpleNamespace(WERKZEUGE={"probe": probe}, ERKLAERUNGEN=[])
        with mock.patch.object(katalog, "_MODULE", [modul]):
            asyncio.run(katalog.ausfuehren("probe", **kw))
        return gesehen["z"]

    def test_ja_gilt_nach_fremdem_text_nicht(self):
        self.assertFalse(self._lauf(fremde_inhalte=True, zustimmung=True))

    def test_ja_gilt_weiter_ohne_fremden_text(self):
        self.assertTrue(self._lauf(fremde_inhalte=False, zustimmung=True))

    def test_ohne_ja_nie(self):
        self.assertFalse(self._lauf(fremde_inhalte=False, zustimmung=False))

    def test_modell_kann_sich_zustimmung_nicht_selbst_geben(self):
        self.assertFalse(self._lauf(argumente={"zustimmung": True}))

    def test_video_wird_nach_fremdem_text_nicht_veroeffentlicht(self):
        """Der ganze Weg mit dem echten Werkzeug: Ja in der Nachricht, Mail im selben Zug."""
        from kern import konnektoren
        from kern.werkzeuge import katalog, studio
        video = {"status": "fertig", "kennung": "v1", "titel": "Test", "video": "v1.mp4"}
        hochladen = mock.AsyncMock(return_value={"id": "abc"})
        with mock.patch.object(konnektoren, "benutzbar", return_value=True), \
                mock.patch.object(konnektoren, "schreiben_erlaubt", return_value=True), \
                mock.patch.object(studio, "_fertige", return_value=[video]), \
                mock.patch.object(studio.dienst, "hochladen", hochladen):
            frage = asyncio.run(katalog.ausfuehren("studio_hochladen", {}, fremde_inhalte=True, zustimmung=True))
            self.assertIn("Soll ich", frage["ergebnis"])
            hochladen.assert_not_awaited()
            asyncio.run(katalog.ausfuehren("studio_hochladen", {}, fremde_inhalte=False, zustimmung=True))
            hochladen.assert_awaited_once()

    def test_echte_werkzeuge_mit_zustimmung_sind_erfasst(self):
        # Wer ein neues Werkzeug mit Zustimmung baut, bekommt hier einen Hinweis, dass die
        # Regel oben auch für ihn gilt (sie sitzt in katalog.ausfuehren, nicht im Werkzeug).
        from kern.werkzeuge import katalog
        mit = {n for m in katalog._MODULE for n, f in m.WERKZEUGE.items() if getattr(f, "kennt_zustimmung", False)}
        self.assertEqual(mit, {"termin_bestaetigen", "studio_hochladen", "whatsapp_antwort", "whatsapp_senden",
                               "schicke_nachricht", "schicke_snap", "kontakt_anfragen", "anfrage_senden", "anfrage_annehmen", "medien_loeschen_bestaetigen",
                               "skill_bestaetigen", "rundnachricht_freigeben", "regel_bestaetigen", "regel_streichen", "vorhaben_starten"})


class AendernNachFremdtext(Basis):
    """Werkzeuge, die etwas ändern, ohne „ja“ zu verlangen, laufen nach fremdem Text nicht."""

    def _lauf(self, name, argumente, fremd):
        from kern import konnektoren
        from kern.werkzeuge import katalog
        with mock.patch.object(konnektoren, "benutzbar", return_value=True), \
                mock.patch.object(konnektoren, "schreiben_erlaubt", return_value=True):
            return asyncio.run(katalog.ausfuehren(name, argumente, fremde_inhalte=fremd))

    def test_video_bleibt_nach_fremdem_text_erhalten(self):
        from kern.werkzeuge import studio
        fertig = Path(self.daten) / "studio" / "fertig"
        fertig.mkdir(parents=True)
        (fertig / "v1.mp4").write_bytes(b"video")
        (fertig / "v1.json").write_text('{"status": "fertig", "kennung": "v1", "video": "v1.mp4", "titel": "T"}')
        r = self._lauf("studio_verwerfen", {}, fremd=True)
        self.assertIn("fehler", r)
        self.assertTrue((fertig / "v1.mp4").exists())
        r = self._lauf("studio_verwerfen", {}, fremd=False)      # Eikes eigene Nachricht: erlaubt
        self.assertIn("Verworfen", r["ergebnis"])
        self.assertFalse((fertig / "v1.mp4").exists())

    def test_einstellungen_nicht_nach_fremdem_text(self):
        for name, args in (("benachrichtigung_stellen", {"art": "warnung", "an": False}),
                           ("ruhezeit_stellen", {"an": False}), ("erinnerung_absagen", {"nummer": 1}),
                           ("vergessen", {"thema": "x"}), ("fahrt_vergessen", {"welche": "1"})):
            self.assertIn("fehler", self._lauf(name, args, fremd=True), name)

    def test_die_liste_nennt_nur_echte_werkzeuge(self):
        from kern.werkzeuge import katalog
        for m in katalog._MODULE:
            for n in getattr(m, "NICHT_NACH_FREMDTEXT", ()):
                self.assertIn(n, m.WERKZEUGE, f"{m.__name__}: {n} gibt es nicht (Tippfehler?)")


if __name__ == "__main__":
    unittest.main()
