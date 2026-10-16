'use client';

import { useEffect, useRef, useState, type RefObject } from 'react';

/**
 * The venue's proposal: free HTML + CSS, sanitised by Norm and wrapped in a
 * CSP'd document (sanitize.proposal_document), shown in a sandboxed frame.
 *
 * NEVER `allow-scripts`. Without it, `allow-same-origin` is safe — nothing
 * in the frame can run — and lets this page read the frame's height, so the
 * proposal sits in the page at its full length (no inner scrollbar). Popups
 * are allowed so the proposal's links (target=_blank from the document's
 * <base>) open in a new tab, outside the sandbox. `allow-modals` is inert
 * without scripts — the frame can't open a dialog itself — and lets THIS page
 * call the frame's own `print()` (`printProposal`): the browser then
 * paginates the proposal as a document. Printed as part of the page, a frame
 * is one unbreakable box that Chromium slices mid-line at page breaks.
 *
 * Height: measured on load, re-measured while images arrive (a short poll —
 * a frame without script can't tell us when they do), on resize and before
 * printing.
 */
export default function ProposalFrame({ document: srcDoc, title, frameRef }: {
  document: string;
  title: string;
  /** Receives the frame, for `printProposal`. */
  frameRef?: RefObject<HTMLIFrameElement | null>;
}) {
  const own = useRef<HTMLIFrameElement>(null);
  const ref = frameRef ?? own;
  const [height, setHeight] = useState(720);

  useEffect(() => {
    const frame = ref.current;
    if (!frame) return;
    let poll: number | undefined;

    const measure = () => {
      const doc = frame.contentDocument;
      const html = doc?.documentElement;
      if (!doc || !html) return;
      const body = doc.body;
      let bodyHeight = 0;
      if (body) {
        const cs = window.getComputedStyle(body);
        bodyHeight = body.scrollHeight + (parseFloat(cs.marginTop) || 0) + (parseFloat(cs.marginBottom) || 0);
      }
      const next = Math.ceil(Math.max(html.getBoundingClientRect().height, bodyHeight));
      if (next > 0) setHeight((prev) => (Math.abs(prev - next) > 1 ? next : prev));
    };

    const onLoad = () => {
      measure();
      window.clearInterval(poll);
      let ticks = 0;
      poll = window.setInterval(() => {
        measure();
        if (++ticks >= 24) window.clearInterval(poll);
      }, 250);
    };

    let resizeFrame = 0;
    const onResize = () => {
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(measure);
    };

    frame.addEventListener('load', onLoad);
    window.addEventListener('resize', onResize);
    window.addEventListener('beforeprint', measure);
    if (frame.contentDocument?.readyState === 'complete') onLoad();
    return () => {
      frame.removeEventListener('load', onLoad);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('beforeprint', measure);
      window.clearInterval(poll);
      cancelAnimationFrame(resizeFrame);
    };
  }, [srcDoc, ref]);

  return (
    <iframe
      ref={ref}
      title={title}
      srcDoc={srcDoc}
      sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-modals"
      referrerPolicy="no-referrer"
      className="fp-proposal"
      style={{ display: 'block', width: '100%', height, border: 0, background: 'var(--bg)' }}
    />
  );
}

/**
 * Print the proposal on its own, paginated by the browser (save as PDF from
 * the print dialog). Falls back to printing the page when the frame can't.
 */
export function printProposal(frame: HTMLIFrameElement | null): void {
  const win = frame?.contentWindow;
  if (win) {
    try {
      win.focus();
      win.print();
      return;
    } catch {
      /* fall through to the page */
    }
  }
  window.print();
}
