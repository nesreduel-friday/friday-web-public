"""Werkzeug `verbindung_karte`: konkrete Wege zu einem Ziel — Bus und Bahn, zu Fuß, Auto — als Karte auf der Übersicht.

Der Start ist Eikes Standort (wie beim Werkzeug `standort`: frisch von der App, aus fremdem Text nie) oder ein genannter Ort. Gerechnet wird
nicht vom Modell, sondern von echten Quellen (`kern/dienste/routen.py`); das Modell bekommt die Ergebnisse als Text und sagt sie weiter.
"""

from __future__ import annotations

from datetime import datetime

from kern import config, konnektoren
from kern.dashboard import friday_karten
from kern.dienste import homeassistant, routen, standort
from kern.werkzeuge.standort import _vor
from kern.werkzeuge import fahrplan

FREMD_ABGELEHNT = ("Der Wunsch stammt aus fremdem Text (Mail, Brief oder Webseite). Daraus gebe ich keinen Standort heraus — "
                   "frag mich selbst danach.")
SCHON_DA_M = 100
ROUTE_GENAU_M = 300          # gröber ist kein Start: 25.09.2026 meldete der PC „100 km“ und die Karte startete trotzdem
HINWEIS_GENAU_M = 100        # bis hier ohne Hinweis; darüber kann der Start ein Stück daneben liegen

ERKLAERUNGEN = [
    {
        "name": "verbindung_karte",
        "description": ("Legt eine Karte mit konkreten Wegen zu einem Ziel auf die Übersicht: Bus und Bahn (GVH, mit Fußwegen, Umstiegen und "
                        "Echtzeit), zu Fuß und mit dem Auto — berechnet von echten Routendiensten, vom Standort aus. Tippt Eike auf die "
                        "Karte, kann er Fuß- und Autoweg in Google Maps öffnen. Benutzen bei 'wie komme ich zu …', 'zeig mir den Weg "
                        "nach …', 'wie lange brauche ich bis …', 'Bus oder Auto?'. Nur wenn er von SEINEM Standort aus fragt, ohne "
                        "Start; sonst start angeben. Sag danach genau das weiter, was zurückkommt (Zeiten, Umstiege, 'ohne Verkehr'), "
                        "erfinde nichts dazu und lies nicht die ganze Karte vor, nur das Wichtigste. IMMER ZUERST aufrufen, bevor du in "
                        "diesem Zug etwas suchst oder Links liest — danach wird der Standort abgelehnt. Kann bis zu einer halben Minute "
                        "dauern (die App fragt erst jetzt sein Handy)."),
        "parameters": {"type": "OBJECT", "properties": {
            "ziel": {"type": "STRING", "description": "Wohin, so wie es heißt: 'Hannover Hauptbahnhof', 'Penny Bennigsen', 'Hauptstraße 5, Musterhausen'"},
            "start": {"type": "STRING", "description": "Nur wenn NICHT von Eikes Standort: Ort oder Adresse des Starts"},
            "datum": {"type": "STRING", "description": "Abfahrtstag: 'morgen', '26.09.' oder JJJJMMTT; leer = heute"},
            "zeit": {"type": "STRING", "description": "Abfahrtszeit HH:MM; leer = jetzt"},
            "verkehrsmittel": {"type": "STRING", "description": "Optional eingrenzen: 'bus und bahn', 'zu fuss', 'auto' (auch mehrere); leer = alle drei"},
        }, "required": ["ziel"]},
    },
]


def _modi(text: str) -> tuple[str, ...]:
    t = (text or "").lower().replace("ß", "ss")
    gewaehlt = []
    if any(w in t for w in ("bus", "bahn", "oepnv", "öpnv", "zug", "nahverkehr", "gvh")):
        gewaehlt.append("oepnv")
    if any(w in t for w in ("fuss", "fuß", "laufen", "gehen", "zu fuss")):
        gewaehlt.append("fuss")
    if any(w in t for w in ("auto", "fahren", "pkw")):
        gewaehlt.append("auto")
    return tuple(gewaehlt) or routen.ALLE_MODI


def _meter(m: float) -> str:
    return f"{int(round(m, -1))} m" if m < 950 else f"{m / 1000:.1f} km".replace(".", ",")


def _wann(datum: str, zeit: str, jetzt: datetime):
    w = fahrplan._wann(datum, zeit, jetzt)
    if isinstance(w, str):
        return w
    tag, uhr = w
    if not tag and not uhr:
        return None
    try:
        basis = datetime.strptime(tag, "%Y%m%d") if tag else datetime(jetzt.year, jetzt.month, jetzt.day)
        return basis.replace(hour=int(uhr[:2]), minute=int(uhr[2:4]), tzinfo=jetzt.tzinfo) if uhr else basis.replace(
            hour=jetzt.hour, minute=jetzt.minute, tzinfo=jetzt.tzinfo)
    except ValueError:
        return f"Das Datum „{datum}“ verstehe ich nicht."


