import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from snapmem.tests import fotos


class FotoHelferTest(unittest.TestCase):
    def test_findet_foto_im_umgebungsverzeichnis(self):
        with tempfile.TemporaryDirectory() as tmp:
            ziel = Path(tmp) / "person_ruecken.jpg"
            ziel.write_bytes(b"")
            with mock.patch.dict(os.environ, {"FRIDAY_PRIVAT_FOTOS": tmp}):
                self.assertEqual(fotos.foto("person_ruecken.jpg"), ziel)

    def test_fehlt_ueberall_dann_skip_mit_grund(self):
        with tempfile.TemporaryDirectory() as tmp:
            leer = Path(tmp) / "leer"
            leer.mkdir()
            with mock.patch.dict(os.environ, {"FRIDAY_PRIVAT_FOTOS": str(leer)}):
                # Auch die Repo-Pfade künstlich ins Leere zeigen lassen, damit der
                # Test unabhängig davon ist, ob auf dieser Maschine echte Fotos liegen.
                with mock.patch.object(fotos, "_REPO", Path(tmp) / "repo"):
                    with mock.patch.object(fotos, "_TESTDATEN", Path(tmp) / "daten"):
                        with self.assertRaises(unittest.SkipTest) as ctx:
                            fotos.foto("person_ruecken.jpg")
            self.assertIn("data/privat/fotos", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
