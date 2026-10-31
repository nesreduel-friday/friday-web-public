"""Wirkungen (P0 der Ehrlichkeit, 12.10.2026): Jedes Werkzeugergebnis trägt status (ok|nicht|rueckfrage), art und eine kurze deutsche
Quittung. Hier: Erkennung der Präfixe, Katalog-Vollständigkeit, Form von `katalog.ausfuehren`, Sammeln je Zug (Chat/Live) und je
Wirkungswerkzeug Erfolg UND Fehlschlag. Der Rest (Kalender, WhatsApp, Garantie-Status) hängt an den Fixtures der jeweiligen Tests."""

import asyncio
import os
import re
import tempfile
import unittest
from unittest import mock

from kern import konnektoren
from kern.dienste import snapchat
from kern.ehrlichkeit import wirkung as w
from kern.personen import register
from kern.skills import fach, primitive
from kern.vorgaben import fach as vfach
from kern.werkzeuge import katalog, personenprimitive as sp
from kern.werkzeuge import snapchat as wsnap
from test import test_ehrlichkeit as te
from test import test_personenprimitive as tp
from test import test_skills_akte as ta
from test import test_vorgaben as tv


def lauf(c):
    return asyncio.run(c)


def pruefe_wirkung(test: unittest.TestCase, r: dict, status: str, art: str, wo: str = "") -> None:
    """Form eines `katalog.ausfuehren`-Ergebnisses: status/art/quittung gesetzt, Quittung ein kurzer Satz ohne Technikwörter,
    bei status != ok beginnt der Text für das Modell mit „NICHT ERLEDIGT“."""
    test.assertEqual((r.get("status"), r.get("art")), (status, art), (wo, r))
    q = r.get("quittung") or ""
    test.assertTrue(q.strip() and len(q) <= 160 and q.count("\n") == 0, (wo, q))
    test.assertIsNone(w.TECHNIK.search(q), (wo, q))
    kopf = w.KOEPFE[art][("ok", "nicht", "rueckfrage").index(status)]
    if status != "ok":
        test.assertTrue(q.startswith(kopf), (wo, q, kopf))
    text = r.get("ergebnis") or r.get("fehler") or ""
    if "ergebnis" in r:
        test.assertEqual(text.startswith("NICHT ERLEDIGT"), status != "ok", (wo, text[:80]))


