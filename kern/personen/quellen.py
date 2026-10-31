"""Quellen-Verknüpfung: Eine Person, überall dieselbe (04.10.2026, Eikes Auftrag).

Kontakt A aus WhatsApp, aus der Garantie, aus Snapchat und aus dem Gedächtnis ist ein Kontakt A.
Das Register merkt sich je Person die Verweise (`quellen`: WhatsApp-Chat-id, Garantie-Profil-id;
der Graph hängt schon an `graph_name`); `kandidaten()` schlägt gleiche Namen vor, Eike
verknüpft einmal — danach löst jeder Name überall auf. Gruppenchats (`@g.us`) und Eike
selbst werden nie vorgeschlagen; ohne Namensgleichheit keine Verknüpfung (fail-closed)."""

from __future__ import annotations

import logging

from kern.dienste import whatsapp
from kern.garantie import ablage as garantieablage
from kern.personen import PersonenFehler, register

log = logging.getLogger("friday.personen")

QUELLEN = ("whatsapp", "garantie")


def _schluessel_set(person: dict) -> set[str]:
    """Alle Namensformen einer Person: Name, Graph-Name, Snapchat-Name. Kontakt A ist Kontakt A."""
    namen = {person.get("name", ""), person.get("graph_name", ""),
             (person.get("kanaele") or {}).get("snapchat", "")}
    return {register.schluessel(n) for n in namen if register.schluessel(n)}


def _woerter(schluessel: str) -> set[str]:
    """Wörter ab 3 Zeichen — „Al“ allein beweist nichts, „Kontakt A“ schon."""
    return {w for w in schluessel.split() if len(w) >= 3}


def _punktzahl(ziele: set[str], name: str) -> tuple[int, list[str]]:
    """Punkte + Gründe: exakt gleich (3), gemeinsame Wörter (2 je Wort)."""
    k = register.schluessel(name)
    if not k:
        return 0, []
    if k in ziele:
        return 3, ["Name gleich"]
    worte = _woerter(k)
    gemeinsam = {w for z in ziele for w in (worte & _woerter(z))}
    if not gemeinsam:
        return 0, []
    return 2 * len(gemeinsam), [f"passt zu „{w}“" for w in sorted(gemeinsam)]


def _passt(ziele: set[str], name: str) -> bool:
    """Gehört dieser Quellen-Name zu dieser Person? Exakt gleich — oder ein Wort
    steht auf beiden Seiten („Kontakt A“ in „Kontakt A“). Nie geraten: Vorschlag und
    Bestätigung bleiben, Mehrdeutiges zeigt die Seite nebeneinander."""
    return _punktzahl(ziele, name)[0] > 0


def _whatsapp_chats() -> list[dict]:
    """Freigegebene 1:1-Chats (keine Gruppen) — nur dort schreibt Friday mit Einzelnen."""
    try:
        chats = whatsapp.frei()
    except Exception:  # noqa: BLE001 — keine Chats = keine Vorschläge, nie ein Absturz
        return []
    return [c for c in chats if isinstance(c, dict) and str(c.get("id", "")).endswith("@c.us")]


def _garantie_profile() -> list[dict]:
    """Aktive Freunde-Profile (kein Archiv) — ohne IBAN & Co., nur id und Name."""
    try:
        profile = garantieablage.standard().profile()
    except Exception:  # noqa: BLE001 — siehe oben
        return []
    return [{"id": p.get("id"), "name": p.get("name", "")} for p in profile
            if isinstance(p, dict) and not p.get("archiviert")]


def kandidaten() -> list[dict]:
    """Verknüpfungs-Vorschläge: gleiche Namen, noch nicht verknüpft — nach Punkten sortiert.
    Eike bestätigt je Vorschlag."""
    vorschlaege = []
    for person in register.liste():
        vergeben = person.get("quellen") or {}
        ziele = _schluessel_set(person)
        if not ziele:
            continue
        for c in _whatsapp_chats():
            if vergeben.get("whatsapp"):
                continue
            punkte, warum = _punktzahl(ziele, c.get("name", ""))
            if punkte:
                vorschlaege.append({"pid": person["id"], "name": person["name"], "quelle": "whatsapp",
                                    "ref": c["id"], "anzeige": c.get("name", ""),
                                    "punkte": punkte, "warum": warum})
        for p in _garantie_profile():
            if vergeben.get("garantie") is not None:
                continue
            punkte, warum = _punktzahl(ziele, p.get("name", ""))
            if punkte:
                vorschlaege.append({"pid": person["id"], "name": person["name"], "quelle": "garantie",
                                    "ref": p["id"], "anzeige": p.get("name", ""),
                                    "punkte": punkte, "warum": warum})
    vorschlaege.sort(key=lambda v: (-v["punkte"], v["name"], v["anzeige"]))
    return vorschlaege


