"""Wissen-Seite: Detailseite zu einem Eintrag (Eckdaten, Gruppen, antippbare Verknüpfungen) und der Filter für Einmaliges."""

import asyncio
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from kern.gedaechtnis import antwort, seite

U1 = "11111111-1111-1111-1111-111111111111"     # der Eintrag selbst (Musterdorf)
U2 = "22222222-2222-2222-2222-222222222222"     # Eike
U3 = "33333333-3333-3333-3333-333333333333"     # Kontakt R
U4 = "44444444-4444-4444-4444-444444444444"     # Musterhausen


class Belegt(unittest.TestCase):
    def _k(self, *texte):
        stamm = {w[:5] for t in texte for w in seite._wortliste(t.lower()) if len(w) >= 5}
        return stamm, {w for t in texte for w in seite._wortliste(t.lower())}

    def test_belegte_aussage(self):
        self.assertTrue(seite.belegt("Eike wohnt im Ortsteil Musterdorf.", *self._k("Eike Beispiel wohnt im Ortsteil Musterdorf.")))

    def test_erfundene_zahl_oder_ort(self):
        k = self._k("Eike wohnt im Ortsteil Musterdorf.")
        self.assertFalse(seite.belegt("Eike wohnt seit 2019 in Musterdorf.", *k))
        self.assertFalse(seite.belegt("Eike wohnt in Hannover.", *k))

    def test_umformuliert_ist_erlaubt_wenn_die_woerter_stimmen(self):
        self.assertTrue(seite.belegt("Mitschülerin und Freundin von Eike.", *self._k("Summer ist eine enge Freundin und Mitschülerin von Eike.")))

    def test_monat_und_wochentag_sind_erlaubt(self):
        self.assertTrue(seite.belegt("Geburtstag am 18. Dezember", *self._k("Geburtstag: 12-18")))
        self.assertTrue(seite.belegt("Dienst montags um 18:30 Uhr", *self._k("Gruppe um 18:30 Feuerwehr Dienst")))

    def test_leere_oder_kurze_aussagen(self):
        self.assertTrue(seite.belegt("Ja.", *self._k("irgendwas")))


class Einmaliges(unittest.TestCase):
    def test_offene_fragen_und_tests_sind_kein_wissen(self):
        for text in ("Eike möchte wissen, wie er von der Schule zum Termin gelangt.",
                     "Eike suchte nach einer E-Mail mit dem Stichwort Studienaufgaben.",
                     "Eike führt einen Test mit curl durch.",
                     "Eike fragt Friday nach einer Verbindung zur Schule.",
                     "Eike testet einen neuen Graphenschlüssel."):
            self.assertTrue(antwort.ist_einmalig(text), text)

    def test_dauerhaftes_bleibt(self):
        for text in ("Eike hat mittwochs direkt nach der Schule Therapie.",
                     "Eike wohnt im Ortsteil Musterdorf.",
                     "Eike wünscht sich minimalistische und kurz gehaltene Antworten.",
                     "Der Termin findet um 15:00 im Beispielweg 1 in Musterhausen statt."):
            self.assertFalse(antwort.ist_einmalig(text), text)

    def test_die_suche_blendet_einmaliges_aus_aber_nie_alles(self):
        fakten = [{"fact": "Eike möchte wissen, wie er zur Therapie gelangt."},
                  {"fact": "Eike hat mittwochs Therapie in Musterhausen."}]
        self.assertEqual([f["fact"] for f in antwort.ohne_einmaliges(fakten)], ["Eike hat mittwochs Therapie in Musterhausen."])
        nur = [{"fact": "Eike möchte wissen, wie spät es ist."}]
        self.assertEqual(antwort.ohne_einmaliges(nur), nur)       # sonst bliebe eine leere Seite


def zeilen_kopf():
    return [["Musterdorf", ["Entity", "Location"], "Ein Ortsteil.", "eike"]]