class Erkennung(unittest.TestCase):
    FAELLE = [
        ("vorgabe_merken", "NICHTS gespeichert. Der Grund. Sag Eike ehrlich, dass es nicht klappte.", "nicht"),
        ("schicke_nachricht", "NICHTS gesendet. Sag Eike ehrlich, dass nichts geschickt wurde. Zu lang.", "nicht"),
        ("vorgabe_aendern", "Das hat so nicht geklappt: zu lang.", "nicht"),
        ("vorgabe_merken", "Das hat nicht geklappt: doppelt. Es ist NICHTS festgehalten.", "nicht"),
        ("termin_bestaetigen", "Noch nichts eingetragen.", "nicht"),
        ("termin_anlegen", "Noch nichts eingetragen. Soll ich Zahnarzt am Montag in den Kalender Privat eintragen?", "rueckfrage"),
        ("schicke_snap", "Noch nichts gesendet. Frag Eike kurz, ob er Kontakt A meint.", "rueckfrage"),
        ("skill_aendern", "Dazu habe ich nichts Passendes. Frag Eike kurz, was er meint.", "rueckfrage"),
        ("uhrzeit", "Es ist 12:00 Uhr.", "ok"),
        ("akte_lesen", "Noch nichts erfasst. Frag Eike.", "ok"),               # lesend: nie „nicht“ aus Text
        ("merken", "Notiert, Sir: a ist b.", "ok"),
    ]

    def test_praefixe_und_rueckfragen(self):
        for name, text, status in self.FAELLE:
            t, wirk = w.normalisieren(name, text)
            self.assertEqual(t, text)
            self.assertEqual(wirk.status, status, (name, text))
            self.assertEqual(wirk.art, w.art_von(name))
            self.assertTrue(wirk.quittung.endswith((".", "?")), wirk.quittung)
            self.assertIsNone(w.TECHNIK.search(wirk.quittung), wirk.quittung)

    def test_fehler_dict(self):
        t, wirk = w.normalisieren("schicke_snap", {"fehler": "Es ging nicht."})
        self.assertEqual((t, wirk.status, wirk.art), ("Es ging nicht.", "nicht", "versand"))

    def test_wirkung_geht_unveraendert_durch(self):
        e = w.Wirkung("rueckfrage", "antrag", "Noch offen: x.", "Text")
        t, wirk = w.normalisieren("irgendwas", e)
        self.assertEqual((t, wirk.status, wirk.art, wirk.quittung), ("Text", "rueckfrage", "antrag", "Noch offen: x."))

    def test_wirkung_ist_ein_str_und_validiert(self):
        e = w.Wirkung("ok", "merken", "Gemerkt: a.", "Notiert.")
        self.assertIsInstance(e, str)
        self.assertEqual((e, e.text, e.startswith("Not")), ("Notiert.", "Notiert.", True))
        with self.assertRaises(ValueError):
            w.Wirkung("vielleicht", "merken", "x", "y")
        with self.assertRaises(ValueError):
            w.Wirkung("ok", "zauberei", "x", "y")

    def test_nicht_str_ist_ok(self):
        t, wirk = w.normalisieren("uhrzeit", ["a", "b"])
        self.assertEqual((t, wirk.status), (["a", "b"], "ok"))

    def test_grund_ohne_anweisungen_und_technik(self):
        q = w.normalisieren("schicke_nachricht", wsnap.KEIN_ERFOLG + "Der Text ist zu lang (300 statt 280) — fass ihn kürzer.")[1].quittung
        self.assertEqual(q, "Nicht gesendet: Der Text ist zu lang (300 statt 280) — fass ihn kürzer.")
        q = w.normalisieren("skill_schreiben", "NICHTS gespeichert. Der Skill braucht eine Version. Sag Eike ehrlich.")[1].quittung
        self.assertEqual(q, "Nicht festgehalten.")                          # Technikwort im Grund -> ohne Zusatz

    def test_praefix_nicht_doppelt(self):
        self.assertEqual(w.mit_praefix("x", "nicht"), "NICHT ERLEDIGT: x")
        self.assertEqual(w.mit_praefix("NICHT ERLEDIGT: x", "nicht"), "NICHT ERLEDIGT: x")
        self.assertEqual(w.mit_praefix("x", "ok"), "x")


class Katalog(unittest.TestCase):
    def namen(self):
        return {e["name"] for m in katalog._MODULE for e in m.ERKLAERUNGEN}

    def test_jedes_werkzeug_hat_eine_art(self):
        for n in self.namen():
            self.assertIn(n, w.ART, f"{n} fehlt in wirkung.ART")
        self.assertTrue(set(w.ART.values()) <= set(w.ARTEN))

    def test_art_nennt_nur_werkzeuge_des_katalogs(self):
        self.assertEqual(set(w.ART) - self.namen(), set())

    def test_lesende_sind_lesen_aendernde_nie(self):
        # Alles, was die Denkrunde darf, liest — bis auf ihre eigenen Schreibwerkzeuge (Gedankenbuch).
        eigene = {"gedanke_festhalten", "hinweis_geben", "faden_anlegen", "faden_weiter", "faden_schliessen", "vorhaben_vorschlagen", "eike_fragen"}   # eike_fragen: benannte Ausnahme (Frage an Eike, nach Fremdtext gesperrt); vorhaben_vorschlagen: benannte Ausnahme (legt nur einen Vorschlag an, nach Fremdtext gesperrt)
        for n in katalog.DENKEN_ERLAUBT - eigene:
            self.assertEqual(w.ART[n], "lesen", n)
        for m in katalog._MODULE:
            for n in getattr(m, "NICHT_NACH_FREMDTEXT", ()):
                self.assertNotEqual(w.ART[n], "lesen", n)

    def test_wirkungswerkzeuge_haben_ihre_art(self):
        erwartet = {"vorgabe": "vorgabe_merken vorgabe_aendern vorgabe_widerrufen",
                    "skill": "skill_schreiben skill_aendern skill_bestaetigen skill_widerrufen",
                    "versand": "schicke_nachricht schicke_snap nachricht_an_personen rundnachricht_freigeben whatsapp_senden",
                    "antrag": "medien_wunsch_stellen garantie_antrag_aus_akte garantie_status_setzen",
                    "erinnerung": "erinnerung erinnerung_absagen", "merken": "merken vergessen", "akte": "akte_erfassen"}
        for art, namen in erwartet.items():
            for n in namen.split():
                self.assertEqual(w.art_von(n), art, n)

    def test_alle_wirkungswerkzeuge_liefern_ausdruecklich_eine_wirkung(self):
        """Die Funktionen selbst sind mit `wirkt` beschrieben: ein Fehlaufruf ohne Netz liefert eine `Wirkung`, keinen blanken Text."""
        for n in ("vorgabe_merken", "vorgabe_aendern", "vorgabe_widerrufen", "skill_schreiben", "skill_aendern", "skill_bestaetigen",
                  "skill_widerrufen", "rundnachricht_freigeben", "akte_erfassen", "merken", "vergessen", "erinnerung",
                  "erinnerung_absagen", "garantie_status_setzen", "schicke_nachricht", "schicke_snap", "medien_wunsch_stellen",
                  "garantie_antrag_aus_akte", "nachricht_an_personen", "whatsapp_senden"):
            f = next(m.WERKZEUGE[n] for m in katalog._MODULE if n in m.WERKZEUGE)
            self.assertTrue(hasattr(f, "__wrapped__") or n == "nachricht_an_personen", n)


