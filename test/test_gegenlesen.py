"""Rückfrage bei Sprache (P4 der Ehrlichkeit, 12.10.2026): Namensabgleich gegen das Register und Gegenlesen im Live-Kanal
(„Ich halte fest: … — richtig?“, erst Eikes Ja in einer späteren Äußerung führt aus). Im Chat ändert sich nichts."""

import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

from kern.ehrlichkeit import gegenlesen
from kern.ehrlichkeit import wirkung as wirk
from kern.personen import abgleich, register


def lauf(c):
    return asyncio.run(c)


class MitRegister(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.gabriel = register.anlegen("Kontakt M", snapchat="gabi_p")
        self.kontakt_c = register.anlegen("Lehrkraft 1 Muster", snapchat="lehrkraft_1")
        self.kontakt_a = register.anlegen("Person A", snapchat="person_a_k")


class Abgleich(MitRegister):
    def stand(self, text):
        d = abgleich.deuten(text)
        return d["stand"], [p["name"] for p in d["personen"]]

    def test_exakt(self):
        self.assertEqual(self.stand("Kontakt M"), ("exakt", ["Kontakt M"]))
        self.assertEqual(self.stand("gabi_p"), ("exakt", ["Kontakt M"]))
        self.assertEqual(self.stand("Muster"), ("exakt", ["Lehrkraft 1 Muster"]))
        self.assertEqual(self.stand("Person A"), ("exakt", ["Person A"]))

    def test_verhoert_ist_unsicher_nie_ein_stiller_treffer(self):
        self.assertEqual(self.stand("Gap"), ("unsicher", ["Kontakt M"]))        # Kurzform/Klang
        self.assertEqual(self.stand("gabiq"), ("unsicher", ["Kontakt M"]))
        self.assertEqual(self.stand("Lehrkraftt"), ("unsicher", ["Lehrkraft 1 Muster"]))
        self.assertEqual(self.stand("Persom"), ("unsicher", ["Person A"]))

    def test_unbekannt_bleibt_unbekannt(self):
        self.assertEqual(self.stand("Zaphod"), ("unbekannt", []))
        self.assertEqual(self.stand(""), ("unbekannt", []))

    def test_mehrdeutig_ist_unsicher(self):
        register.anlegen("Kontakt M Meyer", snapchat="gabe_m")
        stand, namen = self.stand("Kontakt")
        self.assertEqual(stand, "unsicher")
        self.assertEqual(set(namen), {"Kontakt M", "Kontakt M Meyer"})

    def test_frage(self):
        self.assertEqual(abgleich.frage([self.gabriel]), "Meinst du Kontakt M?")
        self.assertEqual(abgleich.frage([self.gabriel, self.kontakt_a]), "Meinst du Kontakt M oder Person A?")

    def test_alltagswort_serien_ist_nicht_kontakt_c(self):
        from unittest import mock
        with mock.patch.object(abgleich.register, "liste", return_value=[{"id": "p3", "name": "Kontakt C", "kanaele": {}}]):
            self.assertEqual(abgleich.verdaechtige("Kontakt B darf Filme und Serien herunterladen."), [])

    def test_verdaechtige_woerter_im_text(self):
        v = abgleich.verdaechtige("Gap hat gesagt, ich soll immer pünktlich sein. Kontakt M weiß es.")
        self.assertEqual([(w, p["name"]) for w, p in v], [("Gap", "Kontakt M")])
        self.assertEqual(abgleich.verdaechtige("Merk dir, dass Person A Dienstag kommt."), [])

    def test_klangform(self):
        self.assertEqual(abgleich.klangform("Muster"), abgleich.klangform("Musder"))
        self.assertNotEqual(abgleich.klangform("Muster"), abgleich.klangform("Meier"))


class Pruefen(MitRegister):
    def test_vorgabe_braucht_erst_das_gegenlesen(self):
        r = {}
        args, w = gegenlesen.pruefen("vorgabe_merken", {"text": "Sei immer kurz."}, False, r)
        self.assertEqual((w.status, w.art), ("rueckfrage", "vorgabe"))
        self.assertEqual(w.quittung, "Ich halte fest: Sei immer kurz. — richtig?")
        self.assertTrue(w.text.startswith("RÜCKFRAGE:"))
        self.assertIn("Noch nichts ausgeführt", w.text)
        self.assertIn("vorgabe_merken", r)

    def test_ein_ja_vor_der_rueckfrage_zaehlt_nicht(self):
        r = {}
        _, w = gegenlesen.pruefen("vorgabe_merken", {"text": "x"}, True, r)        # zustimmung war schon da
        self.assertIsNotNone(w)

    def test_ja_danach_fuehrt_mit_den_vorgelesenen_angaben_aus(self):
        r = {}
        gegenlesen.pruefen("vorgabe_merken", {"text": "Sei immer kurz."}, False, r)
        args, w = gegenlesen.pruefen("vorgabe_merken", {"text": "Sei immer sehr kurz und frech."}, True, r)
        self.assertIsNone(w)
        self.assertEqual(args["text"], "Sei immer kurz.")                         # nicht, was das Modell inzwischen daraus machte
        self.assertNotIn("vorgabe_merken", r)                                      # verbraucht: der nächste Aufruf fragt wieder
        _, w2 = gegenlesen.pruefen("vorgabe_merken", {"text": "Sei immer kurz."}, True, r)
        self.assertIsNotNone(w2)

    def test_nein_ersetzt_die_rueckfrage(self):
        r = {}
        gegenlesen.pruefen("vorgabe_merken", {"text": "alt"}, False, r)
        _, w = gegenlesen.pruefen("vorgabe_merken", {"text": "neu"}, False, r)
        self.assertIn("neu", w.quittung)
        args, _ = gegenlesen.pruefen("vorgabe_merken", {"text": "egal"}, True, r)
        self.assertEqual(args["text"], "neu")

    def test_verfallen(self):
        r = {}
        gegenlesen.pruefen("vorgabe_merken", {"text": "x"}, False, r, jetzt=1000.0)
        _, w = gegenlesen.pruefen("vorgabe_merken", {"text": "x"}, True, r, jetzt=1000.0 + gegenlesen.GILT_S + 1)
        self.assertIsNotNone(w)

    def test_skill_und_nachricht_und_erinnerung(self):
        for name, args in (("skill_schreiben", {"name": "a", "zweck": "Meldungen", "anweisung": "Frag nach der Linie."}),
                           ("skill_aendern", {"id": 1, "anweisung": "Frag nach der Haltestelle."}),
                           ("schicke_nachricht", {"person": "Lehrkraft 1 Muster", "text": "Komm um acht."}),
                           ("schicke_snap", {"person": "Lehrkraft 1 Muster", "suche": "Katze"}),
                           ("erinnerung", {"titel": "t", "text": "Ruf an", "zeitpunkt": "morgen 8", "kanal": "snapchat", "an": "Lehrkraft 1 Muster"})):
            _, w = gegenlesen.pruefen(name, args, False, {})
            self.assertEqual(w.status, "rueckfrage", name)
            self.assertTrue(w.quittung.startswith("Ich halte fest:"), w.quittung)
        _, w = gegenlesen.pruefen("schicke_nachricht", {"person": "Lehrkraft 1 Muster", "text": "Komm um acht."}, False, {})
        self.assertIn("Lehrkraft 1 Muster", w.quittung)                 # der volle Name aus dem Register wird vorgelesen

    def test_bejahte_person_wird_nicht_nochmal_gefragt(self):
        r = {}
        _, w = gegenlesen.pruefen("schicke_snap", {"person": "Lehrkraft 1 Muster", "suche": "Katze"}, False, r, jetzt=1000.0)
        self.assertEqual(w.status, "rueckfrage")
        a, w = gegenlesen.pruefen("schicke_snap", {"person": "Lehrkraft 1 Muster", "suche": "Katze"}, True, r, jetzt=1001.0)
        self.assertIsNone(w)
        a, w = gegenlesen.pruefen("schicke_snap", {"person": "lehrkraft_1", "suche": "Hund"}, False, r, jetzt=1002.0)
        self.assertIsNone(w)                                       # „Lehrkraft 1“ = Lehrkraft 1 Muster, schon bejaht
        self.assertEqual(a["person"], "Lehrkraft 1 Muster")
        _, w = gegenlesen.pruefen("schicke_snap", {"person": "Person A", "suche": "Hund"}, False, r, jetzt=1003.0)
        self.assertEqual(w.status, "rueckfrage")                   # eine andere Person fragt weiter
        _, w = gegenlesen.pruefen("schicke_snap", {"person": "Persom B", "suche": "Hund"}, False, r, jetzt=1004.0)
        self.assertEqual(w.status, "rueckfrage")                   # nur ähnlich: fragt weiter

    def test_an_eike_selbst_und_andere_werkzeuge_ohne_gegenlesen(self):
        for name, args in (("schicke_nachricht", {"person": "mich", "text": "x"}), ("schicke_snap", {"suche": "Katze"}),
                           ("erinnerung", {"titel": "t", "text": "x", "zeitpunkt": "8"}), ("uhrzeit", {}), ("merken", {"thema": "a"}),
                           ("whatsapp_senden", {}), ("skill_bestaetigen", {"id": 1})):
            a, w = gegenlesen.pruefen(name, args, False, {})
            self.assertIsNone(w, name)
            self.assertEqual(a, args)

    def test_verhoerter_name_fragt_statt_zu_treffen(self):
        r = {}
        args, w = gegenlesen.pruefen("schicke_nachricht", {"person": "Gap", "text": "Hallo"}, False, r)
        self.assertEqual(w.quittung, "Meinst du Kontakt M?")
        self.assertIn("Noch nichts ausgeführt", w.text)
        # Eikes Ja → genau diese Person, nicht „Gap“
        args2, w2 = gegenlesen.pruefen("schicke_nachricht", {"person": "Gap", "text": "Hallo"}, True, r)
        self.assertIsNone(w2)
        self.assertEqual(args2["person"], "Kontakt M")

    def test_mehrere_kandidaten_ohne_stille_wahl(self):
        register.anlegen("Kontakt M Meyer", snapchat="gabe_m")
        r = {}
        _, w = gegenlesen.pruefen("schicke_nachricht", {"person": "Kontakt", "text": "Hallo"}, False, r)
        self.assertIn(" oder ", w.quittung)
        self.assertNotIn("schicke_nachricht", r)                  # kein stilles Vorwählen
        args, w2 = gegenlesen.pruefen("schicke_nachricht", {"person": "Kontakt", "text": "Hallo"}, True, r)
        self.assertIsNotNone(w2)                                  # ein bloßes Ja löst die Zweideutigkeit nicht

    def test_unbekannte_person_geht_zum_werkzeug_das_selbst_absagt(self):
        a, w = gegenlesen.pruefen("schicke_nachricht", {"person": "Zaphod", "text": "Hallo"}, False, {})
        self.assertIsNotNone(w)                                   # Wortlaut wird trotzdem gegengelesen
        self.assertIn("Nachricht an Zaphod", w.quittung)

    def test_verdaechtiger_name_im_vorgabetext(self):
        _, w = gegenlesen.pruefen("vorgabe_merken", {"text": "Gap hat gesagt, ich soll leise sein."}, False, {})
        self.assertIn("Meinst du Kontakt M?", w.quittung)

    def test_keine_technikwoerter_in_der_quittung(self):
        _, w = gegenlesen.pruefen("skill_schreiben", {"zweck": "Skill für Vorgabe Nr. 3", "anweisung": "x"}, False, {})
        self.assertIsNone(wirk.TECHNIK.search(w.quittung), w.quittung)


class _Gem:
    def __init__(self, nachrichten):
        self.nachrichten, self.gesendet = nachrichten, []

    def __aiter__(self):
        async def gen():
            for m in self.nachrichten:
                yield json.dumps(m)
        return gen()

    async def send(self, s):
        self.gesendet.append(json.loads(s))


class _Browser:
    def __init__(self):
        self.ereignisse = []

    async def send_json(self, m):
        self.ereignisse.append(m)


def _ruf(name, args, i="1"):
    return {"toolCall": {"functionCalls": [{"id": i, "name": name, "args": args}]}}


def _ja(text):
    return {"serverContent": {"inputTranscription": {"text": text}}}


class LiveGegenlesen(MitRegister):
    def fahren(self, nachrichten, zustand=None):
        from kern.sitzung import live
        gem, br = _Gem(nachrichten), _Browser()
        mit = mock.MagicMock()
        mit.gespraech = 3
        mit.puffer = {"du": "", "friday": ""}
        zustand = zustand if zustand is not None else {"zuege": 0}
        gerufen = []

        async def ausf(name, args=None, *a, **k):
            gerufen.append((name, dict(args or {}), k.get("zustimmung")))
            return {"ergebnis": "Festgehalten: ok", "status": "ok", "art": "vorgabe", "quittung": "Festgehalten: ok."}

        async def los():
            await live._von_gemini(gem, br, zustand, mit, asyncio.Event())

        with mock.patch.object(live.werkzeuge, "ausfuehren", ausf):
            lauf(los())
        return gem, br, gerufen, zustand

    def antworten(self, gem):
        return [m["toolResponse"]["functionResponses"][0]["response"] for m in gem.gesendet if "toolResponse" in m]

    def test_erst_rueckfrage_dann_ja_dann_ausfuehren(self):
        gem, br, gerufen, z = self.fahren([_ruf("vorgabe_merken", {"text": "Sei kurz."}), _ja("Ja"),
                                           _ruf("vorgabe_merken", {"text": "Sei kurz."}, "2")])
        self.assertEqual([g[:2] for g in gerufen], [("vorgabe_merken", {"text": "Sei kurz."})])      # nur der zweite Aufruf lief
        erste, zweite = self.antworten(gem)
        self.assertTrue(erste["ergebnis"].startswith("RÜCKFRAGE:"))
        self.assertNotIn("anweisung", erste)                                        # der Text trägt die Anweisung selbst
        self.assertEqual(zweite["ergebnis"], "Festgehalten: ok")
        q = [e for e in br.ereignisse if e["type"] == "quittung"]
        self.assertEqual([x["status"] for x in q], ["rueckfrage", "ok"])
        self.assertEqual(q[0]["text"], "Ich halte fest: Sei kurz. — richtig?")

    def test_ohne_ja_laeuft_nichts(self):
        gem, br, gerufen, z = self.fahren([_ruf("vorgabe_merken", {"text": "Sei kurz."}), _ruf("vorgabe_merken", {"text": "Sei kurz."}, "2")])
        self.assertEqual(gerufen, [])

    def test_ja_im_selben_zug_vorher_zaehlt_nicht(self):
        gem, br, gerufen, z = self.fahren([_ja("Ja, merk dir: sei kurz"), _ruf("vorgabe_merken", {"text": "Sei kurz."})])
        self.assertEqual(gerufen, [])
        self.assertFalse(z.get("zustimmung"))                                       # das Ja ist mit der Rückfrage verbraucht

    def test_nein_nach_der_rueckfrage_verhindert(self):
        gem, br, gerufen, z = self.fahren([_ruf("vorgabe_merken", {"text": "Sei kurz."}), _ja("Nein, lass das"),
                                           _ruf("vorgabe_merken", {"text": "Sei kurz."}, "2")])
        self.assertEqual(gerufen, [])

    def test_ein_ja_ein_aufruf(self):
        gem, br, gerufen, z = self.fahren([_ruf("vorgabe_merken", {"text": "A"}), _ja("Ja"), _ruf("vorgabe_merken", {"text": "A"}, "2"),
                                           _ruf("vorgabe_merken", {"text": "B"}, "3")])
        self.assertEqual([g[1]["text"] for g in gerufen], ["A"])

    def test_nachricht_an_verhoerten_namen_geht_nur_an_den_bestaetigten(self):
        gem, br, gerufen, z = self.fahren([_ruf("schicke_nachricht", {"person": "Gap", "text": "Hallo"}), _ja("Ja, genau"),
                                           _ruf("schicke_nachricht", {"person": "Gap", "text": "Hallo"}, "2")])
        self.assertEqual(len(gerufen), 1)
        self.assertEqual(gerufen[0][1]["person"], "Kontakt M")
        self.assertTrue(gerufen[0][2])                                               # Eikes Ja geht ans Werkzeug (Auftrag erkannt)
        self.assertEqual([x["text"] for x in br.ereignisse if x["type"] == "quittung"][0], "Meinst du Kontakt M?")

    def test_lesende_und_eigene_werkzeuge_unberuehrt(self):
        gem, br, gerufen, z = self.fahren([_ruf("uhrzeit", {}), _ruf("schicke_nachricht", {"person": "mich", "text": "x"}, "2")])
        self.assertEqual([g[0] for g in gerufen], ["uhrzeit", "schicke_nachricht"])

    def test_chat_hat_kein_gegenlesen(self):
        from kern.sitzung import chat
        self.assertNotIn("gegenlesen", __import__("pathlib").Path(chat.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
