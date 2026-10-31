"""Technik bleibt unsichtbar (12.10.2026): Friday spricht nie über Skills, Vorgaben-Nummern, Werkzeuge, Entwürfe, Akten oder den
Prompt. Zwei Dinge sind prüfbar:

1. Die Regel „Technik unsichtbar“ steht in JEDEM Prompt (Eikes Chat/Sprache/Snapchat und die Personen-Instanzen).
2. Jeder Satz, den das Modell aus einem Werkzeugergebnis, einem Fehler, einer Rückfrage oder einer Block-Überschrift zitieren
   könnte, enthält keine Technikwörter.

Nicht prüfbar ist, ob Gemini sich daran hält — das zeigt nur das echte Gespräch."""

import re
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from kern.gehirn import persona
from kern.skills import fach, primitive, zugriff
from kern.vorgaben import block as vblock, fach as vfach
from kern.werkzeuge import akte as wakte, personenprimitive as sp, skills as wskills, vorgaben as wvorgaben
from test import test_personenprimitive as tp
from test import test_skills_akte as ta

JETZT = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))

# Funktionsnamen mit Unterstrich (akte_erfassen, skill_bestaetigen) sind Wörter für \b — sie zählen NICHT als Treffer; gesucht
# wird das freistehende Wort.
# Vom Modell geschriebener Ablauftext einer Vereinbarung (er darf Funktionsnamen enthalten; er ist keine Antwort an Menschen).
ABLAUF = ("Frag immer nur nach dem, was akte_fehlt zeigt, und halte jede Angabe mit akte_erfassen fest. Ist alles da, sag in einem Satz, "
          "was du weißt, und ruf garantie_antrag_aus_akte auf. Eike gibt jeden Antrag selbst frei.")

TECHNIK = re.compile(
    r"\bskills?\b|\bvorgaben?\b|\bnr\.|\bentw[uü]rfe?\b|primitiv|werkzeug|\bakten?\b|\bprompt\b|aktiv gesetzt|ist jetzt aktiv"
    r"|\bsystemprompt|\barbeitsweisen?\b|\bversion\b|\bv\d+\b|schreibrecht|\bfunktion\b|\bblock\b",
    re.IGNORECASE)

REGEL = "Technik unsichtbar"


def texte(r) -> list[str]:
    """Alle Strings eines Werkzeugergebnisses (Text oder {'ergebnis'|'fehler': Text})."""
    if isinstance(r, dict):
        # P0 12.10.2026: Auch die Quittung ist ein Satz, den Eike zu sehen oder zu hören bekommt.
        return [t for t in (r.get("ergebnis") or r.get("fehler") or "", r.get("quittung")) if isinstance(t, str)]
    if isinstance(r, str):
        return [r] + ([r.quittung] if getattr(r, "quittung", None) else [])      # eine `Wirkung` trägt ihre Quittung mit
    return []


class Sammler:
    def pruefe(self, test: unittest.TestCase, wo: str, *ergebnisse) -> None:
        for r in ergebnisse:
            for t in texte(r):
                test.assertIsNone(TECHNIK.search(t), f"{wo}: Technikwort „{TECHNIK.search(t) and TECHNIK.search(t).group(0)}“ in: {t[:300]}")
                test.assertTrue(t.strip(), f"{wo}: leere Antwort")


