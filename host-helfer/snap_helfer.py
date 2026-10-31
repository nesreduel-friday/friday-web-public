"""snap-helfer — läuft auf hphome selbst, steuert das per USB angeschlossene
Samsung-Handy über `adb` und tauscht sich über Dateien in data/snapchat/ mit
Friday aus (kein HTTP, kein Root, kein uiautomator2 — nur adb, Standardbibliothek
und `adb shell uiautomator dump`, das Android selbst mitbringt).

Kein Emulator: Snapchats Play-Integrity-Prüfung blockt emulierte Umgebungen.
Kein Root: eine virtuelle Kamera bräuchte Root und würde dasselbe Sperrrisiko
zurückbringen, das der Wechsel zum echten Gerät gerade vermeidet.

Die `AUSWAHL`-Tabelle ist am 28.09.2026 gegen das echte Gerät kalibriert
worden (Samsung A52, Snapchat-App-Sprache Englisch — nicht Deutsch, wie
zuerst angenommen). Die meisten Bedienelemente haben KEINE eigene
resource-id (Snapchat verschleiert sie als `0_resource_name_obfuscated`);
genutzt werden nur die wenigen mit echter id (`ngs_memories_icon`,
`grid_frameable_container`, `send_btn`, `chat_input_text_field`, `caption`)
oder eindeutiger Text (`"Camera Roll"`, `"Chat"`, der Anzeigename des
Kontakts). Chat-Nachrichten senden sich nicht über einen Knopf, sondern
über die Enter-Taste der Tastatur (`chat_senden()`, `KEYCODE_ENTER`).

**Wichtig, live widerlegt (28.09.2026):** `Memories → Camera Roll → Foto
antippen → Send to` sendet TROTZ „Sent"-Banner als CHAT-BILD, nicht als
echten Snap (von Eike am Gerät bestätigt) — die Annahme aus der Spec war
falsch. Der echte Weg zu einem Snap ist Snapchats „Director Mode"
(verstecktes Menü hinter „More" im Kamera-Bildschirm) → „Import Media" →
Foto wählen (wird dabei zu einem 5-Sekunden-Clip) → „Preview" → „Send to"
→ Empfänger wählen → „Send".

**Root Cause gefunden und live behoben (28.09.2026, zweiter Durchlauf):**
Der „Preview"-Knopf hat nie geklemmt — Director Mode, der Camera-Roll-
Picker und der Editor danach (Preview/Send to) sind komplett eigen
gerendert (Compose/GL-Oberfläche ohne Accessibility-Semantik). Ein
frischer `uiautomator dump` liefert dort für diese Knöpfe schlicht GAR
NICHTS (leere Trefferliste) oder schlägt ganz mit „could not get idle
state" fehl (die Live-Kamera-Vorschau animiert ständig) — `element_finden`
konnte den Knopf also grundsätzlich nie finden, unabhängig von den
Koordinaten. Die frühere manuelle Kalibrierung hat versucht, die
Koordinaten per Auge aus einem skalierten Screenshot zu schätzen — das war
ungenau genug, dass der Tipp konsequent daneben ging (öffnete stattdessen
das automatisch eingeblendete Lens-Karussell). Mit pixelgenau aus dem
Screenshot ermittelten Koordinaten hat ein echter Snap-Versand funktioniert
(„Snap Sent!"-Banner bestätigt, an Eike zugestellt).

Für diese drei nicht-zugänglichen Bildschirme (Director-Mode-Kamera,
Camera-Roll-Picker, Editor mit Preview/Send-to) gibt es deshalb ZWEI
Ersatzwege statt `element_finden`:

1. **Farbsuche im Screenshot** (`farbe_finden`, `_gelben_knopf_tippen`) für
   „Preview" und „Send to" — beide sind Snapchats markantes Gelb und lassen
   sich damit auch bei leichten Layout-Verschiebungen finden. Kein Treffer
   → sauberer `SnapHelferFehler`, kein Blindtipp (gleiches Prinzip wie
   `element_finden`/`schritt_ausfuehren`).
2. **Fest kalibrierte Koordinaten** (`BLINDE_KOORDINATEN`, `_blind_tippen`)
   für die drei Stellen, die weder Text/Resource-ID noch eine eindeutige
   Farbe haben (Import-Media-Symbol, oberste-linke Kachel im Camera-Roll-
   Grid, Bestätigungs-Häkchen danach) — als Anteil der Bildschirmgröße
   (`bildschirmgroesse()`, `adb shell wm size`), nicht als absolute Pixel.
   Das ist der einzige Punkt im ganzen Modul, der bei einem Snapchat-Update
   still falsch tippen statt sauber abbrechen kann — deshalb so klein wie
   möglich gehalten (nur 3 Stellen) und hier ausdrücklich dokumentiert.

Ändert sich Snapchats UI oder die App-Sprache, bricht `element_finden`
bzw. `farbe_finden` sauber mit `SnapHelferFehler` ab statt blind auf
falsche Koordinaten zu tippen — dann hier neu kalibrieren (Screenshot +
`uiautomator dump`). Nur `BLINDE_KOORDINATEN` selbst hat diese Absicherung
nicht und muss bei Verdacht auf ein UI-Update manuell geprüft werden
(siehe oben).

**Benachrichtigungs-Auslöser (28.09.2026):** `neue_benachrichtigung_da()`
prüft günstig per `dumpsys notification`, ob eine neue Snapchat-Nachricht
da ist (Kanal „Snaps and Chats"), ohne die App zu öffnen — Snapchat zeigt
den Nachrichtentext dort aus Datenschutzgründen nie, nur DASS etwas da ist.
Dient als schneller Auslöser für `einmal()` (Eikes Wunsch), der volle,
träge Takt (`TAKT_S`) bleibt als Sicherheitsnetz.

**Bildunterschrift live korrigiert (28.09.2026, dritter Durchlauf):** Der
Text-Werkzeug-Knopf ("T") sitzt auf dem Editor NACH "Preview", nicht auf
dem Director-Mode-Kamerabildschirm davor — die ursprüngliche Annahme
(`bildunterschrift_feld` mit geratener Resource-ID `caption`) war falsch
und ist entfernt. Live bestätigt: "T" antippen → Text eingeben → an
beliebiger freier Stelle antippen platziert/bestätigt ihn. **Offen:** Die
Samsung-Tastatur-Autokorrektur verfälscht weiterhin über `adb shell input
text` getippten Text (aus "Kalibrierungstest" wurde "Kalbrierustes",
gleiches Muster wie beim Chat-Text) — Eike muss sie in den
Tastatur-Einstellungen abschalten, bevor Bildunterschriften unbeaufsichtigt
laufen. Dabei außerdem entdeckt: ein hängengebliebener Entwurf übersteht
`app_zuruecksetzen()` — siehe `_entwurf_aufraeumen()`, dort auch der noch
offene zweite Dialog-Fall. Vollständig getestet und bestätigt: Chat-Text
senden/empfangen, Bild-Snap mit UND ohne Bildunterschrift senden (echter
Snap, kein Chat-Bild).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import struct
import sys
import subprocess
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path


class SnapHelferFehler(RuntimeError):
    """Vorlesbar: sagt, was los ist."""


class SendenUnbestaetigt(RuntimeError):
    """„Senden“ ist gedrückt, die Nachricht aber nicht im Chat zu sehen (Review 07.10.2026). Bewusst KEIN `SnapHelferFehler`: Ein
    Neuversuch (`mit_neuversuch`, `MAX_VERSUCHE`) würde dieselbe Nachricht bis zu 8-mal tippen. Lieber einmal „unbestätigt“."""


class AuftragVerworfen(SnapHelferFehler):
    """Ein Auftrag wurde absichtlich verworfen (kein/unbekannter Empfänger, Person passt nicht): kein Neuversuch, und der
    schon nachgerückte nächste Auftrag bleibt unangetastet."""


def daten_ordner() -> Path:
    return Path(os.getenv("FRIDAY_DATEN", "") or (Path(__file__).resolve().parent.parent / "data"))


_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


ZUFALL_ANTEIL = 0.15     # höchstens so viel der halben Breite/Höhe, die ein Tipp von der Mitte abweichen darf
ZUFALL_MAX_PX = 8        # und nie mehr als so viele Pixel (07.10.2026, Spec „schnell“, Punkt 4: sparsamer Zufall)


def zufallspunkt(box: tuple[int, int, int, int], zufall=None) -> tuple[int, int]:
    """Ein Punkt nahe der Mitte des Elements, nie außerhalb davon. Mit `ZUFALL_MAX_PX = 0` genau die Mitte."""
    zufall = zufall or random
    x1, x2 = sorted((box[0], box[2]))
    y1, y2 = sorted((box[1], box[3]))
    mx, my = (x1 + x2) // 2, (y1 + y2) // 2
    dx = max(0, min(ZUFALL_MAX_PX, int((x2 - x1) / 2 * ZUFALL_ANTEIL)))
    dy = max(0, min(ZUFALL_MAX_PX, int((y2 - y1) / 2 * ZUFALL_ANTEIL)))
    return mx + zufall.randint(-dx, dx), my + zufall.randint(-dy, dy)


def bounds_mitte(bounds: str) -> tuple[int, int] | None:
    """Die exakte Mitte — auch Messwert für Bänder und Sektionen, deshalb ohne Zufall (Review 07.10.2026). Getippt wird über
    `bounds_zufall`."""
    treffer = _BOUNDS_RE.match(bounds or "")
    if not treffer:
        return None
    x1, y1, x2, y2 = (int(g) for g in treffer.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def bounds_zufall(bounds: str) -> tuple[int, int] | None:
    """Ein Tipp-Ziel: Zufallspunkt nahe der Mitte, innerhalb des Elements."""
    treffer = _BOUNDS_RE.match(bounds or "")
    if not treffer:
        return None
    return zufallspunkt(tuple(int(g) for g in treffer.groups()))


def elemente_finden(dump_xml: str, *, text: str | None = None,
                    resource_id: str | None = None, zufall: bool = False) -> list[tuple[int, int]]:
    """Alle Treffer, in Dokumentreihenfolge — z.B. um bei mehreren gleichen Elementen ohne
    eigenen Text (Snapchats Kamera-Modus-Karussell hat fünf `camera_mode_icon_image_view`,
    das letzte/unterste ist „More") gezielt eins per Index auszuwählen."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return []
    treffer = []
    for knoten in wurzel.iter("node"):
        if text is not None and knoten.get("text", "") != text:
            continue
        if resource_id is not None and knoten.get("resource-id", "") != resource_id:
            continue
        mitte = (bounds_zufall if zufall else bounds_mitte)(knoten.get("bounds", ""))
        if mitte is not None:
            treffer.append(mitte)
    return treffer


def element_finden(dump_xml: str, *, text: str | None = None,
                   resource_id: str | None = None, index: int = 0, zufall: bool = False) -> tuple[int, int] | None:
    treffer = elemente_finden(dump_xml, text=text, resource_id=resource_id, zufall=zufall)
    try:
        return treffer[index]
    except IndexError:
        return None


_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def umlaute_ersetzen(text: str) -> str:
    """`adb shell input text` kann keine Umlaute tippen (NullPointerException im Android-Shell,
    29.09.2026: jede Antwort mit „ü“ scheiterte) — deshalb ae/oe/ue/ss."""
    return text.translate(_UMLAUTE)


def adb_text_escapen(text: str) -> str:
    """`adb shell input text` kennt keine Leerzeichen direkt (werden zu %s) und scheitert an
    vielen Sonderzeichen (Shell-Metazeichen, Emoji). Jeder Leerraum — auch Zeilenumbruch und Tab —
    wird zuerst zu einem einzelnen Leerzeichen zusammengefasst: `adb shell` läuft über eine echte
    Shell auf dem Handy, und ein durchgelassener Zeilenumbruch könnte den Befehl vorzeitig beenden
    und den Rest als eigenen Befehl ausführen lassen."""
    einzeilig = re.sub(r"\s+", " ", umlaute_ersetzen(text))
    sicher = re.sub(r"[^A-Za-z0-9 .,-]", "", einzeilig)  # nur ASCII: alles andere lässt `input text` abstürzen
    return sicher.replace(" ", "%s")


# So meldet der Chat-Baum einen Snap statt eines Textes (Symbolname, kein Nachrichtentext) —
# Friday antwortete darauf wie auf eine Textnachricht („Hey Eike! Was geht ab?“, 29.09.2026).
_SYMBOL_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)+$")  # Symbolnamen wie „snap_envelop“, keine Sprache


def _ist_symbol(text: str) -> bool:
    return bool(_SYMBOL_RE.match(text))


# Das Absender-Label ist der Anzeigename in Großbuchstaben: „ME“, „kontakt_c“, aber auch „TORGE STIER“ (bis vier Wörter). Mit nur einem Wort
# las der Helfer bei jedem Kontakt mit Vor- und Nachnamen keinen einzigen Text (Torge, 15.10.2026). Systemzeilen („YOU AND TORGE STARTED
# A STREAK 🔥“, „… SAVED A SNAP IN CHAT!“) haben Emoji, Satzzeichen oder zu viele Wörter und passen weiter nicht.
_ABSENDER_RE = re.compile(r"^[A-ZÄÖÜ]{2,15}( [A-ZÄÖÜ]{1,15}){0,3}$")


def _textknoten(wurzel) -> list[str]:
    """Alle nichtleeren Texte im Baum von oben nach unten — ohne das Eingabefeld: dessen Platzhalter
    („Send chat“) folgt bei einem Bild ohne Text auf das Absender-Label und wurde als Nachricht von
    Eike gelesen (29.09.2026)."""
    return [k.get("text", "") for k in wurzel.iter("node")
            if k.get("text", "").strip() and not (k.get("resource-id") or "").endswith("chat_input_text_field")]


def neue_nachrichten(dump_xml: str, zuletzt_gesehen: str) -> list[str]:
    """Nachrichtentexte aus der Chat-Ansicht, die nach `zuletzt_gesehen` kamen — nur von der
    Gegenseite, nicht Fridays eigene gesendete Zeilen.

    Snapchat vergibt für Nachrichtentexte keine resource-id (Compose-Ansicht, am echten Gerät
    bestätigt). Stattdessen steht direkt vor jedem Nachrichtentext ein kurzes GROSSGESCHRIEBENES
    Absender-Label — "ME" für die eigene Seite, sonst der Anzeigename (z.B. "EIKE"). Das wird hier
    ausgenutzt: je zwei aufeinanderfolgende Textknoten, bei denen der erste wie ein Absender-Label
    aussieht, bilden ein (Absender, Text)-Paar. Der Dump listet von oben (alt) nach unten (neu)."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return []
    knoten = _textknoten(wurzel)
    eintraege = [(knoten[i], knoten[i + 1]) for i in range(len(knoten) - 1)
                if _ABSENDER_RE.match(knoten[i]) and knoten[i + 1].strip() and knoten[i + 1] != "ME"]
    texte = [text for absender, text in eintraege if absender != "ME" and not _ist_symbol(text)]
    if not zuletzt_gesehen:
        return texte
    if zuletzt_gesehen in texte:
        letzter_index = len(texte) - 1 - texte[::-1].index(zuletzt_gesehen)
        return texte[letzter_index + 1:]
    return texte  # zuletzt_gesehen gesetzt, aber nicht im Dump — lieber einmal zu viel als eine Antwort verlieren


# Snapchats eigene Zeile im Chat, solange der andere einen noch nicht hinzugefügt hat (gesehen 03.10.2026 bei Kontakt A):
# „@Kontakt A has not added you as a friend yet. They won’t be able to see your messages until they do.“
# Das ist KEINE Nachricht der Person — und der einzige Beweis, dass eine Anfrage noch nicht angenommen ist.
_EINSEITIG_RE = re.compile(r"has not added you as a friend|hat dich noch nicht (als freund\w* )?hinzugef", re.I)


def einseitig(dump_xml: str) -> bool:
    """Zeigt der offene Chat Snapchats „hat dich noch nicht hinzugefügt“-Zeile? — pure Funktion."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return False
    return any(_EINSEITIG_RE.search(f"{k.get('text', '')} {k.get('content-desc', '')}") for k in wurzel.iter("node"))


_UHRZEIT_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _zitat_text(knoten: list[str], i: int) -> int | None:
    """Antwortet die Gegenseite auf eine Nachricht („Mmm“ auf Fridays Satz), steht nach ihrem Label erst das Zitat — Absender, Uhrzeit, zitierter
    Text — und danach ihr eigener Text (Torge, 15.10.2026: sein „Mmm“ ging verloren). Index des eigenen Textes, sonst None."""
    if i + 4 < len(knoten) and _ABSENDER_RE.match(knoten[i + 1]) and _UHRZEIT_RE.match(knoten[i + 2]) and knoten[i + 4].strip():
        return i + 4
    return None


def fremde_texte_mit_lage(dump_xml: str) -> list[tuple[str, bool]]:
    """Alle Texte der Gegenseite im Chat (ohne „ME“), von alt nach neu — jeweils mit der Lage: Steht der Text ÜBER Fridays letzter eigener
    Nachricht („ME“)? Dann ist er älter als ihre letzte Antwort."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return []
    knoten = _textknoten(wurzel)
    letzte_eigene = max((i + 1 for i in range(len(knoten) - 1)
                         if knoten[i] == "ME" and knoten[i + 1].strip() and knoten[i + 1] != "ME" and not _UHRZEIT_RE.match(knoten[i + 1])),
                        default=-1)
    aus = []
    for i in range(len(knoten) - 1):
        if not _ABSENDER_RE.match(knoten[i]) or knoten[i] == "ME":
            continue
        j = _zitat_text(knoten, i)
        if j is None:
            j = i + 1
        text = knoten[j]
        if text.strip() and not _ist_symbol(text) and text != "ME" and not _EINSEITIG_RE.search(text):
            aus.append((text, j < letzte_eigene))
    return aus


def fremde_texte(dump_xml: str) -> list[str]:
    """Alle Texte der Gegenseite, die gerade im Chat stehen (ohne „ME“), von alt nach neu."""
    return [t for t, _ in fremde_texte_mit_lage(dump_xml)]


VERALTET_S = 24 * 3600     # so lange gilt ein eingelesener Text als „schon gelesen“, wenn er noch im Chat steht


def nicht_veraltet(dump_xml: str, neue: list[str], person: str | None) -> list[str]:
    """Aus `neue` fallen Texte, die über Fridays letzter eigener Nachricht stehen UND schon eingelesen wurden (gleicher Text, gleiche
    Person, letzte 24 h): Snapchat hat sie nicht gelöscht, sie sind nur noch sichtbar — sonst läse der Helfer bei jedem Blick dasselbe „Ja“
    und Friday antwortete jedes Mal (Vorfall 14./15.10.2026). Was noch nie eingelesen wurde, bleibt auch über der Antwort neu."""
    lagen = fremde_texte_mit_lage(dump_xml)
    texte = [t for t, _ in lagen]
    if not neue or len(neue) > len(texte) or texte[len(texte) - len(neue):] != neue:
        return neue
    ueber = [u for _, u in lagen][len(texte) - len(neue):]
    jetzt = time.time()
    bekannt: dict[str, int] = {}
    for e in _lesen("eingang.json", []):
        if isinstance(e, dict) and (e.get("person") or None) == (person or None) and jetzt - float(e.get("zeit") or 0) < VERALTET_S:
            bekannt[e.get("text")] = bekannt.get(e.get("text"), 0) + 1
    rest = []
    for text, ist_ueber in zip(neue, ueber):
        if ist_ueber and bekannt.get(text, 0) > 0:
            bekannt[text] -= 1
            continue
        rest.append(text)
    return rest


def ungelesene_chat_anzahl(dump_xml: str) -> int:
    """Liest die Zahl auf dem Chat-Tab-Symbol der unteren Navigation (z.B. "1" bei einer
    ungelesenen Nachricht, live am 28.09.2026 auf dem Kamera-Hauptbildschirm gesehen) —
    sichtbar auf jedem Bildschirm mit der unteren Navigation, ohne den Chat-Tab selbst öffnen
    zu müssen. Ergänzt `neue_benachrichtigung_da()` (die prüft per `dumpsys`, ganz ohne die App
    zu öffnen) um ein zweites, aus dem laufenden Bildschirm gelesenes Signal. 0 ohne Treffer —
    das ist der Normalzustand ohne ungelesene Nachricht, kein Fehler."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return 0
    tab_mitte = None
    for knoten in wurzel.iter("node"):
        if knoten.get("resource-id") == "com.snapchat.android:id/ngs_chat_icon_container":
            tab_mitte = bounds_mitte(knoten.get("bounds", ""))
            break
    if tab_mitte is None:
        return 0
    for knoten in wurzel.iter("node"):
        text = knoten.get("text", "")
        if not text.isdigit():
            continue
        mitte = bounds_mitte(knoten.get("bounds", ""))
        # Das Badge sitzt direkt am Symbol — kleiner Toleranzradius statt exakter Deckung,
        # weil das Zahlen-Label nicht exakt mittig über dem Symbol liegt.
        if mitte is not None and abs(mitte[0] - tab_mitte[0]) < 150 and abs(mitte[1] - tab_mitte[1]) < 150:
            return int(text)
    return 0


