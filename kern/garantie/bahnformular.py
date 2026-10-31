"""Die Anträge der Bahn-Fahrgastrechte zum Ausdrucken und Abtippen (21.09.2026).

- Servicecenter Fahrgastrechte: das offizielle „Fahrgastrechte-Formular“ der DB (Formular 2025, ME/08/25, 60 Felder),
  je Fall eines, dazu beim Sammelantrag einer Zeitkarte ein Anschreiben mit der Rechnung. Die Vorlage liegt unverändert in
  `vorlagen/` (von cms.static-bahn.de, geladen am 21.09.2026); ein Test prüft ihre Feldnamen, damit eine neue Fassung
  nicht still leere Formulare erzeugt.
- S-Bahn Hannover (Transdev): ein Online-Formular — alle Angaben in seiner Reihenfolge zum Abtippen und das Anschreiben
  als Beleg zum Hochladen.

Was bewusst NICHT im Formular steht:
- IBAN und BIC. Die IBAN verlässt den Server nie im Klartext (wie bei der Garantie); ein PDF landet auf dem Handy, im
  Drucker, vielleicht in einer Mail. Die Person trägt sie von Hand ein.
- Datum und Unterschrift. Unterschreiben muss der Ticketinhaber selbst — bei Freunden also der Freund, nie Eike.

Gefüllt wird mit pypdf (nur die Werte), gezeichnet mit PDFium: Die Felder sind Kästchenfelder (ein Zeichen je Kästchen),
das zeichnet PDFium richtig, pypdf nicht. Die Formularseiten gehen deshalb als Bild ins fertige PDF (zum Drucken
gedacht); das Anschreiben ist echter Text.

Felder mit Höchstlänge (Namen 20, Bahnhof 26, Straße 33 Zeichen): Passt ein Wert nicht, bleibt das Feld leer und die
Person schreibt ihn von Hand — gekürzt wird nichts, sonst stünde etwas Falsches im Antrag.
"""

from __future__ import annotations

import io
import re
import textwrap
from datetime import date
from pathlib import Path

from kern.garantie import ablage, antrag, bahnablage, fahrgastrechte

VORLAGE = Path(__file__).parent / "vorlagen" / "fahrgastrechte-formular-2025.pdf"
LAENGEN = {"planned_departure_station": 26, "planned_destination_station": 26, "further_text": 26,
           "ticket_season_number": 18, "ticket_digital_number": 18, "compensation_accountholder": 41,
           "compensation_referencenumber": 41, "personal_firstname": 20, "personal_lastname": 20, "personal_street": 33,
           "personal_housenumber": 7, "personal_postcode": 5, "personal_city": 28, "personal_emailaddress": 41}
FELDNAMEN = {"planned_departure_station": "Startbahnhof", "planned_destination_station": "Zielbahnhof",
             "ticket_season_number": "Zeitkarten-Nummer", "ticket_digital_number": "Auftrags-Nummer",
             "compensation_accountholder": "Kontoinhaber", "personal_firstname": "Vorname", "personal_lastname": "Name",
             "personal_street": "Straße", "personal_housenumber": "Hausnummer", "personal_postcode": "PLZ",
             "personal_city": "Wohnort", "personal_emailaddress": "E-Mail-Adresse"}
ANREDE = {"Herr": "/Herr", "Frau": "/Frau", "Divers": "/Neutrale"}
MASSSTAB = 2.0                  # PDFium: 2 × 72 dpi — gut lesbar gedruckt, gut 200 KB je Seite


