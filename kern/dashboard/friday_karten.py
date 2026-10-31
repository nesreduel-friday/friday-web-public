"""Karten, die Friday selbst auf die Übersicht legt (Werkzeug `dashboard_karte`).

Etwas, das Eike ansehen oder für später griffbereit haben soll: eine Abfahrt,
ein Ergebnis, ein Bild. Jede Karte hat eine Lebensdauer. Der Text kommt vom
Modell und enthält womöglich Fremdes: die Seite setzt ihn nur als Text ein.
Bilder nur von `https:` öffentlicher Adressen oder aus Fridays eigenen Bildern —
sonst fragte der Browser ein beliebiges Ziel (auch im Heimnetz) ab.
"""

import json
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from kern import config
from kern.dienste import bildsuche, schulmanager, web

MAX_AKTIV = 6
# Kurz, weil eine veraltete Karte schlimmer ist als keine: „Aktuelle Uhrzeit“ stand am 24.09.2026
# über 10 Minuten da. Wer länger will, sagt es mit `minuten`.
MINUTEN_STANDARD = 10
MINUTEN_MAX = 720
TITEL_MAX = 80
TEXT_MAX = 400
STUNDEN_MAX = 14                 # so viele Stunden trägt eine Stundenplan-Karte höchstens
MINUTEN_STUNDENPLAN = 120        # ein nachgeschlagener Tag soll in Ruhe ansehbar bleiben
_STATUS = {"regulaer", "ausfall", "vertretung"}
# Fridays eigene Bilder: aus dem Gedächtnis (32 Hex) und Kopien aus dem Netz (16 Hex, `dienste/bildsuche.py`).
_EIGENES_BILD = re.compile(r"^/api/(?:bilder/[0-9a-f]{32}|netzbild/[0-9a-f]{16})$")
_sperre = threading.Lock()


class KarteFehler(ValueError):
    """Eine Eingabe, die nicht geht — der Text ist für Friday und Eike lesbar."""


def _pfad() -> Path:
    return config.daten_ordner() / "dashboard_karten.json"


def _lesen() -> list[dict]:
    try:
        return [k for k in json.loads(_pfad().read_text(encoding="utf-8")) if isinstance(k, dict) and k.get("id")]
    except (OSError, ValueError):
        return []


