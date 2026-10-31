"""Die Startseite: greift jede benutzte CSS-Klasse auch wirklich?

Zweimal am 20.09.2026 in dieselbe Falle getappt: Eine Klasse ist im Stylesheet
zwar vorhanden, aber nur GESCHACHTELT (`.block .kopfzeile`, `.eintrag .knoepfe`).
Wer sie woanders benutzt, bekommt gar kein Layout — und weil nichts fehlschlägt,
sieht man es erst auf dem Handy. Betroffen waren die neuen Einstellungen und,
schon länger, die Knöpfe unter „Benachrichtigungen" und auf der Freigaben-Seite.

Dieser Test geht `index.html` durch und prüft für jedes Element: Gibt es zu jeder
seiner Klassen mindestens eine Regel, die hier greifen KANN — also eine ohne
Vorfahren, oder eine, deren geforderter Vorfahre wirklich über dem Element steht?
"""

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"

# Klassen, die absichtlich nur Anker fürs Skript sind und nie etwas darstellen.
NUR_ANKER = {"seite", "reiter-inhalt", "wa-zeile", "wa-lesen", "wa-senden", "t"}


def _regeln() -> dict[str, list[list[str]]]:
    """Je Klasse: die Listen von Vorfahren-Klassen, die ihre Regeln verlangen.

    Eine leere Liste heißt „braucht keinen Vorfahren“. Kommentare fliegen vorher
    raus — sonst liest der Parser Kommentartext als Selektor."""
    css = re.sub(r"/\*.*?\*/", " ", (WEB / "app.css").read_text(encoding="utf-8"), flags=re.S)
    raus: dict[str, list[list[str]]] = {}
    for kopf in re.findall(r"([^{}]+)\{", css):
        for sel in " ".join(kopf.split()).split(","):
            sel = sel.strip()
            if not sel or sel.startswith("@"):
                continue
            teile = sel.replace(" > ", " ").replace(" + ", " ").split()
            for i, teil in enumerate(teile):
                for k in re.findall(r"\.([\w-]+)", teil):
                    vorfahren = [v for t in teile[:i] for v in re.findall(r"\.([\w-]+)", t)]
                    raus.setdefault(k, []).append(vorfahren)
    return raus


class _Sammler(HTMLParser):
    """Jedes Element mit seinen Klassen und den Klassen seiner Vorfahren."""

    def __init__(self):
        super().__init__()
        self.stapel: list[set[str]] = []
        self.funde: list[tuple[str, str, set[str]]] = []   # (tag, klasse, vorfahren)

    def handle_starttag(self, tag, attrs):
        klassen = set((dict(attrs).get("class") or "").split())
        vorfahren = set().union(*self.stapel) if self.stapel else set()
        for k in klassen:
            self.funde.append((tag, k, vorfahren | klassen))
        if tag not in ("br", "img", "input", "meta", "link", "hr"):
            self.stapel.append(klassen)

    def handle_endtag(self, tag):
        if self.stapel and tag not in ("br", "img", "input", "meta", "link", "hr"):
            self.stapel.pop()


class Stylesheet(unittest.TestCase):
    def test_jede_klasse_greift_dort_wo_sie_steht(self):
        regeln = _regeln()
        sammler = _Sammler()
        sammler.feed((WEB / "index.html").read_text(encoding="utf-8"))
        for tag, klasse, ueber in sammler.funde:
            if klasse in NUR_ANKER or klasse not in regeln:
                continue
            passt = any(set(v) <= ueber for v in regeln[klasse])
            with self.subTest(f"<{tag} class={klasse}>"):
                self.assertTrue(passt, f".{klasse} ist nur geschachtelt definiert "
                                       f"({regeln[klasse][:3]}) und greift hier nicht.")

    def test_jede_benutzte_klasse_ist_bekannt(self):
        """Eine Klasse ohne jede Regel ist entweder ein Tippfehler oder ein Anker —
        und dann gehört sie ausdrücklich in NUR_ANKER, nicht stillschweigend ins Nichts."""
        regeln = _regeln()
        sammler = _Sammler()
        sammler.feed((WEB / "index.html").read_text(encoding="utf-8"))
        for tag, klasse, _ in sammler.funde:
            with self.subTest(f"<{tag} class={klasse}>"):
                self.assertTrue(klasse in regeln or klasse in NUR_ANKER, klasse)