def bahnhof(name: str) -> str:
    """So, wie das Formular es möchte („so detailliert wie möglich, z. B. Berlin Hbf“): „Hannover, Hauptbahnhof“ →
    „Hannover Hbf“, „S+U Berlin Hauptbahnhof [Gleis …]“ → „Berlin Hbf“, „Bennigsen Bahnhof“ → „Bennigsen“."""
    n = re.sub(r"\[[^\]]*(\]|$)", " ", name or "")          # „[Gleis …]“ — auch ohne schließende Klammer
    n = re.sub(r"^\s*(S\+U|S|U)\s+", "", n)
    n = n.replace(",", " ")
    n = re.sub(r"\bHauptbahnhof\b|\bHbf\.?", "Hbf", n)
    n = re.sub(r"\s+Bahnhof\s*$|\s+Bhf\.?\s*$", "", n)
    return " ".join(n.split())


def strasse_teilen(text: str) -> tuple[str, str]:
    """„Hauptstraße 12a“ → („Hauptstraße“, „12a“). Ohne erkennbare Nummer bleibt alles bei der Straße."""
    m = re.fullmatch(r"\s*(.+?)\s+(\d+\s*[a-zA-Z]?(?:\s*[-/]\s*\d+\s*[a-zA-Z]?)?)\s*", text or "")
    return (m.group(1), m.group(2).replace(" ", "")) if m else ((text or "").strip(), "")


def _nummer_der_karte(ticket: dict, angaben: dict) -> str:
    """Die Zeitkarten-Nummer fürs Formular: beim Deutschlandticket die Abo-Nummer, sonst die Ticketnummer."""
    if fahrgastrechte.kartenart(ticket) == "dticket":
        return angaben.get("abo_nummer") or ticket.get("abo_nummer") or ""
    return ticket.get("ticketnummer") or ""


def felder(fall: dict, entwurf: dict, angaben: dict, ticket: dict | None, nummer: int, anzahl: int) -> tuple[dict, dict, list[str]]:
    """(Textfelder, Knöpfe, was von Hand dazu muss) für EIN Formular."""
    ab, an, echt = antrag._zeit(fall["ab"]), antrag._zeit(fall["an"]), antrag._zeit(fall.get("an_echt"))
    strasse, hausnummer = strasse_teilen(angaben.get("strasse") or "")
    t = {"planned_day": f"{ab:%d}", "planned_month": f"{ab:%m}", "planned_year": f"{ab:%y}",
         "planned_departure_station": bahnhof(fall["start"]), "planned_departure_hours": f"{ab:%H}",
         "planned_departure_minutes": f"{ab:%M}", "planned_destination_station": bahnhof(fall["ziel"]),
         "planned_destination_hours": f"{an:%H}", "planned_destination_minutes": f"{an:%M}",
         "compensation_accountholder": angaben.get("kontoinhaber") or "",
         "personal_firstname": angaben.get("vorname") or "", "personal_lastname": angaben.get("nachname") or "",
         "personal_street": strasse, "personal_housenumber": hausnummer, "personal_postcode": angaben.get("plz") or "",
         "personal_city": angaben.get("ort") or "", "personal_emailaddress": angaben.get("email") or ""}
    if echt:
        t.update(arrived_day=f"{echt:%d}", arrived_month=f"{echt:%m}", arrived_year=f"{echt:%y}", arrived_hours=f"{echt:%H}",
                 arrived_minutes=f"{echt:%M}")
    k = {"planned_direction": "/R" if fall.get("rueckfahrt") else "/Hin", "compensation": "/Geld"}
    if angaben.get("anrede") in ANREDE:
        k["personal"] = ANREDE[angaben["anrede"]]
    if angaben.get("email"):
        k["personal_email"] = "/Ja"
    v = fall.get("verspaetung") or 0
    if v >= fahrgastrechte.ZEITKARTE_AB:
        k["journey"] = "/Versp"                    # „Verspätung am Ziel (mind. 60 Minuten)“
    else:
        # Ein Fall unter 60 Minuten (nur beim Sammelantrag eines Deutschlandtickets): Das Kästchen „mind. 60 Minuten“
        # wäre falsch. Stattdessen „Sonstiges“ mit Verweis aufs Anschreiben — die echte Ankunft steht ohnehin im Formular.
        k["further_other"] = "/Ja"
        t["further_text"] = f"{v} Min, Anschreiben {nummer}/{anzahl}"
    if fall.get("fahrkarte") == "einzel":
        if fall.get("auftrag"):
            k["ticket_digital"] = "/Ja"
            t["ticket_digital_number"] = fall["auftrag"]
        else:
            k["ticket_other"] = "/Ja"
    else:
        k["ticket_season"] = "/Ja"
        t["ticket_season_number"] = _nummer_der_karte(ticket or {}, angaben)
        von, bis = entwurf.get("von") or "", entwurf.get("bis") or ""
        if von and bis:
            t["compensation_referencenumber"] = f"Sammelantrag {von[8:10]}.{von[5:7]}.–{bis[8:10]}.{bis[5:7]}.{bis[:4]}"
    notizen = []
    for feld, wert in list(t.items()):
        if feld in LAENGEN and len(wert) > LAENGEN[feld]:
            t[feld] = ""
            notizen.append(f"{FELDNAMEN.get(feld, feld)} passt nicht ins Formular ({len(wert)} statt {LAENGEN[feld]} Zeichen) "
                           f"— bitte von Hand: {wert}")
    for feld in ("planned_departure_station", "planned_destination_station", "personal_firstname", "personal_lastname",
                 "personal_street", "personal_postcode", "personal_city"):
        if not t.get(feld) and not any(FELDNAMEN.get(feld, "") in n for n in notizen):
            notizen.append(f"{FELDNAMEN.get(feld, feld)} fehlt — bitte von Hand eintragen.")
    if not hausnummer and strasse:
        notizen.append("Die Hausnummer ließ sich nicht von der Straße trennen — bitte im Formular prüfen.")
    return t, k, notizen