def stand() -> dict:
    """Verknüpfte Quellen je Person, mit Anzeigenamen zum Darstellen."""
    chats = {str(c.get("id")): str(c.get("name", "")) for c in _whatsapp_chats()}
    profile = {}
    for p in _garantie_profile():
        try:
            profile[int(p["id"])] = str(p.get("name", ""))
        except (TypeError, ValueError):
            continue
    aus = {}
    for person in register.liste():
        vergeben = person.get("quellen") or {}
        zeilen = {}
        if vergeben.get("whatsapp") in chats:
            zeilen["whatsapp"] = chats[vergeben["whatsapp"]]
        if isinstance(vergeben.get("garantie"), int) and vergeben["garantie"] in profile:
            zeilen["garantie"] = profile[vergeben["garantie"]]
        if zeilen:
            aus[person["id"]] = zeilen
    return aus


def verknuepfen(pid: str, quelle: str, ref) -> dict:
    """Eikes Ja: Diese Quelle gehört zu dieser Person. Der Name muss JETZT noch passen —
    sonst nichts (umbenannter Chat, fremdes Profil)."""
    if quelle not in QUELLEN:
        raise PersonenFehler("Unbekannte Quelle.")
    person = register.holen(pid)
    if not person:
        raise PersonenFehler("Diese Person gibt es nicht.")
    ziele = _schluessel_set(person)
    if quelle == "whatsapp":
        chat = next((c for c in _whatsapp_chats() if str(c.get("id")) == str(ref)), None)
        if chat is None or not _passt(ziele, chat.get("name", "")):
            raise PersonenFehler("Der Chat passt nicht zu dieser Person — nichts verknüpft.")
        ref = str(chat["id"])
    else:
        try:
            nummer = int(ref)
        except (TypeError, ValueError):
            raise PersonenFehler("Das Garantie-Profil gibt es nicht.") from None
        profil = next((p for p in _garantie_profile() if p.get("id") == nummer), None)
        if profil is None or not _passt(ziele, profil.get("name", "")):
            raise PersonenFehler("Das Profil passt nicht zu dieser Person — nichts verknüpft.")
        ref = nummer
    vergeben = dict(person.get("quellen") or {})
    vergeben[quelle] = ref
    return register.aendern(pid, quellen=vergeben)


def entknuepfen(pid: str, quelle: str) -> dict:
    """Verknüpfung lösen — die Person und die Quelle bleiben, nur die Klammer geht weg."""
    if quelle not in QUELLEN:
        raise PersonenFehler("Unbekannte Quelle.")
    person = register.holen(pid)
    if not person:
        raise PersonenFehler("Diese Person gibt es nicht.")
    vergeben = dict(person.get("quellen") or {})
    vergeben.pop(quelle, None)
    return register.aendern(pid, quellen=vergeben)


def _profil_adresse(profil: dict) -> str:
    """Nur Name + Adresse aus der Kundenkarte — nie IBAN, Mail oder Ticket."""
    teile = [str(profil.get("name", "")).strip()]
    ort = " ".join(x for x in (str(profil.get("strasse", "")).strip(),
                               str(profil.get("plz", "")).strip(),
                               str(profil.get("ort", "")).strip()) if x)
    if ort:
        teile.append(f"wohnt {ort}")
    return " ".join(t for t in teile if t)


async def profil_ins_gedaechtnis(pid: str) -> bool:
    """Adresse aus der verknüpften Kundenkarte ins Wissen: in die Gruppe der Person und als
    Kopie mit Herkunft in Eikes Gruppe — damit „wo wohnt Kontakt A?“ aus dem Vollen geht.
    Ohne Verknüpfung oder ohne Adresse nichts. IBAN & Co. kommen nie in den Graphen."""
    from datetime import datetime, timezone
    from kern.gedaechtnis import graph
    person = register.holen(pid)
    if not person:
        return False
    nummer = (person.get("quellen") or {}).get("garantie")
    if not isinstance(nummer, int):
        return False
    profil = next((p for p in _garantie_profile() if p.get("id") == nummer), None)
    if profil is None:
        return False
    voll = _garantie_profile_voll(nummer)
    text = _profil_adresse(voll if voll else profil)
    if not text or "wohnt" not in text:
        return False
    jetzt = datetime.now(timezone.utc).isoformat(timespec="seconds")
    await graph.einspeisen(f"profil-{pid}-garantie", text, f"Kundenkarte: {person['name']}", jetzt,
                           gruppe=pid)
    await graph.einspeisen(f"profil-{pid}-garantie", f"[Herkunft: Garantie-Profil] {text}",
                           f"Kundenkarte: {person['name']}", jetzt, gruppe="eike")
    return True


def _garantie_profile_voll(nummer: int) -> dict | None:
    """Das volle Profil (mit Adresse) — nur hier, nie an Browser oder Modell."""
    try:
        return garantieablage.standard().profil(nummer)
    except Exception:  # noqa: BLE001 — kein Profil = keine Adresse, nie ein Absturz
        return None


async def profil_aus_dem_gedaechtnis(pid: str) -> None:
    """Adress-Episoden beider Gruppen entfernen (best effort, wie Freigaben-Nachlauf light)."""
    from kern.gedaechtnis import graph
    for gruppe in (pid, "eike"):
        try:
            await graph.episode_loeschen(f"profil-{pid}-garantie", gruppe)
        except Exception:  # noqa: BLE001 — ein Fehler löscht die Verknüpfung nie
            log.exception("Adress-Episode nicht gelöscht")
