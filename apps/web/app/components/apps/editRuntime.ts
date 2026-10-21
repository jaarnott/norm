/**
 * Edit mode, inside an app's sandbox (Oct 2026).
 *
 * Runs in the app's own iframe beside HOST_RUNTIME and does nothing until the
 * host sends `{type:'norm:edit', on:true, values}`. Then:
 *
 *   - Text: words that come from the app's code (`values`, found by
 *     lib/codeEdits) are outlined on hover. A click turns that piece of text
 *     into an editable box; Enter, or clicking anywhere else, sends
 *     `{type:'norm:edit-text', path, occ, before, after}`, and the host writes
 *     the change into the code. Escape drops it. A figure (not in the code)
 *     says so instead.
 *   - Sections (`[data-norm-block]`): hovering one shows a grip at its corner.
 *     Drag it to move the section (`norm:move-block`); click it for a small
 *     menu that removes it (`norm:remove-block`).
 *   - Widths: over the edge of a section that meets another column the cursor
 *     turns to a resize arrow, as in Figma; dragging it sets the two columns'
 *     widths in twelfths of the row (`norm:col-spans`).
 *   - Dragging a section moves it into place in the page as the pointer goes
 *     (a slot shows where it will land, a tag with its name follows the
 *     pointer, the page scrolls at its top and bottom, Escape puts it back).
 *   - While editing, the app's own buttons and links don't act.
 *
 * Moves, removals and resizes show at once; the host writes them into the code
 * without redrawing. Everything this adds sits in one overlay marked
 * `data-norm-ui`, which paths skip, so a text's path is the same here as in
 * the hidden copy the host renders to tell repeated words apart
 * (`norm:probe-read`).
 */

