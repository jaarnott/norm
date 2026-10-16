'use client';

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import Script from 'next/script';

/**
 * /f/<slug> — Norm's hosted web form: the page a venue's iframe embed loads,
 * and a link staff can share on its own.
 *
 * The form itself is drawn by public/embed/forms.js, the same file venue
 * websites load for the inline embed, so the two can't drift apart: one
 * renderer, one set of checks mirroring the server, one submit (a CORS-simple
 * text/plain POST — same-origin here). This page adds what only the hosted
 * page needs:
 *
 * - Framed (on a venue site): a transparent page that tells the parent its
 *   height (`norm-form:height`) so the frame fits, and takes the venue
 *   site's first-touch attribution from the parent (`norm-form:tracking`,
 *   sent by forms.js's iframe mode) — inside the frame it can't read that
 *   site's storage. A plain <iframe> embed gets the page's own attribution.
 * - Standalone: a centred card on the page background.
 */

interface NormFormsApi {
  mount(
    el: HTMLElement,
    slug: string,
    opts: {
      heading?: 'h1' | 'h2';
      tracking?: () => Record<string, string>;
      onLoad?: (schema: { form: { name?: string } } | null) => void;
      onSubmitted?: () => void;
    },
  ): void;
  attribution(): Record<string, string>;
}

const TRACKING_KEYS = ['gclid', 'gbraid', 'wbraid', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'referrer', 'landing_page'];

function isFramed(): boolean {
  try {
    return window.self !== window.top;
  } catch {
    return true; // a cross-origin top that refuses even the comparison
  }
}

/** Only the attribution keys, as short strings — the parent page is any website. */
function cleanTracking(raw: unknown): Record<string, string> | null {
  if (!raw || typeof raw !== 'object') return null;
  const out: Record<string, string> = {};
  for (const key of TRACKING_KEYS) {
    const v = (raw as Record<string, unknown>)[key];
    if (typeof v === 'string' && v) out[key] = v.slice(0, key === 'referrer' || key === 'landing_page' ? 1000 : 500);
  }
  return out;
}

function toParent(message: Record<string, unknown>) {
  // Heights and "ready" say nothing private; any embedding page may hear them.
  if (isFramed()) window.parent.postMessage(message, '*');
}

const noSubscribe = () => () => {};

export default function FormPage({ slug }: { slug: string }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const parentTracking = useRef<Record<string, string> | null>(null);
  const [ready, setReady] = useState(false);
  const [ownPanel, setOwnPanel] = useState(false);
  // Heights go to the parent only once the form is drawn: the loading line's
  // height would collapse the frame, and a cross-origin frame scrolled out of
  // view gets no ResizeObserver callbacks to grow it again.
  const loaded = useRef(false);
  const lastHeight = useRef(0);
  // Server render assumes a frame (transparent, no card): no flash of page
  // colour on a venue's site; a standalone visit settles on hydration.
  const framed = useSyncExternalStore(noSubscribe, isFramed, () => true);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (window.parent === window || e.source !== window.parent) return;
      const data = e.data as { type?: unknown; tracking?: unknown } | null;
      if (data && data.type === 'norm-form:tracking') parentTracking.current = cleanTracking(data.tracking);
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  /** Tell the parent how tall the form is (measured now — a forced layout
   *  works even while the browser throttles an off-screen frame). */
  const postHeight = useCallback(() => {
    const wrap = wrapRef.current;
    if (!loaded.current || !wrap) return;
    const height = Math.ceil(wrap.getBoundingClientRect().height);
    if (height > 0 && height !== lastHeight.current) {
      lastHeight.current = height;
      toParent({ type: 'norm-form:height', slug, height });
    }
  }, [slug]);

  useEffect(() => {
    const api = (window as unknown as { NormForms?: NormFormsApi }).NormForms;
    const host = hostRef.current;
    if (!ready || !api || !host) return;
    api.mount(host, slug, {
      heading: 'h1',
      tracking: () => parentTracking.current ?? api.attribution(),
      onLoad: (schema) => {
        if (schema?.form?.name) document.title = schema.form.name;
        // A themed background paints its own panel (forms.js .nf--filled).
        setOwnPanel(host.classList.contains('nf--filled'));
        loaded.current = true;
        postHeight();
        window.setTimeout(postHeight, 250); // after the panel restyle and fonts
      },
      onSubmitted: () => {
        toParent({ type: 'norm-form:submitted', slug });
        postHeight();
      },
    });
    toParent({ type: 'norm-form:ready', slug });
  }, [ready, slug, postHeight]);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!framed || !wrap || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(postHeight);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, [framed, postHeight]);

  const card = !framed && !ownPanel;
  return (
    <div
      ref={wrapRef}
      style={framed
        ? { padding: '4px 2px 12px', color: 'var(--text)' }
        : { minHeight: '100dvh', padding: '32px 16px 56px', background: 'var(--canvas)', color: 'var(--text)' }}
    >
      {/* The root layout paints the app's cream on body; a framed form must
          show the venue's page through it. */}
      <style>{'html,body{background:transparent!important;height:auto!important}[data-norm-form-ready]~.fp-form-wait{display:none}'}</style>
      <Script src="/embed/forms.js" strategy="afterInteractive" onReady={() => setReady(true)} />
      <div
        style={{
          maxWidth: card ? 688 : 720,
          margin: '0 auto',
          ...(card ? { background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', padding: '28px 24px' } : {}),
        }}
      >
        {/* forms.js owns everything inside the host; React never renders into it. */}
        <div ref={hostRef} style={{ fontSize: 16, margin: '0 auto' }} />
        <p className="fp-form-wait" style={{ margin: 0, color: 'var(--muted)' }}>Loading the form…</p>
        <noscript>
          <p>This form needs JavaScript. Please turn it on, or contact the venue directly.</p>
        </noscript>
      </div>
    </div>
  );
}
