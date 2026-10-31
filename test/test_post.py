"""IServ-Postfach: abgleichen, vorbereiten, suchen — ohne Netz und ohne Anmeldung.

Das Postfach ist eine Attrappe mit echten, hier erzeugten Mails. Ein Test,
der sich bei jedem Lauf bei IServ anmeldet, würde das Schulkonto belasten.
"""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from unittest import mock

from kern import konnektoren
from kern.dienste import iserv_mail
from kern.postfach import ablage, abgleich
from kern.schule import briefe
from kern.werkzeuge import katalog, post


def _mail(betreff, tage_alt, text="Hallo Eike.", anhang=None, html=False):
    m = EmailMessage()
    m["From"] = "Frau Muster <muster@schule.example>"
    m["To"] = "eike@schule.example"
    m["Subject"] = betreff
    m["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(days=tage_alt))
    if html:
        m.set_content(f"<p>{text}</p><p>Gru&szlig;</p>", subtype="html")
    else:
        m.set_content(text)
    if anhang:  # wie oft bei Mails: PDF als octet-stream
        m.add_attachment(anhang[1], maintype="application", subtype="octet-stream", filename=anhang[0])
    return m.as_bytes()


class Attrappe:
    postfach: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def ordner(self):
        return list(self.postfach)

    def uids(self, ordner):
        uv, mails = self.postfach[ordner]
        return uv, sorted(mails)

    def holen(self, ordner, uids):
        uv, mails = self.postfach[ordner]
        return [{"uid": u, "gelesen": mails[u][0], "groesse": len(mails[u][1]), "roh": mails[u][1], "nur_kopf": False}
                for u in uids if u in mails]


ERGEBNIS = {"kurz": "Treffen am Freitag.", "inhalt": "Der Kurs trifft sich am Freitag.",
            "termine": [{"datum": (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"), "was": "Kurstreffen"}],
            "fristen": [], "zu_tun": ["Plan lesen"], "kosten": "", "wichtig": True, "antwort_erwartet": False}


class Postfach(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "ISERV_USER": "", "ISERV_PASS": ""})
        self.env.start()
        konnektoren.setzen("iserv_mail", True)
        konnektoren.zugang_setzen("schulmanager", "eike", "geheim")
        abgleich._zuletzt = 0.0
        Attrappe.postfach = {
            "INBOX": (7, {1: (True, _mail("Alte Info", 200, anhang=("alt.pdf", b"%PDF alt"))),
                          2: (False, _mail("Überraschung im Kurs", 2, "Wir treffen uns am Freitag.",
                                           anhang=("plan.pdf", b"%PDF-1.4 plan")))}),
            "INBOX/Drafts": (1, {1: (True, _mail("Entwurf", 1))}),
            "INBOX/Sent": (3, {5: (True, _mail("Meine Antwort", 1))}),
        }
        self.gemini = mock.AsyncMock(return_value=(ERGEBNIS, "gemini-test"))
        self.flicken = [mock.patch.object(iserv_mail, "Verbindung", Attrappe),
                        mock.patch.object(briefe, "_gemini", self.gemini)]
        for f in self.flicken:
            f.start()

    def tearDown(self):
        for f in self.flicken:
            f.stop()
        self.env.stop()

    def _nach_betreff(self, betreff):
        return next(m for m in ablage.standard().liste(50) if m["betreff"] == betreff)

    def test_abgleich_speichert_richtig_und_laesst_entwuerfe_weg(self):
        self.assertEqual(asyncio.run(abgleich.durchgang(vorbereiten=0)), 3)
        neu, alt, gesendet = (self._nach_betreff(b) for b in ("Überraschung im Kurs", "Alte Info", "Meine Antwort"))
        self.assertEqual((neu["status"], alt["status"], gesendet["status"]), ("neu", "roh", "roh"))
        self.assertTrue((ablage.standard().ordner / neu["anhaenge"][0]["datei"]).exists())   # frisch: Anhang da
        self.assertNotIn("datei", alt["anhaenge"][0])                                        # alt: nur Name
        self.assertFalse(any(m["betreff"] == "Entwurf" for m in ablage.standard().liste(50)))
        self.assertFalse(neu["gelesen"])

    def test_keine_dubletten_und_nur_neue(self):
        asyncio.run(abgleich.durchgang(vorbereiten=0))
        self.assertEqual(asyncio.run(abgleich.durchgang(vorbereiten=0)), 0)
        Attrappe.postfach["INBOX"][1][3] = (False, _mail("Ganz neu", 0))
        self.assertEqual(asyncio.run(abgleich.durchgang(vorbereiten=0, nur_neue=True)), 1)

    def test_vorbereiten_liest_pdf_auch_als_octet_stream(self):
        asyncio.run(abgleich.durchgang())
        self.assertEqual(self.gemini.await_count, 1)                     # nur die frische Mail
        teile = self.gemini.call_args.args[0]
        self.assertIn("Wir treffen uns am Freitag.", teile[0]["text"])
        self.assertTrue(any(t.get("inlineData", {}).get("mimeType") == "application/pdf" for t in teile))
        self.assertIn("antwort_erwartet", self.gemini.call_args.kwargs["schema"]["properties"])
        self.assertEqual(self._nach_betreff("Überraschung im Kurs")["status"], "verarbeitet")

    def test_alte_mail_wird_beim_oeffnen_vorbereitet(self):
        asyncio.run(abgleich.durchgang(vorbereiten=0))
        alt = self._nach_betreff("Alte Info")
        text = asyncio.run(post.mail(alt["id"]))
        self.assertIn("Zusammenfassung: Der Kurs trifft sich am Freitag.", text)
        self.assertIn("Zu tun: Plan lesen", text)
        alt = ablage.standard().eintrag(alt["id"])
        self.assertTrue((ablage.standard().ordner / alt["anhaenge"][0]["datei"]).exists())   # nachgeladen

    def test_suche_ohne_umlaute_und_liste(self):
        asyncio.run(abgleich.durchgang())
        text = asyncio.run(post.mails(suche="uberraschung"))
        self.assertIn("„Überraschung im Kurs“ (ungelesen) [1 Anhang/Anhänge] — Treffen am Freitag. WICHTIG.", text)
        self.assertIn("von Frau Muster", text)
        self.assertNotIn("Alte Info", text)
        self.assertEqual(asyncio.run(post.mails(suche="Zirkus")), "Dazu finde ich keine Mails.")

    def test_suchausdruck_ist_entschaerft(self):
        self.assertEqual(ablage.suchausdruck('Kurs" OR x*'), '"Kurs"* "OR"* "x"*')

    def test_mail_termine(self):
        asyncio.run(abgleich.durchgang())
        self.assertIn("Termin — Kurstreffen (aus „Überraschung im Kurs“", asyncio.run(post.mail_termine()))

    def test_html_mail_wird_text(self):
        teile = abgleich.mail_zerlegen(_mail("H", 1, "Liebe Klasse", html=True))
        self.assertEqual(teile["text"], "Liebe Klasse\nGruß")

    def test_ausgeschaltet_unsichtbar_und_ohne_abgleich(self):
        konnektoren.setzen("iserv_mail", False)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("mails", namen)
        asyncio.run(abgleich.auffrischen())
        self.assertEqual(ablage.standard().liste(50), [])

    def test_nutzt_die_anmeldung_vom_schulmanager(self):
        self.assertEqual(konnektoren.zugang("iserv_mail"), ("eike", "geheim"))
        k = next(k for k in konnektoren.uebersicht() if k["id"] == "iserv_mail")
        self.assertEqual((k["zugangVon"], k["benutzer"]), ("Schulmanager", "eike"))


class FremdeInhalte(unittest.TestCase):
    """Nach dem Lesen einer Mail darf 'merken' nicht mehr direkt schreiben."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "x"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_werkzeuge_mit_fremdem_text(self):
        self.assertTrue(katalog.liefert_fremdes("mail"))
        self.assertTrue(katalog.liefert_fremdes("elternbrief"))
        self.assertFalse(katalog.liefert_fremdes("uhrzeit"))

    def test_chat_merken_nach_mail_ist_fremd(self):
        from kern.sitzung import chat
        runde1 = {"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "mail", "args": {"nummer": 1}}},
            {"functionCall": {"name": "merken", "args": {"thema": "x", "inhalt": "y"}}}]}}]}
        runde2 = {"candidates": [{"content": {"parts": [{"text": "Erledigt."}]}}]}
        ausfuehren = mock.AsyncMock(return_value={"ergebnis": "ok"})
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(side_effect=[(runde1, "m"), (runde2, "m")])), \
             mock.patch.object(chat.werkzeuge, "ausfuehren", ausfuehren):
            asyncio.run(chat.antworten([{"rolle": "du", "text": "Lies Mail 1 und merk dir was drin steht"}]))
        self.assertFalse(ausfuehren.call_args_list[0].kwargs["fremde_inhalte"])
        self.assertTrue(ausfuehren.call_args_list[1].kwargs["fremde_inhalte"])