export const EDIT_CSS = `
html.norm-editing [data-norm-text] { cursor: text; }
html.norm-editing [data-norm-text]:hover { outline: 1.5px dashed var(--norm-accent); outline-offset: 2px; border-radius: 3px; }
html.norm-editing [data-norm-data] { cursor: default; }
html.norm-editing a, html.norm-editing button, html.norm-editing select, html.norm-editing input, html.norm-editing label { cursor: default; }
html.norm-editing [data-norm-text] { cursor: text; }
[data-norm-editing] { outline: 2px solid var(--norm-accent); outline-offset: 2px; border-radius: 3px; background: var(--norm-accent-soft); caret-color: var(--norm-accent-strong); white-space: pre-wrap; }
/* Outlines appear at once on hover and never fade in when Edit is pressed;
   the transition is pinned so an app's own card transition can't fade them. */
html.norm-editing [data-norm-block] { outline: 1px dashed transparent; outline-offset: 5px; border-radius: var(--norm-radius-lg); transition: opacity .15s; }
html.norm-editing [data-norm-block].norm-hover { outline-color: var(--norm-line-strong); }
html.norm-editing [data-norm-block].norm-dragging { opacity: .35; }
html.norm-editing [data-norm-col] { min-height: 48px; }
html.norm-editing body { padding-top: 40px; }
html.norm-grabbing [data-norm-text]:hover, html.norm-resizing [data-norm-text]:hover { outline: none; }
.norm-flash { animation: norm-flash .6s ease; }
@keyframes norm-flash { 0%, 100% { background: transparent; } 30% { background: #fde8e8; } }

#norm-edit-ui { position: absolute; left: 0; top: 0; width: 0; height: 0; z-index: 2147483000; font: 500 12px/1.2 'Figtree', system-ui, sans-serif; }
#norm-edit-ui > * { position: absolute; }

/* The grip: a small square on a section's corner, only while hovering it. */
#norm-edit-ui .norm-grip { width: 22px; height: 22px; box-sizing: border-box; display: flex; align-items: center; justify-content: center;
  background: var(--norm-bg); border: 1px solid var(--norm-line-strong); border-radius: 6px; color: var(--norm-muted);
  box-shadow: 0 1px 3px rgba(0,0,0,.08); cursor: grab; opacity: 0; pointer-events: none; transition: opacity .12s, color .12s, border-color .12s; }
#norm-edit-ui .norm-grip.norm-on { opacity: 1; pointer-events: auto; }
#norm-edit-ui .norm-grip:hover { color: var(--norm-accent-strong); border-color: var(--norm-accent); }
#norm-edit-ui .norm-grip svg { width: 12px; height: 12px; display: block; }

/* Its menu, on a click without a drag. */
#norm-edit-ui .norm-menu { background: var(--norm-bg); border: 1px solid var(--norm-line); border-radius: 8px; padding: 4px; box-shadow: 0 6px 20px rgba(0,0,0,.12); }
#norm-edit-ui .norm-menu button { all: unset; box-sizing: border-box; display: block; padding: 6px 10px; border-radius: 5px; cursor: pointer; color: var(--norm-text); white-space: nowrap; }
#norm-edit-ui .norm-menu button:hover { background: var(--norm-surface-alt); color: var(--norm-error); }

/* Resizing from a section's edge (as in Figma): the cursor says so on hover,
   and the widths show while dragging. */
#norm-edit-ui .norm-size { padding: 3px 8px; border-radius: 999px; background: var(--norm-text); color: var(--norm-bg); font-weight: 600; white-space: nowrap; pointer-events: none; }
html.norm-edge-hover, html.norm-edge-hover * { cursor: col-resize !important; }

/* Dragging a section: it moves into place live, shown as a slot; a tag with
   its name follows the pointer. */
html.norm-editing [data-norm-block].norm-slot { outline: 2px dashed var(--norm-accent) !important; outline-offset: 4px; background: var(--norm-accent-soft) !important; }
html.norm-editing [data-norm-block].norm-slot > * { opacity: .25; }
#norm-edit-ui .norm-tag { display: flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: 8px; background: var(--norm-bg); color: var(--norm-text);
  border: 1px solid var(--norm-accent); box-shadow: 0 8px 24px rgba(0,0,0,.16); font-weight: 600; white-space: nowrap; pointer-events: none; max-width: 260px; overflow: hidden; text-overflow: ellipsis; }
#norm-edit-ui .norm-tag svg { width: 12px; height: 12px; color: var(--norm-accent); flex: none; }
#norm-edit-ui .norm-warn { padding: 2px 7px; border-radius: 999px; background: #fff4e0; border: 1px solid #f0c27a; color: #8a5a10; font-weight: 600; font-size: 11px; cursor: help; }
html.norm-grabbing, html.norm-grabbing * { cursor: grabbing !important; user-select: none; }
html.norm-resizing, html.norm-resizing * { cursor: col-resize !important; user-select: none; }
`;

