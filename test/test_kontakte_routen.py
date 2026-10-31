"""Kontakte-Routen: nur Eikes Tipp, Fehler als Text, kein Werkzeug fürs Modell."""
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern.dienste import benachrichtigungen
from kern.main import app
from kern.personen import rueckfragen, unbekannt
from kern.werkzeuge import katalog


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        p = mock.patch.object(benachrichtigungen, "senden", return_value={"gesendet": 0})
        p.start()
        self.addCleanup(p.stop)
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()


class Kontakte(Basis):
    def test_leer_liefert_werkzeugliste_der_waehlbaren(self):
        d = self.c.get("/api/kontakte").json()
        self.assertEqual(d["personen"], [])
        self.assertEqual(d["unbekannt"], [])
        namen = {w["name"] for w in d["werkzeuge"]}
        # Die Plan-Werkzeuge (Stundenplan, Kalender) steuert seit 14.10.2026 der Hebel „Eikes Plan“, nicht mehr ein Haken je Werkzeug.
        self.assertEqual(namen, {"uhrzeit", "suchen", "nachrichten", "termin_wunsch"})

    def test_person_bringt_status_austausch_und_arbeitsweisen_mit(self):
        pid = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz", "snapchat": "maya_x"}).json()["person"]["id"]
        p = next(x for x in self.c.get("/api/kontakte").json()["personen"] if x["id"] == pid)
        self.assertIn(p["snapchat_status"]["stufe"], {"gelb", "rot"})   # nie grün ohne Beweis
        self.assertIsNone(p["austausch"]["zeit"])
        self.assertEqual(p["arbeitsweisen"], [])
        d = self.c.get("/api/kontakte").json()
        self.assertEqual({w["gruppe"] for w in d["werkzeuge"]}, {"Allgemein", "Eikes Plan"})

    def test_anlegen_aendern_und_pausieren(self):
        r = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz", "snapchat": "maya_x", "persona": "Locker."}).json()
        self.assertTrue(r["ok"])
        pid = r["person"]["id"]
        r = self.c.post(f"/api/kontakte/{pid}", json={"werkzeuge": ["uhrzeit", "suchen"], "pausiert": True}).json()
        self.assertTrue(r["ok"])
        p = self.c.get("/api/kontakte").json()["personen"][0]
        self.assertEqual(p["werkzeuge"], ["uhrzeit", "suchen", "nachrichten"])      # Grundfunktionen immer
        self.assertTrue(p["pausiert"])

    def test_freundschaft_pruefen_legt_ansehen_auftrag_und_zeigt_stand(self):
        from kern.dienste import snapchat as dienst
        pid = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz", "snapchat": "maya_x"}).json()["person"]["id"]
        self.assertIn("unbekannt", self.c.get("/api/kontakte").json()["personen"][0]["snapchat_stand"].lower())
        self.assertTrue(self.c.post(f"/api/kontakte/{pid}/freundschaft").json()["ok"])
        self.assertEqual(dienst.auftrag_lesen()["art"], "stand")
        ohne = self.c.post("/api/kontakte", json={"name": "Ohne Snap"}).json()["person"]["id"]
        self.assertFalse(self.c.post(f"/api/kontakte/{ohne}/freundschaft").json()["ok"])

    def test_senden_erlaubt_ueber_route(self):
        r = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz", "snapchat": "maya_x"}).json()
        pid = r["person"]["id"]
        self.assertFalse(r["person"].get("senden_erlaubt"))
        r = self.c.post(f"/api/kontakte/{pid}", json={"senden_erlaubt": True}).json()
        self.assertTrue(r["ok"])
        p = self.c.get("/api/kontakte").json()["personen"][0]
        self.assertTrue(p["senden_erlaubt"])

    def test_fehler_kommen_als_text_nicht_als_absturz(self):
        self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz", "snapchat": "maya"})
        r = self.c.post("/api/kontakte", json={"name": "Zweite Person", "snapchat": "MAYA"}).json()
        self.assertFalse(r["ok"])
        self.assertIn("gehört schon", r["text"])
        r = self.c.post("/api/kontakte", json={"name": "X", "snapchat": "Eike"}).json()
        self.assertFalse(r["ok"])
        r = self.c.post("/api/kontakte/p99", json={"name": "X"}).json()
        self.assertFalse(r["ok"])
        r = self.c.post("/api/kontakte/../x", json={})
        self.assertIn(r.status_code, (404, 405, 422))

    def test_module_setzen_und_lesen(self):
        pid = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz", "snapchat": "maya_x"}).json()["person"]["id"]
        r = self.c.post(f"/api/kontakte/{pid}", json={"module": {"flammen": True, "plan": "frei"}}).json()
        self.assertTrue(r["ok"], r)
        p = next(x for x in self.c.get("/api/kontakte").json()["personen"] if x["id"] == pid)
        self.assertTrue(p["module"]["flammen"])
        self.assertEqual(p["module"]["plan"], "frei")
        self.assertIn("flammen", p["module_wirksam"])
        self.assertIn("plan", p["module_wirksam"])
        self.assertIn("kalender_frei", p["werkzeuge"])
        self.assertFalse(p["garantie_profil"])

    def test_ungueltiges_modul_gibt_klartext_und_aendert_nichts(self):
        pid = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz", "snapchat": "maya_x"}).json()["person"]["id"]
        r = self.c.post(f"/api/kontakte/{pid}", json={"module": {"flammen": "ja"}}).json()
        self.assertFalse(r["ok"])
        self.assertIn("flammen", r["text"])
        self.assertFalse(self.c.get("/api/kontakte").json()["personen"][0]["module"]["flammen"])

    def test_garantie_an_ohne_profil_klartext(self):
        pid = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz", "snapchat": "maya_x"}).json()["person"]["id"]
        r = self.c.post(f"/api/kontakte/{pid}", json={"module": {"garantie": "an"}}).json()
        self.assertFalse(r["ok"])
        self.assertIn("Profil", r["text"])

    def test_modul_aenderung_aus_der_app_steht_im_protokoll(self):
        import json
        from kern import config
        pid = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz", "snapchat": "maya_x"}).json()["person"]["id"]
        self.c.post(f"/api/kontakte/{pid}", json={"module": {"flammen": True}})
        z = (config.daten_ordner() / "modul_protokoll.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(z[-1])["von"], "app")

    def test_werkzeug_auf_eikes_daten_wird_auch_ueber_die_route_abgelehnt(self):
        pid = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz"}).json()["person"]["id"]
        for boese in ("wissen", "whatsapp_lesen", "merken"):
            r = self.c.post(f"/api/kontakte/{pid}", json={"werkzeuge": ["uhrzeit", boese]}).json()
            self.assertFalse(r["ok"], boese)
        self.assertEqual(self.c.get("/api/kontakte").json()["personen"][0]["werkzeuge"], ["uhrzeit", "suchen", "nachrichten"])
        # Daten-Rechte seit 04.10.2026 wählbar (Eikes Auftrag, je Person ein Haken):
        r = self.c.post(f"/api/kontakte/{pid}", json={"werkzeuge": ["uhrzeit", "kalender_frei"]}).json()
        self.assertTrue(r["ok"])
        self.assertEqual(self.c.get("/api/kontakte").json()["personen"][0]["werkzeuge"],
                         ["uhrzeit", "suchen", "nachrichten", "kalender_frei"])

    def test_unbekannte_erscheinen_werden_angelegt_oder_ignoriert(self):
        unbekannt.melden("snapchat", "Test")
        unbekannt.melden("snapchat", "Spam")
        d = self.c.get("/api/kontakte").json()
        self.assertEqual(sorted(u["anzeige"] for u in d["unbekannt"]), ["Spam", "Test"])
        self.assertTrue(self.c.post("/api/kontakte/unbekannt/ignorieren",
                                    json={"kanal": "snapchat", "anzeige": "Spam"}).json()["ok"])
        self.c.post("/api/kontakte", json={"name": "Testfreund Schulz", "snapchat": "Test"})
        d = self.c.get("/api/kontakte").json()
        self.assertEqual(d["unbekannt"], [])

    def test_bekannte_personen_route_und_anlegen_mit_graph_name(self):
        from kern.personen import bekannt
        antwort = {"personen": [{"name": "Kontakt B Schulz", "quellen": ["Wissen"]}], "ausgefallen": []}
        with mock.patch.object(bekannt, "liste", mock.AsyncMock(return_value=antwort)):
            self.assertEqual(self.c.get("/api/kontakte/bekannt").json(), antwort)
        r = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz", "snapchat": "maya_x", "graph_name": "Kontakt B Schulz"}).json()
        self.assertEqual(r["person"]["graph_name"], "Kontakt B Schulz")
        r = self.c.post("/api/kontakte", json={"name": "Kontakt B2", "snapchat": "m2", "graph_name": " kontakt b schulz "}).json()
        self.assertFalse(r["ok"])

    def test_freigeben_und_widerrufen_ueber_die_routen(self):
        from kern.gedaechtnis import graph
        pid = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz"}).json()["person"]["id"]
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)) as ein, \
             mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=True)) as weg:
            r = self.c.post(f"/api/kontakte/{pid}/freigaben", json={"text": "Eike mag Schach.", "anlass": "Frage"}).json()
            self.assertTrue(r["ok"])
            fid = r["freigabe"]["id"]
            self.assertEqual(ein.await_args.kwargs["gruppe"], pid)
            self.assertEqual(self.c.get("/api/kontakte").json()["personen"][0]["freigaben"][0]["status"], "aktiv")
            r = self.c.post(f"/api/kontakte/{pid}/freigaben/{fid}/widerruf", json={}).json()
            self.assertTrue(r["ok"])
            self.assertEqual(r["freigabe"]["status"], "widerrufen")
            weg.assert_awaited_once()

    def test_freigabe_fehler_kommen_als_text(self):
        from kern.gedaechtnis import graph
        pid = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz"}).json()["person"]["id"]
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=False)):
            r = self.c.post(f"/api/kontakte/{pid}/freigaben", json={"text": "X"}).json()
        self.assertFalse(r["ok"])
        self.assertIn("nichts freigegeben", r["text"])
        for url in ("/api/kontakte/p99/freigaben", "/api/kontakte/../freigaben"):
            r = self.c.post(url, json={"text": "X"})
            self.assertTrue(r.status_code in (404, 405) or r.json().get("ok") is False)
        self.assertFalse(self.c.post(f"/api/kontakte/{pid}/freigaben/x9/widerruf", json={}).json()["ok"])
        self.assertFalse(self.c.post(f"/api/kontakte/{pid}/freigaben", json={"text": ""}).json().get("ok", False))

    def test_freigaben_routen_sperren_fremde_herkunft(self):
        for url in ("/api/kontakte/p1/freigaben", "/api/kontakte/p1/freigaben/f1/widerruf"):
            r = self.c.post(url, json={"text": "X"}, headers={"Origin": "https://boese.example"})
            self.assertEqual(r.status_code, 403, url)

    def test_rueckfragen_erscheinen_werden_freigegeben_oder_abgelehnt(self):
        from kern.gedaechtnis import graph
        from kern.personen import rueckfragen
        pid = self.c.post("/api/kontakte", json={"name": "Kontakt B Schulz", "snapchat": "maya_x"}).json()["person"]["id"]
        a = rueckfragen.stellen(pid, "Geburtstag", "Wann?")
        b = rueckfragen.stellen(pid, "Wohnort", "Wo?")
        d = self.c.get("/api/kontakte").json()
        self.assertEqual(sorted(x["thema"] for x in d["rueckfragen"]), ["Geburtstag", "Wohnort"])
        self.assertEqual(d["rueckfragen"][0]["person_name"], "Kontakt B Schulz")
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)):
            r = self.c.post(f"/api/kontakte/rueckfragen/{a['id']}/freigeben", json={"text": "Eike hat im Oktober Geburtstag."}).json()
        self.assertTrue(r["ok"])
        self.assertEqual(r["rueckfrage"]["status"], "freigegeben")
        r = self.c.post(f"/api/kontakte/rueckfragen/{b['id']}/ablehnen", json={}).json()
        self.assertEqual(r["rueckfrage"]["status"], "abgelehnt")
        self.assertEqual(self.c.get("/api/kontakte").json()["rueckfragen"], [])

    def test_rueckfragen_fehler_kommen_als_text_und_fremde_herkunft_ist_gesperrt(self):
        self.assertFalse(self.c.post("/api/kontakte/rueckfragen/a99/ablehnen", json={}).json()["ok"])
        self.assertFalse(self.c.post("/api/kontakte/rueckfragen/../freigeben", json={"text": "x"}).json().get("ok", False))
        self.assertFalse(self.c.post("/api/kontakte/rueckfragen/a1/freigeben", json={"text": ""}).json().get("ok", False))
        for url in ("/api/kontakte/rueckfragen/a1/freigeben", "/api/kontakte/rueckfragen/a1/ablehnen"):
            r = self.c.post(url, json={"text": "X"}, headers={"Origin": "https://boese.example"})
            self.assertEqual(r.status_code, 403, url)

    def test_fremde_herkunft_ist_gesperrt(self):
        for methode, url, body in (("post", "/api/kontakte", {"name": "X"}),
                                   ("post", "/api/kontakte/p1", {"name": "X"}),
                                   ("post", "/api/kontakte/unbekannt/ignorieren", {"kanal": "snapchat", "anzeige": "X"})):
            r = getattr(self.c, methode)(url, json=body, headers={"Origin": "https://boese.example"})
            self.assertEqual(r.status_code, 403, url)
        self.assertEqual(self.c.get("/api/kontakte").json()["personen"], [])

    def test_kein_werkzeug_fuers_modell_legt_personen_an_oder_aendert_rechte(self):
        namen = {e["name"] for m in katalog._MODULE for e in m.ERKLAERUNGEN}
        for n in namen:
            if n in ("kontakt_anfragen", "anfrage_senden", "snapchat_kontakte"):
                continue  # Anfragen nur mit Eikes Ja; snapchat_kontakte liest nur — legt niemanden an
            self.assertNotIn("kontakt", n)
            self.assertNotIn("person_anlegen", n)

    def test_termin_annehmen_traegt_ein(self):
        from kern.dienste import icloud
        pid = self.c.post("/api/kontakte", json={"name": "Mara Veen"}).json()["person"]["id"]
        aid = rueckfragen.termin_wunsch_stellen(pid, "Kaffee", "2030-10-10T15:00")["id"]
        with mock.patch.object(icloud, "anlegen", return_value={"ok": True}):
            r = self.c.post(f"/api/kontakte/rueckfragen/{aid}/annehmen", json={}).json()
        self.assertTrue(r["ok"])
        self.assertEqual(r["rueckfrage"]["status"], "eingetragen")
        r = self.c.post(f"/api/kontakte/rueckfragen/{aid}/annehmen", json={}).json()
        self.assertFalse(r["ok"])   # kein zweites Mal

    def test_offene_rueckfragen_zeigen_kollision(self):
        pid = self.c.post("/api/kontakte", json={"name": "Mara Veen"}).json()["person"]["id"]
        rueckfragen.termin_wunsch_stellen(pid, "Kaffee", "2030-10-10T15:00")
        d = self.c.get("/api/kontakte").json()
        termin = [x for x in d["rueckfragen"] if x.get("art") == "termin"][0]
        self.assertIn("kollision", termin)

    def test_verknuepfen_und_loesen_ueber_route(self):
        from kern.dienste import whatsapp
        from kern.garantie import ablage as garantieablage
        pid = self.c.post("/api/kontakte", json={"name": "Kontakt A"}).json()["person"]["id"]
        with mock.patch.object(whatsapp, "frei", return_value=[{"id": "1@c.us", "name": "Kontakt A"}]):
            r = self.c.post(f"/api/kontakte/{pid}/verknuepfen", json={"quelle": "whatsapp", "ref": "1@c.us"}).json()
            self.assertTrue(r["ok"])
            d = self.c.get("/api/kontakte").json()
            self.assertEqual(d["quellen"][pid]["whatsapp"], "Kontakt A")
        r = self.c.post(f"/api/kontakte/{pid}/entknuepfen", json={"quelle": "whatsapp"}).json()
        self.assertTrue(r["ok"])
        r = self.c.post(f"/api/kontakte/{pid}/verknuepfen", json={"quelle": "mail", "ref": "x"}).json()
        self.assertFalse(r["ok"])

    def test_namen_brauchen_vor_und_nachnamen(self):
        # 04.10.2026, Eike: keine Verwechslung — Personen immer mit vollem Namen.
        r = self.c.post("/api/kontakte", json={"name": "Kontakt"}).json()
        self.assertFalse(r["ok"])
        r = self.c.post("/api/kontakte", json={"name": "Kontakt A"}).json()
        self.assertTrue(r["ok"])
        pid = r["person"]["id"]
        r = self.c.post(f"/api/kontakte/{pid}", json={"name": "Kontakt"}).json()
        self.assertFalse(r["ok"])


