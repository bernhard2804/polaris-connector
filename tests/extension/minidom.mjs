// Kleines DOM fuer die Extractor-Tests - ohne jsdom (keine Abhaengigkeit).
// Unterstuetzt genau das, was extension/extractor.js nutzt: Tag- und
// Attributselektoren ([a], [a="v"], ^=, $=, *=, Flag i), Selektorlisten mit
// Komma, Nachfahren-Kombinator (Leerzeichen), querySelector(All), matches,
// closest, getAttribute, parentElement, innerText/textContent, getClientRects.

function splitTop(s, sep) {
  const out = [];
  let depth = 0;
  let quote = null;
  let cur = '';
  for (const ch of s) {
    if (quote) {
      if (ch === quote) quote = null;
      cur += ch;
      continue;
    }
    if (ch === '"' || ch === "'") quote = ch;
    else if (ch === '[') depth++;
    else if (ch === ']') depth--;
    if (depth === 0 && (sep === ' ' ? /\s/.test(ch) : ch === sep)) {
      if (cur.trim()) out.push(cur.trim());
      cur = '';
      continue;
    }
    cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

function parseCompound(sel) {
  const m = sel.match(/^([a-zA-Z][\w-]*)?(.*)$/);
  const tag = m[1] ? m[1].toLowerCase() : null;
  const attrs = [];
  const re = /\[\s*([\w-]+)\s*(?:([\^$*]?=)\s*"([^"]*)"\s*(i)?)?\s*\]/g;
  let a;
  while ((a = re.exec(m[2]))) attrs.push({ name: a[1], op: a[2] || null, value: a[3], ci: !!a[4] });
  return { tag, attrs };
}

function matchesCompound(el, { tag, attrs }) {
  if (tag && el.tagName.toLowerCase() !== tag) return false;
  for (const { name, op, value, ci } of attrs) {
    if (!(name in el.attributes)) return false;
    if (!op) continue;
    let v = el.attributes[name];
    let w = value;
    if (ci) {
      v = v.toLowerCase();
      w = w.toLowerCase();
    }
    if (op === '=' && v !== w) return false;
    if (op === '^=' && !v.startsWith(w)) return false;
    if (op === '$=' && !v.endsWith(w)) return false;
    if (op === '*=' && !v.includes(w)) return false;
  }
  return true;
}

function matchesSelector(el, selector) {
  return splitTop(selector, ',').some((complex) => {
    const parts = splitTop(complex, ' ').map(parseCompound);
    if (!matchesCompound(el, parts[parts.length - 1])) return false;
    let node = el.parentElement;
    for (let i = parts.length - 2; i >= 0; i--) {
      while (node && !matchesCompound(node, parts[i])) node = node.parentElement;
      if (!node) return false;
      node = node.parentElement;
    }
    return true;
  });
}

export class El {
  constructor(tag, attributes = {}, children = [], { text = '', visible = true } = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = { ...attributes };
    this.children = [];
    this.parentElement = null;
    this._text = text;
    this._visible = visible;
    for (const c of children) this.append(c);
  }
  append(child) {
    child.parentElement = this;
    this.children.push(child);
    return this;
  }
  getAttribute(name) {
    return name in this.attributes ? this.attributes[name] : null;
  }
  *descendants() {
    for (const c of this.children) {
      yield c;
      yield* c.descendants();
    }
  }
  querySelectorAll(selector) {
    return [...this.descendants()].filter((d) => matchesSelector(d, selector));
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  matches(selector) {
    return matchesSelector(this, selector);
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (node.matches(selector)) return node;
      node = node.parentElement;
    }
    return null;
  }
  get innerText() {
    return [this._text, ...this.children.map((c) => c.innerText)].filter(Boolean).join('\n');
  }
  get textContent() {
    return this.innerText;
  }
  getClientRects() {
    return this._visible ? [{}] : [];
  }
}

export const h = (tag, attributes, children = [], opts) => new El(tag, attributes, children, opts);