class Aufbau(unittest.TestCase):
    """Die Übersicht ersetzt „Sprechen“: Leiste, Alias und die Bedienelemente, die nichts verlieren dürfen."""

    @classmethod
    def setUpClass(cls):
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.app = (WEB / "app.js").read_text(encoding="utf-8")

    def _ids(self):
        return set(re.findall(r'\bid="([^"]+)"', self.html))

    def test_leiste_hat_sechs_punkte_uebersicht_zuerst(self):
        leiste = re.search(r'<nav class="leiste">(.*?)</nav>', self.html, re.S).group(1)
        ziele = re.findall(r'href="#([a-z]+)"', leiste)
        self.assertEqual(ziele, ["uebersicht", "chat", "gedaechtnis", "schule", "studio", "einstellungen"])

    def test_sprechen_seite_ist_weg_und_alias_bleibt(self):
        self.assertNotIn('<section id="sprechen"', self.html)
        self.assertIn("uebersicht", re.search(r"const SEITEN = \[(.*?)\]", self.app).group(1))
        self.assertIn("sprechen", self.app)   # Alias in zeige()

    def test_alle_bedienelemente_der_alten_sprechen_seite_haben_ein_neues_zuhause(self):
        ids = self._ids()
        for i in ("kreis", "gespraech-status", "oeffnen-hier", "halten", "ptt",
                  "chat-verlauf", "chat-form", "chat-text", "chat-bild-auf", "chat-bild-datei",
                  "chat-neu", "chat-live", "chat-live-ende", "uebersicht", "karten", "live-zeile"):
            with self.subTest(i):
                self.assertIn(i, ids)

    def test_alte_ids_sind_weg(self):
        ids = self._ids()
        for i in ("orb", "orb-text", "sprechen-status", "mehr-auf", "transkript-auf", "verlauf",
                  "tippen", "tippen-text", "bild-auf", "bild-datei"):
            with self.subTest(i):
                self.assertNotIn(i, ids)

    def test_kreis_ist_ein_beschrifteter_button(self):
        m = re.search(r'<button[^>]*id="kreis"[^>]*>', self.html)
        self.assertIsNotNone(m)
        self.assertIn("aria-label", m.group(0))
        self.assertIn('type="button"', m.group(0))

    def test_jedes_skript_gibt_es_als_datei(self):
        for src in re.findall(r'<script src="/([^"]+)"', self.html):
            with self.subTest(src):
                self.assertTrue((WEB / src).exists(), src)



    def test_uebersicht_js_hat_zeichner_fuer_jede_kartenart_und_faellt_zurueck(self):
        js = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        for art in ("meldung", "friday", "schule", "musik", "leer", "_allgemein"):
            with self.subTest(art):
                self.assertRegex(js, rf"ZEICHNER\.{art}\s*=|ZEICHNER\['{art}'\]\s*=|\b{art}\s*:\s*\(")
        self.assertIn("ZEICHNER._allgemein", js)   # unbekannte Art → schlichte Karte statt keiner

    def test_uebersicht_js_setzt_nie_innerhtml(self):
        js = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)



    def test_sprechen_js_kennt_keine_alten_ids_mehr_und_bietet_den_bus(self):
        js = (WEB / "sprechen.js").read_text(encoding="utf-8")
        for alt in ("'orb'", "'orb-text'", "'sprechen-status'", "'mehr-auf'", "'transkript-auf'",
                    "'verlauf'", "'tippen'", "'tippen-text'", "'bild-auf'", "'bild-datei'", "new Verlauf"):
            with self.subTest(alt):
                self.assertNotIn(alt, js)
        for neu in ("window.Friday.live", "'kreis'", "'gespraech-status'", "sendenText", "sendenBild", "auf("):
            with self.subTest(neu):
                self.assertIn(neu, js)

    def test_sprechen_js_behaelt_die_hard_won_audio_regeln(self):
        js = (WEB / "sprechen.js").read_text(encoding="utf-8")
        for regel in ("NACHLAUF_S", "ptt_end", "mikroParken", "mikroHolen", "MIKRO_BEHALTEN_S",
                      "neuAbtasten", "audioWorklet.addModule", "/ws/live"):
            with self.subTest(regel):
                self.assertIn(regel, js)



    def test_chat_js_haengt_am_live_bus_und_schneidet_das_live_flag_vor_dem_senden_ab(self):
        js = (WEB / "chat.js").read_text(encoding="utf-8")
        for teil in ("Friday.live", "sendenText", "sendenBild", "live.auf('text'", "live.auf('ende'",
                     "oeffnen-hier", "uebersichtNeu", "dashboard_karte", "verbindet sich noch"):
            with self.subTest(teil):
                self.assertIn(teil, js)
        # Das Flag `live` ist nur für die Anzeige; an den Server geht nur die neue Nachricht (rolle/text/bild), den Verlauf kennt der Strang.
        self.assertRegex(js, r"nachrichten:\s*\[n\],\s*strang:\s*true")

    def test_chat_js_hat_die_alte_chat_logik_behalten(self):
        js = (WEB / "chat.js").read_text(encoding="utf-8")
        for teil in ("/api/chat", "bildVerkleinern", "sessionStorage", "Friday.kennung", "'pc'"):
            with self.subTest(teil):
                self.assertIn(teil, js)

    def test_kreis_im_kopf_ist_mittig(self):
        # ::before erbt box-sizing nicht von `*` — mit content-box war der Ring 4 px breiter als
        # gerechnet und saß 2 px neben der Mitte (Eike, 24.09.2026).
        css = (WEB / "app.css").read_text(encoding="utf-8")
        regel = re.search(r"\.reaktor::before\s*\{(.*?)\}", css, re.S).group(1)
        self.assertIn("box-sizing: border-box", regel)
        self.assertRegex(regel, r"inset:\s*0")
        self.assertRegex(regel, r"margin:\s*auto")

    def test_debug_menue_fuer_die_uebersicht_in_den_einstellungen(self):
        einst = re.search(r'<section id="einstellungen".*?</section>', self.html, re.S).group(0)
        self.assertIn('id="d-menue"', einst)
        for v in ("unterricht", "pause", "freistunde", "vor_schule", "vertretung", "schultag", "aus"):
            with self.subTest(v):
                self.assertIn(f'data-vorschau="{v}"', einst)
        self.assertIn('id="ueb-vorschau"', self.html)

    def test_musikkarte_hat_drei_knoepfe_und_oeffnet_die_spotify_app(self):
        js = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        for teil in ("'vorheriger'", "'naechster'", "knopf('zurueck'", "spotify:", "glas-menue", "role', 'menu'"):
            with self.subTest(teil):
                self.assertIn(teil, js)
        # Die Adresse muss die strenge Form prüfen, bevor sie zu einem Link wird.
        self.assertRegex(js, r"\^spotify:\(track\|episode")

    def test_vorschau_schreibt_keine_daten(self):
        js = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        self.assertIn("vorschau-", js)               # Kennungen der Testkarten
        self.assertIn("startsWith('vorschau-')", js)  # … werden nie als „gesehen“ an den Server gemeldet

    def test_schul_und_stundenplan_karte_oeffnen_ein_glas_menue_zum_schulmanager(self):
        js = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        self.assertIn("ZEICHNER.stundenplan =", js)
        self.assertIn("ZEICHNER.schule =", js)
        self.assertIn("schulmanagerMenue", js)                       # gemeinsamer Weg für beide Karten
        self.assertIn("Schulmanager öffnen", js)
        # Die App startet über einen Kurzbefehl (kein bekanntes URL-Schema), die Webseite bleibt als zweiter Eintrag.
        self.assertIn("shortcuts://run-shortcut?name=Schulmanager", js)
        self.assertIn("https://login.schulmanager-online.de/", js)
        self.assertIn('data-vorschau="stundenplan"', self.html)

    def test_pc_und_jellyfin_karte(self):
        js = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        css = (WEB / "app.css").read_text(encoding="utf-8")
        self.assertIn("ZEICHNER.pc =", js)
        self.assertIn("ZEICHNER.jellyfin =", js)
        # PC: Heikles nur über die Rückfrage-Routen, nie direkt
        for pfad in ("/api/pc/schnell", "/api/pc/herunterfahren", "/api/pc/energiesparen", "/api/pc/offen/"):
            self.assertIn(pfad, js)
        # Jellyfin: das Bild nur von der eigenen Route, streng geprüft
        self.assertIn("api\\/jellyfin\\/bild\\/[0-9a-f]{32}", js)
        self.assertIn("/api/dashboard/jellyfin/", js)
        self.assertIn("helfer_neu", js)
        self.assertIn(".karte.jellyfin", css)
        self.assertIn(".karte.pc", css)
        for name in ("pc", "jellyfin"):
            self.assertIn(f'data-vorschau="{name}"', self.html)
            self.assertIn(f"{name}: [", js)                 # Vorschau-Eintrag

    def test_spotify_und_jellyfin_tragen_eine_herkunftsmarke(self):
        js = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        css = (WEB / "app.css").read_text(encoding="utf-8")
        self.assertIn("quellenmarke('spotify', 'Spotify')", js)
        self.assertIn("logo: 'jellyfin', marke: () => 'Jellyfin'", js)
        self.assertIn("ZEICHNER.pcmedien =", js)
        self.assertIn("/api/dashboard/pcmedien/", js)
        self.assertIn('data-vorschau="pcmedien"', self.html)
        self.assertIn("quellenmarke('windows', 'Windows')", js)
        self.assertIn(".karte.pc > .quelle", css)

    def test_pc_seite_hat_alle_bereiche_und_nutzt_nur_erlaubte_wege(self):
        js = (WEB / "pcseite.js").read_text(encoding="utf-8")
        pc = (WEB / "pc.js").read_text(encoding="utf-8")
        self.assertIn('<script src="/pcseite.js"></script>', self.html)
        self.assertLess(self.html.index('/pc.js'), self.html.index('/pcseite.js'))    # pcseite.js braucht window.FridayPc
        for reiter in ("uebersicht", "wiedergabe", "fenster", "dateien", "bildschirm", "senden", "verlauf"):
            self.assertIn(f"'{reiter}'", js)
        # nur die Routen der Seite; heikles nie direkt, nie „bestaetigt“
        for weg in ("/api/pc/lesen/", "/api/pc/tun", "/api/pc/bildschirm", "/api/pc/status"):
            self.assertIn(weg, js)
        self.assertNotRegex(js, r"bestaetigt\s*:\s*true")
        self.assertNotIn("/api/pc/schnell", js)
        # Fenstertitel, Dateinamen, Prozessnamen sind fremder Text: nie als HTML
        for datei in (js, pc):
            self.assertNotIn("innerHTML", datei)
            self.assertNotIn("insertAdjacentHTML", datei)
        for art in ("fenster_wechseln", "fenster_schliessen", "starten", "prozess_beenden", "oeffnen", "umbenennen",
                    "verschieben", "kopieren", "loeschen", "ordner_anlegen", "meldung", "webseite", "zwischenablage", "medien"):
            self.assertIn(f"'{art}'", js)
        self.assertIn("window.FridayPcSeite", js)
        self.assertIn("FridayPcSeite", pc)              # pc.js übergibt das Zeichnen an die neue Seite

    def test_bilder_im_gespraech_nur_von_fridays_eigenen_routen(self):
        app_js = (WEB / "app.js").read_text(encoding="utf-8")
        chat = (WEB / "chat.js").read_text(encoding="utf-8")
        sprechen = (WEB / "sprechen.js").read_text(encoding="utf-8")
        ueb = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        self.assertIn("bilder(liste)", app_js)
        self.assertIn("netzbild", app_js)                        # die strenge Adressprüfung
        self.assertNotIn("innerHTML", app_js.split("bilder(liste)")[1].split("link(url")[0])
        self.assertIn("a.typ === 'bild'", chat)                  # Chat: Antwort mit Aktionen
        self.assertIn("live.auf('bild'", chat)                   # Sprache: Live-Nachricht
        self.assertIn("case 'bild'", sprechen)
        self.assertIn("api\\/netzbild\\/[0-9a-f]{16}", ueb)       # auch auf Karten der Übersicht
        self.assertIn(".zeile.bilder", (WEB / "app.css").read_text(encoding="utf-8"))

    def test_bildkarte_und_knopf_im_gespraech(self):
        ueb = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        app_js = (WEB / "app.js").read_text(encoding="utf-8")
        css = (WEB / "app.css").read_text(encoding="utf-8")
        self.assertIn("ZEICHNER.bild =", ueb)
        self.assertIn("bild: [", ueb)                                # Vorschau
        self.assertIn('data-vorschau="bild"', self.html)
        self.assertIn(".bildkarte", css)
        self.assertIn("/api/dashboard/bild", app_js)                 # „Auf die Übersicht“ unter dem Bild im Gespräch
        self.assertNotIn("innerHTML", ueb.split("ZEICHNER.bild =")[1].split("ZEICHNER.")[0])

    def test_wissen_seite_hat_themen_und_antwort(self):
        # Wissen-Seite (05.10.2026): Themen-Karten nach Wichtigkeit, Antwort mit Belegen statt Rohfakten.
        js = (WEB / "gedaechtnis.js").read_text(encoding="utf-8")
        css = (WEB / "app.css").read_text(encoding="utf-8")
        for kennung in ('id="w-abschnitte"', 'id="w-seite"', 'id="w-du"', 'id="w-antwort"', 'id="w-lead"', 'id="w-belege"',
                        'id="w-zurueck"', 'id="g-graph-frage"'):
            self.assertIn(kennung, self.html)
        self.assertNotIn('id="g-graph"', self.html)                 # die alte Rohfakten-Liste gibt es nicht mehr
        self.assertIn("/api/gedaechtnis/themen", js)
        self.assertIn("/api/gedaechtnis/antwort", js)
        self.assertIn("/api/gedaechtnis/seite/", js)
        self.assertIn("?modell=${modell}", js)                                # erst sofort, dann die ausgearbeitete Fassung
        self.assertIn("/api/gedaechtnis/treffer", js)                # eindeutige Suche öffnet gleich die Seite
        self.assertIn("'Bearbeiten'", js)                            # Eikes Angaben direkt auf der Seite
        self.assertIn("/notiz", js)
        self.assertIn("wartet auf Verarbeitung", js)                 # Stand der Angabe: kommt sie im Graphen an?                # antippbar bis zur Seite der Person oder des Ortes
        self.assertNotIn("innerHTML", js)
        for klasse in (".kachel", ".zeile", ".gruppenliste", ".seitenkopf", ".lead", ".beleg"):
            self.assertIn(klasse, css)
        self.assertIn("prefers-reduced-motion", css)                # Bewegung nur, wenn gewünscht

    def test_fokus_personen_oberflaeche(self):
        # Fokus-Personen (05.10.2026): „Im Fokus“ ganz oben, Stern und „Neu“ auf der Personenseite.
        js = (WEB / "gedaechtnis.js").read_text(encoding="utf-8")
        css = (WEB / "app.css").read_text(encoding="utf-8")
        self.assertIn('id="w-fokus"', self.html)
        self.assertLess(self.html.index('id="w-fokus"'), self.html.index('id="w-du"'))     # ganz oben, vor „Über dich“
        for text in ("/api/fokus", "Im Fokus", "Jetzt nachsehen", "nichts Neues", "method: 'PUT'", "Unklare Absender", "/zuordnung"):
            self.assertIn(text, js)
        self.assertNotIn("innerHTML", js)
        for klasse in (".stern", ".neu-eintrag", ".fokus-zeile"):
            self.assertIn(klasse, css)

    def test_wissen_antippen_fuehrt_zur_hauptseite(self):
        # Eike (06.10.2026): tief in Unterseiten auf „Wissen“ tippen soll zur Hauptseite zurückführen.
        js = (WEB / "gedaechtnis.js").read_text(encoding="utf-8")
        handler = js.split("/* ---------- Reiter ---------- */")[1].split("/* ---------- Vorschläge")[0]
        self.assertIn("if (reiter === 'wissen') { zuDenThemen();", handler)
        self.assertIn('nav a[href="#gedaechtnis"]', handler)               # auch der Eintrag in der unteren Leiste
        self.assertIn('<a href="#gedaechtnis">', self.html)

    def test_bild_entfernen_nur_mit_zweitem_tipp(self):
        js = (WEB / "gedaechtnis.js").read_text(encoding="utf-8")
        self.assertIn("method: 'DELETE'", js)
        self.assertIn("Endgültig entfernen", js)                     # erst fragen, dann löschen
        self.assertIn("Abbrechen", js)
        self.assertNotIn("innerHTML", js)

    def test_verbindungskarte_oeffnet_google_maps_nur_fuer_fuss_und_auto(self):
        js = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        i = js.index("ZEICHNER.route =")
        block = js[i:js.index("ZEICHNER.schule =", i)] if "ZEICHNER.schule =" in js[i:] else js[i:i + 9000]
        self.assertIn("comgooglemaps://", js)                              # direkt in die App
        self.assertIn("https://www.google.com/maps/dir/?api=1", js)        # sonst Browser
        self.assertIn("walking", js)
        self.assertIn("driving", js)
        self.assertNotIn("transit", js)                                    # Bus/Bahn: kein Google-Link (GVH ist maßgeblich)
        self.assertIn("koordOk", js)                                       # Adressen nur aus geprüften Zahlen
        self.assertNotIn("innerHTML", block)
        self.assertIn("route: [", js)                                      # Vorschau
        self.assertIn('data-vorschau="route"', self.html)
        self.assertIn(".karte.route", (WEB / "app.css").read_text(encoding="utf-8"))
        self.assertIn("verbindung_karte", (WEB / "chat.js").read_text(encoding="utf-8"))

    def test_verbindungskarte_ist_live(self):
        js = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        css = (WEB / "app.css").read_text(encoding="utf-8")
        self.assertIn("Live · vor", js)
        self.assertIn("k.stand", js)                               # Zeitpunkt der letzten Rechnung, nicht der Erstellung
        self.assertIn("Du bist am Ziel", js)
        self.assertIn("Nicht aktuell", js)
        self.assertIn("e.z.art === 'route'", js)                   # öfter abfragen, solange eine Verbindungskarte unterwegs ist
        self.assertIn(".route-live", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_pc_karte_sendet_in_der_vorschau_nichts(self):
        js = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        i = js.index("ZEICHNER.pc =")
        self.assertGreaterEqual(js[i:i + 6000].count("k.vorschau"), 2)   # Aktionsknöpfe und Rückfrage

    def test_glas_menue_beginnt_mit_spotify_oeffnen(self):
        js = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        i = js.index("function eintraege()")
        block = js[i:i + 900]
        self.assertLess(block.index("Spotify öffnen"), block.index("k.uri"), "‚Spotify öffnen‘ steht ganz oben")
        self.assertIn("spotify://", js)


if __name__ == "__main__":
    unittest.main()
