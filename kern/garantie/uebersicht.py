"""Die Garantie über alle Personen — wer noch kann, wer voll ist, was zu tun ist (21.09.2026).

Eike reicht für immer mehr Freunde ein. Die Seite beginnt deshalb nicht mit EINER Person, sondern mit allen:
je Person eine Zeile mit Ticket, Höchstzahlen und dem, was sie gerade aufhält (`person`); darüber, was über alle
Personen hinweg zu tun ist — Anträge, die bereit sind, Rückfragen der ÜSTRA (`aufgaben`). Beim Vorbereiten einer
Fahrt sagt `fuer_fahrt`, für wen sie überhaupt geht, damit Eike nicht erst eine Person öffnen muss.

Hier wird nur gelesen und gezählt. Entschieden wird weiter in `antrag.py` (Grenzen, Stand) und `abschicken.py`
(Prüfung vor dem Senden). Keine IBAN, keine Adresse — nur Namen, Zahlen und Gründe.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from kern import config
from kern.garantie import ablage, antrag, post, profile
from kern.garantie import ticket as ticketlesen

# Was an einer Person hängen kann, grob nach Dringlichkeit — die App sortiert danach. „wartet“: Die restlichen Plätze
# im Monat sind von Anträgen belegt, die noch auf den Bescheid warten — mehr einzureichen lohnt erst, wenn einer abgelehnt wird.
AMPEL = ("frei", "heute_voll", "fehlt", "wartet", "voll", "archiviert")


def _tag(iso: str | None) -> str:
    try:
        return date.fromisoformat(iso or "").strftime("%d.%m.")
    except ValueError:
        return "?"


def ticket_kurz(t: dict | None) -> dict | None:
    """Das Ticket für die Übersicht: was es ist, wie lange es gilt, ob es auf Papier ist."""
    if not t:
        return None
    s = ticketlesen.sorte(t)
    return {"id": t.get("id"), "bezeichnung": t.get("bezeichnung") or t.get("art") or "Ticket", "art": t.get("art") or "",
            "gueltig_von": t.get("gueltig_von") or "", "gueltig_bis": t.get("gueltig_bis") or "",
            "form": t.get("form") or "", "sorte": s["name"] if s else "",
            "vergleichen": ticketlesen.muss_verglichen_werden(t)}


def _zaehler_kurz(z: dict) -> dict:
    """Die Zahlen, die die Seite für die Balken braucht (Ticket und Monat)."""
    return {k: z[k] for k in ("heute", "tag_max", "monat", "monat_max", "grenze", "gezaehlt", "mit_dticket",
                              "monat_name", "monat_rest", "heute_rest", "preis_cent", "preis_rest_cent",
                              "ticket_erstattet_cent", "ticket_offen_cent", "monat_erstattet_cent",
                              "offen_heute", "offen_monat", "offen_ticket", "frei_monat", "frei_heute")}


def person(pid: int) -> dict:
    """Eine Person auf einen Blick. `ampel`: frei · heute_voll · fehlt (etwas hält sie auf) · wartet · voll · archiviert."""
    a = ablage.standard()
    heute = config.jetzt().date()
    p = a.profil(pid) if pid else None
    if pid and not p:
        raise LookupError("Dieses Profil gibt es nicht.")
    name = a.profil_name(pid)
    z = antrag.zaehler(pid)
    staende = Counter(antrag.bewerten(e)["stand"] for e in a.antraege(1000, pid))
    t_heute = a.ticket_fuer(heute, pid)
    t_neu = a.ticket(pid)
    angaben = a.angaben(pid)

    # Was die Person gerade aufhält — in der Reihenfolge, in der Eike es beheben würde.
    gruende = []
    if not t_heute:
        monat = antrag.MONATE[heute.month - 1]
        if t_neu and t_neu.get("gueltig_bis") and t_neu["gueltig_bis"] < heute.isoformat():
            gruende.append(f"Ticket für {monat} fehlt (letztes bis {_tag(t_neu['gueltig_bis'])})")
        else:
            gruende.append(f"Ticket für {monat} fehlt")
    fehlt = a.fehlende_angaben(pid)
    if fehlt:
        gruende.append("Angaben fehlen: " + ", ".join(fehlt))
    if p and not p.get("vollmacht"):
        gruende.append("Zustimmung fehlt")
    if t_heute:
        gruende += [f"{x} fehlt" for x in ticketlesen.fehlende_felder(t_heute, angaben)]
        if grund := ticketlesen.muss_verglichen_werden(t_heute):
            gruende.append(f"{grund}: Nummern vergleichen")
        if t_heute.get("zweifel") and not t_heute.get("korrigiert"):
            gruende.append("Ticket prüfen (Friday war unsicher)")

    preis_voll = z["preis_rest_cent"] is not None and z["preis_rest_cent"] <= 0
    if p and p.get("archiviert"):
        ampel = "archiviert"
    elif z["monat_rest"] == 0 or preis_voll:
        ampel = "voll"
    elif gruende:
        ampel = "fehlt"
    elif z["frei_monat"] == 0:
        ampel = "wartet"
    elif z["heute_rest"] == 0 or z["frei_heute"] == 0:
        ampel = "heute_voll"
    else:
        ampel = "frei"

    if pid:
        r = profile.abrechnung(pid)
        geld = {"summe_cent": r["summe_cent"], "mein_cent": r["mein_cent"], "offen_cent": r["offen_cent"],
                "ich_zahle_aus": r["ich_zahle_aus"], "genehmigt": r["anzahl"]}
    else:
        st = post.statistik(0)
        geld = {"summe_cent": st["insgesamt_cent"], "monat_cent": st["monat_cent"], "genehmigt": st["genehmigt"]}
    return {"id": pid, "name": name, "ich": not pid, "archiviert": bool(p and p.get("archiviert")),
            "vollmacht": bool(p.get("vollmacht")) if p else True, "ampel": ampel, "gruende": gruende,
            "ticket": ticket_kurz(t_heute or t_neu), "ticket_gilt": bool(t_heute),
            "zaehler": _zaehler_kurz(z),
            "antraege": {"bereit": staende["bereit"], "wartet": staende["wartet"] + staende["wird nachgeprüft"],
                         "unvollstaendig": staende["unvollständig"], "abgeschickt": staende["abgeschickt"]
                         + staende["eingegangen"] + staende["Rückfrage"], "genehmigt": staende["genehmigt"]},
            "geld": geld}


def alle() -> list[dict]:
    """Eike zuerst, dann jeder Freund. Archivierte nur mit Namen — ihre Zahlen braucht die Übersicht nicht."""
    a = ablage.standard()
    liste = [person(0)]
    for p in a.profile():
        if p.get("archiviert"):
            liste.append({"id": p["id"], "name": p.get("name") or f"Profil {p['id']}", "archiviert": True,
                          "ampel": "archiviert"})
        else:
            liste.append(person(p["id"]))
    return liste


def aufgaben() -> dict:
    """Was über alle Personen hinweg ansteht: Anträge, die abgeschickt werden können (die mit der knappsten Frist
    zuerst), und Rückfragen der ÜSTRA."""
    a = ablage.standard()
    heute = config.jetzt().date()
    bereit = []
    for e in a.antraege(500, None):
        if e["status"] != "offen":
            continue
        b = antrag.bewerten(e)
        if b["stand"] != "bereit":
            continue
        an = antrag._zeit(b.get("an"))
        frist = (an.date() + timedelta(days=antrag.FRIST_TAGE)) if an else None
        bereit.append({**b, "person": a.profil_name(b.get("profil") or 0),
                       "frist_bis": frist.isoformat() if frist else "",
                       "frist_tage": (frist - heute).days if frist else None})
    bereit.sort(key=lambda x: (x["frist_bis"] or "9999", x["id"]))
    profil_je_antrag = {e["id"]: int(e.get("profil") or 0) for e in a.antraege(10000, None)}
    rueckfragen = []
    for v in a.vorgaenge(1000, None):
        if post.anzeige_stand(v) != "rueckfrage":
            continue
        pid = profil_je_antrag.get(v.get("antrag"), 0)
        rueckfragen.append({"vorgang": v["vorgang"], "profil": pid, "person": a.profil_name(pid),
                            "fahrtdatum": v.get("fahrtdatum") or "", "linie": v.get("linie") or "",
                            "start": v.get("start") or "", "ziel": v.get("ziel") or "", "grund": v.get("grund") or ""})
    return {"bereit": bereit, "rueckfragen": rueckfragen}


def fuer_fahrt(rid: str) -> list[dict]:
    """Für wen lässt sich diese Fahrt vorbereiten? Je Person: geht es (`gruende` leer), was fehlt noch (`warnungen`,
    vorbereiten geht trotzdem), gibt es schon einen Antrag. Gezählt wird für den Tag der Fahrt."""
    a = ablage.standard()
    r = a.reise(rid)
    if not r:
        raise LookupError("Diese Fahrt kenne ich nicht (mehr).")
    zeit = antrag._zeit(r.get("plan"))
    if not zeit:
        raise LookupError("Für diese Fahrt fehlt die Zeit.")
    ergebnis = []
    for pid in [0] + [p["id"] for p in a.profile() if not p.get("archiviert")]:
        p = a.profil(pid) if pid else None
        gruende = antrag._grenzen(zeit, profil=pid)
        vorhanden = a.antrag_zur_reise(rid, pid)
        if vorhanden:
            gruende.insert(0, f"Antrag #{vorhanden['id']} gibt es schon")
        warnungen = []
        if not a.ticket_fuer(zeit.date(), pid):
            warnungen.append(f"kein Ticket für den {zeit:%d.%m.}")
        if a.fehlende_angaben(pid):
            warnungen.append("Angaben unvollständig")
        if p and not p.get("vollmacht"):
            warnungen.append("Zustimmung fehlt")
        offen_am_tag = sum(t == zeit.date() for t in antrag.offene_fahrten(pid))
        if offen_am_tag:
            warnungen.append(f"{offen_am_tag} am {zeit:%d.%m.} warten noch auf den Bescheid")
        ergebnis.append({"id": pid, "name": a.profil_name(pid), "geht": not gruende, "gruende": gruende,
                         "warnungen": warnungen, "antrag": vorhanden["id"] if vorhanden else None,
                         "zaehler": _zaehler_kurz(antrag.zaehler(pid))})
    # Eike zuerst, dann wer geht, dann der Rest — jeweils nach Namen.
    ergebnis.sort(key=lambda x: (x["id"] != 0, not x["geht"], x["name"].casefold()))
    return ergebnis
