/**
 * Editing an App's code from its page: change any text by retyping it where
 * it shows, and move sections by dragging them (Oct 2026).
 *
 * An App (or a dashboard) is code Norm wrote: some static markup and a script
 * that draws the rest from live data. What the person sees is that output,
 * not the code, so an edit on the page has to be carried back into the code.
 * Norm then starts its next change from the edited code, so it keeps the
 * person's words and layout.
 *
 * Text. Every piece of text in the code is found: text between tags in the
 * markup, and every string in the script (split around any tags it carries).
 * An edit on the page names the text it changed (before and after); the piece
 * whose words appear in that text and cover the change is the one rewritten.
 * Text that isn't in the code (a figure, a venue's name) has no piece and
 * can't be edited this way. When the same words appear in several places in
 * the code, `markPieces` lets the host render a hidden copy with each
 * candidate tagged by invisible characters, and read which tag shows where
 * the person clicked.
 *
 * Layout. A page laid out in sections (the convention Norm is told to follow):
 *
 *   <div data-norm-row>
 *     <div data-norm-col data-norm-span="2">
 *       <section data-norm-block="sales"></section>
 *       …
 *
 * Rows hold columns side by side (stacked on a phone); a column's span is its
 * share of the row; sections stack in a column and the script fills each one
 * through `norm.block(id)`. Moving a section moves its tag in the markup.
 */

export interface Piece {
  /** where the text's raw characters are in the source */
  start: number;
  end: number;
  /** the text as it shows on the page */
  value: string;
  /** markup text, or a string in the script */
  kind: 'html' | 'js';
  /** the script string's quote, for re-escaping */
  quote?: string;
  /** the string holds markup (tags or entities): new text is HTML-escaped */
  htmlish: boolean;
}

// ---------------------------------------------------------------------------
// Entities

const ENTITIES: Record<string, string> = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', middot: '·',
  mdash: '—', ndash: '–', hellip: '…', times: '×', divide: '÷', rarr: '→',
  larr: '←', uarr: '↑', darr: '↓', deg: '°', pound: '£', euro: '€', yen: '¥',
  copy: '©', reg: '®', trade: '™', bull: '•', minus: '−', lsquo: '‘', rsquo: '’',
  ldquo: '“', rdquo: '”', laquo: '«', raquo: '»', thinsp: ' ', hairsp: ' ',
  ensp: ' ', emsp: ' ', plusmn: '±', le: '≤', ge: '≥', ne: '≠', half: '½',
  frac12: '½', frac14: '¼', frac34: '¾', star: '☆', check: '✓', cross: '✗',
};

