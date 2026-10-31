"""Ein kleiner Seitensatz für die Ausarbeitung (15.10.2026) — ohne Schriftdateien und ohne neue Abhängigkeiten.

Die PDF-Standardschriften sind in jedem Betrachter da: Helvetica (normal, fett, kursiv) für Text und **Symbol** für die Mathe-Zeichen
(≈ ≤ ≥ ≠ √ ∞ π ∑ ∫ → ⇒ …, griechische Buchstaben). Hoch- und Tiefstellung setzt der Satz echt (kleiner, gehoben/gesenkt). Rechnet das Modell mit
LaTeX-Resten (`\\frac`, `\\cdot`, `$…$`) oder ASCII (`<=`, `=>`, `x^2`, `sqrt(2)`), macht `normalisieren` Zeichen daraus.

Ein Lauf ist `(schrift, text, größenfaktor, hebung)`: Schrift `F1` Helvetica, `F2` fett, `F3` Symbol (der Text trägt dort die Symbol-Codes als Zeichen),
`F4` kursiv; Größenfaktor und Hebung relativ zur Schriftgröße. Breiten stammen aus den Adobe-Standardmaßen (für Symbol grob), mit etwas Luft beim
Umbruch — sie bestimmen nur, wo eine Zeile endet."""

from __future__ import annotations

import re
import unicodedata

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

A4 = (595.28, 841.89)
SCHRIFTEN = {"F1": "/Helvetica", "F2": "/Helvetica-Bold", "F3": "/Symbol", "F4": "/Helvetica-Oblique"}
KLEIN, HOCH, TIEF = 0.66, 0.36, -0.14         # Größenfaktor und Hebung (Anteil der Schriftgröße) für Hoch-/Tiefgestelltes
SYMBOL_LUFT = 140                              # Platz (1/1000 Schriftgröße) um jedes Symbol, damit es nicht an den Nachbarzeichen klebt
LUFT = 0.97                                    # Umbruch rechnet mit 97 % der Breite (die Tabellen sind nicht für jedes Zeichen exakt)

