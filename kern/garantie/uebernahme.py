"""Ein gelesenes Ticket in die Angaben der Person übernehmen — für den Upload in der App (`main.py`) und für die
Annahme per Snapchat (`annahme.py`). Verschoben aus `main._ticket_uebernehmen` (09.10.2026), Verhalten unverändert."""

from __future__ import annotations

from datetime import datetime

from kern.garantie import ticket as garantieticket


def uebernehmen(a, t: dict) -> str:
    """Art und Gültigkeit in die Angaben, den Namen nur, wo noch keiner steht."""
    profil = int(t.get("profil") or 0)
    angaben = a.angaben(profil)
    # Eine Kundenkarte allein ist kein Ticket: Sie sagt nichts über die Fahrkarte.
    neu = {"fahrkarte": t["art"]} if t.get("art") and not t.get("ist_kundenkarte") else {}
    if t.get("gueltig_bis"):
        neu["gueltig_bis"] = t["gueltig_bis"]
    if t.get("inhaber") and not (angaben.get("vorname") or angaben.get("nachname")):
        teile = t["inhaber"].split()
        neu.update({"vorname": " ".join(teile[:-1]), "nachname": teile[-1]} if len(teile) > 1 else {"nachname": teile[0]})
    # Die Abo-Nummer ändert sich nicht (Eike, 16.09.): einmal übernehmen, danach nur warnen.
    abo = t.get("abo_nummer") or ""
    if abo and not angaben.get("abo_nummer"):
        neu["abo_nummer"] = abo
    # Die Kundenkartennummer gehört zur Person, nicht zum Monatsticket (Jugend-/Ausbildungsnetzkarte).
    if t.get("kundenkarte") and not angaben.get("kundenkarte"):
        neu["kundenkarte"] = t["kundenkarte"]
    a.angaben_setzen(neu, profil)
    von = datetime.fromisoformat(t["gueltig_von"]).strftime("%d.%m.") if t.get("gueltig_von") else "?"
    bis = datetime.fromisoformat(t["gueltig_bis"]).strftime("%d.%m.%Y") if t.get("gueltig_bis") else "?"
    text = (f"Erkannt: Kundenkarte{' ' + t['kundenkarte'] if t.get('kundenkarte') else ''}." if t.get("ist_kundenkarte")
            else f"Erkannt: {t.get('bezeichnung') or t.get('art') or 'Fahrkarte'}"
                 + (" (Papier)" if t.get("form") == "papier" else "") + f", gültig {von}–{bis}.")
    if abo and angaben.get("abo_nummer") and abo != angaben["abo_nummer"]:
        text += (f" Auf dem Ticket steht die Abo-Nummer {abo}, gespeichert ist {angaben['abo_nummer']} — "
                 "Friday hat die gespeicherte behalten. Bitte prüfen.")
    elif abo:
        text += f" Abo-Nummer {abo}."
    if t.get("kundenkarte") and angaben.get("kundenkarte") and t["kundenkarte"] != angaben["kundenkarte"]:
        text += (f" Auf dem Ticket steht die Kundenkartennummer {t['kundenkarte']}, gespeichert ist {angaben['kundenkarte']} — "
                 "Friday hat die gespeicherte behalten und schickt erst ab, wenn beide gleich sind. Bitte prüfen.")
    if t.get("ist_kundenkarte"):
        text += " Ihre Nummer gehört zur Person; als Monatsticket zählt sie nicht."
    elif garantieticket.sorte(t) and t.get("form") == "papier":
        text += (" Papierticket: Vergleiche Kundenkarten- und Ticketnummer einmal mit dem Original und setz das Häkchen "
                 "„Nummern verglichen“ — vorher schickt Friday nicht ab.")
    if not (t.get("gueltig_von") and t.get("gueltig_bis")) and not t.get("ist_kundenkarte"):
        text += " Die Gültigkeit war nicht ganz lesbar — bitte ergänzen."
    if t.get("art") == "Deutschlandticket" and garantieticket.ist_uestra(t.get("herausgeber", "")) is False:
        text += (f" Achtung: ausgegeben von „{t['herausgeber']}“ — die Garantie gilt nur für von der ÜSTRA "
                 "ausgegebene Deutschlandtickets.")
    return text
