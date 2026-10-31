import unittest

from snapmem.personen import count_persons, person_boxes
from snapmem.tests.fotos import foto


class PersonenTest(unittest.TestCase):
    def test_person_von_hinten_wird_gefunden(self):
        self.assertGreaterEqual(count_persons(foto("person_ruecken.jpg").read_bytes()), 1)

    def test_laub_ohne_mensch_ergibt_null(self):
        self.assertEqual(count_persons(foto("kein_mensch_laub.jpg").read_bytes()), 0)

    def test_kaputte_datei_ergibt_null(self):
        self.assertEqual(count_persons(b"kein bild"), 0)

    def test_boxen_liegen_im_bild(self):
        for x, y, w, h, score in person_boxes(foto("person_ruecken.jpg").read_bytes()):
            self.assertGreater(score, 0.4)
            self.assertGreater(w, 0)
            self.assertGreater(h, 0)


if __name__ == "__main__":
    unittest.main()
