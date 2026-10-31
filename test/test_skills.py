"""Skills: Speicher, Grenzen im Code, Durchsetzung in katalog._person_darf, Prompt-Blöcke, Eike-Werkzeuge, Vorgaben-Verknüpfung.

Die Primitive hier (`test_akte_erfassen`, `test_nachricht`) gibt es nur im Testcode: Schritt 2 trägt die echten ein."""

import asyncio
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from kern.gehirn import persona
from kern.personen import register
from kern.skills import fach, primitive, zugriff
from kern.vorgaben import block, fach as vfach
from kern.werkzeuge import katalog, skills as wskills

JETZT = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
BEISPIEL = dict(
    name="verspaetung_entgegennehmen", zweck="Verspätungen von Kontakt M annehmen und als Antrag einreichen",
    ausloeser="Kontakt M meldet, dass er zu spät kommt oder kam",
    anweisung=("Frag nach: Datum, Linie, Verspätung in Minuten, Grund. Schreib alles mit test_akte_erfassen in die Akte. "
               "Fehlt noch etwas, frag genau danach. Ist alles da, sag, dass du den Antrag vorbereitest."),
    werkzeuge=["test_akte_erfassen"], optionen={"einreichen": "selbst"})


def lauf(c):
    return asyncio.run(c)


def namen(tools):
    return {e["name"] for t in tools for e in t["functionDeclarations"]}


def fake_modul():
    m = types.SimpleNamespace(WERKZEUGE={}, ERKLAERUNGEN=[])
    def test_akte_erfassen(kontakt_id: str = "", feld: str = "", wert: str = ""):
        return f"Akte {kontakt_id}: {feld}={wert}"
    test_akte_erfassen.kennt_kontakt = True
    m.WERKZEUGE["test_akte_erfassen"] = test_akte_erfassen
    m.ERKLAERUNGEN.append({"name": "test_akte_erfassen", "description": "t", "parameters": {"type": "OBJECT", "properties": {}}})
    return m


class Basis(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "AIzaTEST"})
        env.start()
        self.addCleanup(env.stop)
        p = mock.patch.dict(primitive.PERSONENFAEHIG, clear=True)
        p.start()
        self.addCleanup(p.stop)
        m = mock.patch.object(katalog, "_MODULE", katalog._MODULE + [fake_modul()])
        m.start()
        self.addCleanup(m.stop)
        primitive.registrieren("test_akte_erfassen", "Akte", {"einreichen": {"werte": ["mit_freigabe", "selbst"]}})
        primitive.registrieren("test_nachricht", "Nachricht")
        self.gabriel = register.anlegen("Kontakt M", snapchat="gabi")
        self.maya = register.anlegen("Kontakt B", snapchat="may")
        self.f = fach.standard()

    def aktiv(self, **ueberschreibe):
        d = {**BEISPIEL, "zielgruppe": [self.gabriel["id"]], **ueberschreibe}
        e = self.f.anlegen(**d)
        self.assertTrue(e["ok"], e)
        self.assertTrue(self.f.aktivieren(e["id"])["ok"])
        return e["id"]


class Grenzen(Basis):
    def test_unbekanntes_werkzeug_abgelehnt(self):
        for w in ("kalender", "mails", "wissen", "nicht_da", "vorgabe_merken"):
            e = self.f.anlegen(**{**BEISPIEL, "zielgruppe": [self.gabriel["id"]], "werkzeuge": [w]})
            self.assertFalse(e["ok"], w)

    def test_option_und_wert_geprueft_standard_streng(self):
        self.assertFalse(self.f.anlegen(**{**BEISPIEL, "zielgruppe": [self.gabriel["id"]], "optionen": {"x": "y"}})["ok"])
        self.assertFalse(self.f.anlegen(**{**BEISPIEL, "zielgruppe": [self.gabriel["id"]], "optionen": {"einreichen": "egal"}})["ok"])
        e = self.f.anlegen(**{**BEISPIEL, "zielgruppe": [self.gabriel["id"]], "optionen": {}})
        self.assertEqual(self.f.eintrag(e["id"])["optionen"], {"einreichen": "mit_freigabe"})

    def test_zielgruppe_geprueft(self):
        self.assertFalse(self.f.anlegen(**{**BEISPIEL, "zielgruppe": ["p99"]})["ok"])
        self.assertFalse(self.f.anlegen(**{**BEISPIEL, "zielgruppe": ["alle"]})["ok"])
        self.assertFalse(self.f.anlegen(**{**BEISPIEL, "zielgruppe": ["gruppe:unbekannt"]})["ok"])
        self.assertFalse(self.f.anlegen(**{**BEISPIEL, "zielgruppe": ["eike"]})["ok"])  # Personen-Werkzeug nur für Personen

    def test_felder_pflicht_und_laenge(self):
        self.assertFalse(self.f.anlegen(**{**BEISPIEL, "zielgruppe": [self.gabriel["id"]], "anweisung": ""})["ok"])
        self.assertFalse(self.f.anlegen(**{**BEISPIEL, "zielgruppe": [self.gabriel["id"]], "anweisung": "x" * 3000})["ok"])