class Anfragen(Basis):
    def test_anfragen_liste_und_rueckzug(self):
        from kern.dienste import snapchat as dienst
        dienst.anfrage_legen("mara.v")
        d = self.c.get("/api/kontakte/anfragen").json()
        self.assertEqual([a["snapchat"] for a in d["anfragen"]], ["mara.v"])
        r = self.c.post("/api/kontakte/anfragen/zurueckziehen", json={"snapchat": "mara.v"}).json()
        self.assertTrue(r["ok"])
        self.assertEqual(self.c.get("/api/kontakte/anfragen").json()["anfragen"], [])

    def test_angenommene_anfrage_mit_angelegter_person_verschwindet_aus_der_liste(self):
        from kern.dienste import snapchat as dienst
        from kern.personen import register
        dienst.anfrage_legen("titus_g", name="Kontakt S G", titel="Kontakt S G.")
        dienst.anfrage_setzen("titus_g", "angenommen")
        self.assertEqual(len(self.c.get("/api/kontakte").json()["anfragen"]), 1)
        register.anlegen("Kontakt S G", snapchat="Kontakt S G.")
        self.assertEqual(self.c.get("/api/kontakte").json()["anfragen"], [])

    def test_fremde_herkunft_ist_gesperrt(self):
        r = self.c.post("/api/kontakte/anfragen/zurueckziehen", json={"snapchat": "mara.v"},
                        headers={"Origin": "https://boese.example"})
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