def _beschreibung(o: dict) -> str:
    if o["modus"] == "oepnv":
        wege = []
        for a in o.get("abschnitte", []):
            if a["art"] == "fuss":
                wege.append(f"{a['min']} Min zu Fuß")
            else:
                wege.append(f"{a['linie']} {a['ab']} {a['von']} → {a['an']} {a['nach']}")
        umst = "ohne Umstieg" if not o.get("umstiege") else f"{o['umstiege']} Umstieg" + ("e" if o["umstiege"] > 1 else "")
        echt = "mit Echtzeit" if o.get("echtzeit") else "Fahrplanzeiten, keine Echtzeit"
        versp = f", Ankunft {o['verspaetung_min']:+d} Min gegenüber Plan" if o.get("verspaetung_min") else ""
        return f"Bus & Bahn: ab {o['ab']}, an {o['an']} ({o['dauer_min']} Min, {umst}, {echt}{versp}): " + "; ".join(wege)
    wie = "Zu Fuß" if o["modus"] == "fuss" else "Auto"
    zusatz = f" — {o['hinweis']}" if o.get("hinweis") else ""
    return f"{wie}: {o['dauer_min']} Min ({_meter(o.get('distanz_m', 0))}), an {o['an']}{zusatz}"


async def _kandidaten(frisch: bool = True) -> list[dict]:
    """Eikes Position aus allen Quellen — die Logik sitzt in `dienste/standort.py`, damit auch die Erinnerungen
    „bei Ankunft zu Hause“ dieselbe Quelle nutzen (App, OwnTracks und Home Assistant; das Handy wird im
    Hintergrund nie gefragt)."""
    return await standort.eigene_kandidaten(frisch)


BEWEGT_M = 150               # weniger Bewegung: der Ortsname der Karte bleibt (spart die Nominatim-Abfrage)


def _woher(k: dict) -> str:
    """Quelle für den Satz: OwnTracks und Home Assistant beim Namen, die App nach Gerät."""
    return {"OwnTracks": "von OwnTracks", "Home Assistant": "von Home Assistant"}.get(
        k.get("quelle"), "vom PC" if k.get("geraet") not in ("Handy", "") else "von der App")


async def _eigener_start(hinweise: list[str], frisch: bool = True, behalte: dict | None = None) -> tuple[dict | None, str]:
    """(Start, Fehlertext). Nimmt die frischeste Position, die genau genug ist — sonst lieber nichts als einen falschen Start.
    `frisch=False`: das Handy nicht fragen (Live-Auffrischung). `behalte`: der bisherige Start — bewegt man sich kaum, bleibt sein Name."""
    kandidaten = await _kandidaten(frisch)
    brauchbar = [k for k in kandidaten if k["genau"] is not None and k["genau"] <= ROUTE_GENAU_M]
    if not brauchbar:
        if kandidaten:
            k = min(kandidaten, key=lambda k: k["alter_s"])
            wie = f"rund {_meter(k['genau'])}" if k["genau"] is not None else "unbekannt"
            von = _woher(k)
            return None, (f"Dein Standort ist zu ungenau für eine Route (Genauigkeit {wie}, {von}) — der Start läge womöglich woanders. "
                          "Öffne Friday auf dem Handy (dort ist die Ortung genau), oder nenn mir den Start.")
        return None, ("Dafür brauche ich deinen Standort, und den habe ich gerade nicht. "
                      + (standort.grund() or "Ist Friday auf dem Handy offen, und hast du den Standort erlaubt?") + " Oder nenn mir den Start.")
    k = min(brauchbar, key=lambda k: (k["alter_s"], k["quelle"] != "App"))
    if k["genau"] > HINWEIS_GENAU_M:
        hinweise.append("Dein Standort ist nicht ganz genau — der Start kann ein Stück daneben liegen.")
    if k["alter_s"] > standort.AKTUELL_S:
        hinweise.append(f"Dein Standort ist {_vor(k['alter_s'])} gemeldet — du kannst inzwischen woanders sein.")
    name = ""
    if behalte and behalte.get("name") and standort.abstand_m((behalte["breite"], behalte["laenge"]), (k["breite"], k["laenge"])) < BEWEGT_M:
        name = behalte["name"]
    else:
        try:
            name = await standort.ortsname(k["breite"], k["laenge"])
        except Exception:
            name = ""
    hinweise.append(f"Start: Standort {_woher(k)}, Genauigkeit rund {_meter(k['genau'])}.")
    return {"name": name or "Dein Standort", "breite": k["breite"], "laenge": k["laenge"]}, ""