class PromptRegel(unittest.TestCase):
    def test_regel_in_allen_eike_prompts(self):
        for p in (persona.text_prompt(JETZT), persona.live_prompt(JETZT)):
            self.assertIn(REGEL, p)
        self.assertIn(REGEL, persona.SNAPCHAT_KANAL)

    def test_regel_in_person_prompt_mit_und_ohne_arbeitsweise(self):
        person = {"id": "p1", "name": "Kontakt B", "persona": "", "werkzeuge": [], "kanaele": {}}
        self.assertIn(REGEL, persona.person_prompt(JETZT, person))
        self.assertIn(REGEL, persona.person_prompt(JETZT, person, kanal=persona.person_kanal("Snapchat")))

    def test_regel_hat_gute_beispiele_und_haelt_die_grenzen(self):
        for wort in ("Alles klar, ich kümmere mich darum", "Soll ich", "Ich sag dir Bescheid"):
            self.assertIn(wort, persona.TECHNIK_REGEL)
        # Das Ja-Gate und die Ehrlichkeit bleiben ausdrücklich bestehen.
        self.assertIn("Ja", persona.TECHNIK_REGEL)
        self.assertIn("erfolgreich", persona.TECHNIK_REGEL)

    def test_ehrlichkeit_bleibt_das_letzte(self):
        self.assertTrue(persona.text_prompt(JETZT).rstrip().endswith(persona.SCHLUSS.strip()))
        self.assertTrue(persona.live_prompt(JETZT).rstrip().endswith(persona.SCHLUSS.strip()))

    def test_person_prompt_zeigt_keine_technikwoerter(self):
        # Der Prompt der Person-Instanz darf die Regel nennen, ohne selbst von Skills/Vorgaben zu reden (Fremdperson sieht nichts davon).
        person = {"id": "p1", "name": "Kontakt B", "persona": "", "werkzeuge": [], "kanaele": {}}
        p = persona.person_prompt(JETZT, person)
        for wort in ("Skill", "Vorgabe", "VORGABEN"):
            self.assertNotIn(wort, p)


