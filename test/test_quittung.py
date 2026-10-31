"""Quittungen vom Kern (P2 der Ehrlichkeit, 12.10.2026): Jede Wirkung wird als Fakt-Zeile gezeigt und protokolliert — im Chat
(Rückgabe, Protokoll, chat.js) und im Live-Kanal (Event `quittung`, Protokoll, sprechen.js/chat.js). Vom Modell unabhängig."""

import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kern.ehrlichkeit import wirkung as w

WEB = Path(__file__).resolve().parent.parent / "web"


def lauf(c):
    return asyncio.run(c)


def wk(status, art, quittung, name="x"):
    return {"name": name, "status": status, "art": art, "quittung": quittung}


class Auswahl(unittest.TestCase):
    def test_ok_und_nicht_der_wirkungswerkzeuge_kommen(self):
        q = w.quittungen([wk("ok", "vorgabe", "Festgehalten: immer kurz."), wk("nicht", "versand", "Nicht gesendet: Mara ist unklar."),
                          wk("rueckfrage", "skill", "Wartet auf Eikes Ja.")])
        self.assertEqual(q, [{"text": "Festgehalten: immer kurz.", "status": "ok"},
                             {"text": "Nicht gesendet: Mara ist unklar.", "status": "nicht"},
                             {"text": "Wartet auf Eikes Ja.", "status": "rueckfrage"}])

    def test_lesende_machen_keine_quittung_ausser_bei_fehlschlag(self):
        self.assertEqual(w.quittungen([wk("ok", "lesen", "Nachgesehen.")]), [])
        self.assertEqual(w.quittungen([wk("nicht", "lesen", "Nicht nachgesehen.")]),
                         [{"text": "Nicht nachgesehen.", "status": "nicht"}])

    def test_blosses_erledigt_entfaellt_aber_nicht_der_fehlschlag(self):
        self.assertEqual(w.quittungen([wk("ok", "sonstiges", "Erledigt.")]), [])
        self.assertEqual(w.quittungen([wk("nicht", "sonstiges", "Nicht erledigt.")]),
                         [{"text": "Nicht erledigt.", "status": "nicht"}])
        self.assertEqual(len(w.quittungen([wk("ok", "sonstiges", "Erledigt: Termin am Montag.")])), 1)

    def test_doppelte_hintereinander_einmal_und_kaputtes_ignoriert(self):
        z = wk("ok", "merken", "Gemerkt: Kontakt Ps Geburtstag.")
        self.assertEqual(len(w.quittungen([z, dict(z), z])), 1)
        self.assertEqual(w.quittungen([{"status": "ok"}, {"status": "komisch", "quittung": "x"}, None]), [])

    def test_ohne_technikwoerter(self):
        from kern.werkzeuge import katalog
        for art in w.ARTEN:
            for st in w.STATI:
                self.assertIsNone(w.TECHNIK.search(w.quittung_bauen(art, st, "Skill Entwurf Nr. 3")), (art, st))