class AusfuehrenForm(tv.Basis):
    def test_ergebnis_mit_status_art_quittung(self):
        r = self.aus("uhrzeit")
        pruefe_wirkung(self, r, "ok", "lesen")
        self.assertEqual(set(r), {"ergebnis", "status", "art", "quittung"})

    def test_nicht_erledigt_einheitlich_und_ehrlichkeitstext_bleibt(self):
        r = self.aus("vorgabe_merken", text="")
        pruefe_wirkung(self, r, "nicht", "vorgabe")
        self.assertTrue(r["ergebnis"].startswith("NICHT ERLEDIGT: Das hat nicht geklappt"), r["ergebnis"])
        self.assertIn("NICHTS festgehalten", r["ergebnis"])

    def test_nichts_gesendet_wird_nicht_ersetzt_nur_ergaenzt(self):
        konnektoren.setzen("snapchat", True)
        konnektoren.schreiben_setzen("snapchat", True)
        r = lauf(katalog.ausfuehren("schicke_nachricht", {"person": "Niemand", "text": "Hi"}, eingabe="schick ihm Hi"))
        self.assertTrue(r["ergebnis"].startswith("NICHT ERLEDIGT: NICHTS gesendet."), r["ergebnis"])
        pruefe_wirkung(self, r, "nicht", "versand")

    def test_fehler_bleibt_fehler_und_hat_status(self):
        r = self.aus("gibt_es_nicht")
        self.assertIn("fehler", r)
        self.assertNotIn("ergebnis", r)
        self.assertEqual((r["status"], r["art"]), ("nicht", "sonstiges"))
        self.assertTrue(r["quittung"].startswith("Nicht erledigt"))
        r = self.aus("gedanke_festhalten")                       # nicht erlaubt (nur Denkrunde)
        self.assertEqual(r["status"], "nicht")
        self.assertNotIn("Werkzeug", r["quittung"])

    def test_geheimes_wird_auch_in_der_quittung_geschwaerzt(self):
        key = "sk-ant-" + "a" * 30
        with mock.patch.dict(katalog.vorgaben.WERKZEUGE, {"vorgaben_lesen": lambda bereich="": f"Schlüssel {key}"}):
            r = self.aus("vorgaben_lesen")
        self.assertNotIn(key, str(r))

    def test_modell_sieht_nur_ergebnis_oder_fehler(self):
        r = self.aus("vorgabe_merken", text="")
        m = w.fuers_modell(r)
        self.assertEqual(set(m), {"ergebnis"})
        self.assertEqual(w.fuers_modell({"fehler": "x", "status": "nicht", "art": "a", "quittung": "q"}), {"fehler": "x"})


