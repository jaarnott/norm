import { describe, it, expect } from 'vitest';
import {
  applyText, blockIds, editableValues, findPieces, markersIn, markPieces, moveBlock,
  planTextEdit, removeBlock, setColSpan, stripMarkers,
} from './codeEdits';

const APP = `<style>.k{color:red}</style>
<h1 class="t">Lott Cafe &amp; Bar</h1>
<div id="body"></div>
<script>
  var esc = function (s) { return String(s).replace(/'/g, '&#39;').replace(/"/g, '&quot;'); };
  function head() { return '<tr><th>&nbsp;</th><th>Total</th><th>Food</th></tr>'; }
  function render(w, sales) {
    var h = '<div class="kpi"><div class="lbl">Sales (ex GST)</div><div class="val">' + money(sales) + '</div></div>';
    h += '<p class="win">Ex GST · ' + w + '</p>';
    h += row('Sales', [money(sales)]) + row('Sales', [money(sales)]);
    h += \`<span title="x">Wages &amp; hours for \${w}</span>\`;
    document.getElementById('body').innerHTML = h;
  }
  // 'Not text' in a comment
  var note = "Coffee's on Food";
</script>`;

const pieces = findPieces(APP);
const values = pieces.map((p) => p.value);

describe('finding the text in an App', () => {
  it('finds markup text, strings, text inside tags in strings, and template parts', () => {
    expect(values).toContain('Lott Cafe & Bar');
    expect(values).toContain('Total');
    expect(values).toContain('Sales (ex GST)');
    expect(values).toContain('Ex GST · ');
    expect(values).toContain('Wages & hours for ');
    expect(values).toContain("Coffee's on Food");
  });

  it('skips styles, comments, regexes and tag attributes', () => {
    expect(values.join('|')).not.toContain('color:red');
    expect(values).not.toContain('Not text');
    expect(values.some((v) => v.includes('class='))).toBe(false);
  });

  it('offers only words for editing, never a lone symbol', () => {
    const editable = editableValues(pieces);
    expect(editable).toContain('Total');
    expect(editable).not.toContain(' · ');
  });
});

describe('carrying an edit on the page back into the code', () => {
  it('rewrites the one string a header came from', () => {
    const plan = planTextEdit(pieces, 'Total', 'All');
    expect(plan.kind).toBe('one');
    if (plan.kind !== 'one') return;
    const out = applyText(APP, [plan.candidate]);
    expect(out).toContain('<th>All</th>');
    expect(out).not.toContain('<th>Total</th>');
  });

  it('edits the fixed part of text that also shows live data', () => {
    const plan = planTextEdit(pieces, 'Ex GST · Mon 12 Oct – Sun 18 Oct', 'Excl. GST · Mon 12 Oct – Sun 18 Oct');
    expect(plan.kind).toBe('one');
    if (plan.kind !== 'one') return;
    expect(applyText(APP, [plan.candidate])).toContain("'<p class=\"win\">Excl. GST · '");
  });

  it('refuses a figure: it is not in the code', () => {
    expect(planTextEdit(pieces, '$42,319', '$50,000').kind).toBe('none');
  });

  it('escapes what it writes back: entities in markup strings, quotes in a string', () => {
    const amp = planTextEdit(pieces, 'Lott Cafe & Bar', 'Lott Cafe & Kitchen');
    expect(amp.kind).toBe('one');
    if (amp.kind === 'one') expect(applyText(APP, [amp.candidate])).toContain('Lott Cafe &amp; Kitchen');
    const q = planTextEdit(pieces, "Coffee's on Food", "Coffee's under Beverage");
    expect(q.kind).toBe('one');
    if (q.kind === 'one') {
      const out = applyText(APP, [q.candidate]);
      expect(out).toContain('"Coffee\'s under Beverage"');
      // the result is still a script that parses
      const script = out.slice(out.indexOf('<script>') + 8, out.indexOf('</script>'));
      expect(() => new Function(script)).not.toThrow();
    }
  });

  it('edits text inside a template string', () => {
    const plan = planTextEdit(pieces, 'Wages & hours for last week', 'Wages for last week');
    expect(plan.kind).toBe('one');
    if (plan.kind === 'one') expect(applyText(APP, [plan.candidate])).toContain('<span title="x">Wages for ${w}</span>');
  });

  it('says when the same words come from several places, and tells them apart', () => {
    const plan = planTextEdit(pieces, 'Sales', 'Net sales');
    expect(plan.kind).toBe('many');
    if (plan.kind !== 'many') return;
    expect(plan.candidates).toHaveLength(2);
    const marked = markPieces(APP, plan.candidates);
    const second = marked.slice(marked.indexOf("row('") + 5);
    expect(markersIn(second.slice(second.indexOf("row('") + 5))).toEqual([1]);
    expect(stripMarkers(marked)).toBe(APP);
  });
});

const LAYOUT = `<div data-norm-row>
  <div data-norm-col data-norm-span="2">
    <section data-norm-block="sales"></section>
    <section data-norm-block="sales-chart"></section>
  </div>
  <div data-norm-col>
    <section data-norm-block="wages"></section>
  </div>
</div>
<script>norm.block('sales').innerHTML = '<h2>Sales</h2>';</script>`;

describe('moving sections by hand', () => {
  it('lists the sections in page order', () => {
    expect(blockIds(LAYOUT)).toEqual(['sales', 'sales-chart', 'wages']);
  });

  it('moves a section before another, in another column', () => {
    const out = moveBlock(LAYOUT, 'sales-chart', { before: 'wages' });
    expect(out && blockIds(out)).toEqual(['sales', 'sales-chart', 'wages']);
    expect(out).toContain('<div data-norm-col>\n    <section data-norm-block="sales-chart"></section>\n    <section data-norm-block="wages">');
  });

  it('moves a section to the end of a column, and keeps the indenting', () => {
    const out = moveBlock(LAYOUT, 'sales', { col: 1 });
    expect(out && blockIds(out)).toEqual(['sales-chart', 'wages', 'sales']);
    expect(out).toContain('    <section data-norm-block="wages"></section>\n    <section data-norm-block="sales"></section>\n  </div>');
  });

  it('removes a section, and sets a column’s share of its row', () => {
    expect(blockIds(removeBlock(LAYOUT, 'wages') ?? '')).toEqual(['sales', 'sales-chart']);
    expect(setColSpan(LAYOUT, 1, 3)).toContain('<div data-norm-col data-norm-span="3">');
    expect(setColSpan(LAYOUT, 0, 1)).toContain('<div data-norm-col data-norm-span="1">');
  });

  it('never mistakes a section named in the script for one in the markup', () => {
    expect(moveBlock(LAYOUT, 'nowhere', { col: 0 })).toBeNull();
  });
});