class Chat(unittest.TestCase):
    def test_antworten_liefert_quittungen(self):
        from kern.sitzung import chat
        antworten = iter([
            ({"candidates": [{"content": {"parts": [{"functionCall": {"name": "uhrzeit", "args": {}, "id": "1"}},
                                                    {"functionCall": {"name": "vorgabe_merken", "args": {"text": "x"}, "id": "2"}}]}}]}, "m"),
            ({"candidates": [{"content": {"parts": [{"text": "Fertig."}]}}]}, "m"),
        ])

        async def gemini(inhalt, bevorzugt=None):
            return next(antworten)

        async def ausf(name, *a, **k):
            if name == "uhrzeit":
                return {"ergebnis": "10 Uhr", "status": "ok", "art": "lesen", "quittung": "Nachgesehen."}
            return {"ergebnis": "NICHT ERLEDIGT: Limit.", "status": "nicht", "art": "vorgabe", "quittung": "Nicht festgehalten: Limit."}

        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "AIzaTEST"}), \
             mock.patch.object(chat, "_gemini_anfrage", gemini), mock.patch.object(chat.werkzeuge, "ausfuehren", ausf):
            r = lauf(chat.antworten([{"rolle": "du", "text": "Halt das fest"}]))
        self.assertEqual(r["quittungen"], [{"text": "Nicht festgehalten: Limit.", "status": "nicht"}])

    def test_main_protokolliert_quittungen_mit_art(self):
        from fastapi.testclient import TestClient
        from kern.protokoll import speicher
        tmp = tempfile.mkdtemp()
        erg = {"antwort": "Das hat nicht geklappt.", "werkzeuge": ["vorgabe_merken"], "werkzeug_ergebnisse": [], "aktionen": [], "links": [],
               "wirkungen": [], "quittungen": [{"text": "Nicht festgehalten: Limit.", "status": "nicht"}]}

        async def fake(*a, **k):
            return erg
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tmp, "GEMINI_API_KEY": "AIzaTEST"}):
            from kern import main
            with mock.patch.object(main.chat, "antworten", fake):
                r = TestClient(main.app).post("/api/chat", json={"nachrichten": [{"rolle": "du", "text": "Merk dir das"}]})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["quittungen"], erg["quittungen"])
            b = speicher.Protokoll(Path(tmp)).beitraege(r.json()["gespraech"])
        self.assertIn(("system", "quittung", "Nicht festgehalten: Limit."), [(x["rolle"], x["art"], x["text"]) for x in b])


class Live(unittest.TestCase):
    def test_event_und_protokoll_fuer_ok_und_nicht(self):
        from kern.sitzung import live
        gesendet, protokoll = [], []

        class Gem:
            async def send(self, s):
                pass

        class Br:
            async def send_json(self, m):
                gesendet.append(m)

        mit = mock.MagicMock()
        mit.gespraech = 7
        mit.proto.beitrag.side_effect = lambda *a: protokoll.append(a)
        folgen = iter([
            {"ergebnis": "ok", "status": "ok", "art": "merken", "quittung": "Gemerkt: Tee."},
            {"ergebnis": "NICHT ERLEDIGT: x", "status": "nicht", "art": "versand", "quittung": "Nicht gesendet: Mara unklar."},
            {"ergebnis": "10 Uhr", "status": "ok", "art": "lesen", "quittung": "Nachgesehen."},
        ])

        async def ausf(name, *a, **k):
            return next(folgen)
        aufruf = {"functionCalls": [{"id": str(i), "name": n, "args": {"i": i}} for i, n in enumerate(["merken", "schicke_nachricht", "uhrzeit"])]}
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()}), mock.patch.object(live.werkzeuge, "ausfuehren", ausf):
            lauf(live._werkzeuge_ausfuehren(Gem(), Br(), aufruf, mit, wirkungen=[]))
        q = [m for m in gesendet if m["type"] == "quittung"]
        self.assertEqual(q, [{"type": "quittung", "text": "Gemerkt: Tee.", "status": "ok"},
                             {"type": "quittung", "text": "Nicht gesendet: Mara unklar.", "status": "nicht"}])
        self.assertIn((7, "system", "Gemerkt: Tee.", "quittung"), protokoll)
        self.assertIn((7, "system", "Nicht gesendet: Mara unklar.", "quittung"), protokoll)


class Seite(unittest.TestCase):
    def test_web_zeigt_quittungen(self):
        chat, sprechen, app, css = (( WEB / n).read_text(encoding="utf-8") for n in ("chat.js", "sprechen.js", "app.js", "app.css"))
        self.assertIn("quittung(", app)
        self.assertRegex(chat, r"d\.quittungen")
        self.assertIn("live.auf('quittung'", chat)
        self.assertIn("case 'quittung'", sprechen)
        self.assertIn(".zeile.quittung", css)


if __name__ == "__main__":
    unittest.main()
