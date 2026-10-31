"""Kalender-Hinweise: Rhythmen werden vom Code gezählt, nicht vom Modell."""
import asyncio
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from kern.gedaechtnis import kalenderhinweise as kh


def t(datum, titel, kalender="Feuerwehr", ort=""):
    beginn = datetime.fromisoformat(datum).replace(tzinfo=ZoneInfo("Europe/Berlin")).isoformat(timespec="minutes")   # Sommer-/Winterzeit stimmt
    return {"kalender": kalender, "titel": titel, "beginn": beginn, "ende": "", "ganztags": False, "ort": ort, "notiz": ""}


MONTAGE = [t("2026-10-05T18:30", "Gruppe im Löscheinsatz", ort="Feuerwehrhaus"), t("2026-10-19T18:30", "Maschinistenausbildung", ort="Feuerwehrhaus"),
           t("2026-11-02T18:30", "Erste Hilfe", ort="Feuerwehrhaus"), t("2026-11-16T18:30", "Funkausbildung", ort="Feuerwehrhaus"),
           t("2026-12-07T18:30", "Gerätedienst", ort="Feuerwehrhaus")]
JF = [t("2026-10-07T18:00", "JF"), t("2026-10-28T18:00", "JF"), t("2026-11-18T18:00", "JF"), t("2026-11-25T18:00", "JF"), t("2026-12-02T18:00", "JF")]


class Rhythmen(unittest.TestCase):
    def test_montagsdienst_alle_14_tage_trotz_verschiedener_titel(self):
        zeilen = kh.rhythmen(MONTAGE + JF, ["feuerwehr"])
        montag = next(z for z in zeilen if z.startswith("Montags um 18:30 Uhr"))
        self.assertIn("(Feuerwehrhaus)", montag)
        self.assertIn("Abstand meist 14 Tage", montag)
        self.assertIn("Gerätedienst", montag)

    def test_jf_wird_je_titel_erkannt(self):
        zeilen = kh.rhythmen(MONTAGE + JF, ["feuerwehr"])
        jf = next(z for z in zeilen if z.startswith("„JF“"))
        self.assertIn("meist mittwochs um 18:00 Uhr", jf)
        self.assertIn("5 Termine", jf)

    def test_nur_passende_termine_und_nie_etwas_fremdes(self):
        fremd = [t("2026-10-06T08:00", "Mathe", kalender="Schule")]
        zeilen = kh.rhythmen(fremd + JF, ["feuerwehr"])
        self.assertFalse(any("Mathe" in z for z in zeilen))

    def test_ohne_passende_termine_leer(self):
        self.assertEqual(kh.rhythmen(JF, ["therapie"]), [])
        self.assertEqual(kh.rhythmen([], ["feuerwehr"]), [])

    def test_unter_drei_terminen_kein_rhythmus_nur_naechste_termine(self):
        zeilen = kh.rhythmen(JF[:2], ["feuerwehr"])
        self.assertEqual(len(zeilen), 1)
        self.assertTrue(zeilen[0].startswith("Nächste Termine:"))

    def test_unregelmaessiger_abstand_wird_nicht_als_rhythmus_ausgegeben(self):
        wirr = [t("2026-10-01T18:00", "X"), t("2026-10-03T18:00", "X"), t("2026-10-20T18:00", "X"), t("2026-11-30T18:00", "X")]
        zeile = next(z for z in kh.rhythmen(wirr, ["x"]) if z.startswith("„X“"))
        self.assertIn("unregelmäßig", zeile)


class Legende(unittest.TestCase):
    def test_jf_wird_erklaert(self):
        zeilen = kh.mit_legende(kh.rhythmen(MONTAGE + JF, ["feuerwehr"]))
        self.assertEqual(zeilen[0], "Abkürzungen im Kalender: JF = Jugendfeuerwehr")

    def test_ohne_kuerzel_keine_legende(self):
        zeilen = kh.mit_legende(kh.rhythmen(MONTAGE, ["feuerwehr"]))
        self.assertFalse(zeilen[0].startswith("Abkürzungen"))
        self.assertEqual(kh.mit_legende([]), [])


class Hinweise(unittest.TestCase):
    def test_ohne_freigabe_des_konnektors_leer_und_ohne_kalenderzugriff(self):
        with mock.patch.object(kh.konnektoren, "aktiv", return_value=False), \
             mock.patch.object(kh, "_alle_termine", mock.AsyncMock()) as m:
            self.assertEqual(asyncio.run(kh.hinweise("Feuerwehr", ["feuerwehr"])), [])
        m.assert_not_awaited()

    def test_kalenderfehler_machen_die_seite_nicht_kaputt(self):
        with mock.patch.object(kh.konnektoren, "aktiv", return_value=True), \
             mock.patch.object(kh, "_alle_termine", mock.AsyncMock(side_effect=RuntimeError("weg"))):
            self.assertEqual(asyncio.run(kh.hinweise("Feuerwehr", ["feuerwehr"])), [])


if __name__ == "__main__":
    unittest.main()