# Am 28.09.2026 gegen das echte Gerät kalibriert (siehe Modul-Docstring). App-Sprache: Englisch.
AUSWAHL: dict[str, dict[str, str | int]] = {
    # Kamera-Hauptbildschirm → "More" aufklappen → Director Mode wählen. Die fünf
    # camera_mode_icon_image_view-Symbole haben keinen eigenen Text; index=-1 nimmt das
    # unterste (= "More"). Ein Tipp darauf klappt direkt die volle Liste mit "Director Mode"
    # als eigenem, textbasiertem Eintrag auf (live bestätigt, 28.09.2026).
    "kamera_mehr_umschalten": {"resource_id": "com.snapchat.android:id/camera_mode_icon_image_view",
                               "index": -1},
    "director_mode_eintrag": {"text": "Director Mode"},
    # Send-To-Liste (nach dem Öffnen über den gelben "Send to"-Knopf, siehe farbe_finden):
    # Der Name selbst ist nicht klickbar, die Auswahl-Kreisfläche sitzt rechts daneben
    # (Versatz live vermessen, 28.09.2026).
    "kontakt_eike_snap_auswahl": {"text": "Eike", "versatz_x": 456},
    "snap_endgueltig_senden": {"resource_id": "com.snapchat.android:id/send_to_send_button"},
    # Seit 28.10.2026 hat der Tab keinen Text mehr, nur content-desc „Chat“ und diese ID (wie bei der Kamera).
    "chat_tab": {"resource_id": "com.snapchat.android:id/ngs_chat_icon_container"},
    # Das Namens-Label sitzt direkt am Profilbild — ein Tipp genau darauf öffnet Eikes Profil
    # statt des Chats (live entdeckt, 28.09.2026). Deshalb mit Versatz nach rechts, in den
    # Zeilenkörper hinein, tippen statt auf den Namen selbst.
    "kontakt_eike_chat": {"text": "Eike", "versatz_x": 400},
    "texteingabe": {"resource_id": "com.snapchat.android:id/chat_input_text_field"},
    # Kontakt-Anfrage (03.10.2026): Snapchat-Suche — am echten Gerät kalibriert
    # (04.10.2026, Samsung A52, App-Sprache Englisch). Der Lupen-Knopf oben in der
    # Chatliste hat KEINEN Text, nur resource-id + content-desc „Search“; das
    # Eingabefeld danach heißt exakt `search-textfield` (ohne Paket-Präfix).
    "suche_oeffnen": {"resource_id": "com.snapchat.android:id/hova_header_search_icon"},
    "suche_eingabe": {"resource_id": "search-textfield"},
}

# Bildschirme ganz ohne Accessibility-Baum (Director-Mode-Kamera, Camera-Roll-Picker) — als
# Anteil der Bildschirmgröße kalibriert (Samsung A52, 28.09.2026), siehe Modul-Docstring.
# NICHT über element_finden absicherbar: bricht bei einem Snapchat-Update ggf. still falsch
# ab statt sauber zu scheitern. Deshalb so klein wie möglich gehalten.
BLINDE_KOORDINATEN: dict[str, tuple[float, float]] = {
    "import_media_knopf": (0.9259, 0.4475),      # Director-Mode-Kamera, Galerie-Symbol rechts
    "neuestes_foto_zelle": (0.125, 0.125),        # Camera-Roll-Grid, oberste linke Kachel
    "foto_auswahl_bestaetigen": (0.9056, 0.9279),  # gelbes Häkchen nach der Fotoauswahl
    "text_werkzeug_knopf": (0.9315, 0.0667),      # "T"-Knopf oben rechts, Editor NACH Preview
    "text_platzieren": (0.5, 0.35),               # irgendwo frei antippen platziert/bestätigt den Text
    "editor_schliessen": (0.063, 0.067),         # X oben links im Editor NACH Preview (29.09.2026, blind: nicht im Baum)
    "abandon_knopf": (0.5, 0.54),                # „Abandon“ im Dialog „…abandon your Snapsterpiece?“ (nie Save & Close bei 0,47)
}

# Preview/Send-to sind Snapchats markantes Gelb, aber nicht im Accessibility-Baum — per
# Farbsuche im Screenshot finden statt fest verdrahten (übersteht kleine Layout-Verschiebungen,
# bricht sauber ab statt blind zu tippen, wenn die Farbe fehlt).
_SNAPCHAT_GELB = (255, 252, 0)
_SNAPCHAT_BLAU = (15, 173, 255)      # Snapchat zeigt den „Send to“-Knopf seit dem 14.10.2026 in Blau (am Gerät gemessen)
BEREICH_PREVIEW = {"y_anteil": (0.86, 0.99), "x_anteil": (0.6, 1.0)}
BEREICH_SEND_TO = {"y_anteil": (0.90, 0.99), "x_anteil": (0.6, 1.0)}

# ---------- adb ----------

_zeiten = {"start": 0.0, "adb_aufrufe": 0, "adb_s": 0.0, "dumps": 0, "dump_s": 0.0, "pause_s": 0.0, "dump_stellen": {}, "erster_fehler": ""}


def _zeiten_start() -> None:
    """Beginnt die Zeitmessung eines Auftrags (07.10.2026): wohin geht die Zeit — adb, Bildschirm-Dumps oder Pausen?"""
    _zeiten.update(start=time.monotonic(), adb_aufrufe=0, adb_s=0.0, dumps=0, dump_s=0.0, pause_s=0.0, dump_stellen={}, erster_fehler="")


def _zeiten_ende() -> dict:
    return {"dauer_s": round(time.monotonic() - _zeiten["start"], 1) if _zeiten["start"] else 0.0,
            "adb_aufrufe": _zeiten["adb_aufrufe"], "adb_s": round(_zeiten["adb_s"], 1),
            "dumps": _zeiten["dumps"], "dump_s": round(_zeiten["dump_s"], 1), "pause_s": round(_zeiten["pause_s"], 1),
            "dump_stellen": dict(_zeiten["dump_stellen"]), "erster_fehler": _zeiten["erster_fehler"]}


def _adb(*args: str, timeout: float = 20.0) -> subprocess.CompletedProcess:
    t0 = time.monotonic()
    try:
        return subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SnapHelferFehler(f"adb-Aufruf fehlgeschlagen: {exc}") from exc
    finally:
        dauer = time.monotonic() - t0
        _zeiten["adb_aufrufe"] += 1
        _zeiten["adb_s"] += dauer
        if args[:3] == ("shell", "uiautomator", "dump"):
            _zeiten["dumps"] += 1
            _zeiten["dump_s"] += dauer
            f, ketten = sys._getframe(1), []                  # wer hat den Dump verlangt? (zwei Ebenen, ohne bildschirm_dump selbst)
            while f is not None and len(ketten) < 3:
                if f.f_code.co_name not in ("bildschirm_dump", "_adb"):
                    ketten.append(f.f_code.co_name)
                f = f.f_back
            stelle = "<".join(ketten)
            _zeiten["dump_stellen"][stelle] = _zeiten["dump_stellen"].get(stelle, 0) + 1


def geraet_verbunden() -> bool:
    ergebnis = _adb("devices")
    zeilen = [z for z in ergebnis.stdout.splitlines()[1:] if z.strip()]
    return any(z.split("\t")[-1].strip() == "device" for z in zeilen)


def bildschirm_aufwecken() -> None:
    """Weckt den Bildschirm, ohne ihn umzuschalten (anders als KEYCODE_POWER, das ein- UND
    ausschaltet — WAKEUP ist ungefährlich, auch wenn er schon an ist). Ohne Displaysperre
    (Voraussetzung, siehe Spec) ist danach direkt der Homescreen/die letzte App zu sehen."""
    ergebnis = _adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    if ergebnis.returncode != 0:
        raise SnapHelferFehler(f"Bildschirm wecken fehlgeschlagen: {ergebnis.stderr.strip()}")


def bildschirm_schlafen() -> None:
    """Legt den Bildschirm wieder schlafen — er muss nicht die ganze Zeit an sein, nur während
    einer Aktion. Fehler hier sind nicht kritisch (Bildschirm geht sonst von selbst aus)."""
    _adb("shell", "input", "keyevent", "KEYCODE_SLEEP")


def app_starten() -> None:
    ergebnis = _adb("shell", "monkey", "-p", "com.snapchat.android",
                    "-c", "android.intent.category.LAUNCHER", "1")
    if ergebnis.returncode != 0:
        raise SnapHelferFehler(f"Snapchat ließ sich nicht starten: {ergebnis.stderr.strip()}")


def app_zuruecksetzen() -> None:
    """Beendet Snapchat vollständig, bevor ein neuer Ablauf startet — ein Lauf, der mittendrin
    abgebrochen ist, darf Snapchat nicht in einem Zwischenzustand hinterlassen, an dem die
    nächsten Läufe (Senden wie Empfangen) ihre Startpunkte nicht mehr finden."""
    ergebnis = _adb("shell", "am", "force-stop", "com.snapchat.android")
    if ergebnis.returncode != 0:
        raise SnapHelferFehler(f"Snapchat zurücksetzen fehlgeschlagen: {ergebnis.stderr.strip()}")


def bildschirm_dump() -> str:
    _adb("shell", "uiautomator", "dump", "/sdcard/friday_dump.xml")
    ergebnis = _adb("exec-out", "cat", "/sdcard/friday_dump.xml")
    if ergebnis.returncode != 0 or not ergebnis.stdout:
        raise SnapHelferFehler("uiautomator dump lieferte nichts — läuft Snapchat gerade?")
    return ergebnis.stdout


def tippen(x: int, y: int) -> None:
    ergebnis = _adb("shell", "input", "tap", str(x), str(y))
    if ergebnis.returncode != 0:
        raise SnapHelferFehler(f"Tippen fehlgeschlagen: {ergebnis.stderr.strip()}")


ADB_TASTATUR = "com.android.adbkeyboard/.AdbIME"


def _adb_tastatur_aktiv() -> bool:
    ist = _adb("shell", "settings", "get", "secure", "default_input_method")
    if ist.returncode == 0 and ADB_TASTATUR in (ist.stdout or ""):
        return True
    return _adb("shell", "ime", "set", ADB_TASTATUR).returncode == 0


def text_eingeben(text: str) -> None:
    """Tippt über die ADB-Tastatur (Umlaute, Emoji, Satzzeichen — `input text` kann das nicht und
    stürzt bei Umlauten ab, 29.09.2026). Ohne sie: Rückfall auf `input text`, dann nur ASCII."""
    if _adb_tastatur_aktiv():
        b64 = base64.b64encode(re.sub(r"\s+", " ", text).strip().encode("utf-8")).decode("ascii")
        ergebnis = _adb("shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", b64)
    else:
        ergebnis = _adb("shell", "input", "text", adb_text_escapen(text))
    if ergebnis.returncode != 0:
        raise SnapHelferFehler(f"Texteingabe fehlgeschlagen: {ergebnis.stderr.strip()}")


def bild_uebertragen(lokaler_pfad: Path) -> str:
    ziel = f"/sdcard/DCIM/friday_snap_{int(time.time())}.jpg"
    ergebnis = _adb("push", str(lokaler_pfad), ziel)
    if ergebnis.returncode != 0:
        raise SnapHelferFehler(f"Bild übertragen fehlgeschlagen: {ergebnis.stderr.strip()}")
    scan = _adb("shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d", f"file://{ziel}")
    if scan.returncode != 0:
        raise SnapHelferFehler(f"Medienscan fehlgeschlagen: {scan.stderr.strip()}")
    return ziel


_KOPIE = re.compile(r"/sdcard/DCIM/friday_snap_(\d+)\.jpg")


def bild_entfernen(ziel: str) -> None:
    """Löscht die Übertragungskopie wieder vom Handy und meldet es der Galerie (sonst bleibt ein leerer Eintrag). Fasst NUR
    Dateien nach genau dem Muster von `bild_uebertragen` an; ein Fehler hier ist nie kritisch (14.10.2026: 146 alte Bilder)."""
    if not _KOPIE.fullmatch(ziel or ""):
        return
    try:
        _adb("shell", "rm", "-f", ziel)
        _adb("shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", f"file://{ziel}")
    except Exception as exc:  # noqa: BLE001
        print(f"[snap-helfer] Kopie {ziel} nicht entfernt: {exc}")


def alte_bilder_aufraeumen(vor_s: int = 3600) -> int:
    """Räumt Übertragungskopien weg, die ein früherer Lauf (Absturz, alter Stand) liegen ließ — nur ältere als `vor_s` Sekunden
    nach dem Zeitstempel im Namen, nie die eines laufenden Snaps. Gibt die Zahl der entfernten zurück."""
    try:
        ergebnis = _adb("shell", "ls", "/sdcard/DCIM")
        namen = (ergebnis.stdout or "").split()
    except Exception:  # noqa: BLE001
        return 0
    jetzt, n = time.time(), 0
    for name in namen:
        m = re.fullmatch(r"friday_snap_(\d+)\.jpg", name)
        if m and jetzt - int(m.group(1)) > vor_s:
            bild_entfernen(f"/sdcard/DCIM/{name}")
            n += 1
    return n


# Pausen zwischen Aktionen: unregelmäßig statt im Takt (normale Vorsicht,
# Muster wie kern/dienste/whatsapp.py PAUSE_CHAT_S).
PAUSE_S = (0.4, 1.0)


def _pause() -> None:
    dauer = random.uniform(*PAUSE_S)
    _zeiten["pause_s"] += dauer
    time.sleep(dauer)


def bildschirmgroesse() -> tuple[int, int]:
    """Physische Auflösung des Geräts (`adb shell wm size`) — für BLINDE_KOORDINATEN als
    Anteil statt fester Pixel, damit ein Gerätewechsel nicht jede Zahl neu kalibrieren muss."""
    ergebnis = _adb("shell", "wm", "size")
    treffer = re.search(r"(\d+)x(\d+)", ergebnis.stdout)
    if not treffer:
        raise SnapHelferFehler(f"Bildschirmgröße nicht lesbar: {ergebnis.stdout!r}")
    return int(treffer.group(1)), int(treffer.group(2))


def bildschirm_screenshot() -> bytes:
    ergebnis = subprocess.run(["adb", "exec-out", "screencap", "-p"],
                              capture_output=True, timeout=20)
    if not ergebnis.stdout:
        raise SnapHelferFehler("Screenshot fehlgeschlagen — kein Bild erhalten.")
    return ergebnis.stdout


