import asyncio, json, os, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from kern import konnektoren
from kern.dienste import snapchat as dienst, snapchat_wache as wache
from kern.gehirn import persona
from kern.protokoll import speicher as pspeicher
from kern.sitzung import chat


def lauf(coro):
    return asyncio.run(coro)


def ergebnis(antwort="Alles klar!", werkzeuge=None):
    return {"antwort": antwort, "werkzeuge": [], "werkzeug_ergebnisse": werkzeuge or [],
            "aktionen": [], "links": []}


def protokoll_freigeben():
    """`pspeicher.standard()` hält je Datenordner eine offene Datenbank. Ohne Freigeben sammeln sich
    über die ganze Testsuite so viele Dateihandles, dass spätere Module „Too many open files“ melden."""
    for schluessel in list(pspeicher._instanzen):
        try:
            pspeicher._instanzen.pop(schluessel)._db.close()
        except Exception:
            pass


def echtes_png() -> bytes:
    from io import BytesIO
    from PIL import Image
    aus = BytesIO()
    Image.new("RGBA", (8, 8), (10, 120, 200, 255)).save(aus, "PNG")
    return aus.getvalue()


def alt(text, id="a1", art="text", **mehr):
    """Ein Eingangseintrag, der das Bündelfenster längst hinter sich hat."""
    return {"id": id, "art": art, "text": text, "zeit": time.time() - 60, "gelesen": False, **mehr}


class WacheTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        konnektoren.setzen("snapchat", True)
        wache._zurueck.clear()
        wache._gesehen_texte.clear()
        wache._wiederholung_gemeldet.clear()
        wache._unbekannt_gemeldet.clear()

    def tearDown(self):
        protokoll_freigeben()
        self.env.stop()

    def _eingang_setzen(self, nachrichten):
        dienst._schreiben("eingang.json", nachrichten)  # type: ignore[attr-defined]

    def test_die_kontaktliste_des_helfers_folgt_dem_register_auch_ohne_neuen_auftrag(self):
        # Eike pausiert eine Person: Ein schon gelegter Auftrag für sie darf nicht mehr gesendet werden, dafür muss die Liste, die
        # der Helfer liest, sofort stimmen — nicht erst beim nächsten Auftrag (Review Teil 4a, 30.09.2026).
        from kern.personen import register
        pid = register.anlegen("Kontakt B", snapchat="maya_x")["id"]
        dienst.kontakte_schreiben()
        vorher = json.loads((dienst._ordner() / "kontakte.json").read_text(encoding="utf-8"))
        self.assertEqual([k["anzeige"] for k in vorher["kontakte"]], ["Eike", "maya_x"])
        register.aendern(pid, pausiert=True)
        lauf(wache.einmal())
        nachher = json.loads((dienst._ordner() / "kontakte.json").read_text(encoding="utf-8"))
        self.assertEqual([k["anzeige"] for k in nachher["kontakte"]], ["Eike"])

    def test_ohne_konnektor_tut_nichts(self):
        konnektoren.setzen("snapchat", False)
        self._eingang_setzen([alt("Hi")])
        with mock.patch.object(wache, "_antworten") as m:
            lauf(wache.einmal())
        m.assert_not_called()

    def test_neue_nachricht_wird_beantwortet_und_als_gelesen_markiert(self):
        self._eingang_setzen([alt("Kommst du mit?")])
        with mock.patch.object(wache, "_antworten",
                               mock.AsyncMock(return_value=ergebnis("Na klar, bin dabei!"))) as m, \
             mock.patch.object(dienst, "auftrag_legen") as m_legen:
            lauf(wache.einmal())
        m.assert_awaited_once()
        m_legen.assert_called_once_with("text", text="Na klar, bin dabei!", ausloeser=mock.ANY)
        self.assertEqual(dienst.eingang_neu(), [])

    def test_werkzeug_snap_belegt_den_auftragsplatz_antwort_wird_nachgelegt_und_zug_nicht_wiederholt(self):
        # Am 29.09.2026: Der Snap des Werkzeugs belegte den Auftragsplatz, die Textantwort scheiterte, der Zug
        # blieb ungelesen und lief in jedem Takt erneut — mit jedem Lauf ein weiterer Snap.
        wache._nachzulegen.clear()
        wache._zurueck.clear()
        wache._gesehen_texte.clear()
        wache._wiederholung_gemeldet.clear()
        wache._unbekannt_gemeldet.clear()
        self._eingang_setzen([alt("Schick mir einen Snap")])
        voll = dienst.SnapchatFehler("Es liegt schon ein Sendeauftrag, den der Helfer noch nicht abgeholt hat.")
        mit_snap = ergebnis("Ist unterwegs!")
        mit_snap["werkzeuge"] = ["schicke_snap"]
        # Oben ist der Platz frei; nach der Antwortrunde liegt der Snap des Werkzeugs dort.
        with mock.patch.object(wache, "_antworten", mock.AsyncMock(return_value=mit_snap)) as m, \
             mock.patch.object(dienst, "auftrag_legen", side_effect=voll):
            lauf(wache.einmal())
            lauf(wache.einmal())
        m.assert_awaited_once()                       # der Zug lief NICHT noch einmal
        self.assertEqual(dienst.eingang_neu(), [])    # und gilt als bearbeitet
        self.assertEqual(wache._nachzulegen, ["Ist unterwegs!"])
        # Der Helfer hat den Snap abgeholt: jetzt geht die Textantwort raus, genau einmal.
        with mock.patch.object(dienst, "auftraege_anzahl", return_value=1), \
             mock.patch.object(dienst, "auftrag_legen") as m_legen:
            lauf(wache.einmal())
            lauf(wache.einmal())
        m_legen.assert_called_once_with("text", text="Ist unterwegs!")
        self.assertEqual(wache._nachzulegen, [])

    def test_alter_eintrag_ohne_art_gilt_als_text(self):
        self._eingang_setzen([{"id": "a1", "text": "Hi", "zeit": 1.0, "gelesen": False}])
        nachrichten, namen = wache._zug_bauen(dienst.eingang_neu())
        self.assertEqual(nachrichten, [{"rolle": "du", "text": "Hi"}])
        self.assertEqual(namen, [])

    def test_ohne_neue_nachrichten_tut_nichts(self):
        with mock.patch.object(wache, "_antworten") as m:
            lauf(wache.einmal())
        m.assert_not_called()

    def test_fehler_beim_antworten_darf_lauf_nicht_toeten(self):
        self._eingang_setzen([{"id": "a1", "art": "text", "text": "Hi", "zeit": time.time() - 20, "gelesen": False}])
        with mock.patch.object(wache, "_antworten", mock.AsyncMock(side_effect=chat.ChatFehler("kaputt"))):
            lauf(wache.einmal())  # darf nicht werfen
        self.assertEqual(len(dienst.eingang_neu()), 1)  # bleibt ungelesen, nächster Lauf versucht's nochmal

    def test_lange_unbeantwortete_nachricht_bekommt_notfallantwort(self):
        self._eingang_setzen([{"id": "a1", "art": "text", "text": "Hi", "zeit": 1.0, "gelesen": False}])
        with mock.patch.object(wache, "_antworten", mock.AsyncMock(side_effect=chat.ChatFehler("kaputt"))), \
             mock.patch.object(dienst, "auftrag_legen") as m_legen:
            lauf(wache.einmal())
        m_legen.assert_called_once_with("text", text=wache.NOTFALL_ANTWORT, ausloeser=mock.ANY)
        self.assertEqual(dienst.eingang_neu(), [])

    def test_stiller_helfer_wird_als_nicht_verbunden_vermerkt(self):
        veraltet = {"verbunden": True, "zeit": time.time() - 3600, "letzter_fehler": None}
        with mock.patch.object(dienst, "zustand", return_value=veraltet), \
             mock.patch.object(wache.config, "jetzt",
                               return_value=datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)), \
             mock.patch.object(konnektoren, "vermerken") as m_vermerken:
            wache._vermerkt.clear()
            lauf(wache.einmal())
        self.assertFalse(m_vermerken.call_args.args[1])

    def test_ueberspringt_wenn_bereits_ein_auftrag_wartet(self):
        self._eingang_setzen([alt("Hi")])
        with mock.patch.object(dienst, "auftraege_anzahl", return_value=dienst.QUEUE_MAX + 1), \
             mock.patch.object(wache, "_antworten") as m:
            lauf(wache.einmal())
        m.assert_not_called()

    def test_antwortet_solange_die_schlange_nicht_voll_ist(self):
        self._eingang_setzen([alt("Hi")])
        dienst.auftrag_legen("snap", bild_pfad="snapchat/ausgang/a.jpg")     # der Platz ist belegt
        with mock.patch.object(wache, "_antworten", mock.AsyncMock(return_value=ergebnis("Hallo!"))) as m:
            lauf(wache.einmal())
        m.assert_awaited_once()
        self.assertEqual(dienst.auftraege_anzahl(), 2)                        # die Antwort wartet hinter dem Snap

    def test_ueberspringt_wenn_sendegrenze_erreicht(self):
        self._eingang_setzen([alt("Hi")])
        with mock.patch.object(dienst, "sende_zaehler", return_value=dienst.SENDE_GRENZE), \
             mock.patch.object(wache, "_antworten") as m:
            lauf(wache.einmal())
        m.assert_not_called()

    def test_leere_antwort_wird_nicht_als_auftrag_gelegt(self):
        self._eingang_setzen([alt("Hi")])
        with mock.patch.object(wache, "_antworten", mock.AsyncMock(return_value=ergebnis(""))), \
             mock.patch.object(dienst, "auftrag_legen") as m_legen:
            lauf(wache.einmal())
        m_legen.assert_not_called()
        self.assertEqual(len(dienst.eingang_neu()), 1)  # nicht als gelesen markiert

    def test_zustand_wird_auf_freigaben_seite_vermerkt(self):
        with mock.patch.object(dienst, "zustand",
                               return_value={"verbunden": False, "letzter_fehler": "Handy nicht verbunden."}), \
             mock.patch.object(konnektoren, "vermerken") as m_vermerken:
            wache._vermerkt.clear()
            lauf(wache.einmal())
        m_vermerken.assert_called_once_with("snapchat", False, "Handy nicht verbunden.")

    # ---- Sammelzug ----

    def test_zug_bauen_buendelt_texte_und_haengt_snaps_als_bild_an(self):
        bild = "b" * 32 + ".png"
        eintraege = [
            {"id": "1", "art": "text", "text": "Ich schick dir gleich ein Bild", "zeit": 1.0},
            {"id": "2", "art": "text", "text": "sag was das ist", "zeit": 2.0},
            {"id": "3", "art": "snap", "text": "[Snap]", "bild": bild, "fehler": None, "zeit": 3.0},
        ]
        with mock.patch.object(dienst, "bild_lesen", return_value=echtes_png()):
            nachrichten, namen = wache._zug_bauen(eintraege)
        self.assertEqual(nachrichten[0]["text"], "Ich schick dir gleich ein Bild\nsag was das ist")
        self.assertEqual(nachrichten[1]["bild"]["mime"], "image/jpeg")
        self.assertEqual(namen, [bild])

    def test_zug_bauen_haengt_chatbild_an_und_nennt_es_bild_im_chat(self):
        bild = "c" * 32 + ".png"
        with mock.patch.object(dienst, "bild_lesen", return_value=echtes_png()):
            nachrichten, namen = wache._zug_bauen(
                [{"id": "1", "art": "chatbild", "text": "[Bild]", "bild": bild, "fehler": None, "zeit": 1.0}])
        self.assertIn("Bild im Chat", nachrichten[-1]["text"])
        self.assertEqual(nachrichten[-1]["bild"]["mime"], "image/jpeg")
        self.assertEqual(namen, [bild])

    def test_zug_bauen_sagt_friday_wenn_sie_das_bild_kennt(self):
        alt = {"id": "a" * 32, "name": "Calvin-Zyklus", "beschreibung": "Ein Schema.", "notizen": ""}
        eintrag = {"id": "1", "art": "chatbild", "text": "[Bild]", "bild": "d" * 32 + ".png", "fehler": None,
                   "zeit": 1.0}
        with mock.patch.object(dienst, "bild_lesen", return_value=echtes_png()), \
             mock.patch.object(wache.bilder, "gleiches", return_value=alt):
            nachrichten, _ = wache._zug_bauen([eintrag])
        self.assertIn("kennst du schon", nachrichten[-1]["text"])
        self.assertIn("a" * 32, nachrichten[-1]["text"])
        self.assertIn("Calvin-Zyklus", nachrichten[-1]["text"])

    def test_zug_bauen_snap_ohne_bild_wird_ehrlich_gemeldet(self):
        eintraege = [{"id": "1", "art": "snap", "text": "[Snap]", "bild": None,
                      "fehler": "Bildfläche fehlt", "zeit": 1.0}]
        nachrichten, namen = wache._zug_bauen(eintraege)
        self.assertIn("nicht lesen", nachrichten[-1]["text"])
        self.assertEqual(namen, [])

    def test_text_und_snap_im_selben_fenster_ergeben_einen_auftrag(self):
        self._eingang_setzen([alt("Was ist das?", id="1"),
                              alt("[Snap]", id="2", art="snap", bild=None, fehler="x")])
        with mock.patch.object(wache, "_antworten",
                               mock.AsyncMock(return_value=ergebnis("Das ist ein Test"))) as m, \
             mock.patch.object(dienst, "auftrag_legen") as m_legen:
            lauf(wache.einmal())
        m.assert_awaited_once()
        m_legen.assert_called_once_with("text", text="Das ist ein Test", ausloeser=mock.ANY)
        self.assertEqual(dienst.eingang_neu(), [])

    def test_frische_eintraege_werden_erst_nach_dem_buendelfenster_beantwortet(self):
        self._eingang_setzen([{"id": "1", "art": "text", "text": "Hi", "zeit": time.time(), "gelesen": False}])
        with mock.patch.object(wache, "_antworten", mock.AsyncMock()) as m:
            lauf(wache.einmal())
        m.assert_not_awaited()
        self.assertEqual(len(dienst.eingang_neu()), 1)

    def test_neuer_eintrag_startet_das_fenster_neu(self):
        self._eingang_setzen([alt("Erste", id="1"),
                              {"id": "2", "art": "text", "text": "Zweite", "zeit": time.time(), "gelesen": False}])
        with mock.patch.object(wache, "_antworten", mock.AsyncMock()) as m:
            lauf(wache.einmal())
        m.assert_not_awaited()

    def test_snap_bild_geht_mit_quelle_und_sachlicher_beschreibung_ins_gedaechtnis(self):
        name = "c" * 32 + ".png"
        self._eingang_setzen([alt("[Snap]", art="snap", bild=name, fehler=None)])
        with mock.patch.object(dienst, "bild_lesen", return_value=echtes_png()), \
             mock.patch.object(dienst, "bild_entfernen") as m_weg, \
             mock.patch.object(wache, "_antworten", mock.AsyncMock(return_value=ergebnis("Sieht lustig aus!"))), \
             mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(wache.bildauswertung, "auswerten",
                               mock.AsyncMock(return_value={"beschreibung": "Ein Foto von einem Monitor.",
                                                            "personen": 0, "wer": None})) as m_sachlich, \
             mock.patch.object(wache.bilder, "speichern", return_value="f" * 32) as m_speichern, \
             mock.patch.object(wache.bilder, "beschreiben") as m_beschreiben:
            lauf(wache.einmal())
        self.assertEqual(m_speichern.call_args.args[1], "image/jpeg")
        self.assertEqual(m_speichern.call_args.kwargs["quelle"], "Snapchat · Eike")
        # Die Beschreibung ist die sachliche, NICHT Fridays Antwort.
        m_beschreiben.assert_called_once_with("f" * 32, "Ein Foto von einem Monitor.")
        self.assertEqual(m_sachlich.await_args.args[2], "Snapchat · Eike")
        m_weg.assert_called_once_with(name)

    def test_nachricht_nennt_die_person_dann_wird_sie_dem_bild_zugeordnet(self):
        name = "c" * 32 + ".png"
        self._eingang_setzen([alt("Das ist Kontakt B", id="1"), alt("[Snap]", id="2", art="snap", bild=name, fehler=None)])
        with mock.patch.object(dienst, "bild_lesen", return_value=echtes_png()), \
             mock.patch.object(dienst, "bild_entfernen"), \
             mock.patch.object(wache, "_antworten", mock.AsyncMock(return_value=ergebnis("Hi Kontakt B!"))), \
             mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(wache.bildauswertung, "auswerten",
                               mock.AsyncMock(return_value={"beschreibung": "Eine Frau.", "personen": 1,
                                                            "wer": "Kontakt B"})) as m_aus, \
             mock.patch.object(wache.bilder, "speichern", return_value="f" * 32), \
             mock.patch.object(wache.bilder, "beschreiben"), \
             mock.patch.object(wache.bildpersonen, "zuordnen") as m_zu:
            lauf(wache.einmal())
        self.assertEqual(m_aus.await_args.args[3], "Das ist Kontakt B")  # Eikes Nachricht geht in die Auswertung
        self.assertEqual(m_zu.call_args.args[0], "f" * 32)
        self.assertEqual(m_zu.call_args.args[2], "Das ist Kontakt B")

    def _selfie_lauf(self, selfie, status="gesendet", art="snap", text_davor=False, selfie_fehler=None):
        name = "c" * 32 + ".png"
        eingang = [alt("[Snap]", art=art, bild=name, fehler=None, id="2")]
        if text_davor:
            eingang.insert(0, alt("Schau mal", id="1"))
        self._eingang_setzen(eingang)
        antwort = mock.AsyncMock(return_value=ergebnis("Hi!"))
        aus = mock.AsyncMock(return_value={"beschreibung": "Ein Selfie.", "personen": 1, "wer": None, "selfie": selfie})
        selfie_mock = mock.AsyncMock(return_value=status, side_effect=selfie_fehler)
        with mock.patch.object(dienst, "bild_lesen", return_value=echtes_png()), \
             mock.patch.object(dienst, "bild_entfernen"), \
             mock.patch.object(wache, "_antworten", antwort), \
             mock.patch.object(dienst, "auftrag_legen") as m_auftrag, \
             mock.patch.object(wache.bildauswertung, "auswerten", aus), \
             mock.patch.object(wache.bilder, "speichern", return_value="f" * 32), \
             mock.patch.object(wache.bilder, "beschreiben"), \
             mock.patch.object(wache.friday_selfie, "antworten", selfie_mock):
            lauf(wache.einmal())
        return antwort, m_auftrag, selfie_mock, aus

    def test_selfie_snap_wird_nur_mit_selfie_beantwortet_nicht_zusaetzlich_mit_text(self):
        antwort, auftrag, selfie, aus = self._selfie_lauf(True)
        selfie.assert_awaited_once()
        self.assertIsInstance(selfie.await_args.args[1], bytes)
        self.assertEqual(selfie.await_args.args[2], "Ein Selfie.")
        antwort.assert_not_awaited()          # kein Textmodell-Aufruf
        auftrag.assert_not_called()           # und kein Text-Auftrag (das Selfie legt der Selfie-Baustein, hier gemockt)
        self.assertEqual(aus.await_count, 1)  # die Bildauswertung läuft nur einmal je Bild
        self.assertTrue(json.loads((dienst._ordner() / "eingang.json").read_text(encoding="utf-8"))[0]["gelesen"])

    def test_selfie_das_nicht_rausgeht_wird_stattdessen_mit_text_beantwortet(self):
        for status in ("limit", "aus", "fehler", "ruhezeit", "belegt", "nicht_erlaubt", "zu_frueh"):
            antwort, auftrag, selfie, aus = self._selfie_lauf(True, status=status)
            antwort.assert_awaited_once()
            auftrag.assert_called()
            self.assertEqual(aus.await_count, 1)

    def test_kein_selfie_oder_chatbild_oder_snap_mit_text_gibt_normale_textantwort(self):
        for kw in ({"selfie": False}, {"selfie": True, "art": "chatbild"}, {"selfie": True, "text_davor": True}):
            antwort, auftrag, selfie, _ = self._selfie_lauf(**kw)
            selfie.assert_not_awaited()
            antwort.assert_awaited_once()

    def test_selfie_fehler_faellt_auf_text_zurueck(self):
        antwort, auftrag, selfie, _ = self._selfie_lauf(True, selfie_fehler=RuntimeError("x"))
        antwort.assert_awaited_once()
        self.assertTrue(json.loads((dienst._ordner() / "eingang.json").read_text(encoding="utf-8"))[0]["gelesen"])

    def test_nicht_umwandelbares_bild_wird_als_nicht_lesbar_gemeldet(self):
        with mock.patch.object(dienst, "bild_lesen", return_value=b"kein png"):
            nachrichten, namen = wache._zug_bauen([{"id": "1", "art": "snap", "text": "[Snap]",
                                                    "bild": "a" * 32 + ".png", "zeit": 1.0}])
        self.assertIn("nicht lesen", nachrichten[-1]["text"])
        self.assertEqual(namen, [])

    def test_ohne_sachliche_beschreibung_bleibt_das_bild_unbeschrieben_statt_fridays_antwort(self):
        name = "c" * 32 + ".png"
        self._eingang_setzen([alt("[Snap]", art="snap", bild=name, fehler=None)])
        with mock.patch.object(dienst, "bild_lesen", return_value=echtes_png()), \
             mock.patch.object(dienst, "bild_entfernen"), \
             mock.patch.object(wache, "_antworten", mock.AsyncMock(return_value=ergebnis("Sieht lustig aus!"))), \
             mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(wache.bildauswertung, "auswerten", mock.AsyncMock(return_value=None)), \
             mock.patch.object(wache.bilder, "speichern", return_value="f" * 32), \
             mock.patch.object(wache.bilder, "beschreiben") as m_beschreiben:
            lauf(wache.einmal())
        m_beschreiben.assert_not_called()

    def test_notfallantwort_speichert_das_bild_trotzdem(self):
        name = "d" * 32 + ".png"
        self._eingang_setzen([{"id": "1", "art": "snap", "text": "[Snap]", "bild": name,
                               "fehler": None, "zeit": 1.0, "gelesen": False}])
        with mock.patch.object(dienst, "bild_lesen", return_value=echtes_png()), \
             mock.patch.object(dienst, "bild_entfernen"), \
             mock.patch.object(wache, "_antworten", mock.AsyncMock(side_effect=chat.ChatFehler("kaputt"))), \
             mock.patch.object(dienst, "auftrag_legen") as m_legen, \
             mock.patch.object(wache.bildauswertung, "auswerten", mock.AsyncMock(return_value={"beschreibung": "Ein Bild.", "personen": 0, "wer": None})), \
             mock.patch.object(wache.bilder, "speichern", return_value="e" * 32) as m_speichern, \
             mock.patch.object(wache.bilder, "beschreiben"):
            lauf(wache.einmal())
        m_legen.assert_called_once_with("text", text=wache.NOTFALL_ANTWORT, ausloeser=mock.ANY)
        m_speichern.assert_called_once()


class AntwortenTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        protokoll_freigeben()
        self.env.stop()

    def test_antwort_kommt_vom_web_motor_mit_kanal_und_ohne_aktionen(self):
        roh = {"antwort": "Hi!", "werkzeuge": ["uhrzeit"], "werkzeug_ergebnisse": [],
               "aktionen": [{"art": "oeffnen"}], "links": []}
        with mock.patch.object(wache, "_verlauf", return_value=[]), \
             mock.patch.object(chat, "antworten", mock.AsyncMock(return_value=roh)) as m:
            ergebnis_ = lauf(wache._antworten([{"art": "text", "text": "Wie spät?"}]))
        self.assertEqual(ergebnis_["antwort"], "Hi!")
        self.assertEqual(ergebnis_["aktionen"], [])
        self.assertEqual(m.await_args.kwargs["kanal"], persona.SNAPCHAT_KANAL)
        self.assertEqual(m.await_args.args[0], [{"rolle": "du", "text": "Wie spät?"}])

    def test_gesendete_snaps_gehen_als_block_in_den_kanal(self):
        # Eike öffnet den Snap Stunden später und antwortet: Friday muss wissen, was sie geschickt hat.
        with mock.patch.object(wache, "_verlauf", return_value=[]), \
             mock.patch.object(dienst, "gesendet_kontext", return_value="\n\n[GESENDETE SNAPS] - vor 5 Std.: ein Fuchs"), \
             mock.patch.object(chat, "antworten", mock.AsyncMock(return_value=ergebnis("ok"))) as m:
            lauf(wache._antworten([{"art": "text", "text": "haha süß"}]))
        kanal = m.await_args.kwargs["kanal"]
        self.assertTrue(kanal.startswith(persona.SNAPCHAT_KANAL))
        self.assertIn("ein Fuchs", kanal)

    def test_verlauf_geht_in_die_anfrage_mit(self):
        verlauf = [{"rolle": "du", "text": "Wer ist Kontakt S"}, {"rolle": "friday", "text": "Ein Freund von dir."}]
        with mock.patch.object(wache, "_verlauf", return_value=verlauf), \
             mock.patch.object(chat, "antworten", mock.AsyncMock(return_value=ergebnis("ok"))) as m:
            lauf(wache._antworten([{"art": "text", "text": "Und Muster?"}]))
        nachrichten = m.await_args.args[0]
        self.assertEqual(len(nachrichten), 3)
        self.assertEqual(nachrichten[-1]["text"], "Und Muster?")

    def test_antwort_bekommt_den_laufenden_faden_fuer_bild_werkzeuge(self):
        with mock.patch.object(wache, "_verlauf", return_value=[]), \
             mock.patch.object(wache, "_faden_vorhanden", return_value=42), \
             mock.patch.object(chat, "antworten", mock.AsyncMock(return_value=ergebnis("ok"))) as m:
            lauf(wache._antworten([{"art": "text", "text": "Das war Kontakt E"}]))
        self.assertEqual(m.await_args.kwargs["gespraech"], 42)

    def test_faden_vorhanden_legt_keinen_an(self):
        self.assertIsNone(wache._faden_vorhanden())
        self.assertEqual([g for g in pspeicher.standard().gespraeche(limit=50) if g["art"] == wache.ART], [])

    def test_chatfehler_geht_nach_oben(self):
        with mock.patch.object(wache, "_verlauf", return_value=[]), \
             mock.patch.object(chat, "antworten", mock.AsyncMock(side_effect=chat.ChatFehler("aus"))):
            with self.assertRaises(chat.ChatFehler):
                lauf(wache._antworten([{"art": "text", "text": "Hi"}]))