def _formular_bytes(texte: dict, knoepfe: dict) -> bytes:
    """Die Vorlage mit Werten — Felder bleiben Felder (gezeichnet wird danach mit PDFium)."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject
    w = PdfWriter(clone_from=PdfReader(str(VORLAGE)))
    # Die Vorlage trägt Nutzungsrechte (/Perms); nach dem Füllen wären sie ungültig und Adobe Reader würde warnen.
    if "/Perms" in w._root_object:
        del w._root_object["/Perms"]
    for seite in w.pages:
        namen = set()
        for a in seite.get("/Annots") or []:
            wid = a.get_object()
            eltern = wid.get("/Parent")
            feld = eltern.get_object() if eltern else wid
            namen.add(str(feld.get("/T")))
            name = str(feld.get("/T"))
            if name in knoepfe and "/AP" in wid:
                zustaende = [z for z in wid["/AP"]["/N"].keys() if z != "/Off"]
                treffer = next((z for z in zustaende if str(z).startswith(knoepfe[name])), None)
                wid[NameObject("/AS")] = treffer if treffer is not None else NameObject("/Off")
                if treffer is not None:
                    feld[NameObject("/V")] = treffer
        seitenwerte = {k: v for k, v in texte.items() if k in namen}
        if seitenwerte:
            w.update_page_form_field_values(seite, seitenwerte, auto_regenerate=False)
    puffer = io.BytesIO()
    w.write(puffer)
    return puffer.getvalue()


def _bilder(pdf_bytes: bytes) -> list:
    """Die Seiten mit gezeichneten Feldern — PDFium ist nicht threadsicher, deshalb unter der gemeinsamen Sperre."""
    import pypdfium2 as pdfium
    from kern.schule.buecher import PDF_SPERRE
    bilder = []
    with PDF_SPERRE:
        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            pdf.init_forms()
            for i in range(len(pdf)):
                seite = pdf[i]
                # Graustufen und JPEG-Qualität 60: zum Drucken gedacht (gemessen: gut 180 KB je Seite statt 220 KB).
                bilder.append(seite.render(scale=MASSSTAB, may_draw_forms=True).to_pil().convert("L"))
                seite.close()
        finally:
            pdf.close()
    return bilder


def _pdf_text(text: str) -> bytes:
    roh = (text or "").replace("→", "->").encode("cp1252", errors="replace")
    return roh.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _textseite(writer, zeilen: list[tuple[str, str, float]]) -> None:
    """Eine A4-Seite aus Zeilen (Schrift F1 normal, F2 fett, F3 Schreibmaschine; Größe) — Helvetica/Courier aus den
    Standardschriften jedes PDF-Betrachters, WinAnsi-kodiert (Umlaute, €, „“)."""
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    seite = writer.add_blank_page(width=595.28, height=841.89)
    schriften = DictionaryObject()
    for kurz, name in (("/F1", "/Helvetica"), ("/F2", "/Helvetica-Bold"), ("/F3", "/Courier")):
        schriften[NameObject(kurz)] = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject(name), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
    seite[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): schriften})
    y, teile = 790.0, []
    for schrift, text, groesse in zeilen:
        if text == "":
            y -= groesse * 0.8
            continue
        teile.append(b"BT /" + schrift.encode() + b" " + f"{groesse:g}".encode() + b" Tf 60 " + f"{y:.1f}".encode()
                     + b" Td (" + _pdf_text(text) + b") Tj ET")
        y -= groesse * 1.35
    inhalt = DecodedStreamObject()
    inhalt.set_data(b"\n".join(teile))
    seite[NameObject("/Contents")] = writer._add_object(inhalt)


def _absatz(text: str, breite: int = 92, schrift: str = "F1", groesse: float = 10) -> list[tuple[str, str, float]]:
    # Betrag und „€“ bleiben zusammen (geschütztes Leerzeichen — textwrap trennt nur an ASCII-Leerraum, WinAnsi kennt es).
    text = (text or "").replace(" €", "\u00a0€")
    return [(schrift, z, groesse) for z in textwrap.wrap(text, breite)] or [(schrift, "", groesse)]


def anschreiben_zeilen(entwurf: dict, faelle: list[dict], angaben: dict, ticket: dict | None) -> list[tuple[str, str, float]]:
    """Das Anschreiben zum Sammelantrag einer Zeitkarte: wer, welche Karte, welche Fälle, die Rechnung nach Teil C Nr. 8.1."""
    r = entwurf["rechnung"]
    name = f"{angaben.get('vorname', '')} {angaben.get('nachname', '')}".strip()
    dticket = entwurf["art"] == "dticket"
    von, bis = entwurf.get("von") or "", entwurf.get("bis") or ""
    zeitraum = f"{date.fromisoformat(von):%d.%m.%Y} bis {date.fromisoformat(bis):%d.%m.%Y}" if von and bis else ""
    z: list[tuple[str, str, float]] = [
        ("F1", name, 10), ("F1", angaben.get("strasse") or "", 10), ("F1", f"{angaben.get('plz', '')} {angaben.get('ort', '')}", 10)]
    if angaben.get("email"):
        z.append(("F1", angaben["email"], 10))
    z += [("F1", "", 10), ("F1", "", 10)]
    if entwurf["stelle"] == "servicecenter":
        z += [("F1", "DB Fernverkehr AG", 10), ("F1", "Servicecenter Fahrgastrechte", 10), ("F1", "60647 Frankfurt am Main", 10)]
    else:
        z += [("F1", f"{entwurf['stelle_name']} — Beleg zum Online-Formular", 10)]
    z += [("F1", "", 10), ("F1", f"{angaben.get('ort', '')}, den ____________", 10), ("F1", "", 10),
          ("F2", f"Sammelantrag auf Entschädigung — {'Deutschlandticket' if dticket else 'Zeitkarte'}, "
                 f"Geltungsdauer {zeitraum}", 11), ("F1", "", 8), ("F1", "Sehr geehrte Damen und Herren,", 10), ("F1", "", 6)]
    karte = (ticket or {}).get("bezeichnung") or entwurf.get("ticket_name") or "Zeitkarte"
    nummer = _nummer_der_karte(ticket or {}, angaben)
    preis = fahrgastrechte.euro(r.get("preis_cent")) if r.get("preis_cent") else "—"
    grundlage = ("Teil C Nr. 8.1 und 8.2 der Tarifbedingungen des Deutschlandtarifs (anwendbar nach Nr. 1.6 der "
                 "Tarifbedingungen für das Deutschland-Ticket)" if dticket else
                 "den Beförderungsbedingungen für Zeitkarten (1,50 € je Verspätung ab 60 Minuten, höchstens 25 % des "
                 "gezahlten Preises)")
    z += _absatz(f"als Inhaber{'in' if angaben.get('anrede') == 'Frau' else ''} der Zeitkarte „{karte}“"
                 + (f" (Nummer {nummer})" if nummer else "") + f", gezahlter Preis {preis}, beantrage ich nach {grundlage} "
                 "eine Entschädigung für folgende Verspätungen am Zielbahnhof während der Geltungsdauer:")
    z += [("F1", "", 6), ("F3", f"{'Nr':<2} {'Datum':<10} {'Zug':<9} {'Von -> Nach':<34} {'Plan':<5} {'Ist':<5} {'Min':>4}", 9)]
    for i, f in enumerate(faelle, 1):
        ab, an, echt = antrag._zeit(f["ab"]), antrag._zeit(f["an"]), antrag._zeit(f.get("an_echt"))
        strecke = f"{bahnhof(f['start'])} -> {bahnhof(f['ziel'])}"
        strecke = strecke if len(strecke) <= 34 else strecke[:33] + "."
        z.append(("F3", f"{i:<2} {ab:%d.%m.%Y} {(f.get('linie') or '')[:9]:<9} {strecke:<34} {an:%H:%M} "
                        f"{echt:%H:%M} {f.get('verspaetung') or 0:>4}", 9))
    z.append(("F1", "", 6))
    if dticket:
        z += _absatz(f"Summe: {r['minuten_benoetigt']} Minuten = {r['einheiten_benoetigt']} volle Stunden × "
                     f"{fahrgastrechte.euro(r['satz_cent'])} = {fahrgastrechte.euro(r['roh_cent'])}"
                     + (f", begrenzt auf 25 % des gezahlten Preises: {fahrgastrechte.euro(r['deckel_cent'])}" if r["gedeckelt"] else "")
                     + ". Fälle unter 60 Minuten sind nach Teil C Nr. 8.1 zusammengerechnet; im Formular steht bei ihnen "
                     "„Sonstiges“, weil das Kästchen „mind. 60 Minuten“ nicht zutrifft.")
    else:
        z += _absatz(f"{r['einheiten_benoetigt']} Verspätungen ab 60 Minuten × {fahrgastrechte.euro(r['satz_cent'])} = "
                     f"{fahrgastrechte.euro(r['roh_cent'])}"
                     + (f", begrenzt auf 25 % des gezahlten Preises: {fahrgastrechte.euro(r['deckel_cent'])}" if r["gedeckelt"] else "")
                     + ".")
    z += [("F1", "", 4), ("F2", f"Beantragt: {fahrgastrechte.euro(r['betrag_cent'])}", 11), ("F1", "", 6)]
    z += _absatz("Bitte überweisen Sie den Betrag auf das Konto, das im beiliegenden Formular angegeben ist."
                 if entwurf["stelle"] == "servicecenter" else
                 "Bitte überweisen Sie den Betrag auf das Konto, das ich im Online-Formular angegeben habe.")
    anlagen = ("je Fall ein Fahrgastrechte-Formular, " if entwurf["stelle"] == "servicecenter" else "") + \
        "Kopie der Zeitkarte, Nachweis des gezahlten Preises"
    z += [("F1", "", 6), ("F1", f"Anlagen: {anlagen}", 10), ("F1", "", 10), ("F1", "Mit freundlichen Grüßen", 10),
          ("F1", "", 22), ("F1", "______________________________", 10), ("F1", f"{name} (Unterschrift)", 9)]
    return z


def _hinweisseite(entwurf: dict, notizen: list[str], person: str) -> list[tuple[str, str, float]]:
    """Ein Deckblatt NUR für die Person, die einreicht — nicht mitschicken."""
    z = [("F2", "Nur für dich — nicht mitschicken", 12), ("F1", "", 6),
         ("F1", f"{entwurf['titel']} · {entwurf['stelle_name']} · {fahrgastrechte.euro(entwurf['betrag_cent'])}", 10),
         ("F1", "", 6)]
    wer = "musst du" if person == "du" else f"muss {person}"
    schritte = [f"Unterschreiben {wer} selbst (als Ticketinhaber): jedes Formular unten auf Seite 2 und das Anschreiben.",
                "IBAN und BIC im Formular von Hand eintragen — Friday druckt die IBAN nie aus.",
                "Datum von Hand eintragen."]
    if entwurf["stelle"] == "servicecenter":
        schritte.append(f"Alles zusammen mit der Ticketkopie per Post an: {fahrgastrechte.STELLEN['servicecenter']['adresse']} "
                        "— oder im DB Reisezentrum abgeben.")
    else:
        schritte.append(f"Online-Formular: {fahrgastrechte.STELLEN.get(entwurf['stelle'], {}).get('url', '')} — das "
                        "Anschreiben als Beleg hochladen.")
    if entwurf["art"] == "dticket":
        schritte.append("Einen Nachweis des gezahlten Preises beilegen (Abo-Rechnung oder Kontoauszug).")
    schritte.append("Danach in Friday „eingereicht“ setzen — dann zählt der Antrag, und die Fälle gehen nicht doppelt raus.")
    for i, s in enumerate(schritte + notizen, 1):
        z += _absatz(f"{i}. {s}", 90)
    return z


def pdf(schluessel: str) -> tuple[bytes, str]:
    """Das fertige PDF eines Antrags (Deckblatt nur für dich, Anschreiben beim Sammelantrag, je Fall ein Formular) und ein
    Dateiname. Nur für Anträge, die bereit oder schon eingereicht sind — ein unfertiger Antrag wird nicht gedruckt."""
    from PIL import Image
    from pypdf import PdfReader, PdfWriter
    e, faelle, angaben, ticket = _bauteile(schluessel)
    w = PdfWriter()
    notizen = []
    formulare = []
    if e["stelle"] == "servicecenter":
        for i, f in enumerate(faelle, 1):
            texte, knoepfe, n = felder(f, e, angaben, ticket, i, len(faelle))
            notizen += [f"Formular {i}: {x}" for x in n]
            formulare += _bilder(_formular_bytes(texte, knoepfe))
    _textseite(w, _hinweisseite(e, notizen, e["person"] if e["profil"] else "du"))
    if e["art"] in ("dticket", "zeitkarte"):
        _textseite(w, anschreiben_zeilen(e, faelle, angaben, ticket))
    if formulare:
        puffer = io.BytesIO()
        formulare[0].save(puffer, format="PDF", save_all=True, append_images=formulare[1:], resolution=72 * MASSSTAB, quality=60)
        for seite in PdfReader(io.BytesIO(puffer.getvalue())).pages:
            w.add_page(seite)
    aus = io.BytesIO()
    w.write(aus)
    name = f"fahrgastrechte-{re.sub(r'[^a-z0-9]+', '-', e['titel'].lower()).strip('-')}.pdf"
    return aus.getvalue(), name


def anschreiben_pdf(schluessel: str) -> tuple[bytes, str]:
    """Nur das Anschreiben (für das Online-Formular der S-Bahn als Beleg)."""
    from pypdf import PdfWriter
    e, faelle, angaben, ticket = _bauteile(schluessel)
    w = PdfWriter()
    _textseite(w, anschreiben_zeilen(e, faelle, angaben, ticket))
    aus = io.BytesIO()
    w.write(aus)
    return aus.getvalue(), f"anschreiben-{re.sub(r'[^a-z0-9]+', '-', e['titel'].lower()).strip('-')}.pdf"


def _bauteile(schluessel: str) -> tuple[dict, list[dict], dict, dict | None]:
    """Entwurf (oder schon eingereichter Antrag), seine benötigten Fälle, die Angaben der Person, ihr Ticket."""
    # Schon eingereicht: genau das, was damals hinausging (die Fälle stehen am Antrag) — nie die übrig gebliebenen.
    if eingereicht := bahnablage.antrag_zum_schluessel(schluessel):
        e = _aus_eingereicht(eingereicht)
    else:
        e = fahrgastrechte.entwurf(schluessel)
        if e is None:
            raise LookupError("Diesen Antrag gibt es nicht (mehr).")
        if e["stand"] != "bereit":
            raise ValueError(f"Gedruckt wird erst, wenn der Antrag bereit ist (Stand: {e['stand']}).")
    a = ablage.standard()
    faelle = [fahrgastrechte.bewerten(bahnablage.fall(fid)) for fid in e["benoetigt"] if bahnablage.fall(fid)]
    faelle.sort(key=lambda f: f["ab"])
    ticket = next((t for t in a.tickets(e["profil"]) if t.get("id") == e.get("ticket")), None) if e.get("ticket") else None
    return e, faelle, a.angaben(e["profil"]), ticket


def _aus_eingereicht(x: dict) -> dict:
    """Ein schon eingereichter Antrag, so aufbereitet wie ein Entwurf — zum erneuten Ausdrucken."""
    a = ablage.standard()
    stelle = x.get("stelle") or "unklar"
    titel = (f"{'Deutschlandticket' if x.get('art') == 'dticket' else 'Zeitkarte'} "
             f"{date.fromisoformat(x['von']):%d.%m.}–{date.fromisoformat(x['bis']):%d.%m.%Y}"
             if x.get("art") in ("dticket", "zeitkarte") and x.get("von") and x.get("bis") else f"Antrag B{x['id']}")
    return {"schluessel": x["schluessel"], "profil": int(x.get("profil") or 0), "person": a.profil_name(int(x.get("profil") or 0)),
            "stelle": stelle, "stelle_name": fahrgastrechte.STELLEN.get(stelle, fahrgastrechte.STELLEN["unklar"])["name"],
            "art": x.get("art"), "titel": titel, "von": x.get("von"), "bis": x.get("bis"), "ticket": x.get("ticket"),
            "rechnung": x.get("rechnung") or {}, "benoetigt": x.get("faelle") or [], "betrag_cent": x.get("erwartet_cent") or 0,
            "stand": x.get("status")}


def abtippen(schluessel: str) -> dict:
    """Alles für das Online-Formular der S-Bahn Hannover, in seiner Reihenfolge (Felder gemessen am 21.09.2026). Die IBAN
    nur mit den Enden. Je Fall eine Gruppe — gesendet wird ein Formular je Fahrt, das Anschreiben liegt jedem bei."""
    e, faelle, angaben, ticket = _bauteile(schluessel)
    iban = angaben.get("iban") or ""
    person = [["Anrede", {"Herr": "Herr", "Frau": "Frau"}.get(angaben.get("anrede") or "", "keine Angabe")],
              ["Vorname", angaben.get("vorname") or ""], ["Nachname", angaben.get("nachname") or ""],
              ["Straße, Hausnummer", angaben.get("strasse") or ""], ["Postleitzahl", angaben.get("plz") or ""],
              ["Ort", angaben.get("ort") or ""], ["E-Mail", angaben.get("email") or ""]]
    zeitkarte = e["art"] in ("dticket", "zeitkarte")
    preis = (e.get("rechnung") or {}).get("preis_cent")
    gruppen = [{"titel": "Person", "zeilen": person}]
    for i, f in enumerate(faelle, 1):
        ab, an, echt = antrag._zeit(f["ab"]), antrag._zeit(f["an"]), antrag._zeit(f.get("an_echt"))
        zug = "S-Bahn" if (f.get("kategorie") or "").lower() == "s-bahn" else \
            next((x for x in ("ICE", "IC", "RE", "RB") if (f.get("linie") or "").upper().startswith(x)), "Sonstige")
        zeilen = [["Linie/Strecke", f"{f.get('linie')} {bahnhof(f['start'])} – {bahnhof(f['ziel'])}"],
                  ["Fahrkartenart", "Zeitkarte Nahverkehr" if zeitkarte else "Einzelfahrt"],
                  ["Fahrpreis", fahrgastrechte.euro(preis if zeitkarte else f.get("preis_cent")).replace(" €", "")]]
        if zeitkarte and e.get("von") and e.get("bis"):
            zeilen += [["Gültigkeit der Fahrkarte von", f"{date.fromisoformat(e['von']):%d.%m.%Y}"],
                       ["Gültigkeit der Fahrkarte bis", f"{date.fromisoformat(e['bis']):%d.%m.%Y}"]]
        zeilen += [["Klasse", "2"], ["Bahncard", "keine"], ["Anzahl Erwachsene (ab 15 Jahre)", "1"],
                   ["Reisetag", f"{ab:%d.%m.%Y}"], ["Startbahnhof", bahnhof(f["start"])], ["Zielbahnhof", bahnhof(f["ziel"])],
                   ["Abfahrt lt. Fahrplan", f"{ab:%H:%M}"], ["Ankunft lt. Fahrplan", f"{an:%H:%M}"],
                   ["Ankunftsdatum", f"{echt:%d.%m.%Y}" if echt else ""], ["Tatsächliche Ankunft", f"{echt:%H:%M}" if echt else ""],
                   ["Zugnummer tatsächlicher Zug", f.get("linie") or ""], ["Erster verspäteter/ausgefallener Zug", zug],
                   ["Zugnummer verspäteter Zug", f.get("linie") or ""], ["Abfahrt lt. Fahrplan (verspäteter Zug)", f"{ab:%H:%M}"],
                   ["Verspätung am Ziel (zur Kontrolle, kein Feld)", f"{f.get('verspaetung')} Min"]]
        gruppen.append({"titel": f"Fahrt {i} von {len(faelle)}", "zeilen": zeilen})
    gruppen.append({"titel": "Erstattung", "zeilen": [
        ["Erstattungsart", "Überweisung"], ["Kontoinhaber", angaben.get("kontoinhaber") or ""],
        ["IBAN (nur die Enden — die volle kennst du)", f"{iban[:2]}•• … {iban[-4:]}" if iban else "— fehlt —"],
        ["Beleg zur Erstattung", "Ticket (Screenshot)" + (", Anschreiben (PDF)" if zeitkarte else "")
         + (", Preisnachweis" if e["art"] == "dticket" else "")]]})
    hinweise = [f"Zusammen {fahrgastrechte.euro(e['betrag_cent'])} ({e['titel']})."]
    if len(faelle) > 1:
        hinweise.append("Das Formular fragt eine Fahrt ab: Friday schlägt je Fahrt ein Formular vor, jeweils mit dem Anschreiben "
                        "als Beleg (es listet alle Fahrten und die Rechnung).")
    return {"person": e["person"], "stelle": e["stelle_name"], "url": fahrgastrechte.STELLEN.get(e["stelle"], {}).get("url", ""),
            "hinweise": hinweise, "gruppen": gruppen}

