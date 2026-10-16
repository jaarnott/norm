/**
 * Reply-template bodies are HTML that other staff wrote (or Orbit imported),
 * and the rich-text editor puts them into the page with innerHTML. Unless it
 * is cleaned first, an `<img onerror>` in a template would run as whoever
 * opens Settings — so everything goes through `cleanHtml` before it reaches
 * the editor: a small allowlist of formatting tags, links only to web, mail
 * or phone addresses (or a `{{placeholder}}`), no attributes beyond href.
 *
 * Parsing uses DOMParser, whose documents never run scripts or load images.
 */

const KEEP = new Set([
  'p', 'br', 'div', 'span', 'b', 'strong', 'i', 'em', 'u', 's', 'a',
  'ul', 'ol', 'li', 'h2', 'h3', 'h4', 'blockquote',
]);

/** Removed with everything inside them. */
const DROP = new Set([
  'script', 'style', 'iframe', 'object', 'embed', 'svg', 'math', 'template',
  'noscript', 'title', 'head', 'link', 'meta', 'base', 'form', 'input',
  'button', 'select', 'textarea', 'video', 'audio', 'img', 'picture', 'source',
]);

export type TagAction = 'keep' | 'drop' | 'unwrap';

/** What happens to an element: kept, dropped with its content, or replaced
 *  by its children (an unknown wrapper such as `<font>`). */
export function tagAction(tagName: string): TagAction {
  const t = tagName.toLowerCase();
  if (KEEP.has(t)) return 'keep';
  if (DROP.has(t)) return 'drop';
  return 'unwrap';
}

/** A link target the cleaned HTML may keep, or null. */
export function safeHref(href: string | null | undefined): string | null {
  const v = (href ?? '').trim();
  if (!v) return null;
  if (/^\{\{\s*[a-z_]+\s*\}\}$/.test(v)) return v; // {{quote_link}} and friends
  if (/^(https?:|mailto:|tel:)/i.test(v)) return v;
  return null;
}

function cleanNode(node: Node, doc: Document): void {
  for (const child of Array.from(node.childNodes)) {
    if (child.nodeType === 8) {
      child.remove(); // comments
      continue;
    }
    if (child.nodeType !== 1) continue;
    const el = child as Element;
    const action = tagAction(el.tagName);
    if (action === 'drop') {
      el.remove();
      continue;
    }
    cleanNode(el, doc);
    if (action === 'unwrap') {
      const frag = doc.createDocumentFragment();
      while (el.firstChild) frag.appendChild(el.firstChild);
      el.replaceWith(frag);
      continue;
    }
    const href = el.tagName.toLowerCase() === 'a' ? safeHref(el.getAttribute('href')) : null;
    for (const attr of Array.from(el.attributes)) el.removeAttribute(attr.name);
    if (href) el.setAttribute('href', href);
  }
}

/** The HTML with only safe formatting left. Browser only (DOMParser). */
export function cleanHtml(html: string | null | undefined): string {
  if (!html) return '';
  if (typeof DOMParser === 'undefined') return '';
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
  cleanNode(doc.body, doc);
  return doc.body.innerHTML;
}

/** The text a person would read (for list previews). */
export function plainText(html: string | null | undefined): string {
  return (html ?? '')
    .replace(/<br\s*\/?>/gi, ' ')
    .replace(/<\/(p|div|li|h[1-6])>/gi, ' ')
    .replace(/<[^>]*>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}