class Vorgaben(tv.Basis):
    def test_merken_aendern_widerrufen(self):
        pruefe_wirkung(self, self.aus("vorgabe_merken", text="Nie ohne mich absenden.", bereich="garantie"), "ok", "vorgabe")
        r = self.aus("vorgabe_merken", text="Nie ohne mich absenden.", bereich="garantie")           # Dublette
        pruefe_wirkung(self, r, "nicht", "vorgabe", "dublette")
        r = self.aus("vorgabe_merken", text="Nie ohne mich absenden.")
        self.assertTrue(r["quittung"].startswith("Nicht festgehalten"))
        r = self.aus("vorgabe_aendern", id="absenden", text="Immer erst fragen.")
        pruefe_wirkung(self, r, "ok", "vorgabe", "ändern")
        self.assertTrue(r["quittung"].startswith("Geändert: Immer erst fragen"), r["quittung"])
        pruefe_wirkung(self, self.aus("vorgabe_aendern", id="gibtsnicht", text="x"), "rueckfrage", "vorgabe", "ändern fehl")
        r = self.aus("vorgabe_widerrufen", id="erst fragen")
        pruefe_wirkung(self, r, "ok", "vorgabe", "widerrufen")
        self.assertEqual(r["quittung"], "Zurückgenommen.")
        pruefe_wirkung(self, self.aus("vorgabe_widerrufen", id="gibtsnicht"), "rueckfrage", "vorgabe", "widerrufen fehl")

    def test_erfolgs_quittung_nennt_den_inhalt(self):
        r = self.aus("vorgabe_merken", text="Antworte immer auf Deutsch.", bereich="antworten")
        self.assertEqual(r["quittung"], "Festgehalten: Antworte immer auf Deutsch.")

    def test_quittung_ohne_technikwort_im_inhalt(self):
        r = self.aus("vorgabe_merken", text="Nutze nie eine alte Version der Regel.", bereich="allgemein")
        pruefe_wirkung(self, r, "ok", "vorgabe")
        self.assertEqual(r["quittung"], "Festgehalten.")                    # „Version“ ist ein Technikwort: Zusatz entfällt


class SkillsUndAkte(ta.Basis):
    def zus(self, name, **a):
        return lauf(katalog.ausfuehren(name, a, zustimmung=a.pop("ja", False)))

    def test_skill_wege(self):
        g = self.gabriel["name"]
        pruefe_wirkung(self, self.aus("skill_schreiben", None, name="nur_fuer_mich", zweck="Morgens kurz", ausloeser="morgens",
                                      zielgruppe=["eike"], anweisung="Fass dich kurz."), "ok", "skill", "sofort")
        r = self.aus("skill_schreiben", None, name="x", zweck="z", ausloeser="a", zielgruppe=["Unbekannte"], anweisung="ab")
        pruefe_wirkung(self, r, "rueckfrage", "skill", "unbekannte person (fragt nach)")
        r = self.aus("skill_schreiben", None, name=ta.NAME, zweck="Meldungen annehmen", ausloeser="meldet", zielgruppe=[g],
                     anweisung=ta.ANWEISUNG, werkzeuge=ta.WERKZEUGE, felder=ta.FELDER)
        pruefe_wirkung(self, r, "rueckfrage", "skill", "entwurf für eine Person")
        self.assertEqual(r["quittung"], "Wartet auf Eikes Ja: Meldungen annehmen.")
        sid = str(fach.standard().finden(ta.NAME, ("entwurf",))[0]["id"])
        pruefe_wirkung(self, self.aus("skill_bestaetigen", None, id=sid), "rueckfrage", "skill", "ohne Ja")
        r = lauf(katalog.ausfuehren("skill_bestaetigen", {"id": sid}, zustimmung=True))
        pruefe_wirkung(self, r, "ok", "skill", "mit Ja")
        pruefe_wirkung(self, self.aus("skill_bestaetigen", None, id="99"), "rueckfrage", "skill", "gibt es nicht")
        r = self.aus("skill_aendern", None, id="nur_fuer_mich", zweck="Morgens sehr kurz")
        pruefe_wirkung(self, r, "ok", "skill", "ändern")
        self.assertTrue(r["quittung"].startswith("Geändert"), r["quittung"])
        pruefe_wirkung(self, self.aus("skill_aendern", None, id="99", zweck="x"), "rueckfrage", "skill", "ändern fehl")
        pruefe_wirkung(self, self.aus("skill_widerrufen", None, id="nur_fuer_mich"), "ok", "skill", "widerrufen")
        pruefe_wirkung(self, self.aus("skill_widerrufen", None, id="nur_fuer_mich"), "rueckfrage", "skill", "widerrufen fehl")

    def test_akte_erfassen(self):
        self.skill()
        r = self.aus("akte_erfassen", self.gabriel, skill=ta.NAME, felder={"datum": "15.09.2026", "linie": "360"})
        pruefe_wirkung(self, r, "ok", "akte", "erfolg")
        pruefe_wirkung(self, self.aus("akte_erfassen", self.gabriel, skill=ta.NAME, felder={"gibtsnicht": "x"}), "nicht", "akte", "feld")
        pruefe_wirkung(self, self.aus("akte_erfassen", self.gabriel, skill=ta.NAME, felder="kaputt"), "nicht", "akte", "kaputt")
        pruefe_wirkung(self, self.aus("akte_erfassen", self.maya, skill=ta.NAME, felder={"linie": "1"}), "rueckfrage" if False else "nicht",
                       "akte", "nichts vereinbart")

    def test_antrag_aus_akte(self):
        self.skill()
        self.erg("akte_erfassen", self.gabriel, skill=ta.NAME, felder={"datum": "15.09.2026"})
        pruefe_wirkung(self, self.aus("garantie_antrag_aus_akte", self.gabriel, skill=ta.NAME), "nicht", "antrag", "unvollständig")
        self.erg("akte_erfassen", self.gabriel, skill=ta.NAME, felder=ta.VOLL)
        self.fahrt_bereit()
        r = self.antrag_anfordern()
        pruefe_wirkung(self, r, "ok", "antrag", "erfolg")
        self.assertTrue(r["quittung"].startswith("Vorbereitet: wartet auf Eikes Freigabe"), r["quittung"])