class ProtokollTest(unittest.TestCase):
    """Jeder beantwortete Zug muss im Gesprächsprotokoll stehen — sonst ist er
    nirgends nachweisbar (nicht in der Gesprächsliste, nicht im Rückblick, nicht
    im Langzeitgedächtnis)."""

    def setUp(self):
        wache._zurueck.clear()
        wache._gesehen_texte.clear()
        wache._wiederholung_gemeldet.clear()
        wache._unbekannt_gemeldet.clear()
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        konnektoren.setzen("snapchat", True)

    def tearDown(self):
        protokoll_freigeben()
        self.env.stop()

    def _lauf(self, ein, aus="Alles klar!") -> None:
        dienst._schreiben("eingang.json", [alt(ein)])
        with mock.patch.object(wache, "_antworten", mock.AsyncMock(return_value=ergebnis(aus))):
            lauf(wache.einmal())
        # Der Host-Helfer holt den Auftrag ab — sonst blockiert er den nächsten Lauf.
        (Path(self.ordner) / "snapchat" / "auftrag.json").unlink(missing_ok=True)

    def _faeden(self) -> list[dict]:
        return [g for g in pspeicher.standard().gespraeche(limit=50) if g["art"] == wache.ART]

    def test_dialog_steht_als_gespraech_im_protokoll(self):
        self._lauf("Wie geht es dir?")
        faeden = self._faeden()
        self.assertEqual(len(faeden), 1)
        self.assertEqual([(b["rolle"], b["text"]) for b in pspeicher.standard().beitraege(faeden[0]["id"])],
                         [("du", "Wie geht es dir?"), ("friday", "Alles klar!")])

    def test_werkzeuge_stehen_als_systembeitrag_im_protokoll(self):
        dienst._schreiben("eingang.json", [alt("Wie spät?")])
        mit = ergebnis("Kurz nach drei", [{"name": "uhrzeit", "text": "15:02"}])
        with mock.patch.object(wache, "_antworten", mock.AsyncMock(return_value=mit)):
            lauf(wache.einmal())
        beitraege = pspeicher.standard().beitraege(self._faeden()[0]["id"])
        self.assertIn(("system", "Werkzeug uhrzeit: 15:02"), [(b["rolle"], b["text"]) for b in beitraege])

    def test_faden_wird_ueber_laeufe_wiederverwendet(self):
        self._lauf("Hallo?", "Hi!")
        self._lauf("Und du?", "Sehr gut!")
        faeden = self._faeden()
        self.assertEqual(len(faeden), 1)  # keine Flut in der Gesprächsliste
        self.assertEqual(len(pspeicher.standard().beitraege(faeden[0]["id"])), 4)

    def test_nach_ruhezeit_waechst_ein_neuer_faden(self):
        self._lauf("Hallo?", "Hi!")
        spaeter = datetime.now(timezone.utc) + timedelta(minutes=wache.RUHE_MINUTEN + 5)
        with mock.patch.object(dienst, "datetime") as m_datum:
            m_datum.now.return_value = spaeter
            self._lauf("Und du?", "Sehr gut!")
        self.assertEqual(len(self._faeden()), 2)

    def test_ruhezeit_ist_25_minuten(self):
        self.assertEqual(wache.RUHE_MINUTEN, 25)

    def test_verlauf_kommt_aus_dem_laufenden_faden_und_ist_leer_nach_der_ruhe(self):
        self._lauf("Wer ist Kontakt S?", "Ein Freund.")
        self.assertEqual(wache._verlauf(), [{"rolle": "du", "text": "Wer ist Kontakt S?"},
                                            {"rolle": "friday", "text": "Ein Freund."}])
        spaeter = datetime.now(timezone.utc) + timedelta(minutes=wache.RUHE_MINUTEN + 5)
        with mock.patch.object(dienst, "datetime") as m_datum:
            m_datum.now.return_value = spaeter
            self.assertEqual(wache._verlauf(), [])

    def test_app_chat_haengt_nie_an_einem_snapchat_faden(self):
        self._lauf("Hallo?", "Hi!")
        # `/api/chat` prüft genau damit: ein Chat darf keinen Snap-Faden fortsetzen.
        self.assertFalse(pspeicher.standard().gibt_es(self._faeden()[0]["id"], "chat"))

    def test_ohne_antwort_kein_protokolleintrag(self):
        self._lauf("Hallo?", "")
        self.assertEqual(self._faeden(), [])

    def test_antwortfehler_kein_protokolleintrag(self):
        dienst._schreiben("eingang.json", [{"id": "a1", "art": "text", "text": "Hallo?",
                                            "zeit": time.time() - 20, "gelesen": False}])
        with mock.patch.object(wache, "_antworten", mock.AsyncMock(side_effect=chat.ChatFehler("kaputt"))):
            lauf(wache.einmal())
        self.assertEqual(self._faeden(), [])

    def test_volle_schlange_protokolliert_nichts(self):
        dienst._schreiben("eingang.json", [alt("Hallo?")])
        with mock.patch.object(dienst, "auftraege_anzahl", return_value=dienst.QUEUE_MAX + 1), \
             mock.patch.object(wache, "_antworten") as m:
            lauf(wache.einmal())
        m.assert_not_called()
        self.assertEqual(self._faeden(), [])

    def test_sendegrenze_protokolliert_nichts(self):
        dienst._schreiben("eingang.json", [alt("Hallo?")])
        with mock.patch.object(dienst, "sende_zaehler", return_value=dienst.SENDE_GRENZE), \
             mock.patch.object(wache, "_antworten") as m:
            lauf(wache.einmal())
        m.assert_not_called()
        self.assertEqual(self._faeden(), [])

    def test_fehler_beim_protokollieren_toetet_die_wache_nicht(self):
        dienst._schreiben("eingang.json", [alt("Hallo?")])
        with mock.patch.object(wache, "_antworten", mock.AsyncMock(return_value=ergebnis("Hi!"))), \
             mock.patch.object(pspeicher.Protokoll, "beitrag", side_effect=RuntimeError("DB zu")):
            lauf(wache.einmal())  # darf nicht werfen
        self.assertIsNotNone(dienst.auftrag_lesen())   # der Auftrag liegt
        self.assertEqual(dienst.eingang_neu(), [])      # und die Nachricht gilt als bearbeitet