class EikeWerkzeuge(ta.Basis, Sammler):
    """Skills- und Vorgaben-Werkzeuge: Erfolg, Rückfrage und jeder Fehlerweg."""

    def test_skills_alle_wege(self):
        s = wskills
        gabriel = self.gabriel["name"]
        rs = [
            s.skill_schreiben("x", "z", "a", ["Unbekannte"], "ab"),                        # Person unbekannt
            s.skill_schreiben("x", "z", "a", [""], "ab"),                                   # Person fehlt
            s.skill_schreiben("x", "z", "a", [gabriel], "ab", werkzeuge=["gibt_es_nicht"]),  # Fähigkeit unbekannt
            s.skill_schreiben("x", "z", "a", ["eike"], "ab", werkzeuge=["akte_erfassen"]),  # Fähigkeit ohne Person
            s.skill_schreiben("x", "", "a", ["eike"], "ab"),                               # Feld fehlt
            s.skills_lesen(),                                                               # leer
            s.skill_aendern("99"),                                                          # gibt es nicht
            s.skill_bestaetigen("99"),
            s.skill_widerrufen("99"),
            s.skill_testlauf("99", gabriel),
        ]
        # Entwurf für eine Person -> Rückfrage; ohne Ja bleibt er Entwurf
        e = s.skill_schreiben(ta.NAME, "Meldungen von Kontakt M annehmen", "Kontakt M meldet eine Fahrt", [gabriel], ABLAUF,
                              werkzeuge=ta.WERKZEUGE, felder=ta.FELDER)
        sid = str(fach.standard().finden(ta.NAME, ("entwurf",))[0]["id"])
        rs += [e, s.skills_lesen(), s.skill_testlauf(sid, gabriel), s.skill_testlauf(sid, "Niemand"),
               s.skill_bestaetigen(sid),                                                    # ohne Ja
               s.skill_aendern(sid, zweck="Neu"),
               ]
        sid = str(fach.standard().finden(ta.NAME, ("entwurf",))[0]["id"])
        rs += [s.skill_bestaetigen(sid, zustimmung=True), s.skills_lesen(), s.skill_aendern(ta.NAME, zweck="Wieder neu")]
        sid = str(fach.standard().finden(ta.NAME, ("entwurf",))[0]["id"])
        rs += [s.skill_bestaetigen(sid, zustimmung=True), s.skill_bestaetigen(sid, zustimmung=True),   # zweites Mal: kein Entwurf mehr
               s.skill_schreiben("nur_fuer_mich", "Morgens kurz", "morgens", ["eike"], "Fass dich kurz."),   # sofort wirksam
               s.skill_widerrufen("nur_fuer_mich"), s.skill_widerrufen("nur_fuer_mich"), s.skill_widerrufen(ta.NAME)]
        self.pruefe(self, "skills", *rs)

    def test_vorgaben_alle_wege(self):
        v = wvorgaben
        rs = [v.vorgaben_lesen(), v.vorgaben_lesen("garantie"), v.vorgabe_merken(""), v.vorgabe_merken("Nie ohne mich absenden.", "garantie"),
              v.vorgabe_merken("Nie ohne mich absenden.", "garantie"),                      # Dublette
              v.vorgaben_lesen(), v.vorgaben_lesen("garantie"), v.vorgaben_lesen("schule"),
              v.vorgabe_aendern("gibtsnicht", "x"), v.vorgabe_aendern("absenden", "Immer erst fragen."),
              v.vorgabe_widerrufen("gibtsnicht"), v.vorgabe_widerrufen("erst fragen"), v.vorgaben_lesen()]
        # eine zu einem Skill gehörende Vorgabe lässt sich nicht einzeln ändern
        wskills.skill_schreiben("nur_fuer_mich", "Morgens kurz", "morgens", ["eike"], "Fass dich kurz.")
        rs += [v.vorgabe_aendern("Morgens kurz", "anders"), v.vorgabe_widerrufen("Morgens kurz")]
        self.pruefe(self, "vorgaben", *rs)

    def test_zwei_gleiche_vorgaben_nennen_beide_ohne_technik(self):
        wvorgaben.vorgabe_merken("Erinnerungen immer ruhig formulieren.", "erinnerungen")
        wvorgaben.vorgabe_merken("Erinnerungen am Abend kurz halten.", "erinnerungen")
        self.pruefe(self, "mehrdeutig", wvorgaben.vorgabe_aendern("Erinnerungen", "x"), wvorgaben.vorgabe_widerrufen("Erinnerungen"))

    def test_zwei_gleiche_skills_nennen_beide_ohne_technik(self):
        wskills.skill_schreiben("hilfe_a", "Zweck", "a", ["eike"], "Mach A.")
        wskills.skill_schreiben("hilfe_b", "Zweck", "a", ["eike"], "Mach B.")
        self.pruefe(self, "mehrdeutig", wskills.skill_widerrufen("hilfe"))

    def test_bloecke_und_ueberschriften(self):
        wvorgaben.vorgabe_merken("Nie ohne mich absenden.", "garantie")
        self.skill(anweisung=ABLAUF)
        eike = vblock.bauen()
        self.assertTrue(eike)
        self.pruefe(self, "eike-block", eike)
        # viele Einträge -> Hinweis „weitere“
        voll = vblock.formatieren([{"id": i, "bereich": "allgemein", "text": "x" * 120} for i in range(1, 60)])
        self.pruefe(self, "block-ende", voll)
        self.pruefe(self, "skill-liste", zugriff.eike_block(maximal=120))

    def test_testlauf_ist_ohne_technik(self):
        sid = self.skill()
        s = fach.standard().eintrag(sid)
        self.pruefe(self, "testlauf", zugriff.testlauf(s, None), zugriff.testlauf(s, self.maya), zugriff.testlauf(s, {"id": "p999"}))

    def test_beschreibungen_der_faehigkeiten_in_skills_lesen(self):
        wakte._registrieren()
        sp.registrieren_alle()
        for name, d in primitive.PERSONENFAEHIG.items():
            self.pruefe(self, f"fähigkeit {name}", d["beschreibung"])
            for o, od in d["optionen"].items():
                self.pruefe(self, f"option {o}", od.get("beschreibung", "x"))


