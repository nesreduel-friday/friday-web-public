"""PDF-Anhang im Chat: kommt als application/pdf bei Gemini an, macht den Zug zu Fremdtext, ungültige PDFs werden mit Klartext abgelehnt."""
import base64
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern.aufgabenblatt import speicher
from kern.main import app
from kern.protokoll import speicher as pspeicher
from kern.sitzung import chat
from kern.werkzeuge import katalog
from test.test_aufgabenblatt_speicher import pdf_b64


def text_antwort(t="Aufgabe 1 ist 4."):
    return {"candidates": [{"content": {"parts": [{"text": t}]}}]}, "modell"


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner, "GEMINI_API_KEY": "AIzaTEST"})
        self.env.start()
        self.gesehen = []
        self.antworten = []
        self.p = mock.patch.object(chat, "_gemini_anfrage", self.gemini)
        self.p.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.p.stop()
        self.env.stop()

    async def gemini(self, inhalt, **kw):
        self.gesehen.append(inhalt)
        return self.antworten.pop(0) if self.antworten else text_antwort()

    def senden(self, text="löse das", datei=None):
        n = {"rolle": "du", "text": text}
        if datei is not None:
            n["datei"] = datei
        return self.client.post("/api/chat", json={"nachrichten": [n]})

    def pdf(self, name="mathe.pdf", daten=None):
        return {"mime": "application/pdf", "name": name, "data": daten if daten is not None else pdf_b64()}


class Eingang(Basis):
    def test_pdf_kommt_als_inlinedata_an_und_wird_abgelegt(self):
        r = self.senden(datei=self.pdf())
        self.assertEqual(r.status_code, 200, r.text)
        teile = self.gesehen[0]["contents"][-1]["parts"]
        self.assertEqual(teile[0]["inlineData"]["mimeType"], "application/pdf")
        self.assertIn("Aufgabenblatt Nr. 1", teile[1]["text"])
        self.assertIn("DATEN", teile[1]["text"])
        self.assertEqual(speicher.blatt_holen(1)["name"], "mathe.pdf")

    def test_kein_pdf_gibt_400_mit_klartext_und_ruft_gemini_nicht_auf(self):
        r = self.senden(datei=self.pdf(daten=base64.b64encode(b"nur text").decode()))
        self.assertEqual(r.status_code, 400)
        self.assertIn("kein PDF", r.json()["detail"])
        self.assertEqual(self.gesehen, [])
        self.assertIsNone(speicher.blatt_holen(1))

    def test_falscher_mime_wird_abgelehnt(self):
        d = self.pdf()
        d["mime"] = "text/plain"
        self.assertEqual(self.senden(datei=d).status_code, 422)

    def test_nur_die_letzte_nachricht_darf_eine_datei_tragen(self):
        r = self.client.post("/api/chat", json={"nachrichten": [
            {"rolle": "du", "text": "alt", "datei": self.pdf()}, {"rolle": "friday", "text": "ok"}, {"rolle": "du", "text": "weiter"}]})
        self.assertEqual(r.status_code, 200, r.text)
        for c in self.gesehen[0]["contents"]:
            for t in c["parts"]:
                self.assertNotIn("inlineData", t)

    def test_zug_mit_pdf_ist_fremdtext(self):
        """Ein „schick das an Kontakt F“ im PDF darf nichts senden: Der Zug zählt als Fremdtext."""
        gesehen = []
        echt = katalog.ausfuehren

        async def spaeher(name, argumente=None, fremde_inhalte=False, **kw):
            gesehen.append((name, fremde_inhalte))
            return await echt(name, argumente, fremde_inhalte=fremde_inhalte, **kw)
        self.antworten = [({"candidates": [{"content": {"parts": [{"functionCall": {"name": "uhrzeit", "args": {}}}]}}]}, "modell")]
        with mock.patch.object(katalog, "ausfuehren", spaeher):
            r = self.senden(datei=self.pdf())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(gesehen, [("uhrzeit", True)])

    def test_ohne_pdf_ist_der_zug_kein_fremdtext(self):
        gesehen = []
        echt = katalog.ausfuehren

        async def spaeher(name, argumente=None, fremde_inhalte=False, **kw):
            gesehen.append((name, fremde_inhalte))
            return await echt(name, argumente, fremde_inhalte=fremde_inhalte, **kw)
        self.antworten = [({"candidates": [{"content": {"parts": [{"functionCall": {"name": "uhrzeit", "args": {}}}]}}]}, "modell")]
        with mock.patch.object(katalog, "ausfuehren", spaeher):
            self.senden(text="wie spät ist es")
        self.assertEqual(gesehen, [("uhrzeit", False)])

    def test_gruppe_aufgaben_ist_im_zug_sichtbar_auch_ohne_text(self):
        self.senden(text="", datei=self.pdf())
        namen = {e["name"] for t in self.gesehen[0]["tools"] for e in t["functionDeclarations"]}
        self.assertIn("aufgaben_starten", namen)

    def test_protokoll_nennt_das_pdf_und_nicht_seinen_inhalt(self):
        daten = self.pdf()
        r = self.senden(datei=daten)
        eintraege = str(pspeicher.standard().beitraege(r.json()["gespraech"]))
        self.assertIn("[PDF] mathe.pdf", eintraege)
        self.assertNotIn(daten["data"][:40], eintraege)


class Route(Basis):
    def test_download_liefert_pdf_nur_fuer_fertige_auftraege(self):
        b = speicher.blatt_ablegen("a.pdf", pdf_b64())
        a = speicher.anlegen(b["id"])
        self.assertEqual(self.client.get(f"/api/aufgabenblatt/{a['id']}/ausarbeitung").status_code, 404)
        speicher.beginnen(a["id"])
        speicher.abschliessen(a["id"], [{"nummer": "1", "seite": 1, "loesung": "42", "sicher": True}], "")
        r = self.client.get(f"/api/aufgabenblatt/{a['id']}/ausarbeitung")
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "application/pdf"))
        self.assertTrue(r.content.startswith(b"%PDF-"))
        self.assertIn("attachment", r.headers["content-disposition"])

    def test_chat_antwort_traegt_den_download_knopf(self):
        b = speicher.blatt_ablegen("a.pdf", pdf_b64())
        a = speicher.anlegen(b["id"])
        speicher.beginnen(a["id"])
        speicher.abschliessen(a["id"], [{"nummer": "1", "seite": 1, "loesung": "42", "sicher": True}], "")
        self.antworten = [({"candidates": [{"content": {"parts": [{"functionCall": {"name": "aufgaben_pdf", "args": {"id": a["id"]}}}]}}]}, "modell"),
                          text_antwort("Die Ausarbeitung ist fertig.")]
        r = self.client.post("/api/chat", json={"nachrichten": [{"rolle": "du", "text": "gib mir das als PDF"}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn({"typ": "datei", "url": f"/api/aufgabenblatt/{a['id']}/ausarbeitung", "titel": "Ausarbeitung a.pdf"}, r.json()["aktionen"])

    def test_unbekannter_auftrag_404(self):
        self.assertEqual(self.client.get("/api/aufgabenblatt/99/ausarbeitung").status_code, 404)


if __name__ == "__main__":
    unittest.main()