def kanten():
    # fakt, gültig ab, ungültig ab, Nachbar-Name, Nachbar-Uuid, Nachbar-Labels, Nachbar-Gruppe
    return [["Eike Beispiel wohnt im Ortsteil Musterdorf.", "2026-09-13T00:00:00+00:00", None, "Eike", U2, ["Entity", "Person"], "eike"],
            ["Eike besuchte im Dezember 2023 den Weihnachtsmarkt in Musterdorf.", "2026-09-13T00:00:00+00:00", None, "Eike", U2, ["Entity", "Person"], "eike"],
            ["Kontakt R Muster nahm an einer Feuerwehr-Veranstaltung in Musterdorf teil.", "2026-10-04T00:00:00+00:00", None, "Kontakt R Muster", U3, ["Entity", "Person"], "eike"],
            ["Musterdorf ist ein Ortsteil von Musterhausen.", "2026-09-13T00:00:00+00:00", None, "Musterhausen", U4, ["Entity", "Location"], "eike"],
            ["Eike möchte wissen, wie er nach Musterdorf kommt.", "2026-09-13T00:00:00+00:00", None, "Eike", U2, ["Entity", "Person"], "eike"],
            ["Friday erklärt etwas über Musterdorf.", "2026-09-13T00:00:00+00:00", None, "Friday", "ffffffff-ffff-ffff-ffff-ffffffffffff", ["Entity", "Person"], "eike"]]