class PersonenWacheTest(unittest.TestCase):
    """Die Wache beantwortet Einträge einer Person mit dem Personen-Motor, eigenem Faden und Auftrag an genau sie."""

    def setUp(self):
        from kern.personen import register
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        konnektoren.setzen("snapchat", True)
        wache._nachzulegen.clear()
        wache._zurueck.clear()
        wache._gesehen_texte.clear()
        wache._wiederholung_gemeldet.clear()
        wache._unbekannt_gemeldet.clear()
        self.register = register
        self.pid = register.anlegen("Kontakt B", snapchat="maya_x", graph_name="Kontakt B Schulz")["id"]
        self.pid2 = register.anlegen("Kontakt F", snapchat="paul_y")["id"]

    def tearDown(self):
        protokoll_freigeben()
        self.env.stop()

    def _eingang(self, *eintraege):
        dienst._schreiben("eingang.json", list(eintraege))

    def _motor(self, antwort="Hi!"):
        return mock.patch.object(chat, "antworten", mock.AsyncMock(return_value=ergebnis(antwort)))

    def _ungelesen(self):
        return [e for e in dienst._lesen("eingang.json", []) if not e.get("gelesen")]

    def _alle_ticks(self, maximal=8):
        for _ in range(maximal):
            if not self._ungelesen():
                return
            lauf(wache.einmal())

    def test_person_loest_regeln_aus_eike_nicht_und_ein_fehler_stoert_die_antwort_nicht(self):
        self._eingang(alt("Hallo", id="e1", person=self.pid, kontakt="maya_x"), alt("noch was", id="e2", person=self.pid, kontakt="maya_x"))
        with self._motor("Hi!"), mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(wache.regeln_lauf, "ereignis", mock.AsyncMock(side_effect=RuntimeError("x"))) as m:
            lauf(wache.einmal())
        m.assert_awaited_once()                                   # ein Zug = ein Ereignis, auch bei zwei Nachrichten
        a = m.await_args.args
        self.assertEqual((a[0], a[1], a[2]), ("snapchat", self.pid, "Kontakt B"))
        self.assertIn("Hallo", a[3])
        self.assertEqual(a[4], "e1,e2")
        self.assertEqual(self._ungelesen(), [])                   # die Antwort ging trotz Fehler raus

    def test_eike_loest_keine_regel_aus(self):
        self._eingang(alt("Hallo", id="e1"))
        with mock.patch.object(wache, "_antworten", mock.AsyncMock(return_value=ergebnis("Hi"))), \
             mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(wache.regeln_lauf, "ereignis", mock.AsyncMock()) as m:
            lauf(wache.einmal())
        m.assert_not_awaited()

    def test_person_wird_mit_dem_personenmotor_und_einem_auftrag_an_sie_beantwortet(self):
        self._eingang(alt("Hallo Friday", person=self.pid, kontakt="maya_x"))
        with self._motor("Hi Kontakt B!") as m, mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
        self.assertEqual(m.await_args.kwargs["person"]["id"], self.pid)
        self.assertEqual(m.await_args.kwargs["kanal"], "Snapchat")
        self.assertNotIn("Eike", m.await_args.kwargs["kanal"])              # kein SNAPCHAT_KANAL, kein gesendet_kontext
        legen.assert_called_once_with("text", text="Hi Kontakt B!", person=self.pid, ausloeser=mock.ANY)
        self.assertEqual(self._ungelesen(), [])
        from kern.protokoll import speicher as pspeicher
        self.assertEqual([g["person"] for g in pspeicher.standard().gespraeche()], [self.pid])

    def test_eikes_zug_bleibt_wie_er_war(self):
        self._eingang(alt("Hi Friday"))
        with self._motor("Hallo Sir") as m, mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
        self.assertNotIn("person", m.await_args.kwargs)
        self.assertIn("Snapchat-Chat", m.await_args.kwargs["kanal"])
        legen.assert_called_once_with("text", text="Hallo Sir", ausloeser=mock.ANY)

    def test_verlaeufe_sind_getrennt(self):
        from kern.protokoll import speicher as pspeicher
        p = pspeicher.standard()
        ge = dienst.faden(None)
        p.beitrag(ge, "du", "GEHEIMES VON EIKE")
        p.beitrag(ge, "friday", "Antwort an Eike")
        gm = dienst.faden(self.pid)
        p.beitrag(gm, "du", "Kontakt Bs frühere Frage")
        p.beitrag(gm, "friday", "Kontakt Bs frühere Antwort")
        self._eingang(alt("Und jetzt?", person=self.pid))
        with self._motor() as m, mock.patch.object(dienst, "auftrag_legen"):
            lauf(wache.einmal())
        gesehen = " ".join(n["text"] for n in m.await_args.args[0])
        self.assertIn("Kontakt Bs frühere Frage", gesehen)
        self.assertNotIn("GEHEIMES VON EIKE", gesehen)
        self.assertEqual(m.await_args.kwargs["gespraech"], gm)

    def test_zwei_personen_werden_nacheinander_je_an_die_richtige_beantwortet(self):
        self._eingang(alt("Frage Kontakt B", id="a", person=self.pid), alt("Frage Kontakt F", id="b", person=self.pid2))
        with self._motor("Antwort") as m, mock.patch.object(dienst, "auftrag_legen") as legen:
            self._alle_ticks()
        self.assertEqual(sorted(c.kwargs["person"] for c in legen.call_args_list), sorted([self.pid, self.pid2]))
        for c in m.await_args_list:                                     # jeder Zug enthält nur den Text seiner Person
            text = " ".join(n["text"] for n in c.args[0])
            self.assertFalse("Frage Kontakt B" in text and "Frage Kontakt F" in text)

    def test_unbekannte_pausierte_und_ungueltige_personen_bekommen_keine_antwort(self):
        self.register.aendern(self.pid2, pausiert=True)
        self._eingang(alt("a", id="1", person="p99"), alt("b", id="2", person=self.pid2), alt("c", id="3", person="eike"),
                      alt("d", id="4", person=""), alt("e", id="5", person="p1;"))
        with self._motor() as m, mock.patch.object(dienst, "auftrag_legen") as legen:
            self._alle_ticks()
        m.assert_not_awaited()
        legen.assert_not_called()
        self.assertEqual(self._ungelesen(), [])                          # nichts bleibt hängen, nichts geht an Eike

    def test_volle_schlange_haelt_den_eintrag_der_person_zurueck_ohne_eikes_nachlegen(self):
        self._eingang(alt("Hallo", person=self.pid))
        with self._motor() as m, mock.patch.object(dienst, "auftrag_legen", side_effect=dienst.SnapchatFehler("voll")):
            lauf(wache.einmal())
        self.assertEqual(len(self._ungelesen()), 1)
        self.assertEqual(wache._nachzulegen, [])

    def test_bild_einer_person_traegt_person_quelle_und_den_namen_des_absenders(self):
        from kern.gedaechtnis import bilder
        name = "c" * 32 + ".png"
        self._eingang(alt("[Snap]", art="snap", bild=name, fehler=None, person=self.pid))
        auswertung = {"beschreibung": "Eine Frau am Strand.", "personen": 1, "wer": None}
        with self._motor("Schönes Bild!"), mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(dienst, "bild_lesen", return_value=echtes_png()), \
             mock.patch.object(dienst, "bild_entfernen"), \
             mock.patch.object(wache.bildauswertung, "auswerten", mock.AsyncMock(return_value=auswertung)):
            lauf(wache.einmal())
        eigene = bilder.liste(person=self.pid)
        self.assertEqual(len(eigene), 1)
        self.assertEqual(eigene[0]["quelle"], "Snapchat · Kontakt B")
        self.assertEqual(bilder.eintrag(eigene[0]["id"])["name"], "Kontakt B Schulz")    # eine Person + Absender = der Absender

    def test_kennst_du_schon_zeigt_nie_die_bilder_anderer_personen(self):
        with mock.patch.object(wache.bilder, "gleiches", return_value=None) as g:
            wache._bekannt(b"jpeg", {"id": self.pid, "name": "Kontakt B"})
        self.assertEqual(g.call_args.kwargs["person"], self.pid)
        with mock.patch.object(wache.bilder, "gleiches", return_value=None) as g:
            wache._bekannt(b"jpeg")
        self.assertIsNone(g.call_args.kwargs.get("person"))

    def test_nachrichten_nennen_die_person_nicht_eike(self):
        with mock.patch.object(dienst, "bild_lesen", return_value=echtes_png()):
            nachrichten, _ = wache._zug_bauen([alt("[Snap]", art="snap", bild="d" * 32 + ".png", person=self.pid)],
                                              {"id": self.pid, "name": "Kontakt B"})
        self.assertIn("Kontakt B", nachrichten[-1]["text"])
        self.assertNotIn("Eike", nachrichten[-1]["text"])

    def test_unbekannte_namen_werden_einmal_gemeldet_und_bleiben_in_der_datei(self):
        """Der Helfer ist die einzige Schreibstelle (nur anhängen) — die Wache löscht nichts mehr, merkt sich aber, was sie meldete."""
        dienst._schreiben("unbekannt_eingang.json", [{"anzeige": "Fremder"}, "Fremder"])
        with mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 1}) as push, \
             mock.patch.object(wache.unbekannt, "melden", wraps=wache.unbekannt.melden) as melden:
            lauf(wache.einmal())
            lauf(wache.einmal())
            self.assertEqual(melden.call_count, 1)                       # der zweite Tick fragt nicht erneut
            dienst._schreiben("unbekannt_eingang.json", ["Fremder", "Zweiter"])
            lauf(wache.einmal())
        self.assertEqual(push.call_count, 2)                             # „Zweiter“ kam später dazu
        self.assertEqual(dienst.unbekannte_lesen(), ["Fremder", "Zweiter"])
        from kern.personen import unbekannt
        self.assertEqual([u["anzeige"] for u in unbekannt.liste()], ["Fremder", "Zweiter"])

    def test_ein_bekannter_name_wird_nicht_als_unbekannt_gemeldet(self):
        dienst._schreiben("unbekannt_eingang.json", [{"anzeige": "maya_x"}])
        with mock.patch("kern.dienste.benachrichtigungen.senden") as push:
            lauf(wache.einmal())
        push.assert_not_called()
        self.assertEqual(dienst.unbekannte_lesen(), ["maya_x"])          # die Datei gehört dem Helfer; gemeldet wird der Name nicht

    def _rueckfrage(self, status, text=None):
        from kern.gedaechtnis import graph
        from kern.personen import rueckfragen
        with mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 1}):
            a = rueckfragen.stellen(self.pid, "Geburtstag", "Wann hat Eike Geburtstag?")
        if status == "freigegeben":
            with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)):
                lauf(rueckfragen.freigeben(a["id"], text))
        elif status == "abgelehnt":
            rueckfragen.ablehnen(a["id"])
        return a

    def test_nach_eikes_ja_bekommt_die_person_genau_seinen_freigabetext(self):
        self._rueckfrage("freigegeben", "Eike hat im Oktober Geburtstag.")
        with mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
            lauf(wache.einmal())                                            # nur einmal
        legen.assert_called_once()
        self.assertEqual(legen.call_args.kwargs["person"], self.pid)
        self.assertIn("Eike hat im Oktober Geburtstag.", legen.call_args.kwargs["text"])
        self.assertIn("nachgefragt", legen.call_args.kwargs["text"])
        from kern.protokoll import speicher as pspeicher
        g = dienst.faden_vorhanden(self.pid)
        self.assertIn("Eike hat im Oktober Geburtstag.", pspeicher.standard().abschrift(g, sprecher="Kontakt B"))

    def test_nach_eikes_nein_gibt_es_eine_neutrale_absage_ohne_details(self):
        self._rueckfrage("abgelehnt")
        with mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
        text = legen.call_args.kwargs["text"]
        self.assertIn("leider nichts sagen", text)
        self.assertNotIn("Geburtstag", text)                                 # verrät nichts über die Sache

    def test_nach_verfall_gibt_es_dieselbe_absage_und_die_rueckfrage_ist_erledigt(self):
        a = self._rueckfrage("offen")
        from datetime import datetime, timedelta, timezone
        from kern.personen import rueckfragen
        d = rueckfragen._lesen()
        d["anfragen"][0]["zeit"] = (datetime.now(timezone.utc) - timedelta(hours=rueckfragen.VERFALL_STUNDEN + 1)).isoformat(timespec="seconds")
        rueckfragen._speichern(d)
        with mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
            lauf(wache.einmal())
        legen.assert_called_once()
        self.assertIn("leider nichts sagen", legen.call_args.kwargs["text"])
        self.assertEqual(rueckfragen.zu_melden(), [])

    def test_pausierte_person_bekommt_keine_antwort_die_rueckfrage_gilt_als_erledigt(self):
        self._rueckfrage("abgelehnt")
        self.register.aendern(self.pid, pausiert=True)
        with mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
        legen.assert_not_called()
        from kern.personen import rueckfragen
        self.assertEqual(rueckfragen.zu_melden(), [])

    def test_volle_schlange_wiederholt_es_spaeter_statt_zu_verlieren(self):
        self._rueckfrage("abgelehnt")
        with mock.patch.object(dienst, "auftrag_legen", side_effect=dienst.SnapchatFehler("voll")):
            lauf(wache.einmal())
        from kern.personen import rueckfragen
        self.assertEqual(len(rueckfragen.zu_melden()), 1)
        with mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
            legen.assert_not_called()                                   # in der Frist: nicht im 3-s-Takt hämmern
            with mock.patch.object(wache.time, "time", return_value=time.time() + wache.RUECKSTELLUNG_S + 1):
                lauf(wache.einmal())
        legen.assert_called_once()


class PersonenAntwortPushTest(unittest.TestCase):
    def setUp(self):
        from kern.personen import register
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        konnektoren.setzen("snapchat", True)
        wache._zurueck.clear()
        wache._gesehen_texte.clear()
        wache._wiederholung_gemeldet.clear()
        wache._unbekannt_gemeldet.clear()
        wache._antwort_gemeldet.clear()
        self.pid = register.anlegen("Kontakt B", snapchat="maya_x", graph_name="Kontakt B Schulz")["id"]

    def tearDown(self):
        protokoll_freigeben()
        self.env.stop()

    def _eingang(self, *eintraege):
        dienst._schreiben("eingang.json", list(eintraege))

    def _motor(self, antwort="Hi!"):
        return mock.patch.object(chat, "antworten", mock.AsyncMock(return_value=ergebnis(antwort)))

    def test_ohne_aktive_ueberwachung_kein_push_bei_antwort_der_person(self):
        """Standard: aus (15.10.2026) — Friday antwortet, Eike bekommt keine Meldung."""
        from kern.dienste import benachrichtigungen
        wache._antwort_gemeldet.clear()
        self._eingang(alt("Hallo?", person=self.pid))
        with self._motor("Hi!"), mock.patch.object(dienst, "auftrag_legen") as legen, \
             mock.patch.object(benachrichtigungen, "senden", return_value={"unterdrueckt": ""}) as push:
            lauf(wache.einmal())
        legen.assert_called_once()
        push.assert_not_called()

    def test_personen_antwort_push_einmal_mit_inhalt(self):
        from kern.dienste import benachrichtigungen
        from kern.personen import register
        register.aendern(self.pid, module={"ueberwachung": True})
        wache._antwort_gemeldet.clear()
        self._eingang(alt("Hallo?", person=self.pid))
        with self._motor("Hi!"), mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(benachrichtigungen, "senden", return_value={"unterdrueckt": ""}) as push:
            lauf(wache.einmal())   # beantwortet + genau ein Push
            lauf(wache.einmal())   # nichts Neues: kein zweiter Push
            self._eingang(alt("Eike hier"))
            lauf(wache.einmal())   # Eikes Zug: kein Push
        push.assert_called_once()
        self.assertEqual(push.call_args.args[0], "kontakt_antwort")
        self.assertIn("Kontakt B", push.call_args.args[1])
        self.assertIn("Hallo?", push.call_args.args[2])

    def test_art_aus_heisst_still_aber_verlauf_bleibt(self):
        from kern.dienste import benachrichtigungen
        from kern.personen import register
        register.aendern(self.pid, module={"ueberwachung": True})
        wache._antwort_gemeldet.clear()
        self._eingang(alt("Hallo?", person=self.pid))
        with self._motor("Hi!"), mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(benachrichtigungen, "an", return_value=False):
            with mock.patch.object(benachrichtigungen.push, "senden") as direkt:
                lauf(wache.einmal())
        direkt.assert_not_called()
        from kern.protokoll import speicher as pspeicher
        texte = [b["text"] for b in pspeicher.standard().beitraege(dienst.faden(self.pid))]
        self.assertTrue(any("Hallo?" in t for t in texte))


class AnnahmeTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        konnektoren.setzen("snapchat", True)
        wache._unbekannt_gemeldet.clear()

    def tearDown(self):
        protokoll_freigeben()
        self.env.stop()

    def test_annahme_meldet_genau_einmal_kein_unbekannt(self):
        from kern.dienste import benachrichtigungen
        from kern.personen import unbekannt
        dienst.anfrage_legen("mara.v")
        with mock.patch.object(benachrichtigungen, "senden") as push:
            self.assertTrue(unbekannt.annahme_pruefen("snapchat", "mara.v"))
            self.assertTrue(unbekannt.annahme_pruefen("snapchat", "mara.v"))
        push.assert_called_once()
        self.assertEqual(push.call_args.args[0], "kontakte")
        self.assertEqual(dienst.anfragen()[0]["zustand"], "angenommen")
        self.assertEqual(unbekannt.liste(), [])

    def test_annahme_legt_den_kontakt_selbst_an_wenn_eike_den_namen_nannte(self):
        from kern.dienste import benachrichtigungen
        from kern.personen import register, unbekannt
        dienst.anfrage_legen("titus_g", name="Kontakt S Grünewald")
        with mock.patch.object(benachrichtigungen, "senden") as push:
            self.assertTrue(unbekannt.annahme_pruefen("snapchat", "titus_g"))
        p = register.nach_kanal("snapchat", "titus_g")
        self.assertEqual(p["name"], "Kontakt S Grünewald")
        self.assertEqual(p["werkzeuge"], ["uhrzeit"])          # nur Standard-Rechte
        self.assertIn("als Kontakt", push.call_args.args[2])

    def test_annahme_erkennt_den_anzeigenamen_und_legt_unter_ihm_an(self):
        from kern.dienste import benachrichtigungen
        from kern.personen import register, unbekannt
        dienst.anfrage_legen("titus_g", name="Kontakt S Grünewald", titel="Kontakt S G.")
        with mock.patch.object(benachrichtigungen, "senden"):
            self.assertTrue(unbekannt.annahme_pruefen("snapchat", "Kontakt S G."))
        self.assertEqual(register.nach_kanal("snapchat", "Kontakt S G.")["name"], "Kontakt S Grünewald")
        self.assertEqual(unbekannt.liste(), [])

    def test_vorstellung_nach_der_annahme_geht_genau_einmal_raus(self):
        from kern.dienste import benachrichtigungen
        from kern.personen import unbekannt
        dienst.anfrage_legen("titus_g", name="Kontakt S Grünewald", danach="Hey, ich bin Friday, Eikes Assistentin.")
        with mock.patch.object(benachrichtigungen, "senden"):
            unbekannt.annahme_pruefen("snapchat", "titus_g")
            unbekannt.annahme_pruefen("snapchat", "titus_g")
        auftrag = dienst.auftrag_lesen()
        self.assertEqual((auftrag["art"], auftrag["text"]), ("text", "Hey, ich bin Friday, Eikes Assistentin."))
        self.assertEqual(dienst.auftraege_anzahl(), 1)

    def test_schon_befreundet_gilt_als_annahme_kein_gescheitert(self):
        import json as jsonmod
        import time as zeitmod
        from kern.dienste import benachrichtigungen
        from kern.personen import register, unbekannt
        e = dienst.anfrage_legen("titus_g", name="Kontakt S Grünewald", titel="Kontakt S G.", danach="Hey, ich bin Friday.")
        with open(dienst._ordner() / "ergebnisse.jsonl", "w", encoding="utf-8") as f:
            f.write(jsonmod.dumps({"zeit": e["gelegt"] + 5, "art": "anfrage", "empfaenger": "titus_g", "ergebnis": "verworfen",
                                   "grund": "Schon befreundet: „titus_g“ steht unter „Friends & Groups“ — nichts angetippt."}) + "\n")
        with mock.patch.object(benachrichtigungen, "senden") as push:
            unbekannt.befreundete_uebernehmen()
            unbekannt.befreundete_uebernehmen()          # zweiter Lauf: nichts mehr zu tun
        self.assertEqual(dienst.anfragen()[0]["zustand"], "angenommen")
        self.assertEqual(dienst.anfrage_ergebnisse_spiegeln(), [])
        self.assertEqual(register.nach_kanal("snapchat", "Kontakt S G.")["name"], "Kontakt S Grünewald")
        self.assertEqual(dienst.auftrag_lesen()["text"], "Hey, ich bin Friday.")
        push.assert_called_once()

    def test_vorstellung_geht_auch_unter_dem_anzeigenamen_raus(self):
        from kern.dienste import benachrichtigungen
        from kern.personen import unbekannt
        dienst.anfrage_legen("titus_g", name="Kontakt S Grünewald", titel="Kontakt S G.", danach="Hey, ich bin Friday.")
        with mock.patch.object(benachrichtigungen, "senden"):
            unbekannt.annahme_pruefen("snapchat", "Kontakt S G.")
        self.assertEqual(dienst.auftrag_lesen()["text"], "Hey, ich bin Friday.")

    def test_ohne_namen_wird_kein_kontakt_geraten(self):
        from kern.dienste import benachrichtigungen
        from kern.personen import register, unbekannt
        dienst.anfrage_legen("mara.v")
        with mock.patch.object(benachrichtigungen, "senden"):
            unbekannt.annahme_pruefen("snapchat", "mara.v")
        self.assertIsNone(register.nach_kanal("snapchat", "mara.v"))

    def test_ohne_anfrage_keine_annahme(self):
        from kern.personen import unbekannt
        self.assertFalse(unbekannt.annahme_pruefen("snapchat", "Fremdling"))
        self.assertFalse(unbekannt.annahme_pruefen("chat", "mara.v"))

    def test_name_der_person_zaehlt_auch(self):
        from kern.dienste import benachrichtigungen
        from kern.personen import register, unbekannt
        pid = register.anlegen("Mara Veen", snapchat="mara.v")["id"]
        dienst.anfrage_legen("mara.v", person=pid)
        with mock.patch.object(benachrichtigungen, "senden"):
            self.assertTrue(unbekannt.annahme_pruefen("snapchat", "Mara Veen"))

    def test_gescheitert_push_genau_einmal(self):
        import json as jsonmod
        import time as zeitmod
        from kern.dienste import benachrichtigungen
        dienst.anfrage_legen("mara.v")
        ordner = dienst._ordner()
        with open(ordner / "ergebnisse.jsonl", "w", encoding="utf-8") as f:
            f.write(jsonmod.dumps({"zeit": zeitmod.time(), "art": "anfrage", "empfaenger": "mara.v",
                                   "ergebnis": "aufgegeben", "grund": "Handy still"}) + "\n")
        with mock.patch.object(benachrichtigungen, "senden") as push:
            wache._anfragen_spiegeln_melden()
            wache._anfragen_spiegeln_melden()
        push.assert_called_once()
        self.assertEqual(push.call_args.args[0], "kontakte")
        self.assertIn("mara.v", push.call_args.args[2].lower() if len(push.call_args.args) > 2 else "")

    def test_suchergebnis_push_genau_einmal(self):
        from kern.dienste import benachrichtigungen
        dienst.suchergebnis_schreiben("Kontakt S", [{"titel": "Kontakt S", "name": "titus_1", "grund": ""}])
        with mock.patch.object(benachrichtigungen, "senden") as push:
            wache._suchergebnisse_melden()
            wache._suchergebnisse_melden()
        push.assert_called_once()
        self.assertEqual(push.call_args.args[0], "kontakte")
        self.assertIn("1", push.call_args.args[2])

    def _annehmen_vorbereiten(self, stunde=12):
        konnektoren.schreiben_setzen("snapchat", True)
        dienst.erwartet_legen("Kontakt E")
        self.zeit = mock.patch.object(wache.config, "jetzt", return_value=datetime(2026, 10, 11, stunde, 0))
        self.zeit.start()
        self.addCleanup(self.zeit.stop)

    def test_erwarteter_name_wird_per_auftrag_gesucht_und_nur_einmal_je_takt(self):
        self._annehmen_vorbereiten()
        wache._eingehende_annehmen()
        auftrag = dienst.auftrag_lesen()
        self.assertEqual((auftrag["art"], auftrag["empfaenger"]), ("annehmen", "Kontakt E"))
        wache._eingehende_annehmen()          # Helfer noch belegt, Takt nicht um: nichts Neues
        self.assertEqual(dienst.auftrag_lesen()["id"], auftrag["id"])

    def test_nachts_und_ohne_schreibrecht_wird_nichts_gelegt(self):
        self._annehmen_vorbereiten(stunde=3)
        wache._eingehende_annehmen()
        self.assertIsNone(dienst.auftrag_lesen())
        self.zeit.stop(); self.zeit.start()
        konnektoren.schreiben_setzen("snapchat", False)
        wache._eingehende_annehmen()
        self.assertIsNone(dienst.auftrag_lesen())

    def test_erfolg_legt_kontakt_an_und_beendet_das_warten(self):
        from kern.dienste import benachrichtigungen
        from kern.personen import register
        self._annehmen_vorbereiten()
        wache._eingehende_annehmen()
        auftrag_id = dienst.auftrag_lesen()["id"]
        with mock.patch.object(dienst, "ergebnis_fuer", return_value={"ergebnis": "gesendet"}), \
                mock.patch.object(benachrichtigungen, "senden") as push:
            wache._eingehende_annehmen()
        self.assertIsNotNone(register.nach_kanal("snapchat", "Kontakt E"))
        self.assertEqual(dienst.erwartet_liste(), [])
        self.assertIn("Kontakt E", push.call_args.args[2])

    def test_nicht_gefunden_wartet_weiter(self):
        from kern.personen import register
        self._annehmen_vorbereiten()
        wache._eingehende_annehmen()
        with mock.patch.object(dienst, "ergebnis_fuer",
                               return_value={"ergebnis": "verworfen", "grund": "„Kontakt E“ steht nicht unter „Added Me“."}):
            wache._eingehende_annehmen()
        self.assertIsNone(register.nach_kanal("snapchat", "Kontakt E"))
        self.assertEqual([e["name"] for e in dienst.erwartet_liste()], ["Kontakt E"])

    def test_ticket_nachtraeglich_erkannt_wird_im_profil_gespeichert(self):
        import base64
        from kern.dienste import benachrichtigungen
        from kern.garantie import annahme
        person = {"id": "p5", "name": "Kontakt O", "quellen": {"garantie": 4}}
        b64 = base64.b64encode(b"jpegdaten").decode()
        with mock.patch.object(dienst, "ticket_modus", return_value="an"), \
                mock.patch.object(annahme, "annehmen", mock.AsyncMock(return_value={"ergebnis": "angenommen", "gespeichert": True})) as an, \
                mock.patch.object(wache.bilder, "entfernen") as weg, \
                mock.patch.object(benachrichtigungen, "senden") as push:
            lauf(wache._ticket_nachtraeglich(person, "b1", b64, "Eine Jugendnetzkarte mit Kundennummer."))
            lauf(wache._ticket_nachtraeglich(person, "b2", b64, "Ein grüner Frosch auf einem Ast."))     # kein Ticket
            lauf(wache._ticket_nachtraeglich(None, "b3", b64, "Eine Fahrkarte."))                        # Eike: nie
        an.assert_called_once()
        weg.assert_called_once_with("b1")       # gespeichert → keine Kopie im Bildgedächtnis
        self.assertEqual(an.call_args.args[2], "an")
        push.assert_called_once()
        self.assertIn("Kontakt O", push.call_args.args[1])

    def test_ticket_nachtraeglich_nicht_bei_modus_aus_oder_ohne_profil(self):
        import base64
        from kern.garantie import annahme
        b64 = base64.b64encode(b"x").decode()
        with mock.patch.object(annahme, "annehmen", mock.AsyncMock()) as an:
            with mock.patch.object(dienst, "ticket_modus", return_value="aus"):
                lauf(wache._ticket_nachtraeglich({"id": "p5", "name": "L", "quellen": {"garantie": 4}}, "b", b64, "Ticket"))
            with mock.patch.object(dienst, "ticket_modus", return_value="an"):
                lauf(wache._ticket_nachtraeglich({"id": "p5", "name": "L", "quellen": {}}, "b", b64, "Ticket"))
        an.assert_not_called()

    def test_leere_suche_meldet_nichts_gefunden(self):
        from kern.dienste import benachrichtigungen
        wache._suchen_gemeldet.clear()
        dienst.suchergebnis_schreiben("Niemand", [])
        with mock.patch.object(benachrichtigungen, "senden") as push:
            wache._suchergebnisse_melden()
        self.assertIn("Nichts gefunden", push.call_args.args[1])

    def test_push_ueberlebt_keinen_neustart_doppelt(self):
        # 04.10.2026: RAM-Merker schickte nach jedem Rebuild alles erneut.
        from kern.dienste import benachrichtigungen
        dienst.suchergebnis_schreiben("Kontakt S", [{"titel": "Kontakt S", "name": "titus_1", "grund": ""}])
        with mock.patch.object(benachrichtigungen, "senden") as push:
            wache._suchergebnisse_melden()
            wache._suchen_gemeldet.clear()   # Neustart: RAM leer, Datei bleibt
            wache._suchergebnisse_melden()
        push.assert_called_once()

    def test_eingetragener_termin_meldet_zusage_einmal(self):
        from kern.dienste import benachrichtigungen
        from kern.dienste import snapchat as dienst
        from kern.personen import register, rueckfragen
        pid = register.anlegen("Mara", snapchat="mara.v")["id"]
        register.aendern(pid, senden_erlaubt=True)
        a = rueckfragen.termin_wunsch_stellen(pid, "Kaffee", "2030-10-10T15:00")
        with mock.patch.object(benachrichtigungen, "senden"):
            pass
        from kern.dienste import icloud
        with mock.patch.object(icloud, "anlegen", return_value={"ok": True}):
            rueckfragen.annehmen(a["id"])
        with mock.patch.object(dienst, "auftrag_legen") as legen:
            wache._rueckfragen_melden()
            wache._rueckfragen_melden()
        legen.assert_called_once()
        self.assertIn("zugesagt", legen.call_args.kwargs.get("text", ""))
        self.assertIn("Kaffee", legen.call_args.kwargs.get("text", ""))


