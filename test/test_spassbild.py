"""spassbild.finden: erstes passendes, nie ein schon gesendetes — ohne Netz."""
import asyncio
import unittest
from unittest import mock

from kern.dienste import spassbild


def lauf(c):
    return asyncio.run(c)


class Finden(unittest.TestCase):
    def setUp(self):
        # Der Suchplan fragt ein Modell — hier immer eine feste Antwort, nie das Netz.
        self.plan = mock.patch.object(spassbild.bildsuche, "suchplan", mock.AsyncMock(return_value=["lustige Katze"]))
        self.plan.start()
        self.addCleanup(self.plan.stop)

    def test_nimmt_das_beste_passende_und_ueberspringt_gesendete(self):
        treffer = [{"bild": "https://x.test/a.jpg", "titel": "A"}, {"bild": "https://x.test/b.jpg", "titel": "B"}]
        with mock.patch.object(spassbild.bildsuche, "kandidaten", mock.AsyncMock(return_value=treffer)), \
                mock.patch.object(spassbild.bildsuche, "holen", mock.AsyncMock(side_effect=["k1"])), \
                mock.patch.object(spassbild.bildsuche, "pruefen", mock.AsyncMock(return_value={"k1": {"passt": True, "harmlos": True, "qualitaet": 5, "was": "a"}})), \
                mock.patch.object(spassbild.bildsuche, "bild", side_effect=lambda k: (b"B-" + k.encode(), "image/jpeg")), \
                mock.patch.object(spassbild.bildsuche, "gezeigt_merken"):
            daten, url, titel = lauf(spassbild.finden("lustige Katze", ausser={"https://x.test/b.jpg"}))
        self.assertEqual((daten, url, titel), (b"B-k1", "https://x.test/a.jpg", "A"))

    def test_unbedenklichkeit_ist_pflicht(self):
        treffer = [{"bild": "https://x.test/a.jpg", "titel": "A"}, {"bild": "https://x.test/b.jpg", "titel": "B"}]
        urteil = {"k1": {"passt": True, "harmlos": False, "qualitaet": 9, "was": "a"},
                  "k2": {"passt": True, "harmlos": True, "qualitaet": 3, "was": "b"}}
        with mock.patch.object(spassbild.bildsuche, "kandidaten", mock.AsyncMock(return_value=treffer)), \
                mock.patch.object(spassbild.bildsuche, "holen", mock.AsyncMock(side_effect=["k1", "k2"])), \
                mock.patch.object(spassbild.bildsuche, "pruefen", mock.AsyncMock(return_value=urteil)) as pr, \
                mock.patch.object(spassbild.bildsuche, "bild", side_effect=lambda k: (b"B-" + k.encode(), "image/jpeg")), \
                mock.patch.object(spassbild.bildsuche, "gezeigt_merken"):
            daten, url, _ = lauf(spassbild.finden("etwas"))
        self.assertEqual(url, "https://x.test/b.jpg")                    # das unbedenkliche, auch wenn das andere besser „passt“
        self.assertTrue(pr.call_args.kwargs["harmlos"])

    def test_ohne_pruefung_wird_nichts_gesendet(self):
        treffer = [{"bild": "https://x.test/a.jpg", "titel": "A"}]
        with mock.patch.object(spassbild.bildsuche, "kandidaten", mock.AsyncMock(return_value=treffer)), \
                mock.patch.object(spassbild.bildsuche, "holen", mock.AsyncMock(return_value="k1")), \
                mock.patch.object(spassbild.bildsuche, "pruefen", mock.AsyncMock(return_value=None)), \
                mock.patch.object(spassbild.bildsuche, "bild", return_value=(b"B", "image/jpeg")):
            self.assertIsNone(lauf(spassbild.finden("etwas")))

    def test_geprueft_wird_gegen_den_wunsch_nicht_gegen_den_suchbegriff(self):
        treffer = [{"bild": "https://x.test/a.jpg", "titel": "A"}]
        with mock.patch.object(spassbild.bildsuche, "suchplan", mock.AsyncMock(return_value=["ganz anderer Begriff"])), \
                mock.patch.object(spassbild.bildsuche, "kandidaten", mock.AsyncMock(return_value=treffer)), \
                mock.patch.object(spassbild.bildsuche, "holen", mock.AsyncMock(return_value="k1")), \
                mock.patch.object(spassbild.bildsuche, "pruefen", mock.AsyncMock(return_value={"k1": {"passt": True, "harmlos": True, "qualitaet": 5, "was": "a"}})) as pr, \
                mock.patch.object(spassbild.bildsuche, "bild", return_value=(b"B", "image/jpeg")), \
                mock.patch.object(spassbild.bildsuche, "gezeigt_merken"):
            lauf(spassbild.finden("Katze im Karton"))
        self.assertEqual(pr.call_args.args[0], "Katze im Karton")

    def _lauf_mit(self, treffer, **kw):
        n = len(treffer)
        urteil = {f"k{i}": {"passt": True, "harmlos": True, "qualitaet": 9 - i, "was": "x"} for i in range(1, n + 1)}
        with mock.patch.object(spassbild.bildsuche, "kandidaten", mock.AsyncMock(return_value=treffer)), \
                mock.patch.object(spassbild.bildsuche, "holen", mock.AsyncMock(side_effect=[f"k{i}" for i in range(1, n + 1)])), \
                mock.patch.object(spassbild.bildsuche, "pruefen", mock.AsyncMock(return_value=urteil)), \
                mock.patch.object(spassbild.bildsuche, "bild", side_effect=lambda k: (b"B-" + k.encode(), "image/jpeg")), \
                mock.patch.object(spassbild.bildsuche, "gezeigt_merken"):
            return lauf(spassbild.finden("Katze", **kw))

    def test_hochkant_wird_bevorzugt_auch_wenn_das_querformat_besser_bewertet_ist(self):
        treffer = [{"bild": "https://x.test/quer.jpg", "titel": "Q", "breite": 1600, "hoehe": 900},
                   {"bild": "https://x.test/hoch.jpg", "titel": "H", "breite": 900, "hoehe": 1600}]
        self.assertEqual(self._lauf_mit(treffer, hochkant=True)[1], "https://x.test/hoch.jpg")
        self.assertEqual(self._lauf_mit(treffer)[1], "https://x.test/quer.jpg")        # ohne Wunsch nach Hochkant gilt die Bewertung

    def test_ohne_hochkant_treffer_geht_das_querformat(self):
        treffer = [{"bild": "https://x.test/quer.jpg", "titel": "Q", "breite": 1600, "hoehe": 900}]
        self.assertEqual(self._lauf_mit(treffer, hochkant=True)[1], "https://x.test/quer.jpg")

    def test_unbekannte_masse_zaehlen_nicht_als_hochkant(self):
        treffer = [{"bild": "https://x.test/a.jpg", "titel": "A"},
                   {"bild": "https://x.test/hoch.jpg", "titel": "H", "breite": 900, "hoehe": 1600}]
        self.assertEqual(self._lauf_mit(treffer, hochkant=True)[1], "https://x.test/hoch.jpg")

    def test_nur_deutlich_hochkant_zaehlt(self):
        treffer = [{"bild": "https://x.test/fast.jpg", "titel": "F", "breite": 1000, "hoehe": 1050},
                   {"bild": "https://x.test/hoch.jpg", "titel": "H", "breite": 900, "hoehe": 1600}]
        self.assertEqual(self._lauf_mit(treffer, hochkant=True)[1], "https://x.test/hoch.jpg")

    def test_nichts_gefunden_gibt_none(self):
        with mock.patch.object(spassbild.bildsuche, "kandidaten", mock.AsyncMock(return_value=[])):
            self.assertIsNone(lauf(spassbild.finden("etwas")))

    def test_nichts_passt_gibt_none(self):
        treffer = [{"bild": "https://x.test/a.jpg", "titel": "A"}]
        with mock.patch.object(spassbild.bildsuche, "kandidaten", mock.AsyncMock(return_value=treffer)), \
                mock.patch.object(spassbild.bildsuche, "holen", mock.AsyncMock(return_value="k1")), \
                mock.patch.object(spassbild.bildsuche, "pruefen", mock.AsyncMock(return_value={"k1": {"passt": False, "harmlos": True, "qualitaet": 1, "was": "x"}})), \
                mock.patch.object(spassbild.bildsuche, "bild", return_value=(b"B", "image/jpeg")):
            self.assertIsNone(lauf(spassbild.finden("etwas")))

    def test_ohne_wunsch_wird_etwas_lustiges_gesucht(self):
        gefragt = []

        async def kand(begriff, ausser=None):
            gefragt.append(begriff)
            return []
        with mock.patch.object(spassbild.bildsuche, "suchplan", mock.AsyncMock(return_value=[])), \
                mock.patch.object(spassbild.bildsuche, "kandidaten", side_effect=kand):
            lauf(spassbild.finden(""))
        self.assertTrue(gefragt[0] in spassbild.LUSTIG)
