"""Was eine fremde Person über Friday nutzen darf — eine feste Positivliste.

Nur diese Werkzeuge sind für Fremde wählbar; alles andere (Kalender, Mails, WhatsApp, Notizbuch, Garantie,
Schule, Standort, PC, hphome, Gedächtnis) ist es nie. Ein neues Werkzeug ist damit standardmäßig NICHT für
Fremde erlaubt: Es müsste hier von Hand eingetragen werden. Der Antwortmotor (Teil 2) filtert mit derselben Liste.

Bewusst NICHT drin (Review 29.09.2026): `wetter` — ohne Ort nennt es Eikes Wohnort (`FRIDAY_ORT`); erst wieder mit
erzwungenem Ort. `webseite` — hphome ruft dann beliebige Adressen ab, der Betreiber sähe Eikes Heim-IP im Log."""

from __future__ import annotations

from kern.personen import PersonenFehler

FREMDEN_ERLAUBT: dict[str, str] = {
    "uhrzeit": "Uhrzeit und Datum",
    "suchen": "Internetsuche",
    "nachrichten": "Nachrichten aus dem Netz",
    "stundenplan": "Eikes Stundenplan (Fächer, Zeiten, Räume)",
    "kalender_frei": "Nur ob Eike Zeit hat — nie was im Kalender steht",
    "kalender": "Eikes Kalender mit allen Details (Titel, Zeiten)",
    "kalender_suchen": "Im Kalender nach Terminen suchen (mit Details)",
    "termin_wunsch": "Eike Termine vorschlagen (er entscheidet im Menü)",
}
STANDARD: tuple[str, ...] = ("uhrzeit",)

# Nur für die Anzeige in der Kontakte-Seite (gibt nichts frei): Gruppe und ob das Recht Eikes Daten sichtbar macht.
GRUPPEN: dict[str, str] = {
    "uhrzeit": "Allgemein", "suchen": "Allgemein", "nachrichten": "Allgemein",
    "stundenplan": "Eikes Plan", "kalender_frei": "Eikes Plan", "kalender": "Eikes Plan",
    "kalender_suchen": "Eikes Plan", "termin_wunsch": "Eikes Plan",
}
EIKES_DATEN: frozenset[str] = frozenset({"stundenplan", "kalender", "kalender_suchen"})


def pruefen(namen) -> list[str]:
    """Die Namen ohne Doppelte in der Reihenfolge des Aufrufs; wirft bei allem, was nicht erlaubt ist."""
    if not isinstance(namen, (list, tuple)):
        raise PersonenFehler("Die Werkzeuge müssen eine Liste sein.")
    aus: list[str] = []
    for n in namen:
        if not isinstance(n, str) or n not in FREMDEN_ERLAUBT:
            raise PersonenFehler(f"„{n}“ darf eine fremde Person nicht nutzen.")
        if n not in aus:
            aus.append(n)
    return aus
