// Verhaltenstest für web/mathe.js mit eigenem DOM-Stub (keine Pakete).
// Aufruf: node mathe_verhalten.js <pfad zu mathe.js>
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const quelle = fs.readFileSync(process.argv[2], 'utf8');

class Knoten {
  constructor(tag, text = '') {
    this.tag = tag;
    this.kind = tag === '#text' ? 'text' : 'el';
    this.textContent = text;
    this.children = [];
    this.className = '';
  }
  appendChild(k) { this.children.push(k); return k; }
  append(...k) { this.children.push(...k); }
  all(f, out = []) { if (f(this)) out.push(this); this.children.forEach((c) => c.all(f, out)); return out; }
}

const dokument = {
  createElement: (tag) => new Knoten(tag),
  createTextNode: (text) => new Knoten('#text', text),
  createDocumentFragment: () => new Knoten('#fragment'),
};
const sb = { window: {}, document: dokument, console };
vm.runInNewContext(quelle, sb);
const M = sb.window.FridayMathe;
assert(M && typeof M.teile === 'function' && typeof M.zeichne === 'function', 'FridayMathe fehlt');

const text = (k) => (k.kind === 'text' ? k.textContent : (k.textContent || '') + k.children.map(text).join(''));
const hatKlasse = (k, klasse) => k.all((e) => e.kind === 'el' && e.className.split(/\s+/).includes(klasse)).length > 0;
const erste = (k, klasse) => k.all((e) => e.kind === 'el' && e.className.split(/\s+/).includes(klasse))[0];

// 1. Text, Formel, Text: Dollarzeichen werden aus dem Fließtext herausgeschnitten.
const teile1 = M.teile('Die Binomialkoeffizienten-Formel $\\frac{n!}{k!(n-k)!} = \\binom{n}{k}$ gilt.');
assert.strictEqual(teile1.length, 3, JSON.stringify(teile1));
assert.strictEqual(teile1[0].art, 'text');
assert.strictEqual(teile1[1].art, 'mathe');
assert.strictEqual(teile1[1].block, false);
assert.ok(teile1[1].text.includes('binom'));
assert.strictEqual(teile1[2].art, 'text');

// 2. Kein Mathe-Raten bei Geldbeträgen.
const teile2 = M.teile('Das kostet $5 und jenes $10.');
assert.strictEqual(teile2.filter((t) => t.art === 'mathe').length, 0);
assert.ok(teile2.map((t) => t.text).join('').includes('$5'));

// 3. Anzeigeformel: $$…$$ und \[…\] sind Blöcke.
for (const quelle2 of ['$$\\frac{1}{2}$$', '\\[x^2 + y^2\\]']) {
  const t = M.teile(quelle2);
  assert.strictEqual(t.length, 1, quelle2);
  assert.strictEqual(t[0].art, 'mathe', quelle2);
  assert.strictEqual(t[0].block, true, quelle2);
}

// 4. Escaped Dollarzeichen bleibt Text, unvollständige Formel bleibt Text.
assert.strictEqual(M.teile('\\$5').filter((t) => t.art === 'mathe').length, 0);
assert.strictEqual(M.teile('$x + 1').filter((t) => t.art === 'mathe').length, 0);

// 5. Bruch, Binomialkoeffizient, Hoch/Tief, Wurzel, Symbole.
const ziel = new Knoten('div');
M.zeichne(ziel, '\\frac{n!}{k!(n-k)!} = \\binom{n}{k}', false);
assert.ok(hatKlasse(ziel, 'mathe'));
assert.ok(hatKlasse(ziel, 'mathe-bruch'));
assert.strictEqual(erste(ziel, 'mathe-bruch').children.length, 2);
assert.ok(hatKlasse(erste(ziel, 'mathe-bruch').children[0], 'mathe-zaehler'));
assert.ok(hatKlasse(erste(ziel, 'mathe-bruch').children[1], 'mathe-nenner'));
assert.ok(hatKlasse(ziel, 'mathe-binom'));
assert.ok(text(ziel).includes('='));
assert.ok(text(ziel).includes('!'));

const ziel2 = new Knoten('div');
M.zeichne(ziel2, 'x^2 + a_{i+1}', false);
assert.strictEqual(ziel2.all((e) => e.tag === 'sup').length, 1);
assert.strictEqual(ziel2.all((e) => e.tag === 'sub').length, 1);
assert.ok(text(ziel2).includes('2'));
assert.ok(text(ziel2).includes('i+1'));

const ziel3 = new Knoten('div');
M.zeichne(ziel3, '\\sqrt[3]{x}', false);
assert.ok(hatKlasse(ziel3, 'mathe-wurzel'));
assert.ok(hatKlasse(ziel3, 'mathe-wurzel-index'));
assert.ok(text(ziel3).includes('√'));

const ziel4 = new Knoten('div');
M.zeichne(ziel4, '\\alpha \\le \\beta \\cdot \\infty \\to \\sum', false);
for (const z of ['α', '≤', 'β', '·', '∞', '→', '∑']) assert.ok(text(ziel4).includes(z), z);

// 6. \left( … \right) und \text: Klammern bzw. Rohtext, niemals HTML.
const ziel5 = new Knoten('div');
M.zeichne(ziel5, '\\left( \\frac{a}{b} \\right) \\text{<img src=x onerror=1>}', false);
assert.ok(text(ziel5).includes('(') && text(ziel5).includes(')'));
assert.ok(text(ziel5).includes('<img src=x onerror=1>'));
assert.strictEqual(ziel5.all((e) => e.tag === 'img').length, 0);
assert.strictEqual(ziel5.all((e) => e.kind === 'text' && e.textContent.includes('<img')).length, 1);

// 7. Blockformel bekommt einen eigenen Block, Inline bleibt inline.
const ziel6 = new Knoten('div');
M.zeichne(ziel6, 'x + 1', false);
assert.strictEqual(ziel6.children[0].tag, 'span');
const ziel7 = new Knoten('div');
M.zeichne(ziel7, 'x + 1', true);
assert.strictEqual(ziel7.children[0].tag, 'span');
assert.ok(hatKlasse(ziel7, 'mathe-block'));

console.log('ok');
