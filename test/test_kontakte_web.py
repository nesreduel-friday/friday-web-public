"""Kontakte-Seite: nur textContent, keine Inline-Skripte, Reiter und Bereich stehen in der Startseite."""
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


class Kontakte(unittest.TestCase):
    def test_seite_baut_alles_per_textcontent(self):
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        for verboten in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(verboten, js)

    def test_keine_wirkungslosen_schalter_mehr(self):
        # 04.10.2026, Eike: proaktiv wirkte nie (raus); Empfänger-Merkung senden_erlaubt abgeschaltet (auto nach Ja).
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        self.assertNotIn("von sich aus schreiben", js)
        self.assertNotIn("anschreiben", js.lower())

    def test_hebel_sind_verdrahtet(self):
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        for stueck in ("modulBlock", "flammenBlock", "flammeEikeKarte", "/api/flamme-eike", "grundBlock", "'flammen'", "'snap_wuensche'", "'medien'", "module:", "garantie_profil", "module_verfuegbar"):
            self.assertIn(stueck, js)

    def test_garantie_tab_zeigt_die_bestaetigung_durch_die_person(self):
        js = (WEB / "garantie.js").read_text(encoding="utf-8")
        self.assertIn("pruefung_quelle", js)
        self.assertIn("von der Person bestätigt", js)

    def test_anfragen_sind_verdrahtet(self):
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        self.assertIn("/api/kontakte/anfragen/zurueckziehen", js)
        self.assertIn("Zurückziehen", js)
        self.assertIn("Anlegen", js)

    def test_startseite_hat_reiter_bereich_und_skript(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-reiter="kontakte"', html)
        self.assertIn('id="r-kontakte"', html)
        self.assertIn('id="kontakte-inhalt"', html)
        self.assertIn('<script src="/kontakte.js"></script>', html)
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html), [])  # keine Inline-Skripte

    def test_abschnitte_trennen_snapchat_pausiert(self):
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        self.assertIn("ko-titel", js)
        self.assertIn("Kontakte (", js)
        self.assertIn("Pausiert", js)

    def test_freigabe_textarea_ist_vollbreit(self):
        css = (WEB / "app.css").read_text(encoding="utf-8")
        self.assertIn(".ko-freigaben textarea", css)

    def test_status_austausch_rechte_und_arbeitsweisen_sind_da(self):
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        for stueck in ("ko-pille", "austauschText", "profilBlock", "Am Handy prüfen", "grundBlock", "arbeitsweisenBlock", "ko-schalter"):
            self.assertIn(stueck, js)
        self.assertNotIn("noch nie geschrieben", js)   # senden_erlaubt gilt seit 04.10.2026 nicht mehr

    def test_alle_karten_lassen_sich_aufklappen(self):
        # Früher konnte man pausierte Personen nicht öffnen und damit nicht wieder aktivieren.
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        self.assertNotIn("personKarte(p, true)", js)

    def test_suche_filter_und_ungespeichert_hinweis(self):
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        for stueck in ("suchfeld", "ko-filterchip", "Kann schreiben", "Ungespeichert", "entwurf["):
            self.assertIn(stueck, js)


if __name__ == "__main__":
    unittest.main()

    def test_termin_karte_kann_annehmen(self):
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        self.assertIn("/api/kontakte/rueckfragen/${r.id}/annehmen", js)
        self.assertIn("Annehmen & eintragen", js)

    def test_werkzeuge_als_dropdown_mit_suche(self):
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        self.assertIn("Hinzufügen", js)
        self.assertIn("Werkzeug suchen", js)
        self.assertIn("ko-x", js)

    def test_controls_kennen_das_dark_theme(self):
        css = (WEB / "app.css").read_text(encoding="utf-8")
        self.assertIn("color-scheme: dark", css)
        self.assertIn("#kontakte-inhalt button", css)
        self.assertIn("button.primaer", css)

    def test_abschnitte_sind_beschriftet(self):
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        self.assertIn("Snapchat-Kontakte", js)
        self.assertIn("Zu entscheiden", js)

    def test_recht_reihe_ist_beschriftet(self):
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        self.assertIn("Weiteres Recht geben", js)
        self.assertIn("ko-reihe", js)

    def test_zusammenfuehren_ist_verdrahtet(self):
        js = (WEB / "kontakte.js").read_text(encoding="utf-8")
        self.assertIn("Zusammenführen", js)
        self.assertIn("/api/kontakte/${v.pid}/verknuepfen", js)
        self.assertIn("/entknuepfen", js)
