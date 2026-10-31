"""Werkzeugauswahl im Chat: nur Kern + passende Gruppen, `werkzeuge_laden` holt den Rest (13.10.2026)."""
import asyncio
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kern import konnektoren
from kern.sitzung import chat
from kern.werkzeuge import auswahl, katalog


def namen(tools):
    return {d["name"] for d in tools[0]["functionDeclarations"]}


class MitKonnektoren(unittest.TestCase):
    """Die Auswahl zeigt nur Werkzeuge freigegebener Konnektoren (`katalog._frei`). Diese Tests prüfen die
    Auswahl, nicht Eikes Freigaben — sie schalten die erwarteten Konnektoren in einem Wegwerf-Datenordner
    selbst ein, statt sich auf `data/konnektoren.json` und `data/zugaenge.json` zu verlassen."""

    KONNEKTOREN = ("spotify", "pc")

    def setUp(self):
        daten = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, daten, ignore_errors=True)
        p = mock.patch.dict(os.environ, {"FRIDAY_DATEN": daten})
        p.start()
        self.addCleanup(p.stop)
        for kid in self.KONNEKTOREN:
            konnektoren.setzen(kid, True)
            konnektoren.zugang_setzen(kid, "test", "test")


class Waehlen(unittest.TestCase):
    def test_kern_ohne_treffer(self):
        self.assertEqual(auswahl.waehlen(["Wie geht es dir?"]), set())
        m = auswahl.module_von(set())
        self.assertEqual(m, set(auswahl.KERN))

    def test_treffer_und_ja_traegt_den_zug_davor(self):
        self.assertIn("musik", auswahl.waehlen(["Spiel was von Queen"]))
        g = auswahl.waehlen(["Kannst du das Limit für Kontakt B auf 5 setzen?", "Soll ich das so festhalten?", "Ja"])
        self.assertIn("regeln", g)

    def test_nur_letzte_nachrichten_zaehlen(self):
        alt = ["Spiel Musik"] + ["Und sonst?"] * auswahl.FENSTER
        self.assertNotIn("musik", auswahl.waehlen(alt))

    def test_geladene_bleiben(self):
        self.assertIn("pc", auswahl.waehlen(["Hallo"], {"pc"}))

    def test_jedes_modul_der_gruppen_gibt_es(self):
        alle = {m.__name__.rsplit(".", 1)[-1] for m in katalog._MODULE}
        for g, (module, _, _) in auswahl.GRUPPEN.items():
            self.assertTrue(set(module) <= alle, g)
        self.assertTrue(set(auswahl.KERN) <= alle)

    def test_kein_modul_in_zwei_gruppen_und_nichts_vergessen(self):
        alle = {m.__name__.rsplit(".", 1)[-1] for m in katalog._MODULE}
        belegt = [m for module, _, _ in auswahl.GRUPPEN.values() for m in module]
        self.assertEqual(len(belegt), len(set(belegt)))
        nur_besondere = {"kontakte", "gedanken", "anliegen", "regel_aktionen"}   # kontakte: nur Fremde; gedanken: Denkrunde/Fäden; anliegen: Kern; regel_aktionen: nur im Regel-Lauf
        self.assertEqual(alle - set(belegt) - set(auswahl.KERN), set() | (alle - set(belegt) - set(auswahl.KERN)) & nur_besondere)


class Katalog(unittest.TestCase):
    def test_filter_nach_modulen_spart_viel(self):
        voll = len(json.dumps(katalog.gemini_tools()))
        klein = len(json.dumps(katalog.gemini_tools(module=auswahl.module_von(set()))))
        self.assertLess(klein, voll * 0.4)
        self.assertIn("uhrzeit", namen(katalog.gemini_tools(module=set(auswahl.KERN))))
        self.assertNotIn("musik", namen(katalog.gemini_tools(module=set(auswahl.KERN))))


class Prompt(unittest.TestCase):
    def test_ohne_gruppen_ist_alles_drin_wie_vorher(self):
        from kern.gehirn import persona
        self.assertEqual(persona.faehigkeiten(None), persona.FAEHIGKEITEN)
        self.assertEqual(persona.faehigkeiten(set(auswahl.GRUPPEN)), persona.FAEHIGKEITEN)

    def test_gruppen_filtern_abschnitte_kern_bleibt(self):
        from kern.gehirn import persona
        kern = persona.faehigkeiten(set())
        self.assertLess(len(kern), len(persona.FAEHIGKEITEN) * 0.5)
        self.assertIn("uhrzeit", kern)             # Kern
        self.assertIn("merken", kern)
        self.assertNotIn("musik_pause", kern)
        self.assertIn("musik_pause", persona.faehigkeiten({"musik"}))
        self.assertIn("musik_pause", persona.faehigkeiten_zusatz({"musik"}))
        self.assertEqual(persona.faehigkeiten_zusatz(set()), "")

    def test_jede_zugeordnete_gruppe_gibt_es_und_trifft_einen_abschnitt(self):
        from kern.gehirn import persona
        for anfang, g in persona._FAEHIGKEIT_GRUPPE:
            self.assertIn(g, auswahl.GRUPPEN)
            self.assertTrue(any(t.startswith(anfang) and gr == g for gr, t in persona._faehigkeit_teile()), anfang)

    def test_chat_prompt_kuerzer_und_nachgeladene_gruppe_kommt_als_zusatz(self):
        antworten, systeme = [antwort({"functionCall": {"name": "werkzeuge_laden", "args": {"gruppen": ["musik"]}}}),
                              antwort({"text": "ok"})], []

        async def a(inhalt, **kw):
            systeme.append(inhalt["systemInstruction"]["parts"][0]["text"])
            return antworten.pop(0)

        with mock.patch.object(chat, "_gemini_anfrage", a):
            asyncio.run(chat.antworten([{"rolle": "du", "text": "Hallo"}]))
        self.assertNotIn("musik_pause", systeme[0])
        self.assertIn("musik_pause", systeme[1])
        self.assertIn("[ZUSATZ FÜR DIESEN ZUG]", systeme[1])


