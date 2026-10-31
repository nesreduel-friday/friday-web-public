/* Formeln im Chat — ein kleiner LaTeX-Zeichner ohne Abhängigkeit.

   Eike sah am 30.10.2026 statt einer Formel den Rohtext
   „$\frac{n!}{k!(n-k)!} = \binom{n}{k}$“. Die Oberfläche setzt Modelltext
   bewusst als Textknoten (Prompt-Injection), also zeichnet dieser kleine
   Renderer die üblichen Mathe-Befehle selbst — nur aus Textknoten und
   eigenen Elementen, ohne Netz und ohne Fremdbibliothek. Was er nicht
   kennt, bleibt wörtlich stehen.

   `teile(text)` zerlegt Fließtext in Text- und Mathe-Teile: $…$ und \(…\)
   inline, $$…$$ und \[…\] als Block. `zeichne(ziel, inhalt, block)` hängt
   die gezeichnete Formel an ein Element. Geld wie „$5“ bleibt Text. */
(() => {
  const SYMBOLE = {
    alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ε', varepsilon: 'ε',
    zeta: 'ζ', eta: 'η', theta: 'θ', vartheta: 'ϑ', iota: 'ι', kappa: 'κ',
    lambda: 'λ', mu: 'μ', nu: 'ν', xi: 'ξ', omicron: 'ο', pi: 'π', varpi: 'ϖ',
    rho: 'ρ', varrho: 'ϱ', sigma: 'σ', varsigma: 'ς', tau: 'τ', upsilon: 'υ',
    phi: 'φ', varphi: 'φ', chi: 'χ', psi: 'ψ', omega: 'ω',
    Gamma: 'Γ', Delta: 'Δ', Theta: 'Θ', Lambda: 'Λ', Xi: 'Ξ', Pi: 'Π', Sigma: 'Σ',
    Upsilon: 'Υ', Phi: 'Φ', Psi: 'Ψ', Omega: 'Ω',
    times: '×', cdot: '·', cdotp: '·', div: '÷', pm: '±', mp: '∓', ast: '∗',
    star: '⋆', bullet: '•', circ: '∘', degree: '°', prime: '′',
    le: '≤', leq: '≤', ge: '≥', geq: '≥', ne: '≠', neq: '≠', approx: '≈',
    equiv: '≡', sim: '∼', simeq: '≃', propto: '∝', ll: '≪', gg: '≫',
    infty: '∞', partial: '∂', nabla: '∇', forall: '∀', exists: '∃',
    in: '∈', notin: '∉', ni: '∋', subset: '⊂', subseteq: '⊆',
    supset: '⊃', supseteq: '⊇', cup: '∪', cap: '∩', emptyset: '∅', varnothing: '∅',
    setminus: '∖', sum: '∑', prod: '∏', coprod: '∐', int: '∫', iint: '∬',
    iiint: '∭', oint: '∮', to: '→', rightarrow: '→', leftarrow: '←', gets: '←',
    leftrightarrow: '↔', Rightarrow: '⇒', Leftarrow: '⇐', Leftrightarrow: '⇔',
    implies: '⟹', iff: '⟺', mapsto: '↦', longrightarrow: '⟶', longleftarrow: '⟵',
    ldots: '…', dots: '…', cdots: '⋯', vdots: '⋮', ddots: '⋱',
    angle: '∠', perp: '⊥', parallel: '∥', therefore: '∴', because: '∵',
    neg: '¬', lnot: '¬', land: '∧', wedge: '∧', lor: '∨', vee: '∨',
    oplus: '⊕', otimes: '⊗', triangle: '△', square: '□', checkmark: '✓',
    langle: '⟨', rangle: '⟩', lfloor: '⌊', rfloor: '⌋', lceil: '⌈', rceil: '⌉',
    mid: '|', vert: '|', Vert: '‖', '%': '%', '$': '$', '&': '&', '_': '_',
    '#': '#', '{': '{', '}': '}', '|': '‖',
  };

  // Aufrecht gesetzte Namen (sonst würde „sin“ wie a·s·i·n aussehen).
  const FUNKTIONEN = new Set([
    'sin', 'cos', 'tan', 'cot', 'sec', 'csc', 'arcsin', 'arccos', 'arctan',
    'sinh', 'cosh', 'tanh', 'coth', 'log', 'ln', 'lg', 'exp', 'lim', 'max',
    'min', 'sup', 'inf', 'det', 'dim', 'ker', 'deg', 'gcd', 'hom', 'arg', 'Pr',
  ]);

  const TEXT_BEFEHLE = new Set([
    'text', 'textrm', 'textnormal', 'textup', 'mathrm', 'mathsf', 'mathtt',
    'mathcal', 'mathbb', 'mathfrak', 'mathnormal', 'operatorname',
  ]);

  const SCHMUCK = {
    overline: 'mathe-overline', bar: 'mathe-overline', underline: 'mathe-underline',
    vec: 'mathe-vektor', hat: 'mathe-hat', widehat: 'mathe-hat',
    tilde: 'mathe-tilde', widetilde: 'mathe-tilde', dot: 'mathe-dot', ddot: 'mathe-ddot',
  };

  const ABSTAENDE = {
    ',': ' ', ':': ' ', ';': ' ', '!': '', ' ': ' ',
    quad: ' ', qquad: '  ',
  };

  const OHNE_WIRKUNG = new Set([
    'left', 'right', 'middle', 'big', 'Big', 'bigg', 'Bigg',
    'bigl', 'bigr', 'Bigl', 'Bigr', 'biggl', 'biggr', 'Biggl', 'Biggr',
    'displaystyle', 'textstyle', 'scriptstyle', 'scriptscriptstyle', 'limits', 'nolimits',
  ]);

  const DOPPELSTRICH = { R: 'ℝ', N: 'ℕ', Z: 'ℤ', Q: 'ℚ', C: 'ℂ', P: 'ℙ', H: 'ℍ' };

  const MATHE_ZEICHEN = /[=<>+\-*\/^_{}±×÷·≤≥≠≈∞→←↔⇒⇔∑∏∫√]/;

  // Steht vor dieser Stelle eine ungerade Zahl Backslashes? Dann ist das Zeichen escaped.
  function _maskiert(text, pos) {
    let n = 0;
    for (let i = pos - 1; i >= 0 && text[i] === '\\'; i--) n++;
    return n % 2 === 1;
  }

  // Nur was nach Formel aussieht, wird gezeichnet. „$5“ und „$10“ bleiben Geld.
  function _istMathe(inhalt, block) {
    if (!inhalt.trim()) return false;
    if (block) return true;
    if (/[\\^_{}]/.test(inhalt)) return true;
    if (MATHE_ZEICHEN.test(inhalt)) return true;
    if (/^[A-Za-z]$/.test(inhalt.trim())) return true;
    if (/^[\s\d.,+\-*\/()]+$/.test(inhalt) && /[+\-*\/]/.test(inhalt)) return true;
    return false;
  }

  function _endeInline(text, von) {
    for (let i = von; i < text.length; i++) {
      if (text[i] === '\n') return -1;                       // $…$ endet mit der Zeile
      if (text[i] === '$' && !_maskiert(text, i)) {
        if (text[i + 1] === '$') return -1;                  // kein halber Block
        return i;
      }
    }
    return -1;
  }

  function teile(text) {
    const roh = String(text ?? '');
    const teile = [];
    let pos = 0, i = 0;
    const textBis = (bis) => { if (bis > pos) teile.push({ art: 'text', text: roh.slice(pos, bis) }); };

    while (i < roh.length) {
      let start = -1, inhalt = '', block = false, nach = 0;
      if (roh[i] === '$' && !_maskiert(roh, i)) {
        if (roh[i + 1] === '$') {
          const ende = roh.indexOf('$$', i + 2);
          if (ende !== -1) { start = i; inhalt = roh.slice(i + 2, ende); block = true; nach = ende + 2; }
        } else {
          const ende = _endeInline(roh, i + 1);
          if (ende !== -1) { start = i; inhalt = roh.slice(i + 1, ende); nach = ende + 1; }
        }
      } else if (roh.startsWith('\\[', i) && !_maskiert(roh, i)) {
        const ende = roh.indexOf('\\]', i + 2);
        if (ende !== -1) { start = i; inhalt = roh.slice(i + 2, ende); block = true; nach = ende + 2; }
      } else if (roh.startsWith('\\(', i) && !_maskiert(roh, i)) {
        const ende = roh.indexOf('\\)', i + 2);
        if (ende !== -1) { start = i; inhalt = roh.slice(i + 2, ende); nach = ende + 2; }
      }

      if (start >= 0 && _istMathe(inhalt, block)) {
        textBis(start);
        teile.push({ art: 'mathe', text: inhalt, block });
        pos = nach;
        i = nach;
      } else {
        i++;
      }
    }
    textBis(roh.length);
    if (!teile.length) teile.push({ art: 'text', text: '' });
    return teile;
  }

  function _leerzeichen(z) {
    while (z.i < z.quelle.length && /\s/.test(z.quelle[z.i])) z.i++;
  }

  function _atom(z) {
    const frag = document.createDocumentFragment();
    _leerzeichen(z);
    if (z.i >= z.quelle.length) return frag;
    const c = z.quelle[z.i];
    if (c === '{') { z.i++; frag.appendChild(_liste(z, '}')); }
    else if (c === '\\') { _befehl(z, frag); }
    else { frag.appendChild(document.createTextNode(c)); z.i++; }
    return frag;
  }

  function _liste(z, ende) {
    const frag = document.createDocumentFragment();
    const s = z.quelle;
    while (z.i < s.length) {
      const c = s[z.i];
      if (ende && c === ende) { z.i++; return frag; }
      if (c === '{') { z.i++; frag.appendChild(_liste(z, '}')); continue; }
      if (c === '}') { z.i++; if (ende) return frag; continue; }
      if (c === '^' || c === '_') {
        z.i++;
        const e = document.createElement(c === '^' ? 'sup' : 'sub');
        e.appendChild(_atom(z));
        frag.appendChild(e);
        continue;
      }
      if (c === '\\') { _befehl(z, frag); continue; }
      if (c === '~') { frag.appendChild(document.createTextNode(' ')); z.i++; continue; }
      if (c === '&') { z.i++; continue; }                    // Ausrichtung (align) ohne Spalten
      frag.appendChild(document.createTextNode(c));
      z.i++;
    }
    return frag;
  }

  // Rohinhalt eines Arguments ({…} oder ein Zeichen) für \text & Co.
  function _argumentRoh(z) {
    _leerzeichen(z);
    const s = z.quelle;
    if (z.i >= s.length) return '';
    if (s[z.i] !== '{') return s[z.i++];
    let tiefe = 0;
    const start = ++z.i;
    while (z.i < s.length) {
      const c = s[z.i];
      if (c === '\\') { z.i += 2; continue; }
      if (c === '{') tiefe++;
      else if (c === '}') {
        if (tiefe === 0) { const roh = s.slice(start, z.i); z.i++; return roh; }
        tiefe--;
      }
      z.i++;
    }
    return s.slice(start);
  }

  function _entschaerfen(roh) {
    return roh.replace(/\\([\\{}%$&#_ ])/g, '$1');
  }

  function _bruch(z) {
    const e = document.createElement('span');
    e.className = 'mathe-bruch';
    const oben = document.createElement('span');
    oben.className = 'mathe-zaehler';
    oben.appendChild(_atom(z));
    const unten = document.createElement('span');
    unten.className = 'mathe-nenner';
    unten.appendChild(_atom(z));
    e.appendChild(oben);
    e.appendChild(unten);
    return e;
  }

  function _binom(z) {
    const e = document.createElement('span');
    e.className = 'mathe-binom';
    const inhalt = document.createElement('span');
    inhalt.className = 'mathe-binom-inhalt';
    const oben = document.createElement('span');
    oben.className = 'mathe-binom-zeile';
    oben.appendChild(_atom(z));
    const unten = document.createElement('span');
    unten.className = 'mathe-binom-zeile';
    unten.appendChild(_atom(z));
    inhalt.appendChild(oben);
    inhalt.appendChild(unten);
    e.appendChild(inhalt);
    return e;
  }

  function _wurzel(z) {
    _leerzeichen(z);
    let index = null;
    if (z.quelle[z.i] === '[') { z.i++; index = _liste(z, ']'); }
    const e = document.createElement('span');
    e.className = 'mathe-wurzel';
    if (index) {
      const w = document.createElement('sup');
      w.className = 'mathe-wurzel-index';
      w.appendChild(index);
      e.appendChild(w);
    }
    const zeichen = document.createElement('span');
    zeichen.className = 'mathe-wurzel-zeichen';
    zeichen.textContent = '√';
    e.appendChild(zeichen);
    const inhalt = document.createElement('span');
    inhalt.className = 'mathe-wurzel-inhalt';
    inhalt.appendChild(_atom(z));
    e.appendChild(inhalt);
    return e;
  }

  function _befehl(z, ziel) {
    const s = z.quelle;
    z.i++;                                                   // Backslash
    let name = '';
    if (z.i < s.length && /[A-Za-z]/.test(s[z.i])) {
      const start = z.i;
      while (z.i < s.length && /[A-Za-z]/.test(s[z.i])) z.i++;
      name = s.slice(start, z.i);
    } else if (z.i < s.length) {
      name = s[z.i++];                                       // \, \{ \\ …
    }

    if (name === '\\') { ziel.appendChild(document.createElement('br')); return; }
    if (name === 'frac' || name === 'dfrac' || name === 'tfrac' || name === 'cfrac') {
      ziel.appendChild(_bruch(z)); return;
    }
    if (name === 'binom' || name === 'dbinom' || name === 'tbinom') {
      ziel.appendChild(_binom(z)); return;
    }
    if (name === 'sqrt') { ziel.appendChild(_wurzel(z)); return; }
    if (OHNE_WIRKUNG.has(name)) {
      if (name === 'right' && s[z.i] === '.') z.i++;         // unsichtbare Klammer
      return;
    }
    if (name === 'mathbf' || name === 'textbf') {
      const e = document.createElement('strong');
      e.appendChild(_atom(z));
      ziel.appendChild(e);
      return;
    }
    if (name === 'mathit' || name === 'textit') {
      const e = document.createElement('em');
      e.appendChild(_atom(z));
      ziel.appendChild(e);
      return;
    }
    if (name === 'pmod') {
      ziel.appendChild(document.createTextNode(` (mod ${_entschaerfen(_argumentRoh(z))})`));
      return;
    }
    if (name === 'bmod' || name === 'mod') {
      ziel.appendChild(document.createTextNode(' mod '));
      return;
    }
    if (TEXT_BEFEHLE.has(name)) {
      let roh = _entschaerfen(_argumentRoh(z));
      if (name === 'mathbb') roh = [...roh].map((c) => DOPPELSTRICH[c] || c).join('');
      ziel.appendChild(document.createTextNode(roh));
      return;
    }
    if (SCHMUCK[name]) {
      const e = document.createElement('span');
      e.className = SCHMUCK[name];
      e.appendChild(_atom(z));
      ziel.appendChild(e);
      return;
    }
    if (Object.prototype.hasOwnProperty.call(ABSTAENDE, name)) {
      if (ABSTAENDE[name]) ziel.appendChild(document.createTextNode(ABSTAENDE[name]));
      return;
    }
    if (FUNKTIONEN.has(name)) {
      ziel.appendChild(document.createTextNode(name));
      return;
    }
    if (Object.prototype.hasOwnProperty.call(SYMBOLE, name)) {
      ziel.appendChild(document.createTextNode(SYMBOLE[name]));
      return;
    }
    // Unbekannt: wörtlich stehen lassen und nichts verschlucken.
    ziel.appendChild(document.createTextNode(`\\${name}`));
  }

  function zeichne(ziel, inhalt, block) {
    // Immer ein <span>: Die Formel steht in der Textspanne der Chat-Zeile;
    // die Blockdarstellung kommt aus der Klasse (display: block).
    const rahmen = document.createElement('span');
    rahmen.className = block ? 'mathe mathe-block' : 'mathe';
    rahmen.appendChild(_liste({ i: 0, quelle: String(inhalt ?? '') }, ''));
    ziel.appendChild(rahmen);
    return rahmen;
  }

  window.FridayMathe = { teile, zeichne };
})();
