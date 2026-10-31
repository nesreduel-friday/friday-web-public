"""Heikle Inhalte, die Friday nie ungefragt an Dritte schickt (Spec 07.10.2026, Punkt 1.4). Bewusst grob und
vorsichtig: im Zweifel fragt Friday Eike einmal nach, statt etwas Unwiderrufliches zu senden."""
import re

_MUSTER = [
    (re.compile(r"\b(passwort|kennwort|password)\b[^\n]{0,12}?(\bist\b|\blautet\b|:|=)", re.I), "Zugangsdaten"),
    (re.compile(r"\b(pin|tan|cvv|cvc)\b\s*(?:ist|lautet|:|=)?\s*\d{3,8}\b", re.I), "Zugangsdaten"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?:\s?\d{4}){3,7}(?:\s?\d{1,4})?\b"), "eine IBAN"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "eine Kartennummer"),
    (re.compile(r"\b(code|kennung)\b[^\n]{0,30}\b\d{4,8}\b", re.I), "einen Sicherheitscode"),
    (re.compile(r",\s*\d{5}\s+[A-ZÄÖÜ][a-zäöüß]+"), "eine Adresse"),
    # Straße mit Hausnummer; „Steintorplatz 17 Uhr“ / „Heimweg 20 Minuten“ sind keine Adressen.
    (re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]+\s?(?:straße|strasse|str\.|weg|allee|platz|gasse)\s+\d+\b"
                r"(?!\s*(?:uhr|min|minuten|stunden|sekunden|km|m)\b)", re.I), "eine Adresse"),
    (re.compile(r"(?:\+49|\b0)\s?1\d{2}[\s/-]?\d{6,8}\b"), "eine Telefonnummer"),
    (re.compile(r"\b0[2-9]\d{1,4}[\s/-]?\d{4,8}\b"), "eine Telefonnummer"),
]


def heikel(text: str) -> str | None:
    for muster, was in _MUSTER:
        if muster.search(text or ""):
            return (f"Der Text enthält {was} — so etwas schicke ich nicht ohne dein ausdrückliches Ja. "
                    "Sag mir, ob ich es trotzdem senden soll.")
    return None


# Hat Eike in DIESEM Zug selbst etwas Sendeartiges gesagt? Fremdtext kann Eikes Wort nicht schreiben, aber das Modell
# kann einen Versand „von sich aus“ anstoßen (aus dem Verlauf, einem Gedanken, einer früheren Antwort). Bewusst weit bei
# den Verben, hart bei der Verneinung: im Zweifel fragt Friday einmal nach, statt unwiderruflich zu senden.
_VERB = re.compile(r"\b(schick|send|schreib|text\b|texte|simse|chatte|snap|sag|teil|gib|geb|meld|richte|leite|"
                   r"informier|benachrichtig|bescheid|ausricht|weitergeb|weiterleit|frag|ruf|kontaktier|gr(?:ü|ue)ß|gruss|"
                   r"antworte|erz(?:ä|ae)hl|zeig|(?:ü|ue)bermittel|(?:ü|ue)berbring|nachricht|lass\w*\s+(?:\w+\s+){0,3}wissen|"
                   r"tell|message|show)", re.I)
_VERNEINT = re.compile(r"\b(nein|nee|ne|n(?:ö|oe)+|nope|no|nicht|nich|net|ned|nichts|nix|kein\w*|nie|niemals|niemand\w*|"
                       r"nirgend\w*|weder|stopp?|abbrechen|halt|vergiss\w*|verwirf\w*|unterlass\w*|warte|"
                       r"h(?:ö|oe)r\s+auf|l(?:ä|ae)sst?\s+(?:es|das)|lass\s+(?:es|das|mal)|doch\s+nicht)\b", re.I)


def auftrag_erkannt(eingabe: str, zustimmung: bool = False) -> bool:
    """Gibt Eikes Äußerung in diesem Zug einen Sendeauftrag her? Ja, wenn er ein Sendeverb sagt (ohne Verneinung) oder
    gerade ausdrücklich zugestimmt hat (`zustimmung` setzt der Kern, nach Fremdtext nie) — etwa auf Fridays Rückfrage."""
    text = (eingabe or "").strip()
    if not text:
        return False
    if zustimmung:
        return True
    return bool(_VERB.search(text)) and not _VERNEINT.search(text)


_ANNEHMEN = re.compile(r"\b(nimm|nehm\w*|annehm\w*|angenommen|akzeptier\w*|best(?:ä|ae)tig\w*|accept\w*|"
                       r"add\w*|hinzuf(?:ü|ue)g\w*)\b", re.I)


def annahme_erkannt(eingabe: str, zustimmung: bool = False) -> bool:
    """Hat Eike in diesem Zug verlangt, eine Kontaktanfrage ANZUNEHMEN? Eine Frage („hat Tjard dir eine Anfrage
    geschickt?“) ist keine Freigabe (18.10.2026: Friday nahm auf diese Frage hin Tjard an, unwiderruflich)."""
    text = (eingabe or "").strip()
    if not text:
        return False
    if zustimmung:
        return True
    return bool(_ANNEHMEN.search(text)) and not _VERNEINT.search(text)


# Sucht/beauftragt Eike in DIESEM Zug wirklich jemanden auf Snapchat? Eine bloße Erwähnung des Namens
# („Titus hat erzählt …“) ist kein Auftrag für eine Handy-Suche (30.10.2026). Dieselbe Sperre wie beim
# Annehmen: der Server wertet Eikes Worte aus, nicht das Modell.
_ANFRAGE = re.compile(r"\b(anfrag\w*|kontaktier\w*|hinzuf(?:ü|ue)g\w*|hinzu|freundschaftsanfrage\w*|add\w*|"
                      r"f(?:ü|ue)g\w*|such\w*|find\w*|frag\w*\s+(?:\w+\s+){0,3}an)\b", re.I)


def anfrage_erkannt(eingabe: str, zustimmung: bool = False) -> bool:
    """Hat Eike in diesem Zug ausdrücklich verlangt, jemanden auf Snapchat zu suchen/anzufragen?
    Ohne seine Bitte startet `kontakt_anfragen` keine Handy-Suche mehr — sonst kam der Push „Suche fertig“
    ohne erkennbaren Auslöser (30.10.2026: eine Suche nach Titus, die Eike nie verlangt hatte)."""
    text = (eingabe or "").strip()
    if not text:
        return False
    if zustimmung:
        return True
    return bool(_ANFRAGE.search(text)) and not _VERNEINT.search(text)
