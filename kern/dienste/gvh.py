"""GVH — Bus und Bahn im Großraum Hannover.

Phase 3: Haltestellen suchen, nächste Abfahrten, Verbindung von A nach B.

Warum eigener Code statt einer Bibliothek: GVH läuft auf HAFAS, aber nicht auf
dem klassischen `mgate.exe`. Der Zugang ist ein Gateway unter
`https://gvh.hafas.de/hamm`, und `pyhafas` (die naheliegende Bibliothek) hat
weder ein GVH-Profil noch spricht sie dieses Gateway; sie arbeitet zudem
synchron mit `requests`, während hier alles async läuft. Für die zwei Fragen,
die Eike stellt, ist das Protokoll überschaubar.

Alles an Eikes echtem Netz gemessen (13.09.2026):
- Der Endpunkt hängt intern `/socket` an; jeder Unterpfad läuft ins Leere.
  `https://gvh.hafas.de/hamm` ist die vollständige Adresse.
- Die Client-Kennung entscheidet: `{"id": "GVH"}` wird mit HTTP 500 abgewiesen,
  `{"id": "HAFAS", "l": "vs_webapp"}` liefert 200. Nicht raten — genau so senden.
- Zugangsdaten braucht es keine, nur das feste `aid` aus der öffentlichen
  Konfiguration der GVH-Weboberfläche.
- Am 13.09. fehlte `dTimeR` durchgängig. Am 15.09.2026 kam es für zeitnahe Fahrten
  (die S5 mit +17 Min, Busse mit ihrer echten Zeit); ältere Fahrten verlieren es
  wieder. Messung 15.09. abends: Bennigsen 17 von 20 Abfahrten mit `dTimeR`,
  Kröpcke 20 von 20, TripSearch Bennigsen → Hbf 3 von 3 Verbindungen mit
  `dTimeR`/`aTimeR` in `dep`/`arr` UND in jedem `secL[]`. `abfahrten` und
  `verbindungen` übernehmen es, wo es kommt; fehlt es, bleibt die Echtzeit leer
  (None) — nie „pünktlich“ geraten.
- `stbLoc.lid` darf direkt die Kennung sein („de:03241:5844“) — ohne Namenssuche.
  `dur` im StationBoard wird ignoriert, es zählt nur `maxJny`.
- `JourneyDetails` löst eine Fahrt (`jid`) mit ALLEN Halten auf, je Halt Plan- und
  Echtzeit; `extId` der Halte = Kennung des Garantie-Formulars plus Steig.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from kern import config

URL = "https://gvh.hafas.de/hamm"
# Öffentlich aus https://gvh.hafas.de/config/webapp.config.json — kein Geheimnis,
# kein Konto, nichts Persönliches. Deshalb im Code und nicht in zugaenge.json.
AID = "IKSEvZ1SsVdfIRSK"
CLIENT = {"id": "HAFAS", "type": "WEB", "name": "webapp", "l": "vs_webapp"}
VERSION = "1.40"
ZEITLIMIT = 20.0

MAX_TREFFER = 6
MAX_ABFAHRTEN = 8
MAX_VERBINDUNGEN = 4


class GvhFehler(Exception):
    pass


async def _ruf(methode: str, anfrage: dict) -> dict:
    rumpf = {"lang": "de", "ver": VERSION, "auth": {"type": "AID", "aid": AID},
             "client": CLIENT, "formatted": False,
             "svcReqL": [{"meth": methode, "req": anfrage}]}
    try:
        async with httpx.AsyncClient(timeout=ZEITLIMIT) as client:
            antwort = await client.post(URL, json=rumpf, headers={
                "Content-Type": "application/json", "Accept": "application/json",
                "User-Agent": "Friday/0.1"})
    except httpx.HTTPError as exc:
        raise GvhFehler("Die Fahrplanauskunft antwortet gerade nicht.") from exc
    if antwort.status_code != 200:
        raise GvhFehler(f"Die Fahrplanauskunft antwortet mit {antwort.status_code}.")
    try:
        dienst = antwort.json()["svcResL"][0]
    except (ValueError, KeyError, IndexError) as exc:
        raise GvhFehler("Die Fahrplanauskunft hat unverständlich geantwortet.") from exc
    if dienst.get("err") not in (None, "OK"):
        raise GvhFehler(f"Die Fahrplanauskunft meldet: {dienst.get('errTxt') or dienst.get('err')}")
    return dienst.get("res") or {}


def _uhr(wert) -> str:
    """„212300“ -> „21:23“. Führende Ziffern sind Tage: „1002300“ -> „00:23 (+1 Tag)“."""
    s = "".join(z for z in str(wert or "") if z.isdigit())
    if len(s) < 6:
        return ""
    tage, s = (int(s[:-6]) if len(s) > 6 else 0), s[-6:]
    return f"{s[0:2]}:{s[2:4]}" + (f" (+{tage} Tag)" if tage else "")


def _dauer(wert) -> str:
    """„002236“ -> „22 Min“, „010100“ -> „1 Std 1 Min“."""
    s = "".join(z for z in str(wert or "") if z.isdigit()).zfill(6)[-6:]
    stunden, minuten = int(s[0:2]), int(s[2:4])
    return f"{stunden} Std {minuten} Min" if stunden else f"{minuten} Min"


async def haltestellen(name: str, anzahl: int = MAX_TREFFER) -> list[dict]:
    """Haltestellen zu einem Namen. Das Fragezeichen ist HAFAS-Syntax für
    „fängt so an“ — ohne es findet die Suche oft nichts."""
    name = (name or "").strip()
    if not name:
        return []
    res = await _ruf("LocMatch", {"input": {
        "field": "S", "loc": {"name": name + "?", "type": "S"}, "maxLoc": max(1, min(anzahl, 12))}})
    treffer = ((res.get("match") or {}).get("locL")) or []
    return [{"id": h.get("lid", ""), "name": h.get("name", "")} for h in treffer if h.get("lid")]


MAX_UMKREIS_M = 3000      # weiter weg ist keine „Haltestelle in der Nähe“ mehr


async def haltestellen_um(breite: float, laenge: float, umkreis: int = 1500,
                          anzahl: int = 4) -> list[dict]:
    """Haltestellen um einen Punkt, nächste zuerst — mit Entfernung in Metern.

    HAFAS rechnet Koordinaten in Millionstel Grad. `LocGeoPos` liefert `dist` (Meter)
    gleich mit, das ist also eine Messung und keine Schätzung.

    ABER: dort heißen die Haltestellen NUR „Denkmal“, ohne Ort (gemessen 20.09.2026).
    Mit so einem Namen findet `abfahrten` die falsche Stadt — „Denkmal“ ergibt bei
    `LocMatch` über 250 Treffer in ganz Deutschland, Musterdorf erst an achter Stelle.
    Genau daran scheiterte Friday am 20.09. („Musterdorf, Schlehenweg“ → Meschede).
    Deshalb holt `LocDetails` zu den gefundenen Kennungen die vollen Namen nach —
    alle in EINEM Aufruf — und nur die gehen nach draußen.
    """
    umkreis = max(100, min(int(umkreis), MAX_UMKREIS_M))
    anzahl = max(1, min(int(anzahl), 8))
    res = await _ruf("LocGeoPos", {
        "ring": {"cCrd": {"x": int(round(laenge * 1e6)), "y": int(round(breite * 1e6))},
                 "maxDist": umkreis, "minDist": 0},
        "getStops": True, "getPOIs": False, "maxLoc": anzahl})
    roh = [h for h in (res.get("locL") or []) if h.get("lid")][:anzahl]
    if not roh:
        return []
    namen = {}
    try:
        details = await _ruf("LocDetails", {"locL": [{"lid": h["lid"], "type": "S"} for h in roh]})
        namen = {l.get("lid"): l.get("name", "") for l in (details.get("locL") or []) if l.get("lid")}
    except GvhFehler:
        namen = {}                      # lieber ohne Ortsnamen als gar nichts — siehe unten
    liste = []
    for h in roh:
        name = namen.get(h["lid"]) or ""
        if not name:
            continue                    # ohne vollen Namen nicht ausgeben: führt in die falsche Stadt
        liste.append({"id": h["lid"], "name": name,
                      "meter": int(h["dist"]) if isinstance(h.get("dist"), int) else None})
    liste.sort(key=lambda h: (h["meter"] is None, h["meter"] or 0))
    return liste


# Was Eike sagt und was der GVH schreibt, ist nicht immer dasselbe Wort.
GLEICHBEDEUTEND = {"hbf": "hauptbahnhof", "bhf": "bahnhof", "hauptbf": "hauptbahnhof"}


def _worte(text: str) -> set[str]:
    """Tragende Wörter ab drei Zeichen — „Wennigsen (Deister), KGS“ wird zu
    {wennigsen, deister, kgs}, „Hannover Hbf“ zu {hannover, hauptbahnhof}."""
    sauber = "".join(z if z.isalnum() else " " for z in (text or "").casefold())
    return {GLEICHBEDEUTEND.get(w, w) for w in map(_strasse, sauber.split()) if len(w) >= 3}


def _strasse(wort: str) -> str:
    """„Bismarckstr.“, „Bismarckstrasse“ und „Bismarckstraße“ sind dieselbe Haltestelle (Formular und GVH schreiben
    sie verschieden — #60, #66, #73 scheiterten daran am 15.10.2026)."""
    if wort.endswith("strasse"):
        return wort[:-7] + "straße"
    return wort[:-3] + "straße" if wort.endswith("str") and len(wort) > 3 else wort


def _passt(wunsch: str, gefunden: str) -> bool:
    """Ist der Treffer wirklich die gesuchte Haltestelle?

    JEDES tragende Wort des Wunsches muss vorkommen — ein einzelnes genügt
    nicht. Die GVH-Suche antwortet deutschlandweit, und „Beispielweg Musterdorf“
    ergibt „Lübeck, Lübeck Beispielweg“: Die Straße stimmt, die STADT nicht.
    Genau so schickte „nimm den ersten Treffer“ Eike am 13.09.2026 von
    LUDWIGSHAFEN nach Musterhausen.

    Die strenge Regel verwirft auch Adressen, die keine Haltestelle sind
    („Musterhausen Steinweg“ → „Musterhausen (Han), Steintor“). Das ist gewollt: Dann
    fragt Friday nach und nennt die Alternativen, statt still danebenzugreifen.
    """
    w, g = _worte(wunsch), _worte(gefunden)
    return bool(w) and w <= g


async def _eine(name: str) -> dict:
    liste = await haltestellen(name, MAX_TREFFER)
    if not liste:
        raise GvhFehler(f"Eine Haltestelle „{name}“ finde ich im GVH nicht.")
    for halt in liste:
        if _passt(name, halt["name"]):
            return halt
    # Lieber ehrlich nachfragen als die falsche Stadt nehmen.
    vorschlaege = ", ".join(h["name"] for h in liste[:3])
    raise GvhFehler(f"Zu „{name}“ finde ich keine passende Haltestelle. "
                    f"Gemeint sein könnte: {vorschlaege}. Sag mir bitte, welche.")


def _echt(datum: str | None, plan, echt) -> tuple[str, int | None]:
    """Echtzeit als („HH:MM“, Minuten Abweichung) — oder („“, None), wenn keine kam.

    Fehlt `dTimeR`/`aTimeR`, ist das KEINE Aussage: weder pünktlich noch verspätet.
    Für die Differenz genügt irgendein Tag, falls die Antwort keinen nennt."""
    if not echt or not _uhr(echt):
        return "", None
    tag = datum or "20000101"
    return _uhr(echt), _minuten(zeitpunkt(tag, plan), zeitpunkt(tag, echt))


async def abfahrten(haltestelle: str, anzahl: int = MAX_ABFAHRTEN) -> dict:
    """Was als Nächstes von einer Haltestelle fährt — Linie, Richtung, Zeit und,
    wo der GVH sie liefert, die Echtzeit (seit 15.09.2026 für zeitnahe Fahrten)."""
    halt = await _eine(haltestelle)
    res = await _ruf("StationBoard", {"type": "DEP", "stbLoc": {"lid": halt["id"]},
                                      "maxJny": max(1, min(anzahl, 20))})
    linien = (res.get("common") or {}).get("prodL") or []
    fahrten = []
    for j in (res.get("jnyL") or [])[:anzahl]:
        stop = j.get("stbStop") or {}
        nummer = j.get("prodX")
        echt, versp = _echt(j.get("date"), stop.get("dTimeS"), stop.get("dTimeR"))
        fahrten.append({
            "linie": linien[nummer]["name"] if isinstance(nummer, int) and nummer < len(linien) else "?",
            "richtung": j.get("dirTxt") or "",
            "zeit": _uhr(stop.get("dTimeS")),
            "echt": echt, "verspaetung": versp,
            "faellt_aus": bool(stop.get("dCncl")),
        })
    return {"haltestelle": halt["name"], "abfahrten": fahrten}


# HAFAS-Produktklassen (Bitmaske) → Verkehrsmittel fürs Symbol auf der Karte
_KLASSEN = ((1 | 2 | 4 | 8, "zug"), (16, "sbahn"), (32, "bus"), (128, "ubahn"), (256, "tram"), (64, "schiff"))


def verkehrsmittel(klasse) -> str:
    try:
        k = int(klasse)
    except (TypeError, ValueError):
        return "bahn"
    for maske, name in _KLASSEN:
        if k & maske:
            return name
    return "bahn"


def _auswerten(res: dict, anzahl: int) -> list[dict]:
    """Die Verbindungen einer TripSearch-Antwort, mit Umstiegen und Fußwegen (auch am Anfang und Ende)."""
    gemeinsam = res.get("common") or {}
    orte, linien = gemeinsam.get("locL") or [], gemeinsam.get("prodL") or []

    def ortsname(nummer) -> str:
        return orte[nummer]["name"] if isinstance(nummer, int) and nummer < len(orte) else ""

    ergebnis = []
    for fahrt in (res.get("outConL") or [])[:anzahl]:
        tag = fahrt.get("date")
        abschnitte = []
        for teil in fahrt.get("secL") or []:
            ab, an = teil.get("dep") or {}, teil.get("arr") or {}
            reise = teil.get("jny")
            nummer = (reise or {}).get("prodX")
            linie_ok = reise and isinstance(nummer, int) and nummer < len(linien)
            ab_echt, ab_versp = _echt(tag, ab.get("dTimeS"), ab.get("dTimeR"))
            an_echt, an_versp = _echt(tag, an.get("aTimeS"), an.get("aTimeR"))
            abschnitte.append({
                "linie": linien[nummer]["name"] if linie_ok else "Fußweg",
                "richtung": (reise or {}).get("dirTxt") or "",
                "ab": _uhr(ab.get("dTimeS")), "an": _uhr(an.get("aTimeS")),
                "ab_echt": ab_echt, "ab_verspaetung": ab_versp,
                "an_echt": an_echt, "an_verspaetung": an_versp,
                "von": ortsname(ab.get("locX")), "nach": ortsname(an.get("locX")),
                "zu_fuss": reise is None,
                "meter": int((teil.get("gis") or {}).get("dist") or 0) if reise is None else 0,
                "verkehrsmittel": verkehrsmittel(linien[nummer].get("cls")) if linie_ok else "fuss",
                "faellt_aus": bool(ab.get("dCncl") or an.get("aCncl")),
            })
        ab, an = fahrt.get("dep") or {}, fahrt.get("arr") or {}
        ab_echt, ab_versp = _echt(tag, ab.get("dTimeS"), ab.get("dTimeR"))
        an_echt, an_versp = _echt(tag, an.get("aTimeS"), an.get("aTimeR"))
        ergebnis.append({
            "ab": _uhr(ab.get("dTimeS")), "an": _uhr(an.get("aTimeS")),
            "ab_echt": ab_echt, "ab_verspaetung": ab_versp,
            "an_echt": an_echt, "an_verspaetung": an_versp,
            "dauer": _dauer(fahrt.get("dur")),
            "datum": tag, "an_roh": an.get("aTimeR") or an.get("aTimeS"), "ab_roh": ab.get("dTimeR") or ab.get("dTimeS"),
            "umstiege": int(fahrt.get("chg") or 0),
            "abschnitte": abschnitte,
        })
    return ergebnis


def _anfrage(start: dict, ziel: dict, datum: str, zeit: str, anzahl: int) -> dict:
    anfrage = {"depLocL": [start], "arrLocL": [ziel], "getPolyline": False, "getPasslist": False,
               "maxChg": 3, "numF": max(1, min(anzahl, 6))}
    if datum:
        anfrage["outDate"] = datum
    if zeit:
        anfrage["outTime"] = zeit
    return anfrage


async def verbindungen(von: str, nach: str, datum: str = "", zeit: str = "",
                       anzahl: int = MAX_VERBINDUNGEN) -> dict:
    """Verbindungen von A nach B, mit Umstiegen und Fußwegen."""
    start, ziel = await _eine(von), await _eine(nach)
    res = await _ruf("TripSearch", _anfrage({"type": "S", "lid": start["id"]}, {"type": "S", "lid": ziel["id"]},
                                            datum, zeit, anzahl))
    return {"von": start["name"], "nach": ziel["name"], "verbindungen": _auswerten(res, anzahl)}


def _punkt(breite: float, laenge: float) -> dict:
    """Eine Koordinate als HAFAS-Ort (Millionstel Grad) — dann gehört der Fußweg vom und zum Haus zur Verbindung."""
    if not (-90 <= breite <= 90 and -180 <= laenge <= 180):
        raise GvhFehler("Diese Koordinaten gibt es nicht.")
    return {"type": "C", "crd": {"x": int(round(laenge * 1_000_000)), "y": int(round(breite * 1_000_000))}}


async def verbindungen_koordinaten(start: tuple[float, float], ziel: tuple[float, float], datum: str = "",
                                   zeit: str = "", anzahl: int = 3) -> list[dict]:
    """Von einem Punkt zu einem Punkt (Tür zu Tür): Fußweg, Fahrten, Umstiege, Fußweg — mit Echtzeit, wo der GVH sie hat."""
    res = await _ruf("TripSearch", _anfrage(_punkt(*start), _punkt(*ziel), datum, zeit, anzahl))
    return _auswerten(res, anzahl)


# ---------- Mit Echtzeit: Tafel und Fahrtverlauf (für die ÜSTRA-Garantie) ----------

def zeitpunkt(datum: str | None, wert) -> datetime | None:
    """HAFAS: Datum JJJJMMTT plus HHMMSS, führende Ziffern sind Folgetage."""
    s = "".join(z for z in str(wert or "") if z.isdigit())
    if len(s) < 6 or not datum:
        return None
    tage, s = (int(s[:-6]) if len(s) > 6 else 0), s[-6:]
    try:
        basis = datetime.strptime(str(datum), "%Y%m%d")
    except ValueError:
        return None
    return (basis + timedelta(days=tage, hours=int(s[0:2]), minutes=int(s[2:4]))).replace(
        tzinfo=config.jetzt().tzinfo)


def _minuten(plan: datetime | None, echt: datetime | None) -> int | None:
    return round((echt - plan).total_seconds() / 60) if plan and echt else None


def referenz(ext_id: str | None) -> str:
    """„de:03241:5844:2:“ (mit Steig) → „de:03241:5844“ — so heißt die Haltestelle im Formular."""
    return ":".join(str(ext_id or "").split(":")[:3])


def _produkt(prod: list, nummer) -> dict:
    return prod[nummer] if isinstance(nummer, int) and 0 <= nummer < len(prod) else {}


def _betreiber(gemeinsam: dict, p: dict) -> str:
    """Wer den Zug fährt — für die Fahrgastrechte, denn danach richtet sich, wohin der Antrag geht. Gemessen am 21.09.2026
    in Hannover Hbf (`common.opL[prodL.oprX].name`): „S-Bahn Hannover (Transdev)“, „metronom“, „DB Regio AG Nord“,
    „WestfalenBahn“, „enno“, „Verkehrsgesellschaft Start Deutschland mbH“, „DB Fernverkehr AG“."""
    betreiber = gemeinsam.get("opL") or []
    nummer = p.get("oprX")
    return str((betreiber[nummer] if isinstance(nummer, int) and 0 <= nummer < len(betreiber) else {}).get("name") or "")


async def tafel_roh(lid: str, von: datetime, anzahl: int = 80) -> list[dict]:
    """Abfahrten ab `von` mit Fahrt-Kennung, Verkehrsmittel und — wo vorhanden — Echtzeit."""
    res = await _ruf("StationBoard", {"type": "DEP", "stbLoc": {"type": "S", "lid": lid},
                                      "date": von.strftime("%Y%m%d"), "time": von.strftime("%H%M00"),
                                      "maxJny": max(1, min(anzahl, 120))})
    gemeinsam = res.get("common") or {}
    prod = gemeinsam.get("prodL") or []
    liste = []
    for j in res.get("jnyL") or []:
        s, p = j.get("stbStop") or {}, _produkt(prod, j.get("prodX"))
        plan, echt = zeitpunkt(j.get("date"), s.get("dTimeS")), zeitpunkt(j.get("date"), s.get("dTimeR"))
        if not plan or not j.get("jid"):
            continue
        liste.append({"jid": j["jid"], "linie": p.get("name") or "?",
                      "kategorie": (p.get("prodCtx") or {}).get("catOut") or "",
                      "richtung": j.get("dirTxt") or "", "plan": plan, "echt": echt,
                      "verspaetung": _minuten(plan, echt), "faellt_aus": bool(s.get("dCncl")),
                      "betreiber": _betreiber(gemeinsam, p)})
    return liste


async def fahrt_verlauf(jid: str) -> dict:
    """Eine Fahrt mit allen Halten, je Halt Plan- und Echtzeit (Ankunft und Abfahrt)."""
    res = await _ruf("JourneyDetails", {"jid": jid, "getPolyline": False})
    gemeinsam = res.get("common") or {}
    orte, prod = gemeinsam.get("locL") or [], gemeinsam.get("prodL") or []
    reise = res.get("journey") or {}
    p, datum = _produkt(prod, reise.get("prodX")), reise.get("date")
    halte = []
    for s in reise.get("stopL") or []:
        ort = orte[s["locX"]] if isinstance(s.get("locX"), int) and s["locX"] < len(orte) else {}
        an_p, an_e = zeitpunkt(datum, s.get("aTimeS")), zeitpunkt(datum, s.get("aTimeR"))
        ab_p, ab_e = zeitpunkt(datum, s.get("dTimeS")), zeitpunkt(datum, s.get("dTimeR"))
        halte.append({"name": ort.get("name") or "?", "reference": referenz(ort.get("extId")),
                      "an_plan": an_p, "an_echt": an_e, "ab_plan": ab_p, "ab_echt": ab_e,
                      "verspaetung": _minuten(an_p, an_e) if an_p else _minuten(ab_p, ab_e),
                      "faellt_aus": bool(s.get("aCncl") or s.get("dCncl"))})
    return {"jid": jid, "linie": p.get("name") or "?", "kategorie": (p.get("prodCtx") or {}).get("catOut") or "",
            "richtung": reise.get("dirTxt") or "", "halte": halte, "betreiber": _betreiber(gemeinsam, p)}
