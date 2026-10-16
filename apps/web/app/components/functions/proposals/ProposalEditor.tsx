'use client';

/**
 * An enquiry's proposal (`?page=functions-inbox&view=proposal&enquiry=<id>`):
 * free HTML + CSS, started from the venue's brand template.
 *
 * - Preview: the server's sanitised document (what the customer sees), in a
 *   sandboxed iframe without allow-scripts.
 * - Edit: the same HTML made editable in place — designMode inside an equally
 *   sandboxed frame, so the proposal's CSS never touches Norm's page.
 * - Code: the raw HTML and CSS.
 * Saving sends the HTML back; the server sanitises it and the money check
 * compares its dollar figures with the recorded options. Send freezes a
 * version and emails the quote link (SendProposal). Under the editor: the
 * option panel the customer gets below the proposal, and the sent versions.
 */
import { useEffect, useRef, useState } from 'react';
import { Code2, Eye, ImagePlus, PencilLine, RotateCcw, Save, Send } from 'lucide-react';
import { ConfirmDialog } from '../../ui/Dialog';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import Tabs from '../../ui/Tabs';
import { FnApiError, fnGet, fnSend } from '../shared/api';
import { instantLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { errorText, Notice, ReadOnlyHint, type NoticeTone } from '../inbox/bits';
import { openEnquiry } from '../inbox/nav';
import { RichToolbar } from '../inbox/RichText';
import { agoLabel, decodeEntities } from '../inbox/text';
import type { Mailbox } from '../inbox/types';
import ImageLibrary from './ImageLibrary';
import OfferedOptions from './OfferedOptions';
import { editableDocument, imageTag, spliceText } from './proposalDoc';
import SendProposal from './SendProposal';
import type { EnquiryForProposal, LibraryImage, MoneyCheck, Proposal, ProposalPayload, ProposalSendResult, ProposalVersion } from './types';

type Tab = 'preview' | 'edit' | 'code';
type Working = { proposalId: string; version: number; html: string; css: string };

const FRAME_HEIGHT = 'min(78vh, 1000px)';
const frameStyle = {
  display: 'block',
  width: '100%',
  height: FRAME_HEIGHT,
  border: '1px solid var(--line)',
  borderRadius: 'var(--radius-lg)',
  background: 'var(--bg)',
} as const;
const mono = { fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', lineHeight: 1.5, width: '100%' } as const;

export default function ProposalEditor({
  enquiryId,
  meta,
  mailboxes,
  canWrite,
  now,
}: {
  enquiryId: string;
  meta: FnMeta;
  mailboxes: Mailbox[];
  canWrite: boolean;
  now: Date;
}) {
  const enc = encodeURIComponent(enquiryId);
  const [tick, setTick] = useState(0);
  const key = `${enquiryId}|${tick}`;
  const [loaded, setLoaded] = useState<{ key: string; data?: ProposalPayload; error?: unknown } | null>(null);
  const [enquiry, setEnquiry] = useState<{ id: string; data?: EnquiryForProposal; error?: string } | null>(null);
  const [versions, setVersions] = useState<{ key: string; rows: ProposalVersion[] } | null>(null);
  const [work, setWork] = useState<Working | null>(null);
  const [tab, setTab] = useState<Tab>('preview');
  const [editDoc, setEditDoc] = useState('');
  const [viewing, setViewing] = useState<ProposalVersion | null>(null);
  const [preview, setPreview] = useState<{ key: string; document?: string; error?: string } | null>(null);
  const [liveCheck, setLiveCheck] = useState<{ html: string; check: MoneyCheck } | null>(null);
  const [busy, setBusy] = useState<null | 'save' | 'start' | 'restart'>(null);
  const [notice, setNotice] = useState<{ tone: NoticeTone; text: string; reload?: boolean } | null>(null);
  const [dialog, setDialog] = useState<null | 'images' | 'restart' | 'send'>(null);
  const [startVenue, setStartVenue] = useState('');
  const frame = useRef<HTMLIFrameElement>(null);
  const code = useRef<HTMLTextAreaElement>(null);
  const savedRange = useRef<Range | null>(null);
  const codeSelection = useRef<[number, number] | null>(null);
  const baseRef = useRef<Proposal | null>(null);

  useEffect(() => {
    let alive = true;
    const id = key.slice(0, key.indexOf('|'));
    fnGet<ProposalPayload>(`/enquiries/${encodeURIComponent(id)}/proposal`)
      .then((data) => alive && setLoaded({ key, data }))
      .catch((error) => alive && setLoaded({ key, error }));
    fnGet<{ rows: ProposalVersion[] }>(`/enquiries/${encodeURIComponent(id)}/proposal/versions`)
      .then((r) => alive && setVersions({ key, rows: r.rows }))
      .catch(() => alive && setVersions({ key, rows: [] }));
    return () => {
      alive = false;
    };
  }, [key]);

  useEffect(() => {
    let alive = true;
    fnGet<EnquiryForProposal>(`/enquiries/${encodeURIComponent(enquiryId)}`)
      .then((data) => alive && setEnquiry({ id: enquiryId, data }))
      .catch((err) => alive && setEnquiry({ id: enquiryId, error: errorText(err) }));
    return () => {
      alive = false;
    };
  }, [enquiryId]);

  const data = loaded?.data ?? null;
  const base = data?.proposal ?? null;
  // The working copy follows the saved proposal until someone edits it; a new
  // saved version (save, restart, reload) starts it afresh.
  const working: Working | null =
    work && base && work.proposalId === base.id && work.version === base.version
      ? work
      : base
        ? { proposalId: base.id, version: base.version, html: base.html, css: base.css }
        : null;
  const dirty = !!(working && base && (working.html !== base.html || working.css !== base.css));
  useEffect(() => {
    baseRef.current = base;
  });

  // The preview: the saved proposal, or the sent version being looked at.
  const previewKey = `${enquiryId}|${base?.version ?? ''}|${viewing?.id ?? ''}|${tick}`;
  const hasPreview = !!base || !!viewing;
  useEffect(() => {
    if (!hasPreview) return;
    let alive = true;
    const [id, , versionId] = previewKey.split('|');
    fnGet<{ document: string }>(`/enquiries/${encodeURIComponent(id)}/proposal/preview`, { version_id: versionId || undefined })
      .then((r) => alive && setPreview({ key: previewKey, document: r.document }))
      .catch((err) => alive && setPreview({ key: previewKey, error: errorText(err) }));
    return () => {
      alive = false;
    };
  }, [previewKey, hasPreview]);

  // Unsaved edits get the money check too, a moment after typing stops.
  const uncheckedHtml = dirty && working ? working.html : null;
  useEffect(() => {
    if (uncheckedHtml === null) return;
    let alive = true;
    const t = setTimeout(() => {
      fnSend<MoneyCheck>('POST', `/enquiries/${encodeURIComponent(enquiryId)}/proposal/check`, { html: uncheckedHtml })
        .then((check) => alive && setLiveCheck({ html: uncheckedHtml, check }))
        .catch(() => undefined);
    }, 800);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [uncheckedHtml, enquiryId]);

  const origin = typeof window === 'undefined' ? '' : window.location.origin;
  const venueId = enquiry?.data?.enquiry.venue_id ?? null;
  const ctx = enquiry?.id === enquiryId ? enquiry.data : undefined;

  const edit = (patch: Partial<Pick<Working, 'html' | 'css'>>) => {
    if (!working) return;
    setWork({ ...working, ...patch });
  };

  // From the editable frame (its listener outlives renders, so it reads the
  // saved proposal through a ref and merges with the latest working copy).
  const onEditInput = (html: string) => {
    const b = baseRef.current;
    if (!b) return;
    setWork((w) => ({
      proposalId: b.id,
      version: b.version,
      css: w && w.proposalId === b.id && w.version === b.version ? w.css : b.css,
      html,
    }));
  };

  const changeTab = (next: Tab) => {
    if (next === 'edit' && working) setEditDoc(editableDocument(working.html, working.css, origin));
    if (next !== 'preview') setViewing(null);
    setTab(next);
  };

  const took = (out: ProposalPayload, message: string) => {
    setLoaded({ key, data: out });
    const p = out.proposal;
    if (p) {
      setWork({ proposalId: p.id, version: p.version, html: p.html, css: p.css });
      if (tab === 'edit') setEditDoc(editableDocument(p.html, p.css, origin));
    }
    setNotice({ tone: 'ok', text: message });
  };

  const fail = (err: unknown) =>
    setNotice({ tone: 'error', text: errorText(err), reload: err instanceof FnApiError && err.conflict });

  const save = async (): Promise<boolean> => {
    if (!working || !base) return false;
    setBusy('save');
    setNotice(null);
    try {
      const out = await fnSend<ProposalPayload>('PUT', `/enquiries/${enc}/proposal`, {
        version: base.version,
        html: working.html,
        css: working.css,
      });
      took(out, 'Saved — the preview shows exactly what the customer will see.');
      return true;
    } catch (err) {
      fail(err);
      return false;
    } finally {
      setBusy(null);
    }
  };

  const start = async (restart: boolean) => {
    setBusy(restart ? 'restart' : 'start');
    setNotice(null);
    try {
      const body = restart ? { restart: true, version: base?.version } : venueId ? {} : { venue_id: startVenue || meta.venues[0]?.id };
      const out = await fnSend<ProposalPayload>('POST', `/enquiries/${enc}/proposal`, body);
      took(out, restart ? 'Rebuilt from the venue’s brand template.' : 'Started from the venue’s brand template.');
    } catch (err) {
      fail(err);
      if (restart) throw new Error(errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const openSend = async () => {
    if (dirty && !(await save())) return;
    setDialog('send');
  };

  const openImages = () => {
    if (tab === 'edit') {
      const sel = frame.current?.contentDocument?.getSelection();
      savedRange.current = sel && sel.rangeCount ? sel.getRangeAt(0).cloneRange() : null;
    } else if (code.current) {
      codeSelection.current = [code.current.selectionStart, code.current.selectionEnd];
    }
    setDialog('images');
  };

  const insertImage = (img: LibraryImage) => {
    setDialog(null);
    const tag = imageTag(img.file_id, img.name);
    if (tab === 'edit') {
      const doc = frame.current?.contentDocument;
      if (!doc) return;
      frame.current?.contentWindow?.focus();
      const sel = doc.getSelection();
      if (sel && savedRange.current) {
        sel.removeAllRanges();
        sel.addRange(savedRange.current);
        doc.execCommand('insertHTML', false, tag); // fires the frame's input → onEditInput
      } else {
        doc.body.insertAdjacentHTML('beforeend', tag);
        onEditInput(doc.body.innerHTML);
      }
    } else if (working) {
      const [a, b] = codeSelection.current ?? [working.html.length, working.html.length];
      edit({ html: spliceText(working.html, a, b, tag).text });
    }
  };

  const onFrameLoad = () => {
    const doc = frame.current?.contentDocument;
    if (!doc || !canWrite) return;
    doc.designMode = 'on';
    doc.addEventListener('input', () => onEditInput(doc.body.innerHTML));
  };

  // ── States before there's a proposal to edit ──────────────────────────
  const title = 'Proposal';
  const subject = decodeEntities(ctx?.enquiry.subject) || 'Enquiry';
  const headerMeta = ctx ? [subject, ctx.venue?.name ?? 'Group-wide', ctx.contact?.name].filter(Boolean).join(' · ') : undefined;
  const back = { label: 'Back to enquiry', onClick: () => openEnquiry(enquiryId) };

  if (!data) {
    const err = loaded?.key === key ? loaded.error : null;
    return (
      <div>
        <PageHeader title={title} back={back} meta={headerMeta} />
        {err ? (
          <PageState
            kind="error"
            title={err instanceof FnApiError && err.code === 'not_found' ? 'This enquiry isn’t available' : 'Couldn’t load the proposal'}
            detail={errorText(err)}
          />
        ) : (
          <PageState kind="loading" title="Loading the proposal…" />
        )}
      </div>
    );
  }

  if (!base) {
    return (
      <div>
        <PageHeader title={title} back={back} meta={headerMeta} />
        {notice && (
          <div style={{ marginBottom: 12 }}>
            <Notice tone={notice.tone}>{notice.text}</Notice>
          </div>
        )}
        <PageState
          kind="empty"
          title="No proposal for this enquiry yet"
          detail="Start from the venue’s brand template — cover, options, menus, inclusions, terms and sign-off, filled in from the enquiry. Then edit anything."
          action={
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
              {ctx && !venueId && (
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)' }}>
                  Brand of
                  <select className="n-select" value={startVenue || meta.venues[0]?.id || ''} onChange={(e) => setStartVenue(e.target.value)}>
                    {meta.venues.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <Button variant="primary" onClick={() => start(false)} disabled={!canWrite || busy !== null}>
                {busy === 'start' ? 'Starting…' : 'Start from venue brand template'}
              </Button>
              {!canWrite && <ReadOnlyHint />}
            </div>
          }
        />
        <div style={{ marginTop: 16 }}>
          <OfferedOptions options={data.options} meta={meta} title="Options the proposal will offer" />
        </div>
      </div>
    );
  }

  // ── The editor ────────────────────────────────────────────────────────
  const summary = data.summary;
  const check = dirty && liveCheck && working && liveCheck.html === working.html ? liveCheck.check : data.check;
  const tz = ctx?.venue?.timezone || meta.company.default_timezone;
  const currentPreview = preview?.key === previewKey ? preview : null;
  const versionRows = versions?.key === key ? versions.rows : [];

  return (
    <div>
      <PageHeader
        title={title}
        back={back}
        meta={headerMeta}
        status={
          <>
            {summary.last_sent_at && <Badge tone="info">Sent {agoLabel(summary.last_sent_at, now)}</Badge>}
            {summary.last_viewed_at && <Badge tone="ok">Viewed {agoLabel(summary.last_viewed_at, now)}</Badge>}
          </>
        }
        actions={
          <>
            <Button icon={RotateCcw} onClick={() => setDialog('restart')} disabled={!canWrite || busy !== null}>
              Restart from brand
            </Button>
            <Button icon={Save} onClick={save} disabled={!canWrite || !dirty || busy !== null}>
              {busy === 'save' ? 'Saving…' : 'Save'}
            </Button>
            <Button variant="primary" icon={Send} onClick={openSend} disabled={!canWrite || busy !== null || !ctx}>
              Send
            </Button>
          </>
        }
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {!canWrite && <ReadOnlyHint />}
        {check && check.warnings.length > 0 && (
          <div role="alert" data-testid="fn-money-warning" style={{ padding: '10px 14px', borderRadius: 'var(--radius)', background: 'var(--warn-bg)', color: 'var(--warn)', border: '1px solid var(--warn)' }}>
            <div style={{ fontWeight: 600 }}>Check the figures before sending</div>
            <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 'var(--fs-sm)' }}>
              {check.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
            {dirty && <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)' }}>Checked against your unsaved edits.</div>}
          </div>
        )}
        {check && check.ok && check.stated_cents.length > 0 && (
          <Notice tone="ok">Every dollar figure in the proposal matches a recorded option or menu.</Notice>
        )}
        {summary.brand_changed && (
          <Notice
            tone="info"
            action={
              <Button size="sm" onClick={() => setDialog('restart')} disabled={!canWrite}>
                Restart from brand
              </Button>
            }
          >
            The venue’s brand has changed since this proposal was started. Restart from the brand template to pick it up (your edits are replaced).
          </Notice>
        )}
        {notice && (
          <Notice
            tone={notice.tone}
            action={
              notice.reload ? (
                <Button size="sm" onClick={() => { setNotice(null); setWork(null); setTick((t) => t + 1); }}>
                  Reload
                </Button>
              ) : undefined
            }
          >
            {notice.text}
            {notice.reload && ' Reloading replaces your unsaved edits.'}
          </Notice>
        )}

        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Tabs
              label="Proposal view"
              items={[
                { id: 'preview', label: <TabLabel icon={Eye} text="Preview" /> },
                { id: 'edit', label: <TabLabel icon={PencilLine} text="Edit" /> },
                { id: 'code', label: <TabLabel icon={Code2} text="Code" /> },
              ]}
              value={tab}
              onChange={(id) => changeTab(id as Tab)}
            />
          </div>
          {dirty && (
            <span style={{ paddingBottom: 10 }}>
              <Badge tone="warn">Unsaved changes</Badge>
            </span>
          )}
        </div>

        {tab === 'preview' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {viewing && (
              <Notice
                tone="info"
                action={
                  <Button size="sm" onClick={() => setViewing(null)}>
                    Back to the current proposal
                  </Button>
                }
              >
                Version {viewing.version_number} as it was sent{viewing.sent_at ? ` on ${instantLabel(viewing.sent_at, tz, true)}` : ''} — read-only.
              </Notice>
            )}
            {dirty && !viewing && <Notice tone="warn">The preview shows the saved proposal. Save to see your edits exactly as the customer will.</Notice>}
            {currentPreview?.error ? (
              <PageState kind="error" title="Couldn’t load the preview" detail={currentPreview.error} />
            ) : currentPreview?.document ? (
              <iframe
                title={viewing ? `Proposal version ${viewing.version_number}` : 'Proposal preview'}
                sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
                referrerPolicy="no-referrer"
                srcDoc={currentPreview.document}
                style={frameStyle}
              />
            ) : (
              <PageState kind="loading" title="Loading the preview…" />
            )}
          </div>
        )}

        {tab === 'edit' && (
          <div>
            {canWrite ? (
              <RichToolbar
                doc={() => frame.current?.contentDocument ?? null}
                focus={() => frame.current?.contentWindow?.focus()}
                onImage={openImages}
              >
                <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Click into the proposal and type. Prices here are just text.</span>
              </RichToolbar>
            ) : null}
            <iframe ref={frame} title="Edit the proposal" sandbox="allow-same-origin" srcDoc={editDoc} onLoad={onFrameLoad} style={frameStyle} />
          </div>
        )}

        {tab === 'code' && working && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <label style={{ display: 'block' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span className="n-label" style={{ margin: 0 }}>
                  HTML
                </span>
                {canWrite && (
                  <Button size="sm" variant="quiet" icon={ImagePlus} onClick={openImages}>
                    Insert image
                  </Button>
                )}
              </span>
              <textarea
                ref={code}
                className="n-input"
                rows={20}
                spellCheck={false}
                readOnly={!canWrite}
                value={working.html}
                onChange={(e) => edit({ html: e.target.value })}
                style={mono}
              />
            </label>
            <label style={{ display: 'block' }}>
              <span className="n-label">CSS</span>
              <textarea
                className="n-input"
                rows={10}
                spellCheck={false}
                readOnly={!canWrite}
                value={working.css}
                onChange={(e) => edit({ css: e.target.value })}
                style={mono}
              />
            </label>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
              Saved through Norm’s sanitiser: scripts, forms, event handlers and outside images or fonts are removed; images come from the venue library.
            </div>
          </div>
        )}

        <OfferedOptions
          options={viewing ? viewing.options : data.options}
          meta={meta}
          title={viewing ? `Offered in version ${viewing.version_number}` : 'What the customer sees below the proposal'}
        />

        {versionRows.length > 0 && (
          <section aria-label="Sent versions">
            <h3 style={{ margin: '0 0 8px', fontSize: 'var(--fs-base)', fontWeight: 600 }}>Sent versions</h3>
            <ul className="n-card" style={{ listStyle: 'none', margin: 0, padding: 0, overflow: 'hidden' }}>
              {versionRows.map((v, i) => (
                <li key={v.id} style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', padding: '9px 14px', borderTop: i ? '1px solid var(--line)' : 'none' }}>
                  <span style={{ fontWeight: 600 }}>Version {v.version_number}</span>
                  <span style={{ flex: '1 1 200px', minWidth: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                    {[
                      v.sent_at ? `sent ${instantLabel(v.sent_at, tz, true)}` : 'frozen, not sent',
                      v.sent_by_label,
                      v.options.length ? `${v.options.length} option${v.options.length === 1 ? '' : 's'}` : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                  <Button
                    size="sm"
                    variant={viewing?.id === v.id ? 'secondary' : 'quiet'}
                    onClick={() => {
                      setViewing(v);
                      setTab('preview');
                    }}
                  >
                    {viewing?.id === v.id ? 'Viewing' : 'View'}
                  </Button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>

      {dialog === 'images' && (
        <ImageLibrary venues={meta.venues} venueId={venueId} canWrite={canWrite} onPick={insertImage} onClose={() => setDialog(null)} />
      )}
      {dialog === 'restart' && (
        <ConfirmDialog
          title="Restart from the brand template?"
          confirmLabel="Restart"
          busyLabel="Restarting…"
          danger
          onConfirm={() => start(true)}
          onClose={() => setDialog(null)}
        >
          The proposal is rebuilt from the venue’s brand template with the enquiry’s current details. Your edits are replaced; versions already sent are kept.
        </ConfirmDialog>
      )}
      {dialog === 'send' && ctx && (
        <SendProposal
          enquiry={ctx}
          meta={meta}
          mailboxes={mailboxes}
          canWrite={canWrite}
          onSent={(r: ProposalSendResult) => {
            setDialog(null);
            setNotice({
              tone: 'ok',
              text: r.mode === 'proposal' ? 'Proposal sent — a new version was frozen with the options on offer.' : 'Email sent.',
            });
            setTick((t) => t + 1);
          }}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}

function TabLabel({ icon, text }: { icon: typeof Eye; text: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <Icon icon={icon} size={15} />
      {text}
    </span>
  );
}