export function decodeEntities(text: string): string {
  return text.replace(/&(#x[0-9a-f]+|#[0-9]+|[a-z][a-z0-9]*);/gi, (all, body: string) => {
    if (body[0] === '#') {
      const code = body[1] === 'x' || body[1] === 'X' ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : all;
    }
    return ENTITIES[body.toLowerCase()] ?? all;
  });
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ---------------------------------------------------------------------------
// The source's parts: markup, scripts, styles

interface Region { start: number; end: number; kind: 'html' | 'script' | 'style' | 'comment' }

export function regions(source: string): Region[] {
  const out: Region[] = [];
  let i = 0;
  let htmlStart = 0;
  const lower = source.toLowerCase();
  while (i < source.length) {
    const lt = source.indexOf('<', i);
    if (lt < 0) break;
    i = lt;
    if (source.startsWith('<!--', i)) {
      const end = source.indexOf('-->', i + 4);
      const stop = end < 0 ? source.length : end + 3;
      if (i > htmlStart) out.push({ start: htmlStart, end: i, kind: 'html' });
      out.push({ start: i, end: stop, kind: 'comment' });
      i = htmlStart = stop;
      continue;
    }
    const m = /^<(script|style)\b[^>]*>/i.exec(source.slice(i, i + 400));
    if (m) {
      const tag = m[1].toLowerCase();
      const bodyStart = i + m[0].length;
      const close = lower.indexOf(`</${tag}`, bodyStart);
      const bodyEnd = close < 0 ? source.length : close;
      out.push({ start: htmlStart, end: bodyStart, kind: 'html' });
      out.push({ start: bodyStart, end: bodyEnd, kind: tag === 'script' ? 'script' : 'style' });
      i = htmlStart = bodyEnd;
      continue;
    }
    i++;
  }
  if (htmlStart < source.length) out.push({ start: htmlStart, end: source.length, kind: 'html' });
  return out.filter((r) => r.end > r.start);
}

// ---------------------------------------------------------------------------
// Script strings

interface JsString { start: number; end: number; quote: string }

const REGEX_AFTER = new Set(['(', ',', '=', ':', '[', '!', '&', '|', '?', '{', '}', ';', '+', '-', '*', '%', '<', '>', '~', '^']);
const REGEX_KEYWORDS = new Set(['return', 'typeof', 'case', 'do', 'else', 'in', 'of', 'new', 'delete', 'void', 'throw', 'yield', 'await', 'instanceof']);

/** Every string in a script: quoted strings, and each fixed part of a
 * template string. `start`/`end` are the string's content (no quotes). */
export function jsStrings(source: string, from: number, to: number): JsString[] {
  const out: JsString[] = [];
  let i = from;
  // what came before, to tell a regex from a division
  let prev: string | null = null;

  const scan = (stopAtBrace: boolean): void => {
    let depth = 0;
    while (i < to) {
      const c = source[i];
      if (c === ' ' || c === '\t' || c === '\n' || c === '\r') { i++; continue; }
      if (c === '/' && source[i + 1] === '/') {
        const nl = source.indexOf('\n', i);
        i = nl < 0 || nl > to ? to : nl;
        continue;
      }
      if (c === '/' && source[i + 1] === '*') {
        const close = source.indexOf('*/', i + 2);
        i = close < 0 || close > to ? to : close + 2;
        continue;
      }
      if (c === "'" || c === '"') {
        let j = i + 1;
        while (j < to && source[j] !== c && source[j] !== '\n') j += source[j] === '\\' ? 2 : 1;
        out.push({ start: i + 1, end: Math.min(j, to), quote: c });
        i = j + 1;
        prev = 'str';
        continue;
      }
      if (c === '`') {
        let j = i + 1;
        let chunk = j;
        while (j < to) {
          if (source[j] === '\\') { j += 2; continue; }
          if (source[j] === '`') break;
          if (source[j] === '$' && source[j + 1] === '{') {
            out.push({ start: chunk, end: j, quote: '`' });
            i = j + 2;
            prev = '{';
            scan(true);
            j = i;
            chunk = j;
            continue;
          }
          j++;
        }
        out.push({ start: chunk, end: Math.min(j, to), quote: '`' });
        i = j + 1;
        prev = 'str';
        continue;
      }
      if (c === '/') {
        const isRegex = prev === null || REGEX_AFTER.has(prev) || REGEX_KEYWORDS.has(prev);
        if (isRegex) {
          let j = i + 1;
          let inClass = false;
          while (j < to && source[j] !== '\n') {
            const ch = source[j];
            if (ch === '\\') { j += 2; continue; }
            if (ch === '[') inClass = true;
            else if (ch === ']') inClass = false;
            else if (ch === '/' && !inClass) { j++; break; }
            j++;
          }
          while (j < to && /[a-z]/i.test(source[j])) j++;
          i = j;
          prev = 'regex';
          continue;
        }
        i++;
        prev = '/';
        continue;
      }
      if (/[A-Za-z0-9_$]/.test(c)) {
        let j = i + 1;
        while (j < to && /[A-Za-z0-9_$]/.test(source[j])) j++;
        prev = source.slice(i, j);
        if (!REGEX_KEYWORDS.has(prev)) prev = 'ident';
        i = j;
        continue;
      }
      if (c === '{') depth++;
      if (c === '}') {
        if (stopAtBrace && depth === 0) { i++; return; }
        depth--;
      }
      prev = c === ')' || c === ']' ? 'ident' : c;
      i++;
    }
  };
  scan(false);
  return out;
}

/** A script string's text, with where each character came from. */
function decodeJs(raw: string): { text: string; from: number[] } {
  let text = '';
  const from: number[] = [];
  let i = 0;
  const push = (s: string, at: number) => { for (const ch of s) { text += ch; from.push(at); } };
  while (i < raw.length) {
    const c = raw[i];
    if (c !== '\\') { push(c, i); i++; continue; }
    const n = raw[i + 1];
    const simple: Record<string, string> = { n: '\n', t: '\t', r: '\r', b: '\b', f: '\f', v: '\v', '0': '\0' };
    if (n === undefined) { i++; continue; }
    if (n in simple) { push(simple[n], i); i += 2; continue; }
    if (n === 'x') { push(String.fromCharCode(parseInt(raw.slice(i + 2, i + 4), 16)), i); i += 4; continue; }
    if (n === 'u' && raw[i + 2] === '{') {
      const close = raw.indexOf('}', i);
      push(String.fromCodePoint(parseInt(raw.slice(i + 3, close), 16)), i);
      i = close + 1;
      continue;
    }
    if (n === 'u') { push(String.fromCharCode(parseInt(raw.slice(i + 2, i + 6), 16)), i); i += 6; continue; }
    if (n === '\n') { i += 2; continue; }
    push(n, i);
    i += 2;
  }
  return { text, from };
}

// ---------------------------------------------------------------------------
// Pieces of text

/** Text runs of `text` outside tags: [start, end) offsets. */
function textRuns(text: string): [number, number][] {
  const runs: [number, number][] = [];
  let i = 0;
  let runStart = 0;
  while (i < text.length) {
    if (text[i] === '<' && /[A-Za-z/!]/.test(text[i + 1] ?? '')) {
      if (i > runStart) runs.push([runStart, i]);
      let j = i + 1;
      let q: string | null = null;
      while (j < text.length) {
        const ch = text[j];
        if (q) { if (ch === q) q = null; }
        else if (ch === '"' || ch === "'") q = ch;
        else if (ch === '>') break;
        j++;
      }
      i = runStart = j + 1;
      continue;
    }
    i++;
  }
  if (runStart < text.length) runs.push([runStart, text.length]);
  return runs;
}

const HAS_WORD = /[\p{L}]/u;

/** Every piece of text in an App's code that could show on its page. */
export function findPieces(source: string): Piece[] {
  const pieces: Piece[] = [];
  for (const r of regions(source)) {
    if (r.kind === 'html') {
      const text = source.slice(r.start, r.end);
      for (const [a, b] of textRuns(text)) {
        const value = decodeEntities(text.slice(a, b));
        if (value.trim()) pieces.push({ start: r.start + a, end: r.start + b, value, kind: 'html', htmlish: true });
      }
    } else if (r.kind === 'script') {
      for (const s of jsStrings(source, r.start, r.end)) {
        const raw = source.slice(s.start, s.end);
        if (!raw) continue;
        const { text, from } = decodeJs(raw);
        const htmlish = /<[A-Za-z/!]/.test(text) || /&(#?[a-z0-9]+);/i.test(text);
        const runs = htmlish ? textRuns(text) : [[0, text.length] as [number, number]];
        for (const [a, b] of runs) {
          const value = htmlish ? decodeEntities(text.slice(a, b)) : text.slice(a, b);
          if (!value.trim()) continue;
          const start = s.start + from[a];
          const end = b < from.length ? s.start + from[b] : s.end;
          pieces.push({ start, end, value, kind: 'js', quote: s.quote, htmlish });
        }
      }
    }
  }
  return pieces;
}

/** The words people can retype: pieces with a letter in them. A figure, a
 * currency sign or a separator on its own is never "the text" of anything. */
export function editableValues(pieces: Piece[]): string[] {
  return [...new Set(pieces.map((p) => p.value.trim()).filter((v) => v.length >= 2 && HAS_WORD.test(v)))];
}

// ---------------------------------------------------------------------------
// Changing text

export interface Candidate { piece: Piece; value: string }

export type TextPlan =
  | { kind: 'none' }
  | { kind: 'one'; candidate: Candidate }
  | { kind: 'many'; candidates: Candidate[] };

/** Which piece of the code a change to some text on the page belongs to. */
export function planTextEdit(pieces: Piece[], before: string, after: string): TextPlan {
  if (before === after) return { kind: 'none' };
  let p = 0;
  while (p < before.length && p < after.length && before[p] === after[p]) p++;
  let s = 0;
  while (
    s < before.length - p && s < after.length - p
    && before[before.length - 1 - s] === after[after.length - 1 - s]
  ) s++;
  const changeStart = p;
  const changeEnd = before.length - s;

  const found: Candidate[] = [];
  for (const piece of pieces) {
    const v = piece.value;
    if (!HAS_WORD.test(v)) continue;
    let at = before.indexOf(v);
    while (at >= 0) {
      if (at <= changeStart && at + v.length >= changeEnd) {
        const value = after.slice(at, after.length - (before.length - (at + v.length)));
        found.push({ piece, value });
        break;
      }
      at = before.indexOf(v, at + 1);
    }
  }
  if (!found.length) return { kind: 'none' };
  // The longest words that cover the change are the most specific.
  const longest = Math.max(...found.map((c) => c.piece.value.length));
  const best = found.filter((c) => c.piece.value.length === longest);
  return best.length === 1 ? { kind: 'one', candidate: best[0] } : { kind: 'many', candidates: best };
}

function encodeFor(piece: Piece, value: string): string {
  let out = piece.htmlish ? escapeHtml(value) : value;
  if (piece.kind === 'js') {
    out = out.replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/\r/g, '\\r');
    if (piece.quote === '`') out = out.replace(/`/g, '\\`').replace(/\$\{/g, '\\${');
    else out = out.split(piece.quote ?? "'").join(`\\${piece.quote ?? "'"}`);
  }
  return out;
}

/** The source with each candidate's text replaced (later pieces first, so
 * earlier positions hold). */
export function applyText(source: string, edits: Candidate[]): string {
  let out = source;
  for (const { piece, value } of [...edits].sort((a, b) => b.piece.start - a.piece.start)) {
    out = out.slice(0, piece.start) + encodeFor(piece, value) + out.slice(piece.end);
  }
  return out;
}

// Invisible tags for telling candidates apart in a hidden render.
const MARK = '⁣';
const ZERO = '​';
const ONE = '‌';

export function markerFor(n: number): string {
  return MARK + n.toString(2).split('').map((b) => (b === '1' ? ONE : ZERO)).join('') + MARK;
}

/** The source with candidate n's text prefixed by markerFor(n). */
export function markPieces(source: string, candidates: Candidate[]): string {
  const order = candidates.map((c, n) => ({ c, n })).sort((a, b) => b.c.piece.start - a.c.piece.start);
  let out = source;
  for (const { c, n } of order) out = out.slice(0, c.piece.start) + markerFor(n) + out.slice(c.piece.start);
  return out;
}

/** The candidate numbers whose markers appear in `text`. */
export function markersIn(text: string): number[] {
  const out: number[] = [];
  const re = new RegExp(`${MARK}([${ZERO}${ONE}]+)${MARK}`, 'g');
  for (const m of text.matchAll(re)) {
    out.push(parseInt(m[1].split('').map((ch) => (ch === ONE ? '1' : '0')).join(''), 2));
  }
  return out;
}

export function stripMarkers(text: string): string {
  return text.replace(new RegExp(`${MARK}[${ZERO}${ONE}]+${MARK}`, 'g'), '');
}

// ---------------------------------------------------------------------------
// Layout: rows, columns and sections in the markup

interface Element { name: string; start: number; openEnd: number; end: number; attrs: Record<string, string> }

function parseAttrs(tag: string): Record<string, string> {
  const attrs: Record<string, string> = {};
  for (const m of tag.matchAll(/([^\s=/>]+)(?:\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
    if (m.index === 0) continue; // the tag name
    attrs[m[1].toLowerCase()] = m[3] ?? m[4] ?? m[5] ?? '';
  }
  return attrs;
}

/** Elements in the markup carrying `attr`, with their full extent. */
export function elementsWith(source: string, attr: string): Element[] {
  const html = regions(source).filter((r) => r.kind === 'html');
  const tags: { name: string; start: number; end: number; close: boolean; attrs: Record<string, string> }[] = [];
  for (const r of html) {
    const re = /<(\/?)([A-Za-z][A-Za-z0-9-]*)\b([^>]*)>/g;
    re.lastIndex = r.start;
    let m: RegExpExecArray | null;
    const text = source.slice(0, r.end);
    while ((m = re.exec(text)) && m.index < r.end) {
      tags.push({ name: m[2].toLowerCase(), start: m.index, end: m.index + m[0].length, close: m[1] === '/', attrs: m[1] ? {} : parseAttrs(m[0].slice(1, -1)) });
    }
  }
  const out: Element[] = [];
  tags.forEach((t, k) => {
    if (t.close || !(attr in t.attrs)) return;
    let depth = 0;
    for (let j = k + 1; j < tags.length; j++) {
      const u = tags[j];
      if (u.name !== t.name) continue;
      if (!u.close) depth++;
      else if (depth === 0) { out.push({ name: t.name, start: t.start, openEnd: t.end, end: u.end, attrs: t.attrs }); return; }
      else depth--;
    }
  });
  return out;
}

export function blockIds(source: string): string[] {
  return elementsWith(source, 'data-norm-block').map((e) => e.attrs['data-norm-block']);
}

/** The text before `at` on its own line, if only spaces. */
function indentAt(source: string, at: number): string {
  const lineStart = source.lastIndexOf('\n', at - 1) + 1;
  const lead = source.slice(lineStart, at);
  return /^[ \t]*$/.test(lead) ? lead : '';
}

function cut(source: string, el: { start: number; end: number }): { rest: string; text: string } {
  let start = el.start;
  let end = el.end;
  const indent = indentAt(source, start);
  if (indent || source[start - 1] === '\n') {
    start -= indent.length;
    if (source[end] === '\n') end++;
  }
  return { rest: source.slice(0, start) + source.slice(end), text: source.slice(el.start, el.end) };
}

function insertBefore(source: string, at: number, text: string): string {
  const indent = indentAt(source, at);
  if (indent || source[at - 1] === '\n') return source.slice(0, at) + text + '\n' + indent + source.slice(at);
  return source.slice(0, at) + text + source.slice(at);
}

export type Target = { before: string } | { col: number };

/** Move section `id` before another section, or to the end of column `col`
 * (counted across the page in order). Null when either isn't there. */
export function moveBlock(source: string, id: string, target: Target): string | null {
  const el = elementsWith(source, 'data-norm-block').find((e) => e.attrs['data-norm-block'] === id);
  if (!el) return null;
  if ('before' in target && target.before === id) return source;
  const { rest, text } = cut(source, el);
  if ('before' in target) {
    const at = elementsWith(rest, 'data-norm-block').find((e) => e.attrs['data-norm-block'] === target.before);
    return at ? insertBefore(rest, at.start, text) : null;
  }
  const col = elementsWith(rest, 'data-norm-col')[target.col];
  if (!col) return null;
  const closeAt = rest.lastIndexOf('</', col.end - 1);
  const inner = indentAt(rest, closeAt);
  // Inside the column, a step in from its closing tag.
  if (inner || rest[closeAt - 1] === '\n') {
    const lineStart = closeAt - inner.length;
    return rest.slice(0, lineStart) + inner + '  ' + text + '\n' + rest.slice(lineStart);
  }
  return rest.slice(0, closeAt) + text + rest.slice(closeAt);
}

export function removeBlock(source: string, id: string): string | null {
  const el = elementsWith(source, 'data-norm-block').find((e) => e.attrs['data-norm-block'] === id);
  return el ? cut(source, el).rest : null;
}

/** Set column `col`'s share of its row. */
export function setColSpan(source: string, col: number, span: number): string | null {
  const el = elementsWith(source, 'data-norm-col')[col];
  if (!el) return null;
  const open = source.slice(el.start, el.openEnd);
  const next = /\sdata-norm-span\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/.test(open)
    ? open.replace(/(\sdata-norm-span\s*=\s*)("[^"]*"|'[^']*'|[^\s>]+)/, `$1"${span}"`)
    : open.replace(/(\sdata-norm-col\b)/, `$1 data-norm-span="${span}"`);
  return source.slice(0, el.start) + next + source.slice(el.openEnd);
}