class PersonenInstanz(ta.Basis, Sammler):
    """Alles, was eine Person-Instanz (Kontakt M, Kontakt B) aus Akte, Antrag und Block zu lesen bekommt."""

    def test_block_fuer_die_person(self):
        self.skill(anweisung=ABLAUF)
        b = zugriff.person_block(self.gabriel)
        self.assertTrue(b)
        # Der Block nennt die Namen der Funktionen (die braucht das Modell), aber keine Technikwörter als Erzähltext.
        ohne_funktionen = re.sub(r"\b[a-z]+(?:_[a-z]+)+\b", "", b)
        self.assertIsNone(TECHNIK.search(ohne_funktionen), TECHNIK.search(ohne_funktionen) and TECHNIK.search(ohne_funktionen).group(0))
        self.assertIn("Technik", b)         # der Block erinnert die Person-Instanz an die Regel

    def test_akte_wege(self):
        self.skill()
        g, m = self.gabriel, self.maya
        rs = [
            self.aus("akte_lesen", g, skill=ta.NAME),                                       # noch nichts
            self.aus("akte_fehlt", g, skill=ta.NAME),
            self.aus("akte_erfassen", g, skill=ta.NAME, felder={"datum": "15.09.2026", "linie": "360"}),
            self.aus("akte_erfassen", g, skill=ta.NAME, felder={"gibtsnicht": "x"}),
            self.aus("akte_erfassen", g, skill=ta.NAME, felder={"datum": "kein datum"}),
            self.aus("akte_erfassen", g, skill=ta.NAME, felder="kaputt"),
            self.aus("akte_erfassen", g, skill=ta.NAME, felder={}),
            self.aus("akte_erfassen", g, skill="gibt_es_nicht", felder={"linie": "1"}),
            self.aus("akte_lesen", g, skill=ta.NAME),
            self.aus("akte_fehlt", g, skill=ta.NAME),
            self.aus("garantie_antrag_aus_akte", g, skill=ta.NAME),                         # unvollständig
            self.aus("akte_lesen", m, skill=ta.NAME),                                       # Kontakt B: nichts vereinbart
            self.aus("akte_erfassen", m, skill=ta.NAME, felder={"linie": "1"}),
            self.aus("akte_lesen", None, skill=ta.NAME),                                    # Eike
        ]
        self.pruefe(self, "akte", *rs)

    def test_akte_ohne_felder(self):
        self.skill(name="ohne_felder", felder=[])
        self.pruefe(self, "ohne felder", self.aus("akte_erfassen", self.gabriel, skill="ohne_felder", felder={"a": "b"}))

    def test_antrag_wege(self):
        self.skill()
        self.fertig_erfassen()
        rs = [self.aus("garantie_antrag_aus_akte", self.gabriel, skill=ta.NAME)]            # keine gemessene Fahrt
        self.fahrt_bereit()
        rs.append(self.antrag_anfordern())                                                  # Erfolg
        rs.append(self.antrag_anfordern())                                                  # zweiter Versuch
        rs.append(self.aus("akte_erfassen", self.gabriel, skill=ta.NAME, felder={"linie": "1"}))   # abgeschlossen
        rs.append(self.aus("akte_lesen", self.gabriel, skill=ta.NAME))
        self.pruefe(self, "antrag", *rs)

    def test_antrag_ohne_profil_und_ohne_freigabe(self):
        self.skill([self.maya["id"]])
        self.aus("akte_erfassen", self.maya, skill=ta.NAME, felder=ta.VOLL)
        r1 = self.aus("garantie_antrag_aus_akte", self.maya, skill=ta.NAME)                 # kein Profil
        with mock.patch.object(wakte.konnektoren, "benutzbar", return_value=False):
            r2 = self.aus("garantie_antrag_aus_akte", self.maya, skill=ta.NAME)             # Garantie aus
        self.pruefe(self, "antrag ohne profil", r1, r2)

    def test_meldungen_an_eike(self):
        """Anliegen und Push an Eike (Titel/Text) — Friday liest sie ihm vor."""
        self.skill()
        self.fertig_erfassen()
        self.antrag_anfordern()          # keine Fahrt -> „Fahrt nicht gefunden“-Anliegen
        from kern.anliegen import fach as anliegen
        offen = anliegen.standard().offene("eike")
        self.pruefe(self, "anliegen", *[f"{a.get('titel', '')} {a.get('text', '')}" for a in offen])