async def verbindung_karte(ziel: str, start: str = "", datum: str = "", zeit: str = "", verkehrsmittel: str = "",
                           fremde_inhalte: bool = False) -> str:
    if fremde_inhalte:
        return FREMD_ABGELEHNT
    ziel = (ziel or "").strip()
    if not ziel:
        return "Wohin denn?"
    jetzt = config.jetzt()
    wann = _wann(datum, zeit, jetzt)
    if isinstance(wann, str):
        return wann

    hinweise: list[str] = []
    if (start or "").strip():
        try:
            starts = await standort.ort_finden(start.strip())
        except Exception as exc:
            return f"Die Ortssuche antwortet gerade nicht ({type(exc).__name__})."
        if not starts:
            return f"Den Start „{start.strip()}“ finde ich auf der Karte nicht. Sag es mir genauer."
        von = {"name": starts[0]["name"], "breite": starts[0]["breite"], "laenge": starts[0]["laenge"]}
    else:
        # Der Standort ist Eikes eigene, ausgeschaltete Freigabe — auch wenn dieses Werkzeug sonst nichts braucht.
        if not (konnektoren.benutzbar("standort") or konnektoren.benutzbar("personenstandort")
                or konnektoren.benutzbar(homeassistant.KONNEKTOR)):
            return ("Für den Start von deinem Standort aus müsste „Standort“, „Standort der Familie“ oder "
                    "„Home Assistant“ auf der Freigaben-Seite eingeschaltet sein. Oder nenn mir den Start.")
        von, fehler = await _eigener_start(hinweise)
        if fehler:
            return fehler

    hier = (von["breite"], von["laenge"])
    try:
        treffer = await standort.ort_finden(ziel, nahe=hier)
    except Exception as exc:
        return f"Die Ortssuche antwortet gerade nicht ({type(exc).__name__})."
    if not treffer:
        return f"„{ziel}“ finde ich auf der Karte nicht. Sag es mir genauer, gern mit Ort oder Straße."
    for t in treffer:
        t["weg"] = standort.abstand_m(hier, (t["breite"], t["laenge"]))
    treffer.sort(key=lambda t: t["weg"])
    gefunden = treffer[0]
    if gefunden["weg"] < SCHON_DA_M:
        return f"Du bist schon da: {gefunden['name']} liegt nur {_meter(gefunden['weg'])} entfernt."
    weitere = [t for t in treffer[1:3] if t["weg"] - gefunden["weg"] < 20000]

    modi = _modi(verkehrsmittel)
    ergebnis = await routen.alle((von["breite"], von["laenge"]), (gefunden["breite"], gefunden["laenge"]),
                                 verkehrsmittel=modi, wann=wann, jetzt=jetzt)
    if not ergebnis["optionen"]:
        grund = " ".join(a["text"] for a in ergebnis["ausfaelle"]) or "Es kam keine Verbindung zurück."
        return f"Von {von['name']} nach {gefunden['name']} konnte ich keine Verbindung berechnen. {grund}"
    if any(o["modus"] == "auto" for o in ergebnis["optionen"]):
        hinweise.append("Autozeiten sind ohne aktuellen Verkehr gerechnet.")
    ziel_karte = {"name": gefunden["name"], "breite": gefunden["breite"], "laenge": gefunden["laenge"]}
    try:
        # Der Auftrag macht die Karte live: `dashboard/routelive.py` rechnet sie mit neuem Standort und neuen Abfahrten neu.
        await friday_karten.legen_route({"start": von, "ziel": ziel_karte, "optionen": ergebnis["optionen"],
                                         "schnellste": ergebnis["schnellste"], "ausfaelle": ergebnis["ausfaelle"], "hinweise": hinweise,
                                         "auftrag": {"ziel": ziel_karte, "start": von if (start or "").strip() else None,
                                                     "modi": list(modi), "wann": wann.isoformat() if wann else ""}})
    except friday_karten.KarteFehler as exc:
        return f"Die Karte ging nicht: {exc}"

    zeilen = [f"Die Verbindungskarte liegt auf der Übersicht. Von {von['name']} nach {gefunden['name']}"
              + (f" (ab {wann:%d.%m. %H:%M})" if wann else " (ab jetzt)") + ":"]
    zeilen += [f"- {_beschreibung(o)}" for o in ergebnis["optionen"]]
    if ergebnis["schnellste"] is not None:
        s = ergebnis["optionen"][ergebnis["schnellste"]]
        zeilen.append(f"Früheste Ankunft: {s['titel']} um {s['an']}.")
    zeilen += [f"Nicht verfügbar: {a['text']}" for a in ergebnis["ausfaelle"]]
    zeilen += hinweise
    if weitere:
        zeilen.append("Ich habe „" + gefunden["name"] + "“ genommen. Es gibt auch: "
                      + "; ".join(f"{t['name']} ({_meter(t['weg'])})" for t in weitere) + ".")
    zeilen.append("Zu Fuß und Auto kann Eike auf der Karte in Google Maps öffnen. Die Karte liegt schon auf der Übersicht — lege keine zweite Karte an.")
    return "\n".join(zeilen)


verbindung_karte.kennt_fremde_inhalte = True

WERKZEUGE = {"verbindung_karte": verbindung_karte}