def _png_zeilen(png_bytes: bytes) -> tuple[int, int, int, list[bytes]]:
    """Entpackt ein PNG ohne fremde Abhängigkeit (nur zlib/struct aus der Standardbibliothek) —
    reicht für die einfachen 8-Bit-Screenshots von `adb exec-out screencap -p`."""
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise SnapHelferFehler("Screenshot ist kein gültiges PNG.")
    pos = 8
    idat = b""
    breite = hoehe = farbtyp = None
    while pos < len(png_bytes):
        laenge = int.from_bytes(png_bytes[pos:pos + 4], "big")
        typ = png_bytes[pos + 4:pos + 8]
        daten = png_bytes[pos + 8:pos + 8 + laenge]
        if typ == b"IHDR":
            breite, hoehe, _tiefe, farbtyp = struct.unpack(">IIBB", daten[:10])
        elif typ == b"IDAT":
            idat += daten
        pos += 8 + laenge + 4
    if breite is None:
        raise SnapHelferFehler("Screenshot-PNG ohne IHDR.")
    kanaele = {2: 3, 6: 4, 0: 1}.get(farbtyp)
    if kanaele is None:
        raise SnapHelferFehler(f"Nicht unterstützter PNG-Farbtyp {farbtyp}.")
    roh = zlib.decompress(idat)
    stride = breite * kanaele
    zeilen: list[bytes] = []
    prev = bytearray(stride)
    i = 0
    for _ in range(hoehe):
        filtertyp = roh[i]; i += 1
        zeile = bytearray(roh[i:i + stride]); i += stride
        for x in range(stride):
            a = zeile[x - kanaele] if x >= kanaele else 0
            b = prev[x]
            c = prev[x - kanaele] if x >= kanaele else 0
            if filtertyp == 1:
                zeile[x] = (zeile[x] + a) & 0xff
            elif filtertyp == 2:
                zeile[x] = (zeile[x] + b) & 0xff
            elif filtertyp == 3:
                zeile[x] = (zeile[x] + (a + b) // 2) & 0xff
            elif filtertyp == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                zeile[x] = (zeile[x] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 0xff
        zeilen.append(bytes(zeile))
        prev = zeile
    return breite, hoehe, kanaele, zeilen


def _png_block(typ: bytes, daten: bytes) -> bytes:
    return (struct.pack(">I", len(daten)) + typ + daten
            + struct.pack(">I", zlib.crc32(typ + daten) & 0xffffffff))


def png_zuschneiden(png_bytes: bytes, links: int, oben: int, rechts: int, unten: int) -> bytes:
    """Schneidet ein Screenshot-PNG zu (nur Standardbibliothek). Der Ausschnitt wird auf das Bild
    begrenzt; ist er danach leer, gilt das als Fehler statt eines leeren Bildes."""
    breite, hoehe, kanaele, zeilen = _png_zeilen(png_bytes)
    links, oben = max(0, links), max(0, oben)
    rechts, unten = min(breite, rechts), min(hoehe, unten)
    if rechts <= links or unten <= oben:
        raise SnapHelferFehler("Bildausschnitt liegt außerhalb des Screenshots.")
    farbtyp = {1: 0, 3: 2, 4: 6}[kanaele]
    roh = b"".join(b"\x00" + zeile[links * kanaele:rechts * kanaele] for zeile in zeilen[oben:unten])
    kopf = struct.pack(">IIBBBBB", rechts - links, unten - oben, 8, farbtyp, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _png_block(b"IHDR", kopf)
            + _png_block(b"IDAT", zlib.compress(roh)) + _png_block(b"IEND", b""))


def bildflaeche(dump_xml: str) -> tuple[int, int, int, int] | None:
    """Bounds der reinen Snap-Bildfläche (ohne Statusleiste, Antwortleiste, Symbole unten)."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return None
    for k in wurzel.iter("node"):
        if k.get("resource-id") == "com.snapchat.android:id/base_image_layer_container":
            m = _BOUNDS_RE.match(k.get("bounds", ""))
            if m:
                return tuple(int(g) for g in m.groups())  # type: ignore[return-value]
    return None


def snap_offen(dump_xml: str) -> bool:
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return False
    return any(k.get("resource-id") == "com.snapchat.android:id/opera_viewer" for k in wurzel.iter("node"))


def neuer_snap_knoten(dump_xml: str, anzeige: str | None = None) -> tuple[int, int] | None:
    """Mitte des „New Snap“-Textes in der Chatliste — ein Tipp darauf öffnet den Snap. Mit `anzeige` nur in der Zeile
    DIESES Kontakts: Sonst würde der Snap eines anderen geöffnet (und verbraucht) und Eike zugeschrieben."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return None
    zeile = None
    if anzeige is not None:
        zeile = chat_zeile(dump_xml, anzeige)
        if zeile is None:
            return None
    for k in wurzel.iter("node"):
        if not k.get("text", "").startswith("New Snap"):
            continue
        mitte = bounds_mitte(k.get("bounds", ""))
        if mitte is None:
            continue
        if zeile is not None:
            von_x, von_y, bis_x, bis_y = zeile["box"]
            if not (von_x <= mitte[0] <= bis_x and von_y <= mitte[1] <= bis_y):
                continue
        return mitte
    return None


# ---------- Kontakte auseinanderhalten (Teil 4a, 29.09.2026 am Gerät gemessen) ----------
# Mehrere Kontakte im Friday-Konto: Jede Zeile der Chatliste ist ein `ff_item`, der Chat-Kopf nennt den Kontakt, die
# Send-to-Liste hat Titel UND Untertitel-Zeilen, und die Leiste unten nennt die gewählten Empfänger. Erkannt wird nur
# über exakt (normalisiert) gleiche Namen — nie „enthält“ — und immer wird NACH dem Antippen geprüft, wo man gelandet ist.

_UNSICHTBAR = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


def _norm(name) -> str:
    """Vergleichsform eines Anzeigenamens: NFKC, ohne unsichtbare Zeichen und Variation Selectors, klein, Leerzeichen
    zusammengefasst. Dieselbe Regel wie `kern.personen.register.schluessel` (ein Test hält beide gleich)."""
    s = unicodedata.normalize("NFKC", name if isinstance(name, str) else "")
    s = "".join(c for c in s if unicodedata.category(c) not in _UNSICHTBAR
                and not ("︀" <= c <= "️" or "\U000e0100" <= c <= "\U000e01ef"))
    return " ".join(s.split()).casefold()


def eike_name() -> str:
    """So heißt Eike in Fridays Snapchat-Konto: der Eintrag ohne Person in `kontakte.json` (der Dienst schreibt ihn aus
    `SNAPCHAT_EIKE_NAME`, damit Dienst und Helfer nie auseinanderlaufen — der Helfer hat die Umgebung des Containers nicht),
    sonst „Eike“."""
    d = _lesen("kontakte.json", None)
    if isinstance(d, dict) and isinstance(d.get("kontakte"), list):
        for k in d["kontakte"]:
            if isinstance(k, dict) and k.get("person") is None and str(k.get("anzeige", "")).strip():
                return str(k["anzeige"]).strip()
    return os.getenv("SNAPCHAT_EIKE_NAME", "").strip() or "Eike"


def _box(knoten) -> tuple[int, int, int, int] | None:
    m = _BOUNDS_RE.match(knoten.get("bounds", ""))
    return tuple(int(g) for g in m.groups()) if m else None  # type: ignore[return-value]


def chat_zeilen(dump_xml: str) -> list[dict]:
    """Die Kontaktzeilen der Chatliste (`ff_item`): Name, Status (`content-desc`: „New Chat“, „Received“ …), Grenzen und
    Mitte. Nur echte Zeilen — „Find friends“ und Vorschläge stehen außerhalb von `ff_item`."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return []
    zeilen = []
    for zeile in wurzel.iter("node"):
        if not (zeile.get("resource-id") or "").endswith("ff_item"):
            continue
        grenzen = _box(zeile)
        if grenzen is None:
            continue
        name = next((k.get("text") for k in zeile.iter("node")
                     if k.get("text") and _box(k) and _box(k)[1] == grenzen[1] and _box(k)[0] == 0), None)
        if name:
            zeilen.append({"anzeige": name, "status": zeile.get("content-desc") or "", "box": grenzen,
                           "mitte": zufallspunkt(grenzen)})
    return zeilen


def chat_zeilen_fuer(dump_xml: str, anzeige: str) -> list[dict]:
    """ALLE Zeilen, deren Name (normalisiert) `anzeige` ist — mehr als eine heißt: Der Name ist nicht eindeutig (zweiter Freund,
    Gruppe), und es darf an keine von beiden gesendet werden."""
    ziel = _norm(anzeige)
    if not ziel:
        return []
    return [z for z in chat_zeilen(dump_xml) if _norm(z["anzeige"]) == ziel]


def chat_zeile(dump_xml: str, anzeige: str) -> dict | None:
    zeilen = chat_zeilen_fuer(dump_xml, anzeige)
    return zeilen[0] if zeilen else None


_RESERVIERT = {"my ai", "team snapchat"}


def zeile_ist_neu(status: str) -> bool:
    """Wartet in dieser Chatzeile etwas Ungelesenes (Chat oder Snap)? Die Umkehrung von `_status_sicher`, dazu „New Chat“."""
    s = _norm(status)
    if re.search(r"\b\d+\s*(d|w)\s*$", s):
        return False     # Tage/Wochen alt: Friday antwortet nicht auf Altes (sonst Antwort nach 2 Tagen an einen Freund)
    # Mit Zähler: „2 new Chats“ / „2 neue Chats“ (Kontakt M, 09.10.2026 — wurde nie gelesen, weil nur „New …“ galt)
    return "snap" in s or s.startswith("new ") or s.startswith("neu") or bool(re.match(r"\d+\s+(new|neue)\b", s))


def neue_kontakte(dump_xml: str, bekannte: dict[str, str | None]) -> tuple[list[tuple[dict, str | None]], list[str]]:
    """Zeilen der Chatliste mit Neuem: (bekannte Kontakte samt Person, Namen unbekannter). Eike (Person None) und mehrdeutige Namen
    fehlen bewusst — Eike hat seinen eigenen Weg, und bei einem mehrdeutigen Namen weiß niemand, wessen Chat es wäre."""
    zeilen = chat_zeilen(dump_xml)
    anzahl: dict[str, int] = {}
    for z in zeilen:
        anzahl[_norm(z["anzeige"])] = anzahl.get(_norm(z["anzeige"]), 0) + 1
    mit_person, unbekannt, gesehen = [], [], set()
    for z in zeilen:
        n = _norm(z["anzeige"])
        if not zeile_ist_neu(z["status"]) or anzahl[n] > 1 or n in gesehen or n in _RESERVIERT:
            continue
        gesehen.add(n)
        if n in bekannte:
            if bekannte[n] is not None:
                mit_person.append((z, bekannte[n]))
        elif n != _norm(eike_name()):
            unbekannt.append(z["anzeige"])
    return mit_person, unbekannt


def _status_sicher(status: str) -> bool:
    """Ist der Status einer Chatzeile harmlos, sodass ein Tipp auf die Zeile keinen Snap verbraucht? Alles, was nach einem Snap
    riecht („New Snap“, „Neuer Snap“, „Snap received“ …) oder mit „New “ beginnt, aber kein „New Chat“ ist, gilt als
    gefährlich — ein geöffneter Snap ist verbraucht, ein abgebrochener Versand nur ärgerlich. Nur der Status zählt: Andere Texte
    der Zeile (z. B. der „Snap“-Knopf bei Kontakten ohne Nachrichten) sind kein Hinweis."""
    s = _norm(status)
    if s in ("double-tap to snap", "tap to snap"):
        return True     # nur der Hinweis „zum Snappen tippen“ bei Kontakten ohne Nachrichten, kein wartender Snap (Eike, 09.10.2026)
    if "snap" in s:
        return False
    return not (s.startswith("new ") and not s.startswith("new chat"))


def chat_kopfname(dump_xml: str) -> str | None:
    """Der Name im Kopf des offenen Chats (erster Text oben rechts vom Zurück-Pfeil), sonst None — auch in der Chatliste."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return None
    # Nur in einem Chat: Die Chatliste hat oben denselben Bereich mit dem Titel „Chat“ — das ist kein Kontakt.
    if not any((k.get("resource-id") or "").endswith("chat_message_list") for k in wurzel.iter("node")):
        return None
    for k in wurzel.iter("node"):
        g = _box(k)
        if k.get("text") and g and 88 <= g[1] <= 180 and g[3] <= 235 and g[0] >= 200:
            return k.get("text")
    return None


def sendto_zeilen(dump_xml: str, anzeige: str) -> list[tuple[int, int]]:
    """Wohin man in der Send-to-Liste tippt, um `anzeige` auszuwählen (der Kreis rechts, x ≈ 0,895 × Breite, y = Mitte der
    Zeile aus Titel und Untertitel) — für JEDE Zeile mit diesem Titel. Nicht gemeint sind der Untertitel unter „Last Snap“ und
    alles in der Leiste unten. Mehr als eine Zeile heißt: Der Name ist nicht eindeutig."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return []
    ziel = _norm(anzeige)
    if not ziel:
        return []
    breite = (_box(wurzel) or (0, 0, 1080, 0))[2] or 1080
    leiste = next((_box(k) for k in wurzel.iter("node")
                   if (k.get("resource-id") or "").endswith("send_to_confirmation_bar")), None)
    texte = [(k.get("text"), _box(k)) for k in wurzel.iter("node") if k.get("text") and _box(k)]
    treffer = []
    for i, (text, g) in enumerate(texte):
        if _norm(text) != ziel or g[0] < 150:
            continue
        if leiste and g[1] >= leiste[1]:
            continue
        if i > 0 and texte[i - 1][0] == "Last Snap":
            continue
        y = (g[1] + g[3]) // 2
        if i + 1 < len(texte):
            n = texte[i + 1][1]
            if n[0] == g[0] and g[3] - 5 <= n[1] <= g[3] + 10:      # Untertitel derselben Zeile
                y = (g[1] + n[3]) // 2
        # Zeile über die ganze Breite (Namensfeld reicht bis rechts): Kreis bei 0,895 × Breite. Zwei-Spalten-Raster („Best Friends“,
        # Snapchat seit dem 10.10.2026): Das Namensfeld endet in der Mitte, der Kreis sitzt knapp rechts davon — sonst träfe der
        # Tipp die Nachbarspalte (am Gerät: „Eike“ wählte „Test“).
        x = round(breite * 0.895) if (g[2] - g[0]) > breite * 0.5 else g[2] + 50    # Namensfeld breit = Listenzeile, schmal = Rasterzelle
        treffer.append((x, y))
    return treffer


def sendto_zeile(dump_xml: str, anzeige: str) -> tuple[int, int] | None:
    zeilen = sendto_zeilen(dump_xml, anzeige)
    return zeilen[0] if zeilen else None


# Tages-Trenner im Chat („TODAY“, …) sitzen links wie Absender-Labels, sind aber keine — ohne diese
# Ausnahme gälte jeder längere Chat als Gruppe (02.10.2026: Kontakt Cs Antwort ging nicht raus).
_KEINE_ABSENDER = {"today", "yesterday", "heute", "gestern"}


def chat_fremde_labels(dump_xml: str, anzeige: str) -> list[str]:
    """Absender-Labels im offenen Chat, die weder „ME“ noch der Kontakt selbst sind — Hinweis auf einen Gruppenchat (oder einen
    zweiten Kontakt mit demselben Namen): Dann darf weder gesendet noch als dieser Kontakt gelesen werden."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return []
    ziel = _norm(anzeige)
    # Ein Label sitzt ganz links (x ≈ 21 am Gerät gemessen), ein Nachrichtentext beginnt weiter rechts: Eine Nachricht wie „OK“ ist
    # sonst von einem Absender-Label nicht zu unterscheiden.
    labels = {k.get("text", "") for k in wurzel.iter("node")
              if _ABSENDER_RE.match(k.get("text", "")) and _box(k) and _box(k)[0] <= 30}
    return sorted(t for t in labels if t != "ME" and _norm(t) != ziel and _norm(t) not in _KEINE_ABSENDER)


def sendto_ausgewaehlt(dump_xml: str) -> list[str]:
    """Die Namen in der Auswahlleiste unten („Test“ …) — leer, solange niemand gewählt ist."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return []
    leiste = next((k for k in wurzel.iter("node")
                   if (k.get("resource-id") or "").endswith("send_to_confirmation_bar")), None)
    if leiste is None:
        return []
    # Snapchat zeigt in der Leiste neuerdings auch einen Zähler („2“) und „Add a Chat …“ (09.10.2026 am Gerät): keine Empfänger.
    return [t for t in (k.get("text") for k in leiste.iter("node"))
            if t and not t.isdigit() and not t.lower().startswith("add a chat")]


def erlaubte_kontakte() -> dict[str, str | None]:
    """Normalisierter Anzeigename → Person (None = Eike) aus der Kontaktliste des Dienstes (`kontakte.json`). Fehlt sie oder ist
    sie kaputt, nur Eike — nie „irgendwer“."""
    d = _lesen("kontakte.json", None)
    if isinstance(d, dict) and isinstance(d.get("kontakte"), list):
        kontakte = {_norm(k.get("anzeige", "")): k.get("person") for k in d["kontakte"] if isinstance(k, dict)}
        kontakte.pop("", None)
        if kontakte:
            return kontakte
    return {_norm(eike_name()): None}


def erlaubte_empfaenger() -> set[str]:
    return set(erlaubte_kontakte())


def freundschaft_aus_liste(dump_xml: str) -> dict[str, str]:
    """Freundschaftsstand je Chatzeile aus der Chatliste selbst (am Gerät gesehen 04.10.2026): Rechts in der Zeile steht bei
    gegenseitigen Freunden der Kamera-Knopf (content-desc „Camera Reply“), bei jemandem, der Friday NICHT hinzugefügt hat,
    ein Kreuz (Knoten mit dem Text `feed_cross_button`). Systemzeilen (Team Snapchat, My AI: `feed_chat_button`) zählen nicht.
    Zurück: {normierter Name: "befreundet" | "einseitig"} — pure Funktion."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return {}
    stand = {}
    for z in chat_zeilen(dump_xml):
        oben, unten = z["box"][1], z["box"][3]
        for k in wurzel.iter("node"):
            kasten = _box(k)
            if kasten is None or not (oben <= kasten[1] and kasten[3] <= unten) or kasten[0] < z["box"][2] // 2:
                continue
            if "feed_cross_button" in f"{k.get('resource-id', '')} {k.get('text', '')} {k.get('content-desc', '')}":
                stand[_norm(z["anzeige"])] = "einseitig"
            elif (k.get("content-desc") or "") == "Camera Reply":
                stand.setdefault(_norm(z["anzeige"]), "befreundet")
    return stand


def freundschaft_vermerken(anzeige: str, ist_einseitig: bool, befreundet: bool = False) -> None:
    """Merkt sich, was der geöffnete Chat über die Freundschaft zeigt — Friday liest es (`freundschaft.json`).
    „einseitig“ = Snapchat zeigt die Hinweiszeile; „kein_hinweis“ = Chat offen, Zeile fehlt (kein Beweis für
    eine Annahme, aber auch keiner dagegen)."""
    alle = _lesen("freundschaft.json", {})
    alle = alle if isinstance(alle, dict) else {}
    stand = "einseitig" if ist_einseitig else ("befreundet" if befreundet else "kein_hinweis")
    alle[_norm(anzeige)] = {"anzeige": anzeige, "stand": stand, "zeit": time.time()}
    _schreiben("freundschaft.json", alle)


def stand_pruefen(anzeige: str) -> str:
    """Öffnet den Chat von `anzeige` nur zum Ansehen und vermerkt den Freundschaftsstand. Schreibt nichts."""
    if not geraet_verbunden():
        raise SnapHelferFehler("Handy nicht per USB verbunden.")
    bildschirm_aufwecken()
    app_zuruecksetzen()
    app_starten()
    _pause()
    _auf_oberflaeche_warten()
    schritt_ausfuehren("chat_tab")
    liste = {}
    for _ in range(4):                    # die Liste lädt nach dem App-Start nach: erst warten, dann aufgeben
        liste = freundschaft_aus_liste(bildschirm_dump())
        if _norm(anzeige) in liste:
            break
        _pause()
    if _norm(anzeige) in liste:           # sicherer Weg: nur die Chatliste ansehen, keinen Chat öffnen
        freundschaft_vermerken(anzeige, liste[_norm(anzeige)] == "einseitig", liste[_norm(anzeige)] == "befreundet")
        return _lesen("freundschaft.json", {}).get(_norm(anzeige), {}).get("stand", "")
    kontakt_chat_oeffnen(anzeige)         # Zeile nicht sichtbar: Chat öffnen, vermerkt den Stand selbst
    _zur_chatliste()
    stand = _lesen("freundschaft.json", {}).get(_norm(anzeige), {}).get("stand", "")
    return stand


def kontakt_chat_oeffnen(anzeige: str) -> None:
    """Öffnet den Chat von `anzeige` aus der Chatliste und prüft, dass er wirklich dieser Kontakt ist: Der Name in der Liste muss
    eindeutig sein (zwei Zeilen gleichen Namens → Abbruch), der Status der Zeile darf nicht nach einem ungeöffneten Snap aussehen
    (ein Tipp würde ihn verbrauchen), der Kopf muss den Namen nennen und im Chat dürfen keine fremden Absender stehen (Gruppe)."""
    dump = bildschirm_dump()
    zeilen = chat_zeilen_fuer(dump, anzeige)
    if not zeilen:
        raise SnapHelferFehler(f"Chat „{anzeige}“ steht nicht in der Chatliste.")
    if len(zeilen) > 1:
        raise SnapHelferFehler(f"Der Name „{anzeige}“ ist in der Chatliste mehrdeutig ({len(zeilen)} Zeilen) — nichts gesendet. "
                               "Jeder Freund im Friday-Konto braucht einen eigenen Spitznamen.")
    zeile = zeilen[0]
    if neuer_snap_knoten(dump, anzeige) is not None or not _status_sicher(zeile["status"]):
        raise SnapHelferFehler(f"Von „{anzeige}“ wartet vielleicht ein ungeöffneter Snap (Status „{zeile['status']}“) — ein Tipp "
                               "würde ihn verbrauchen.")
    tippen(*zeile["mitte"])
    _pause()
    chat = bildschirm_dump()
    gefunden = chat_kopfname(chat)
    if gefunden is None or _norm(gefunden) != _norm(anzeige):
        raise SnapHelferFehler(f"Falscher Chat geöffnet: erwartet „{anzeige}“, gefunden „{gefunden}“ — nichts gesendet.")
    fremde = chat_fremde_labels(chat, anzeige)
    if fremde:
        raise SnapHelferFehler(f"Im Chat „{anzeige}“ schreiben auch {fremde} — vermutlich eine Gruppe mit gleichem Namen. Nichts gesendet.")
    freundschaft_vermerken(anzeige, einseitig(chat))


def _eike_chat_oeffnen() -> str | None:
    """Wie `kontakt_chat_oeffnen(eike_name())`, verträgt aber eine gebündelte Zeile („4 Snaps and Chats“,
    kein einzelner „New Snap“-Knoten tippbar): Das war derselbe Deadlock wie bei Kontakt C (02.10.2026), nur
    auf Eikes eigenem Leseweg bewusst nicht angefasst — mit der Folge, dass `einmal()` bei einer gebündelten
    Zeile Eikes „verbunden: False“ meldete UND `_kontakte_lesen()` nie erreichte: Stand Eikes Zeile so,
    bekam NIEMAND mehr eine Antwort, nicht nur Eike. Bei einer gebündelten Zeile deshalb ein Tipp mit
    Verzweigung wie bei anderen Kontakten (Betrachter → abholen, Chat → Kopf und Gruppe geprüft). Lässt
    sich nichts auflösen: `None` statt Fehler — der nächste Durchlauf (3 s später) versucht es erneut,
    ohne die Wache oder andere Kontakte aufzuhalten."""
    anzeige = eike_name()
    try:
        kontakt_chat_oeffnen(anzeige)
        return bildschirm_dump()
    except SnapHelferFehler:
        dump = bildschirm_dump()
        zeilen = chat_zeilen_fuer(dump, anzeige)
        if len(zeilen) != 1 or neuer_snap_knoten(dump, anzeige) is not None or _status_sicher(zeilen[0]["status"]):
            raise   # kein gebündelter Fall (Zeile fehlt/mehrdeutig oder ein einzelner Snap klemmt) — Fehler wie bisher
        zeile = zeilen[0]
        tippen(*zeile["mitte"])
        _pause()
        dump = bildschirm_dump()
        if snap_offen(dump):
            snaps_abholen(anzeige)
            dump = bildschirm_dump()
        kopf = chat_kopfname(dump)
        if kopf is not None and _norm(kopf) == _norm(anzeige):
            fremde = chat_fremde_labels(dump, anzeige)
            if fremde:
                raise SnapHelferFehler(f"Im Chat „{anzeige}“ schreiben auch {fremde} — vermutlich eine Gruppe mit gleichem "
                                       "Namen. Nichts gelesen.") from None
            freundschaft_vermerken(anzeige, einseitig(dump))
            return dump
        _zur_chatliste()
        return None


def sendto_waehlen(anzeige: str) -> None:
    """Wählt `anzeige` in der Send-to-Liste und prüft in der Leiste unten, dass genau dieser Empfänger (und sonst niemand)
    gewählt ist. Die Liste ist lang: bis zu vier Mal nach oben wischen, wenn der Name nicht sichtbar ist."""
    dump = bildschirm_dump()
    ziele = sendto_zeilen(dump, anzeige)
    for _ in range(4):
        if ziele:
            break
        breite, hoehe = bildschirmgroesse()
        _adb("shell", "input", "swipe", str(breite // 2), str(int(hoehe * 0.7)), str(breite // 2), str(int(hoehe * 0.3)), "400")
        _pause()
        dump = bildschirm_dump()
        ziele = sendto_zeilen(dump, anzeige)
    if not ziele:
        raise SnapHelferFehler(f"„{anzeige}“ steht nicht in der Send-to-Liste.")
    if len(ziele) > 1:
        raise SnapHelferFehler(f"Der Name „{anzeige}“ ist in der Send-to-Liste mehrdeutig ({len(ziele)} Zeilen) — nichts gesendet. "
                               "Jeder Freund im Friday-Konto braucht einen eigenen Spitznamen.")
    tippen(*ziele[0])
    _pause()
    gewaehlt = sendto_ausgewaehlt(bildschirm_dump())
    if [_norm(n) for n in gewaehlt] != [_norm(anzeige)]:
        raise SnapHelferFehler(f"Falscher Empfänger ausgewählt: erwartet „{anzeige}“, ausgewählt {gewaehlt} — nichts gesendet.")


def symbole_vermerken(dump_xml: str) -> None:
    """Symbolknoten im Chat mit Namen festhalten (einmal je Name), damit die Tabelle der bekannten
    Symbole wachsen kann. Sie werden nie als Nachricht beantwortet."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return
    knoten = _textknoten(wurzel)
    namen = {knoten[i + 1] for i in range(len(knoten) - 1)
             if _ABSENDER_RE.match(knoten[i]) and _ist_symbol(knoten[i + 1])}
    if not namen:
        return
    datei = _snapchat_ordner() / "unbekannt.jsonl"
    schon = set()
    if datei.exists():
        schon = {json.loads(z).get("symbol") for z in datei.read_text(encoding="utf-8").splitlines() if z}
    neue = sorted(namen - schon - {"snap_envelop"})  # snap_envelop ist bekannt: ein Snap
    if not neue:
        return
    with datei.open("a", encoding="utf-8") as f:
        for name in neue:
            f.write(json.dumps({"symbol": name, "zeit": time.time()}) + "\n")


def snap_bild_holen(dump_xml: str) -> str:
    """Screenshot des offenen Snaps, zugeschnitten auf die reine Bildfläche, unter
    `snapchat/bilder/` abgelegt. Ohne erkennbare Bildfläche gibt es KEIN Bild — ein Vollbild mit
    Oberfläche käme sonst ins Gedächtnis."""
    flaeche = bildflaeche(dump_xml)
    if flaeche is None:
        raise SnapHelferFehler("Snap-Bildfläche im Baum nicht gefunden.")
    png = png_zuschneiden(bildschirm_screenshot(), *flaeche)
    ordner = _snapchat_ordner() / "bilder"
    ordner.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.png"
    (ordner / name).write_bytes(png)
    return name


def _eingang_anhaengen(eintraege: list[dict], person: str | None = None) -> None:
    """Hängt Einträge an den Eingang. `person` = Register-Id des Kontakts; ohne Angabe ist es Eike (kein Feld)."""
    eingang = _lesen("eingang.json", [])
    jetzt = time.time()
    for e in eintraege:
        eingang.append({"id": f"{int(jetzt * 1000)}-{len(eingang)}", "zeit": jetzt, "gelesen": False, **e,
                        **({"person": person} if person else {})})
    _schreiben("eingang.json", eingang[-200:])


UNBEKANNT_MAX = 200


def kontakt_zustand(kontakt: str) -> dict:
    """Was beim letzten Blick im Chat von `kontakt` stand: Texte (None = noch nie gelesen) und Bild-Hashes. Eike hat seine
    eigenen Dateien (`sichtbar.json`, `sichtbar_bilder.json`); dies gilt für alle anderen Kontakte."""
    d = _lesen("kontakt_zustand.json", {})
    z = d.get(_norm(kontakt)) if isinstance(d, dict) else None
    z = z if isinstance(z, dict) else {}
    return {"sichtbar": z["sichtbar"] if isinstance(z.get("sichtbar"), list) else None,
            "bilder": z["bilder"] if isinstance(z.get("bilder"), list) else []}


def kontakt_zustand_schreiben(kontakt: str, zustand: dict) -> None:
    d = _lesen("kontakt_zustand.json", {})
    d = d if isinstance(d, dict) else {}
    d[_norm(kontakt)] = {"sichtbar": zustand.get("sichtbar"), "bilder": zustand.get("bilder", [])}
    _schreiben("kontakt_zustand.json", d)


def unbekannte_vermerken(namen: list[str]) -> None:
    """Namen von Chatzeilen mit Neuem, die nicht im Register stehen — nur der Name, nie Inhalt. Der Helfer hängt nur an (nie
    doppelt, höchstens `UNBEKANNT_MAX`); die Wache liest und meldet, löscht aber nichts."""
    vorhanden = _lesen("unbekannt_eingang.json", [])
    vorhanden = [n for n in vorhanden if isinstance(n, str)] if isinstance(vorhanden, list) else []
    bekannt = {_norm(n) for n in vorhanden}
    geaendert = False
    for n in namen:
        if len(vorhanden) >= UNBEKANNT_MAX:
            break
        if isinstance(n, str) and n.strip() and _norm(n) not in bekannt:
            vorhanden.append(n)
            bekannt.add(_norm(n))
            geaendert = True
    if geaendert:
        _schreiben("unbekannt_eingang.json", vorhanden)


def neue_texte(sichtbar: list[str], vorher: list[str] | None, nur_zuwachs: bool = False) -> list[str]:
    """Was in `sichtbar` neu ist. Snapchat löscht gesehene Chats: Was beim Öffnen im Chat steht, ist neu — nur wenn die Liste um neue
    Einträge WÄCHST, zählt allein der Zuwachs (dieselbe Regel wie bei Eike)."""
    if vorher and len(sichtbar) > len(vorher) and sichtbar[:len(vorher)] == vorher:
        return sichtbar[len(vorher):]
    return [] if nur_zuwachs else sichtbar   # `nur_zuwachs`: der Chat wurde ohne „neu“-Status geöffnet, was steht, ist schon gelesen


def chat_bild_flaechen(dump_xml: str) -> list[tuple[int, int, int, int]]:
    """Bounds der Chat-Bilder der Gegenseite, die im Chat GANZ sichtbar stehen (Vorschau-Knoten
    `chat_media/thumbnail`, am echten Gerät gemessen 29.09.2026: das Bild steht direkt im Chat, ein Tipp
    ist nicht nötig). Der Absender steht als Label in der Zeile davor; „ME“ (eigene Bilder) zählt nicht.
    Angeschnittene Bilder fallen weg — ein Zuschnitt aus dem Screenshot wäre unvollständig."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return []
    liste = next((k for k in wurzel.iter("node")
                  if (k.get("resource-id") or "").endswith("chat_message_list")), None)
    if liste is None:
        return []
    m = _BOUNDS_RE.match(liste.get("bounds", ""))
    if not m:
        return []
    _, oben, _, unten = (int(g) for g in m.groups())
    flaechen, absender = [], None
    for zeile in liste:
        for k in zeile.iter("node"):
            if _ABSENDER_RE.match(k.get("text", "")):
                absender = k.get("text")
        if absender == "ME":
            continue
        for k in zeile.iter("node"):
            if (k.get("resource-id") or "").endswith("chat_media/thumbnail"):
                b = _BOUNDS_RE.match(k.get("bounds", ""))
                if b:
                    box = tuple(int(g) for g in b.groups())
                    if box[1] >= oben and box[3] <= unten and box[2] > box[0] and box[3] > box[1]:
                        flaechen.append(box)  # type: ignore[arg-type]
    return flaechen


# ---------- Ticketbild im Original (09.10.2026, Spec 2026-10-09-ticket-annahme-design.md) ----------

TICKET_ORDNER = "/sdcard/DCIM/Snapchat"
LANG_DRUCK_MS = 900                       # am Gerät gemessen: so lang öffnet sich das Menü unter dem Chat-Bild
MENUE_SPEICHERN = "Save to Camera Roll"   # Handy auf Englisch; „Save in Chat“ (Symbol neben dem Bild) legt KEINE Datei an
ORIGINALE_JE_DURCHGANG = 1                # danach hat sich der Chat evtl. verschoben; weitere Bilder kommen im nächsten Durchgang
_DATEINAME = re.compile(r"^[A-Za-z0-9_.-]+$")   # geht später durch `adb shell rm` (Shell!): nur harmlose Namen


def ticket_erwartet_fuer(person: str | None) -> bool:
    """Erwartet Friday von dieser Person (Register-Id; None = Eike, Schlüssel „eike“) gerade ein Ticketbild? Nur mit Schalter
    `ticket_annahme` trocken/an und Eintrag in `ticket_erwartet.json`. Kaputte Dateien heißen: nein."""
    einst = _lesen("einstellungen.json", {})
    if not (isinstance(einst, dict) and einst.get("ticket_annahme") in ("trocken", "an")):
        return False
    liste = _lesen("ticket_erwartet.json", [])
    return isinstance(liste, list) and (person or "eike") in liste


def dateien_im_ordner() -> set[str]:
    ergebnis = _adb("shell", "ls", "-1", TICKET_ORDNER)
    if ergebnis.returncode != 0:
        return set()
    return {z.strip() for z in ergebnis.stdout.splitlines()
            if _DATEINAME.match(z.strip()) and z.strip().lower().endswith((".jpg", ".jpeg", ".png"))}


def neue_datei(vorher: set[str], nachher: set[str]) -> str | None:
    neu = sorted(nachher - vorher)
    return neu[-1] if neu else None


def menue_speichern_position(dump_xml: str) -> tuple[int, int] | None:
    return element_finden(dump_xml, text=MENUE_SPEICHERN)


def chat_bild_original_holen(box: tuple[int, int, int, int]) -> str:
    """Das Chat-Bild in `box` lang drücken → „Save to Camera Roll“ → neue Datei in `DCIM/Snapchat` → `adb pull` nach
    `snapchat/bilder/<uuid>.jpg` → vom Handy löschen. Zurück: der Dateiname. Findet sich der Menüeintrag nicht, wird NICHT blind
    getippt: Zurück-Taste und `SnapHelferFehler` (der Aufrufer fällt auf den Screencap zurück)."""
    x, y = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    vorher = dateien_im_ordner()
    try:
        _adb("shell", "input", "swipe", str(x), str(y), str(x), str(y), str(LANG_DRUCK_MS))
        _pause()
        position = menue_speichern_position(bildschirm_dump())
        if position is None:
            raise SnapHelferFehler("Menüeintrag „Save to Camera Roll“ nicht gefunden")
        tippen(*position)
        datei, ende = None, time.monotonic() + 5.0
        while datei is None and time.monotonic() < ende:
            datei = neue_datei(vorher, dateien_im_ordner())
            if datei is None:
                time.sleep(0.5)
        if datei is None:
            raise SnapHelferFehler("Nach dem Speichern keine neue Datei in der Galerie")
    except SnapHelferFehler:
        _adb("shell", "input", "keyevent", "KEYCODE_BACK")     # Menü schließen
        raise
    name = f"{uuid.uuid4().hex}.jpg"
    ordner = _snapchat_ordner() / "bilder"
    ordner.mkdir(parents=True, exist_ok=True)
    try:
        ergebnis = _adb("pull", f"{TICKET_ORDNER}/{datei}", str(ordner / name), timeout=30.0)
        if ergebnis.returncode != 0:
            raise SnapHelferFehler(f"adb pull fehlgeschlagen: {ergebnis.stderr.strip()}")
    finally:
        _adb("shell", "rm", "-f", f"{TICKET_ORDNER}/{datei}")   # nie ein Ticket auf dem Handy liegen lassen
    return name


def chat_bilder_holen(dump_xml: str, kontakt: str | None = None, person: str | None = None) -> int:
    """Holt neue Chat-Bilder der Gegenseite als reine Bildfläche unter `snapchat/bilder/` und meldet
    sie im Eingang (`art: "chatbild"`). „Neu“ heißt: der Bild-Hash stand beim letzten Blick nicht im
    Chat (`sichtbar_bilder.json`) — so wird ein Bild, das noch im Chat steht, nicht zweimal geholt.
    Ohne erkennbare Fläche gibt es kein Bild; ein Fehler beim Zuschneiden meldet nur, dass eins kam.
    `kontakt`/`person`: der Chat eines anderen Kontakts — dessen Bild-Zustand steht in `kontakt_zustand.json`, die Einträge tragen `person`."""
    def merken(liste: list[str]) -> None:
        if kontakt is None:
            _schreiben("sichtbar_bilder.json", liste)
        else:
            z = kontakt_zustand(kontakt)
            z["bilder"] = liste
            kontakt_zustand_schreiben(kontakt, z)

    flaechen = chat_bild_flaechen(dump_xml)
    if not flaechen:
        merken([])
        return 0
    schon = set(kontakt_zustand(kontakt)["bilder"] if kontakt is not None else _lesen("sichtbar_bilder.json", []))
    jetzt, eintraege, originale = [], [], 0
    screenshot = bildschirm_screenshot()
    ordner = _snapchat_ordner() / "bilder"
    for box in flaechen:
        try:
            png = png_zuschneiden(screenshot, *box)
        except SnapHelferFehler as exc:
            eintraege.append({"text": "[Bild]", "art": "chatbild", "bild": None, "fehler": str(exc)})
            continue
        kennung = hashlib.sha1(png).hexdigest()
        jetzt.append(kennung)
        if kennung in schon:
            continue
        if ticket_erwartet_fuer(person):
            if originale >= ORIGINALE_JE_DURCHGANG:
                jetzt.pop()     # noch nicht „gesehen“: kommt im nächsten Durchgang an die Reihe
                continue
            try:
                original = chat_bild_original_holen(box)
            except SnapHelferFehler as exc:
                print(f"Ticketbild-Original nicht geholt ({exc}) — Rückfall auf Screencap", file=sys.stderr)
            else:
                originale += 1
                eintraege.append({"text": "[Ticketbild]", "art": "ticketbild", "bild": original, "fehler": None})
                continue
        ordner.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.png"
        (ordner / name).write_bytes(png)
        eintraege.append({"text": "[Bild]", "art": "chatbild", "bild": name, "fehler": None})
    merken(jetzt)
    if eintraege:
        _eingang_anhaengen(eintraege, person=person)
    return len(eintraege)


SNAP_LADEZEIT_S = 10.0  # so lange darf ein Snap zum Laden brauchen, bevor der Betrachter erscheint


def _auf_snap_warten() -> bool:
    """Wartet nach dem Tipp auf den Snap-Betrachter (er kann mehrere Sekunden laden). Ohne diese
    Wartezeit sah der nächste Durchlauf noch die Chatliste und tippte ein zweites Mal — und ein
    Snap schließt sich von selbst, die Bildfläche muss also sofort nach dem Öffnen abgegriffen werden.
    True, wenn der Betrachter offen ist; False, wenn er in der Ladezeit nicht erschien."""
    ende = time.time() + SNAP_LADEZEIT_S
    while time.time() < ende:
        time.sleep(0.5)
        if snap_offen(bildschirm_dump()):
            return True
    return False


def snaps_abholen(anzeige: str | None = None, max_snaps: int = 5, person: str | None = None) -> int:
    """Öffnet neue Snaps aus der Chatliste, holt je das reine Bild und geht zurück in die Liste.
    Ein geöffneter Snap ist bei Snapchat verbraucht — deshalb wird jeder genau einmal geöffnet und
    sein Eingangseintrag sofort geschrieben, auch wenn das Bild nicht lesbar war."""
    anzeige = anzeige or eike_name()   # nur Snaps DIESES Kontakts: ein Snap eines anderen wäre verbraucht und Eike zugeschrieben
    anzahl = 0
    for _ in range(max_snaps * 3):
        if anzahl >= max_snaps:
            break
        dump = bildschirm_dump()
        if snap_offen(dump):
            try:
                _eingang_anhaengen([{"text": "[Snap]", "art": "snap",
                                     "bild": snap_bild_holen(dump), "fehler": None}], person=person)
            except SnapHelferFehler as exc:
                _eingang_anhaengen([{"text": "[Snap]", "art": "snap", "bild": None, "fehler": str(exc)}], person=person)
            anzahl += 1
            print(f"[snap-helfer] Snap {anzahl} abgeholt", flush=True)
            breite, hoehe = bildschirmgroesse()
            tippen(int(breite * 0.9), int(hoehe * 0.5))  # nächster Snap oder zurück zur Liste
            _pause()
            continue
        mitte = neuer_snap_knoten(dump, anzeige)
        if mitte is None:
            break
        print("[snap-helfer] neuer Snap in der Chatliste, öffne ihn", flush=True)
        tippen(*mitte)
        if not _auf_snap_warten():
            # Der Betrachter wurde nicht erkannt. Steht „New Snap“ noch da, hat der Tipp nicht gegriffen
            # (nächster Versuch); sonst wurde der Snap geöffnet und ist weg — dann wenigstens sagen, dass er
            # kam, damit Friday nicht so tut, als wäre nichts gewesen.
            if neuer_snap_knoten(bildschirm_dump(), anzeige) is None:
                _eingang_anhaengen([{"text": "[Snap]", "art": "snap", "bild": None,
                                     "fehler": "Der Snap wurde geöffnet, aber der Betrachter nicht erkannt."}], person=person)
                anzahl += 1
    return anzahl


def farbe_finden(png_bytes: bytes, farbe: tuple[int, int, int] = _SNAPCHAT_GELB, *,
                 toleranz: int = 25, y_anteil: tuple[float, float] = (0.0, 1.0),
                 x_anteil: tuple[float, float] = (0.0, 1.0)) -> tuple[int, int] | None:
    """Mitte des Farbbereichs `farbe` innerhalb des Suchfensters (als Anteil der Bildgröße),
    oder None, wenn nichts passt — der Aufrufer bricht dann sauber ab wie bei `element_finden`."""
    breite, hoehe, kanaele, zeilen = _png_zeilen(png_bytes)
    y_von = max(0, round(hoehe * y_anteil[0])); y_bis = min(hoehe, round(hoehe * y_anteil[1]))
    x_von = max(0, round(breite * x_anteil[0])); x_bis = min(breite, round(breite * x_anteil[1]))
    r0, g0, b0 = farbe
    treffer = []
    for y in range(y_von, y_bis):
        zeile = zeilen[y]
        for x in range(x_von, x_bis):
            o = x * kanaele
            r, g, b = zeile[o], zeile[o + 1], zeile[o + 2]
            if abs(r - r0) <= toleranz and abs(g - g0) <= toleranz and abs(b - b0) <= toleranz:
                treffer.append((x, y))
    if not treffer:
        return None
    xs = [p[0] for p in treffer]; ys = [p[1] for p in treffer]
    return (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2


def _blind_tippen(name: str) -> None:
    """Tippt eine der wenigen fest kalibrierten Koordinaten für Bildschirme ohne Accessibility-
    Baum (siehe Modul-Docstring und BLINDE_KOORDINATEN) — als Anteil der Bildschirmgröße."""
    breite, hoehe = bildschirmgroesse()
    anteil_x, anteil_y = BLINDE_KOORDINATEN[name]
    tippen(round(breite * anteil_x), round(hoehe * anteil_y))
    _pause()


def _sendeknopf_finden(png_bytes: bytes, **bereich) -> tuple[int, int] | None:
    """Der Weiter-/„Send to“-Knopf: gelb (bisher) oder blau (seit 14.10.2026) — beides, damit ein Rückfall der App nicht bricht."""
    for farbe in (_SNAPCHAT_GELB, _SNAPCHAT_BLAU):
        mitte = farbe_finden(png_bytes, farbe, **bereich)
        if mitte is not None:
            return mitte
    return None


def _gelben_knopf_tippen(bereich: dict) -> None:
    mitte = _sendeknopf_finden(bildschirm_screenshot(), **bereich)
    if mitte is None:
        raise SnapHelferFehler(
            f"Gelber oder blauer Knopf im Bereich {bereich} nicht gefunden - "
            "Snapchat-UI hat sich vermutlich geändert.")
    tippen(*mitte)
    _pause()


def schritt_ausfuehren(name: str) -> None:
    kriterien = dict(AUSWAHL[name])
    versatz_x = kriterien.pop("versatz_x", 0)
    versatz_y = kriterien.pop("versatz_y", 0)
    dump = bildschirm_dump()
    mitte = element_finden(dump, zufall=True, **kriterien)
    if mitte is None:
        raise SnapHelferFehler(
            f"Element '{name}' nicht auf dem Bildschirm gefunden - "
            "Snapchat-UI hat sich vermutlich geändert, AUSWAHL in snap_helfer.py prüfen.")
    tippen(mitte[0] + versatz_x, mitte[1] + versatz_y)
    _pause()


def _chat_tab_oeffnen() -> None:
    """Auf die Chatliste wechseln — aber nur, wenn sie nicht schon offen ist. Der Tipp auf den Chat-Tab einer offenen Liste scrollt sie
    (am Gerät gesehen 07.10.2026: Eike und Test verschwanden aus dem Bild, „steht nicht in der Chatliste“, Neustart, doppelte Dauer)."""
    if chat_zeilen(bildschirm_dump()):
        return
    schritt_ausfuehren("chat_tab")


def chat_senden() -> None:
    """Sendet den eingegebenen Chat-Text ab. Snapchat hat dafür keinen eigenen Knopf (am echten
    Gerät geprüft) — die Enter-Taste der Tastatur löst den Versand aus."""
    ergebnis = _adb("shell", "input", "keyevent", "KEYCODE_ENTER")
    if ergebnis.returncode != 0:
        raise SnapHelferFehler(f"Chat-Senden fehlgeschlagen: {ergebnis.stderr.strip()}")


def mit_neuversuch(funktion, *args):
    """Ein Fehlschlag (`SnapHelferFehler`) bekommt genau eine zweite Chance: App komplett
    zurücksetzen und neu starten — das räumt praktisch jeden Zwischenzustand weg (falsches
    Untermenü, Lens-Karussell im Weg) — und `funktion` noch einmal von vorn versuchen. Scheitert
    auch der zweite Versuch (oder schon das Zurücksetzen selbst), schlägt dessen eigener Fehler
    durch — kein endloses Herumprobieren, kein Verschleiern."""
    try:
        return funktion(*args)
    except SnapHelferFehler as exc:
        _zeiten["erster_fehler"] = str(exc)[:200]
        print(f"[snap-helfer] Versuch 1 gescheitert ({exc}) — App neu starten und noch einmal", flush=True)
        app_zuruecksetzen()
        app_starten()
        _pause()
        _auf_oberflaeche_warten()
        _entwurf_aufraeumen()
        return funktion(*args)


def _editor_offen() -> bool:
    """Steht der Editor nach „Preview“ offen? Er ist nicht im Accessibility-Baum, aber sein gelber „Send to“-Knopf unten rechts
    ist im Screenshot (dasselbe Gelb, das der Sendeablauf ohnehin sucht). Ein abgedunkelter Knopf hinter dem Dialog
    zählt nicht — ist der Dialog schon offen, wird er vom nächsten Lauf über den Neustart erledigt."""
    return _sendeknopf_finden(bildschirm_screenshot(), **BEREICH_SEND_TO) is not None


def _entwurf_aufraeumen() -> None:
    """Räumt einen hängengebliebenen Director-Mode-Entwurf weg, falls einer offen ist —
    Snapchat behält einen begonnenen Entwurf über `app_zuruecksetzen()` (force-stop) hinweg und
    zeigt ihn beim nächsten App-Start wieder an, statt zur leeren Kamera zurückzukehren (live
    entdeckt, 28.09.2026: `app_zuruecksetzen`+`app_starten` allein reicht NICHT als sauberer
    Reset). Der Bestätigungs-Dialog „Discard Entire Video?" (vor Preview) ist zuverlässig über
    den „Cancel"-Knopf zu finden (eigene Resource-ID, live vermessen) — „Discard" selbst hat
    keine eigene ID, sitzt aber immer fest 159px darüber. Kein Dialog offen → tut nichts, kein
    Fehler.

    **Nach „Preview“ (29.09.2026 am Gerät gemessen):** Editor und Abbruchdialog sind blind (nicht im Accessibility-Baum). Erkannt
    wird der Editor am gelben „Send to“-Knopf im Screenshot; verworfen wird über X oben links und „Abandon“ (kalibrierte
    Tipps `editor_schliessen`, `abandon_knopf`), danach kontrolliert ein Screenshot, dass er weg ist (`docs/LEHREN.md`)."""
    dump = bildschirm_dump()
    cancel = element_finden(dump, resource_id="com.snapchat.android:id/cancel_button")
    if cancel is not None:
        tippen(cancel[0], cancel[1] - 159)
        _pause()
    if _editor_offen():
        # Entwurf nach „Preview“ (29.09.2026 gemessen): Editor und Dialog sind blind. X oben links, dann „Abandon“ — NIE „Save & Close“
        # (das speichert in Memories). Danach per Screenshot kontrollieren, dass der Editor wirklich weg ist.
        _blind_tippen("editor_schliessen")
        _blind_tippen("abandon_knopf")
        if _editor_offen():
            raise SnapHelferFehler("Der Entwurf ist nach „Abandon“ noch offen — nichts gesendet.")


def _zur_kamera() -> None:
    """Der Bild-Ablauf braucht den Kamera-Hauptbildschirm. Snapchat öffnet nach dem Neustart nicht immer dort (am 10.10.2026
    bei Spotlight, einmal in der Spotlight-Aufnahme „Add a Sound“): Ist das „More“-Symbol der Kamera nicht da, erst Zurück
    (schließt die Aufnahme/Dialoge), dann den Tab „Camera“ unten antippen. Findet sich nichts, geht es wie bisher weiter —
    `schritt_ausfuehren` meldet dann den ehrlichen Fehler."""
    for _ in range(3):
        dump = bildschirm_dump()
        if element_finden(dump, resource_id=AUSWAHL["kamera_mehr_umschalten"]["resource_id"]) is not None:
            return
        tab = element_finden(dump, resource_id="com.snapchat.android:id/ngs_camera_icon_container")   # Label heißt „Explore“, die ID bleibt
        if tab is not None:
            tippen(*tab)
        else:
            _adb("shell", "input", "keyevent", "4")
        _pause()


def _bild_snap_ablauf(bild_pfad: str, text: str, empfaenger: str | None = None) -> None:
    """Ein Baustein für sich — auch einzeln aufrufbar/testbar, nicht nur über `snap_senden`."""
    alte_bilder_aufraeumen()
    ziel = bild_uebertragen(Path(bild_pfad))
    try:
        _bild_snap_schritte(text, empfaenger)
    finally:
        bild_entfernen(ziel)


def _bild_snap_schritte(text: str, empfaenger: str | None) -> None:
    _pause()
    _zur_kamera()
    schritt_ausfuehren("kamera_mehr_umschalten")
    schritt_ausfuehren("director_mode_eintrag")
    _blind_tippen("import_media_knopf")
    _blind_tippen("neuestes_foto_zelle")
    _blind_tippen("foto_auswahl_bestaetigen")
    _gelben_knopf_tippen(BEREICH_PREVIEW)
    if text:
        # Der Text-Werkzeug-Knopf ("T") lebt auf dem Editor NACH Preview, nicht davor — live
        # korrigiert (28.09.2026), siehe Modul-Docstring.
        _blind_tippen("text_werkzeug_knopf")
        text_eingeben(text)
        _blind_tippen("text_platzieren")
    _gelben_knopf_tippen(BEREICH_SEND_TO)
    sendto_waehlen(empfaenger or eike_name())      # wählt UND prüft die Auswahlleiste, sonst Abbruch vor „Senden“
    schritt_ausfuehren("snap_endgueltig_senden")


def eigener_text_sichtbar(dump_xml: str, text: str) -> bool:
    """Steht der gesendete Text (so, wie `adb_text_escapen` ihn eingetippt hat) im Chat? Ohne diese
    Probe meldete der Helfer „gesendet“, obwohl er nur getippt hatte (29.09.2026)."""
    # Nur Buchstaben und Ziffern vergleichen: Die Tastatur ergänzt Satzzeichen („gibts“ → „gibt's“)
    # und lässt an Emoji-Stellen doppelte Leerzeichen — ein Zeichenvergleich schlug deshalb fehl.
    def kern(t: str) -> str:
        return re.sub(r"[\W_]+", "", umlaute_ersetzen(t), flags=re.UNICODE).lower()

    erwartet = kern(adb_text_escapen(text).replace("%s", " "))
    if not erwartet:
        return True
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return False
    return any(erwartet in kern(k.get("text", "")) for k in wurzel.iter("node"))


_beim_senden_gesehen: list[str] = []


def _chat_text_ablauf(text: str, empfaenger: str | None = None) -> None:
    """Ein Baustein für sich — auch einzeln aufrufbar/testbar, nicht nur über `snap_senden`."""
    empfaenger = empfaenger or eike_name()
    _chat_tab_oeffnen()
    # Ein Tipp auf Eikes Zeile öffnet einen wartenden Snap statt des Chats — und verbraucht ihn.
    # Deshalb erst Snaps abholen (Eintrag im Eingang), dann in den Chat.
    snaps_abholen()
    person = erlaubte_kontakte().get(_norm(empfaenger))
    if person:
        snaps_abholen(empfaenger, person=person)   # ein wartender Snap DIESES Kontakts: sonst bricht das Öffnen seines Chats ab
    zeilen = chat_zeilen_fuer(bildschirm_dump(), empfaenger) if person else []
    war_neu = len(zeilen) == 1 and zeile_ist_neu(zeilen[0]["status"])   # nur dann ist, was im Chat steht, wirklich ungelesen
    kontakt_chat_oeffnen(empfaenger)     # tippt die Zeile UND prüft den Kopf: falscher Chat → Abbruch vor dem Tippen
    # Das Öffnen des Chats „sieht“ auch neu eingetroffene Nachrichten — und Snapchat löscht sie
    # danach. Deshalb hier mitlesen, sonst gehen sie verloren: bei Eike über `_beim_senden_gesehen`, bei jedem anderen Kontakt
    # direkt in den Eingang mit SEINER `person` (nie Eike zugeschrieben).
    try:
        if _norm(empfaenger) == _norm(eike_name()):
            _beim_senden_gesehen.extend(fremde_texte(bildschirm_dump()))
        elif person:
            _kontakt_mitlesen(empfaenger, person, bildschirm_dump(), nur_zuwachs=not war_neu)
    except SnapHelferFehler:
        pass
    schritt_ausfuehren("texteingabe")
    text_eingeben(text)
    _pause()
    chat_senden()
    time.sleep(2.0)
    if not eigener_text_sichtbar(bildschirm_dump(), text):
        raise SendenUnbestaetigt("Gesendeter Text steht nicht im Chat - Senden nicht bestätigt.")
    if heimat_aktiv():
        # Raus aus dem Chat: Eine Antwort, die im offenen Chat eintrifft, würde sonst beim nächsten Aufwecken „gesehen“ und danach
        # von Snapchat gelöscht (Review 07.10.2026). Darf nie werfen — der Text ist raus, ein Neuversuch würde doppelt senden.
        try:
            _zur_chatliste()
        except SnapHelferFehler:
            pass


def heimat_aktiv() -> bool:
    """Heimat-Modus (07.10.2026, Spec „schnell“): Snapchat bleibt auf der Chatliste — kein Neustart je Auftrag, kein Warten im Chat.
    Rückfallschalter in `data/snapchat/einstellungen.json` (`{"heimat": true}`), Standard aus."""
    e = _lesen("einstellungen.json", {})
    return bool(isinstance(e, dict) and e.get("heimat") is True)


def _heimat_vorbereiten() -> None:
    """Snapchat läuft schon: nur sicherstellen, dass die Chatliste steht. Sonst der harte alte Weg (App neu starten) — nie ein
    blinder Tipp auf einen unbekannten Bildschirm."""
    bildschirm_aufwecken()
    try:
        _zur_chatliste()          # prüft selbst zuerst, ob die Liste schon steht (ein Dump), und geht sonst zurück
    except SnapHelferFehler:
        app_zuruecksetzen()
        app_starten()
        _pause()


def _auf_oberflaeche_warten(max_s: float = 30.0) -> None:
    """Nach dem Kaltstart zeigt Snapchat erst einen Splash (seit dem Update vom 10.10.2026 länger als die kurze Pause): Warten, bis
    die untere Leiste (Chat/Kamera-Tab) im Dump steht. Läuft die Zeit ab, geht es trotzdem weiter — der Ablauf meldet dann seinen
    eigenen, ehrlichen Fehler."""
    ende = time.monotonic() + max_s
    while time.monotonic() < ende:
        try:
            dump = bildschirm_dump()
        except SnapHelferFehler:
            dump = ""
        if element_finden(dump, resource_id="com.snapchat.android:id/ngs_chat_icon_container") is not None:
            return
        time.sleep(1.5)


def _app_bereit(neustart: bool) -> None:
    if neustart:
        bildschirm_aufwecken()
        app_zuruecksetzen()
        app_starten()
        _pause()
        _auf_oberflaeche_warten()
    else:
        _heimat_vorbereiten()


def snap_senden(bild_pfad: str | None, text: str, empfaenger: str | None = None, neustart: bool = True) -> None:
    if not geraet_verbunden():
        raise SnapHelferFehler("Handy nicht per USB verbunden.")
    _app_bereit(neustart)
    if bild_pfad is not None:
        mit_neuversuch(_bild_snap_ablauf, bild_pfad, text, empfaenger)
    else:
        mit_neuversuch(_chat_text_ablauf, text, empfaenger)


# ---------- Kontakt-Anfrage ----------

HINZUFUEGEN_TEXTE = ("Add", "Hinzufügen")
AUSSTEHEND_TEXTE = ("Pending", "Ausstehend", "Added", "Hinzugefügt")
# Sektions-Titel der Snapchat-Suche (04.10.2026 am Gerät gemessen): „From Contacts“
# lädt per SMS/WhatsApp ein (KEINE Snapchat-Anfrage — nie antippen), „Add Friends“
# ist die echte Anfrage-Zeile. Steht der Titel im Dump, zählen nur Treffer darunter.
_ADD_FRIENDS_TITEL = ("add friends", "freunde hinzufügen")
_FROM_CONTACTS_TITEL = ("from contacts", "aus kontakten")
_FREUNDE_TITEL = ("friends & groups", "freunde & gruppen")   # Sektion der Suche für bestehende Freunde (09.10.2026 am Gerät gemessen)
SCHON_BEFREUNDET = "Schon befreundet"                       # Anfang des Verwurf-Grundes — der Server wertet ihn aus
_TREFFER_BAND_PX = 120
SUCH_TREFFER_MAX = 5  # mehr zeigt Friday Eike nie an („sag 1 bis 5“)


def anfrage_suche_oeffnen() -> None:
    """Snapchat-Suche öffnen und Eingabefeld fokussieren (Kalibrierung am Gerät ausstehend)."""
    if not geraet_verbunden():
        raise SnapHelferFehler("Handy nicht per USB verbunden.")
    bildschirm_aufwecken()
    app_zuruecksetzen()
    app_starten()
    _pause()
    _auf_oberflaeche_warten()
    schritt_ausfuehren("suche_oeffnen")
    schritt_ausfuehren("suche_eingabe")


def _ist_suchfeld(knoten) -> bool:
    """Such-Eingabefeld (Echo des Getippten) — zählt nie als Treffer oder fremde Zeile."""
    return "search" in (knoten.get("resource-id", "") or "").lower()


def anfrage_treffer(dump_xml: str, anzeige: str) -> list[tuple[int, int]]:
    """Trefferzeilen mit genau diesem Namen (normiert) als Tipp-Koordinaten — pure Funktion.
    Stehen Sektionstitel im Dump, zählen nur Zeilen unter „Add Friends“: Die
    „From Contacts“-Sektion darüber lädt per SMS/WhatsApp ein (keine Snapchat-Anfrage).
    Gibt es nur „From Contacts“ ohne „Add Friends“, ist das kein Treffer (Abbruch)."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return []
    ziel = _norm(anzeige or "")
    ab_y, nur_kontakte = _anfrage_bereich(dump_xml)
    if nur_kontakte:
        return []
    treffer = []
    for knoten in wurzel.iter("node"):
        if _ist_suchfeld(knoten):
            continue
        text = knoten.get("text", "")
        if text and _norm(text) == ziel:
            mitte = bounds_mitte(knoten.get("bounds", ""))
            if mitte is not None and (ab_y is None or mitte[1] > ab_y):
                treffer.append(mitte)
    return treffer


def _sektion_y(dump_xml: str, titel: tuple[str, ...]) -> int | None:
    """Y-Mitte eines Sektionstitels (Text oder content-desc, normiert verglichen)."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return None
    for knoten in wurzel.iter("node"):
        for feld in (knoten.get("text", ""), knoten.get("content-desc", "")):
            if _norm(feld or "") in titel:
                mitte = bounds_mitte(knoten.get("bounds", ""))
                if mitte is not None:
                    return mitte[1]
    return None


def _unter_sektion(dump_xml: str, titel: tuple[str, ...], y: int) -> bool:
    """Steht die Zeile bei `y` unter dem Sektionstitel `titel` — und unter keinem anderen, näheren Titel (Add Friends, From Contacts)?"""
    start = _sektion_y(dump_xml, titel)
    if start is None or start >= y:
        return False
    andere = [t for t in (_sektion_y(dump_xml, _ADD_FRIENDS_TITEL), _sektion_y(dump_xml, _FROM_CONTACTS_TITEL)) if t is not None]
    return not any(start < t < y for t in andere)


def _anfrage_bereich(dump_xml: str) -> tuple[int | None, bool]:
    """(Ab-Y, nur_kontakte): Treffer zählen erst unter dem „Add Friends“-Titel.
    Steht nur „From Contacts“ da (Person nicht auf Snapchat, nur SMS-Einladung),
    ist jeder Treffer keiner (True) — kein Raten, kein SMS-Tipp. Ohne Titel
    (alte Fixtures): kein Filter (None, False)."""
    add_y = _sektion_y(dump_xml, _ADD_FRIENDS_TITEL)
    if add_y is not None:
        return add_y, False
    from_y = _sektion_y(dump_xml, _FROM_CONTACTS_TITEL)
    if from_y is not None:
        return None, True
    return None, False


def _add_friends_ab_y(dump_xml: str) -> int | None:
    """Y-Mitte des „Add Friends“-Sektionstitels — darunter stehen die echten
    Anfrage-Zeilen. None, wenn der Titel fehlt (alte Fixtures, andere Sprache:
    dann gilt das alte Verhalten ohne Sektions-Filter)."""
    ab_y, _ = _anfrage_bereich(dump_xml)
    return ab_y


def _band_fremde_namen(dump_xml: str, y: int, eigen_norm: str,
                       eigene: frozenset[str] = frozenset()) -> list[str]:
    """Andere Namenszeilen im Trefferband (03.10.2026, Review): Steht neben dem Treffer eine
    fremde Zeile, gehört der Add-Knopf vielleicht ihr — dann nicht antippen. Knopf- und
    Status-Texte sowie das Suchfeld zählen nicht. Statuszeilen (`result-reason` wie
    „IN MY CONTACTS“) zählen nie — auch die eigene Zeile hat eine (Gespräch 439:
    sonst tötet die eigene Zeile den Ablauf). `eigene`: weitere erlaubte Namen der
    Trefferzeile (Titel + Username, „Kontakt A“ vs. „tim_kudla“)."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return []
    erlaubt = {eigen_norm} | set(eigene)
    fremd = []
    for knoten in wurzel.iter("node"):
        if _ist_suchfeld(knoten):
            continue
        if (knoten.get("resource-id", "") or "").endswith("result-reason"):
            continue
        text = (knoten.get("text", "") or "").strip()
        if not text or _norm(text) in erlaubt or text in HINZUFUEGEN_TEXTE + AUSSTEHEND_TEXTE:
            continue
        mitte = bounds_mitte(knoten.get("bounds", ""))
        if mitte is not None and abs(mitte[1] - y) <= _TREFFER_BAND_PX and text not in fremd:
            fremd.append(text)
    return fremd


_EIGENE_ZEILE_PX = 70  # Titel und Username der Trefferzeile liegen so eng beisammen


def _eigene_zeile_namen(dump_xml: str, y: int, eigen_norm: str) -> frozenset[str]:
    """Titel + Username der Trefferzeile als erlaubte Namen (Gespräch 439: „Kontakt A“
    vs. „tim_kudla“ — sonst gilt der eigene Titel als fremd). Nur `result-title`
    und `result-subtitle` im engen Band zählen dazu."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return frozenset()
    namen = {eigen_norm}
    for knoten in wurzel.iter("node"):
        if knoten.get("resource-id") not in ("result-title", "result-subtitle"):
            continue
        mitte = bounds_mitte(knoten.get("bounds", ""))
        text = (knoten.get("text", "") or "").strip()
        if text and mitte is not None and abs(mitte[1] - y) <= _EIGENE_ZEILE_PX:
            namen.add(_norm(text))
    return frozenset(namen)


def _zeilenumgebung(dump_xml: str, y: int) -> list[str]:
    """Texte im selben Zeilenband (±120 px) — für Knopf- und Status-Suche."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return []
    texte = []
    for knoten in wurzel.iter("node"):
        text = knoten.get("text", "")
        mitte = bounds_mitte(knoten.get("bounds", ""))
        if text and mitte is not None and abs(mitte[1] - y) <= _TREFFER_BAND_PX:
            texte.append(text)
    return texte


def hinzufuegen_knopf(dump_xml: str, y: int) -> tuple[int, int] | None:
    """Hinzufügen-Knopf in der Trefferzeile — None, wenn keiner da ist (Abbruch, kein Raten).
    Der echte Knopf hat keinen Text, nur content-desc „Add“ (04.10.2026 am Gerät gemessen)."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return None
    for knoten in wurzel.iter("node"):
        if knoten.get("text", "") in HINZUFUEGEN_TEXTE \
                or (knoten.get("content-desc", "") or "") in HINZUFUEGEN_TEXTE:
            mitte = bounds_mitte(knoten.get("bounds", ""))
            if mitte is not None and abs(mitte[1] - y) <= _TREFFER_BAND_PX:
                return mitte
    return None


def _anfrage_ausstehend(dump_xml: str, anzeige: str) -> bool:
    """Ist die Anfrage raus? Nur bei positivem Status-Text in der Zeile (Review 03.10.2026:
    „Knopf weg“ allein beweist nichts — nach unkalibriertem Tap meldet der Helfer lieber
    Neuversuch als stillen Erfolg)."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return False
    for knoten in wurzel.iter("node"):
        text = knoten.get("text", "")
        if text and _norm(text) == _norm(anzeige):
            mitte = bounds_mitte(knoten.get("bounds", ""))
            if mitte is None:
                continue
            umgebung = _zeilenumgebung(dump_xml, mitte[1])
            if any(t in AUSSTEHEND_TEXTE for t in umgebung):
                return True
    return False


def anfrage_abarbeiten(auftrag: dict, dump_xml: str | None = None) -> None:
    """Genau eine Kontakt-Anfrage: suchen, bei genau einem Treffer Hinzufügen antippen,
    Zustand prüfen. Jeder Zweifel → AuftragVerworfen VOR dem Antippen; technische
    Fehler → SnapHelferFehler (Neuversuch über den normalen Weg)."""
    name = (auftrag.get("empfaenger") or "").strip()
    if not name:
        raise AuftragVerworfen("Anfrage ohne Namen verworfen.")
    if dump_xml is None:
        anfrage_suche_oeffnen()
        text_eingeben(name)          # ADB-Tastatur (Umlaute/Emoji), nie geraten
        dump_xml = bildschirm_dump()
    treffer = anfrage_treffer(dump_xml, name)
    if not treffer:
        raise AuftragVerworfen(f"„{name}“ nicht gefunden — nichts angetippt.")
    if len(treffer) > 1:
        raise AuftragVerworfen(f"„{name}“ ist mehrdeutig — nichts angetippt.")
    fremd = _band_fremde_namen(dump_xml, treffer[0][1], _norm(name),
                                 _eigene_zeile_namen(dump_xml, treffer[0][1], _norm(name)))
    if fremd:
        raise AuftragVerworfen(f"Fremde Zeile neben „{name}“ — nichts angetippt.")
    knopf = hinzufuegen_knopf(dump_xml, treffer[0][1])
    if knopf is None:
        if _unter_sektion(dump_xml, _FREUNDE_TITEL, treffer[0][1]):
            raise AuftragVerworfen(f"{SCHON_BEFREUNDET}: „{name}“ steht unter „Friends & Groups“ — nichts angetippt.")
        raise AuftragVerworfen(f"Bei „{name}“ gibt es keinen Hinzufügen-Knopf — nichts angetippt.")
    tippen(*knopf)
    _pause()
    if _anfrage_ausstehend(bildschirm_dump(), name):
        return
    raise SnapHelferFehler(f"Hinzufügen bei „{name}“ nicht bestätigt — neuer Versuch.")


# ---------- Eingegangene Anfragen annehmen (11.10.2026, Torge-Fall) ----------
# Am Gerät gemessen (Samsung A52, Englisch): Chatliste → Symbol „Add friends“ (nur content-desc) → Abschnitt
# „Added Me“ mit Zeilen result-title + Knopf `scu_search_action_button` „Accept“. Ohne Knopf („Say hi!“) ist
# die Person schon Freund. Angezeigt wird der ANZEIGENAME, nie der Benutzername.
ANNEHMEN_TEXTE = ("Accept", "Annehmen")
_ADD_FRIENDS_ICON = ("add friends", "freunde hinzufügen")
_ADDED_ME_TITEL = ("added me",)
_MEHR_ZELLE = "add_friends_view_more_cell"


def _add_friends_oeffnen() -> None:
    dump = bildschirm_dump()
    try:
        wurzel = ET.fromstring(dump)
    except ET.ParseError:
        wurzel = None
    mitte = None
    if wurzel is not None:
        for knoten in wurzel.iter("node"):
            if _norm(knoten.get("content-desc", "") or "") in _ADD_FRIENDS_ICON and knoten.get("text", "") == "":
                mitte = bounds_zufall(knoten.get("bounds", ""))
                if mitte is not None:
                    break
    if mitte is None:
        raise SnapHelferFehler("Symbol „Add friends“ nicht gefunden — Snapchat-UI hat sich vermutlich geändert.")
    tippen(*mitte)
    _pause()


def annehmen_zeilen(dump_xml: str) -> list[dict]:
    """Zeilen im Abschnitt „Added Me“ als {name, knopf} — pure Funktion, tippt nie. `knopf` ist die Tipp-Mitte des
    „Accept“-Knopfs oder None (schon Freund). Der Abschnitt endet beim nächsten Abschnittstitel (bzw. „View more“)."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return []
    titel_y = None
    ende_y = None
    for knoten in wurzel.iter("node"):
        if knoten.get("resource-id") != "section-title":
            continue
        mitte = bounds_mitte(knoten.get("bounds", ""))
        if mitte is None:
            continue
        if titel_y is None and _norm(knoten.get("text", "")) in _ADDED_ME_TITEL:
            titel_y = mitte[1]
        elif titel_y is not None and mitte[1] > titel_y and ende_y is None:
            ende_y = mitte[1]
    if titel_y is None:
        return []
    zeilen = []
    for knoten in wurzel.iter("node"):
        if knoten.get("resource-id") != "result-title":
            continue
        mitte = bounds_mitte(knoten.get("bounds", ""))
        name = (knoten.get("text", "") or "").strip()
        if mitte is None or not name or mitte[1] <= titel_y or (ende_y is not None and mitte[1] >= ende_y):
            continue
        knopf = None
        for k2 in wurzel.iter("node"):
            beschriftung = (k2.get("text", "") or k2.get("content-desc", "") or "").strip()   # wie „Add“: nur content-desc
            if k2.get("resource-id") == "scu_search_action_button" and beschriftung in ANNEHMEN_TEXTE:
                m2 = bounds_zufall(k2.get("bounds", ""))
                m2_exakt = bounds_mitte(k2.get("bounds", ""))
                if m2 is not None and m2_exakt is not None and abs(m2_exakt[1] - mitte[1]) <= _TREFFER_BAND_PX:
                    knopf = m2
                    break
        zeilen.append({"name": name, "knopf": knopf})
    return zeilen


def annehmen_abarbeiten(auftrag: dict) -> None:
    """Genau eine eingegangene Anfrage annehmen: Add friends öffnen, die Zeile mit GENAU diesem Namen unter „Added Me“
    finden (bei „View more“ einmal aufklappen), „Accept“ tippen, prüfen. Jeder Zweifel → AuftragVerworfen VOR dem Tippen."""
    name = (auftrag.get("empfaenger") or "").strip()
    if not name:
        raise AuftragVerworfen("Annehmen ohne Namen verworfen.")
    if not geraet_verbunden():
        raise SnapHelferFehler("Handy nicht per USB verbunden.")
    bildschirm_aufwecken()
    app_zuruecksetzen()
    app_starten()
    _pause()
    _auf_oberflaeche_warten()
    _add_friends_oeffnen()
    dump = bildschirm_dump()
    zeilen = annehmen_zeilen(dump)
    if not [z for z in zeilen if _norm(z["name"]) == _norm(name) and z["knopf"]] and _MEHR_ZELLE in dump:
        mehr = element_finden(dump, resource_id=f"com.snapchat.android:id/{_MEHR_ZELLE}", zufall=True) \
            or element_finden(dump, resource_id=_MEHR_ZELLE, zufall=True)
        if mehr is not None:
            tippen(*mehr)
            _pause()
            dump = bildschirm_dump()
            zeilen = annehmen_zeilen(dump)
    if not zeilen:
        raise SnapHelferFehler("Abschnitt „Added Me“ nicht gefunden — Seite nicht geladen oder UI geändert.")
    passend = [z for z in zeilen if _norm(z["name"]) == _norm(name)]
    offen = [z["name"] for z in zeilen if z["knopf"]]
    if not passend:
        liste = ", ".join(offen) if offen else "keine"
        raise AuftragVerworfen(f"„{name}“ steht nicht unter „Added Me“. Offene Anfragen von: {liste} — nichts angetippt.")
    if len(passend) > 1:
        raise AuftragVerworfen(f"„{name}“ steht mehrfach unter „Added Me“ — nichts angetippt.")
    if passend[0]["knopf"] is None:
        raise AuftragVerworfen(f"{SCHON_BEFREUNDET}: bei „{name}“ gibt es keinen Accept-Knopf — nichts angetippt.")
    tippen(*passend[0]["knopf"])
    _pause()
    nachher = [z for z in annehmen_zeilen(bildschirm_dump()) if _norm(z["name"]) == _norm(name) and z["knopf"]]
    if nachher:
        raise SnapHelferFehler(f"Accept bei „{name}“ nicht bestätigt — neuer Versuch.")


def suchkandidaten(dump_xml: str, limit: int = SUCH_TREFFER_MAX) -> tuple[list[dict], int, str]:
    """(Kandidaten, ohne_knopf, hinweis) aus der Handy-Suche — pure Funktion, tippt NIE.
    Nur Zeilen unter „Add Friends“ mit Add-Knopf werden Kandidaten
    ({titel, name, grund}); Zeilen ohne Knopf zählen nur. Steht nur „From Contacts“
    da (SMS/WhatsApp-Einladung, niemand auf Snapchat), kommt der Hinweis dafür."""
    try:
        wurzel = ET.fromstring(dump_xml)
    except ET.ParseError:
        return [], 0, ""
    add_y = _sektion_y(dump_xml, _ADD_FRIENDS_TITEL)
    from_y = _sektion_y(dump_xml, _FROM_CONTACTS_TITEL)
    if add_y is None and from_y is not None:
        return [], 0, "nur Handy-Kontakte (SMS-Einladung), niemand auf Snapchat"
    kandidaten, gesehen, ohne = [], set(), 0
    for knoten in wurzel.iter("node"):
        if knoten.get("resource-id") != "result-title":
            continue
        titel = (knoten.get("text", "") or "").strip()
        mitte = bounds_mitte(knoten.get("bounds", ""))
        if not titel or mitte is None:
            continue
        if add_y is not None and mitte[1] <= add_y:
            continue
        y = mitte[1]
        name, grund = "", ""
        for k2 in wurzel.iter("node"):
            rid = k2.get("resource-id")
            if rid not in ("result-subtitle", "result-reason"):
                continue
            m2 = bounds_mitte(k2.get("bounds", ""))
            if m2 is None or abs(m2[1] - y) > _TREFFER_BAND_PX:
                continue
            if rid == "result-subtitle" and not name:
                name = (k2.get("text", "") or "").strip()
            elif rid == "result-reason" and not grund:
                grund = (k2.get("text", "") or "").strip()
        if hinzufuegen_knopf(dump_xml, y) is None:
            ohne += 1
            continue
        schluessel = _norm(name or titel)
        if not schluessel or schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        kandidaten.append({"titel": titel, "name": name or titel, "grund": grund})
        if len(kandidaten) >= limit:
            break
    return kandidaten, ohne, ""


def suchbegriffe(name: str, maximal: int = 3) -> list[str]:
    """Reihenfolge der Handy-Suchen: erst der Name wie genannt, dann (bei „Vor Nach“ oder
    „vor_nach“) die einzelnen Wörter — Snapchat-Namen sind oft nur der Vorname oder ein Spitzname.
    Tippt nie; die Wahl unter den Treffern trifft Eike."""
    ganz = " ".join((name or "").split())
    worte = [w for w in re.split(r"[\s_.\-]+", ganz) if len(w) >= 3]
    reihe = [ganz] + [w for w in worte if w.lower() != ganz.lower()]
    gesehen, aus = set(), []
    for b in reihe:
        if b.lower() not in gesehen:
            gesehen.add(b.lower())
            aus.append(b)
    return aus[:maximal]


def suchauftrag_abarbeiten(auftrag: dict) -> int:
    """Handy-Suche ohne Antippen (04.10.2026): suchen, Kandidaten in
    `suchergebnisse.json` ablegen, Anzahl zurück. Tippt nie auf Hinzufügen —
    die Wahl (1–5) trifft Eike, das Antippen erst `anfrage_abarbeiten`."""
    name = (auftrag.get("empfaenger") or "").strip()
    if not name:
        raise AuftragVerworfen("Suche ohne Namen verworfen.")
    kandidaten, ohne, hinweis = [], 0, ""
    for begriff in suchbegriffe(name):
        anfrage_suche_oeffnen()
        text_eingeben(begriff)          # ADB-Tastatur (Umlaute/Emoji), nie geraten
        kandidaten, ohne, hinweis = suchkandidaten(bildschirm_dump())
        if kandidaten:
            if begriff != name:
                hinweis = f"gefunden über „{begriff}“ (volle Suche nach „{name}“ blieb leer)"
            break
    alle = _lesen("suchergebnisse.json", {})
    alle = alle if isinstance(alle, dict) else {}
    alle[_norm(name)] = {"begriff": name, "zeit": time.time(), "kandidaten": kandidaten,
                         "ohne_knopf": ohne, "hinweis": hinweis}
    _schreiben("suchergebnisse.json", dict(list(alle.items())[-20:]))
    return len(kandidaten)


# ---------- Empfangen ----------

def _eingang_pruefen_ablauf(zuletzt_gesehen: str) -> tuple[list[str], str]:
    """Ein Baustein für sich — auch einzeln aufrufbar/testbar, nicht nur über `eingang_pruefen`."""
    _chat_tab_oeffnen()
    snaps_abholen()
    dump = _eike_chat_oeffnen()
    if dump is None:
        # Eikes Zeile ist gebündelt und ließ sich diesmal nicht auflösen — nichts Neues, kein Fehler:
        # `einmal()` liest trotzdem die anderen Kontakte, statt an Eikes Zeile stehen zu bleiben.
        return [], zuletzt_gesehen
    symbole_vermerken(dump)
    chat_bilder_holen(dump)
    sichtbar = fremde_texte(dump)
    vorher = _lesen("sichtbar.json", None)
    if isinstance(vorher, list):
        # Snapchat löscht gesehene Chats (am Gerät gesehen, 29.09.2026): was beim Öffnen im Chat
        # steht, ist neu — auch ein zweites, gleichlautendes „Hey“. Nur wenn die Liste um neue
        # Einträge WÄCHST, zählt allein der Zuwachs.
        if vorher and len(sichtbar) > len(vorher) and sichtbar[:len(vorher)] == vorher:
            neue = sichtbar[len(vorher):]
        else:
            neue = sichtbar
    else:
        neue = neue_nachrichten(dump, zuletzt_gesehen)
    neue = nicht_veraltet(dump, neue, None)
    _schreiben("sichtbar.json", sichtbar)
    zuletzt = neue[-1] if neue else zuletzt_gesehen
    return neue, zuletzt


def _zur_chatliste() -> None:
    """Aus einem offenen Chat zurück in die Chatliste (Zurück-Taste, bis Kontaktzeilen zu sehen sind)."""
    for _ in range(3):
        if chat_zeilen(bildschirm_dump()):
            return
        _adb("shell", "input", "keyevent", "4")
        _pause()
    if not chat_zeilen(bildschirm_dump()):
        raise SnapHelferFehler("Zurück in die Chatliste kam ich nicht.")


def _kontakt_mitlesen(anzeige: str, person: str, dump_xml: str, nur_zuwachs: bool = False) -> int:
    """Die Fremdtexte des offenen Chats von `anzeige`, die seit dem letzten Blick neu sind, gehen mit `person` in den Eingang; der
    Zustand des Kontakts wird nachgeführt (so liest ein wiederholter Ablauf nichts doppelt). Zurück: Zahl der neuen Texte."""
    freundschaft_vermerken(anzeige, einseitig(dump_xml))
    sichtbar = fremde_texte(dump_xml)
    z = kontakt_zustand(anzeige)
    neue = nicht_veraltet(dump_xml, neue_texte(sichtbar, z["sichtbar"], nur_zuwachs), person)
    if not sichtbar:
        try:      # Diagnose (Kontakt M, 09.10.2026: „2 new Chats“ geöffnet, nichts gelesen): den Bildschirm für die Analyse aufheben
            (_snapchat_ordner() / f"leerer_chat_{_norm(anzeige)[:20].replace(' ', '_')}.xml").write_text(dump_xml, encoding="utf-8")
        except OSError:
            pass
    if neue:
        _eingang_anhaengen([{"text": t, "art": "text"} for t in neue], person=person)
    z["sichtbar"] = sichtbar
    kontakt_zustand_schreiben(anzeige, z)
    return len(neue)


CHAT_HOCH_MAX = 3      # so oft höchstens nach oben scrollen, um Ungelesenes zu finden


def _chat_hochscrollen() -> None:
    breite, hoehe = bildschirmgroesse()
    _adb("shell", "input", "swipe", str(breite // 2), str(int(hoehe * 0.3)), str(breite // 2), str(int(hoehe * 0.7)), "300")
    _pause()


def _ungelesenes_suchen(dump: str) -> str:
    """Snapchat: War der Chat schon einmal offen (z. B. beim Senden), rutschen neue Nachrichten beim nächsten Öffnen nach oben
    aus dem Bild — man muss hochscrollen (Eike, 09.10.2026, Kontakt M). Zeigt der offene Chat keinen Fremdtext, wird bis
    `CHAT_HOCH_MAX`-mal hochgescrollt, bis einer erscheint. Zurück: der letzte Dump."""
    for _ in range(CHAT_HOCH_MAX):
        if fremde_texte(dump):
            break
        _chat_hochscrollen()
        dump = bildschirm_dump()
    return dump


def _offenen_kontakt_lesen(anzeige: str, person: str, dump: str) -> int:
    """Liest den BEREITS geöffneten Chat von `anzeige` (Kopf geprüft): Symbole, Bilder und Texte gehen
    mit `person` in den Eingang. Wie bei Eike bleibt der Chat für die Antwort der Wache offen."""
    dump = _ungelesenes_suchen(dump)
    symbole_vermerken(dump)
    anzahl = chat_bilder_holen(dump, kontakt=anzeige, person=person)
    neue_texte_anzahl = _kontakt_mitlesen(anzeige, person, dump)
    if neue_texte_anzahl and not heimat_aktiv() and _lesen("auftrag.json", None) is None:
        # Wie bei Eike: Der Chat bleibt offen, damit die Antwort der Wache ohne App-Neustart rausgeht.
        gespraech_halten([], fremde_texte(dump), kontakt=anzeige, person=person)
    return anzahl + neue_texte_anzahl


def kontakt_lesen(zeile: dict, person: str) -> int:
    """Liest EINEN Kontakt mit Neuem: erst ein Snap aus SEINER Zeile (sonst verbraucht der Tipp auf den Chat ihn), dann den Chat.
    Jeder Eintrag trägt `person`. Zurück: Zahl der neuen Einträge."""
    anzeige = zeile["anzeige"]
    anzahl = snaps_abholen(anzeige, person=person)
    dump = bildschirm_dump()
    zeilen = chat_zeilen_fuer(dump, anzeige)
    if len(zeilen) != 1 or not zeile_ist_neu(zeilen[0]["status"]):
        return anzahl        # der Snap war alles, oder die Zeile ist weg/mehrdeutig
    if neuer_snap_knoten(dump, anzeige) is None and "snap" in _norm(zeilen[0]["status"]):
        # Aggregierte Zeile („3 Snaps and Chats“, 02.10.2026 bei Kontakt C): Der Status nennt Snaps, aber kein
        # einzelner „New Snap“-Knoten ist tippbar — `snaps_abholen` fände nie etwas und `kontakt_chat_oeffnen`
        # verweigerte den Tipp für immer (keine Antwort, bis Eike die Zeile selbst öffnet). Ein Tipp öffnet
        # entweder den Betrachter (wird wie sonst abgeholt) oder gleich den Chat (Kopf und Gruppe geprüft).
        tippen(*zeilen[0]["mitte"])
        _pause()
        dump = bildschirm_dump()
        if snap_offen(dump):
            anzahl += snaps_abholen(anzeige, person=person)
            dump = bildschirm_dump()
        kopf = chat_kopfname(dump)
        if kopf is not None:
            if _norm(kopf) != _norm(anzeige):
                _zur_chatliste()   # falscher Chat (C1): nichts lesen, nichts senden
                return anzahl
            fremde = chat_fremde_labels(dump, anzeige)
            if fremde:
                raise SnapHelferFehler(f"Im Chat „{anzeige}“ schreiben auch {fremde} — vermutlich eine Gruppe mit gleichem Namen. Nichts gelesen.")
            return anzahl + _offenen_kontakt_lesen(anzeige, person, dump)
        _zur_chatliste()
        dump = bildschirm_dump()
        zeilen = chat_zeilen_fuer(dump, anzeige)
        if len(zeilen) != 1 or not zeile_ist_neu(zeilen[0]["status"]):
            return anzahl
    kontakt_chat_oeffnen(anzeige)
    return anzahl + _offenen_kontakt_lesen(anzeige, person, bildschirm_dump())


SEITEN_MAX = 3      # so viele Seiten der Chatliste je Durchgang (Heimat-Modus), 07.10.2026
ZEILEN_MAX = 25     # und so viele Kontakte höchstens


def braucht_weitere_seite(zeilen: list[dict]) -> bool:
    """Zeigt auch die unterste sichtbare Zeile Neues, könnte darunter noch mehr warten (die Liste ist nach Aktivität sortiert)."""
    return bool(zeilen) and zeile_ist_neu(zeilen[-1]["status"])


def _liste_wischen() -> None:
    breite, hoehe = bildschirmgroesse()
    _adb("shell", "input", "swipe", str(breite // 2), str(int(hoehe * 0.7)), str(breite // 2), str(int(hoehe * 0.3)), "400")
    _pause()


def _liste_nach_oben() -> None:
    breite, hoehe = bildschirmgroesse()
    for _ in range(SEITEN_MAX):
        _adb("shell", "input", "swipe", str(breite // 2), str(int(hoehe * 0.3)), str(breite // 2), str(int(hoehe * 0.8)), "250")
    _pause()


def _kontakte_lesen() -> None:
    """Nach Eike: alle anderen Kontakte der Liste (`kontakte.json`), bei denen die Chatliste Neues zeigt. Im Heimat-Modus bis zu
    `SEITEN_MAX` Seiten (Rückstau); jeder Name höchstens einmal je Durchgang (eine Zeile, die beim Lesen nach oben rutscht, wird nicht
    doppelt gelesen). Unbekannte Namen werden nur vermerkt und nie geöffnet. Ein Fehler bei einem Kontakt beendet nur diesen."""
    _zur_chatliste()
    gelesen: set[str] = set()
    gemeldet: set[str] = set()
    seite = 0
    for seite in range(SEITEN_MAX if heimat_aktiv() else 1):
        if seite:
            _liste_nach_oben()
            for _ in range(seite):
                _liste_wischen()
        dump = bildschirm_dump()
        zeilen = chat_zeilen(dump)
        bekannte, unbekannte = neue_kontakte(dump, erlaubte_kontakte())
        neu_unbekannt = [u for u in unbekannte if u not in gemeldet]
        if neu_unbekannt:
            unbekannte_vermerken(neu_unbekannt)
            gemeldet.update(neu_unbekannt)
        vorher = len(gelesen)
        for zeile, person in bekannte:
            n = _norm(zeile["anzeige"])
            if n in gelesen or len(gelesen) >= ZEILEN_MAX:
                continue
            gelesen.add(n)
            try:
                _zur_chatliste()
                kontakt_lesen(zeile, person)
            except SnapHelferFehler as exc:
                print(f"[snap-helfer] Kontakt „{zeile['anzeige']}“ nicht gelesen: {exc}", flush=True)
        if len(gelesen) >= ZEILEN_MAX or not braucht_weitere_seite(zeilen) or (seite and len(gelesen) == vorher):
            break
    if heimat_aktiv():
        # Aufräumen für den nächsten Auftrag: offenen Chat schließen und die Liste wieder nach oben (Review 07.10.2026: eine gescrollte
        # Liste ließ „Chat … steht nicht in der Chatliste“ melden, dann Neustart).
        try:
            _zur_chatliste()
            if seite > 0:
                _liste_nach_oben()
        except SnapHelferFehler:
            pass


def eingang_pruefen(zuletzt_gesehen: str, neustart: bool = True) -> tuple[list[str], str]:
    if not geraet_verbunden():
        raise SnapHelferFehler("Handy nicht per USB verbunden.")
    _app_bereit(neustart)
    return mit_neuversuch(_eingang_pruefen_ablauf, zuletzt_gesehen)


# ---------- Zustand & Dateiaustausch mit dem Dienst ----------

def _snapchat_ordner() -> Path:
    p = daten_ordner() / "snapchat"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _schreiben(name: str, daten) -> None:
    ziel = _snapchat_ordner() / name
    neu = ziel.with_suffix(".neu")
    neu.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    os.chmod(neu, 0o600)
    neu.replace(ziel)


def _lesen(name: str, standard):
    try:
        return json.loads((_snapchat_ordner() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return standard


def zustand_schreiben(daten: dict) -> None:
    _schreiben("zustand.json", daten)


def _konnektor_aktiv() -> bool:
    """Liest dieselbe Datei wie `kern.konnektoren` (`data/konnektoren.json`) roh — der Helfer läuft
    außerhalb des Containers und importiert `kern` nicht. Schaltet Eike den Konnektor aus, während noch
    ein Auftrag wartet oder eine Antwort ungelesen ist, darf der Helfer beides nicht mehr anstoßen."""
    try:
        d = json.loads((daten_ordner() / "konnektoren.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get("snapchat", {}).get("aktiv") is True


def _bild_pfad_aufloesen(relativ: str) -> Path:
    """Löst einen Bildpfad, der relativ zu daten_ordner() abgelegt wurde, gegen den ECHTEN
    Ordner auf dem Host auf — der Auftrag kommt aus dem Container, wo derselbe Datenordner
    unter /app/data hängt (docker-compose.yml), auf dem Host aber unter dem echten Pfad
    liegt. Ein Pfad, der aus dem Datenordner ausbricht, wird abgelehnt."""
    basis = daten_ordner().resolve()
    ziel = (basis / relativ).resolve()
    if not ziel.is_relative_to(basis):
        raise SnapHelferFehler(f"Bildpfad „{relativ}“ zeigt außerhalb des Datenordners.")
    return ziel


_BENACHRICHTIGUNGS_ZEIT_RE = re.compile(r"mPostedTimeMs=(\d+)")


def neue_benachrichtigung_da(zuletzt_gesehen_ms: int) -> tuple[bool, int]:
    """Günstiger Vor-Check OHNE die App zu öffnen: gibt es eine neue Snapchat-Nachrichten-
    Benachrichtigung? Dient als schneller Auslöser für `einmal()`, ersetzt `eingang_pruefen()`
    aber nicht — Snapchat zeigt den Nachrichtentext aus Datenschutzgründen nie in der
    Benachrichtigung, nur DASS etwas da ist (bestätigt am echten Gerät, 28.09.2026).

    Erkennungsmerkmal: `adb shell dumpsys notification` listet für Snapchat mehrere ständig
    vorhandene STILLE Sammel-Benachrichtigungen (Kanal "Silent…") — die sagen nichts. Eine
    ECHTE Nachricht erzeugt zusätzlich einen Eintrag im Kanal "Snaps and Chats" mit neuem
    Zeitstempel. Reiner Lesezugriff auf ein System-Diagnosewerkzeug (kein Netzwerkverkehr zu
    Snapchat) — braucht keine der sonstigen Vorsichtsmaßnahmen (Pausen, aktive Stunden)."""
    ergebnis = _adb("shell", "dumpsys", "notification", "--noredact")
    if ergebnis.returncode != 0:
        return False, zuletzt_gesehen_ms
    neuester = zuletzt_gesehen_ms
    gefunden = False
    for zeile in ergebnis.stdout.splitlines():
        if "com.snapchat.android" not in zeile or "mChannelName='Silent" in zeile:
            continue
        treffer = _BENACHRICHTIGUNGS_ZEIT_RE.search(zeile)
        if not treffer:
            continue
        zeitstempel = int(treffer.group(1))
        if zeitstempel > zuletzt_gesehen_ms:
            gefunden = True
            neuester = max(neuester, zeitstempel)
    return gefunden, neuester


# ---------- Gesprächsmodus ----------
# Nach dem Lesen bleibt der Chat offen: Die Antwort wird direkt dort getippt (kein App-Neustart,
# Friday bleibt „online“) und Folgenachrichten erscheinen live im offenen Chat.

GESPRAECH_WARTE_S = 120        # so lange wartet der offene Chat auf die Antwort der Wache
GESPRAECH_NACHLAUF_S = 60      # so lange nach einer Antwort noch auf eine Folgenachricht
GESPRAECH_TAKT_S = 2.0
HEIMAT_WARTE_S = 30            # Heimat-Modus: so lange wartet Eikes offener Chat auf Fridays Antwort (statt 120 s)
HEIMAT_NACHLAUF_S = 20         # und so lange nach der Antwort auf eine Folgenachricht (statt 60 s)


def _im_offenen_chat_senden(text: str) -> None:
    """Tippt und sendet im schon offenen Chat und prüft, dass der Text dort steht."""
    schritt_ausfuehren("texteingabe")
    text_eingeben(text)
    _pause()
    chat_senden()
    time.sleep(2.0)
    if not eigener_text_sichtbar(bildschirm_dump(), text):
        raise SendenUnbestaetigt("Gesendeter Text steht nicht im Chat - Senden nicht bestätigt.")


def _neu_im_chat(sichtbar: list[str], bekannt: list[str]) -> list[str]:
    """Was in `sichtbar` über `bekannt` hinaus dazugekommen ist (Vielfachheit zählt)."""
    rest = list(bekannt)
    neu = []
    for t in sichtbar:
        if t in rest:
            rest.remove(t)
        else:
            neu.append(t)
    return neu


def _naechsten_nachruecken() -> None:
    """Der Platz `auftrag.json` ist frei: der älteste wartende Auftrag der Schlange rückt dorthin. Der Dienst legt
    neue Aufträge auf den freien Platz oder, wenn er belegt ist, in `warteschlange/` — der Helfer arbeitet einen
    nach dem anderen ab. Der Platz wird exklusiv angelegt: Ein Dienst, der im selben Moment schreibt, wird nie
    überschrieben."""
    schlange = _snapchat_ordner() / "warteschlange"
    if not schlange.is_dir():
        return
    for datei in sorted(schlange.glob("*.json")):
        try:
            inhalt = datei.read_text(encoding="utf-8")
            json.loads(inhalt)
        except (OSError, ValueError):
            datei.unlink(missing_ok=True)   # kaputt: nicht ewig den Platz blockieren
            continue
        try:
            fd = os.open(_snapchat_ordner() / "auftrag.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return                           # der Platz ist schon wieder belegt
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(inhalt)
        datei.unlink(missing_ok=True)
        return


ERGEBNISSE_MAX = 500


def ergebnis_protokollieren(auftrag: dict, ergebnis: str, grund: str = "") -> None:
    """Ein Auftrag ist erledigt: `gesendet`, `verworfen` oder `aufgegeben`. Der Zustand nennt nur den letzten Fehler des letzten Laufs
    — ein verworfener Auftrag sah danach aus wie ein gesendeter. Nie der Text, nur seine Länge; höchstens `ERGEBNISSE_MAX` Zeilen."""
    zeile = {"zeit": time.time(), "id": auftrag.get("id"), "art": auftrag.get("art"), "empfaenger": auftrag.get("empfaenger"),
             "person": auftrag.get("person"), "ergebnis": ergebnis, "grund": str(grund)[:200],
             "gelegt": auftrag.get("gelegt"), "ausloeser": auftrag.get("ausloeser"),
             **_zeiten_ende(),
             "laenge": len(auftrag.get("text") or "")}
    try:
        datei = _snapchat_ordner() / "ergebnisse.jsonl"
        alt = datei.read_text(encoding="utf-8").splitlines() if datei.exists() else []
        alt = (alt + [json.dumps(zeile, ensure_ascii=False)])[-ERGEBNISSE_MAX:]
        datei.write_text("\n".join(alt) + "\n", encoding="utf-8")
    except OSError as exc:   # das Protokoll ist Zugabe: Es darf nie einen Auftrag aufhalten
        print(f"[snap-helfer] Ergebnis nicht protokolliert: {exc}", flush=True)


GESENDET_DATEI = "gesendet_ids.json"


def _id_gesendet(auftrag_id: str) -> bool:
    """Wurde ein Auftrag mit dieser ID schon gesendet? (07.10.2026: Bricht der Helfer nach dem Senden, vor dem Löschen des
    Auftrags ab, liegt der Auftrag nach dem Neustart wieder da — er darf nicht ein zweites Mal raus.)"""
    return bool(auftrag_id) and auftrag_id in (_lesen(GESENDET_DATEI, []) or [])


def _id_vermerken(auftrag_id: str) -> None:
    if not auftrag_id:
        return
    liste = [i for i in (_lesen(GESENDET_DATEI, []) or []) if i != auftrag_id]
    _schreiben(GESENDET_DATEI, (liste + [auftrag_id])[-200:])


def _auftrag_erledigt() -> None:
    """Der laufende Auftrag ist fertig (oder aufgegeben): Platz freigeben, nächsten nachrücken lassen."""
    (_snapchat_ordner() / "auftrag.json").unlink(missing_ok=True)
    _naechsten_nachruecken()


def _falscher_chat(dump_xml: str, kontakt: str) -> bool:
    """Steht im Kopf des offenen Chats ein anderer Name als `kontakt`? (Kein erkennbarer Kopf zählt nicht als falsch.)"""
    kopf = chat_kopfname(dump_xml)
    return kopf is not None and _norm(kopf) != _norm(kontakt)


def gespraech_halten(eigene_texte: list[str], bekannt: list[str], jetzt_fn=time.time,
                     kontakt: str | None = None, person: str | None = None,
                     warte_s: float = GESPRAECH_WARTE_S, nachlauf_s: float = GESPRAECH_NACHLAUF_S) -> None:
    """Hält den offenen Chat, solange ein Gespräch läuft. Ändert `eigene_texte` und schreibt
    Folgenachrichten in den Eingang. Jeder Fehler beendet nur den Gesprächsmodus — ein liegender
    Auftrag bleibt dann für den normalen Weg (App neu starten) erhalten.
    `kontakt`/`person`: der offene Chat gehört diesem Kontakt (Standard Eike). Bedient wird nur ein Auftrag mit genau diesem
    Empfänger UND dieser Person; Folgenachrichten gehen mit `person` in den Eingang."""
    kontakt = kontakt or eike_name()
    ist_eike = _norm(kontakt) == _norm(eike_name())
    _adb("shell", "svc", "power", "stayon", "usb")  # Bildschirm bleibt an, solange das Gespräch läuft
    frist = jetzt_fn() + warte_s
    try:
        while jetzt_fn() < frist:
            auftrag = _lesen("auftrag.json", None)
            if auftrag is not None:
                if (auftrag.get("art") != "text" or auftrag.get("bild_pfad")
                        or _norm(auftrag.get("empfaenger", "")) != _norm(kontakt)
                        or auftrag.get("person") != person):
                    return  # Bild-Snaps und Aufträge an andere Kontakte gehen über den normalen Weg
                if _falscher_chat(bildschirm_dump(), kontakt):
                    return  # ein anderer Chat ist offen: nichts senden, der normale Weg öffnet den richtigen
                if _id_gesendet(auftrag.get("id", "")):
                    # Schon gesendet (Absturz vor dem Löschen): nur aufräumen, nie ein zweites Mal senden.
                    _auftrag_erledigt()
                    ergebnis_protokollieren(auftrag, "gesendet", "bereits gesendet (Neustart)")
                    continue
                try:
                    _im_offenen_chat_senden(auftrag.get("text", ""))
                except SendenUnbestaetigt as exc:
                    # Gedrückt, aber nicht gesehen: nie erneut tippen (der alte Weg ließ den Auftrag liegen und sendete ihn ein
                    # zweites Mal). ID vermerken, ehrlich protokollieren, App neu starten, damit kein Entwurf stehenbleibt.
                    _id_vermerken(auftrag.get("id", ""))
                    ergebnis_protokollieren(auftrag, "unbestaetigt", str(exc))
                    _auftrag_erledigt()
                    print(f"[snap-helfer] Gesprächsmodus: {exc}", flush=True)
                    try:
                        app_zuruecksetzen()
                        app_starten()
                    except SnapHelferFehler:
                        pass
                    return
                _id_vermerken(auftrag.get("id", ""))      # sofort nach dem Senden, vor allem anderen
                _auftrag_erledigt()
                ergebnis_protokollieren(auftrag, "gesendet")
                eigene_texte.append(auftrag.get("text", ""))
                del eigene_texte[:-20]
                frist = jetzt_fn() + nachlauf_s
                bekannt = fremde_texte(bildschirm_dump())
                continue
            dump = bildschirm_dump()
            if _falscher_chat(dump, kontakt):
                return  # nicht der erwartete Chat: nichts lesen, nichts zuschreiben
            if any(k.get("text") == "snap_envelop" for k in ET.fromstring(dump).iter("node")):
                return  # ein neuer Snap wartet: der nächste Lauf holt ihn aus der Chatliste
            sichtbar = fremde_texte(dump)
            neu = [t for t in _neu_im_chat(sichtbar, bekannt) if t not in eigene_texte]
            if neu:
                eingang = _lesen("eingang.json", [])
                jetzt = jetzt_fn()
                for text in neu:
                    eingang.append({"id": f"{int(jetzt * 1000)}-{len(eingang)}", "text": text,
                                    "art": "text", "zeit": jetzt, "gelesen": False,
                                    **({"person": person} if person else {})})
                _schreiben("eingang.json", eingang[-200:])
                if ist_eike:
                    _schreiben("sichtbar.json", sichtbar)
                else:
                    z = kontakt_zustand(kontakt)
                    z["sichtbar"] = sichtbar
                    kontakt_zustand_schreiben(kontakt, z)
                bekannt = sichtbar
                frist = max(frist, jetzt + warte_s)  # neue Frage: wieder auf die Antwort warten
            time.sleep(GESPRAECH_TAKT_S)
    except (SnapHelferFehler, ET.ParseError) as exc:
        print(f"[snap-helfer] Gesprächsmodus beendet: {exc}", flush=True)
    finally:
        _adb("shell", "svc", "power", "stayon", "false")


def _texte_in_eingang(neue: list[str], jetzt: float) -> None:
    """Eikes Texte an den Eingang hängen (die Wache beantwortet sie)."""
    eingang = _lesen("eingang.json", [])
    for text in neue:
        eingang.append({"id": f"{int(jetzt * 1000)}-{len(eingang)}", "text": text,
                        "art": "text", "zeit": jetzt, "gelesen": False})
    _schreiben("eingang.json", eingang[-200:])


def einmal(nur_auftraege: bool = False) -> bool:
    """Ein Durchgang. Zurück: False, wenn das Handy/Snapchat dabei klemmte (Schutzschalter zählt das), sonst True."""
    if not _konnektor_aktiv():
        return True
    ok = True
    heimat = heimat_aktiv()
    _zeiten_start()
    jetzt = time.time()
    zustand = _lesen("zustand.json", {})
    if _lesen("auftrag.json", None) is None:
        _naechsten_nachruecken()
    auftrag = _lesen("auftrag.json", None)
    fehler = None
    eigene_texte = list(zustand.get("eigene_texte", []))
    _beim_senden_gesehen.clear()
    mitgelesen: list[str] = []

    if not geraet_verbunden():
        zustand_schreiben({"verbunden": False, "zeit": jetzt,
                           "letzter_fehler": "Handy nicht per USB verbunden.",
                           "zuletzt_gesehen": zustand.get("zuletzt_gesehen", ""),
                           "eigene_texte": eigene_texte})
        return False

    try:
        if auftrag is not None:
            try:
                bild_pfad = auftrag.get("bild_pfad")
                lokaler_bild_pfad = str(_bild_pfad_aufloesen(bild_pfad)) if bild_pfad else None
                gesendeter_text = auftrag.get("text", "")
                empfaenger = auftrag.get("empfaenger")
                if not isinstance(empfaenger, str) or not empfaenger.strip():
                    raise AuftragVerworfen("Auftrag ohne Empfänger verworfen.")
                if auftrag.get("art") == "suche":
                    # Handy-Suche: nur auflisten, NIE antippen — die Wahl trifft Eike.
                    anzahl = suchauftrag_abarbeiten(auftrag)
                    mitgelesen = []
                    _auftrag_erledigt()
                    ergebnis_protokollieren(auftrag, "gesendet", f"{anzahl} Treffer")
                elif auftrag.get("art") == "stand":
                    # Nur ansehen: Chat öffnen, Freundschaftsstand vermerken — schreibt nichts.
                    stand = stand_pruefen(empfaenger)
                    mitgelesen = []
                    _auftrag_erledigt()
                    ergebnis_protokollieren(auftrag, "gesendet", stand)
                elif auftrag.get("art") == "annehmen":
                    # Eingegangene Anfrage annehmen — Eikes Bitte ist die Freigabe; getippt wird nur bei genau einer Zeile.
                    annehmen_abarbeiten(auftrag)
                    mitgelesen = []
                    _auftrag_erledigt()
                    ergebnis_protokollieren(auftrag, "gesendet")
                elif auftrag.get("art") == "anfrage":
                    # Kontakt-Anfrage: Empfänger steht gerade NICHT in der Kontaktliste
                    # (darum geht es) — eigene Prüfung im Ablauf statt erlaubte_kontakte.
                    anfrage_abarbeiten(auftrag)
                    mitgelesen = []
                    _auftrag_erledigt()
                    ergebnis_protokollieren(auftrag, "gesendet")
                else:
                    kontakte = erlaubte_kontakte()
                    if _norm(empfaenger) not in kontakte:
                        raise AuftragVerworfen(f"Empfänger „{empfaenger}“ steht nicht in der Kontaktliste — Auftrag verworfen.")
                    if auftrag.get("person") != kontakte[_norm(empfaenger)]:
                        raise AuftragVerworfen(f"Person und Empfänger „{empfaenger}“ passen nicht zusammen — Auftrag verworfen.")
                    if _id_gesendet(auftrag.get("id", "")):
                        # Schon gesendet (Absturz vor dem Löschen): nur aufräumen, nie ein zweites Mal senden.
                        _auftrag_erledigt()
                        ergebnis_protokollieren(auftrag, "gesendet", "bereits gesendet (Neustart)")
                        mitgelesen = []
                    else:
                        snap_senden(lokaler_bild_pfad, gesendeter_text, empfaenger,
                                    **({"neustart": False} if heimat and not lokaler_bild_pfad else {}))   # Bild-Ablauf braucht die Kamera
                        _id_vermerken(auftrag.get("id", ""))       # sofort nach dem Senden, vor allem anderen
                        mitgelesen = [t for t in dict.fromkeys(_beim_senden_gesehen) if t not in eigene_texte]
                        _auftrag_erledigt()
                        ergebnis_protokollieren(auftrag, "gesendet")
                    if bild_pfad is None and gesendeter_text and _norm(empfaenger) == _norm(eike_name()):
                        # Chat-Text: landet gleich in der eigenen Chat-Historie — nicht als
                        # Eikes Antwort missverstehen (Selbstgespräch, falsche Zustimmung).
                        eigene_texte.append(gesendeter_text)
                        eigene_texte = eigene_texte[-20:]
            except SendenUnbestaetigt as exc:
                # Gedrückt, aber nicht gesehen: nie erneut tippen. ID vermerken, Auftrag beenden, ehrlich protokollieren.
                fehler = str(exc)
                _id_vermerken(auftrag.get("id", ""))
                ergebnis_protokollieren(auftrag, "unbestaetigt", fehler)
                _auftrag_erledigt()
                try:       # ein womöglich stehengebliebener Entwurf im Eingabefeld darf nicht mit dem nächsten Text zusammen rausgehen
                    app_zuruecksetzen()
                    app_starten()
                except SnapHelferFehler:
                    pass
            except AuftragVerworfen as exc:
                fehler = str(exc)
                ergebnis_protokollieren(auftrag, "verworfen", fehler)
                _auftrag_erledigt()          # weg damit — und der nächste rückt auf, ohne dass hier etwas überschrieben wird
            except SnapHelferFehler as exc:
                ok = False
                fehler = str(exc)
                versuche = int(auftrag.get("versuche", 0)) + 1
                if versuche >= MAX_VERSUCHE:
                    # Nicht ewig die App neu starten: nach ein paar Fehlschlägen aufgeben und es sagen.
                    _auftrag_erledigt()
                    fehler = f"Antwort nach {versuche} Versuchen aufgegeben: {exc}"
                    ergebnis_protokollieren(auftrag, "aufgegeben", fehler)
                else:
                    if _lesen("auftrag.json", None) is not None:   # ein verworfener Auftrag wird nicht neu geschrieben
                        _schreiben("auftrag.json", {**auftrag, "versuche": versuche})

        if nur_auftraege:
            # Schnelle Auftragsschleife: fertige Antworten zuerst, gelesen wird im nächsten Durchgang. Was beim Öffnen des Chats
            # zum Senden schon zu sehen war, muss trotzdem in den Eingang (Snapchat löscht Gesehenes) — Review 07.10.2026.
            if auftrag is not None:
                if mitgelesen:
                    _texte_in_eingang(mitgelesen, jetzt)
                zustand_schreiben({"verbunden": True, "zeit": jetzt, "letzter_fehler": fehler,
                                   "zuletzt_gesehen": zustand.get("zuletzt_gesehen", ""), "eigene_texte": eigene_texte})
            return ok
        try:
            neue, zuletzt = eingang_pruefen(zustand.get("zuletzt_gesehen", ""), **({"neustart": False} if heimat else {}))
            neue = mitgelesen + [t for t in neue if t not in eigene_texte]
            if neue:
                _texte_in_eingang(neue, jetzt)
            zustand_schreiben({"verbunden": True, "zeit": jetzt, "letzter_fehler": fehler,
                               "zuletzt_gesehen": zuletzt, "eigene_texte": eigene_texte})
            if neue and _lesen("auftrag.json", None) is None:
                # Eikes Chat bleibt kurz offen und beantwortet ihn gleich (Live-Befund 07.10.2026: ohne das 85–104 s); im Heimat-Modus
                # begrenzt, damit die anderen Kontakte nicht lange warten.
                gespraech_halten(eigene_texte, fremde_texte(bildschirm_dump()),
                                 **({"warte_s": HEIMAT_WARTE_S, "nachlauf_s": HEIMAT_NACHLAUF_S} if heimat else {}))
                zustand_schreiben({"verbunden": True, "zeit": jetzt, "letzter_fehler": fehler,
                                   "zuletzt_gesehen": zuletzt, "eigene_texte": eigene_texte})
            # Erst jetzt die anderen Kontakte: Eikes Texte stehen im Eingang (Snapchat löscht Gelesenes), sein Gespräch ist zu Ende,
            # und kein Chat eines Kontakts kann in Eikes Gesprächsmodus offen bleiben (Review 4c, C1/I1). Jeder Fehler hier
            # kostet nichts mehr.
            try:
                _kontakte_lesen()
            except Exception as exc:  # noqa: BLE001 — Eikes Weg ist fertig und gesichert
                print(f"[snap-helfer] Andere Kontakte nicht gelesen: {type(exc).__name__}: {exc}", flush=True)
        except SnapHelferFehler as exc:
            ok = False
            zustand_schreiben({"verbunden": False, "zeit": jetzt, "letzter_fehler": fehler or str(exc),
                               "zuletzt_gesehen": zustand.get("zuletzt_gesehen", ""),
                               "eigene_texte": eigene_texte})
    finally:
        # Der Bildschirm muss nicht an bleiben, bis der nächste Lauf (bis zu TAKT_S später) beginnt.
        bildschirm_schlafen()
    return ok


# ---------- Dauerschleife ----------
# Muster wie kern/dienste/whatsapp.py: aktive Stunden, gestreuter Takt für den vollen Lauf
# (öffnet die App) — normale Vorsicht, keine gezielte Tarnung. Der Benachrichtigungs-Vor-Check
# selbst rührt Snapchat nicht an und läuft deshalb deutlich öfter (siehe neue_benachrichtigung_da).

from datetime import datetime, time as tageszeit  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")   # hphome läuft in UTC: Ohne das galten „07–23 Uhr“ faktisch 09–01 Uhr MESZ

WACH_AB = tageszeit(7, 0)
WACH_BIS = tageszeit(23, 0)
TAKT_S = 900              # spätestens nach so vielen Sekunden ein voller Lauf, auch ohne Auslöser
STREUUNG_S = 300
MAX_VERSUCHE = 4          # so oft darf ein Sendeauftrag scheitern, bevor er verworfen wird
BENACHRICHTIGUNGS_TAKT_S = 10  # so oft wird günstig auf eine neue Benachrichtigung geprüft


def rund_um_die_uhr() -> bool:
    """Will Eike den Helfer auch nachts (Freigaben-Seite, `wachzeiten.json` — schreibt der Container,
    Standard ist Ruhe 23–7 Uhr)? Dann gelten keine aktiven Stunden."""
    d = _lesen("wachzeiten.json", {})
    return bool(isinstance(d, dict) and d.get("rund_um_die_uhr"))


def ist_wach(jetzt: datetime) -> bool:
    """Liegt `jetzt` (Berliner Zeit!) in den aktiven Stunden? Eine Zeit ohne Zone gilt als Berliner Zeit."""
    if rund_um_die_uhr():
        return True
    if jetzt.tzinfo is not None:
        jetzt = jetzt.astimezone(BERLIN)
    return WACH_AB <= jetzt.time() <= WACH_BIS


def auftrag_wartet() -> bool:
    """Liegt ein Sendeauftrag der Wache bereit? Dann nicht bis zum nächsten Takt warten
    (bis zu 15 Minuten) — die Antwort soll zeitnah raus (29.09.2026: sie blieb sonst liegen)."""
    if _lesen("auftrag.json", None) is not None:
        return True
    schlange = _snapchat_ordner() / "warteschlange"
    return schlange.is_dir() and any(schlange.glob("*.json"))


HEIMAT_LESEN_NACH_S = 90   # spätestens nach so vielen Sekunden Auftragsschleife wird wieder gelesen (Eike, Snaps, Kontakte)
HEIMAT_TAKT_S = 3          # im Heimat-Modus so oft nachsehen, solange eine Antwort der Wache erwartet wird


def soll_schnell(heimat: bool, auftrag_liegt: bool, konnektor_an: bool, jetzt: float, letzter_voller_lauf: float) -> bool:
    """Arbeitet `lauf` jetzt die schnelle Auftragsschleife ab? Nur im Heimat-Modus mit wartendem Auftrag und eingeschaltetem Konnektor
    (sonst Leerlauf-Hämmern), und nie, wenn zu lange nicht gelesen wurde: Beim Senden an Kontakte kommen deren Antworten nach, die
    Schlange würde sonst nie leer und Eike bliebe ungelesen (Review 07.10.2026)."""
    return heimat and auftrag_liegt and konnektor_an and (jetzt - letzter_voller_lauf) < HEIMAT_LESEN_NACH_S


def _antwort_erwartet() -> bool:
    """Liegt gelesener Eingang, den die Wache noch nicht beantwortet hat? Dann bald wieder nach dem fertigen Auftrag sehen."""
    e = _lesen("eingang.json", [])
    return isinstance(e, list) and any(isinstance(x, dict) and not x.get("gelesen") for x in e)


FEHLER_MAX = 5
PAUSE_FEHLER_S = 600


class Schutzschalter:
    """Nach `FEHLER_MAX` Fehlern in Folge pausiert der Helfer `PAUSE_FEHLER_S` Sekunden (07.10.2026): Ein klemmendes Handy soll nicht
    im Sekundentakt dieselbe Nachricht versuchen oder Snapchat reizen. Danach ein frischer Zähler."""

    def __init__(self) -> None:
        self._serie = 0
        self._bis = 0.0

    def fehler(self, jetzt: float) -> None:
        self._serie += 1
        if self._serie >= FEHLER_MAX:
            self._bis = jetzt + PAUSE_FEHLER_S
            self._serie = 0

    def erfolg(self) -> None:
        self._serie = 0

    def pausiert(self, jetzt: float) -> bool:
        return jetzt < self._bis

    @property
    def bis(self) -> float:
        return self._bis


def schutz_nach_lauf(schutz: Schutzschalter, jetzt: float, fehlgeschlagen: bool) -> None:
    """Wertet einen Durchgang aus und vermerkt `pausiert_bis` im Zustand (der Dienst sagt dann ehrlich „kurze Pause“)."""
    if fehlgeschlagen:
        schutz.fehler(jetzt)
    else:
        schutz.erfolg()
    bis = schutz.bis if schutz.pausiert(jetzt) else 0
    z = _lesen("zustand.json", {})
    if not isinstance(z, dict):
        z = {}
    if z.get("pausiert_bis", 0) != bis:
        zustand_schreiben({**z, "pausiert_bis": bis})


FERNSTEUERUNG_FRISCH_S = 15.0    # so lange gilt der Herzschlag der Handy-Steuerung (sie meldet sich alle 5 s)


def fernsteuerung_aktiv() -> bool:
    """Steuert Eike das Handy gerade von Hand (Seite `handy_steuerung`)? Dann fasst der Helfer es nicht an. Der Dienst schreibt
    alle 5 s `data/snapchat/handy_steuerung.json` ({"ts": …}); bleibt der Herzschlag aus (Tab zu, Absturz), läuft der Helfer von
    selbst weiter. Eine kaputte Datei oder eine Zeit in der Zukunft pausiert nie."""
    try:
        ts = float(json.loads((_snapchat_ordner() / "handy_steuerung.json").read_text(encoding="utf-8"))["ts"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return 0 <= time.time() - ts <= FERNSTEUERUNG_FRISCH_S


def lauf() -> None:
    print(f"[snap-helfer] läuft, Daten unter {_snapchat_ordner()}", flush=True)
    zuletzt_benachrichtigung_ms = 0
    naechster_voller_lauf = 0.0
    schutz = Schutzschalter()
    letzter_voller_lauf = time.time()
    while True:
        if fernsteuerung_aktiv():   # Eike bedient das Handy selbst: nichts anfassen, Aufträge bleiben in der Schlange
            time.sleep(2)
            continue
        if ist_wach(datetime.now(BERLIN)):
            if schutz.pausiert(time.time()):
                time.sleep(BENACHRICHTIGUNGS_TAKT_S)
                continue
            benachrichtigung_da = False
            try:
                benachrichtigung_da, zuletzt_benachrichtigung_ms = neue_benachrichtigung_da(
                    zuletzt_benachrichtigung_ms)
            except Exception as exc:
                print(f"[snap-helfer] Benachrichtigungs-Check fehlgeschlagen: "
                     f"{type(exc).__name__}: {exc}", flush=True)
            if soll_schnell(heimat_aktiv(), auftrag_wartet(), _konnektor_aktiv(), time.time(), letzter_voller_lauf):
                fehlgeschlagen = False
                try:
                    fehlgeschlagen = einmal(nur_auftraege=True) is False   # fertige Antworten zuerst, ohne Lesen, ohne Neustart
                except Exception as exc:  # ein Fehler darf den Lauf nie töten
                    fehlgeschlagen = True
                    print(f"[snap-helfer] Auftragslauf: {type(exc).__name__}: {exc}", flush=True)
                schutz_nach_lauf(schutz, time.time(), fehlgeschlagen)
                time.sleep(0.5)
                continue
            if benachrichtigung_da or auftrag_wartet() or time.time() >= naechster_voller_lauf:
                fehlgeschlagen = False
                letzter_voller_lauf = time.time()
                try:
                    fehlgeschlagen = einmal() is False
                except Exception as exc:  # ein Fehler darf den Lauf nie töten
                    fehlgeschlagen = True
                    print(f"[snap-helfer] unerwarteter Fehler: {type(exc).__name__}: {exc}", flush=True)
                schutz_nach_lauf(schutz, time.time(), fehlgeschlagen)
                naechster_voller_lauf = time.time() + max(60.0, TAKT_S + random.uniform(-STREUUNG_S, STREUUNG_S))
        time.sleep(HEIMAT_TAKT_S if heimat_aktiv() and _antwort_erwartet() else BENACHRICHTIGUNGS_TAKT_S)


if __name__ == "__main__":
    os.umask(0o077)
    lauf()