class Rundnachricht(tp.Basis):
    def test_nachricht_und_freigabe(self):
        self.skill()
        g = self.gabriel
        kw = dict(empfaenger="garantie_beteiligte", schluessel="hinweis_selbst_einreichen")
        r = self.aus("nachricht_an_personen", g, text="", **kw)
        pruefe_wirkung(self, r, "nicht", "versand", "leer")
        self.assertTrue(r["ergebnis"].startswith("NICHT ERLEDIGT: NICHTS gesendet."), r["ergebnis"])
        r = self.aus("nachricht_an_personen", g, text=tp.TEXT, **kw)
        pruefe_wirkung(self, r, "rueckfrage", "versand", "liegt zur Freigabe")
        self.assertEqual(r["quittung"], "Noch nicht gesendet: wartet auf Eikes Ja.")
        pruefe_wirkung(self, self.aus("rundnachricht_freigeben", schluessel="hinweis_selbst_einreichen"), "rueckfrage", "versand", "ohne Ja")
        r = self.aus("rundnachricht_freigeben", schluessel="hinweis_selbst_einreichen", ja=True)
        pruefe_wirkung(self, r, "ok", "versand", "mit Ja")
        pruefe_wirkung(self, self.aus("rundnachricht_freigeben"), "nicht", "versand", "nichts wartet")

    def test_nachricht_direkt(self):
        self.skill()
        with mock.patch.object(sp.zugriff, "richtlinie", return_value="selbst"):
            r = self.aus("nachricht_an_personen", self.gabriel, text=tp.TEXT, empfaenger="garantie_beteiligte",
                         schluessel="hinweis_selbst_einreichen")
        pruefe_wirkung(self, r, "ok", "versand", "direkt")
        self.assertEqual(r["quittung"], "Auf dem Weg: Mitteilung an mehrere Personen.")

    def test_medienwunsch(self):
        self.skill(**{**tp.SKILL_B, "zielgruppe": [self.maya["id"]]})

        async def suche(titel):
            return [tp.MATRIX] if "matrix" in titel.lower() else []

        with mock.patch.object(sp.radarr, "online_suchen", suche):
            r = self.aus("medien_wunsch_stellen", self.maya, titel="Matrix", art="film")
            pruefe_wirkung(self, r, "ok", "antrag", "erfolg")
            self.assertEqual(r["quittung"], "Vorbereitet: Wunsch „Matrix“ angenommen.")
            pruefe_wirkung(self, self.aus("medien_wunsch_stellen", self.maya, titel="Matrix", art="film"), "nicht", "antrag", "doppelt")
            pruefe_wirkung(self, self.aus("medien_wunsch_stellen", self.maya, titel="Gibtesnicht", art="film"), "rueckfrage", "antrag",
                           "fehlt (fragt nach)")
            pruefe_wirkung(self, self.aus("medien_wunsch_stellen", self.gabriel, titel="Matrix", art="film"), "nicht", "antrag", "nicht erlaubt")
            pruefe_wirkung(self, self.aus("medien_wunsch_stellen", self.maya, titel="Matrix", art=""), "rueckfrage", "antrag", "film/serie?")


