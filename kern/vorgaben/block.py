"""Der Prompt-Block `[VORGABEN VON EIKE …]` — die aktiven Vorgaben, nach Bereich gruppiert, begrenzt.

Nur für Eikes Kanäle (Chat, Sprache, Snapchat mit Eike, Nachdenken). Fremde Personen bekommen ihn nie. Fehler im Fach dürfen nie
ein Gespräch brechen: `bauen_sicher` gibt dann einen leeren Block.
"""

from __future__ import annotations

import logging

from kern.vorgaben import fach

log = logging.getLogger("friday.vorgaben")

KOPF = "[WAS EIKE DIR FEST AUFGETRAGEN HAT — verbindlich, vor deinem eigenen Ermessen; nur dein Wissen, sprich nie von Nummern]"
BLOCK_MAX = 3000


def formatieren(vorgaben: list[dict], maximal: int = BLOCK_MAX) -> str:
    """Kopf plus Zeilen „- (Nummer) Text“, nach Bereich gruppiert (Reihenfolge von `fach.BEREICHE`). Passt nicht alles, steht am
    Ende, wie viele fehlen (`vorgaben_lesen` zeigt alle)."""
    if not vorgaben:
        return ""
    zeilen, laenge, fehlen = [KOPF], len(KOPF), 0
    for bereich in fach.BEREICHE:
        eigene = [v for v in vorgaben if v["bereich"] == bereich]
        if not eigene:
            continue
        zeilen_bereich = [f"{bereich}:"] + [f"- ({v['id']}) {v['text']}" for v in eigene]
        for z in zeilen_bereich:
            if laenge + len(z) + 1 > maximal:
                fehlen += 1 if z.startswith("- (") else 0
                continue
            zeilen.append(z)
            laenge += len(z) + 1
    if fehlen:
        zeilen.append(f"(+{fehlen} weitere, die du bei Bedarf nachliest)")
    return "\n".join(zeilen)


def bauen(bereiche: tuple[str, ...] | None = None) -> str:
    """`bereiche` beschränkt den Block (Hintergrundjobs); ohne = alle."""
    # Vorgaben, die zu einem Skill gehören, stehen nicht doppelt hier: die Skill-Liste kommt als eigener Block dazu.
    aktive = [v for v in fach.standard().aktive() if v["quelle"] not in fach.GEBUNDEN]
    if bereiche is not None:
        aktive = [v for v in aktive if v["bereich"] in bereiche]
    text = formatieren(aktive)
    if bereiche is None:
        from kern.skills import zugriff  # hier, weil skills.zugriff über personen.register nichts von vorgaben weiß
        if (sk := zugriff.eike_block_sicher()):
            text = (text + "\n\n" + sk) if text else sk
    return text


def bauen_sicher(bereiche: tuple[str, ...] | None = None) -> str:
    try:
        return bauen(bereiche)
    except Exception:                                  # noqa: BLE001
        log.exception("Vorgaben nicht lesbar")
        return ""
