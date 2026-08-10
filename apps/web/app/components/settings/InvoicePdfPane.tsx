'use client';

import { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../lib/api';

/**
 * The dojo's invoice-copy pane: renders the sample's PDF (every page,
 * stacked) scaled to the container width, with zoom buttons, wheel zoom
 * toward the cursor, and click-drag panning. Sits side by side with the
 * replica comparison so the admin reads what the INVOICE says next to what
 * was extracted and what Loaded holds.
 *
 * pdf.js renders each page once at 2x the container width (zoom headroom);
 * zoom/pan are pure CSS transforms, so interaction stays cheap. The worker
 * is served from /pdf.worker.min.mjs (copied from pdfjs-dist into public/ —
 * bundler-independent; re-copy it when the dependency is upgraded).
 */

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 6;

export default function InvoicePdfPane({ sampleId }: { sampleId: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const pagesRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [pageCount, setPageCount] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  // Authoritative zoom/pan live in refs — gesture events arrive faster than
  // React re-renders, so handler closures over state go stale mid-gesture
  // (a drag rebased off stale state teleports the page). State only mirrors
  // the refs for rendering.
  const zoomRef = useRef(1);
  const panRef = useRef({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);
  // Active pointers, for gestures: one finger drags, two fingers pinch-zoom.
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ dist: number; midX: number; midY: number } | null>(null);

  const commit = (nz: number, np: { x: number; y: number }) => {
    zoomRef.current = nz;
    panRef.current = np;
    setZoom(nz);
    setPan(np);
  };

  useEffect(() => {
    let cancelled = false;
    setState('loading');
    commit(1, { x: 0, y: 0 });
    (async () => {
      try {
        const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/pdf`);
        if (!res.ok) throw new Error(`pdf fetch ${res.status}`);
        const data = await res.arrayBuffer();
        const pdfjs = await import('pdfjs-dist');
        pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs';
        const doc = await pdfjs.getDocument({ data }).promise;
        if (cancelled) return;
        const host = pagesRef.current;
        const width = containerRef.current?.clientWidth || 600;
        if (!host) return;
        host.innerHTML = '';
        for (let n = 1; n <= doc.numPages; n++) {
          const page = await doc.getPage(n);
          if (cancelled) return;
          const base = page.getViewport({ scale: 1 });
          // 2x the container width for crispness when zoomed in.
          const renderScale = (width * 2) / base.width;
          const viewport = page.getViewport({ scale: renderScale });
          const canvas = document.createElement('canvas');
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.style.width = '100%';
          canvas.style.display = 'block';
          canvas.style.background = '#fff';
          canvas.style.boxShadow = '0 1px 4px rgba(0,0,0,0.15)';
          canvas.style.marginBottom = '8px';
          const ctx = canvas.getContext('2d');
          if (!ctx) continue;
          await page.render({ canvas, canvasContext: ctx, viewport }).promise;
          if (cancelled) return;
          host.appendChild(canvas);
        }
        setPageCount(doc.numPages);
        setState('ready');
      } catch {
        if (!cancelled) setState('error');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sampleId]);

  const clampZoom = (z: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

  // Zoom keeping the given container-relative point stationary; an optional
  // (dx, dy) pans with the pinch midpoint as the hands move.
  const zoomAt = (factor: number, cx: number, cy: number, dx = 0, dy = 0) => {
    const z = zoomRef.current;
    const nz = clampZoom(z * factor);
    const f = nz / z;
    const p = panRef.current;
    commit(nz, { x: cx - (cx - p.x) * f + dx, y: cy - (cy - p.y) * f + dy });
  };

  const zoomCentred = (factor: number) => {
    const el = containerRef.current;
    zoomAt(factor, (el?.clientWidth || 0) / 2, (el?.clientHeight || 0) / 2);
  };

  const onWheel = (e: React.WheelEvent) => {
    // A trackpad PINCH arrives as wheel-with-ctrlKey (and ctrl+wheel means
    // zoom by convention); a plain wheel / two-finger scroll must scroll the
    // page, never zoom the invoice.
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    const rect = containerRef.current?.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0));
  };

  const pinchState = () => {
    const [a, b] = [...pointers.current.values()];
    return {
      dist: Math.hypot(a.x - b.x, a.y - b.y),
      midX: (a.x + b.x) / 2,
      midY: (a.y + b.y) / 2,
    };
  };

  const onPointerDown = (e: React.PointerEvent) => {
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    try {
      (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    } catch {
      // capture is an enhancement (keeps the drag when the finger leaves
      // the pane) — never let it break the gesture
    }
    if (pointers.current.size === 2) {
      drag.current = null;
      pinch.current = pinchState();
    } else if (pointers.current.size === 1) {
      const p = panRef.current;
      drag.current = { x: e.clientX, y: e.clientY, panX: p.x, panY: p.y };
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch.current && pointers.current.size >= 2) {
      const prev = pinch.current;
      const now = pinchState();
      if (prev.dist > 0 && now.dist > 0) {
        const rect = containerRef.current?.getBoundingClientRect();
        zoomAt(
          now.dist / prev.dist,
          prev.midX - (rect?.left || 0),
          prev.midY - (rect?.top || 0),
          now.midX - prev.midX,
          now.midY - prev.midY,
        );
      }
      pinch.current = now;
      return;
    }
    if (!drag.current) return;
    const d = drag.current;
    const np = { x: d.panX + (e.clientX - d.x), y: d.panY + (e.clientY - d.y) };
    panRef.current = np;
    setPan(np);
  };

  const endDrag = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    if (pointers.current.size === 1) {
      // One finger stays down after a pinch: rebase the drag on it so the
      // page doesn't jump.
      const [p] = [...pointers.current.values()];
      const cur = panRef.current;
      drag.current = { x: p.x, y: p.y, panX: cur.x, panY: cur.y };
    } else {
      drag.current = null;
    }
  };

  const btn: React.CSSProperties = {
    fontSize: '0.72rem',
    padding: '2px 10px',
    border: '1px solid #ccc',
    borderRadius: 4,
    background: '#fff',
    color: '#555',
    cursor: 'pointer',
    fontFamily: 'inherit',
  };

  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, background: '#f2f0ec', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '0.4rem 0.6rem', background: '#faf9f7', borderBottom: '1px solid #eee' }}>
        <span style={{ fontSize: '0.66rem', textTransform: 'uppercase', color: '#8a8a8a' }}>
          Invoice copy{pageCount > 1 ? ` · ${pageCount} pages` : ''}
        </span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <button type="button" style={btn} onClick={() => zoomCentred(1 / 1.3)} title="zoom out">−</button>
          <button type="button" style={btn} onClick={() => zoomCentred(1.3)} title="zoom in">+</button>
          <button type="button" style={btn} onClick={() => commit(1, { x: 0, y: 0 })} title="fit to width">
            Fit
          </button>
        </span>
      </div>
      <div
        ref={containerRef}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        style={{
          // No fixed height: the container wraps the pages at fit-width
          // (transforms don't affect layout, so zooming keeps this height
          // and the overflow clips — the box is always exactly the PDF's
          // full-width size).
          minHeight: 120,
          overflow: 'hidden',
          position: 'relative',
          cursor: drag.current ? 'grabbing' : 'grab',
          touchAction: 'none',
        }}
      >
        {state === 'loading' && (
          <div style={{ padding: '2rem', fontSize: '0.74rem', color: '#8a8a8a' }}>Loading the invoice copy…</div>
        )}
        {state === 'error' && (
          <div style={{ padding: '2rem', fontSize: '0.74rem', color: '#a02b2b' }}>Could not load the PDF.</div>
        )}
        <div
          ref={pagesRef}
          style={{
            width: '100%',
            padding: 8,
            boxSizing: 'border-box',
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: '0 0',
          }}
        />
      </div>
    </div>
  );
}