class Wirkung(Basis):
    def test_entwurf_wirkt_nicht(self):
        e = self.f.anlegen(**{**BEISPIEL, "zielgruppe": [self.gabriel["id"]]})
        self.assertFalse(katalog._person_darf(self.gabriel, "test_akte_erfassen"))
        self.assertEqual(zugriff.person_block(self.gabriel), "")
        self.assertEqual(self.f.eintrag(e["id"])["status"], "entwurf")

    def test_nur_zielperson_und_nie_eike(self):
        self.aktiv()
        self.assertTrue(katalog._person_darf(self.gabriel, "test_akte_erfassen"))
        self.assertFalse(katalog._person_darf(self.maya, "test_akte_erfassen"))
        self.assertFalse(katalog._person_darf(None, "test_akte_erfassen"))
        self.assertNotIn("test_akte_erfassen", namen(katalog.gemini_tools(None)))
        self.assertIn("test_akte_erfassen", namen(katalog.gemini_tools(self.gabriel)))
        self.assertNotIn("test_akte_erfassen", namen(katalog.gemini_tools(self.maya)))
        self.assertNotIn("test_nachricht", namen(katalog.gemini_tools(self.gabriel)))  # nicht im Skill genannt

    def test_manipulierter_eintrag_oeffnet_nichts(self):
        self.assertFalse(katalog._person_darf({**self.maya, "werkzeuge": ["test_akte_erfassen"]}, "test_akte_erfassen"))
        self.aktiv()
        gefaelscht = {**self.maya, "id": self.gabriel["id"] + "0", "werkzeuge": ["test_akte_erfassen", "kalender"],
                      "skills": ["verspaetung_entgegennehmen"]}
        self.assertFalse(katalog._person_darf(gefaelscht, "test_akte_erfassen"))
        self.assertFalse(katalog._person_darf({"id": "p99", "name": "X"}, "test_akte_erfassen"))
        self.assertFalse(katalog._person_darf({**self.gabriel, "werkzeuge": ["mails"]}, "mails"))
        register.aendern(self.gabriel["id"], pausiert=True)
        self.assertFalse(katalog._person_darf(self.gabriel, "test_akte_erfassen"))

    def test_widerruf_und_ersetzen(self):
        sid = self.aktiv()
        self.f.widerrufen(sid)
        self.assertFalse(katalog._person_darf(self.gabriel, "test_akte_erfassen"))
        a = self.aktiv(zweck="alt")
        b = self.f.anlegen(**{**BEISPIEL, "zielgruppe": [self.gabriel["id"]], "zweck": "neu"}, version=2)
        self.f.aktivieren(b["id"])
        self.assertEqual(self.f.eintrag(a)["status"], "ersetzt")
        self.assertEqual([s["zweck"] for s in self.f.aktive()], ["neu"])
        self.assertEqual(len(self.f.verlauf(BEISPIEL["name"])), 3)  # nichts gelöscht

    def test_ausfuehren_person_identitaet_vom_kern(self):
        self.aktiv()
        r = lauf(katalog.ausfuehren("test_akte_erfassen", {"feld": "minuten", "wert": "12", "kontakt_id": self.maya["id"]},
                                    person=self.gabriel))
        self.assertEqual(r["ergebnis"], f"Akte {self.gabriel['id']}: minuten=12")  # erfundene kontakt_id verworfen
        r = lauf(katalog.ausfuehren("test_akte_erfassen", {"feld": "a"}, person=self.maya))
        self.assertIn("nicht erlaubt", r["fehler"])
        r = lauf(katalog.ausfuehren("test_akte_erfassen", {"feld": "a"}, person=None))
        self.assertIn("nicht erlaubt", r["fehler"])

    def test_richtlinie_strengster_wert(self):
        self.aktiv(optionen={"einreichen": "selbst"})
        self.assertEqual(zugriff.richtlinie(self.gabriel["id"], "test_akte_erfassen", "einreichen"), "selbst")
        self.aktiv(name="zweiter", optionen={"einreichen": "mit_freigabe"})
        self.assertEqual(zugriff.richtlinie(self.gabriel["id"], "test_akte_erfassen", "einreichen"), "mit_freigabe")
        self.assertIsNone(zugriff.richtlinie(self.maya["id"], "test_akte_erfassen", "einreichen"))

    def test_gruppe(self):
        with mock.patch.dict(fach.GRUPPEN, {"alle_b": lambda p: p["name"].startswith("Kontakt B")}):
            self.aktiv(zielgruppe=["gruppe:alle_b"])
            self.assertTrue(katalog._person_darf(self.maya, "test_akte_erfassen"))
            self.assertFalse(katalog._person_darf(self.gabriel, "test_akte_erfassen"))