class Sprache(MitKonnektoren):
    def test_setup_tools_start_klein_mit_meta(self):
        from kern.sitzung import live
        t = namen(live._tools(set(live.START_GRUPPEN)))
        self.assertIn(auswahl.META, t)
        self.assertIn("musik", t)
        self.assertNotIn("pc_status", t)
        self.assertIn("pc_status", namen(live._tools(set(live.START_GRUPPEN) | {"pc"})))
        self.assertIn("pc_status", namen(live._tools(None)))      # None = alle, wie vor der Auswahl
        setup = live.setup_nachricht("x", None, None, {"pc"})
        self.assertIn(auswahl.META, namen(setup["setup"]["tools"]))

    def _mit(self, text="Fahr den PC runter"):
        from kern.sitzung import live
        gem = mock.Mock()
        gem.send = mock.AsyncMock()
        mit = mock.Mock()
        mit.puffer = {"du": text, "friday": ""}
        zustand = {"gruppen": set(live.START_GRUPPEN)}
        return live, gem, mit, zustand

    def test_nachladen_verbindet_neu_und_merkt_die_frage(self):
        live, gem, mit, zustand = self._mit()
        fc = [{"id": "1", "name": auswahl.META, "args": {"gruppen": ["pc"]}}]
        self.assertTrue(asyncio.run(live._nachladen(gem, zustand, mit, fc, [])))
        self.assertIn("pc", zustand["gruppen"])
        self.assertEqual(zustand["weiter"], "Fahr den PC runter")
        gem.send.assert_not_called()

    def test_unbekannte_oder_schon_geladene_gruppe_bleibt_in_der_sitzung(self):
        live, gem, mit, zustand = self._mit()
        for gruppen, wort in ((["quatsch"], "gibt es nicht"), (["musik"], "schon")):
            gem.send.reset_mock()
            fc = [{"id": "1", "name": auswahl.META, "args": {"gruppen": gruppen}}]
            self.assertFalse(asyncio.run(live._nachladen(gem, zustand, mit, fc, [])))
            self.assertIn(wort, gem.send.await_args.args[0])
            self.assertNotIn("weiter", zustand)

    def test_gleichzeitig_andere_aufrufe_werden_nicht_verschluckt(self):
        live, gem, mit, zustand = self._mit()
        fc = [{"id": "1", "name": auswahl.META, "args": {"gruppen": ["pc"]}}]
        self.assertFalse(asyncio.run(live._nachladen(gem, zustand, mit, fc, [{"name": "uhrzeit"}])))
        self.assertIn("pc", zustand["gruppen"])        # gilt ab dem nächsten Verbinden
        gem.send.assert_awaited_once()

    def test_live_prompt_filtert_wie_der_chat(self):
        from kern import config
        from kern.gehirn import persona
        klein = persona.live_prompt(config.jetzt(), gruppen=set())
        self.assertLess(len(klein), len(persona.live_prompt(config.jetzt())))
        self.assertNotIn("musik_pause", klein)


def antwort(*teile):
    return ({"candidates": [{"content": {"parts": list(teile)}}]}, "m")


class ChatSchleife(MitKonnektoren):
    def test_werkzeuge_laden_erweitert_den_naechsten_durchgang(self):
        antworten, gesehen = [antwort({"functionCall": {"name": "werkzeuge_laden", "args": {"gruppen": ["pc"]}}}),
                              antwort({"text": "fertig"})], []

        async def a(inhalt, **kw):
            gesehen.append(namen(inhalt["tools"]))   # Kopie zum Aufrufzeitpunkt: `inhalt` wird danach verändert
            return antworten.pop(0)

        with mock.patch.object(chat, "_gemini_anfrage", a):
            r = asyncio.run(chat.antworten([{"rolle": "du", "text": "Hallo"}]))
        erste, zweite = gesehen
        self.assertNotIn("pc_status", erste)
        self.assertIn(auswahl.META, erste)
        self.assertIn("pc_status", zweite)
        self.assertEqual(r["antwort"], "fertig")
        self.assertEqual(r["werkzeuge"].count(auswahl.META), 1)

    def test_unbekannte_gruppe_wird_abgelehnt(self):
        a = mock.AsyncMock(side_effect=[antwort({"functionCall": {"name": "werkzeuge_laden", "args": {"gruppen": ["quatsch"]}}}),
                                        antwort({"text": "ok"})])
        with mock.patch.object(chat, "_gemini_anfrage", a):
            asyncio.run(chat.antworten([{"rolle": "du", "text": "Hallo"}]))
        gesendet = json.dumps(a.await_args_list[1].args[0]["contents"], ensure_ascii=False)
        self.assertIn("gibt es nicht", gesendet)

    def test_schalter_aus_gibt_alle_werkzeuge(self):
        with mock.patch.dict("os.environ", {"WERKZEUG_AUSWAHL": "aus"}):
            self.assertNotIn(auswahl.META, namen(chat._tools(None, set())))
            self.assertIn("pc_status", namen(chat._tools(None, set())))

    def test_person_bekommt_keine_auswahl(self):
        self.assertNotIn(auswahl.META, namen(chat._tools({"id": "p1", "werkzeuge": ["uhrzeit"]}, set())))


if __name__ == "__main__":
    unittest.main()
