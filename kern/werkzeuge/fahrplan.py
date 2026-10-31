"""Fahrplan-Werkzeuge — Bus und Bahn im GVH.

Rein lesend, was den GVH angeht: Es gibt nichts zu buchen und nichts zu ändern,
also auch kein Schreibrecht. Die WACHEN („sag mir Bescheid, wenn der 300er
Verspätung hat“) ändern nur Fridays eigene Merkliste, nicht den Fahrplan. Wichtig ist die Ehrlichkeit bei den Zeiten: Am 13.09.2026 lieferte
der GVH nur Fahrplanzeiten, seit 15.09.2026 Echtzeit für zeitnahe Fahrten — aber
nicht für jede. Jede Antwort sagt deshalb, welche Zeiten Echtzeit tragen und
welche nicht; wo keine kommt, wird weder „pünktlich“ noch „verspätet“ behauptet.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from kern import config, konnektoren
from kern.dienste import gvh, standort as standortdienst, uestra, wache

KONNEKTOR = "gvh"

# Kein „kennt der GVH nicht“ mehr: Das war am 13.09. eine Grenze der Schnittstelle,
# seit 15.09. heißt fehlende Echtzeit nur, dass sie für DIESE Fahrten nicht kam.
OHNE_ECHTZEIT = "Das sind Fahrplanzeiten — für diese Fahrten meldet der GVH gerade keine Echtzeit."

GVH_ECHTZEIT = ("Mit Echtzeit des GVH, wo angegeben — eine Prognose kann sich noch ändern. "
                "Wo „keine Echtzeit“ steht, ist es die Fahrplanzeit.")


MIT_ECHTZEIT = ("Zeiten mit Echtzeit-Prognose der ÜSTRA (gilt auch für S-Bahnen) — "
                "eine Prognose kann sich noch ändern.")


def _echtzeit_zusatz(a: dict) -> str:
    """Was die Prognose zu einer Abfahrt sagt — und nichts, wo sie schweigt."""
    if a["faellt_aus"]:
        return "fällt aus"
    if a["verspaetung"] is None:
        return "keine Prognose"
    if a["verspaetung"] >= 1:
        return f"{a['verspaetung']} Min später, voraussichtlich {a['prognose']:%H:%M}"
    if a["verspaetung"] <= -1:
        return f"{-a['verspaetung']} Min früher, voraussichtlich {a['prognose']:%H:%M}"
    return "pünktlich"


def _echtzeit_zeile(a: dict) -> str:
    """Vorne steht die Zeit, zu der die Bahn voraussichtlich KOMMT — danach ist die
    Liste sortiert. Mit der Planzeit vorn las sich die echte Tafel vom 15.09. wie
    ein Sortierfehler („18:17 …“ vor „18:11 … voraussichtlich 18:37“)."""
    richtung = f" Richtung {a['richtung']}" if a["richtung"] else ""
    if a["faellt_aus"] or not a["verspaetung"]:            # ausgefallen, pünktlich oder ohne Prognose
        zusatz = _echtzeit_zusatz(a)
        klammer = a["verspaetung"] is None and not a["faellt_aus"]
        return f"{a['plan']:%H:%M}  {a['linie']}{richtung}" + (f"  ({zusatz})" if klammer else f"  — {zusatz}")
    wie = f"{a['verspaetung']} Min später" if a["verspaetung"] > 0 else f"{-a['verspaetung']} Min früher"
    return f"{a['prognose']:%H:%M}  {a['linie']}{richtung}  — {wie} (Plan {a['plan']:%H:%M})"


async def abfahrten(haltestelle: str) -> str:
    """Was als Nächstes von einer Haltestelle fährt — mit Echtzeit, wo es sie gibt.

    Zuerst die ÜSTRA-Abfahrtstafel (Prognosen, auch für S-Bahnen). Kennt sie die
    Haltestelle nicht oder ist sie weg, gibt es GVH-Fahrplanzeiten wie bisher.
    Passen mehrere Haltestellen, fragt Friday nach — ohne auf den GVH auszuweichen,
    der sonst still eine davon nähme."""
    try:
        stand = await uestra.abfahrten(haltestelle)
    except uestra.Mehrdeutig as exc:
        return str(exc)
    except uestra.Unbekannt:
        return await _abfahrten_fahrplan(haltestelle)
    except Exception:
        return await _abfahrten_fahrplan(haltestelle, "Die Echtzeit der ÜSTRA ist gerade nicht erreichbar.")
    if not stand["abfahrten"]:
        return f"Von {stand['haltestelle']} fährt gerade nichts mehr."
    zeilen = [f"Nächste Abfahrten ab {stand['haltestelle']}:"] + [_echtzeit_zeile(a) for a in stand["abfahrten"]]
    if stand["meldungen"]:
        zeilen.append("Meldungen der ÜSTRA: " + " | ".join(stand["meldungen"]))
    zeilen.append(MIT_ECHTZEIT)
    return "\n".join(zeilen)


def _gvh_abweichung(echt: str, verspaetung: int | None) -> str:
    """Was die GVH-Echtzeit sagt — leer, wo keine kam (dann gilt keine Aussage)."""
    if verspaetung is None:
        return ""
    if verspaetung == 0:
        return "pünktlich"
    return f"{abs(verspaetung)} Min {'später' if verspaetung > 0 else 'früher'}, voraussichtlich {echt}"


async def _abfahrten_fahrplan(haltestelle: str, vorweg: str = "") -> str:
    """Der GVH-Weg — für Haltestellen außerhalb der ÜSTRA-Liste. Echtzeit, wo der
    GVH sie liefert; sonst ausdrücklich Fahrplanzeit."""
    try:
        stand = await gvh.abfahrten(haltestelle)
    except gvh.GvhFehler as exc:
        return str(exc)
    except Exception as exc:
        return f"Die Fahrplanauskunft antwortet gerade nicht ({type(exc).__name__})."
    if not stand["abfahrten"]:
        return f"Von {stand['haltestelle']} fährt gerade nichts mehr."
    mit_echtzeit = any(f.get("verspaetung") is not None for f in stand["abfahrten"])
    zeilen = ([vorweg] if vorweg else []) + [f"Nächste Abfahrten ab {stand['haltestelle']}:"]
    for f in stand["abfahrten"]:
        zeile = f"{f['zeit']}  {f['linie']}"
        if f["richtung"]:
            zeile += f" Richtung {f['richtung']}"
        if f["faellt_aus"]:
            zeile += "  — fällt aus"
        elif f.get("verspaetung") is not None:
            zeile += "  — " + _gvh_abweichung(f.get("echt", ""), f["verspaetung"])
        elif mit_echtzeit:                     # nur markieren, wenn andere Zeilen Echtzeit tragen
            zeile += "  (keine Echtzeit)"
        zeilen.append(zeile)
    zeilen.append(GVH_ECHTZEIT if mit_echtzeit else OHNE_ECHTZEIT)
    return "\n".join(zeilen)


def _wann(datum: str, zeit: str, jetzt: datetime) -> tuple[str, str] | str:
    """(JJJJMMTT oder „“, HHMMSS oder „“) — oder ein Satz, was nicht verstanden wurde.

    Das Modell liefert Datum und Uhrzeit in vielen Schreibweisen oder lässt das Datum weg. Ohne
    Datum galt „ab 7:50“ abends als HEUTE 07:50 — der GVH lieferte dann 02:26 (24.09.2026). Eine
    Uhrzeit, die heute schon vorbei ist, heißt deshalb morgen."""
    zeit, datum = (zeit or "").strip().lower(), (datum or "").strip().lower()
    stunde_min = ""
    if zeit:
        m = re.fullmatch(r"(\d{1,2})[:.](\d{2})(?:[:.]\d{2})?(?:\s*uhr)?", zeit) \
            or re.fullmatch(r"(\d{2})(\d{2})(?:\d{2})?", zeit)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            return f"Die Uhrzeit „{zeit}“ verstehe ich nicht. Bitte als HH:MM angeben, z. B. 07:50."
        stunde_min = f"{int(m.group(1)):02d}{m.group(2)}"
    heute = jetzt.date()
    tag: date | None = None
    if datum in ("", "heute"):
        tag = None
    elif datum == "morgen":
        tag = heute + timedelta(days=1)
    elif datum == "übermorgen":
        tag = heute + timedelta(days=2)
    else:
        try:
            if re.fullmatch(r"\d{8}", datum):
                tag = datetime.strptime(datum, "%Y%m%d").date()
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum):
                tag = date.fromisoformat(datum)
            elif m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?", datum):
                tag = date(int(m.group(3) or heute.year), int(m.group(2)), int(m.group(1)))
                if not m.group(3) and tag < heute:
                    tag = tag.replace(year=tag.year + 1)
            else:
                raise ValueError
        except ValueError:
            return f"Das Datum „{datum}“ verstehe ich nicht. Bitte als TT.MM.JJJJ oder „morgen“ angeben."
    if tag is None and stunde_min and stunde_min < f"{jetzt:%H%M}":
        tag = heute + timedelta(days=1)
    return (tag.strftime("%Y%m%d") if tag and tag != heute else "",
            stunde_min + "00" if stunde_min else "")


async def verbindung(von: str, nach: str, datum: str = "", zeit: str = "") -> str:
    """Wie komme ich von A nach B?"""
    wann = _wann(datum, zeit, config.jetzt())
    if isinstance(wann, str):
        return wann
    datum, zeit = wann
    try:
        stand = await gvh.verbindungen(von, nach, datum, zeit)
    except gvh.GvhFehler as exc:
        return str(exc)
    except Exception as exc:
        return f"Die Fahrplanauskunft antwortet gerade nicht ({type(exc).__name__})."
    if not stand["verbindungen"]:
        return f"Von {stand['von']} nach {stand['nach']} finde ich gerade keine Verbindung."
    gvh_echt = any(x.get("ab_verspaetung") is not None or x.get("an_verspaetung") is not None
                   for v in stand["verbindungen"] for x in [v, *v["abschnitte"]])
    # Die ÜSTRA nur noch als Ersatz: wenn der GVH für die erste Fahrt ab dem Start
    # keine Echtzeit meldet. Klappt es nicht, bleibt es bei den Fahrplanzeiten.
    tafel = []
    if any(_erste_fahrt(v) and _erste_fahrt(v).get("ab_verspaetung") is None for v in stand["verbindungen"]):
        try:
            tafel = (await uestra.abfahrten(von, anzahl=40))["abfahrten"]
        except Exception:
            tafel = []
    geprueft = False
    zeilen = [f"Von {stand['von']} nach {stand['nach']}:"]
    for i, v in enumerate(stand["verbindungen"], 1):
        umstieg = ("ohne Umstieg" if not v["umstiege"]
                   else f"{v['umstiege']} Umstieg" + ("e" if v["umstiege"] > 1 else ""))
        zeile = f"{i}. {v['ab']} – {v['an']} ({v['dauer']}, {umstieg})"
        if any(a.get("faellt_aus") for a in v["abschnitte"]):
            zeile += "  — fällt aus (mindestens ein Abschnitt)"
        elif gvh_echt:
            teile = [f"{wo} " + (_gvh_abweichung(v.get(f"{k}_echt", ""), v.get(f"{k}_verspaetung"))
                                 or "ohne Echtzeit") for wo, k in (("Abfahrt", "ab"), ("Ankunft", "an"))]
            zeile += ("  — " + "; ".join(teile) if v.get("ab_verspaetung") is not None
                      or v.get("an_verspaetung") is not None else "  (keine Echtzeit)")
        zeilen.append(zeile)
        erste = _erste_fahrt(v)
        for a in v["abschnitte"]:
            if a["zu_fuss"]:
                zeilen.append(f"     zu Fuß {a['ab']} {a['von']} → {a['nach']}")
                continue
            richtung = f" Richtung {a['richtung']}" if a["richtung"] else ""
            zeile = f"     {a['linie']}{richtung}: {a['ab']} {a['von']} → {a['an']} {a['nach']}"
            if a.get("faellt_aus"):
                zeile += "  [fällt aus]"
            elif v["umstiege"]:                         # ohne Umstieg steht alles schon oben
                teile = [f"{wo} " + _gvh_abweichung(a.get(f"{k}_echt", ""), a.get(f"{k}_verspaetung"))
                         for wo, k in (("ab", "ab"), ("an", "an")) if a.get(f"{k}_verspaetung")]
                if teile:
                    zeile += f"  [{'; '.join(teile)}]"
            if a is erste and a.get("ab_verspaetung") is None and tafel:   # Ersatz: ÜSTRA am Start
                treffer = next((t for t in tafel if uestra.gleiche_linie(t["linie"], a["linie"])
                                and f"{t['plan']:%H:%M}" == a["ab"][:5]), None)
                if treffer:
                    geprueft = True
                    zeile += f"  [ÜSTRA: {_echtzeit_zusatz(treffer)}]"
            zeilen.append(zeile)
    if gvh_echt:
        zeilen.append(GVH_ECHTZEIT + (" Wo „ÜSTRA“ steht, ist es deren Prognose." if geprueft else ""))
    else:
        zeilen.append("Die erste Fahrt trägt, wo angegeben, die Echtzeit-Prognose der ÜSTRA; Umstiege und "
                      "Ankunft sind Fahrplanzeiten." if geprueft else OHNE_ECHTZEIT)
    return "\n".join(zeilen)


def _erste_fahrt(v: dict) -> dict | None:
    """Der erste Abschnitt einer Verbindung — nur er fährt am Start ab, nur ihn
    kennt die ÜSTRA-Tafel dort. Ein Fußweg vorweg heißt: kein Abgleich."""
    abschnitte = v.get("abschnitte") or []
    return abschnitte[0] if abschnitte and not abschnitte[0]["zu_fuss"] else None


async def haltestellen_hier() -> str:
    """Welche Haltestellen sind von Eike aus die nächsten?

    Hängt an ZWEI Freigaben: am GVH (dieses Modul) und am Standort. Ist der Standort
    aus, sagt das Werkzeug das, statt eine Haltestelle zu erfinden.

    Am 20.09.2026 versuchte Friday das über die Websuche und riet: `verbindung` suchte
    „Musterdorf, Schlehenweg“ als Haltestelle und landete in Meschede.
    """
    if not konnektoren.benutzbar("standort"):
        return ("Dafür bräuchte ich deinen Standort, und der ist auf der Freigaben-Seite noch aus. "
                "Sag mir sonst, in welchem Ort du bist, dann suche ich die Haltestellen dort.")
    pos = standortdienst.aktuell()
    if not pos or pos["alter_s"] > standortdienst.FRISCH_S:
        try:
            pos = await standortdienst.frisch_holen() or pos
        except Exception:
            pass
    if not pos:
        return ("Ich habe gerade keinen Standort von dir. "
                + (standortdienst.grund() or "Ist Friday auf dem Handy offen?"))
    try:
        liste = await gvh.haltestellen_um(pos["breite"], pos["laenge"])
    except gvh.GvhFehler as exc:
        return str(exc)
    except Exception as exc:
        return f"Die Fahrplanauskunft antwortet gerade nicht ({type(exc).__name__})."
    if not liste:
        return "Im Umkreis von drei Kilometern finde ich keine Haltestelle im GVH."
    zeilen = [f"{h['name']}" + (f" — {h['meter']} m Luftlinie" if h["meter"] is not None else "")
              for h in liste]
    ungenau = pos.get("genauigkeit") is None or pos["genauigkeit"] > standortdienst.UNGENAU_M
    text = "Haltestellen in deiner Nähe:\n" + "\n".join(zeilen)
    text += ("\n(Luftlinie, nicht der Fußweg. Die Namen sind vollständig — genau so für abfahrten "
             "oder verbindung benutzen.)")
    if ungenau:
        text += " Dein Standort ist gerade nur grob bekannt, die Entfernungen sind entsprechend ungenau."
    return text


# ---------- Abfahrten im Blick ----------
# Eike wollte, dass Friday von selbst meldet, wenn sein Bus Verspätung hat
# (20.09.2026). Hier stehen nur die Werkzeuge; das Nachsehen macht
# `kern/dienste/wache.py` im Hintergrund.

async def fahrt_merken(haltestelle: str, linie: str = "", richtung: str = "", ab: str = "",
                       bis: str = "", tage: str = "", schwelle: int = wache.SCHWELLE_MIN) -> str:
    try:
        w = await wache.anlegen(haltestelle, linie, richtung, ab, bis, tage, schwelle)
    except wache.WachenFehler as exc:
        return str(exc)
    except Exception as exc:
        return f"Das hat nicht geklappt ({type(exc).__name__})."
    from kern.dienste import benachrichtigungen
    zusatz = ("" if benachrichtigungen.an(wache.ART) else
              " Achtung: „Abfahrten im Blick“ ist in den Einstellungen gerade aus — "
              "so kommt die Meldung nicht an.")
    return f"Gemerkt: {wache.text(w)}. Ich melde mich, wenn da etwas klemmt.{zusatz}"


async def fahrten_gemerkt() -> str:
    liste = wache.alle()
    if not liste:
        return ("Ich habe keine Fahrt im Blick. Sag mir Haltestelle, Linie und Zeitfenster, "
                "dann passe ich auf.")
    zeilen = [f"{w['id']}: {wache.text(w)}" + ("" if w.get("aktiv", True) else " — pausiert")
              for w in liste]
    return "Diese Fahrten habe ich im Blick:\n" + "\n".join(zeilen)


async def fahrt_vergessen(welche: str) -> str:
    treffer = wache.finden(welche)
    if not treffer:
        # Auch die Nummer aus `fahrten_gemerkt` zulassen — Eike liest sie vor.
        weg = wache.entfernen(str(welche).strip())
        if weg:
            return f"Nicht mehr im Blick: {wache.text(weg)}."
        return f"„{welche}“ habe ich nicht im Blick. Frag mich, was ich mir gemerkt habe."
    if len(treffer) > 1:
        return ("Dazu passen mehrere: " + "; ".join(f"{w['id']}: {wache.text(w)}" for w in treffer)
                + ". Welche soll weg?")
    weg = wache.entfernen(treffer[0]["id"])
    return f"Nicht mehr im Blick: {wache.text(weg)}."


ERKLAERUNGEN = [
    {
        "name": "haltestellen_hier",
        "description": ("Die nächsten Bus- und Bahnhaltestellen um Eikes aktuellen Standort, mit "
                        "Entfernung. Benutzen bei 'welche Haltestelle ist die nächste', 'wo fährt hier "
                        "ein Bus', und IMMER bevor du eine Verbindung 'von hier' suchst — die Namen aus "
                        "der Liste nimmst du dann unverändert für abfahrten oder verbindung. Rate nie "
                        "eine Haltestelle aus einem Straßennamen: 'Musterdorf, Schlehenweg' gibt es nicht."),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "abfahrten",
        "description": ("Nächste Abfahrten von einer Haltestelle im Großraum Hannover — Linie, Richtung, "
                        "Planzeit und, wo es sie gibt, die Echtzeit-Prognose mit Verspätung (auch S-Bahn). "
                        "Benutzen bei 'wann fährt die nächste Bahn', 'hat die S5 Verspätung', "
                        "'was fährt gleich am Kröpcke'."),
        "parameters": {"type": "OBJECT", "properties": {
            "haltestelle": {"type": "STRING", "description": "Name der Haltestelle, z.B. Kröpcke oder Bennigsen"},
        }, "required": ["haltestelle"]},
    },
    {
        "name": "verbindung",
        "description": ("Verbindung von einer Haltestelle zu einer anderen im GVH, mit Umstiegen und "
                        "Fußwegen. Benutzen bei 'wie komme ich von X nach Y', 'wann muss ich los'. "
                        "Sage genau die Zeiten weiter, die zurückkommen. Abfahrt, Umstiege und Ankunft "
                        "tragen oft die Echtzeit des GVH; wo 'keine Echtzeit' steht, sind es Fahrplanzeiten."),
        "parameters": {"type": "OBJECT", "properties": {
            "von": {"type": "STRING", "description": "Starthaltestelle, z.B. Bennigsen"},
            "nach": {"type": "STRING", "description": "Zielhaltestelle, z.B. Hannover Hauptbahnhof"},
            "datum": {"type": "STRING", "description": "Abfahrtstag: „morgen“, „25.09.“ oder JJJJMMTT; leer = heute"},
            "zeit": {"type": "STRING", "description": "Abfahrtszeit als HH:MM, z. B. 07:50; leer = jetzt. "
                                                       "Ist die Zeit heute schon vorbei und du gibst kein Datum an, gilt morgen"},
        }, "required": ["von", "nach"]},
    },
    {
        "name": "fahrt_merken",
        "description": ("Merkt sich eine Fahrt, auf die Eike regelmäßig wartet, und meldet sich von "
                        "selbst aufs Handy, wenn sie Verspätung hat oder ausfällt. Benutzen bei „sag mir "
                        "Bescheid, wenn mein Bus Verspätung hat“, „pass morgens auf die Bahn nach "
                        "Hannover auf“. Frag nach, was du nicht weißt — Haltestelle und Zeitfenster "
                        "brauche ich wirklich, Linie und Richtung machen es genauer. Die Haltestelle muss "
                        "eine mit Echtzeit sein; sonst sage ich es dir."),
        "parameters": {"type": "OBJECT", "properties": {
            "haltestelle": {"type": "STRING", "description": "Wo er einsteigt, z.B. Bennigsen"},
            "linie": {"type": "STRING", "description": "Liniennummer, z.B. 300 oder S5 — leer heißt alle"},
            "richtung": {"type": "STRING", "description": "Fahrtziel der Linie, z.B. Hannover"},
            "ab": {"type": "STRING", "description": "Beginn des Zeitfensters als 07:10"},
            "bis": {"type": "STRING", "description": "Ende des Zeitfensters als 07:40"},
            "tage": {"type": "STRING", "description": ("werktags, täglich, am Wochenende oder Kürzel "
                                                       "wie „Mo Di Do“")},
            "schwelle": {"type": "INTEGER", "description": "Ab wie vielen Minuten Verspätung, Standard 5"},
        }, "required": ["haltestelle"]},
    },
    {
        "name": "fahrten_gemerkt",
        "description": ("Sagt, welche Fahrten Friday im Blick hat. Benutzen bei „worauf passt du auf“ "
                        "und immer, bevor du eine wegnimmst."),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "fahrt_vergessen",
        "description": ("Nimmt eine gemerkte Fahrt wieder weg. Benutzen bei „pass nicht mehr auf den "
                        "300er auf“. Passen mehrere, frag nach, welche gemeint ist."),
        "parameters": {"type": "OBJECT", "properties": {
            "welche": {"type": "STRING", "description": "Haltestelle, Linie oder die Nummer aus fahrten_gemerkt"},
        }, "required": ["welche"]},
    },
]

# Ändert etwas und verlangt kein „ja“: nach fremdem Text im selben Zug gesperrt (katalog.ausfuehren).
NICHT_NACH_FREMDTEXT = {"fahrt_merken", "fahrt_vergessen"}

WERKZEUGE = {"abfahrten": abfahrten, "verbindung": verbindung,
             "haltestellen_hier": haltestellen_hier,
             "fahrt_merken": fahrt_merken, "fahrten_gemerkt": fahrten_gemerkt,
             "fahrt_vergessen": fahrt_vergessen}