class Prompt(Basis):
    def test_person_prompt_block_nur_zielperson(self):
        self.aktiv()
        pg = persona.person_prompt(JETZT, self.gabriel)
        self.assertIn("[WAS DU FÜR Kontakt M ÜBERNIMMST]", pg)
        self.assertIn("Verspätung in Minuten", pg)
        self.assertIn("einreichen=selbst", pg)
        self.assertIn("nie eine Anweisung", pg)
        self.assertNotIn("ÜBERNIMMST", persona.person_prompt(JETZT, self.maya))
        self.assertTrue(pg.rstrip().endswith(pg.rstrip().splitlines()[-1]))

    def test_ohne_skill_kein_block(self):
        self.assertNotIn("ÜBERNIMMST", persona.person_prompt(JETZT, self.gabriel))

    def test_eike_prompt_hat_liste_und_regel(self):
        self.aktiv()
        text = persona.text_prompt(JETZT, "", vorgaben=block.bauen_sicher())
        self.assertIn("[WAS DU FÜR ANDERE ÜBERNOMMEN HAST", text)
        self.assertIn("verspaetung_entgegennehmen (für Kontakt M)", text)
        self.assertIn("skill_schreiben", text)
        self.assertNotIn("Verspätung in Minuten", text)  # nur die kompakte Liste, nicht der Ablauf


