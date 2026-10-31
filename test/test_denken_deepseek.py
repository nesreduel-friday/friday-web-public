"""DeepSeek-Adapter der Denkrunde: Gemini-Format <-> OpenAI-Format, Schlüssel, Tokenzählung, ganze Runde mit Attrappe.

Dazu die Aufzeichnung vom 29.10.2026 (`test/daten/deepseek_dsml_178.json`): DeepSeek-Flash schrieb den Werkzeugaufruf zusätzlich
als „DSML“-Markup in `content`, und das Markup landete als `runden.ergebnis` in der Anzeige. `content` der Datei ist wörtlich
`runden.ergebnis` der Runde 178 (dort auf 500 Zeichen gekürzt, deshalb endet das Markup ohne schließendes Tag); `tool_calls` sind
aus dem Markup nachgebaut, weil die Datenbank keine Argumente speichert. Kein echter API-Aufruf — der Schlüssel in `Basis` ist
erfunden."""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kern.denken import buch as gedankenbuch
from kern.denken import deepseek, runde as denkrunde

DATEN = Path(__file__).parent / "daten"
MARKUP = json.loads((DATEN / "deepseek_dsml_178.json").read_text(encoding="utf-8"))


class Antwort:
    def __init__(self, daten, code=200):
        self._d, self.status_code, self.text = daten, code, json.dumps(daten)

    def json(self):
        return self._d


def nachricht(content=None, aufrufe=None, rueck=None, usage=None):
    m = {"role": "assistant", "content": content}
    if rueck:
        m["reasoning_content"] = rueck
    if aufrufe:
        m["tool_calls"] = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                           for i, (n, a) in enumerate(aufrufe)]
    return {"choices": [{"message": m}], "usage": usage or {"prompt_tokens": 10, "completion_tokens": 4}}


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        schl = self.tmp / "deepseek.key"
        schl.write_text("sk-test-geheim\n")
        u = mock.patch.dict(os.environ, {"FRIDAY_DATEN": str(self.tmp / "daten"), "DEEPSEEK_KEY_DATEI": str(schl)})
        u.start()
        self.addCleanup(u.stop)


