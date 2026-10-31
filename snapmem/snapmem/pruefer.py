"""Bildprüfungen für die gefilterten Modi, hinter einer kleinen Schnittstelle, damit die
Pipeline ohne die schweren Modelle testbar bleibt."""

import re

from . import einbrand
from .faces import count_faces
from .personen import count_persons

_UUID = re.compile(r"_([0-9A-Fa-f-]{36})-main")


def grosse_uuid(name: str) -> bool:
    """Dateien mit GROSSgeschriebener UUID (iOS) haben viel öfter Eingebranntes ohne Overlay-Datei."""
    found = _UUID.search(name)
    return bool(found) and not re.search(r"[a-f]", found.group(1))


class StandardPruefer:
    def gesichter(self, data: bytes) -> int:
        return count_faces(data)

    def personen(self, data: bytes) -> int:
        return count_persons(data)

    def einbrand(self, data: bytes, name: str) -> tuple[bool, str]:
        """(eingebrannte Schrift/Sticker/Linse?, Begründung)."""
        return einbrand.pruefen(data, grosse_uuid(name), chat=name.startswith("chat_media/"))