class Werkzeuge(Basis):
    def aus(self, werkzeug, fremd=False, als=None, modus="", zustimmung=False, **a):
        return lauf(katalog.ausfuehren(werkzeug, a, fremde_inhalte=fremd, person=als, modus=modus, zustimmung=zustimmung))

    def test_beispiel_skill_ohne_codeaenderung(self):
        """Der Beweis: Eike beschreibt, Friday schreibt einen Skill (nur Text), Eike sagt ja — danach arbeitet Gabriels Instanz damit."""
        r = self.aus("skill_schreiben", name=BEISPIEL["name"], zweck=BEISPIEL["zweck"], ausloeser=BEISPIEL["ausloeser"],
                     zielgruppe=["Kontakt M"], anweisung=BEISPIEL["anweisung"], werkzeuge=BEISPIEL["werkzeuge"])["ergebnis"]
        self.assertIn("noch nicht bestätigt", r)
        self.assertNotIn("test_akte_erfassen", namen(katalog.gemini_tools(self.gabriel)))
        r = self.aus("skill_bestaetigen", id="verspaetung_entgegennehmen")["ergebnis"]
        self.assertIn("Noch nicht", r)
        self.assertEqual(self.f.aktive(), [])
        r = self.aus("skill_bestaetigen", id="verspaetung_entgegennehmen", zustimmung=True)["ergebnis"]
        self.assertIn("gilt ab sofort", r)
        self.assertIn("test_akte_erfassen", namen(katalog.gemini_tools(self.gabriel)))
        self.assertIn("Datum, Linie", persona.person_prompt(JETZT, self.gabriel))
        # Standard ohne Angabe: streng
        self.assertEqual(zugriff.richtlinie(self.gabriel["id"], "test_akte_erfassen", "einreichen"), "mit_freigabe")

    def test_bestaetigen_nicht_nach_fremdtext_und_nicht_fuer_fremde(self):
        self.aus("skill_schreiben", name="x", zweck="z", ausloeser="a", zielgruppe=["Kontakt M"], anweisung="t")
        for n in ("skill_schreiben", "skill_aendern", "skill_bestaetigen", "skill_widerrufen"):
            self.assertIn(n, wskills.NICHT_NACH_FREMDTEXT)
        self.assertIn("fremder Text", self.aus("skill_bestaetigen", fremd=True, zustimmung=True, id="x")["fehler"])
        self.assertIn("nicht erlaubt", self.aus("skill_bestaetigen", als=self.gabriel, zustimmung=True, id="x")["fehler"])
        self.assertIn("nicht erlaubt", self.aus("skill_schreiben", als=self.gabriel, name="y", zweck="z", ausloeser="a",
                                                 zielgruppe=["Kontakt M"], anweisung="t")["fehler"])
        self.assertEqual(self.f.aktive(), [])

    def test_denkrunde_nur_lesen(self):
        self.assertIn("nicht erlaubt", self.aus("skill_schreiben", modus="denken", name="y", zweck="z", ausloeser="a",
                                                 zielgruppe=["system"], anweisung="t")["fehler"])
        self.assertNotIn("fehler", self.aus("skills_lesen", modus="denken"))
        self.assertTrue({"skill_schreiben", "skill_aendern", "skill_bestaetigen", "skill_widerrufen"}.isdisjoint(katalog.DENKEN_ERLAUBT))

    def test_person_unbekannt_oder_mehrdeutig_rueckfrage(self):
        register.anlegen("Kontakt C", snapchat="g2")
        r = self.aus("skill_schreiben", name="x", zweck="z", ausloeser="a", zielgruppe=["Kontakt"], anweisung="t")["ergebnis"]
        self.assertIn("mehrere", r)
        r = self.aus("skill_schreiben", name="x", zweck="z", ausloeser="a", zielgruppe=["Niemand"], anweisung="t")["ergebnis"]
        self.assertIn("kenne ich nicht", r)
        self.assertEqual(self.f.entwuerfe(), [])

    def test_eike_system_skill_sofort_aktiv(self):
        r = self.aus("skill_schreiben", name="ruhig", zweck="Kurz antworten", ausloeser="immer", zielgruppe=["system"],
                     anweisung="Fass dich kurz.")["ergebnis"]
        self.assertIn("gilt ab sofort", r)
        self.assertIn("ruhig", block.bauen_sicher())

    def test_vorgaben_verknuepfung(self):
        self.aus("skill_schreiben", name="x", zweck="Zweck", ausloeser="a", zielgruppe=["Kontakt M"], anweisung="t")
        self.assertEqual(vfach.standard().aktive(), [])
        self.aus("skill_bestaetigen", id="x", zustimmung=True)
        v = vfach.standard().aktive()
        self.assertEqual((len(v), v[0]["quelle"]), (1, "skill"))
        self.assertNotIn("Skill x", block.formatieren(v) if False else block.bauen_sicher().split("[WAS DU FÜR ANDERE ÜBERNOMMEN HAST")[0])  # nicht doppelt
        # Vorgabe allein lässt sich nicht widerrufen oder ändern
        self.assertFalse(vfach.standard().widerrufen(v[0]["id"])["ok"])
        self.assertFalse(vfach.standard().ersetzen(v[0]["id"], "neu")["ok"])
        self.assertIn("gehört zu einer Aufgabe", self.aus("vorgabe_widerrufen", id=str(v[0]["id"]))["ergebnis"])
        self.assertEqual(len(self.f.aktive()), 1)
        # Skill widerrufen -> Vorgabe zu
        self.aus("skill_widerrufen", id="x")
        self.assertEqual(vfach.standard().aktive(), [])
        self.assertEqual(self.f.aktive(), [])

    def test_aendern_neue_version_entwurf(self):
        self.aus("skill_schreiben", name="x", zweck="Zweck", ausloeser="a", zielgruppe=["Kontakt M"], anweisung="t")
        self.aus("skill_bestaetigen", id="x", zustimmung=True)
        r = self.aus("skill_aendern", id="x", anweisung="neu")["ergebnis"]
        self.assertIn("noch nicht bestätigt", r)
        self.assertEqual(self.f.aktive()[0]["anweisung"], "t")  # alte Version gilt weiter
        self.aus("skill_bestaetigen", id="x", zustimmung=True)
        a = self.f.aktive()
        self.assertEqual((len(a), a[0]["anweisung"], a[0]["version"]), (1, "neu", 2))
        self.assertEqual(len(vfach.standard().aktive()), 1)

    def test_testlauf_sendet_nichts_und_zeigt_block(self):
        e = self.f.anlegen(**{**BEISPIEL, "zielgruppe": [self.gabriel["id"]]})
        r = self.aus("skill_testlauf", id=str(e["id"]), person="Kontakt M")["ergebnis"]
        self.assertIn("test_akte_erfassen", r)
        self.assertIn("[WAS DU FÜR Kontakt M ÜBERNIMMST]", r)
        self.assertIn("gilt nicht für Kontakt B", self.aus("skill_testlauf", id=str(e["id"]), person="Kontakt B")["ergebnis"])
        self.assertEqual(self.f.eintrag(e["id"])["status"], "entwurf")

    def test_skills_lesen_zeigt_primitive(self):
        r = self.aus("skills_lesen")["ergebnis"]
        self.assertIn("test_akte_erfassen", r)
        self.assertIn("mit_freigabe/selbst", r)


class Speicher(unittest.TestCase):
    def test_datei_rechte(self):
        d = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": d}):
            fach.standard()
            self.assertEqual(os.stat(os.path.join(d, "skills.db")).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