export const EDIT_RUNTIME = `
(function () {
  var on = false, values = [], ui = null, observer = null, queued = false, listening = false;
  var scrollTo = null, scrollUntil = 0, drag = null, resize = null, menu = null, hovered = null;
  var active = null; // the text box being typed in: { box, finish }
  var MARKS = /\\u2063[\\u200b\\u200c]+\\u2063/g;
  var GRIP = '<svg viewBox="0 0 12 12" aria-hidden="true"><g fill="currentColor"><circle cx="4" cy="2.5" r="1.1"/><circle cx="8" cy="2.5" r="1.1"/><circle cx="4" cy="6" r="1.1"/><circle cx="8" cy="6" r="1.1"/><circle cx="4" cy="9.5" r="1.1"/><circle cx="8" cy="9.5" r="1.1"/></g></svg>';
  function post(m) { parent.postMessage(m, '*'); }
  function isUi(n) { return n && n.nodeType === 1 && n.hasAttribute('data-norm-ui'); }
  function inUi(n) { var e = n && (n.nodeType === 1 ? n : n.parentElement); return !!(e && e.closest('[data-norm-ui]')); }
  function inBox(n) { var e = n && (n.nodeType === 1 ? n : n.parentElement); return !!(e && e.closest('[data-norm-editing]')); }

  // A node's place under <body>, not counting the edit overlay.
  function pathOf(node) {
    var path = [];
    while (node && node !== document.body) {
      var parentNode = node.parentNode;
      if (!parentNode) return null;
      var k = 0;
      for (var c = parentNode.firstChild; c && c !== node; c = c.nextSibling) if (!isUi(c)) k++;
      path.unshift(k);
      node = parentNode;
    }
    return node === document.body ? path : null;
  }
  function nodeAt(path) {
    var node = document.body;
    for (var i = 0; i < path.length; i++) {
      var c = node.firstChild, k = 0;
      while (c) { if (!isUi(c)) { if (k === path[i]) break; k++; } c = c.nextSibling; }
      if (!c) return null;
      node = c;
    }
    return node;
  }
  function texts() {
    var out = [], walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), n;
    while ((n = walker.nextNode())) {
      var p = n.parentElement;
      if (!p || !n.nodeValue.trim() || p.closest('script,style,[data-norm-ui]')) continue;
      out.push(n);
    }
    return out;
  }
  function occurrence(node) {
    var all = texts(), k = 0;
    for (var i = 0; i < all.length; i++) {
      if (all[i] === node) return k;
      if (all[i].nodeValue === node.nodeValue) k++;
    }
    return -1;
  }
  function fromCode(text) {
    for (var i = 0; i < values.length; i++) if (text.indexOf(values[i]) >= 0) return true;
    return false;
  }

  function mark() {
    var old = document.querySelectorAll('[data-norm-text],[data-norm-data]');
    for (var i = 0; i < old.length; i++) { old[i].removeAttribute('data-norm-text'); old[i].removeAttribute('data-norm-data'); }
    texts().forEach(function (n) {
      var p = n.parentElement;
      if (p.closest('svg') || p.closest('[data-norm-editing]')) return;
      if (fromCode(n.nodeValue)) { p.removeAttribute('data-norm-data'); p.setAttribute('data-norm-text', ''); }
      else if (!p.hasAttribute('data-norm-text')) p.setAttribute('data-norm-data', '');
    });
  }

  // ---- the overlay: grips, resize bars, the drop line
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
  function place(node, left, top) { node.style.left = Math.round(left) + 'px'; node.style.top = Math.round(top) + 'px'; }
  function cols() { return Array.prototype.slice.call(document.querySelectorAll('[data-norm-col]')); }
  function blocks() { return Array.prototype.slice.call(document.querySelectorAll('[data-norm-block]')); }
  function rowCols(row) { return Array.prototype.filter.call(row.children, function (c) { return c.hasAttribute('data-norm-col'); }); }
  function spanOf(col) { var s = parseInt(col.getAttribute('data-norm-span') || '1', 10); return s >= 1 && s <= 12 ? s : 1; }
  function closeMenu() { if (menu && menu.parentNode) menu.parentNode.removeChild(menu); menu = null; }

  function overlay() {
    if (!ui) return;
    closeMenu();
    while (ui.firstChild) ui.removeChild(ui.firstChild);
    var sx = window.scrollX, sy = window.scrollY;
    blocks().forEach(function (b) {
      var r = b.getBoundingClientRect(), id = b.getAttribute('data-norm-block');
      var grip = el('div', 'norm-grip');
      grip.innerHTML = GRIP;
      grip.title = 'Drag to move · click for more';
      grip.setAttribute('data-for', id);
      grip.addEventListener('pointerdown', function (e) { startDrag(e, b, id, grip); });
      ui.appendChild(grip);
      // On the top edge, a little in from the corner: clear of the gap where
      // the column resize bar sits.
      place(grip, r.left + sx + 12, r.top + sy - 11);
      // Narrower than what it holds (a wide table spills out): say so.
      if (b.scrollWidth > b.clientWidth + 2) {
        var warn = el('div', 'norm-warn', '\\u26a0 Too narrow');
        warn.title = 'This section is narrower than what it holds. Widen its column, or ask Norm to fit it.';
        ui.appendChild(warn);
        place(warn, r.right + sx - warn.offsetWidth - 8, r.top + sy - 10);
      }
    });
    showGrip(hovered);
  }

  // Only the hovered section's grip shows.
  function showGrip(block) {
    if (hovered && hovered !== block) hovered.classList.remove('norm-hover');
    hovered = block;
    if (block) block.classList.add('norm-hover');
    var id = block && block.getAttribute('data-norm-block');
    if (!ui) return;
    Array.prototype.forEach.call(ui.querySelectorAll('.norm-grip'), function (g) {
      g.classList.toggle('norm-on', !!id && g.getAttribute('data-for') === id);
    });
  }
  var hoverEdge = null;
  document.addEventListener('mousemove', function (e) {
    if (!on || drag || resize) return;
    var t = e.target;
    hoverEdge = active || menu ? null : edgeAt(e.clientX, e.clientY);
    showEdge(hoverEdge);
    if (inUi(t)) {
      var g = t.closest && t.closest('.norm-grip');
      if (g) showGrip(document.querySelector('[data-norm-block="' + g.getAttribute('data-for') + '"]'));
      return;
    }
    showGrip(t && t.closest ? t.closest('[data-norm-block]') : null);
  }, true);

  // ---- moving a section: it moves into place in the page as you drag
  function headingOf(block) {
    var h = block.querySelector('h1,h2,h3,h4,h5,h6,[class*="title"],th');
    var t = (h ? h.textContent : block.textContent || '').replace(/\\s+/g, ' ').trim();
    return t ? t.slice(0, 48) : block.getAttribute('data-norm-block');
  }
  function columnOf(block) { return block.parentElement && block.parentElement.closest('[data-norm-col]'); }
  function startDrag(e, block, id, grip) {
    e.preventDefault();
    e.stopPropagation();
    commitText();
    closeMenu();
    try { window.focus(); } catch (_) { /* focus stays where it was */ }
    drag = { block: block, id: id, grip: grip, x: e.clientX, y: e.clientY, moved: false, tag: null,
             home: { parent: block.parentNode, next: block.nextSibling }, scroll: null };
    window.addEventListener('pointermove', moveDrag);
    window.addEventListener('pointerup', endDrag, { once: true });
    window.addEventListener('keydown', escDrag, true);
  }
  function nearestColumn(x, y) {
    var best = null, bestD = Infinity;
    cols().forEach(function (c) {
      var r = c.getBoundingClientRect();
      var dx = x < r.left ? r.left - x : x > r.right ? x - r.right : 0;
      var dy = y < r.top ? r.top - y : y > r.bottom ? y - r.bottom : 0;
      if (dx + dy < bestD) { bestD = dx + dy; best = c; }
    });
    return bestD < 160 ? best : null;
  }
  function autoScroll(y) {
    var edge = 56, step = y < edge ? -Math.ceil((edge - y) / 4) : y > window.innerHeight - edge ? Math.ceil((y - window.innerHeight + edge) / 4) : 0;
    if (drag) drag.scrollStep = step;
    if (step && drag && !drag.scroll) {
      drag.scroll = setInterval(function () {
        if (!drag || !drag.scrollStep) { if (drag) { clearInterval(drag.scroll); drag.scroll = null; } return; }
        window.scrollBy(0, drag.scrollStep);
        placeTag();
      }, 16);
    }
  }
  function placeTag() {
    if (drag && drag.tag) place(drag.tag, drag.px + window.scrollX + 14, drag.py + window.scrollY + 14);
  }
  function moveDrag(e) {
    if (!drag) return;
    drag.px = e.clientX; drag.py = e.clientY;
    if (!drag.moved) {
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) < 4) return;
      drag.moved = true;
      drag.block.classList.add('norm-slot');
      document.documentElement.classList.add('norm-grabbing');
      Array.prototype.forEach.call(ui.querySelectorAll('.norm-grip,.norm-warn'), function (g) { g.style.display = 'none'; });
      drag.tag = el('div', 'norm-tag');
      drag.tag.innerHTML = GRIP;
      drag.tag.appendChild(document.createTextNode(headingOf(drag.block)));
      ui.appendChild(drag.tag);
    }
    placeTag();
    autoScroll(e.clientY);
    var under = document.elementFromPoint(e.clientX, e.clientY);
    if (!under || drag.block.contains(under)) return;
    var over = under.closest('[data-norm-block]');
    if (over && over !== drag.block) {
      var r = over.getBoundingClientRect(), upper = e.clientY < r.top + r.height / 2;
      var sameCol = columnOf(over) === columnOf(drag.block);
      var after = sameCol && (drag.block.compareDocumentPosition(over) & Node.DOCUMENT_POSITION_FOLLOWING);
      // In its own column it swaps past a section once the pointer crosses
      // that section's middle; elsewhere, the half it's in decides.
      if (sameCol) {
        if (after && !upper) over.parentNode.insertBefore(drag.block, over.nextSibling);
        else if (!after && upper) over.parentNode.insertBefore(drag.block, over);
      } else {
        over.parentNode.insertBefore(drag.block, upper ? over : over.nextSibling);
      }
      return;
    }
    var col = under.closest('[data-norm-col]') || nearestColumn(e.clientX, e.clientY);
    if (!col) return;
    var inCol = Array.prototype.filter.call(col.children, function (c) { return c.hasAttribute('data-norm-block') && c !== drag.block; });
    var last = inCol[inCol.length - 1];
    if (!last || e.clientY > last.getBoundingClientRect().bottom) {
      if (drag.block.parentNode !== col || drag.block !== col.lastElementChild) col.appendChild(drag.block);
    } else if (e.clientY < inCol[0].getBoundingClientRect().top && drag.block.nextElementSibling !== inCol[0]) {
      col.insertBefore(drag.block, inCol[0]);
    }
  }
  function stopDragging() {
    window.removeEventListener('pointermove', moveDrag);
    window.removeEventListener('keydown', escDrag, true);
    document.documentElement.classList.remove('norm-grabbing');
    var d = drag; drag = null;
    if (!d) return null;
    if (d.scroll) clearInterval(d.scroll);
    d.block.classList.remove('norm-slot');
    if (d.tag && d.tag.parentNode) d.tag.parentNode.removeChild(d.tag);
    return d;
  }
  function escDrag(k) {
    if (k.key !== 'Escape') return;
    if (cancelAll()) { k.preventDefault(); k.stopPropagation(); }
  }
  // Escape: put back a section being dragged, a resize in progress, close
  // the menu, or drop the text being typed. True when there was something.
  function cancelAll() {
    if (drag) {
      window.removeEventListener('pointerup', endDrag);
      var d = stopDragging();
      d.home.parent.insertBefore(d.block, d.home.next && d.home.next.parentNode === d.home.parent ? d.home.next : null);
      soon();
      return true;
    }
    if (resize) {
      window.removeEventListener('pointerup', endResize);
      window.removeEventListener('pointermove', moveResize);
      document.documentElement.classList.remove('norm-resizing');
      var r = resize; resize = null;
      r.list.forEach(function (c, k) {
        if (r.start[k] === null) c.removeAttribute('data-norm-span'); else c.setAttribute('data-norm-span', r.start[k]);
      });
      if (r.label.parentNode) r.label.parentNode.removeChild(r.label);
      showEdge(null);
      soon();
      return true;
    }
    if (menu) { closeMenu(); return true; }
    if (active) { active.finish(false); return true; }
    return false;
  }
  function endDrag() {
    var d = stopDragging();
    if (!d) return;
    if (!d.moved) { openMenu(d.grip, d.id); return; }
    soon();
    if (d.block.parentNode === d.home.parent && d.block.nextSibling === d.home.next) return; // where it was
    var next = d.block.nextElementSibling;
    while (next && !next.hasAttribute('data-norm-block')) next = next.nextElementSibling;
    var col = columnOf(d.block);
    var t = next ? { before: next.getAttribute('data-norm-block') } : { col: cols().indexOf(col) };
    post({ type: 'norm:move-block', block: d.id, target: t });
  }
  function openMenu(grip, id) {
    closeMenu();
    menu = el('div', 'norm-menu');
    var remove = el('button', '', 'Remove section');
    remove.onclick = function () {
      var b = document.querySelector('[data-norm-block="' + id + '"]');
      closeMenu();
      if (b && b.parentNode) b.parentNode.removeChild(b);
      post({ type: 'norm:remove-block', block: id });
    };
    menu.appendChild(remove);
    ui.appendChild(menu);
    place(menu, parseFloat(grip.style.left), parseFloat(grip.style.top) + 26);
  }

  // ---- resizing from a section's edge, in twelfths of the row
  function sameLine(a, b) { return Math.abs(a.getBoundingClientRect().top - b.getBoundingClientRect().top) < 4; }
  // The edge of a section under the pointer that another column shares.
  function edgeAt(x, y) {
    var list = blocks();
    for (var k = 0; k < list.length; k++) {
      var b = list[k], r = b.getBoundingClientRect();
      if (y < r.top - 2 || y > r.bottom + 2) continue;
      var col = columnOf(b);
      if (!col) continue;
      var row = col.parentElement, cs = rowCols(row), i = cs.indexOf(col);
      if (Math.abs(x - r.right) <= 6 && i < cs.length - 1 && sameLine(cs[i], cs[i + 1])) return { block: b, side: 'right', row: row, i: i };
      if (Math.abs(x - r.left) <= 6 && i > 0 && sameLine(cs[i - 1], cs[i])) return { block: b, side: 'left', row: row, i: i - 1 };
    }
    return null;
  }
  function showEdge(edge) {
    document.documentElement.classList.toggle('norm-edge-hover', !!edge);
  }
  function shareWords(units) { return Math.round(units / 12 * 100) + '%'; }
  function startResize(e, edge) {
    var list = rowCols(edge.row), i = edge.i, total = 0;
    list.forEach(function (c) { total += spanOf(c); });
    var units = list.map(function (c) { return Math.max(1, Math.round(spanOf(c) * 12 / total)); });
    units[units.length - 1] += 12 - units.reduce(function (a, b) { return a + b; }, 0);
    resize = { edge: edge, list: list, i: i, units: units, pair: units[i] + units[i + 1], changed: false,
               start: list.map(function (c) { return c.getAttribute('data-norm-span'); }),
               x0: list[i].getBoundingClientRect().left, x1: list[i + 1].getBoundingClientRect().right, label: el('div', 'norm-size') };
    ui.appendChild(resize.label);
    document.documentElement.classList.add('norm-resizing');
    Array.prototype.forEach.call(ui.querySelectorAll('.norm-grip,.norm-warn'), function (g) { g.style.display = 'none'; });
    window.addEventListener('pointermove', moveResize);
    window.addEventListener('pointerup', endResize, { once: true });
    window.addEventListener('keydown', escDrag, true);
    moveResize(e);
  }
  function moveResize(e) {
    if (!resize) return;
    var r = resize, frac = (e.clientX - r.x0) / Math.max(1, r.x1 - r.x0);
    var left = Math.max(1, Math.min(r.pair - 1, Math.round(frac * r.pair)));
    if (left !== r.units[r.i]) {
      r.units[r.i] = left;
      r.units[r.i + 1] = r.pair - left;
      r.changed = true;
      r.list.forEach(function (c, k) { c.setAttribute('data-norm-span', String(r.units[k])); });
    }
    showEdge(r.edge);
    r.label.textContent = shareWords(r.units[r.i]) + '  |  ' + shareWords(r.units[r.i + 1]);
    // Above the section, on its edge: never over what's being resized.
    var er = r.edge.block.getBoundingClientRect(), ex = r.edge.side === 'right' ? er.right : er.left;
    var above = er.top - 32 >= 4;
    place(r.label, ex + window.scrollX - r.label.offsetWidth / 2, (above ? er.top - 32 : er.bottom + 8) + window.scrollY);
  }
  function endResize() {
    window.removeEventListener('pointermove', moveResize);
    window.removeEventListener('keydown', escDrag, true);
    document.documentElement.classList.remove('norm-resizing');
    if (!resize) return;
    var r = resize; resize = null;
    if (r.label.parentNode) r.label.parentNode.removeChild(r.label);
    showEdge(null);
    soon();
    if (!r.changed) return;
    var all = cols();
    post({ type: 'norm:col-spans', spans: r.list.map(function (c, k) { return { col: all.indexOf(c), span: r.units[k] }; }) });
  }

  // ---- retyping text
  function caretAt(x, y) {
    if (document.caretRangeFromPoint) { var r = document.caretRangeFromPoint(x, y); return r && { node: r.startContainer, offset: r.startOffset }; }
    if (document.caretPositionFromPoint) { var p = document.caretPositionFromPoint(x, y); return p && { node: p.offsetNode, offset: p.offset }; }
    return null;
  }
  function flash(node) { node.classList.remove('norm-flash'); void node.offsetWidth; node.classList.add('norm-flash'); }
  function commitText() { if (active) active.finish(true); }
  function startText(e) {
    var at = caretAt(e.clientX, e.clientY);
    var node = at && at.node;
    if (!node || node.nodeType !== 3 || !node.nodeValue.trim()) return;
    // The browser finds the nearest text even for a click on empty space:
    // edit only when the pointer is on the words themselves.
    var hit = document.createRange();
    hit.selectNodeContents(node);
    var rects = hit.getClientRects(), over = false;
    for (var i = 0; i < rects.length; i++) {
      var q = rects[i];
      if (e.clientX >= q.left - 3 && e.clientX <= q.right + 3 && e.clientY >= q.top - 3 && e.clientY <= q.bottom + 3) { over = true; break; }
    }
    if (!over) return;
    var p = node.parentElement;
    if (!p || p.closest('[data-norm-editing]')) return;
    if (p.closest('svg')) { post({ type: 'norm:edit-hint', kind: 'chart' }); return; }
    var before = node.nodeValue;
    if (!fromCode(before)) { flash(p); post({ type: 'norm:edit-hint', kind: 'data', text: before.trim().slice(0, 60) }); return; }
    var path = pathOf(node), occ = occurrence(node);
    var box = document.createElement('span');
    box.setAttribute('data-norm-editing', '');
    box.textContent = before;
    box.setAttribute('contenteditable', 'plaintext-only');
    if (box.contentEditable !== 'plaintext-only') box.setAttribute('contenteditable', 'true');
    box.spellcheck = true;
    node.parentNode.replaceChild(box, node);
    box.focus();
    var range = document.createRange(), sel = window.getSelection();
    range.setStart(box.firstChild || box, Math.min(at.offset, before.length));
    range.collapse(true);
    sel.removeAllRanges(); sel.addRange(range);
    var done = false;
    function finish(keep) {
      if (done) return;
      done = true;
      if (active && active.box === box) active = null;
      var after = keep ? (box.textContent || '').replace(/[\\r\\n]+/g, ' ') : before;
      var t = document.createTextNode(after);
      if (box.parentNode) box.parentNode.replaceChild(t, box);
      if (keep && after !== before) post({ type: 'norm:edit-text', path: path, occ: occ, before: before, after: after, scroll: window.scrollY });
    }
    active = { box: box, finish: finish };
    box.addEventListener('keydown', function (k) {
      k.stopPropagation();
      if (k.key === 'Enter') { k.preventDefault(); finish(true); }
      else if (k.key === 'Escape') { k.preventDefault(); finish(false); }
    });
    box.addEventListener('blur', function () { finish(true); });
  }

  // While editing, a press anywhere else first keeps what was typed (the
  // page's own handling is held back, so the box would never lose focus by
  // itself), and clicks edit instead of doing what the app does.
  var skipClick = false;
  function onDown(e) {
    if (!on || inUi(e.target)) return;
    if (inBox(e.target)) return;
    commitText();
    if (menu) closeMenu();
    e.preventDefault();
    var edge = edgeAt(e.clientX, e.clientY);
    if (edge) { skipClick = true; startResize(e, edge); }
  }
  function onClick(e) {
    if (!on || inUi(e.target) || inBox(e.target)) return;
    e.preventDefault();
    e.stopPropagation();
    if (skipClick) { skipClick = false; return; }
    startText(e);
  }
  document.addEventListener('mousedown', onDown, true);
  document.addEventListener('click', onClick, true);
  ['dblclick', 'submit', 'change', 'input'].forEach(function (t) {
    document.addEventListener(t, function (e) { if (on && !inBox(e.target)) { if (t === 'submit') e.preventDefault(); e.stopPropagation(); } }, true);
  });

  function refresh() {
    queued = false;
    if (!on) return;
    observer && observer.disconnect();
    mark();
    if (!drag && !resize) overlay();
    if (scrollTo !== null && Date.now() < scrollUntil && document.documentElement.scrollHeight >= scrollTo + window.innerHeight * 0.5) { window.scrollTo(0, scrollTo); scrollTo = null; }
    observer && observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  function soon() { if (!queued) { queued = true; setTimeout(refresh, 60); } }

  window.addEventListener('message', function (ev) {
    var m = ev.data || {};
    if (m.type === 'norm:edit') {
      on = !!m.on;
      values = (m.values || []).filter(function (v) { return v && v.length >= 2; });
      document.documentElement.classList.toggle('norm-editing', on);
      if (on) {
        if (!ui) { ui = document.createElement('div'); ui.id = 'norm-edit-ui'; ui.setAttribute('data-norm-ui', ''); document.body.appendChild(ui); }
        if (typeof m.scroll === 'number' && m.scroll > 0) { scrollTo = m.scroll; scrollUntil = Date.now() + 4000; }
        if (!observer) observer = new MutationObserver(function (list) {
          for (var i = 0; i < list.length; i++) if (!inUi(list[i].target)) { soon(); return; }
        });
        if (!listening) { listening = true; window.addEventListener('resize', soon); }
        refresh();
        post({ type: 'norm:edit-layout', blocks: blocks().length, cols: cols().length });
      } else {
        commitText();
        observer && observer.disconnect();
        showGrip(null);
        if (ui) { ui.parentNode && ui.parentNode.removeChild(ui); ui = null; }
        var marked = document.querySelectorAll('[data-norm-text],[data-norm-data]');
        for (var i = 0; i < marked.length; i++) { marked[i].removeAttribute('data-norm-text'); marked[i].removeAttribute('data-norm-data'); }
      }
    } else if (m.type === 'norm:escape') {
      cancelAll();
    } else if (m.type === 'norm:edit-values') {
      // The code changed under an edit shown in place: its words are what
      // can be retyped next.
      values = (m.values || []).filter(function (v) { return v && v.length >= 2; });
      soon();
    } else if (m.type === 'norm:probe-read') {
      var node = m.path ? nodeAt(m.path) : null;
      var byOcc = null, k = 0, all = texts();
      for (var i = 0; i < all.length; i++) {
        if (all[i].nodeValue.replace(MARKS, '') === m.before) { if (k === m.occ) { byOcc = all[i].nodeValue; break; } k++; }
      }
      post({ type: 'norm:probe-text', text: node && node.nodeType === 3 ? node.nodeValue : null, byOcc: byOcc });
    }
  });
})();
`;
