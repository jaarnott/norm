'use client';

/**
 * Runs one user-built app in a sandboxed iframe.
 *
 * The security model, stated once: the iframe gets `sandbox="allow-scripts"`
 * (Norm's own built-in apps also get `allow-modals`, for confirm()/print())
 * and a srcdoc document, so it runs on an opaque origin with NO access to the
 * session token, localStorage, or Norm's DOM. Every byte of data it wants
 * comes back through this component over postMessage, and this component only
 * ever forwards to `/api/apps/{slug}/call` / `/run` — the server-side door
 * that enforces the app's declared allowlist and the viewer's own permissions
 * (services/app_runtime.py). The iframe is containment; the door is the
 * boundary.
 *
 * Bridge protocol (all messages carry `norm:` types, ids are the app's):
 *   iframe → parent  {type:'norm:ready'}
 *                    {type:'norm:call', id, connector, action, params, venueId?}
 *                    {type:'norm:run',  id, params, venueId?}
 *                    {type:'norm:resize', height}
 *                    {type:'norm:store', id, op, collection, recordId?, body}
 *   parent → iframe  {type:'norm:init', context}
 *                    {type:'norm:result', id, ok, data|error}
 *
 * `context.component` / `context.inputs` say WHERE to start: which of the
 * app's declared components, and the inputs it declared (Hiring: job,
 * candidate — ids or names the app resolves itself). They ride every init,
 * venue switches included, so an app honours them on its FIRST init only.
 * See services/app_components.py.
 *
 * Before anything renders, the viewer sees the app's declared reach in the
 * same consent language the Claude connector uses — and if their own
 * permissions cannot satisfy it, they are told which is missing instead of
 * watching every call fail.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ExternalLink, History, Users, PencilLine, Undo2 } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import { announceAppChanged, announceAppSaved, onChange } from '../../lib/liveChanges';
import {
  applyText, editableValues, findPieces, markersIn, markPieces, moveBlock, planTextEdit,
  removeBlock, setColSpan, stripMarkers, type Candidate, type Target,
} from '../../lib/codeEdits';
import { EDIT_CSS, EDIT_RUNTIME } from './editRuntime';
import { setPageDocument } from '../../lib/pageDocument';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import AppSharePanel from './AppSharePanel';
import AppHistory from './AppHistory';
import { HOST_CSS } from './hostStyles';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect from '../ui/VenueSelect';
import Button from '../ui/Button';
import Badge from '../ui/Badge';
import AppIcon from '../ui/AppIcon';

/**
 * Where the app is shown. `page`: a menu page — fills the content area under
 * a page header, like Orders. `embedded`: a card in a conversation — a compact
 * framed header, and either its own height (inline) or the whole pane it sits
 * in (`fill`, the split view). `standalone`: /apps/<slug>, a page of its own.
 */
export type AppVariant = 'page' | 'embedded' | 'standalone';

/** Ask the shell to open this app's menu page (page.tsx listens). */
export const OPEN_APP_PAGE_EVENT = 'norm:open-app-page';

// Figtree for the sandbox: it runs on an opaque origin and cannot load Norm's
// fonts, so the host fetches the file once and hands it over as a data: URL.
// Fails soft — the app falls back to the system font.
let fontFacePromise: Promise<string> | null = null;
function loadFontFace(): Promise<string> {
  if (!fontFacePromise) {
    fontFacePromise = fetch('/fonts/Figtree-latin.woff2')
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('font'))))
      .then((blob) => new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(
          `@font-face { font-family: 'Figtree'; font-style: normal; font-weight: 300 900; font-display: swap; src: url(${String(reader.result)}) format('woff2'); }`,
        );
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(blob);
      }))
      .catch(() => '');
  }
  return fontFacePromise;
}

interface AppDetail {
  slug: string;
  name: string;
  /** app | dashboard; a template is a dashboard everyone uses as it is */
  kind?: 'app' | 'dashboard';
  template?: boolean;
  /** "you", the maker's name, or "Norm" */
  owner?: string;
  icon?: string | null;
  description?: string | null;
  access: string;
  write_approved: boolean;
  version: number;
  /** built into Norm: no author, no versions — `build` is its code's hash */
  builtin?: boolean;
  build?: string | null;
  ui_source?: string | null;
  has_logic: boolean;
  reach: string[];
  missing_permissions: string[];
  spec: { writes?: unknown[] } & Record<string, unknown>;
}

interface VenueRow { id: string; name: string }

