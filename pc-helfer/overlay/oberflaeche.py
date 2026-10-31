"""Das Overlay: eine schmale Pille über allen Fenstern, mit Zustand, Pegel und letzten Sätzen.

Frameless, durchsichtig, immer obenauf, ohne Eintrag in der Taskleiste. Verschieben mit der Maus.
Beenden nur über das Symbol im Infobereich (sonst schließt ein Fehlklick das ganze Programm). Ereignisse kommen aus einer Queue (`friday_overlay.py`), nie direkt aus den Threads.
"""

from __future__ import annotations

import math
import queue

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen, QRadialGradient)
from PySide6.QtWidgets import QApplication, QWidget

from stand import (BREITE, KOPF_HOEHE, RAND, ZEILEN_ABSTAND, ZEILE_HOEHE, ZUSTAENDE, Stand,
                   werkzeug_anzeige)

NACHLAUF_MS = 3000       # so lange bleibt das Fenster nach dem Gespräch stehen, damit man den letzten Satz liest
TEXT_BREITE = BREITE - RAND - 94   # Platz neben Kennwort „Friday“/„Du“


def _farbe(hexwert: str, alpha: int = 255) -> QColor:
    c = QColor(hexwert)
    c.setAlpha(alpha)
    return c


class Oberflaeche(QWidget):
    def __init__(self, eingang: queue.Queue):
        super().__init__()
        self._eingang = eingang
        self._stand = Stand()
        self._phase = 0.0
        self._ziehen = None
        self._platziert = False

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(BREITE)
        self._hoehe_setzen()
        self._klein = QFont()
        self._klein.setPixelSize(12)
        self._ausblenden = QTimer(self)
        self._ausblenden.setSingleShot(True)
        self._ausblenden.timeout.connect(self.hide)

        self._abfrage = QTimer(self)
        self._abfrage.timeout.connect(self._lies_queue)
        self._abfrage.start(30)
        self._zeichnen = QTimer(self)
        self._zeichnen.timeout.connect(self._tick)
        self._zeichnen.start(33)

    # ---- Ereignisse und Zustand -------------------------------------------------

    def _lies_queue(self) -> None:
        geaendert = False
        try:
            while True:
                ev = self._eingang.get_nowait()
                if ev[0] == "pegel":
                    self._stand.verarbeite(ev)
                    continue
                self._stand.verarbeite(ev)
                geaendert = True
        except queue.Empty:
            pass
        if geaendert:
            self._hoehe_setzen()
        self._sichtbarkeit()

    def _sichtbarkeit(self) -> None:
        """Da, solange ein Gespräch läuft. Nach dem Gespräch kurz nachlaufen, dann weg."""
        if self._stand.sichtbar:
            self._ausblenden.stop()
            if not self.isVisible():
                self.show()
        elif self.isVisible() and not self._ausblenden.isActive():
            self._ausblenden.start(NACHLAUF_MS)

    def _text_hoehe(self, text: str) -> int:
        rect = QFontMetrics(self._klein).boundingRect(QRect(0, 0, TEXT_BREITE, 10000), Qt.TextWordWrap, text)
        return max(ZEILE_HOEHE, rect.height())

    def _hoehe_setzen(self) -> None:
        hoehe = KOPF_HOEHE
        for _rolle, text in self._stand.zeilen:
            hoehe += self._text_hoehe(text) + ZEILEN_ABSTAND
        if self._stand.zeilen:
            hoehe += 4                                  # Luft unter dem letzten Satz
        if self.height() != hoehe:
            self.setFixedHeight(hoehe)

    def _am_rand_unten(self) -> None:
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - self.width() // 2, geo.bottom() - self.height() - 90)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if not self._platziert:
            self._platziert = True
            self._am_rand_unten()

    def _tick(self) -> None:
        self._phase += 0.12
        self._stand.pegel *= 0.97
        self.update()

    # ---- Zeichnen -----------------------------------------------------------------

    def paintEvent(self, _ev) -> None:
        akzent, beschriftung = ZUSTAENDE.get(self._stand.zustand, ZUSTAENDE["aus"])
        if self._stand.zustand == "fehler" and self._stand.fehler:
            beschriftung = self._stand.fehler
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        karte = QRectF(RAND / 2, RAND / 2, self.width() - RAND, self.height() - RAND)
        verlauf = QLinearGradient(karte.topLeft(), karte.bottomLeft())
        verlauf.setColorAt(0.0, _farbe("#1d2540", 232))
        verlauf.setColorAt(1.0, _farbe("#0b0f1c", 240))
        p.setPen(Qt.NoPen)
        p.setBrush(verlauf)
        p.drawRoundedRect(karte, 22, 22)

        # Leuchtrand in der Zustandsfarbe
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_farbe(akzent, 150), 1.4))
        p.drawRoundedRect(karte.adjusted(0.5, 0.5, -0.5, -0.5), 22, 22)

        # Orb links: Halo wächst mit dem Pegel
        mitte = QPointF(karte.left() + 30, karte.top() + KOPF_HOEHE / 2)
        atmen = 1.0 + 0.06 * math.sin(self._phase)
        halo = 14 + 14 * min(1.0, self._stand.pegel * 4) + 2 * atmen
        grad = QRadialGradient(mitte, halo)
        grad.setColorAt(0.0, _farbe(akzent, 230))
        grad.setColorAt(0.55, _farbe(akzent, 90))
        grad.setColorAt(1.0, _farbe(akzent, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawEllipse(mitte, halo, halo)
        p.setBrush(_farbe("#ffffff", 235))
        p.drawEllipse(mitte, 4.5, 4.5)

        # Balken rechts: Pegel beim Zuhören, Welle beim Sprechen, sonst ruhig
        balken = 7
        x0 = karte.right() - 22 - balken * 6
        basis = karte.top() + KOPF_HOEHE / 2
        for i in range(balken):
            if self._stand.zustand == "hoert":
                hoch = 3 + 22 * min(1.0, self._stand.pegel * 3) * (0.55 + 0.45 * math.sin(self._phase + i * 0.9))
            elif self._stand.zustand == "spricht":
                hoch = 4 + 12 * (0.5 + 0.5 * math.sin(self._phase * 1.6 + i * 0.7))
            elif self._stand.zustand == "denkt":
                hoch = 4 + 6 * (0.5 + 0.5 * math.sin(self._phase * 2 + i))
            else:
                hoch = 3 + 2 * math.sin(self._phase * 0.5 + i * 0.5)
            hoch = max(3.0, hoch)
            p.setBrush(_farbe(akzent, 220))
            p.drawRoundedRect(QRectF(x0 + i * 6, basis - hoch / 2, 3.5, hoch), 1.75, 1.75)

        # Beschriftung
        textlinks = mitte.x() + halo + 12
        schrift = QFont()
        schrift.setPixelSize(15)
        schrift.setWeight(QFont.DemiBold)
        p.setFont(schrift)
        p.setPen(_farbe("#f1f5f9", 245))
        kopf = QRectF(textlinks, karte.top() + 12, x0 - textlinks - 6, 22)
        if self._stand.werkzeug and self._stand.zustand == "denkt":
            beschriftung = f"{beschriftung} · {werkzeug_anzeige(self._stand.werkzeug)}"
        p.drawText(kopf, Qt.AlignLeft | Qt.AlignVCenter, beschriftung)

        # Letzte Sätze darunter
        if self._stand.zeilen:
            p.setFont(self._klein)
            y = karte.top() + KOPF_HOEHE - 2
            for rolle, text in self._stand.zeilen:
                du = rolle == "du"
                hoehe = self._text_hoehe(text)
                p.setPen(_farbe("#94a3b8" if du else akzent, 235))
                p.drawText(QRectF(karte.left() + 22, y, 50, hoehe), Qt.AlignLeft | Qt.AlignTop,
                           "Du" if du else "Friday")
                p.setPen(_farbe("#e2e8f0", 220))
                p.drawText(QRectF(karte.left() + 72, y, TEXT_BREITE, hoehe),
                           Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, text)
                y += hoehe + ZEILEN_ABSTAND
        p.end()

    # ---- Maus: verschieben, Menü -----------------------------------------------

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self._ziehen = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, ev) -> None:
        if self._ziehen is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._ziehen)

    def mouseReleaseEvent(self, _ev) -> None:
        self._ziehen = None