class Uebersetzer(Basis):
    def test_nachrichten_system_werkzeugaufruf_und_antwort(self):
        inhalt = {"systemInstruction": {"parts": [{"text": "Regeln"}]}, "contents": [
            {"role": "user", "parts": [{"text": "Denke"}]},
            {"role": "model", "parts": [{"text": ""}, {"_reasoning": "hm"}, {"functionCall": {"id": "c0", "name": "wissen", "args": {"frage": "x"}}}]},
            {"role": "user", "parts": [{"functionResponse": {"id": "c0", "name": "wissen", "response": {"ok": True}}}]}]}
        m = deepseek._nachrichten(inhalt)
        self.assertEqual([x["role"] for x in m], ["system", "user", "assistant", "tool"])
        self.assertEqual(m[2]["tool_calls"][0]["function"]["name"], "wissen")
        self.assertEqual(json.loads(m[2]["tool_calls"][0]["function"]["arguments"]), {"frage": "x"})
        self.assertEqual(m[2]["reasoning_content"], "hm")
        self.assertEqual(m[3]["tool_call_id"], "c0")

    def test_werkzeuge_typen_klein(self):
        t = deepseek._werkzeuge([{"functionDeclarations": [{"name": "a", "description": "d", "parameters": {
            "type": "OBJECT", "properties": {"x": {"type": "STRING"}, "n": {"type": "ARRAY", "items": {"type": "INTEGER"}}}}}]}])
        p = t[0]["function"]["parameters"]
        self.assertEqual((p["type"], p["properties"]["x"]["type"], p["properties"]["n"]["items"]["type"]), ("object", "string", "integer"))

    def test_antwort_wird_gemini_form_mit_usage(self):
        a = deepseek._antwort(nachricht("Text", [("wissen", {"frage": "q"})], rueck="denk",
                                        usage={"prompt_tokens": 7, "completion_tokens": 3, "completion_tokens_details": {"reasoning_tokens": 2}}))
        teile = a["candidates"][0]["content"]["parts"]
        self.assertEqual(teile[0]["text"], "Text")
        self.assertEqual(teile[1]["functionCall"], {"id": "c0", "name": "wissen", "args": {"frage": "q"}})
        self.assertEqual(a["usageMetadata"]["thoughtsTokenCount"], 2)

    def test_kaputte_argumente_werden_leer(self):
        roh = nachricht(None, [("x", {})])
        roh["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{kaputt"
        self.assertEqual(deepseek._antwort(roh)["candidates"][0]["content"]["parts"][0]["functionCall"]["args"], {})


class MarkupStattText(unittest.TestCase):
    """29.10.2026: Markup im `content` ist kein Antworttext — die Werkzeugaufrufe aus `tool_calls` bleiben trotzdem."""

    def teile(self):
        return deepseek._antwort(json.loads(json.dumps(MARKUP)))["candidates"][0]["content"]["parts"]

    def test_markup_wird_kein_antworttext(self):
        teile = self.teile()
        self.assertEqual([t for t in teile if "text" in t], [])              # „kein Text, nur Werkzeuge“
        self.assertNotIn("DSML", json.dumps(teile, ensure_ascii=False))

    def test_aufruf_aus_tool_calls_bleibt_erhalten(self):
        aufrufe = [t["functionCall"] for t in self.teile() if "functionCall" in t]
        self.assertEqual([a["name"] for a in aufrufe], ["gedanke_festhalten"])
        self.assertTrue(aufrufe[0]["args"]["text"].startswith("Lehrkraft wollte bis 26.10."))
        self.assertEqual(len(aufrufe[0]["args"]["quellen"]), 1)

    def test_text_vor_dem_markup_bleibt(self):
        roh = json.loads(json.dumps(MARKUP))
        roh["choices"][0]["message"]["content"] = "Ich sehe nach. " + roh["choices"][0]["message"]["content"]
        teile = deepseek._antwort(roh)["candidates"][0]["content"]["parts"]
        self.assertEqual(teile[0]["text"], "Ich sehe nach.")

    def test_normaler_text_bleibt_unveraendert(self):
        for text in ("Fertig.", " Ein | und < und > im Satz bleibt. "):
            self.assertEqual(deepseek._antwort(nachricht(text))["candidates"][0]["content"]["parts"][0]["text"], text)

    def test_markup_varianten_werden_erkannt(self):
        for form in ("<｜｜DSML｜｜ calls>", "<|DSML|> calls>", "<||DSML||>", "<｜DSML｜> "):
            self.assertTrue(deepseek.hat_markup(form), form)
            self.assertEqual(deepseek.ohne_markup("Text. " + form + " Rest"), "Text.")
        self.assertFalse(deepseek.hat_markup("Ein Satz über DSML ohne Markup."))


class Anfrage(Basis):
    def run_(self, post, inhalt=None):
        return asyncio.run(deepseek.anfrage(inhalt or {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}, post=post))

    def test_schluessel_im_header_nie_im_koerper_und_tokens_gezaehlt(self):
        gesehen = {}

        async def post(url, koerper, kopf):
            gesehen.update(url=url, koerper=koerper, kopf=kopf)
            return Antwort(nachricht("ok"))
        antwort, modell = self.run_(post)
        self.assertEqual(modell, "deepseek:deepseek-flash")
        self.assertEqual(gesehen["kopf"]["Authorization"], "Bearer sk-test-geheim")
        self.assertNotIn("sk-test-geheim", json.dumps(gesehen["koerper"]))
        from kern.verbrauch import tokens
        self.assertEqual(tokens.uebersicht()["eintraege"][0]["bereich"], "denken")

    def test_400_ohne_denkparameter_wird_einmal_wiederholt(self):
        koerper = []

        async def post(url, k, kopf):
            koerper.append(k)
            return Antwort({"error": "thinking?"}, 400) if "thinking" in k else Antwort(nachricht("ok"))
        antwort, _ = self.run_(post)
        self.assertEqual(len(koerper), 2)
        self.assertNotIn("reasoning_effort", koerper[1])
        self.assertEqual(antwort["candidates"][0]["content"]["parts"][0]["text"], "ok")

    def test_markup_ohne_aufrufe_wird_einmal_ohne_denken_wiederholt(self):
        koerper = []
        markup = MARKUP["choices"][0]["message"]["content"]

        async def post(url, k, kopf):
            koerper.append(k)
            return Antwort({"choices": [{"message": {"role": "assistant", "content": markup}}]} if len(koerper) == 1
                           else nachricht("Fertig."))
        antwort, _ = self.run_(post)
        self.assertEqual(len(koerper), 2)
        self.assertNotIn("thinking", koerper[1])
        self.assertNotIn("reasoning_effort", koerper[1])
        self.assertEqual(antwort["candidates"][0]["content"]["parts"][0]["text"], "Fertig.")

    def test_markup_mit_aufrufen_wird_nicht_wiederholt(self):
        koerper = []

        async def post(url, k, kopf):
            koerper.append(k)
            return Antwort(json.loads(json.dumps(MARKUP)))
        antwort, _ = self.run_(post)
        self.assertEqual(len(koerper), 1)                    # nichts doppelt auslösen
        self.assertEqual(antwort["candidates"][0]["content"]["parts"][0]["functionCall"]["name"], "gedanke_festhalten")

    def test_bleibt_es_beim_markup_ohne_aufrufe_gibt_es_einen_fehler(self):
        from kern.sitzung.chat import ChatFehler
        markup = MARKUP["choices"][0]["message"]["content"]
        koerper = []

        async def post(url, k, kopf):
            koerper.append(k)
            return Antwort({"choices": [{"message": {"role": "assistant", "content": markup}}]})
        with self.assertRaises(ChatFehler) as e:
            self.run_(post)
        self.assertEqual(len(koerper), 2)                    # genau eine Wiederholung, kein Kreislauf
        self.assertIn("nur als Text", str(e.exception))
        self.assertNotIn("sk-test-geheim", str(e.exception))

    def test_fehlender_schluessel_gibt_chatfehler(self):
        from kern.sitzung.chat import ChatFehler
        with mock.patch.dict(os.environ, {"DEEPSEEK_KEY_DATEI": str(self.tmp / "gibtsnicht")}), \
                mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            with self.assertRaises(ChatFehler):
                self.run_(None)

    def test_http_fehler_schwaerzt_und_kuerzt(self):
        from kern.sitzung.chat import ChatFehler

        async def post(*a):
            return Antwort({"error": "x" * 900}, 402)
        with self.assertRaises(ChatFehler) as e:
            self.run_(post)
        self.assertIn("402", str(e.exception))
        self.assertLessEqual(len(str(e.exception)), 320)


class GanzeRunde(Basis):
    def test_runde_mit_werkzeug_und_haltung(self):
        b = gedankenbuch.Buch(self.tmp / "daten")
        gesendet = []
        folge = [nachricht(None, [("gedanke_festhalten", {"art": "erkenntnis", "text": "Ein neuer Gedanke.", "quellen": ["wissen: a", "wissen: b"]})]),
                 nachricht("Fertig.")]

        async def post(url, koerper, kopf):
            gesendet.append(koerper)
            return Antwort(folge.pop(0))

        async def anfrage(inhalt, bevorzugt=None, schluessel_liste=None):
            return await deepseek.anfrage(inhalt, bevorzugt, schluessel_liste, post=post)
        with mock.patch("kern.denken.kontext.baue", return_value="Überblick"):
            d = asyncio.run(denkrunde.runde_deepseek(b, anfrage=anfrage))
        self.assertTrue(d["ok"], d["fehler"])
        system = gesendet[0]["messages"][0]["content"]
        self.assertTrue(system.startswith("Dein Auftraggeber"))
        self.assertIn("höchster Stelle", system)
        self.assertIn("Du bist Friday", system)                          # der bisherige Prompt steht dahinter
        namen = {t["function"]["name"] for t in gesendet[0]["tools"]}
        self.assertIn("gedanke_festhalten", namen)                       # dieselben Werkzeuge wie bei Gemini
        self.assertEqual(gesendet[1]["messages"][-1]["role"], "tool")    # Ergebnis ging zurück an DeepSeek
        self.assertEqual(b.runden(1)[0]["modell"], "deepseek:deepseek-flash")

    def test_runde_speichert_markup_nicht_als_ergebnis(self):
        """Der Fehler vom 29.10.2026: Markup stand als `runden.ergebnis` in der Anzeige, der Aufruf fehlte."""
        b = gedankenbuch.Buch(self.tmp / "daten")
        gesendet, folge = [], [json.loads(json.dumps(MARKUP)), nachricht("Fertig.")]

        async def post(url, koerper, kopf):
            gesendet.append(koerper)
            return Antwort(folge.pop(0))

        async def anfrage(inhalt, bevorzugt=None, schluessel_liste=None):
            return await deepseek.anfrage(inhalt, bevorzugt, schluessel_liste, post=post)
        with mock.patch("kern.denken.kontext.baue", return_value="Überblick"):
            d = asyncio.run(denkrunde.runde_deepseek(b, anfrage=anfrage))
        self.assertTrue(d["ok"], d["fehler"])
        runde = b.runden(1)[0]
        self.assertEqual(runde["ergebnis"], "Fertig.")
        self.assertIn("gedanke_festhalten", runde["werkzeuge"])            # der Aufruf ging nicht verloren
        self.assertEqual(d["gedanken"], 1)
        self.assertNotIn("DSML", json.dumps(gesendet, ensure_ascii=False))  # Markup geht nicht zurück ans Modell

    def test_backend_deepseek_ist_waehlbar(self):
        self.assertIn("deepseek", denkrunde.BACKENDS)


if __name__ == "__main__":
    unittest.main()
