'use client';

/**
 * Runs one user-built app in a sandboxed iframe.
 *
 * The security model, stated once: the iframe gets `sandbox="allow-scripts"`
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
 * Before anything renders, the viewer sees the app's declared reach in the
 * same consent language the Claude connector uses — and if their own
 * permissions cannot satisfy it, they are told which is missing instead of
 * watching every call fail.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch } from '../../lib/api';
import { setPageDocument } from '../../lib/pageDocument';
import AppSharePanel from './AppSharePanel';

interface AppDetail {
  slug: string;
  name: string;
  icon?: string | null;
  description?: string | null;
  access: string;
  write_approved: boolean;
  version: number;
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
        parent.postMessage({ type: 'norm:call', id: id, connector: connector, action: action, params: params || {} }, '*');
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
    storage: {
      list: function (collection, query) { return _store('list', collection, null, query || {}); },
      get: function (collection, id) { return _store('get', collection, id, {}); },
      create: function (collection, data, venueId) { return _store('create', collection, null, { data: data, venueId: venueId }); },
      update: function (collection, id, data, venueId) { return _store('update', collection, id, { data: data, venueId: venueId }); },
      remove: function (collection, id) { return _store('delete', collection, id, {}); },
    },
  };
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

const BASE_CSS = `
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, -apple-system, sans-serif; color: #2a2a2a; background: #fff; }
`;

export default function AppRunner({ slug }: { slug: string }) {
  const [app, setApp] = useState<AppDetail | null>(null);
  const [venues, setVenues] = useState<VenueRow[]>([]);
  const [venueId, setVenueId] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [height, setHeight] = useState(360);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const venueRef = useRef<string>('');
  venueRef.current = venueId;

  // Tell the chat what the user is LOOKING AT: "rename this app" must
  // resolve to this slug without asking. Same publish/clear contract as the
  // RecipeEditor; module-scope, so it survives the send.
  useEffect(() => {
    if (app) {
      setPageDocument({ kind: 'app', slug: app.slug, name: app.name, version: app.version });
    }
    return () => setPageDocument(null);
  }, [app]);

  useEffect(() => {
    let live = true;
    (async () => {
      const [a, v] = await Promise.all([
        apiFetch(`/api/apps/${slug}`).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`Error ${r.status}`)))),
        apiFetch('/api/venues').then((r) => (r.ok ? r.json() : { venues: [] })).catch(() => ({ venues: [] })),
      ]).catch((e) => { if (live) setError(e instanceof Error ? e.message : 'Could not load the app'); return [null, null]; });
      if (!live || !a) return;
      setApp(a as AppDetail);
      const rows: VenueRow[] = ((v?.venues ?? []) as VenueRow[]).map((x) => ({ id: x.id, name: x.name }));
      setVenues(rows);
      if (rows.length && !venueRef.current) setVenueId(rows[0].id);
    })();
    return () => { live = false; };
  }, [slug]);

  // The whole document the sandbox runs: runtime first, then the app's markup.
  const srcdoc = useMemo(() => {
    if (!app?.ui_source) return null;
    return `<!doctype html><html><head><meta charset="utf-8"><style>${BASE_CSS}</style><script>${HOST_RUNTIME}</script></head><body>${app.ui_source}</body></html>`;
  }, [app?.ui_source]);

  const post = useCallback((msg: Record<string, unknown>) => {
    iframeRef.current?.contentWindow?.postMessage(msg, '*');
  }, []);

  useEffect(() => {
    if (!app) return undefined;
    const onMessage = async (ev: MessageEvent) => {
      // Only our own iframe — a message from anywhere else is not ours to answer.
      if (ev.source !== iframeRef.current?.contentWindow) return;
      const m = ev.data || {};
      if (m.type === 'norm:ready') {
        post({
          type: 'norm:init',
          context: {
            app: { slug: app.slug, name: app.name, version: app.version },
            venueId: venueRef.current || null,
            venues,
          },
        });
      } else if (m.type === 'norm:resize' && typeof m.height === 'number') {
        setHeight(Math.min(Math.max(m.height, 120), 4000));
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
              `/api/apps/${app.slug}/files/${encodeURIComponent(String(m.collection))}`,
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
          list: ['POST', `/api/apps/${app.slug}/records/${col}/query`],
          get: ['GET', `/api/apps/${app.slug}/records/${col}/${rid}`],
          create: ['POST', `/api/apps/${app.slug}/records/${col}`],
          update: ['PUT', `/api/apps/${app.slug}/records/${col}/${rid}`],
          delete: ['DELETE', `/api/apps/${app.slug}/records/${col}/${rid}`],
        };
        // File ops share the bridge but not the record paths.
        if (m.op === 'files' || m.op === 'file-delete' || m.op === 'file-url') {
          const fileId = encodeURIComponent(String(m.recordId ?? ''));
          try {
            if (m.op === 'files') {
              const q = m.recordId ? `?record_id=${fileId}` : '';
              const res = await apiFetch(`/api/apps/${app.slug}/files/${encodeURIComponent(String(m.collection))}${q}`);
              const json = await res.json().catch(() => ({}));
              post(res.ok
                ? { type: 'norm:result', id: m.id, ok: true, data: json.files }
                : { type: 'norm:result', id: m.id, ok: false, error: json?.detail || `Error ${res.status}` });
            } else if (m.op === 'file-delete') {
              const res = await apiFetch(`/api/apps/${app.slug}/file/${fileId}`, { method: 'DELETE' });
              post({ type: 'norm:result', id: m.id, ok: res.ok, data: { deleted: m.recordId } });
            } else {
              // Fetched here and handed to the iframe as a data: URL. The
              // sandbox runs on an opaque origin, so it can neither fetch this
              // itself (no token) nor load a parent-origin blob: URL — but a
              // data: URL carries no origin, so an <img src> in the sandbox
              // renders it.
              const res = await apiFetch(`/api/apps/${app.slug}/file/${fileId}`);
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
        try {
          const res = await apiFetch(`/api/apps/${app.slug}/${isCall ? 'call' : 'run'}`, {
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
          post({ type: 'norm:result', id: m.id, ok: true, data: out.data });
        } catch (e) {
          post({ type: 'norm:result', id: m.id, ok: false, error: e instanceof Error ? e.message : 'call failed' });
        }
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [app, venues, post]);

  // Venue changes re-init the app rather than reloading the iframe.
  useEffect(() => {
    if (app && venueId) {
      post({ type: 'norm:init', context: { app: { slug: app.slug, name: app.name, version: app.version }, venueId, venues } });
    }
  }, [venueId, app, venues, post]);

  if (error) return <div style={{ padding: '2rem', color: '#c0392b' }}>✗ {error}</div>;
  if (!app) return <div style={{ padding: '2rem', color: '#888' }}>Loading…</div>;

  if (app.missing_permissions.length > 0) {
    return (
      <div style={{ padding: '2rem', maxWidth: 560 }}>
        <h2 style={{ margin: '0 0 0.5rem' }}>{app.icon} {app.name}</h2>
        <p style={{ color: '#b45309' }}>
          This app needs permissions you don&rsquo;t have:{' '}
          <strong>{app.missing_permissions.join(', ')}</strong>. Ask an administrator
          for access, or for a narrower version of the app.
        </p>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto', padding: '1rem' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 8 }}>
        <h2 style={{ margin: 0, fontSize: '1.1rem' }}>{app.icon} {app.name}</h2>
        <span style={{ fontSize: '0.7rem', color: '#999' }}>v{app.version}</span>
        {venues.length > 1 && (
          <select value={venueId} onChange={(e) => setVenueId(e.target.value)}
            style={{ marginLeft: 'auto', font: 'inherit', fontSize: '0.8rem', padding: '3px 8px' }}>
            {venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
          </select>
        )}
      </div>
      {/* The reach line: always visible, same words as the consent screen. */}
      {app.reach.length > 0 && (
        <div style={{ fontSize: '0.68rem', color: '#8a8a8a', marginBottom: 8 }}>
          {app.reach.map((r) => r.split(' — ')[0]).join(' · ')}
          {(app.spec.writes?.length ?? 0) > 0 && !app.write_approved && (
            <span style={{ color: '#b45309' }}> · writes not approved for you</span>
          )}
        </div>
      )}
      {srcdoc ? (
        <iframe
          ref={iframeRef}
          sandbox="allow-scripts"
          srcDoc={srcdoc}
          title={app.name}
          style={{ width: '100%', height, border: '1px solid #e5e2dc', borderRadius: 10, background: '#fff' }}
        />
      ) : (
        <div style={{ padding: '2rem', color: '#888' }}>This app has no interface yet.</div>
      )}
      {/* Owners see the sharing panel; the endpoint 403s for anyone else and
          the panel renders nothing. */}
      {(app.access === 'owner' || app.access === 'edit') && (
        <AppSharePanel slug={app.slug} />
      )}
    </div>
  );
}