class Seite(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        seite._zwischenspeicher.clear()
        seite._geladen.clear()
        self.kanten = kanten()
        self.verwandt = []
        self.kalender = []

    def tearDown(self):
        self.env.stop()

    def _lauf(self, modell=None, uuid=U1, mit_modell=True):
        async def abfrage(cypher, werte=None):
            if "CONTAINS 'wohnt'" in cypher:
                return []
            if "CONTAINS $w" in cypher:
                return self.verwandt
            return zeilen_kopf() if "RETURN n.name, labels(n)" in cypher else self.kanten
        antwort_json = None if modell is None else {"candidates": [{"content": {"parts": [{"text": modell}]}}]}
        chat = mock.AsyncMock(side_effect=RuntimeError("kein Modell")) if modell is None else mock.AsyncMock(return_value=(antwort_json, "m"))
        with mock.patch.object(seite.wissensgraph, "eingerichtet", return_value=True), \
             mock.patch.object(seite.wissensgraph, "_abfrage", side_effect=abfrage), \
             mock.patch.object(seite.bilder, "mit_namen", return_value=[]), \
             mock.patch.object(seite.kalenderhinweise, "hinweise", mock.AsyncMock(side_effect=lambda *a: self.kalender)), \
             mock.patch("kern.sitzung.chat._gemini_anfrage", chat):
            return asyncio.run(seite.seite(uuid, modell=mit_modell)), chat

    def test_mit_modell_titel_kernaussagen_details_und_rollen(self):
        # Nachbarn nach Zahl der Fakten: 1 Eike (2 gültige), 2 Kontakt R, 3 Musterhausen
        modell = json.dumps({
            "titel": "Musterdorf bei Musterhausen", "untertitel": "Ortsteil von Musterhausen",
            "kernaussagen": ["Eike wohnt in Musterdorf.", "Kontakt R war bei einer Feuerwehr-Veranstaltung dort."],
            "angaben": [{"titel": "Lage", "zeilen": [{"label": "Gehört zu", "wert": "Musterhausen"}, {"label": "", "wert": "x"}, "kaputt"]}, {"titel": "Leer", "zeilen": []}],
            "gruppen": [{"titel": "Wohnen hier", "mitglieder": [{"nr": 1, "rolle": "wohnt im Ortsteil Musterdorf"}]},
                        {"titel": "Besucher", "mitglieder": [{"nr": 2, "rolle": ""}]}]})
        r, chat = self._lauf(modell)
        self.assertEqual((r["quelle"], r["veredelt"], r["titel"], r["untertitel"]), ("modell", True, "Musterdorf bei Musterhausen", "Ortsteil von Musterhausen"))
        self.assertEqual(r["kernaussagen"][0], "Eike wohnt in Musterdorf.")
        self.assertEqual(r["angaben"], [{"titel": "Lage", "zeilen": [{"label": "Gehört zu", "wert": "Musterhausen"}]}])   # leere Zeilen/Blöcke fallen weg
        eike = r["gruppen"][0]["eintraege"][0]
        self.assertEqual((eike["name"], eike["uuid"], eike["typ"], eike["zusatz"]), ("Eike", U2, "Person", "wohnt im Ortsteil Musterdorf"))
        kontakt_r = r["gruppen"][1]["eintraege"][0]
        self.assertIn("Feuerwehr-Veranstaltung", kontakt_r["zusatz"])        # ohne Rolle: der beste Fakt
        namen = [e["name"] for g in r["gruppen"] for e in g["eintraege"]]
        self.assertEqual(sorted(namen), ["Eike", "Kontakt R Muster", "Musterhausen"])           # Musterhausen: nicht zugeordnet, aber da
        weitere = r["gruppen"][-1]
        self.assertEqual((weitere["titel"], weitere.get("weitere")), ("Weitere Verknüpfungen", True))   # bewusst ausgelassen → eingeklappt
        self.assertEqual([e["name"] for e in weitere["eintraege"]], ["Musterhausen"])

    def test_sofort_ohne_modell_ist_die_einfache_fassung(self):
        modell = json.dumps({"titel": "", "untertitel": "x", "kernaussagen": [], "angaben": [], "gruppen": []})
        r, chat = self._lauf(modell, mit_modell=False)
        chat.assert_not_awaited()
        self.assertEqual((r["quelle"], r["veredelt"], r["titel"]), ("gruppiert", False, "Musterdorf"))
        self.assertEqual(r["kernaussagen"], ["Ein Ortsteil."])                            # die Zusammenfassung aus dem Graphen

    def test_gemerkte_seite_kommt_sofort_und_ueberlebt_den_neustart(self):
        modell = json.dumps({"titel": "T", "untertitel": "U", "kernaussagen": ["K"], "angaben": [], "gruppen": []})
        self._lauf(modell)
        seite._zwischenspeicher.clear()
        seite._geladen.clear()                                                              # wie nach einem Neustart
        r, chat = self._lauf(modell, mit_modell=False)
        chat.assert_not_awaited()
        self.assertEqual((r["titel"], r["veredelt"]), ("T", True))
        self.assertTrue((pathlib.Path(os.environ["FRIDAY_DATEN"]) / "wissensseiten.json").exists())

    def test_geaenderte_eingaben_bauen_neu(self):
        modell = json.dumps({"titel": "", "untertitel": "x", "kernaussagen": [], "angaben": [], "gruppen": []})
        _, chat1 = self._lauf(modell)
        self.kanten = self.kanten + [["Eike feiert in Musterdorf Silvester.", "2026-10-01T00:00:00+00:00", None, "Eike", U2, ["Entity", "Person"], "eike"]]
        _, chat2 = self._lauf(modell)
        self.assertEqual((chat1.await_count, chat2.await_count), (1, 1))

    def test_eikes_eigene_angaben_stehen_im_prompt_mit_vorrang(self):
        from kern.gedaechtnis import speicher as gspeicher
        gspeicher.standard().merken("Musterdorf", "Kontakt R ist vor Jahren aus der Jugendfeuerwehr ausgetreten.", quelle="eike")
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "gruppen": []})
        _, chat = self._lauf(modell)
        prompt = json.dumps(chat.await_args.args[0], ensure_ascii=False)
        self.assertIn("Von Eike selbst gesagt (haben Vorrang)", prompt)
        self.assertIn("ausgetreten", prompt)

    def test_friday_und_einmaliges_kommen_nicht_auf_die_seite(self):
        r, _ = self._lauf(None)
        namen = [e["name"] for g in r["gruppen"] for e in g["eintraege"]]
        self.assertNotIn("Friday", namen)
        self.assertNotIn("Eike möchte wissen, wie er nach Musterdorf kommt.", [f["fakt"] for f in r["fakten"]])

    def test_ohne_modell_nach_typ_gruppiert(self):
        r, _ = self._lauf(None)
        self.assertEqual((r["quelle"], r["veredelt"]), ("gruppiert", False))
        self.assertEqual([g["titel"] for g in r["gruppen"]], ["Personen", "Orte"])
        self.assertEqual(r["untertitel"], "Ort")

    def test_ungueltige_nummern_und_doppelte_zuordnung_werden_ignoriert(self):
        modell = json.dumps({"titel": "", "untertitel": "x", "kernaussagen": ["a", "", 5], "angaben": [],
                             "gruppen": [{"titel": "A", "mitglieder": [{"nr": 1, "rolle": "r"}, {"nr": 1, "rolle": "doppelt"},
                                                                       {"nr": 99, "rolle": "x"}, {"nr": "x"}, "kaputt"]},
                                         {"titel": "B", "mitglieder": [{"nr": 1, "rolle": "r"}, {"nr": 2, "rolle": "q"}]}]})
        r, _ = self._lauf(modell)
        self.assertEqual(r["kernaussagen"], ["a"])
        a = next(g for g in r["gruppen"] if g["titel"] == "A")
        self.assertEqual([(e["name"], e["zusatz"]) for e in a["eintraege"]], [("Eike", "r")])
        b = next(g for g in r["gruppen"] if g["titel"] == "B")
        self.assertEqual([e["name"] for e in b["eintraege"]], ["Kontakt R Muster"])

    def test_unbekannte_oder_kaputte_uuid(self):
        self.assertIsNone(asyncio.run(seite.seite("kein-uuid")))
        async def leer(cypher, werte=None):
            return []
        with mock.patch.object(seite.wissensgraph, "eingerichtet", return_value=True), \
             mock.patch.object(seite.wissensgraph, "_abfrage", side_effect=leer):
            self.assertIsNone(asyncio.run(seite.seite(U1)))

    def test_prompt_enthaelt_kein_seit_datum_und_verwandtes(self):
        # „seit 13.09.2026“ ist, wann Friday es erfuhr — nicht wann es begann. Das Modell machte daraus „aktiv seit Oktober 2026“.
        self.kanten[0][1] = "2026-10-05T00:00:00+00:00"
        self.verwandt = [["Jugendfeuerwehr", "Eike war früher Mitglied in der Jugendfeuerwehr.", None]]
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "gruppen": []})
        _, chat = self._lauf(modell)
        angaben = json.dumps(chat.await_args.args[0]["contents"], ensure_ascii=False)       # nur die Angaben, nicht die Anweisung
        prompt = json.dumps(chat.await_args.args[0], ensure_ascii=False)
        self.assertNotIn("(seit", angaben)
        self.assertNotIn("05.10.2026", angaben)
        self.assertIn("Verwandte Einträge", prompt)
        self.assertIn("Jugendfeuerwehr: Eike war früher Mitglied", prompt)

    def test_kalendertermine_und_rhythmen_stehen_im_prompt(self):
        self.kalender = ["Montags um 18:30 Uhr (Feuerwehrhaus): 5 Termine zwischen 05.10. und 07.12. (Erste Hilfe, Funkausbildung); Abstand meist 14 Tage"]
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "gruppen": []})
        _, chat = self._lauf(modell)
        angaben = json.dumps(chat.await_args.args[0]["contents"], ensure_ascii=False)
        self.assertIn("Kalender (Termine der nächsten Wochen", angaben)
        self.assertIn("Abstand meist 14 Tage", angaben)

    def test_anweisung_trennt_bereiche_und_markiert_frueheres(self):
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "gruppen": []})
        _, chat = self._lauf(modell)
        anweisung = json.dumps(chat.await_args.args[0]["systemInstruction"], ensure_ascii=False)
        self.assertIn("mische nie Bereiche", anweisung)
        self.assertIn("(früher)", anweisung)
        self.assertIn("Zusammenhang", anweisung)                 # der volle Name darf aus dem Kontext folgen (FF Musterdorf)

    def test_neue_kalendertermine_bauen_die_seite_neu(self):
        modell = json.dumps({"titel": "", "untertitel": "x", "kernaussagen": [], "angaben": [], "gruppen": []})
        _, chat1 = self._lauf(modell)
        self.kalender = ["Montags um 18:30 Uhr: 3 Termine"]
        _, chat2 = self._lauf(modell)
        self.assertEqual((chat1.await_count, chat2.await_count), (1, 1))

    def test_widerspruch_zu_eikes_angaben_blendet_die_verknuepfung_aus(self):
        # Eike schrieb „Kontakt R ist nicht in der Feuerwehr“ — dann darf sie weder bei den Mitgliedern noch „weiter unten“ stehen.
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "ausgeschlossen": [2],
                             "gruppen": [{"titel": "Mitglieder", "mitglieder": [{"nr": 1, "rolle": "aktiv", "unsicher": False}]}]})
        r, _ = self._lauf(modell)
        namen = [e["name"] for g in r["gruppen"] for e in g["eintraege"]]
        self.assertNotIn("Kontakt R Muster", namen)
        self.assertIn("Eike", namen)

    def test_nicht_eingeordnete_personen_bleiben_sichtbar_und_unsicher(self):
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "ausgeschlossen": [],
                             "gruppen": [{"titel": "Mitglieder", "mitglieder": [{"nr": 1, "rolle": "aktiv", "unsicher": False}]}]})
        r, _ = self._lauf(modell)
        weitere = next(g for g in r["gruppen"] if g["titel"] == "Weitere Personen")
        self.assertEqual([(e["name"], e["unsicher"]) for e in weitere["eintraege"]], [("Kontakt R Muster", True)])
        sonstige = next(g for g in r["gruppen"] if g["titel"] == "Weitere Verknüpfungen")       # Musterhausen (Ort): eingeklappt
        self.assertTrue(sonstige["weitere"])

    def test_unsicher_aus_dem_modell_wird_durchgereicht(self):
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "ausgeschlossen": [],
                             "gruppen": [{"titel": "Mitglieder", "mitglieder": [{"nr": 1, "rolle": "aktiv", "unsicher": False},
                                                                                {"nr": 2, "rolle": "laut Profil", "unsicher": True}]}]})
        r, _ = self._lauf(modell)
        mitglieder = r["gruppen"][0]["eintraege"]
        self.assertEqual([(e["name"], e["unsicher"]) for e in mitglieder], [("Eike", False), ("Kontakt R Muster", True)])

    def test_anweisung_eine_mitgliedergruppe_und_frueher_nur_bei_vergangenem(self):
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "ausgeschlossen": [], "gruppen": []})
        _, chat = self._lauf(modell)
        anweisung = json.dumps(chat.await_args.args[0]["systemInstruction"], ensure_ascii=False)
        self.assertIn("EINE Gruppe „Mitglieder“", anweisung)
        self.assertIn("NUR an eine Überschrift", anweisung)
        self.assertIn("in der Zukunft stehen, gelten als aktuell", anweisung)

    def test_frueher_steht_nur_an_ueberschriften_mit_vergangenem(self):
        self.kalender = ["Mittwochs um 18:00 Uhr (JF)", "Eike wohnte bis 2020 in Hannover."]       # die Angaben müssen belegt sein
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "ausgeschlossen": [], "gruppen": [],
                             "angaben": [{"titel": "Jugendfeuerwehr (früher)", "zeilen": [{"label": "Termine", "wert": "Mittwochs um 18:00 Uhr"}]},
                                         {"titel": "Wohnort (früher)", "zeilen": [{"label": "Ort", "wert": "wohnte bis 2020 in Hannover"}]}]})
        r, _ = self._lauf(modell)
        self.assertEqual([b["titel"] for b in r["angaben"]], ["Jugendfeuerwehr", "Wohnort (früher)"])

    def test_notizbuch_sucht_nicht_nach_eike(self):
        from kern.gedaechtnis import speicher as gspeicher
        gspeicher.standard().merken("Umgang mit Hintergrundmusik", "Wenn Musik als Eikes Sprache gedeutet wird, ignorieren.", quelle="whatsapp")
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "ausgeschlossen": [], "gruppen": []})
        _, chat = self._lauf(modell)                                  # Nachbar „Eike“ darf nicht alles ans Notizbuch ziehen
        self.assertNotIn("Hintergrundmusik", json.dumps(chat.await_args.args[0]["contents"], ensure_ascii=False))

    def test_eikes_wohnort_steht_zum_einordnen_im_prompt(self):
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "gruppen": []})
        async def abfrage(cypher, werte=None):
            if "CONTAINS 'wohnt'" in cypher:
                return [["Eike Beispiel wohnt im Ortsteil Musterdorf."]]
            if "CONTAINS $w" in cypher:
                return []
            return zeilen_kopf() if "RETURN n.name, labels(n)" in cypher else self.kanten
        antwort_json = {"candidates": [{"content": {"parts": [{"text": modell}]}}]}
        chat = mock.AsyncMock(return_value=(antwort_json, "m"))
        with mock.patch.object(seite.wissensgraph, "eingerichtet", return_value=True), \
             mock.patch.object(seite.wissensgraph, "_abfrage", side_effect=abfrage), \
             mock.patch.object(seite.bilder, "mit_namen", return_value=[]), \
             mock.patch.object(seite.kalenderhinweise, "hinweise", mock.AsyncMock(return_value=[])), \
             mock.patch("kern.sitzung.chat._gemini_anfrage", chat):
            asyncio.run(seite.seite(U1))
        self.assertIn("Zum Einordnen (Eike): Eike Beispiel wohnt im Ortsteil Musterdorf.",
                      json.dumps(chat.await_args.args[0]["contents"], ensure_ascii=False))

    def test_unbelegte_aussagen_fliegen_raus(self):
        # Eingaben kennen: Eike wohnt in Musterdorf, Kontakt R Feuerwehr-Veranstaltung. NICHT: 2019, Hannover, Orchester.
        modell = json.dumps({"titel": "", "untertitel": "", "ausgeschlossen": [], "gruppen": [{"titel": "G", "mitglieder": [
                                 {"nr": 1, "rolle": "spielt im Orchester", "unsicher": False}]}],
                             "kernaussagen": ["Eike wohnt in Musterdorf.", "Eike zog 2019 nach Hannover.", "Eike spielt im Orchester."],
                             "steckbrief": [{"label": "Wohnort", "wert": "Musterdorf"}, {"label": "Hobby", "wert": "Orchester"}],
                             "angaben": [{"titel": "X", "zeilen": [{"label": "Seit", "wert": "2019"}, {"label": "Ort", "wert": "Musterdorf"}]}]})
        r, _ = self._lauf(modell)
        self.assertEqual(r["kernaussagen"], ["Eike wohnt in Musterdorf."])
        self.assertEqual(r["angaben"], [{"titel": "X", "zeilen": [{"label": "Ort", "wert": "Musterdorf"}]}])
        eike = r["gruppen"][0]["eintraege"][0]
        self.assertNotIn("Orchester", eike["zusatz"])                       # unbelegte Rolle → der beste Fakt

    def test_steckbrief_nur_bei_personen_und_geprueft(self):
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "gruppen": [], "ausgeschlossen": [],
                             "steckbrief": [{"label": "Wohnort", "wert": "Musterdorf"}, {"label": "Hobby", "wert": "Orchester"}]})
        r, _ = self._lauf(modell)                                           # Musterdorf ist ein Ort, keine Person
        self.assertEqual(r["steckbrief"], [])

    def test_person_bekommt_steckbrief_profil_und_neu_im_prompt(self):
        zeilen_person = [["Summer Muster", ["Entity", "Person"], "Eine Freundin.", "eike"]]
        async def abfrage(cypher, werte=None):
            if "CONTAINS 'wohnt'" in cypher or "CONTAINS $w" in cypher:
                return []
            return zeilen_person if "RETURN n.name, labels(n)" in cypher else self.kanten
        modell = json.dumps({"titel": "", "untertitel": "Freundin", "kernaussagen": ["Summer schreibt über Freitag."], "angaben": [],
                             "gruppen": [], "ausgeschlossen": [], "steckbrief": [{"label": "Geburtstag", "wert": "18. Dezember"},
                                                                                {"label": "Wohnort", "wert": "Bennigsen"}]})
        antwort_json = {"candidates": [{"content": {"parts": [{"text": modell}]}}]}
        chat = mock.AsyncMock(return_value=(antwort_json, "m"))
        profil = ["Beziehung: enge Freundin und Mitschülerin", "Geburtstag: 12-18", "Über sie/ihn: Summer Muster wohnt in Bennigsen."]
        with mock.patch.object(seite.wissensgraph, "eingerichtet", return_value=True), \
             mock.patch.object(seite.wissensgraph, "_abfrage", side_effect=abfrage), \
             mock.patch.object(seite.bilder, "mit_namen", return_value=[]), \
             mock.patch.object(seite, "_whatsapp_profil", return_value=profil), \
             mock.patch.object(seite, "_fokus_neu", return_value=["Summer schreibt, dass sie am Freitag Zeit hat. (WhatsApp)"]), \
             mock.patch.object(seite.kalenderhinweise, "hinweise", mock.AsyncMock(return_value=[])), \
             mock.patch("kern.sitzung.chat._gemini_anfrage", chat):
            r = asyncio.run(seite.seite(U1))
        angaben = json.dumps(chat.await_args.args[0]["contents"], ensure_ascii=False)
        anweisung = json.dumps(chat.await_args.args[0]["systemInstruction"], ensure_ascii=False)
        self.assertIn("WhatsApp-Profil (aus Chatverläufen ausgewertet", angaben)
        self.assertIn("Geburtstag: 12-18", angaben)
        self.assertIn("Neu (Zusammenfassungen der letzten Tage", angaben)
        self.assertIn("PERSON", anweisung)
        self.assertIn("Sensible Themen", anweisung)
        self.assertEqual([z["label"] for z in r["steckbrief"]], ["Geburtstag", "Wohnort"])    # 18. Dezember: Monatsname + Zahl belegt (12-18)

    def test_aktuell_nur_aus_neuem_und_eike_nicht_als_verknuepfung_bei_personen(self):
        zeilen_person = [["Kontakt R Muster", ["Entity", "Person"], "", "eike"]]
        async def abfrage(cypher, werte=None):
            if "CONTAINS 'wohnt'" in cypher or "CONTAINS $w" in cypher:
                return []
            return zeilen_person if "RETURN n.name, labels(n)" in cypher else self.kanten
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "gruppen": [], "ausgeschlossen": [], "steckbrief": []})
        antwort_json = {"candidates": [{"content": {"parts": [{"text": modell}]}}]}
        chat = mock.AsyncMock(return_value=(antwort_json, "m"))
        with mock.patch.object(seite.wissensgraph, "eingerichtet", return_value=True), \
             mock.patch.object(seite.wissensgraph, "_abfrage", side_effect=abfrage), \
             mock.patch.object(seite.bilder, "mit_namen", return_value=[]), \
             mock.patch.object(seite.kalenderhinweise, "hinweise", mock.AsyncMock(return_value=[])), \
             mock.patch("kern.sitzung.chat._gemini_anfrage", chat):
            r = asyncio.run(seite.seite(U1))
        namen = [e["name"] for g in r["gruppen"] for e in g["eintraege"]]
        self.assertNotIn("Eike", namen)
        anweisung = json.dumps(chat.await_args.args[0]["systemInstruction"], ensure_ascii=False)
        self.assertIn("NUR aus „Neu“, Kalender und den neuesten Gesprächsnotizen", anweisung)
        self.assertIn("ALLE belegten Eckdaten", anweisung)

    def test_person_ohne_neu_hat_nichts_aktuelles_und_keinen_rohen_graphtext(self):
        zeilen_person = [["Kontakt B Kontakt H", ["Entity", "Person"], "Zeile eins.\nZeile zwei mit Gefühlen.", "eike"]]
        async def abfrage(cypher, werte=None):
            if "CONTAINS 'wohnt'" in cypher or "CONTAINS $w" in cypher:
                return []
            return zeilen_person if "RETURN n.name, labels(n)" in cypher else self.kanten
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": ["Kontakt B hat Zeit am Freitag."], "angaben": [], "gruppen": [],
                             "ausgeschlossen": [], "steckbrief": []})
        antwort_json = {"candidates": [{"content": {"parts": [{"text": modell}]}}]}
        with mock.patch.object(seite.wissensgraph, "eingerichtet", return_value=True), \
             mock.patch.object(seite.wissensgraph, "_abfrage", side_effect=abfrage), \
             mock.patch.object(seite.bilder, "mit_namen", return_value=[]), \
             mock.patch.object(seite, "_fokus_neu", return_value=[]), \
             mock.patch.object(seite.kalenderhinweise, "hinweise", mock.AsyncMock(return_value=[])), \
             mock.patch("kern.sitzung.chat._gemini_anfrage", mock.AsyncMock(return_value=(antwort_json, "m"))):
            r = asyncio.run(seite.seite(U1))
            einfach = asyncio.run(seite.seite(U1, modell=False))
        self.assertEqual(r["kernaussagen"], [])                    # Personen: „Neu“ ersetzt „Aktuell“
        self.assertEqual(einfach["kernaussagen"], [])              # und kein roher Graph-Text

    def test_person_hat_keine_doppelten_angaben_ohne_kalender(self):
        zeilen_person = [["Summer Muster", ["Entity", "Person"], "", "eike"]]
        async def abfrage(cypher, werte=None):
            if "CONTAINS 'wohnt'" in cypher or "CONTAINS $w" in cypher:
                return []
            return zeilen_person if "RETURN n.name, labels(n)" in cypher else self.kanten
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": ["Eike wohnt in Musterdorf."], "ausgeschlossen": [], "gruppen": [],
                             "steckbrief": [{"label": "Wohnort", "wert": "Musterdorf"}],
                             "angaben": [{"titel": "Wohnort", "zeilen": [{"label": "Wohnort", "wert": "Musterdorf"}]}]})
        antwort_json = {"candidates": [{"content": {"parts": [{"text": modell}]}}]}
        with mock.patch.object(seite.wissensgraph, "eingerichtet", return_value=True), \
             mock.patch.object(seite.wissensgraph, "_abfrage", side_effect=abfrage), \
             mock.patch.object(seite.bilder, "mit_namen", return_value=[]), \
             mock.patch.object(seite, "_fokus_neu", return_value=["Summer schreibt, dass sie am Freitag Zeit hat. (WhatsApp)"]), \
             mock.patch.object(seite.kalenderhinweise, "hinweise", mock.AsyncMock(return_value=[])), \
             mock.patch("kern.sitzung.chat._gemini_anfrage", mock.AsyncMock(return_value=(antwort_json, "m"))):
            r = asyncio.run(seite.seite(U1))
        self.assertEqual((r["kernaussagen"], r["angaben"]), ([], []))
        self.assertEqual(r["steckbrief"], [{"label": "Wohnort", "wert": "Musterdorf"}])

    def test_stichwoerter_ohne_fuellwoerter(self):
        self.assertEqual(seite._stichwoerter("Freiwillige Feuerwehr"), ["feuerwehr"])
        self.assertEqual(seite._stichwoerter("Therapie-Termin"), ["therapie"])

    def test_prompt_nennt_angaben_als_daten_und_verbietet_erfinden(self):
        modell = json.dumps({"titel": "", "untertitel": "", "kernaussagen": [], "angaben": [], "gruppen": []})
        _, chat = self._lauf(modell)
        prompt = json.dumps(chat.await_args.args[0], ensure_ascii=False)
        self.assertIn("AUSSCHLIESSLICH", prompt)
        self.assertIn("keine Anweisungen", prompt)
        self.assertIn("Zeitform", prompt)                                                  # „war dabei“ ist nicht „ist dabei“


if __name__ == "__main__":
    unittest.main()