class VieleKontakteTest(unittest.TestCase):
    """07.10.2026: 12 Kontakte schreiben gleichzeitig — jede Person bekommt genau ihre Antwort, in der Reihenfolge ihres ältesten
    Eintrags, nichts vermischt sich, und eine scheiternde Person hält die anderen nicht auf."""

    N = 12

    def setUp(self):
        from kern.personen import register
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        konnektoren.setzen("snapchat", True)
        wache._nachzulegen.clear()
        wache._zurueck.clear()
        wache._gesehen_texte.clear()
        wache._wiederholung_gemeldet.clear()
        wache._unbekannt_gemeldet.clear()
        self.pids = [register.anlegen(f"Kontakt{i}", snapchat=f"k{i}")["id"] for i in range(self.N)]
        jetzt = time.time()
        # Person 0 schrieb zuerst, Person 11 zuletzt — alle schon außerhalb des Bündelfensters.
        dienst._schreiben("eingang.json", [
            alt(f"Hallo von {i}", id=f"e{i}", person=pid, kontakt=f"k{i}", zeit=jetzt - 120 + i)
            for i, pid in enumerate(self.pids)])
        self.abgeholt = []                     # (person, text) in der Reihenfolge, in der der „Helfer“ sie abholt

    def tearDown(self):
        protokoll_freigeben()
        self.env.stop()

    def _helfer_holt_ab(self):
        """Wie der Helfer: den laufenden Auftrag und die Schlange leeren, in Reihenfolge."""
        dateien = [dienst._ordner() / "auftrag.json", *sorted(dienst._schlange().glob("*.json"))]
        for d in dateien:
            if d.exists():
                a = json.loads(d.read_text(encoding="utf-8"))
                self.abgeholt.append((a["person"], a["text"]))
                d.unlink()

    def _motor(self, scheitert=None):
        async def antworten(nachrichten, **kw):
            text = nachrichten[-1]["text"]
            if scheitert is not None and f"Hallo von {scheitert}" in text:
                raise RuntimeError("Gemini 429")
            return ergebnis(f"Antwort auf: {text}")
        return mock.patch.object(chat, "antworten", side_effect=antworten)

    def _laufen(self, maximal=60):
        for _ in range(maximal):
            lauf(wache.einmal())
            self._helfer_holt_ab()
            if not [e for e in dienst._lesen("eingang.json", []) if not e.get("gelesen")]:
                return

    def test_jede_person_bekommt_genau_ihre_antwort_in_reihenfolge(self):
        with self._motor():
            self._laufen()
        self.assertEqual(len(self.abgeholt), self.N)
        self.assertEqual([p for p, _ in self.abgeholt], self.pids)               # ältester Eintrag zuerst
        for i, (pid, text) in enumerate(self.abgeholt):
            self.assertEqual(pid, self.pids[i])
            self.assertIn(f"Hallo von {i}", text)
            fremde = [j for j in range(self.N) if j != i and f"Hallo von {j}" in text.replace(f"Hallo von {i}", "")]
            self.assertEqual(fremde, [], f"Antwort an {i} enthält Text einer anderen Person")

    def test_scheiternde_person_haelt_die_anderen_nicht_auf(self):
        with self._motor(scheitert=3):
            self._laufen(maximal=40)
        antworten_an = [p for p, _ in self.abgeholt]
        self.assertEqual(sorted(antworten_an), sorted(p for i, p in enumerate(self.pids) if i != 3))
        self.assertNotIn(self.pids[3], antworten_an)
        ungelesen = [e for e in dienst._lesen("eingang.json", []) if not e.get("gelesen")]
        self.assertEqual([e["person"] for e in ungelesen], [self.pids[3]])           # bleibt für den nächsten Versuch liegen

    def test_wache_laesst_einen_platz_fuer_eikes_direktauftraege_frei(self):
        """Review 07.10.2026: Bei vielen aktiven Kontakten füllte die Wache die Schlange bis zum Rand — Eikes „schick X …“
        scheiterte dann an „Es warten schon 5 Aufträge“."""
        with self._motor() as motor:
            with mock.patch.object(dienst, "auftraege_anzahl", return_value=dienst.QUEUE_MAX):
                lauf(wache.einmal())
        motor.assert_not_called()                                       # ein Platz bleibt frei, die Wache wartet
        with self._motor() as motor:
            with mock.patch.object(dienst, "auftraege_anzahl", return_value=dienst.QUEUE_MAX - 1):
                lauf(wache.einmal())
        motor.assert_called()                                           # darunter arbeitet sie weiter

    # ---- parallele Gemini-Antworten (07.10.2026, Spec „schnell“, Punkt 3) ----
    def _nebenlaeufig_motor(self, scheitert=None):
        z = {"jetzt": 0, "max": 0, "je_person": {}, "reihenfolge": []}

        async def antworten(nachrichten, **kw):
            pid = (kw.get("person") or {}).get("id")
            z["reihenfolge"].append(pid)
            z["jetzt"] += 1
            z["max"] = max(z["max"], z["jetzt"])
            z["je_person"][pid] = z["je_person"].get(pid, 0) + 1
            assert z["je_person"][pid] == 1, "dieselbe Person doppelt in Flug"
            try:
                await asyncio.sleep(0.05)
                if scheitert is not None and f"Hallo von {scheitert}" in nachrichten[-1]["text"]:
                    raise RuntimeError("Gemini 429")
                return ergebnis(f"Antwort auf: {nachrichten[-1]['text']}")
            finally:
                z["jetzt"] -= 1
                z["je_person"][pid] -= 1
        return mock.patch.object(chat, "antworten", side_effect=antworten), z

    def test_hoechstens_drei_gleichzeitig_je_person_eine_und_alle_bedient(self):
        wache._parallel.update(grenze=wache.PARALLEL, ruhig=0)
        motor, z = self._nebenlaeufig_motor()
        with motor:
            self._laufen()
        self.assertEqual(z["max"], wache.PARALLEL)
        self.assertEqual(sorted(p for p, _ in self.abgeholt), sorted(self.pids))

    def test_ein_fehler_stoppt_die_anderen_nicht_und_senkt_auf_eins(self):
        wache._parallel.update(grenze=wache.PARALLEL, ruhig=0)
        motor, z = self._nebenlaeufig_motor(scheitert=1)
        with motor:
            lauf(wache.einmal())
        self.assertEqual(wache._parallel["grenze"], 1)                  # nach dem Fehler vorsichtig
        gelesen = [e["person"] for e in dienst._lesen("eingang.json", []) if e.get("gelesen")]
        self.assertEqual(sorted(gelesen), sorted([self.pids[0], self.pids[2]]))     # die anderen zwei der Gruppe wurden fertig

    def test_grenze_steigt_nach_ruhigen_ticks_wieder(self):
        wache._parallel.update(grenze=1, ruhig=0)
        motor, _ = self._nebenlaeufig_motor()
        with motor:
            for _ in range(wache.RUHIG_BIS_HOCH + 1):
                lauf(wache.einmal())
                self._helfer_holt_ab()
        self.assertGreater(wache._parallel["grenze"], 1)

    def test_eike_kommt_zuerst(self):
        dienst._schreiben("eingang.json", [
            alt("Hallo von Person", id="e0", person=self.pids[0], kontakt="k0", zeit=time.time() - 300),
            alt("Hallo Friday", id="eike", zeit=time.time() - 100)])
        wache._parallel.update(grenze=wache.PARALLEL, ruhig=0)
        motor, z = self._nebenlaeufig_motor()
        with motor:
            lauf(wache.einmal())
        self.assertIsNone(z["reihenfolge"][0])                          # Eike (None) vor der älteren Person
        self.assertEqual(len(z["reihenfolge"]), 2)


