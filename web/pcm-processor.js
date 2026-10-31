/**
 * Sammelt Mikrofon-Frames und schickt sie gebündelt an den Hauptthread.
 *
 * Ein AudioWorklet liefert 128 Samples pro Aufruf — bei 48 kHz sind das 375
 * postMessage pro Sekunde. Auf einem iPhone reicht das, um die Bridge zum
 * Hauptthread zu fluten. Darum wird hier auf ~2048 Samples gepuffert.
 */
class PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(2048);
    this.used = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;

    for (let i = 0; i < ch.length; i++) {
      this.buf[this.used++] = ch[i];
      if (this.used === this.buf.length) {
        // Kopie senden: der Puffer wird sofort weiterbeschrieben.
        this.port.postMessage(this.buf.slice(0));
        this.used = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcm-processor', PcmProcessor);