/** The script injected ahead of the app's own markup: defines window.norm. */
const HOST_RUNTIME = `
(function () {
  var pending = {};
  var seq = 0;
  var readyCbs = [];
  var ctx = null;
  window.norm = {
    get context() { return ctx; },
    onReady: function (cb) { readyCbs.push(cb); if (ctx) cb(ctx); },
    call: function (connector, action, params) {
      return new Promise(function (resolve, reject) {
        var id = 'c' + (++seq);
        pending[id] = { resolve: resolve, reject: reject };
        var p = params || {};
        // params.venue_id asks for another venue (a by-venue comparison);
        // the server still checks the viewer may see it. It used to be
        // dropped, so every venue's bar showed the picked venue (22 Oct 2026).
        parent.postMessage({ type: 'norm:call', id: id, connector: connector, action: action, params: p, venueId: p.venue_id || null }, '*');
      });
    },
    run: function (params) {
      return new Promise(function (resolve, reject) {
        var id = 'r' + (++seq);
        pending[id] = { resolve: resolve, reject: reject };
        parent.postMessage({ type: 'norm:run', id: id, params: params || {} }, '*');
      });
    },
    resize: function () {
      parent.postMessage({ type: 'norm:resize', height: document.documentElement.scrollHeight }, '*');
    },
    // A section of a page laid out in rows, columns and sections
    // (data-norm-block): fill it with norm.block('sales').innerHTML = ….
    // A section someone removed by hand is a detached element, so code that
    // still fills it carries on.
    block: function (id) {
      return document.querySelector('[data-norm-block="' + String(id).replace(/"/g, '') + '"]') || document.createElement('section');
    },
    files: {
      // The picker and the upload live here so an app never has to hold a
      // token or build a URL — it asks the host, which is the only side that
      // can authenticate.
      pick: function (collection, recordId, accept) {
        return new Promise(function (resolve, reject) {
          var id = 'f' + (++seq);
          pending[id] = { resolve: resolve, reject: reject };
          parent.postMessage({ type: 'norm:file-pick', id: id, collection: collection,
                               recordId: recordId || null, accept: accept || '' }, '*');
        });
      },
      list: function (collection, recordId) { return _store('files', collection, recordId, {}); },
      remove: function (fileId) { return _store('file-delete', null, fileId, {}); },
      // A src for an <img>/<a>. Same-origin to the host, so the browser sends
      // the session cookie-less request through the parent's fetch instead —
      // hence the blob handed back by pick/list rather than a raw URL.
      urlFor: function (fileId) { return _store('file-url', null, fileId, {}); },
    },
    // This person's own choices on this page (colours, % or $, the last
    // period): nobody else's screen changes. Records (storage) are shared.
    mine: {
      get: function (name) { return _mine('get', name); },
      set: function (name, value) { return _mine('set', name, value); },
      all: function () { return _mine('all'); },
    },
    storage: {
      list: function (collection, query) { return _store('list', collection, null, query || {}); },
      get: function (collection, id) { return _store('get', collection, id, {}); },
      create: function (collection, data, venueId) { return _store('create', collection, null, { data: data, venueId: venueId }); },
      update: function (collection, id, data, venueId) { return _store('update', collection, id, { data: data, venueId: venueId }); },
      remove: function (collection, id) { return _store('delete', collection, id, {}); },
    },
  };
  function _mine(op, name, value) {
    return new Promise(function (resolve, reject) {
      var rid = 'm' + (++seq);
      pending[rid] = { resolve: resolve, reject: reject };
      parent.postMessage({ type: 'norm:mine', id: rid, op: op, name: name == null ? null : String(name), value: value === undefined ? null : value }, '*');
    });
  }
  function _store(op, collection, id, body) {
    return new Promise(function (resolve, reject) {
      var rid = 's' + (++seq);
      pending[rid] = { resolve: resolve, reject: reject };
      parent.postMessage({ type: 'norm:store', id: rid, op: op, collection: collection, recordId: id, body: body || {} }, '*');
    });
  }
  window.addEventListener('message', function (ev) {
    var m = ev.data || {};
    if (m.type === 'norm:init') {
      // Fires on EVERY context change (venue switch re-inits), so callbacks
      // stay registered — onReady means "whenever the context (re)loads".
      ctx = m.context || {};
      readyCbs.forEach(function (cb) { try { cb(ctx); } catch (e) { console.error(e); } });
    } else if (m.type === 'norm:result' && pending[m.id]) {
      var p = pending[m.id]; delete pending[m.id];
      if (m.ok) p.resolve(m.data); else p.reject(new Error(m.error || 'call failed'));
    }
  });
  // Height reporting: observe rather than asking apps to remember to call resize().
  var report = function () {
    parent.postMessage({ type: 'norm:resize', height: document.documentElement.scrollHeight }, '*');
  };
  new ResizeObserver(report).observe(document.documentElement);
  window.addEventListener('load', function () { report(); parent.postMessage({ type: 'norm:ready' }, '*'); });
})();
`;

