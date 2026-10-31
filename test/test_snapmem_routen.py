"""Snapchat-Memories: Routen (Upload in Teilen → Auftrag → Stand → Ergebnis)."""
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from fastapi.testclient import TestClient

from kern.main import app
from kern.snapmem import auftraege as a


def _zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("json/memories_history.json", "{}")
    return buf.getvalue()


class Routen(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.addCleanup(self.env.stop)
        klein = mock.patch.object(a, "TEIL", 16)
        klein.start()
        self.addCleanup(klein.stop)
        self.c = TestClient(app)

    def _hochladen(self, inhalt=None, name="export.zip"):
        inhalt = inhalt or _zip()
        start = self.c.post("/api/snapmem/hochladen", json={"name": name, "groesse": len(inhalt)})
        self.assertEqual(start.status_code, 200, start.text)
        uid = start.json()["id"]
        for nr in range((len(inhalt) + 15) // 16):
            r = self.c.put(f"/api/snapmem/hochladen/{uid}/{nr}", content=inhalt[nr * 16:(nr + 1) * 16],
                           headers={"Content-Type": "application/octet-stream"})
            self.assertEqual(r.status_code, 200, r.text)
        return self.c.post(f"/api/snapmem/hochladen/{uid}/fertig")

    def test_ganzer_weg_bis_zum_download(self):
        fertig = self._hochladen()
        self.assertEqual(fertig.status_code, 200, fertig.text)
        datei = fertig.json()["datei"]
        self.assertEqual([u["datei"] for u in self.c.get("/api/snapmem").json()["uploads"]], [datei])
        aid = self.c.post("/api/snapmem/auftrag", json={"zips": [datei], "modus": "sauber"}).json()["id"]
        self.assertEqual(self.c.get(f"/api/snapmem/auftrag/{aid}").json()["zustand"], "wartet")
        self.assertEqual(self.c.get(f"/api/snapmem/auftrag/{aid}/ergebnis").status_code, 404)
        # Der Arbeiter meldet fertig:
        (a.ordner() / "ergebnisse" / f"{aid}.zip").write_bytes(b"ERGEBNIS")
        (a.ordner() / "ergebnisse" / f"{aid}.status.json").write_text(json.dumps({"zustand": "fertig"}))
        r = self.c.get(f"/api/snapmem/auftrag/{aid}/ergebnis")
        self.assertEqual((r.status_code, r.content), (200, b"ERGEBNIS"))
        self.assertIn("snapchat-memories-sauber.zip", r.headers["content-disposition"])
        self.assertEqual(self.c.post(f"/api/snapmem/auftrag/{aid}/loeschen").json(), {"ok": True})
        self.assertEqual(self.c.get(f"/api/snapmem/auftrag/{aid}").status_code, 400)

    def test_fehler_kommen_als_400_mit_text(self):
        r = self.c.post("/api/snapmem/hochladen", json={"name": "foto.jpg", "groesse": 10})
        self.assertEqual(r.status_code, 400)
        self.assertIn("ZIP", r.json()["detail"])
        self.assertEqual(self.c.post("/api/snapmem/auftrag", json={"zips": ["x.zip"], "modus": "alles"}).status_code, 400)
        self.assertEqual(self.c.get("/api/snapmem/auftrag/../../etc").status_code in (400, 404), True)

    def test_zu_grosses_teil_wird_abgelehnt(self):
        uid = self.c.post("/api/snapmem/hochladen", json={"name": "x.zip", "groesse": 64}).json()["id"]
        r = self.c.put(f"/api/snapmem/hochladen/{uid}/0", content=b"x" * 40,
                       headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(r.status_code, 413)

    def test_fremder_ursprung_wird_abgewiesen(self):
        r = self.c.post("/api/snapmem/hochladen", json={"name": "x.zip", "groesse": 4},
                        headers={"Sec-Fetch-Site": "cross-site"})
        self.assertGreaterEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