class Snapchat(te.Basis):
    def test_nachricht(self):
        with mock.patch.object(wsnap.snapchat, "auftrag_legen", return_value="a1"):
            r = lauf(katalog.ausfuehren("schicke_nachricht", {"person": "Kontakt C", "text": "Hi"}, eingabe="schick Kontakt C hi"))
        pruefe_wirkung(self, r, "ok", "versand", "erfolg")
        self.assertEqual(r["quittung"], "Auf dem Weg: Nachricht an Kontakt C.")
        for a, status in ((dict(person="Kontakt C", text="Hi"), "rueckfrage"), (dict(person="Zzyzx Niemand", text="Hi"), "rueckfrage"),
                          (dict(person="Kontakt C", text=""), "rueckfrage"),            # fragt, was geschrieben werden soll
                          (dict(person="Kontakt C", text="x" * 300), "nicht")):
            r = lauf(katalog.ausfuehren("schicke_nachricht", a, eingabe="Wie spät?" if a["text"] == "Hi" else "schick Kontakt C was"))
            pruefe_wirkung(self, r, status, "versand", str(a))
        r = lauf(katalog.ausfuehren("schicke_nachricht", {"person": "Kontakt C", "text": "Hi"}, eingabe="schick hi", fremde_inhalte=True))
        pruefe_wirkung(self, r, "nicht", "versand", "fremdtext")

    def test_an_eike_und_dienstfehler(self):
        with mock.patch.object(wsnap.snapchat, "auftrag_legen", return_value="a1"):
            r = lauf(katalog.ausfuehren("schicke_nachricht", {"person": "mich", "text": "Hi"}, eingabe="schreib mir"))
        pruefe_wirkung(self, r, "ok", "versand", "an eike")
        self.assertEqual(r["quittung"], "Auf dem Weg: Nachricht an Eike.")
        with mock.patch.object(wsnap.snapchat, "auftrag_legen", side_effect=snapchat.SnapchatFehler("Das reicht erstmal.")):
            r = lauf(katalog.ausfuehren("schicke_nachricht", {"person": "Kontakt C", "text": "Hi"}, eingabe="schick Kontakt C hi"))
        pruefe_wirkung(self, r, "nicht", "versand", "dienstfehler")
        self.assertEqual(r["quittung"], "Nicht gesendet: Das reicht erstmal.")

    def test_snap(self):
        def snap(**kw):
            with mock.patch("kern.werkzeuge.snapchat._aus_dem_netz", mock.AsyncMock(return_value=(b"bild", "", "Eiffelturm"))), \
                 mock.patch("kern.werkzeuge.snapchat.snapbild.vorbereiten", return_value=b"fertig"), \
                 mock.patch("kern.werkzeuge.snapchat.snapbild.ablegen", return_value="snapchat/ausgang/t.png"):
                return lauf(katalog.ausfuehren("schicke_snap", {"suche": "Eiffelturm", "quelle": "internet"}, **kw))
        r = snap(eingabe="schick mir einen Snap vom Eiffelturm")
        pruefe_wirkung(self, r, "ok", "versand", "erfolg")
        self.assertEqual(r["quittung"], "Auf dem Weg: Snap an Eike.")
        pruefe_wirkung(self, snap(eingabe="Wie wird das Wetter?"), "rueckfrage", "versand", "ohne Verlangen (fragt nach)")
        pruefe_wirkung(self, snap(eingabe="schick mir einen Snap", fremde_inhalte=True), "nicht", "versand", "fremdtext")


class Merken(tv.Basis):
    def test_merken_und_vergessen(self):
        r = self.aus("merken", thema="Lieblingsfarbe", inhalt="Blau")
        pruefe_wirkung(self, r, "ok", "merken", "merken")
        self.assertEqual(r["quittung"], "Gemerkt: Lieblingsfarbe.")
        r = self.aus("merken", fremd=True, thema="Wlan", inhalt="geheim")
        pruefe_wirkung(self, r, "rueckfrage", "merken", "fremdtext = Vorschlag")
        self.assertEqual(r["quittung"], "Noch offen: Wlan wartet auf Eikes Bestätigung.")
        pruefe_wirkung(self, self.aus("vergessen", thema="Lieblingsfarbe"), "ok", "merken", "vergessen")
        pruefe_wirkung(self, self.aus("vergessen", thema="Gibtesnicht"), "nicht", "merken", "nichts da")