export default function AppRunner({ slug, component, inputs, variant = 'embedded', fill = false, back, extraActions, meta: metaOverride, bottomGap = 0 }: {
  slug: string;
  /** which of the app's declared components to start on */
  component?: string;
  /** that component's inputs — where to start inside it */
  inputs?: Record<string, unknown>;
  variant?: AppVariant;
  /** embedded only: fill the pane it sits in instead of sizing to content */
  fill?: boolean;
  /** a back link above the title (Team, Apps, /apps/<slug>) */
  back?: { label: string; onClick: () => void };
  /** page only: more buttons beside Edit (a Dashboard page's own) */
  extraActions?: React.ReactNode;
  /** page only: the line under the title, instead of "Version N · reach" */
  meta?: string;
  /** page only: room (px) after the content, so the last section scrolls
   * clear of a message box floating over the page (a Dashboard page) */
  bottomGap?: number;
}) {
  const [app, setApp] = useState<AppDetail | null>(null);
  const [venues, setVenues] = useState<VenueRow[]>([]);
  const [venueId, setVenueId] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [height, setHeight] = useState(360);
  const [fontFace, setFontFace] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  // This person's own settings on the page (norm.mine), read once per load.
  const mineRef = useRef<Record<string, unknown> | null>(null);
  // A page remembers the venue it was last looking at, shared with every other
  // page (Orders, Roster…). A card in a conversation keeps its own.
  const persistVenue = variant !== 'embedded';
  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const sharedRef = useRef<string | null>(null);
  sharedRef.current = persistVenue ? sharedVenue : null;
  // Pages fill their area and the iframe scrolls inside it; only an inline
  // card grows with its content.
  const fills = variant !== 'embedded' || fill;
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const venueRef = useRef<string>('');
  venueRef.current = venueId;
  const startRef = useRef<{ component: string | null; inputs: Record<string, unknown> }>({ component: null, inputs: {} });
  startRef.current = { component: component || null, inputs: inputs || {} };

  // Edit mode: the person retypes text and moves sections on the page itself,
  // and the change is written into the app's code (lib/codeEdits). `working`
  // is the code being edited; each change redraws the page from it, with the
  // data fetched once for the whole session, so a redraw is instant. Save
  // keeps it as a new version, which Norm starts from next time.
  const [editing, setEditing] = useState(false);
  const [working, setWorking] = useState<string | null>(null);
  // What the frame was last drawn from. A change already showing on the page
  // (typed text, a section dragged) is written into `working` without a
  // redraw, so the page keeps its place and state; anything else redraws.
  const [drawn, setDrawn] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [changes, setChanges] = useState<string[]>([]);
  const [redraw, setRedraw] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [sections, setSections] = useState(0);
  const [newerSaved, setNewerSaved] = useState(false);
  const editingRef = useRef(false);
  editingRef.current = editing;
  const workingRef = useRef<string | null>(null);
  workingRef.current = working;
  const callCache = useRef(new Map<string, unknown>());
  const scrollRef = useRef(0);
  // A hidden copy of the page, drawn to tell repeated words apart.
  const probeRef = useRef<HTMLIFrameElement>(null);
  const [probeDoc, setProbeDoc] = useState<string | null>(null);
  const probeReply = useRef<((r: { text: string | null; byOcc: string | null }) => void) | null>(null);
  const probeReady = useRef<(() => void) | null>(null);

  // Tell the chat what the user is LOOKING AT: "rename this app" must
  // resolve to this slug without asking. Same publish/clear contract as the
  // RecipeEditor; module-scope, so it survives the send.
  useEffect(() => {
    if (app) {
      setPageDocument({ kind: app.kind === 'dashboard' ? 'dashboard' : 'app', slug: app.slug, name: app.name, version: app.version });
    }
    return () => setPageDocument(null);
  }, [app]);

  useEffect(() => {
    let live = true;
    mineRef.current = null;
    (async () => {
      const [a, v, font] = await Promise.all([
        apiFetch(`/api/apps/${encodeURIComponent(slug)}`).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`Error ${r.status}`)))),
        apiFetch('/api/venues').then((r) => (r.ok ? r.json() : { venues: [] })).catch(() => ({ venues: [] })),
        loadFontFace(),
      ]).catch((e) => { if (live) setError(e instanceof Error ? e.message : 'Could not load the app'); return [null, null, '']; });
      if (!live || !a) return;
      setFontFace(font as string);
      setApp(a as AppDetail);
      const rows: VenueRow[] = ((v?.venues ?? []) as VenueRow[]).map((x) => ({ id: x.id, name: x.name }));
      setVenues(rows);
      if (rows.length && !venueRef.current) {
        const remembered = sharedRef.current;
        setVenueId(remembered && rows.some((r) => r.id === remembered) ? remembered : rows[0].id);
      }
    })();
    return () => { live = false; };
  }, [slug]);

  // Norm saved a new version of this app (asked from this page, or another
  // tab): show it. Until Oct 2026 the page kept the version it opened with,
  // and the person had to leave, refresh and open the app again.
  useEffect(() => onChange('app', ({ slug: saved }) => {
    if (saved !== slug) return;
    // Not under someone's hands: their Save will say a newer version exists.
    if (editingRef.current) { setNewerSaved(true); return; }
    apiFetch(`/api/apps/${encodeURIComponent(slug)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((a) => { if (a) setApp(a as AppDetail); })
      .catch(() => {});
  }), [slug]);

  // The whole document the sandbox runs: runtime first, then the app's markup.
  const docFor = useCallback((source: string) => (
    `<!doctype html><html lang="en" data-norm-variant="${variant}"><head><meta charset="utf-8"><style>${fontFace ?? ''}${HOST_CSS}${EDIT_CSS}${bottomGap > 0 ? `body::after{content:'';display:block;height:${Math.round(bottomGap)}px}` : ''}</style><script>${HOST_RUNTIME}</script><script>${EDIT_RUNTIME}</script></head><body>${source}</body></html>`
  ), [fontFace, variant, bottomGap]);
  const shown = editing && drawn !== null ? drawn : app?.ui_source;
  const srcdoc = useMemo(() => {
    if (!shown || fontFace === null) return null;
    // `redraw` forces a fresh draw when an edit couldn't be made (the page
    // already shows the person's typing, which has to go back).
    return docFor(shown) + (redraw ? `<!--${redraw}-->` : '');
  }, [shown, fontFace, docFor, redraw]);

  const post = useCallback((msg: Record<string, unknown>) => {
    iframeRef.current?.contentWindow?.postMessage(msg, '*');
  }, []);

  useEffect(() => {
    if (!app) return undefined;
    const onMessage = async (ev: MessageEvent) => {
      // Only our own iframes — a message from anywhere else is not ours to answer.
      const fromProbe = !!probeRef.current && ev.source === probeRef.current.contentWindow;
      if (!fromProbe && ev.source !== iframeRef.current?.contentWindow) return;
      const sender = ev.source as Window;
      const post = (msg: Record<string, unknown>) => sender.postMessage(msg, '*');
      const m = ev.data || {};
      if (fromProbe && m.type === 'norm:probe-text') {
        probeReply.current?.({ text: m.text ?? null, byOcc: m.byOcc ?? null });
        return;
      }
      if (fromProbe && m.type === 'norm:store' && !['list', 'get', 'files', 'file-url'].includes(m.op)) {
        post({ type: 'norm:result', id: m.id, ok: false, error: 'not while checking an edit' });
        return;
      }
      if (m.type === 'norm:ready') {
        post({
          type: 'norm:init',
          context: {
            app: { slug: app.slug, name: app.name, version: app.version },
            venueId: venueRef.current || null,
            venues,
            host: { variant },
            ...startRef.current,
          },
        });
        if (fromProbe) probeReady.current?.();
        else if (editingRef.current && workingRef.current !== null) {
          post({ type: 'norm:edit', on: true, values: editableValues(findPieces(workingRef.current)), scroll: scrollRef.current });
        }
      } else if (fromProbe) {
        if (m.type !== 'norm:call' && m.type !== 'norm:store') return;
      }
      if (m.type === 'norm:resize' && typeof m.height === 'number') {
        // A filling iframe scrolls inside itself; only an inline card grows.
        if (!fills) setHeight(Math.min(Math.max(m.height, 120), 4000));
      } else if (m.type === 'norm:file-pick') {
        // The host owns the file dialog: the sandbox has no session and
        // cannot upload on its own.
        const input = document.createElement('input');
        input.type = 'file';
        if (m.accept) input.accept = String(m.accept);
        input.onchange = async () => {
          const chosen = input.files?.[0];
          if (!chosen) { post({ type: 'norm:result', id: m.id, ok: false, error: 'no file chosen' }); return; }
          const form = new FormData();
          form.append('file', chosen);
          if (m.recordId) form.append('record_id', String(m.recordId));
          if (venueRef.current) form.append('venue_id', venueRef.current);
          try {
            const res = await apiFetch(
              `/api/apps/${encodeURIComponent(app.slug)}/files/${encodeURIComponent(String(m.collection))}`,
              { method: 'POST', body: form },
            );
            const json = await res.json().catch(() => ({}));
            post(res.ok
              ? { type: 'norm:result', id: m.id, ok: true, data: json }
              : { type: 'norm:result', id: m.id, ok: false, error: json?.detail || `Error ${res.status}` });
          } catch (e) {
            post({ type: 'norm:result', id: m.id, ok: false, error: e instanceof Error ? e.message : 'upload failed' });
          }
        };
        input.click();
      } else if (m.type === 'norm:mine') {
        // One person's own settings: read once per page load, then kept here.
        try {
          if (!mineRef.current) {
            const res = await apiFetch(`/api/apps/${encodeURIComponent(app.slug)}/mine`);
            mineRef.current = res.ok ? ((await res.json()).data || {}) : {};
          }
          const mine = mineRef.current as Record<string, unknown>;
          if (m.op === 'set' && m.name) {
            const res = await apiFetch(`/api/apps/${encodeURIComponent(app.slug)}/mine`, {
              method: 'PUT', body: JSON.stringify({ data: { [String(m.name)]: m.value ?? null } }),
            });
            const json = await res.json().catch(() => ({}));
            if (!res.ok) { post({ type: 'norm:result', id: m.id, ok: false, error: json?.detail || `Error ${res.status}` }); return; }
            mineRef.current = json.data || {};
            post({ type: 'norm:result', id: m.id, ok: true, data: m.value ?? null });
          } else {
            post({ type: 'norm:result', id: m.id, ok: true, data: m.op === 'all' ? { ...mine } : (mine[String(m.name)] ?? null) });
          }
        } catch (e) {
          post({ type: 'norm:result', id: m.id, ok: false, error: e instanceof Error ? e.message : 'settings call failed' });
        }
      } else if (m.type === 'norm:store') {
        // The app's own rows. Same shape of forwarding as norm:call — the
        // server-side door decides what this app may touch; this side just
        // carries the message and never interprets the collection.
        const q = m.body || {};
        // ENCODED, because these come from untrusted app markup. Interpolated
        // raw, a collection of '../../other-app/records/people' is normalised
        // by fetch into a path against a DIFFERENT app — which the server
        // would then authorize as that app, using this viewer's token, and
        // audit against the innocent app. Encoding keeps it one segment, so
        // the server refuses it as an undeclared collection.
        const col = encodeURIComponent(String(m.collection ?? ''));
        const rid = encodeURIComponent(String(m.recordId ?? ''));
        const paths: Record<string, [string, string]> = {
          list: ['POST', `/api/apps/${encodeURIComponent(app.slug)}/records/${col}/query`],
          get: ['GET', `/api/apps/${encodeURIComponent(app.slug)}/records/${col}/${rid}`],
          create: ['POST', `/api/apps/${encodeURIComponent(app.slug)}/records/${col}`],
          update: ['PUT', `/api/apps/${encodeURIComponent(app.slug)}/records/${col}/${rid}`],
          delete: ['DELETE', `/api/apps/${encodeURIComponent(app.slug)}/records/${col}/${rid}`],
        };
        // File ops share the bridge but not the record paths.
        if (m.op === 'files' || m.op === 'file-delete' || m.op === 'file-url') {
          const fileId = encodeURIComponent(String(m.recordId ?? ''));
          try {
            if (m.op === 'files') {
              const q = m.recordId ? `?record_id=${fileId}` : '';
              const res = await apiFetch(`/api/apps/${encodeURIComponent(app.slug)}/files/${encodeURIComponent(String(m.collection))}${q}`);
              const json = await res.json().catch(() => ({}));
              post(res.ok
                ? { type: 'norm:result', id: m.id, ok: true, data: json.files }
                : { type: 'norm:result', id: m.id, ok: false, error: json?.detail || `Error ${res.status}` });
            } else if (m.op === 'file-delete') {
              const res = await apiFetch(`/api/apps/${encodeURIComponent(app.slug)}/file/${fileId}`, { method: 'DELETE' });
              post({ type: 'norm:result', id: m.id, ok: res.ok, data: { deleted: m.recordId } });
            } else {
              // Fetched here and handed to the iframe as a data: URL. The
              // sandbox runs on an opaque origin, so it can neither fetch this
              // itself (no token) nor load a parent-origin blob: URL — but a
              // data: URL carries no origin, so an <img src> in the sandbox
              // renders it.
              const res = await apiFetch(`/api/apps/${encodeURIComponent(app.slug)}/file/${fileId}`);
              if (!res.ok) { post({ type: 'norm:result', id: m.id, ok: false, error: `Error ${res.status}` }); return; }
              const blob = await res.blob();
              const dataUrl = await new Promise<string>((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(String(reader.result));
                reader.onerror = () => reject(reader.error ?? new Error('file read failed'));
                reader.readAsDataURL(blob);
              });
              post({ type: 'norm:result', id: m.id, ok: true, data: dataUrl });
            }
          } catch (e) {
            post({ type: 'norm:result', id: m.id, ok: false, error: e instanceof Error ? e.message : 'file call failed' });
          }
          return;
        }
        const entry = paths[m.op as string];
        if (!entry) {
          post({ type: 'norm:result', id: m.id, ok: false, error: `unknown storage op ${m.op}` });
          return;
        }
        const [method, path] = entry;
        const venue = q.venueId ?? venueRef.current ?? null;
        const body =
          m.op === 'list'
            ? JSON.stringify({ where: q.where ?? null, venue_id: q.venueId ?? venueRef.current ?? null, include_global: q.includeGlobal !== false, limit: q.limit ?? 200, offset: q.offset ?? 0 })
            : m.op === 'create' || m.op === 'update'
              ? JSON.stringify({ data: q.data ?? {}, venue_id: venue })
              : undefined;
        try {
          const res = await apiFetch(path, { method, ...(body ? { body } : {}) });
          const json = await res.json().catch(() => ({}));
          if (!res.ok) {
            post({ type: 'norm:result', id: m.id, ok: false, error: json?.detail || `Error ${res.status}` });
          } else {
            post({ type: 'norm:result', id: m.id, ok: true, data: m.op === 'list' ? json.records : json });
          }
        } catch (e) {
          post({ type: 'norm:result', id: m.id, ok: false, error: e instanceof Error ? e.message : 'storage call failed' });
        }
      } else if (m.type === 'norm:call' || m.type === 'norm:run') {
        const isCall = m.type === 'norm:call';
        // While editing, each redraw asks for the same figures: fetch them once.
        const key = isCall ? JSON.stringify([m.connector, m.action, m.params, m.venueId || venueRef.current || null]) : '';
        if (isCall && (editingRef.current || fromProbe) && callCache.current.has(key)) {
          post({ type: 'norm:result', id: m.id, ok: true, data: callCache.current.get(key) });
          return;
        }
        try {
          const res = await apiFetch(`/api/apps/${encodeURIComponent(app.slug)}/${isCall ? 'call' : 'run'}`, {
            method: 'POST',
            body: JSON.stringify(
              isCall
                ? { connector: m.connector, action: m.action, params: m.params, venue_id: m.venueId || venueRef.current || null }
                : { params: m.params, venue_id: m.venueId || venueRef.current || null },
            ),
          });
          if (!res.ok) {
            const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
            throw new Error(typeof b.detail === 'string' ? b.detail : `Error ${res.status}`);
          }
          const out = await res.json();
          // Kept always, used only while editing: entering Edit mode redraws
          // from the figures already on screen.
          if (isCall) callCache.current.set(key, out.data);
          post({ type: 'norm:result', id: m.id, ok: true, data: out.data });
        } catch (e) {
          post({ type: 'norm:result', id: m.id, ok: false, error: e instanceof Error ? e.message : 'call failed' });
        }
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [app, venues, post, variant, fills]);

  // Venue changes re-init the app rather than reloading the iframe.
  useEffect(() => {
    if (app && venueId) {
      post({ type: 'norm:init', context: { app: { slug: app.slug, name: app.name, version: app.version }, venueId, venues, host: { variant }, ...startRef.current } });
    }
  }, [venueId, app, venues, post, variant]);

  // ---- Edit mode -------------------------------------------------------

  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const startEditing = () => {
    if (!app?.ui_source) return;
    scrollRef.current = 0;
    setWorking(app.ui_source);
    setDrawn(app.ui_source);
    setHistory([]);
    setChanges([]);
    setNewerSaved(false);
    setEditing(true);
  };
  const stopEditing = () => {
    setEditing(false);
    setWorking(null);
    setDrawn(null);
    setHistory([]);
    setChanges([]);
    setSections(0);
    post({ type: 'norm:edit', on: false });
  };

  // Entering Edit mode doesn't change the page, so the frame isn't redrawn:
  // tell it now. (After each change it is redrawn and told on `norm:ready`.)
  useEffect(() => {
    if (editing && workingRef.current !== null) {
      post({ type: 'norm:edit', on: true, values: editableValues(findPieces(workingRef.current)), scroll: 0 });
    }
  }, [editing, post]);

  /** Keep `next` as the page being edited, described for the history note.
   * `shown`: the page already shows it, so the frame isn't redrawn — it is
   * only told which words it can retype now. Null: the change couldn't be
   * made, and the page is redrawn to drop what it shows. */
  const commit = useCallback((next: string | null, what: string, scroll?: number, shown = false) => {
    const current = workingRef.current;
    if (current === null) return;
    if (typeof scroll === 'number') scrollRef.current = scroll;
    if (next === null || next === current) {
      setDrawn(current);
      setRedraw((n) => n + 1);
      return;
    }
    setHistory((h) => [...h, current]);
    setChanges((c) => [...c, what]);
    setWorking(next);
    if (shown) {
      iframeRef.current?.contentWindow?.postMessage({ type: 'norm:edit-values', values: editableValues(findPieces(next)) }, '*');
    } else {
      setDrawn(next);
    }
  }, []);

  const undo = () => {
    const prev = history[history.length - 1];
    if (prev === undefined) return;
    setHistory(history.slice(0, -1));
    setChanges(changes.slice(0, -1));
    setWorking(prev);
    setDrawn(prev);
  };

  /** Draw a hidden copy with each candidate tagged, and read which tag shows
   * at the text the person changed. Null when it can't tell. */
  const whichCandidate = useCallback(async (
    source: string, candidates: Candidate[], at: { path: number[] | null; occ: number; before: string },
  ): Promise<number | null> => {
    const ready = new Promise<void>((resolve) => { probeReady.current = resolve; });
    setProbeDoc(docFor(markPieces(source, candidates)) + `<!--probe ${Date.now()}-->`);
    const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
    try {
      await Promise.race([ready, wait(6000)]);
      for (let tries = 0; tries < 20; tries++) {
        await wait(250);
        const frame = probeRef.current?.contentWindow;
        if (!frame) continue;
        const reply = await Promise.race([
          new Promise<{ text: string | null; byOcc: string | null }>((resolve) => {
            probeReply.current = resolve;
            frame.postMessage({ type: 'norm:probe-read', path: at.path, occ: at.occ, before: at.before }, '*');
          }),
          wait(800).then(() => ({ text: null, byOcc: null })),
        ]);
        // The node at the same place, if it holds the same words; else the
        // same words, counted down the page.
        const text = reply.text !== null && stripMarkers(reply.text) === at.before ? reply.text : reply.byOcc;
        const found = text ? markersIn(text) : [];
        if (found.length) return found[0];
      }
      return null;
    } finally {
      probeReply.current = null;
      probeReady.current = null;
      setProbeDoc(null);
    }
  }, [docFor]);

  useEffect(() => {
    if (!editing) return undefined;
    const onEdit = async (ev: MessageEvent) => {
      if (ev.source !== iframeRef.current?.contentWindow) return;
      const m = ev.data || {};
      const source = workingRef.current;
      if (source === null) return;
      if (m.type === 'norm:edit-layout') {
        setSections(Number(m.blocks) || 0);
      } else if (m.type === 'norm:edit-hint') {
        setToast(m.kind === 'chart'
          ? 'Charts are drawn from your data. Ask Norm to change a chart.'
          : `“${String(m.text || 'That')}” comes from your data, so it can’t be retyped. Ask Norm to change how it’s shown.`);
      } else if (m.type === 'norm:edit-text') {
        const before = String(m.before ?? '');
        const after = String(m.after ?? '');
        const plan = planTextEdit(findPieces(source), before, after);
        if (plan.kind === 'none') {
          setToast('That part comes from your data, so it can’t be retyped. Ask Norm to change it.');
          commit(null, '', m.scroll);
          return;
        }
        let chosen: Candidate[] = plan.kind === 'one' ? [plan.candidate] : plan.candidates;
        if (plan.kind === 'many') {
          const n = await whichCandidate(source, plan.candidates, { path: m.path ?? null, occ: Number(m.occ ?? 0), before });
          if (n !== null && plan.candidates[n]) chosen = [plan.candidates[n]];
          else setToast(`Changed in ${plan.candidates.length} places: the page uses the same words in each.`);
        }
        const b = before.trim();
        const a = after.trim();
        // One place changed is exactly what the page shows already.
        commit(applyText(source, chosen), a ? `“${b}” → “${a}”` : `removed “${b}”`, m.scroll, chosen.length === 1);
      } else if (m.type === 'norm:move-block') {
        const next = moveBlock(source, String(m.block), m.target as Target);
        if (next === null) setToast('That section couldn’t be moved.');
        commit(next, `moved “${m.block}”`, undefined, true);
      } else if (m.type === 'norm:remove-block') {
        commit(removeBlock(source, String(m.block)), `removed the “${m.block}” section`, undefined, true);
      } else if (m.type === 'norm:col-spans') {
        let next: string | null = source;
        for (const { col, span } of (m.spans || []) as { col: number; span: number }[]) {
          next = next === null ? null : setColSpan(next, Number(col), Math.max(1, Math.min(12, Number(span) || 1)));
        }
        commit(next, 'resized columns', undefined, true);
      }
    };
    window.addEventListener('message', onEdit);
    return () => window.removeEventListener('message', onEdit);
  }, [editing, commit, whichCandidate]);

  // Escape belongs to whatever is under the pointer in the page (a drag, a
  // resize, the text being typed), wherever the keyboard focus happens to be.
  useEffect(() => {
    if (!editing) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') iframeRef.current?.contentWindow?.postMessage({ type: 'norm:escape' }, '*');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [editing]);

  const save = async () => {
    if (!app || working === null || !changes.length) return;
    setSaving(true);
    try {
      const res = await apiFetch(`/api/apps/${encodeURIComponent(app.slug)}/edit`, {
        method: 'POST',
        body: JSON.stringify({ ui_source: working, base_version: app.version, note: `Edited by hand: ${changes.join('; ')}` }),
      });
      if (res.status === 409) {
        setNewerSaved(true);
        setToast('A newer version was saved while you were editing, so this wasn’t saved. Cancel, then make your changes again on the new version.');
        return;
      }
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        setToast(typeof b.detail === 'string' ? b.detail : `Couldn’t save (error ${res.status}).`);
        return;
      }
      const fresh = await apiFetch(`/api/apps/${encodeURIComponent(app.slug)}`).then((r) => (r.ok ? r.json() : null));
      stopEditing();
      if (fresh) setApp(fresh as AppDetail);
      announceAppSaved(app.slug);
      setToast('Saved. Norm will start from this version next time.');
    } finally {
      setSaving(false);
    }
  };

  const changeVenue = (id: string) => {
    setVenueId(id);
    if (persistVenue) setActiveVenue(id);
  };

  const pagePad: React.CSSProperties = { padding: '20px 24px 0' };

  if (error || !app) {
    const state = error
      ? <PageState kind="error" title="This app didn’t load" detail={error} />
      : <PageState kind="loading" title="Opening the app…" />;
    return variant === 'embedded' ? <div className="n-card" style={{ padding: 12 }}>{state}</div> : <div className="n-page">{back && <PageHeader title="" back={back} />}{state}</div>;
  }

  // The reach line is consent text — what the app can read and do — so a
  // page shows all of it, in the same words as the consent screen.
  const writesBlocked = (app.spec.writes?.length ?? 0) > 0 && !app.write_approved;
  const reach = app.reach.map((r) => r.split(' — ')[0]).join(' · ');
  const meta = app.builtin
    ? (app.description || 'Built into Norm')
    : [`Version ${app.version}`, reach].filter(Boolean).join(' · ');
  const canShare = !app.builtin && (app.access === 'owner' || app.access === 'edit');
  // Owners and editors can retype text and move sections on the page itself.
  const canEdit = canShare && variant !== 'embedded' && !!app.ui_source;
  // A dashboard is shared with the whole organisation from its Dashboard
  // page's own buttons, never with named people.
  const showSharing = canShare && app.kind !== 'dashboard';
  const venuePicker = venues.length > 1
    ? <VenueSelect venues={venues} value={venueId} onChange={changeVenue} />
    : null;

  if (app.missing_permissions.length > 0) {
    const body = (
      <PageState
        kind="error"
        title="You don’t have the permissions this app needs"
        detail={<>Missing: <strong>{app.missing_permissions.join(', ')}</strong>. Ask an administrator for access, or for a narrower version of the app.</>}
      />
    );
    return variant === 'embedded'
      ? <div className="n-card" style={{ padding: 12 }}>{body}</div>
      : <div className="n-page"><PageHeader title={app.name} back={back} />{body}</div>;
  }

  const frame = srcdoc ? (
    <iframe
      ref={iframeRef}
      // Norm's own apps may confirm()/prompt()/print(); a user-built app
      // stays scripts-only, so it can't raise dialogs over Norm's chrome.
      sandbox={app.builtin ? 'allow-scripts allow-modals' : 'allow-scripts'}
      srcDoc={srcdoc}
      title={app.name}
      style={fills
        ? { display: 'block', flex: 1, minHeight: 0, width: '100%', border: 'none', background: 'transparent' }
        : { display: 'block', width: '100%', height, border: 'none', background: 'transparent' }}
    />
  ) : app.ui_source ? null : (
    <PageState kind="empty" title="This app has no interface yet." />
  );

  if (variant === 'embedded') {
    // A card in the conversation: name, venue, and the way to the full page.
    return (
      <div className="n-card" style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column', ...(fills ? { height: '100%' } : {}) }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px 8px 14px', borderBottom: '1px solid var(--line)', flexWrap: 'wrap' }}>
          <AppIcon app={app} size="inline" />
          <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{app.name}</span>
          {writesBlocked && <Badge tone="warn">Writes not approved for you</Badge>}
          <span style={{ flex: 1 }} />
          {venuePicker}
          <Button
            size="sm"
            variant="quiet"
            icon={ExternalLink}
            onClick={() => window.dispatchEvent(new CustomEvent(OPEN_APP_PAGE_EVENT, { detail: { slug: app.slug } }))}
          >
            Open page
          </Button>
        </div>
        {frame}
      </div>
    );
  }

  const editMeta = sections > 0
    ? 'Editing · click words to retype them · drag a section by its grip · drag a section’s edge to resize'
    : 'Editing · click words to retype them';
  const editActions = (
    <>
      <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>
        {changes.length ? `${changes.length} change${changes.length === 1 ? '' : 's'}` : 'No changes yet'}
      </span>
      <Button size="sm" variant="quiet" icon={Undo2} disabled={!history.length} onClick={undo}>Undo</Button>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => {
          if (changes.length && !window.confirm(`Discard ${changes.length === 1 ? 'your change' : `your ${changes.length} changes`}?`)) return;
          stopEditing();
        }}
      >
        Cancel
      </Button>
      <Button size="sm" variant="primary" disabled={!changes.length || saving} onClick={save}>{saving ? 'Saving…' : 'Save'}</Button>
    </>
  );

  // page / standalone: the app fills the content area under one page header,
  // like Orders — no box around it, no centred column.
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, position: 'relative' }}>
      <div style={pagePad}>
        <PageHeader
          title={app.name}
          back={back}
          meta={editing ? editMeta : (metaOverride ?? meta)}
          status={writesBlocked ? <Badge tone="warn">Writes not approved for you</Badge> : undefined}
          actions={editing ? editActions : (venuePicker || canShare || extraActions) ? (
            <>
              {venuePicker}
              {extraActions}
              {canEdit && (
                <Button size="sm" variant="secondary" icon={PencilLine} onClick={startEditing}>Edit</Button>
              )}
              {canShare && (
                <Button size="sm" variant="secondary" icon={History} onClick={() => { setHistoryOpen(true); setSharing(false); }}>
                  History
                </Button>
              )}
              {showSharing && (
                <Button size="sm" variant={sharing ? 'quiet' : 'secondary'} icon={Users} aria-expanded={sharing} onClick={() => setSharing((x) => !x)}>
                  Sharing
                </Button>
              )}
            </>
          ) : undefined}
        />
        {editing && newerSaved && (
          <div role="status" style={{ margin: '0 0 12px', padding: '8px 12px', borderRadius: 'var(--radius)', background: 'var(--warn-soft, #fdf3e1)', color: 'var(--text)', fontSize: 'var(--fs-sm)' }}>
            A newer version of this app was saved while you were editing. Saving now won’t replace it: cancel, then edit the new version.
          </div>
        )}
        {/* Owners manage who can run it; the endpoint 403s for anyone else and
            the panel renders nothing. */}
        {!editing && sharing && showSharing && <div style={{ marginBottom: 16 }}><AppSharePanel slug={app.slug} /></div>}
        {!editing && historyOpen && canShare && (
          <AppHistory slug={app.slug} onClose={() => setHistoryOpen(false)} onRestored={() => { setHistoryOpen(false); announceAppChanged(app.slug); }} />
        )}
      </div>
      {frame}
      {probeDoc && (
        <iframe
          ref={probeRef}
          aria-hidden
          tabIndex={-1}
          sandbox="allow-scripts"
          srcDoc={probeDoc}
          title="Checking an edit"
          style={{ position: 'fixed', left: -20000, top: 0, width: iframeRef.current?.clientWidth || 1200, height: 900, visibility: 'hidden', pointerEvents: 'none', border: 'none' }}
        />
      )}
      {toast && (
        <div
          role="status"
          style={{
            position: 'absolute', left: '50%', bottom: 20, transform: 'translateX(-50%)', maxWidth: 'min(560px, calc(100% - 32px))',
            padding: '10px 16px', borderRadius: 999, background: 'var(--text)', color: 'var(--bg)',
            fontSize: 'var(--fs-sm)', boxShadow: '0 6px 24px rgba(0,0,0,.18)', zIndex: 5,
          }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}
