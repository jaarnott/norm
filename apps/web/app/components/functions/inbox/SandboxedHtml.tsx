'use client';

/**
 * HTML Norm didn't write — a customer's email, a mailbox signature, quoted
 * history — shown inside a sandboxed iframe. No `allow-scripts`, so nothing
 * in it can run even if the server's sanitiser missed something, and its CSS
 * can't reach Norm's page. `allow-same-origin` is safe without scripts and
 * lets this component size the frame to its content and fold quoted text.
 * Links open in a new tab.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

const QUOTE_SELECTOR = '.gmail_quote, .norm-quote, blockquote[type="cite"]';

/** A token's value from the page: the frame can't read Norm's CSS variables,
 *  so the few it needs are copied in (with the light-theme values as fallback
 *  for the first render on the server). */
function token(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** A complete srcdoc document with its own CSP: no scripts, no connections,
 *  images allowed (email needs them), no referrer, links in a new tab. */
export function emailDocument(html: string, opts: { foldQuotes?: boolean } = {}): string {
  const csp =
    "default-src 'none'; img-src https: http: data: cid:; style-src 'unsafe-inline'; font-src https: data:; base-uri 'none'; form-action 'none'";
  const fold = opts.foldQuotes ? `html:not(.n-show-quoted) :is(${QUOTE_SELECTOR}){display:none}` : '';
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    `<meta http-equiv="Content-Security-Policy" content="${csp}">` +
    '<meta name="referrer" content="no-referrer"><base target="_blank"><style>' +
    'html,body{margin:0;padding:0;background:transparent}' +
    // flow-root: children's margins stay inside body, so its height is the content's.
    `body{display:flow-root;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;font-size:14px;line-height:1.5;color:${token('--text', '#1a1a1a')};overflow-wrap:anywhere}` +
    `a{color:${token('--accent', '#7d6234')}}img{max-width:100%;height:auto}table{max-width:100%}pre{white-space:pre-wrap}` +
    `blockquote{margin:0 0 0 4px;padding-left:10px;border-left:2px solid ${token('--line', '#e2ddd7')};color:${token('--text-soft', '#5c554d')}}` +
    fold +
    `</style></head><body>${html}</body></html>`
  );
}

export default function SandboxedHtml({
  html,
  title,
  foldQuotes = false,
  minHeight = 20,
}: {
  html: string;
  title: string;
  /** Hide Gmail's quoted history behind "Show quoted text" (as Gmail does). */
  foldQuotes?: boolean;
  minHeight?: number;
}) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(minHeight);
  const [hasQuote, setHasQuote] = useState(false);
  const [showQuoted, setShowQuoted] = useState(false);
  const srcDoc = useMemo(() => emailDocument(html, { foldQuotes }), [html, foldQuotes]);

  const measure = useCallback(() => {
    const body = ref.current?.contentDocument?.body;
    if (!body) return;
    setHeight(Math.max(minHeight, Math.ceil(Math.max(body.scrollHeight, body.getBoundingClientRect().height))));
  }, [minHeight]);

  const onLoad = () => {
    const doc = ref.current?.contentDocument;
    if (doc && foldQuotes) {
      setHasQuote(!!doc.querySelector(QUOTE_SELECTOR));
      doc.documentElement.classList.toggle('n-show-quoted', showQuoted);
    }
    measure();
  };

  // Text re-wraps when the frame's width changes: measure again.
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    let width = el.clientWidth;
    const observer = new ResizeObserver(() => {
      if (el.clientWidth !== width) {
        width = el.clientWidth;
        measure();
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [measure]);

  const toggleQuoted = () => {
    const next = !showQuoted;
    setShowQuoted(next);
    ref.current?.contentDocument?.documentElement.classList.toggle('n-show-quoted', next);
    requestAnimationFrame(measure);
  };

  return (
    <div>
      <iframe
        ref={ref}
        title={title}
        sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="no-referrer"
        srcDoc={srcDoc}
        onLoad={onLoad}
        style={{ display: 'block', width: '100%', height, border: 0 }}
      />
      {hasQuote && (
        <button type="button" className="n-btn n-btn--link" onClick={toggleQuoted} style={{ marginTop: 6, fontSize: 'var(--fs-sm)' }}>
          {showQuoted ? 'Hide quoted text' : 'Show quoted text'}
        </button>
      )}
    </div>
  );
}