class Erinnerung(tv.Basis):
    def test_erinnerung_und_absagen(self):
        from kern import config
        wann = (config.jetzt().replace(microsecond=0) + __import__("datetime").timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        with mock.patch("kern.dienste.push.geraete", return_value=[object()]):
            r = self.aus("erinnerung", titel="Zahnarzt", text="", zeitpunkt=wann)
        pruefe_wirkung(self, r, "ok", "erinnerung", "erfolg")
        self.assertEqual(r["quittung"], "Erinnerung gesetzt: Zahnarzt.")
        pruefe_wirkung(self, self.aus("erinnerung", titel="Zahnarzt", text="", zeitpunkt="2020-01-01T10:00"), "rueckfrage", "erinnerung",
                       "vorbei (fragt nach dem Zeitpunkt)")
        pruefe_wirkung(self, self.aus("erinnerung", titel="Zahnarzt", text="", zeitpunkt="morgen"), "nicht", "erinnerung", "ohne Zeit")
        pruefe_wirkung(self, self.aus("erinnerung", fremd=True, titel="X", text="", zeitpunkt=wann), "nicht", "erinnerung", "fremdtext")
        nr = __import__("kern.erinnerungen", fromlist=["x"]).standard().offene()[0]["id"]
        r = self.aus("erinnerung_absagen", nummer=nr)
        pruefe_wirkung(self, r, "ok", "erinnerung", "absagen")
        pruefe_wirkung(self, self.aus("erinnerung_absagen", nummer=nr), "nicht", "erinnerung", "absagen fehl")


class Quittungen(unittest.TestCase):
    def test_kopf_je_art_und_status(self):
        for art, (ok, nicht, frage) in w.KOEPFE.items():
            self.assertEqual(w.quittung_bauen(art, "ok"), f"{ok}.")
            self.assertEqual(w.quittung_bauen(art, "nicht", "weil"), f"{nicht}: weil.")
            self.assertEqual(w.quittung_bauen(art, "rueckfrage", "Soll ich?"), f"{frage}: Soll ich?")
        self.assertEqual(set(w.KOEPFE), set(w.ARTEN))

    def test_lang_wird_gekuerzt(self):
        self.assertLessEqual(len(w.quittung_bauen("merken", "ok", "x" * 500)), 160)


class Sammeln(unittest.TestCase):
    """Chat und Live sammeln je Zug die Wirkungen; das Modell sieht nur ergebnis/fehler."""

    def test_chat(self):
        from kern.sitzung import chat
        antworten = iter([
            ({"candidates": [{"content": {"parts": [{"functionCall": {"name": "uhrzeit", "args": {}, "id": "1"}}]}}]}, "m"),
            ({"candidates": [{"content": {"parts": [{"text": "fertig"}]}}]}, "m"),
        ])
        gesehen = []

        async def gemini(inhalt, bevorzugt=None):
            gesehen.append(inhalt)
            return next(antworten)

        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "AIzaTEST"}), \
             mock.patch.object(chat, "_gemini_anfrage", gemini):
            r = lauf(chat.antworten([{"rolle": "du", "text": "Wie spät?"}]))
        self.assertEqual(r["wirkungen"], [{"name": "uhrzeit", "status": "ok", "art": "lesen", "quittung": "Nachgesehen."}])
        antwort = gesehen[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
        self.assertEqual(set(antwort), {"ergebnis"})

    def test_live(self):
        from kern.sitzung import live
        sent = []

        class Gem:
            async def send(self, s):
                sent.append(s)

        class Br:
            async def send_json(self, m):
                pass

        mit = mock.MagicMock()
        mit.gespraech = 1
        liste: list = []
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()}):
            lauf(live._werkzeuge_ausfuehren(Gem(), Br(), {"functionCalls": [{"id": "1", "name": "uhrzeit", "args": {}}]}, mit,
                                            wirkungen=liste))
        self.assertEqual(liste, [{"name": "uhrzeit", "status": "ok", "art": "lesen", "quittung": "Nachgesehen."}])
        import json
        antwort = json.loads(sent[0])["toolResponse"]["functionResponses"][0]["response"]
        self.assertEqual(set(antwort), {"ergebnis"})


if __name__ == "__main__":
    unittest.main()