# ---------- Breiten (1/1000 Schriftgröße) ----------
_ZEICHEN = {" ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667, "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278,
            "-": 333, ".": 278, "/": 278, ":": 278, ";": 278, "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015, "[": 278, "\\": 278,
            "]": 278, "^": 469, "_": 556, "`": 333, "{": 334, "|": 260, "}": 334, "~": 584, "€": 556, "–": 556, "—": 1000, "„": 333,
            "“": 333, "”": 333, "‘": 222, "’": 222, "·": 278, "×": 584, "÷": 584, "°": 400, "±": 584, "§": 556, "…": 1000, "ß": 611}
_GROSS = [667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]
_KLEIN = [556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500]
_GROSS_F = [722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]
_KLEIN_F = [556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500]
_FETT_ABW = {"!": 333, '"': 474, "'": 238, ":": 333, ";": 333, "?": 611, "@": 975, "[": 333, "]": 333, "^": 584}


def _tabelle(gross, klein, abw) -> dict[str, int]:
    t = dict(_ZEICHEN)
    t.update(abw)
    t.update({chr(65 + i): w for i, w in enumerate(gross)})
    t.update({chr(97 + i): w for i, w in enumerate(klein)})
    t.update({str(i): 556 for i in range(10)})
    return t


_NORMAL = _tabelle(_GROSS, _KLEIN, {})
_FETT = _tabelle(_GROSS_F, _KLEIN_F, _FETT_ABW)
_SYMBOL_BREITE = {"α": 631, "γ": 411, "δ": 494, "ε": 439, "ζ": 494, "η": 603, "θ": 521, "ι": 329, "μ": 576, "ν": 521, "ξ": 493, "σ": 603, "τ": 439,
                  "υ": 576, "φ": 521, "ψ": 686, "ω": 686, "∞": 713, "∑": 713, "∫": 274, "→": 1060, "←": 1060, "↔": 1110, "⇒": 1060, "⇐": 1060,
                  "⇔": 1110, "∂": 494, "⁄": 167}


def _breite_zeichen(font: str, ch: str) -> float:
    if font == "F3":
        u = _CODE_ZEICHEN.get(ch, ch)
        return _SYMBOL_BREITE.get(u, 700 if u.isupper() else 549) + SYMBOL_LUFT          # Betrachter zeichnen Symbole unterschiedlich breit
    t = _FETT if font == "F2" else _NORMAL
    if ch in t:
        return t[ch]
    basis = unicodedata.normalize("NFD", ch)[:1]          # ä → a: gleiche Breite wie der Grundbuchstabe
    return t.get(basis, 556)


# ---------- Symbol-Schrift: Zeichen → Code ----------
_SYMBOL = {"≤": 0xA3, "≥": 0xB3, "≠": 0xB9, "≈": 0xBB, "∞": 0xA5, "√": 0xD6, "∑": 0xE5, "∏": 0xD5, "∫": 0xF2, "∂": 0xB6, "∈": 0xCE,
           "∉": 0xCF, "∩": 0xC7, "∪": 0xC8, "⊂": 0xCC, "⊆": 0xCD, "∅": 0xC6, "→": 0xAE, "←": 0xAC, "↔": 0xAB, "⇒": 0xDE, "⇐": 0xDC,
           "⇔": 0xDB, "∇": 0xD1, "∝": 0xB5, "≡": 0xBA, "≅": 0x40, "∧": 0xD9, "∨": 0xDA, "∀": 0x22, "∃": 0x24, "∠": 0xD0, "⊥": 0x5E,
           "′": 0xA2, "″": 0xB2, "−": 0x2D, "⋅": 0xD7, "∙": 0xD7, "⁄": 0xA4,
           "α": 0x61, "β": 0x62, "χ": 0x63, "δ": 0x64, "ε": 0x65, "φ": 0x66, "γ": 0x67, "η": 0x68, "ι": 0x69, "κ": 0x6B, "λ": 0x6C, "μ": 0x6D,
           "ν": 0x6E, "ο": 0x6F, "π": 0x70, "θ": 0x71, "ρ": 0x72, "σ": 0x73, "τ": 0x74, "υ": 0x75, "ω": 0x77, "ξ": 0x78, "ψ": 0x79, "ζ": 0x7A,
           "Α": 0x41, "Β": 0x42, "Χ": 0x43, "Δ": 0x44, "Ε": 0x45, "Φ": 0x46, "Γ": 0x47, "Η": 0x48, "Ι": 0x49, "Κ": 0x4B, "Λ": 0x4C, "Μ": 0x4D,
           "Ν": 0x4E, "Ο": 0x4F, "Π": 0x50, "Θ": 0x51, "Ρ": 0x52, "Σ": 0x53, "Τ": 0x54, "Υ": 0x55, "Ω": 0x57, "Ξ": 0x58, "Ψ": 0x59, "Ζ": 0x5A}
_SYMBOL_CODE = {k: chr(v) for k, v in _SYMBOL.items()}
_CODE_ZEICHEN = {chr(v): k for k, v in _SYMBOL.items()}          # Symbol-Code → Unicode-Zeichen (die Läufe tragen den Code)

_HOCH_ZEICHEN = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿⁱᵃᵇᶜᵈᵉᶠᵍʰʲᵏˡᵐᵒᵖʳˢᵗᵘᵛʷˣʸᶻ", "0123456789+-niabcdefghjklmoprstuvwxyz"))
_TIEF_ZEICHEN = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋ₙᵢⱼᵣᵤᵥₐₑₒₓₕₖₗₘₚₛₜ", "0123456789+-nijruvaeoxhklmpst"))
_URL = re.compile(r"https?://\S+")


# ---------- Text aufbereiten ----------
_LATEX = {"cdot": "·", "times": "×", "div": "÷", "pm": "±", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈",
          "infty": "∞", "pi": "π", "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ", "theta": "θ", "lambda": "λ", "mu": "μ",
          "sigma": "σ", "Sigma": "Σ", "omega": "ω", "Omega": "Ω", "phi": "φ", "rho": "ρ", "tau": "τ", "epsilon": "ε", "varepsilon": "ε",
          "rightarrow": "→", "to": "→", "leftarrow": "←", "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "sum": "∑", "prod": "∏",
          "int": "∫", "partial": "∂", "ldots": "…", "dots": "…", "in": "∈", "cap": "∩", "cup": "∪", "subset": "⊂", "emptyset": "∅", "forall": "∀",
          "exists": "∃", "neg": "¬", "quad": " ", "qquad": " ", "left": "", "right": "", "mathrm": "", "mathbf": "", "text": ""}
_FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SQRT = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_TEXT = re.compile(r"\\(?:text|mathrm|mathbf|operatorname)\s*\{([^{}]*)\}")
_BEFEHL = re.compile(r"\\([A-Za-z]+)|\\([,;:!%{}])")


def _klammer(t: str) -> str:
    t = t.strip()
    return t if re.fullmatch(r"[A-Za-z0-9.,]+", t) else f"({t})"


def normalisieren(text: str) -> str:
    """LaTeX-Reste und ASCII-Mathe → Zeichen. Normaler Text bleibt unverändert."""
    t = str(text or "")
    for roh, neu in (("✓", "ok"), ("✔", "ok"), ("☑", "ok"), ("✗", "falsch"), ("✘", "falsch"), ("❌", "falsch"), ("{,}", ","),
                     ("ℕ", "N"), ("ℤ", "Z"), ("ℚ", "Q"), ("ℝ", "R"), ("ℂ", "C"),
                     ("⬟", "⇔")):                      # „⬟“ liefert Gemini mitunter statt ⇔ (Äquivalenzketten); Symbol-Schrift kennt es nicht
        t = t.replace(roh, neu)
    # „Vm in N En in N“ (Gemini schreibt ∀ und ∃ mitunter als V und E): nur ein V/E direkt vor einer einzelnen Variablen und „in/∈ <Zahlbereich>“
    t = re.sub(r"\b([VE])([a-z]'?)(?=\s+(?:in|∈)\s+[NZQRC]\b)", lambda m: ("∀" if m.group(1) == "V" else "∃") + m.group(2), t)
    # „x in N“, „y in {x}“, „i, j in {1, 2, 3}“ → ∈ — nur hinter einer einzelnen Variablen (nicht hinter einem Wort) und vor einem Mengennamen
    # (N Z Q R C M A B) oder einer Klammer; das deutsche „in“ („in Worte“, „in Mathe“) bleibt.
    t = re.sub(r"(?<![A-Za-zÄÖÜäöüß]{2})(?<=[A-Za-z0-9}\)'])\s+in\s+(?=(?:[NZQRCMAB]\b|\{|\())", " ∈ ", t)
    t = _FRAC.sub(lambda m: f"{_klammer(m.group(1))}/{_klammer(m.group(2))}", t)
    t = _SQRT.sub(lambda m: f"√({m.group(1)})", t)
    t = _TEXT.sub(lambda m: m.group(1), t)
    t = _BEFEHL.sub(lambda m: _LATEX.get(m.group(1), "") if m.group(1) else {"%": "%", "{": "{", "}": "}"}.get(m.group(2), " "), t)
    t = t.replace("$", "").replace("\\(", "").replace("\\)", "").replace("\\[", "").replace("\\]", "")
    t = re.sub(r"\bsqrt\s*\(", "√(", t)
    for roh, neu in (("<=>", "⇔"), ("<->", "↔"), ("=>", "⇒"), ("->", "→"), ("<=", "≤"), (">=", "≥"), ("!=", "≠"), ("~=", "≈"), ("+-", "±")):
        t = t.replace(roh, neu)
    t = re.sub(r"(?<![A-Za-zÄÖÜäöüß]{2})(?<=[A-Za-z0-9}\)'])\s+not in\s+(?=(?:[NZQRCMAB]\b|\{|\())", " ∉ ", t)
    t = re.sub(r"(?<=\s)\*(?=\s)", "·", t)
    t = re.sub(r"(?<=[0-9A-Za-z)])\*(?=[0-9A-Za-z(])", "·", t)
    return re.sub(r"(?<=≠) O(?![A-Za-z0-9])", " ∅", t)               # „!= O“: Gemini schreibt O statt ∅ (nur hinter ≠ ist das die leere Menge)


_SCHRITT = re.compile(r"(?<=[.!?:;)]) (?=(?:[2-9]|1[0-9])\. |\([2-9]\) )")


def schritte_trennen(text: str) -> str:
    """„1. … 2. … 3. …“ und „(1) … (2) …“, die das Modell in einen Absatz schreibt, bekommen je eine Zeile."""
    return _SCHRITT.sub("\n", str(text or ""))


_MARKE = re.compile(r"\^(\{[^}]*\}|\([^)]*\)|[+\-−]?[A-Za-z0-9]+)|(?<=[A-Za-z0-9)])_(\{[^}]*\}|[A-Za-z0-9]{1,3})(?![A-Za-z0-9_])")


def _marken(t: str) -> list[tuple[str, str]]:
    aus: list[tuple[str, str]] = []
    pos = 0
    for m in _MARKE.finditer(t):
        if m.start() > pos:
            aus.append((t[pos:m.start()], "n"))
        roh = m.group(1) if m.group(1) is not None else m.group(2)
        if roh[:1] in "{(":
            roh = roh[1:-1]
        aus.append((roh, "h" if m.group(1) is not None else "t"))
        pos = m.end()
    if pos < len(t):
        aus.append((t[pos:], "n"))
    return aus


def _stuecke(t: str) -> list[tuple[str, str]]:
    """Text in (Stück, Art) mit Art 'n' normal, 'h' hoch, 't' tief — aus `^…`, `_…` und Unicode-Hoch/Tief. Adressen (https://…) bleiben unberührt."""
    aus: list[tuple[str, str]] = []
    pos = 0
    for url in _URL.finditer(t):
        aus += _marken(t[pos:url.start()])
        aus.append((url.group(0), "u"))
        pos = url.end()
    aus += _marken(t[pos:])
    fertig: list[tuple[str, str]] = []
    for stueck, art in aus:
        if art == "u":
            fertig.append((stueck, "n"))
            continue
        if art != "n":
            fertig.append((stueck, art))
            continue
        for ch in stueck:
            if ch in _HOCH_ZEICHEN:
                fertig.append((_HOCH_ZEICHEN[ch], "h"))
            elif ch in _TIEF_ZEICHEN:
                fertig.append((_TIEF_ZEICHEN[ch], "t"))
            else:
                fertig.append((ch, "n"))
    zusammen: list[tuple[str, str]] = []
    for s, a in fertig:
        if zusammen and zusammen[-1][1] == a:
            zusammen[-1] = (zusammen[-1][0] + s, a)
        else:
            zusammen.append((s, a))
    return zusammen


def _cp1252(ch: str) -> bool:
    try:
        ch.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def laeufe(text: str, fett: bool = False, kursiv: bool = False) -> list[tuple[str, str, float, float]]:
    """Text → Läufe `(schrift, text, größenfaktor, hebung)`. Nur Zeichen, die es in Helvetica oder Symbol gibt; sonst „?“."""
    basis = "F2" if fett else ("F4" if kursiv else "F1")
    aus: list[tuple[str, str, float, float]] = []
    for stueck, art in _stuecke(normalisieren(text)):
        faktor, hebung = {"n": (1.0, 0.0), "h": (KLEIN, HOCH), "t": (KLEIN, TIEF)}[art]
        for ch in stueck:
            if ch in _SYMBOL_CODE:
                font, z = "F3", _SYMBOL_CODE[ch]
            elif ch == "\n" or _cp1252(ch):
                font, z = basis, ch
            else:
                font, z = basis, "?"
            if aus and aus[-1][0] == font and aus[-1][2] == faktor and aus[-1][3] == hebung:
                aus[-1] = (font, aus[-1][1] + z, faktor, hebung)
            else:
                aus.append((font, z, faktor, hebung))
    return aus


def breite(runs, groesse: float) -> float:
    return sum(_breite_zeichen(f, ch) * sf for f, t, sf, _ in runs for ch in t) / 1000 * groesse


# ---------- Umbruch ----------
def _atome(runs) -> list[tuple[str, str, float, float]]:
    return [(f, ch, sf, hb) for f, t, sf, hb in runs for ch in t]


def _zu_laeufen(atome) -> list[tuple[str, str, float, float]]:
    aus: list[tuple[str, str, float, float]] = []
    for f, ch, sf, hb in atome:
        if aus and aus[-1][0] == f and aus[-1][2] == sf and aus[-1][3] == hb:
            aus[-1] = (f, aus[-1][1] + ch, sf, hb)
        else:
            aus.append((f, ch, sf, hb))
    return aus


def _atom_breite(a, groesse: float) -> float:
    return _breite_zeichen(a[0], a[1]) * a[2] / 1000 * groesse


def umbrechen(text: str, groesse: float, max_breite: float, fett: bool = False, kursiv: bool = False) -> list[list[tuple[str, str, float, float]]]:
    """Text → Zeilen aus Läufen, keine breiter als `max_breite`. Absätze (`\\n`) und Leerzeilen bleiben; Wörter bleiben ganz, außer sie sind
    allein breiter als eine Zeile."""
    grenze = max_breite * LUFT
    zeilen: list[list[tuple[str, str, float, float]]] = []
    for absatz in _absaetze(laeufe(text, fett, kursiv)):
        if not absatz:
            zeilen.append([])
            continue
        woerter, wort = [], []
        for a in absatz:
            if a[1] == " " and a[3] == 0:
                woerter.append(wort)
                wort = []
            else:
                wort.append(a)
        woerter.append(wort)
        zeile: list = []
        for w in (x for x in woerter if x):
            wb = sum(_atom_breite(a, groesse) for a in w)
            leer = _breite_zeichen("F1", " ") / 1000 * groesse
            zb = sum(_atom_breite(a, groesse) for a in zeile)
            if zeile and zb + leer + wb > grenze:
                zeilen.append(_zu_laeufen(zeile))
                zeile = []
                zb = 0
            if wb > grenze:                                          # ein einziges, zu langes Wort: hart trennen
                if zeile:
                    zeilen.append(_zu_laeufen(zeile))
                    zeile = []
                stueck, sb = [], 0.0
                for a in w:
                    ab = _atom_breite(a, groesse)
                    if stueck and sb + ab > grenze:
                        zeilen.append(_zu_laeufen(stueck))
                        stueck, sb = [], 0.0
                    stueck.append(a)
                    sb += ab
                zeile = stueck
                continue
            zeile = zeile + ([("F1", " ", 1.0, 0.0)] if zeile else []) + w
        zeilen.append(_zu_laeufen(zeile))
    return zeilen or [[]]


def _absaetze(runs) -> list[list[tuple[str, str, float, float]]]:
    absaetze: list[list] = [[]]
    for a in _atome(runs):
        if a[1] == "\n":
            absaetze.append([])
        else:
            absaetze[-1].append(a)
    return absaetze


# ---------- Seite ----------
def _rgb(farbe: str) -> str:
    h = farbe.lstrip("#")
    return " ".join(f"{int(h[i:i + 2], 16) / 255:.3f}" for i in (0, 2, 4))


def _pdf_text(font: str, text: str) -> bytes:
    roh = bytes(ord(c) & 0xFF for c in text) if font == "F3" else text.encode("cp1252", errors="replace")
    return roh.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").replace(b"\r", b"")


class Seite:
    """Eine A4-Seite aus Zeichenbefehlen (Ursprung unten links, Maße in pt)."""

    def __init__(self):
        self.ops: list[bytes] = []

    def rechteck(self, x: float, y: float, b: float, h: float, farbe: str, radius: float = 0) -> None:
        """Gefülltes Rechteck mit linker unterer Ecke (x, y), optional mit runden Ecken."""
        f = _rgb(farbe)
        if radius <= 0:
            self.ops.append(f"{f} rg {x:.2f} {y:.2f} {b:.2f} {h:.2f} re f".encode())
            return
        r = min(radius, b / 2, h / 2)
        k = 0.5523 * r
        x2, y2 = x + b, y + h
        pfad = (f"{x + r:.2f} {y:.2f} m {x2 - r:.2f} {y:.2f} l {x2 - r + k:.2f} {y:.2f} {x2:.2f} {y + r - k:.2f} {x2:.2f} {y + r:.2f} c "
                f"{x2:.2f} {y2 - r:.2f} l {x2:.2f} {y2 - r + k:.2f} {x2 - r + k:.2f} {y2:.2f} {x2 - r:.2f} {y2:.2f} c "
                f"{x + r:.2f} {y2:.2f} l {x + r - k:.2f} {y2:.2f} {x:.2f} {y2 - r + k:.2f} {x:.2f} {y2 - r:.2f} c "
                f"{x:.2f} {y + r:.2f} l {x:.2f} {y + r - k:.2f} {x + r - k:.2f} {y:.2f} {x + r:.2f} {y:.2f} c f")
        self.ops.append(f"{f} rg {pfad}".encode())

    def linie(self, x1: float, y1: float, x2: float, y2: float, farbe: str, staerke: float = 0.6) -> None:
        self.ops.append(f"{_rgb(farbe)} RG {staerke:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S".encode())

    def text(self, x: float, y: float, runs, groesse: float, farbe: str) -> float:
        """Schreibt Läufe ab (x, y = Grundlinie). Gibt das Ende (x) zurück."""
        f = _rgb(farbe)
        for font, t, sf, hb in runs:
            if not t:
                continue
            for stueck in ([*t] if font == "F3" else [t]):               # Symbole einzeln: mit Luft davor und danach
                if font == "F3":
                    x += SYMBOL_LUFT / 2 / 1000 * groesse * sf
                self.ops.append(b"BT /" + font.encode() + f" {groesse * sf:.2f} Tf {f} rg 1 0 0 1 {x:.2f} {y + hb * groesse:.2f} Tm (".encode()
                                + _pdf_text(font, stueck) + b") Tj ET")
                breite_ = sum(_breite_zeichen(font, ch) for ch in stueck)
                x += (breite_ - (SYMBOL_LUFT / 2 if font == "F3" else 0)) * sf / 1000 * groesse
        return x


def schreibe_seiten(writer: PdfWriter, seiten: list[Seite]) -> None:
    """Hängt die Seiten (A4) an das PDF an; die vier Standardschriften teilen sich alle Seiten."""
    schriften = DictionaryObject()
    for kurz, name in SCHRIFTEN.items():
        f = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject(name)})
        if name != "/Symbol":
            f[NameObject("/Encoding")] = NameObject("/WinAnsiEncoding")
        schriften[NameObject("/" + kurz)] = writer._add_object(f)
    for s in seiten:
        pdfseite = writer.add_blank_page(width=A4[0], height=A4[1])
        pdfseite[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): schriften})
        inhalt = DecodedStreamObject()
        inhalt.set_data(b"\n".join(s.ops))
        pdfseite[NameObject("/Contents")] = writer._add_object(inhalt)