class TicketWacheTest(unittest.TestCase):
    NAME = "b" * 32 + ".jpg"

    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        konnektoren.setzen("snapchat", True)
        wache._nachzulegen.clear()
        wache._zurueck.clear()
        wache._gesehen_texte.clear()
        wache._wiederholung_gemeldet.clear()
        wache._unbekannt_gemeldet.clear()
        from kern.personen import register
        self.pid = register.anlegen("Kontakt B", snapchat="maya_x", graph_name="Kontakt B Schulz")["id"]

    def tearDown(self):
        protokoll_freigeben()
        self.env.stop()

    def _modus(self, modus):
        dienst._schreiben("einstellungen.json", {"ticket_annahme": modus})

    def _ticketbild(self, person):
        (dienst._ordner() / "bilder").mkdir(exist_ok=True)
        (dienst._ordner() / "bilder" / self.NAME).write_bytes(echtes_png())      # PIL liest das Bild, die Endung ist egal
        e = alt("[Ticketbild]", id="t1", art="ticketbild", bild=self.NAME)
        dienst._schreiben("eingang.json", [{**e, "person": person} if person else e])

    def _ungelesen(self):
        return [e for e in dienst._lesen("eingang.json", []) if not e.get("gelesen")]

    def _bild_da(self):
        return (dienst._ordner() / "bilder" / self.NAME).exists()

    def _annahme(self, hinweis="[TICKET-PRÜFUNG vom Server] Ergebnis: angenommen, gültig 06.10.2026 bis 06.11.2026."):
        return mock.patch.object(wache.annahme, "annehmen", mock.AsyncMock(
            return_value={"ergebnis": "angenommen", "grund": "ok", "gespeichert": True, "profil": 1, "hinweis": hinweis}))

    def _motor(self):
        return mock.patch.object(chat, "antworten", mock.AsyncMock(return_value=ergebnis("Danke, ist angekommen!")))

    def test_an_ersetzt_das_bild_durch_den_hinweis_und_friday_antwortet(self):
        self._modus("an")
        self._ticketbild(self.pid)
        with self._annahme() as annahme, self._motor() as motor, mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
        annahme.assert_awaited_once()
        self.assertEqual(annahme.await_args.args[2], "an")
        nachrichten = motor.await_args.args[0]
        self.assertTrue(any("TICKET-PRÜFUNG" in n["text"] for n in nachrichten))
        self.assertFalse(any("bild" in n for n in nachrichten), "das Ticketbild selbst geht nie an das Modell")
        legen.assert_called_once_with("text", text="Danke, ist angekommen!", person=self.pid, ausloeser=mock.ANY)
        self.assertEqual(self._ungelesen(), [])
        self.assertFalse(self._bild_da(), "die Bilddatei wird nach der Annahme gelöscht")

    def test_probelauf_antwortet_nicht_und_markiert_gelesen(self):
        self._modus("trocken")
        self._ticketbild(self.pid)
        with self._annahme() as annahme, self._motor() as motor, mock.patch.object(dienst, "auftrag_legen") as legen:
            lauf(wache.einmal())
        self.assertEqual(annahme.await_args.args[2], "trocken")
        motor.assert_not_awaited()
        legen.assert_not_called()
        self.assertEqual(self._ungelesen(), [])
        self.assertFalse(self._bild_da())

    def test_aus_behandelt_das_ticketbild_wie_ein_normales_chatbild(self):
        self._modus("aus")
        self._ticketbild(self.pid)
        with self._annahme() as annahme, self._motor() as motor, mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(wache.bilder, "speichern", return_value=None):
            lauf(wache.einmal())
        annahme.assert_not_awaited()
        nachrichten = motor.await_args.args[0]
        self.assertTrue(any("bild" in n for n in nachrichten), "wie früher: als Bild im Zug")

    def test_eike_ohne_probelauf_wird_zum_chatbild(self):
        self._modus("an")
        self._ticketbild(None)
        with self._annahme() as annahme, self._motor() as motor, mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(wache.bilder, "speichern", return_value=None), \
             mock.patch.object(wache.bildauswertung, "auswerten", mock.AsyncMock(return_value=None)):
            lauf(wache.einmal())
        annahme.assert_not_awaited()
        self.assertTrue(any("bild" in n for n in motor.await_args.args[0]))

    def test_eike_im_probelauf_wird_angenommen(self):
        self._modus("trocken")
        self._ticketbild(None)
        with self._annahme() as annahme, self._motor() as motor, mock.patch.object(dienst, "auftrag_legen"):
            lauf(wache.einmal())
        self.assertIsNone(annahme.await_args.args[0], "person=None heißt Eikes Chat")
        motor.assert_not_awaited()

    def test_absturz_in_annehmen_gibt_den_festen_fehlerhinweis_und_die_wache_laeuft_weiter(self):
        self._modus("an")
        self._ticketbild(self.pid)
        kaputt = mock.patch.object(wache.annahme, "annehmen", mock.AsyncMock(side_effect=RuntimeError("boom")))
        with kaputt, self._motor() as motor, mock.patch.object(dienst, "auftrag_legen"):
            lauf(wache.einmal())
        self.assertTrue(any(wache.annahme.HINWEIS_FEHLER == n["text"] for n in motor.await_args.args[0]))
        self.assertEqual(self._ungelesen(), [])

    def test_unlesbares_bild_gibt_den_festen_nicht_angekommen_hinweis(self):
        self._modus("an")
        self._ticketbild(self.pid)
        (dienst._ordner() / "bilder" / self.NAME).write_bytes(b"kein bild")
        with self._annahme() as annahme, self._motor() as motor, mock.patch.object(dienst, "auftrag_legen"):
            lauf(wache.einmal())
        annahme.assert_not_awaited()
        self.assertTrue(any(wache.annahme.HINWEIS_NICHT_LESBAR == n["text"] for n in motor.await_args.args[0]))

    def test_ticketbild_geht_nicht_ins_bildgedaechtnis(self):
        self._modus("an")
        self._ticketbild(self.pid)
        with self._annahme(), self._motor(), mock.patch.object(dienst, "auftrag_legen"), \
             mock.patch.object(wache.bilder, "speichern") as speichern:
            lauf(wache.einmal())
        speichern.assert_not_called()