def _schreiben(liste: list[dict]) -> None:
    pfad = _pfad()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(liste, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(pfad)


def _aktiv(k: dict, jetzt: datetime) -> bool:
    if k.get("weg"):
        return False
    try:
        return datetime.fromisoformat(k["bis"]) > jetzt
    except (KeyError, ValueError):
        return False


async def _bild_pruefen(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if _EIGENES_BILD.match(url):
        return url
    if not url.lower().startswith("https://"):
        raise KarteFehler("Bilder gehen nur von https-Adressen oder aus Fridays eigenen Bildern.")
    try:
        await web.pruefen(url)
    except web.NichtLesbar as exc:
        raise KarteFehler(f"Dieses Bild geht nicht: {exc}.") from None
    return url


def _ablegen(art: str, titel: str, text: str, bild: str, minuten: int, daten: dict | None = None) -> dict:
    try:
        minuten = int(minuten)
    except (TypeError, ValueError):
        minuten = MINUTEN_STANDARD
    minuten = max(1, min(MINUTEN_MAX, minuten))
    jetzt = config.jetzt()
    neu = {"id": "f" + uuid.uuid4().hex[:8], "art": art, "titel": titel, "text": text, "bild": bild,
           "daten": daten or {}, "zeit": jetzt.isoformat(),
           "bis": (jetzt + timedelta(minutes=minuten)).isoformat(), "weg": False}
    with _sperre:
        liste = [k for k in _lesen() if _aktiv(k, jetzt)]
        liste.append(neu)
        _schreiben(liste[-MAX_AKTIV:])
    return neu


async def legen(titel: str, text: str = "", bild_url: str = "", minuten: int = MINUTEN_STANDARD) -> dict:
    titel = (titel or "").strip()[:TITEL_MAX]
    if not titel:
        raise KarteFehler("Die Karte braucht einen Titel.")
    bild = await _bild_pruefen(bild_url)
    if bild and _EIGENES_BILD.match(bild):
        # Ein Bild, das Friday selbst gezeigt hat (Gedächtnis oder Kopie aus dem Netz): gleich eine Bildkarte mit Quelle.
        # Am 25.09.2026 lag „zeig es mir als Karte“ sonst ohne Bild da.
        m = bildsuche.meta(bild.rsplit("/", 1)[-1]) if bild.startswith("/api/netzbild/") else None
        m = m or {"quelle": "Gedächtnis"}
        return await legen_bild(bild, titel, m.get("quelle", ""), m.get("lizenz", ""), m.get("urheber", ""), m.get("link", ""),
                                minuten, (text or "").strip()[:TEXT_MAX])
    return _ablegen("friday", titel, (text or "").strip()[:TEXT_MAX], bild, minuten)


_DATEI_ADRESSE = re.compile(r"/api/aufgabenblatt/[0-9]{1,9}/ausarbeitung")      # die einzige Datei, die eine Karte anbieten darf (Eikes eigene Ausarbeitung)


async def legen_datei(titel: str, text: str, url: str, datei_titel: str = "", minuten: int = MINUTEN_STANDARD) -> dict:
    """Eine Karte mit einem Knopf zum Herunterladen einer Datei von Friday selbst (die Ausarbeitung eines Aufgabenblatts). Nur diese Adressform
    wird angenommen — nie eine fremde Adresse, denn der Knopf lädt ohne weitere Rückfrage."""
    url = (url or "").strip()
    if not _DATEI_ADRESSE.fullmatch(url):
        raise KarteFehler("Diese Datei biete ich nicht an.")
    titel = (titel or "").strip()[:TITEL_MAX]
    if not titel:
        raise KarteFehler("Die Karte braucht einen Titel.")
    return _ablegen("friday", titel, (text or "").strip()[:TEXT_MAX], "", minuten, {"datei": {"url": url, "titel": (datei_titel or "Datei").strip()[:80]}})


MINUTEN_BILD = 60                # ein gezeigtes Bild soll in Ruhe ansehbar bleiben


async def legen_bild(url: str, titel: str, quelle: str = "", lizenz: str = "", urheber: str = "", link: str = "",
                     minuten: int = MINUTEN_BILD, text: str = "") -> dict:
    """Eine Bildkarte: ein Bild groß, darunter Titel und Quelle. Nur Fridays eigene Bilder (Gedächtnis, Netz-Kopien) —
    nie eine fremde Adresse, denn Eikes Gerät soll keinen fremden Server abfragen. Der Quell-Link nur als https."""
    titel = (titel or "").strip()[:TITEL_MAX]
    if not titel:
        raise KarteFehler("Die Bildkarte braucht einen Titel.")
    if not _EIGENES_BILD.match((url or "").strip()):
        raise KarteFehler("Bildkarten gehen nur mit Fridays eigenen Bildern.")
    link = (link or "").strip()
    daten = {"quelle": (quelle or "").strip()[:60], "lizenz": (lizenz or "").strip()[:40],
             "urheber": (urheber or "").strip()[:80], "link": link[:400] if link.lower().startswith("https://") else ""}
    return _ablegen("bild", titel, (text or "").strip()[:TEXT_MAX], url.strip(), minuten, daten)


MINUTEN_ROUTE = 45              # Abfahrten und Ankünfte veralten: lieber neu fragen als eine alte Verbindung stehen lassen
_ROUTE_MODI = {"oepnv", "fuss", "auto"}
_ABSCHNITT_ARTEN = {"fuss", "bus", "tram", "ubahn", "sbahn", "zug", "schiff", "bahn"}


def _zahl(x, unten=-10**9, oben=10**9, standard=0) -> int:
    try:
        return max(unten, min(oben, int(round(float(x)))))
    except (TypeError, ValueError):
        return standard


def _ort_sauber(o) -> dict:
    o = o if isinstance(o, dict) else {}
    try:
        b, l = float(o["breite"]), float(o["laenge"])
    except (KeyError, TypeError, ValueError):
        raise KarteFehler("Start und Ziel brauchen Koordinaten.") from None
    if not (-90 <= b <= 90 and -180 <= l <= 180):
        raise KarteFehler("Diese Koordinaten gibt es nicht.")
    return {"name": str(o.get("name") or "").strip()[:120], "breite": round(b, 6), "laenge": round(l, 6)}


def _option_sauber(o: dict) -> dict | None:
    if not isinstance(o, dict) or o.get("modus") not in _ROUTE_MODI:
        return None
    t = lambda k, n: str(o.get(k) or "").strip()[:n]                       # noqa: E731
    sauber = {"modus": o["modus"], "titel": t("titel", 30), "dauer_min": _zahl(o.get("dauer_min"), 0, 10000), "ab": t("ab", 8),
              "an": t("an", 8), "hinweis": t("hinweis", 160)}
    if o.get("distanz_m") is not None:
        sauber["distanz_m"] = _zahl(o["distanz_m"], 0, 10**7)
    if o["modus"] == "oepnv":
        sauber.update({"umstiege": _zahl(o.get("umstiege"), 0, 20), "echtzeit": bool(o.get("echtzeit")),
                       "verspaetung_min": None if o.get("verspaetung_min") is None else _zahl(o["verspaetung_min"], -600, 600),
                       "ab_in_min": None if o.get("ab_in_min") is None else _zahl(o["ab_in_min"], 0, 10000), "abschnitte": []})
        for a in list(o.get("abschnitte") or [])[:12]:
            if not isinstance(a, dict):
                continue
            ta = lambda k, n: str(a.get(k) or "").strip()[:n]              # noqa: E731
            sauber["abschnitte"].append({
                "art": a.get("art") if a.get("art") in _ABSCHNITT_ARTEN else "bahn", "linie": ta("linie", 20), "richtung": ta("richtung", 40),
                "von": ta("von", 40), "nach": ta("nach", 40), "ab": ta("ab", 8), "an": ta("an", 8), "min": _zahl(a.get("min"), 0, 1000),
                "meter": _zahl(a.get("meter"), 0, 10**6),
                "verspaetung_min": None if a.get("verspaetung_min") is None else _zahl(a["verspaetung_min"], -600, 600)})
    return sauber


def _auftrag_sauber(a) -> dict | None:
    """Womit die Karte sich selbst aktuell hält (Live): Ziel, Start (None = Eikes Standort), Verkehrsmittel, gewünschte Zeit."""
    if not isinstance(a, dict):
        return None
    wann = str(a.get("wann") or "")[:40]
    try:
        if wann:
            datetime.fromisoformat(wann)
    except ValueError:
        wann = ""
    return {"ziel": _ort_sauber(a.get("ziel")), "start": _ort_sauber(a["start"]) if a.get("start") else None,
            "modi": [m for m in list(a.get("modi") or []) if m in _ROUTE_MODI][:3] or sorted(_ROUTE_MODI), "wann": wann}


def route_inhalt(daten: dict) -> dict:
    """Die geprüften Daten einer Verbindungskarte — beim Legen und bei jeder Auffrischung. Alles wird gekürzt: Die Karte trägt
    Koordinaten, die der Browser in einen Karten-Link steckt, und Texte aus dem Netz (Ortsnamen)."""
    start, ziel = _ort_sauber(daten.get("start")), _ort_sauber(daten.get("ziel"))
    optionen = [o for o in (_option_sauber(x) for x in list(daten.get("optionen") or [])[:5]) if o]
    if not optionen:
        raise KarteFehler("Es gibt keine Verbindung, die ich zeigen könnte.")
    schnellste = daten.get("schnellste")
    schnellste = schnellste if isinstance(schnellste, int) and 0 <= schnellste < len(optionen) else None
    inhalt = {"start": start, "ziel": ziel, "optionen": optionen, "schnellste": schnellste,
              "ausfaelle": [{"modus": str(a.get("modus") or "")[:8], "text": str(a.get("text") or "")[:120]}
                            for a in list(daten.get("ausfaelle") or [])[:3] if isinstance(a, dict)],
              "hinweise": [str(h)[:160] for h in list(daten.get("hinweise") or [])[:4]],
              "stand": config.jetzt().isoformat(), "angekommen": False, "veraltet": False}
    auftrag = _auftrag_sauber(daten.get("auftrag"))
    if auftrag:
        inhalt["auftrag"] = auftrag
    return inhalt


async def legen_route(daten: dict, minuten: int = MINUTEN_ROUTE) -> dict:
    """Die Verbindungskarte: Start, Ziel und die Optionen (Bus/Bahn, zu Fuß, Auto)."""
    inhalt = route_inhalt(daten)
    return _ablegen("route", f"Nach {inhalt['ziel']['name'] or 'Ziel'}"[:TITEL_MAX], "", "", minuten, inhalt)


def aktive_routen() -> list[dict]:
    """Die gelegten Verbindungskarten, die noch gelten und sich selbst aktuell halten können (mit Auftrag)."""
    jetzt = config.jetzt()
    return [k for k in _lesen() if k.get("art") == "route" and _aktiv(k, jetzt) and (k.get("daten") or {}).get("auftrag")]


def route_ersetzen(karte_id: str, daten: dict, bis_mindestens: datetime | None = None) -> bool:
    """Die Daten einer Verbindungskarte durch frisch gerechnete ersetzen — dieselbe Karte, dieselbe Stelle in der Übersicht."""
    inhalt = route_inhalt(daten)
    with _sperre:
        liste = _lesen()
        for k in liste:
            if k["id"] == karte_id and not k.get("weg"):
                k["daten"] = inhalt
                if bis_mindestens:
                    try:
                        k["bis"] = max(datetime.fromisoformat(k["bis"]), bis_mindestens).isoformat()
                    except ValueError:
                        k["bis"] = bis_mindestens.isoformat()
                _schreiben(liste)
                return True
    return False


def route_markieren(karte_id: str, bis: datetime | None = None, **merkmale) -> bool:
    """Ein Merkmal setzen (`veraltet`, `angekommen`), ohne die Daten anzufassen; optional das Ende der Karte vorziehen/verlängern."""
    erlaubt = {"veraltet", "angekommen"}
    with _sperre:
        liste = _lesen()
        for k in liste:
            if k["id"] == karte_id and not k.get("weg"):
                for name, wert in merkmale.items():
                    if name in erlaubt:
                        k.setdefault("daten", {})[name] = bool(wert)
                if bis:
                    k["bis"] = bis.isoformat()
                _schreiben(liste)
                return True
    return False


def _stunde_sauber(x: dict) -> dict:
    """Eine Stunde für die Karte: nur bekannte Felder, gekürzt. Der Status kommt nie ungeprüft durch."""
    def t(feld, n):
        return str(x.get(feld) or "").strip()[:n]
    status = x.get("status") if x.get("status") in _STATUS else "regulaer"
    return {"nr": t("nr", 3), "von": t("von", 5), "bis": t("bis", 5), "fach": t("fach", 60), "raum": t("raum", 30),
            "lehrer": t("lehrer", 60), "status": status, "hinweis": t("hinweis", 80)}


async def legen_stundenplan(tag: date, stunden: list[dict], minuten: int = MINUTEN_STUNDENPLAN) -> dict:
    """Der Stundenplan EINES Tages als Karte (Werkzeug `stundenplan_karte`)."""
    sauber = [_stunde_sauber(x) for x in list(stunden or [])[:STUNDEN_MAX] if isinstance(x, dict)]
    if not sauber:
        raise KarteFehler("Für diesen Tag gibt es keine Stunden.")
    return _ablegen("stundenplan", f"Stundenplan {tag:%d.%m.%Y}", "", "", minuten,
                    {"datum": tag.isoformat(), "stunden": sauber, "app_url": schulmanager.SCHULMANAGER_BASIS})


async def karten() -> list[dict]:
    jetzt = config.jetzt()
    offen = [k for k in _lesen() if _aktiv(k, jetzt)]
    offen.sort(key=lambda k: k["zeit"], reverse=True)
    return [{"id": k["id"], "art": k.get("art", "friday"), "titel": k.get("titel", ""), "text": k.get("text", ""),
             "bild": k.get("bild", ""), "bis": k["bis"], "zeit": k["zeit"], **(k.get("daten") or {})} for k in offen]


def bild_entfernt(bild_url: str) -> None:
    """Ein Bild ist aus dem Gedächtnis entfernt: Karten, die es zeigen, gehen mit."""
    with _sperre:
        liste = _lesen()
        geaendert = False
        for k in liste:
            if k.get("bild") == bild_url and not k.get("weg"):
                k["weg"] = True
                geaendert = True
        if geaendert:
            _schreiben(liste)


def gesehen(karten_id: str) -> bool:
    if not karten_id.startswith("f"):
        return False
    with _sperre:
        liste = _lesen()
        for k in liste:
            if k["id"] == karten_id:
                k["weg"] = True
                _schreiben(liste)
                return True
    return False
