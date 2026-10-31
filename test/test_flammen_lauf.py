"""Flammen-Lauf: Auftrag legen, abrechnen, Fehlschläge, Probelauf — Netz und Handy sind Attrappen."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from kern import config
from kern.personen import flammen, module, register

B = ZoneInfo("Europe/Berlin")
JETZT = datetime(2026, 10, 14, 15, 0, tzinfo=B)


def lauf(c):
    return asyncio.run(c)


async def bild(pid, name):
    return (b"JPEG", "https://beispiel.test/b.jpg", "Katze")


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.verfuegbar = mock.patch("kern.personen.module.VERFUEGBAR", frozenset(module.MODULE))
        self.verfuegbar.start()
        self.p = register.anlegen("Kontakt B Muster", snapchat="maya_x")
        register.aendern(self.p["id"], module={"flammen": True})
        (config.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)
        self.setze_modus("an")
        self.auftraege = []
        self.patches = [
            mock.patch("kern.personen.flammen.snapchat.snap_wartet", return_value=False),
            mock.patch("kern.personen.flammen.snapchat.auftrag_legen", side_effect=self._auftrag),
            mock.patch("kern.personen.flammen.snapbild.vorbereiten", return_value=b"J"),
            mock.patch("kern.personen.flammen.snapbild.ablegen", return_value="snapchat/ausgang/x.jpg"),
            mock.patch("kern.dienste.benachrichtigungen.senden", return_value={}),
            mock.patch("kern.personen.flammen.konnektoren.benutzbar", return_value=True),
            mock.patch("kern.personen.flammen.konnektoren.schreiben_erlaubt", return_value=True),
            mock.patch("kern.personen.flammen.stoerung.melden", return_value={"vorhaben": None, "gemeldet": False}),
            mock.patch("kern.personen.flammen.stoerung.erledigt"),
        ]
        self.gemockt = [p.start() for p in self.patches]
        self.senden = self.gemockt[4]
        self.stoerung_melden = self.gemockt[7]
        self.stoerung_erledigt = self.gemockt[8]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.verfuegbar.stop()
        self.env.stop()

    def _auftrag(self, art, **kw):
        self.auftraege.append((art, kw))
        return f"id{len(self.auftraege)}"

    def setze_modus(self, wert):
        (config.daten_ordner() / "snapchat" / "einstellungen.json").write_text(json.dumps({"flammen": wert}), encoding="utf-8")


class Einmal(Basis):
    def test_legt_genau_einen_auftrag_und_merkt_ihn(self):
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [self.p["id"]])
        self.assertEqual(self.auftraege[0][0], "snap")
        self.assertEqual(self.auftraege[0][1]["person"], self.p["id"])
        self.assertEqual(flammen.zustand_lesen()["personen"][self.p["id"]]["auftrag"], "id1")
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [])          # Auftrag offen → nicht noch einmal

    def test_aus_und_trocken_senden_nichts(self):
        self.setze_modus("aus")
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [])
        self.setze_modus("trocken")
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [])
        self.assertEqual(self.auftraege, [])
        probe = config.daten_ordner() / "snapchat" / "flammen_probe.jsonl"
        self.assertEqual(len(probe.read_text(encoding="utf-8").splitlines()), 1)
        lauf(flammen.einmal(JETZT + timedelta(minutes=5), bild))
        self.assertEqual(len(probe.read_text(encoding="utf-8").splitlines()), 1)      # nur einmal je Person und Tag

    def test_hebel_aus_pausiert_oder_ohne_snapchat_name_nichts(self):
        register.aendern(self.p["id"], module={"flammen": False})
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [])
        register.aendern(self.p["id"], module={"flammen": True}, pausiert=True)
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [])
        register.aendern(self.p["id"], pausiert=False, snapchat="")
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [])
        self.assertEqual(self.auftraege, [])

    def test_ein_snap_wartet_beim_helfer_dann_spaeter(self):
        with mock.patch("kern.personen.flammen.snapchat.snap_wartet", return_value=True):
            self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [])
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [self.p["id"]])

    def test_kein_bild_kein_auftrag_und_kein_fehlerzaehler(self):
        async def nichts(pid, name):
            return None
        self.assertEqual(lauf(flammen.einmal(JETZT, nichts)), [])
        self.assertEqual(self.auftraege, [])
        self.assertEqual(flammen.zustand_lesen()["personen"][self.p["id"]]["fehler"], 0)

    def test_bild_wird_gemerkt(self):
        lauf(flammen.einmal(JETZT, bild))
        self.assertIn("https://beispiel.test/b.jpg", flammen.zustand_lesen()["personen"][self.p["id"]]["bilder"])

    def test_fehler_bei_einer_person_beendet_nur_diese(self):
        zweite = register.anlegen("Lena Test", snapchat="lena_x")
        register.aendern(zweite["id"], module={"flammen": True})
        aufrufe = []

        def seltsam(art, **kw):
            aufrufe.append(kw["person"])
            if kw["person"] == self.p["id"]:
                raise RuntimeError("kaputt")
            return "ok2"
        self.gemockt[1].side_effect = seltsam
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [])                  # erste Person scheitert, wird 10 Min ausgesetzt
        self.assertEqual(lauf(flammen.einmal(JETZT + timedelta(minutes=5), bild)), [zweite["id"]])
        self.assertEqual(aufrufe, [self.p["id"], zweite["id"]])


class Abrechnen(Basis):
    def offen(self):
        lauf(flammen.einmal(JETZT, bild))

    def test_gesendet_setzt_letzter_und_tag(self):
        self.offen()
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "gesendet"}):
            z = flammen.zustand_lesen()
            flammen.abrechnen(z, JETZT + timedelta(minutes=3))
        p = z["personen"][self.p["id"]]
        self.assertIsNone(p["auftrag"])
        self.assertEqual(p["letzter_tag"], "2026-10-14")
        self.assertEqual(p["fehler"], 0)
        self.assertIsNotNone(p["letzter"])

    def test_noch_nichts_gemeldet_bleibt_offen(self):
        self.offen()
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value=None):
            z = flammen.zustand_lesen()
            flammen.abrechnen(z, JETZT + timedelta(minutes=3))
        self.assertEqual(z["personen"][self.p["id"]]["auftrag"], "id1")

    def fehlschlaege(self, anzahl=3, ergebnis="aufgegeben"):
        zeit = JETZT - timedelta(hours=5)             # 10:00 — nach der Pause liegt der Wiederversuch noch im Fenster
        for _ in range(anzahl):
            lauf(flammen.einmal(zeit, bild))
            with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": ergebnis}):
                z = flammen.zustand_lesen()
                flammen.abrechnen(z, zeit, melden=True)
                flammen.zustand_schreiben(z)
            zeit += timedelta(minutes=10)
        return zeit

    def test_pause_nach_drei_fehlern_bleibt(self):
        zeit = self.fehlschlaege()
        p = flammen.zustand_lesen()["personen"][self.p["id"]]
        self.assertIsNotNone(p["pause_bis"])
        self.assertEqual(p["fehler"], 0)
        self.assertEqual(lauf(flammen.einmal(zeit, bild)), [])           # Pause bis morgen
        self.assertEqual(lauf(flammen.einmal(zeit + timedelta(hours=8), bild)), [])                      # später am selben Tag noch immer
        self.assertEqual(lauf(flammen.einmal(zeit.replace(hour=12) + timedelta(days=1), bild)), [self.p["id"]])

    def test_fehler_gehen_an_die_stoerung_und_nach_drei_fehlern_einmal_aufs_handy(self):
        self.fehlschlaege()
        self.senden.assert_called_once()
        self.assertEqual(self.senden.call_args.args[0], "kontakte")
        self.assertIn("schau bitte nach dem Handy", self.senden.call_args.args[2])
        self.assertEqual(self.senden.call_args.args[4], f"flamme-{self.p['id']}")
        self.senden.reset_mock()
        self.senden.side_effect = RuntimeError("Push kaputt")                  # Versandfehler bricht den Lauf nicht ab
        self.fehlschlaege()
        self.assertEqual(self.stoerung_melden.call_count, 3)
        for aufruf in self.stoerung_melden.call_args_list:
            self.assertEqual(aufruf.args[0], "Flammen-Snap an Kontakt B Muster")
            self.assertEqual(aufruf.args[1], f"flamme:{self.p['id']}")
        self.stoerung_erledigt.assert_not_called()

    def test_erfolg_ruft_erledigt(self):
        self.offen()
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "gesendet"}):
            flammen.abrechnen(flammen.zustand_lesen(), JETZT + timedelta(minutes=3), melden=True)
        self.stoerung_erledigt.assert_called_once_with(f"flamme:{self.p['id']}")
        self.stoerung_melden.assert_not_called()

    def test_fuenf_tage_in_folge_ergeben_ein_vorhaben_und_ein_anliegen(self):
        from pathlib import Path
        from kern.anliegen.fach import Fach as AnliegenFach
        from kern.werkbank.speicher import Fach
        ordner = Path(tempfile.mkdtemp())
        fach, anliegen = Fach(ordner / "w"), AnliegenFach(ordner / "a")
        echt = self.patches[7].temp_original            # die echte Funktion hinter der Attrappe aus setUp
        pid = self.p["id"]
        with mock.patch("kern.personen.flammen.stoerung.melden",
                        side_effect=lambda *a, **k: echt(*a, **k, fach=fach, anliegen=anliegen)), \
                mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "aufgegeben"}):
            for tag in range(5):
                z = {"personen": {pid: {**flammen._neu(), "auftrag": "x"}}}
                flammen.abrechnen(z, JETZT + timedelta(days=tag), melden=True)
        self.assertEqual(len(fach.offene()), 1)
        self.assertEqual(len(anliegen.offene("eike")), 1)


    def test_neustart_zwischen_legen_und_ergebnis_legt_nicht_doppelt(self):
        self.offen()
        self.assertEqual(lauf(flammen.einmal(JETZT + timedelta(minutes=1), bild)), [])    # Zustand kommt nur von der Platte
        self.assertEqual(len(self.auftraege), 1)

    def test_erfolg_am_naechsten_tag_zu_frueh_ist_nicht_faellig(self):
        self.offen()
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "gesendet"}):
            lauf(flammen.einmal(JETZT + timedelta(minutes=2), bild))
        morgen_frueh = JETZT.replace(hour=9) + timedelta(days=1)          # 18 h später: noch keine 20 h
        self.assertEqual(lauf(flammen.einmal(morgen_frueh, bild)), [])


class DauerhafteEmpfaengerfehler(Basis):
    """30.10.2026: Der Helfer gab den Flammen-Snap an eine Person auf, weil er den Snapchat-Namen in der
    Send-to-Liste nicht (eindeutig) fand. Der Grund ging in der Störung verloren, und der Lauf versuchte es
    jeden Tag erneut. Ein solcher Empfängerfehler ist ohne Änderung an der Person nicht behebbar."""

    GRUND = ("Antwort nach 4 Versuchen aufgegeben: „Kontakt B Muster“ steht nicht in der Send-to-Liste.")

    def fehlschlag(self, zeit, ergebnis="aufgegeben", grund=GRUND):
        lauf(flammen.einmal(zeit, bild))
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer",
                        return_value={"ergebnis": ergebnis, "grund": grund}):
            z = flammen.zustand_lesen()
            flammen.abrechnen(z, zeit, melden=True)
            flammen.zustand_schreiben(z)

    def test_erkennt_namensfehler_des_helfers(self):
        for grund in (
            "Antwort nach 4 Versuchen aufgegeben: „Kontakt B Muster“ steht nicht in der Send-to-Liste.",
            "Der Name „Kontakt B Muster“ ist in der Send-to-Liste mehrdeutig (2 Zeilen) — nichts gesendet.",
            "Falscher Empfänger ausgewählt: erwartet „Kontakt B Muster“, ausgewählt ['Kontakt B'] — nichts gesendet.",
            "Empfänger „Kontakt B Muster“ steht nicht in der Kontaktliste — Auftrag verworfen.",
            "Person und Empfänger „Kontakt B Muster“ passen nicht zusammen — Auftrag verworfen.",
        ):
            self.assertTrue(flammen._dauerhaft(grund), grund)
        for grund in ("", None, "Element 'kamera_mehr_umschalten' nicht auf dem Bildschirm gefunden",
                      "Handy nicht per USB verbunden.", "Antwort nach 4 Versuchen aufgegeben: uiautomator dump lieferte nichts"):
            self.assertFalse(flammen._dauerhaft(grund), grund)

    def test_stoerung_nennt_den_grund_des_helfers(self):
        self.fehlschlag(JETZT - timedelta(hours=5))
        self.assertEqual(self.stoerung_melden.call_count, 1)
        self.assertIn("Send-to-Liste", self.stoerung_melden.call_args.args[2])

    def test_namensfehler_pausiert_die_flamme_bis_zur_namensaenderung(self):
        zeit = JETZT - timedelta(hours=5)
        self.fehlschlag(zeit)
        p = flammen.zustand_lesen()["personen"][self.p["id"]]
        self.assertTrue(p["gesperrt"])                                  # ein Fehlversuch reicht: der Helfer hat es 8-mal versucht
        self.assertIsNone(p["pause_bis"])                               # keine „schau nach dem Handy“-Pause
        self.assertEqual(self.senden.call_count, 1)                     # aber Eike erfährt den Grund
        self.assertIn("Send-to-Liste", self.senden.call_args.args[2])
        self.assertIn("pausiere die Flamme", self.senden.call_args.args[2])

        tag_spaeter = zeit + timedelta(days=1)
        self.assertEqual(lauf(flammen.einmal(tag_spaeter, bild)), [])   # kein täglicher Neuversuch
        self.assertEqual(len(self.auftraege), 1)

        register.aendern(self.p["id"], snapchat="maya_neu")             # Eike korrigiert den Snapchat-Namen
        self.assertEqual(lauf(flammen.einmal(tag_spaeter + timedelta(minutes=5), bild)), [self.p["id"]])

    def test_pausieren_und_weiter_aktivieren_hebt_die_sperre_auf(self):
        """Der Push sagt „bis du den Snapchat-Namen änderst oder die Freundschaft prüfst“. Prüft Eike die
        Freundschaft, bleibt der Name gleich — dann muss Pausieren/Weiteraktivieren den Versuch wieder erlauben."""
        zeit = JETZT - timedelta(hours=5)
        self.fehlschlag(zeit)
        self.assertTrue(flammen.zustand_lesen()["personen"][self.p["id"]]["gesperrt"])

        register.aendern(self.p["id"], pausiert=True)
        naechster_tag = zeit + timedelta(days=1)
        self.assertEqual(lauf(flammen.einmal(naechster_tag, bild)), [])            # pausiert: kein Snap
        self.assertNotIn("gesperrt", flammen.zustand_lesen()["personen"][self.p["id"]])

        register.aendern(self.p["id"], pausiert=False)
        self.assertEqual(lauf(flammen.einmal(naechster_tag + timedelta(minutes=5), bild)), [self.p["id"]])

    def test_umbenennen_waehrend_des_auftrags_sperrt_den_neuen_namen_nicht(self):
        """Der Helfer protokolliert den Empfänger des Auftrags. Benennt Eike die Person um, bevor das Ergebnis da
        ist, gehört die Sperre zum alten Namen — der neue darf nicht blockiert sein."""
        zeit = JETZT - timedelta(hours=5)
        lauf(flammen.einmal(zeit, bild))                                 # Auftrag mit „maya_x“ ist offen
        register.aendern(self.p["id"], snapchat="maya_neu")              # Eike korrigiert den Namen schon vorher
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer",
                        return_value={"ergebnis": "verworfen", "empfaenger": "maya_x",
                                      "grund": "Empfänger „maya_x“ steht nicht in der Kontaktliste — Auftrag verworfen."}):
            z = flammen.zustand_lesen()
            flammen.abrechnen(z, zeit, melden=True)
            flammen.zustand_schreiben(z)
        self.assertEqual(lauf(flammen.einmal(zeit + timedelta(days=1), bild)), [self.p["id"]])

    def test_erfolg_nach_der_korrektur_loescht_die_sperre(self):
        zeit = JETZT - timedelta(hours=5)
        self.fehlschlag(zeit)
        register.aendern(self.p["id"], snapchat="maya_neu")
        self.assertEqual(lauf(flammen.einmal(zeit + timedelta(days=1), bild)), [self.p["id"]])
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "gesendet"}):
            z = flammen.zustand_lesen()
            flammen.abrechnen(z, zeit + timedelta(days=1), melden=True)
        self.assertNotIn("gesperrt", z["personen"][self.p["id"]])

    def test_technischer_fehler_blockiert_nicht_sondern_pausiert_wie_bisher(self):
        for i in range(3):
            self.fehlschlag(JETZT - timedelta(hours=5) + timedelta(minutes=10 * i),
                            grund="Element 'kamera_mehr_umschalten' nicht auf dem Bildschirm gefunden")
        p = flammen.zustand_lesen()["personen"][self.p["id"]]
        self.assertNotIn("gesperrt", p)
        self.assertIsNotNone(p["pause_bis"])
        self.assertIn("schau bitte nach dem Handy", self.senden.call_args.args[2])


class Standardbild(Basis):
    def test_flammen_wollen_hochkant(self):
        with mock.patch("kern.dienste.spassbild.finden", mock.AsyncMock(return_value=None)) as f:
            lauf(flammen._standardbild(self.p["id"], "Kontakt B"))
        self.assertTrue(f.call_args.kwargs["hochkant"])


class Review(Basis):
    """Befunde der Prüfung vom 14.10.2026."""

    def offen(self, zeit=JETZT):
        lauf(flammen.einmal(zeit, bild))

    def test_unbestaetigt_zaehlt_als_gesendet_und_legt_nicht_doppelt(self):
        self.offen()
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "unbestaetigt"}):
            self.assertEqual(lauf(flammen.einmal(JETZT + timedelta(minutes=5), bild)), [])
        p = flammen.zustand_lesen()["personen"][self.p["id"]]
        self.assertEqual(p["letzter_tag"], "2026-10-14")
        self.assertEqual(p["fehler"], 0)
        self.assertEqual(len(self.auftraege), 1)

    def test_letzter_ist_die_zeit_des_helfers(self):
        self.offen()
        gesendet = (JETZT + timedelta(minutes=1)).timestamp()
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "gesendet", "zeit": gesendet}):
            z = flammen.zustand_lesen()
            flammen.abrechnen(z, JETZT + timedelta(days=3))              # lange nicht abgerechnet (Schalter war aus)
        self.assertTrue(z["personen"][self.p["id"]]["letzter"].startswith("2026-10-14T15:01"))
        self.assertEqual(z["personen"][self.p["id"]]["letzter_tag"], "2026-10-14")

    def test_auftrag_ohne_ergebnis_wird_nach_sechs_stunden_aufgegeben_ohne_neuversuch_am_selben_tag(self):
        self.offen(JETZT - timedelta(hours=5))
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value=None):
            lauf(flammen.einmal(JETZT, bild))                              # 5 h: noch gesperrt
            self.assertEqual(flammen.zustand_lesen()["personen"][self.p["id"]]["auftrag"], "id1")
            spaeter = JETZT + timedelta(hours=2)                           # 7 h
            self.assertEqual(lauf(flammen.einmal(spaeter, bild)), [])
        p = flammen.zustand_lesen()["personen"][self.p["id"]]
        self.assertIsNone(p["auftrag"])
        self.assertEqual(p["letzter_tag"], "2026-10-14")                   # kein zweiter Snap am selben Tag
        self.assertEqual(len(self.auftraege), 1)

    def test_schalter_aus_rechnet_trotzdem_offene_auftraege_ab_aber_meldet_nichts(self):
        self.offen()
        self.setze_modus("aus")
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "aufgegeben"}):
            for i in range(3):
                zust = flammen.zustand_lesen()
                zust["personen"][self.p["id"]]["auftrag"] = f"x{i}"
                flammen.zustand_schreiben(zust)
                lauf(flammen.einmal(JETZT + timedelta(minutes=i), bild))
        self.senden.assert_not_called()
        self.assertIsNone(flammen.zustand_lesen()["personen"][self.p["id"]]["auftrag"])

    def scheitern(self, pid, melden):
        flammen.zustand_schreiben({"personen": {pid: {**flammen._neu(), "auftrag": "w1", "fehler": 2}}})
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "aufgegeben"}):
            z = flammen.zustand_lesen()
            flammen.abrechnen(z, JETZT, melden=melden)
        return z

    def test_pause_meldet_nur_im_modus_an_und_nur_bei_flammenpersonen(self):
        andere = register.anlegen("Lena Test", snapchat="lena_x")      # Wunsch-Person ohne Flamme
        self.scheitern(andere["id"], True)
        self.stoerung_melden.assert_not_called()                       # Person ohne wirksames Flammenmodul
        self.scheitern(self.p["id"], False)
        self.stoerung_melden.assert_not_called()                       # Modus aus/trocken
        self.scheitern(self.p["id"], True)
        self.stoerung_melden.assert_called_once()                      # Modus an + Flammenperson
        self.senden.assert_called_once()                               # und genau ein Push „schau nach dem Handy“

    def test_erledigt_nur_im_modus_an(self):
        self.offen()
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "gesendet"}):
            flammen.abrechnen(flammen.zustand_lesen(), JETZT + timedelta(minutes=3), melden=False)
        self.stoerung_erledigt.assert_not_called()

    def test_stoerung_wirft_flammenlauf_laeuft_weiter(self):
        self.stoerung_melden.side_effect = RuntimeError("Ablage kaputt")
        z = self.scheitern(self.p["id"], True)
        p = z["personen"][self.p["id"]]
        self.assertEqual(p["fehler"], 0)
        self.assertIsNotNone(p["pause_bis"])
        self.assertIsNone(p["auftrag"])

    def test_kein_snapchat_schreibrecht_dann_kein_flammen_snap(self):
        for benutzbar, schreiben in ((False, True), (True, False)):
            with mock.patch("kern.personen.flammen.konnektoren.benutzbar", return_value=benutzbar), \
                    mock.patch("kern.personen.flammen.konnektoren.schreiben_erlaubt", return_value=schreiben):
                self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [])
        self.assertEqual(self.auftraege, [])

    def test_reservierung_haelt_waehrend_das_bild_gesucht_wird_und_nichts_geht_verloren(self):
        andere = register.anlegen("Lena Test", snapchat="lena_x")
        register.aendern(andere["id"], module={"flammen": True}, pausiert=True)        # der Lauf lässt sie aus, der Wunsch-Snap geht trotzdem
        zwischen = {}

        async def langsam(pid, name):
            # Währenddessen: derselbe Lauf noch einmal (z. B. zweiter Takt) und ein Wunsch-Snap an eine andere Person
            zwischen["zweiter_lauf"] = await flammen.einmal(JETZT, bild)
            flammen.merken_auftrag(andere["id"], "wunsch-1", JETZT)
            return (b"JPEG", "https://beispiel.test/c.jpg", "Hund")
        self.assertEqual(lauf(flammen.einmal(JETZT, langsam)), [self.p["id"]])
        self.assertEqual(zwischen["zweiter_lauf"], [])                    # Person reserviert → kein zweiter Snap
        z = flammen.zustand_lesen()["personen"]
        self.assertEqual(z[self.p["id"]]["auftrag"], "id1")
        self.assertEqual(z[andere["id"]]["auftrag"], "wunsch-1")          # der Wunsch-Eintrag überlebt den Lauf

    def test_haengende_reservierung_wird_wieder_frei(self):
        zust = {"personen": {self.p["id"]: {**flammen._neu(), "auftrag": flammen.RESERVIERT,
                                            "auftrag_zeit": (JETZT - timedelta(minutes=16)).isoformat()}}}
        flammen.zustand_schreiben(zust)
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [self.p["id"]])

    def test_fehler_beim_bild_gibt_die_reservierung_frei(self):
        async def kaputt(pid, name):
            raise RuntimeError("Netz weg")
        self.assertEqual(lauf(flammen.einmal(JETZT, kaputt)), [])
        self.assertIsNone(flammen.zustand_lesen()["personen"][self.p["id"]]["auftrag"])
        self.assertEqual(lauf(flammen.einmal(JETZT + timedelta(minutes=5), bild)), [])                  # noch in der 10-Minuten-Pause
        self.assertEqual(lauf(flammen.einmal(JETZT + timedelta(minutes=11), bild)), [self.p["id"]])
