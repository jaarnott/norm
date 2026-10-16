/**
 * Reply-template HTML before it goes into the composer's contentEditable.
 * Templates are stored as written (staff, or Orbit's import), and the editor
 * takes its content with innerHTML — so an `<img onerror>` in a template would
 * otherwise run as whoever inserts it. A small formatting allowlist; links only
 * to web, mail or phone addresses (or a `{{placeholder}}`); no other attributes.
 * DOMParser documents never run scripts or load images. (The server cleans the
 * body again when it is saved or sent.)
 */

const KEEP = new Set(['p', 'br', 'div', 'span', 'b', 'strong', 'i', 'em', 'u', 's', 'a', 'ul', 'ol', 'li', 'h2', 'h3', 'h4', 'blockquote']);
const DROP = new Set(['script', 'style', 'iframe', 'object', 'embed', 'svg', 'math', 'template', 'noscript', 'title', 'head', 'img', 'video', 'audio', 'form', 'input', 'button', 'select', 'textarea', 'link', 'meta', 'base']);
const SAFE_HREF = /^(https?:|mailto:|tel:|\{\{\s*[a-z_]+\s*\}\})/i;

function cleanInto(source: Node, target: Node, doc: Document): void {
  source.childNodes.forEach((node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      target.appendChild(doc.createTextNode(node.textContent || ''));
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return; // comments, processing instructions
    const el = node as Element;
    const tag = el.tagName.toLowerCase();
    if (DROP.has(tag)) return;
    if (!KEEP.has(tag)) {
      cleanInto(el, target, doc); // unknown wrapper (<font>, <section>): keep its content
      return;
    }
    const copy = doc.createElement(tag);
    if (tag === 'a') {
      const href = (el.getAttribute('href') || '').trim();
      if (SAFE_HREF.test(href)) copy.setAttribute('href', href);
    }
    cleanInto(el, copy, doc);
    target.appendChild(copy);
  });
}

export function cleanTemplateHtml(html: string): string {
  if (typeof DOMParser === 'undefined') return '';
  const parsed = new DOMParser().parseFromString(html || '', 'text/html');
  const out = document.implementation.createHTMLDocument('');
  const box = out.createElement('div');
  cleanInto(parsed.body, box, out);
  return box.innerHTML;
}