class Rundnachricht(tp.Basis, Sammler):
    """Personen wählen, Rundnachricht, Medienwunsch — Fehler, Rückfragen und Hinweise an Eike."""

    def test_personen_und_rundnachricht(self):
        self.skill()
        g, m = self.gabriel, self.maya
        rs = [
            self.aus("personen_auswaehlen", g, regel="mamas_freunde"),
            self.aus("personen_auswaehlen", g, regel="alle_kontakte"),
            self.aus("personen_auswaehlen", g, regel="garantie_beteiligte"),
            self.aus("personen_auswaehlen", g, regel="garantie_beteiligte", schluessel="hinweis_selbst_einreichen"),
            self.aus("personen_auswaehlen", None, regel="garantie_beteiligte"),
            self.aus("nachricht_an_personen", g, empfaenger="garantie_beteiligte", text="", schluessel="k"),
            self.aus("nachricht_an_personen", g, empfaenger="garantie_beteiligte", text="Hallo", schluessel=""),
            self.aus("nachricht_an_personen", g, empfaenger="garantie_beteiligte", text="x" * 400, schluessel="k"),
            self.aus("nachricht_an_personen", g, empfaenger="garantie_beteiligte", text="Eigener Text", schluessel="k"),
            self.aus("nachricht_an_personen", g, empfaenger="alle_kontakte", text=tp.TEXT, schluessel="k"),
            self.aus("nachricht_an_personen", g, empfaenger="garantie_beteiligte", text=tp.TEXT, schluessel="hinweis_selbst_einreichen"),
            self.aus("nachricht_an_personen", g, empfaenger="garantie_beteiligte", text=tp.TEXT, schluessel="hinweis_selbst_einreichen"),
            self.aus("rundnachricht_freigeben"),
            self.aus("rundnachricht_freigeben", schluessel="hinweis_selbst_einreichen"),
            self.aus("rundnachricht_freigeben", schluessel="hinweis_selbst_einreichen", ja=True),
            self.aus("rundnachricht_freigeben"),
            self.aus("nachricht_an_personen", g, empfaenger="garantie_beteiligte", text=tp.TEXT, schluessel="hinweis_selbst_einreichen"),
        ]
        with mock.patch.object(sp.konnektoren, "schreiben_erlaubt", return_value=False):
            rs.append(self.aus("nachricht_an_personen", m, empfaenger="garantie_beteiligte", text=tp.TEXT, schluessel="neu"))
        self.pruefe(self, "rundnachricht", *rs)
        # Hinweis an Eike (Anliegen + Push), wie ihn das Modell vorliest
        for aufruf in self.push.call_args_list:
            self.pruefe(self, "push", " ".join(str(a) for a in aufruf.args[1:3]))

    def test_freigeben_ohne_schreibrecht(self):
        self.skill()
        self.aus("nachricht_an_personen", self.gabriel, empfaenger="garantie_beteiligte", text=tp.TEXT, schluessel="hinweis_selbst_einreichen")
        with mock.patch.object(sp.konnektoren, "schreiben_erlaubt", return_value=False):
            self.pruefe(self, "freigeben", self.aus("rundnachricht_freigeben", schluessel="hinweis_selbst_einreichen", ja=True))

    def test_medien_wege(self):
        self.skill(**{**tp.SKILL_B, "zielgruppe": [self.maya["id"]]})
        m = self.maya

        async def suche(titel):
            return [tp.MATRIX] if "matrix" in titel.lower() else []

        async def nichts(titel):
            raise sp.sonarr.SonarrFehler("aus")

        with mock.patch.object(sp.radarr, "online_suchen", suche), mock.patch.object(sp.sonarr, "online_suchen", nichts):
            rs = [self.aus("medien_wunsch_stellen", m, titel="Matrix", art=""), self.aus("medien_wunsch_stellen", m, titel="", art="film"),
                  self.aus("medien_wunsch_stellen", m, titel="Gibtesnicht", art="film"),
                  self.aus("medien_wunsch_stellen", m, titel="Matrix", art="film"),
                  self.aus("medien_wunsch_stellen", m, titel="Matrix", art="film"),
                  self.aus("medien_wunsch_stellen", m, titel="Etwas", art="serie"),
                  self.aus("medien_wunsch_stellen", self.gabriel, titel="Matrix", art="film")]
        self.pruefe(self, "medien", *rs)
        # Das Anliegen für Eike, das aus dem Wunsch entsteht
        from kern.anliegen import fach as anliegen
        a = anliegen.standard()
        for x in a.offene("eike"):
            self.pruefe(self, "medien-anliegen", f"{x.get('titel', '')} {x.get('text', '')}")

    def test_option_selbst_ohne_medienrecht(self):
        with mock.patch.object(sp.konnektoren, "schreiben_erlaubt", return_value=False):
            self.pruefe(self, "option", sp._medien_selbst_pruefen("selbst"))


if __name__ == "__main__":
    unittest.main()
