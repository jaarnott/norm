'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { ChevronDown, ChevronRight, CornerDownRight, Download, Info, TriangleAlert, X } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';
import DojoSampleView, { type DojoDiff, type ExtractionDoc } from './DojoSampleView';

/**
 * The SENSEI's proposal: rationale + spec text + candidate
 * verification. Apply = write the spec AND baseline the sensei's ground
 * truth; green means every dojo check held. Shared by the per-spec sample
 * view and the Dojo triage page.
 */

export interface DojoAnalysis {
  status: string;
  green?: boolean;
  rationale?: string;
  layout_facts?: string[];
  proposed_instructions?: string;
  // Same layout as an existing spec: Apply adds this supplier as an alias on
  // that spec (and moves the sample there) instead of keeping a duplicate.
  alias_of?: string | null;
  // Roster hygiene. A spec is named for a BUSINESS and shared by every venue,
  // but rows are auto-created from whatever one account typed — so the sensei
  // can propose the canonical name, and can report aliases sitting on a spec
  // that names a different business (which silently routes that supplier's
  // invoices through the wrong prompt).
  canonical_name?: string | null;
  wrong_aliases?: { spec_id: string; spec: string; alias: string }[];
  // Self-training: a green new-supplier proposal (no existing prompt text)
  // was applied automatically by the analysis itself.
  auto_applied?: boolean;
  applied_at?: string;
  // The current prompts already read this document correctly — the proposal
  // is values-only by design, never a spec rewrite.
  spec_not_needed?: boolean;
  // The sensei's corrected values fail the document's own arithmetic
  // (qty × price ≠ line total etc.) — the proposal can never be green.
  ground_truth_violations?: string[];
  // The correction conversation: admin replies the sensei has folded in.
  thread?: { role: string; text: string; at?: string }[];
  error?: string;
  ground_truth?: ExtractionDoc | null;
  candidate_results?: {
    own?: { status?: string; diffs?: DojoDiff[]; extraction?: ExtractionDoc | null };
    siblings?: { samples?: { id: string; label: string; status: string; diffs?: DojoDiff[]; extraction?: ExtractionDoc | null }[]; passed?: number; failed?: number; errors?: number; new?: number };
  };
  model?: string;
  at?: string;
}

// Dojo check status → badge tone, as on the Supplier specs list. "new" (no
// baseline yet) is for an admin to settle, so it reads as "needs your input".
const STATUS_TONES: Record<string, BadgeTone> = {
  pass: 'ok',
  fail: 'error',
  error: 'error',
  new: 'accent',
};

function StatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONES[status] || STATUS_TONES.new;
  return <Badge tone={tone}>{status.charAt(0).toUpperCase() + status.slice(1)}</Badge>;
}

// The label over one block of the card (the dojo's "Last run" style).
const labelStyle: React.CSSProperties = { display: 'block', marginBottom: 4, fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-soft)' };

// A note inside the card: the status colour on its own tint.
const NOTICE_ICONS = { error: TriangleAlert, info: Info, ok: Info } as const;
function Notice({ tone, children }: { tone: keyof typeof NOTICE_ICONS; children: ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 10, padding: '8px 12px', borderRadius: 'var(--radius)', background: `var(--${tone}-bg)`, color: `var(--${tone})`, fontSize: 'var(--fs-sm)' }}>
      <Icon icon={NOTICE_ICONS[tone]} size={16} style={{ marginTop: 1 }} />
      <div style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{children}</div>
    </div>
  );
}

export default function SenseiProposalCard({
  sampleId,
  analysis: a,
  analysing,
  applying,
  onReanalyse,
  onApply,
  onDismiss,
  onClose,
}: {
  sampleId: string;
  analysis: DojoAnalysis;
  analysing?: boolean;
  applying?: boolean;
  onReanalyse: (feedback: string) => void;
  onApply: () => void;
  onDismiss: () => void;
  onClose: () => void;
}) {
  const [feedback, setFeedback] = useState('');
  // The CURRENT prompts' extraction (the sample's last run) and the baseline
  // STORED on the sample today — both from the last-run endpoint. Shown as
  // extra toggles in the verify table so stored/before/proposed/corrected
  // sit side by side.
  const [currentRun, setCurrentRun] = useState<{ extraction: ExtractionDoc | null; diffs: DojoDiff[]; expected: ExtractionDoc | null } | null>(null);
  // Expanded sibling from the verification sweep: which row is open, and the
  // sibling's STORED baseline (fetched on expand — the candidate extraction
  // and its diffs already travel in the analysis payload). Cache per sample
  // id so re-expanding costs nothing.
  const [openSibling, setOpenSibling] = useState<string | null>(null);
  const [siblingBaselines, setSiblingBaselines] = useState<Record<string, ExtractionDoc | null>>({});
  const toggleSibling = async (id: string) => {
    if (openSibling === id) { setOpenSibling(null); return; }
    setOpenSibling(id);
    if (!(id in siblingBaselines)) {
      try {
        const res = await apiFetch(`/api/supplier-invoice-specs/samples/${id}/last-run`);
        const data = res.ok ? await res.json() : {};
        setSiblingBaselines((m) => ({ ...m, [id]: (data.expected as ExtractionDoc) ?? null }));
      } catch {
        setSiblingBaselines((m) => ({ ...m, [id]: null }));
      }
    }
  };
  // The PDF fetch carries the auth header, so a plain <a href> can't serve
  // it — fetch to a blob and hand the browser a download.
  const downloadSiblingPdf = async (id: string, label: string) => {
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${id}/pdf`);
      if (!res.ok) return;
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = label || 'invoice.pdf';
      link.click();
      URL.revokeObjectURL(url);
    } catch { /* button simply does nothing on a network error */ }
  };
  useEffect(() => {
    let cancelled = false;
    setCurrentRun(null);
    (async () => {
      try {
        const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/last-run`);
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setCurrentRun({ extraction: data.extraction ?? null, diffs: data.diffs ?? [], expected: data.expected ?? null });
      } catch { /* toggle simply stays hidden */ }
    })();
    return () => { cancelled = true; };
  }, [sampleId, a.at]);
  const own = a.candidate_results?.own;
  const sib = a.candidate_results?.siblings;
  return (
    <div className="n-card" style={{ padding: '12px 16px 14px', lineHeight: 1.45, color: 'var(--text)' }}>
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <strong style={{ fontSize: 'var(--fs-base)', fontWeight: 600 }}>Sensei proposal</strong>
        <StatusBadge status={a.status === 'ready' ? 'pass' : a.status === 'failed' ? 'error' : 'new'} />
        {a.status === 'applied' && a.auto_applied && (
          <Badge tone="ok" title="a green sensei proposal for a brand-new supplier (no existing prompt) — applied automatically">
            Auto-applied
          </Badge>
        )}
        {a.model && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{a.model}</span>}
        <IconButton icon={X} label="Close" iconSize={16} onClick={onClose} style={{ margin: '-6px -8px -6px auto' }} />
      </div>
      {a.error && <div style={{ marginBottom: 8, fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{a.error}</div>}
      {a.rationale && (
        <div style={{ marginBottom: 10, fontSize: 'var(--fs-base)', color: 'var(--text)', whiteSpace: 'pre-wrap' }}>{a.rationale}</div>
      )}
      {(a.ground_truth_violations?.length ?? 0) > 0 && (
        <Notice tone="error">
          The sensei&rsquo;s corrected values fail the document&rsquo;s own arithmetic — treat this proposal with suspicion:
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {a.ground_truth_violations!.map((v, i) => <li key={i}>{v}</li>)}
          </ul>
        </Notice>
      )}
      {(a.layout_facts?.length ?? 0) > 0 && (
        <ul style={{ margin: '0 0 10px', paddingLeft: 18, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          {a.layout_facts!.map((f, i) => <li key={i}>{f}</li>)}
        </ul>
      )}
      {a.alias_of && (
        <Notice tone="info">
          Same layout as existing spec <strong>{a.alias_of}</strong> — Apply adds this supplier as an alias on that spec
          and moves this sample there. No new spec is created.
        </Notice>
      )}
      {a.canonical_name && (
        <Notice tone="ok">
          Rename this spec to <strong>{a.canonical_name}</strong> — specs are shared by every venue, so they
          are named for the business, not for one account&rsquo;s spelling of it.
        </Notice>
      )}
      {(a.wrong_aliases?.length ?? 0) > 0 && (
        <Notice tone="error">
          Misfiled {a.wrong_aliases!.length === 1 ? 'alias' : 'aliases'} to remove — each one routes that
          supplier&rsquo;s invoices through the wrong prompt:
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {a.wrong_aliases!.map((w) => (
              <li key={`${w.spec_id}:${w.alias}`}><strong>{w.alias}</strong> on the {w.spec} spec</li>
            ))}
          </ul>
        </Notice>
      )}
      {(a.proposed_instructions ?? '').trim() ? (
        <div style={{ marginBottom: 10 }}>
          <div style={labelStyle}>
            {a.alias_of ? `Proposed spec text for '${a.alias_of}' (replaces its current instructions)` : 'Proposed spec text (replaces the current instructions)'}
          </div>
          <pre style={{ margin: 0, padding: '8px 10px', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontFamily: 'inherit', fontSize: 'var(--fs-sm)', color: 'var(--text)', background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>{a.proposed_instructions}</pre>
        </div>
      ) : (
        <div style={{ marginBottom: 10, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          {a.alias_of
            ? `No text change — '${a.alias_of}' already covers this layout as written.`
            : a.spec_not_needed
              ? 'The current prompts already read this document correctly — no spec change needed; the corrected values were baselined.'
              : 'No spec change proposed — the sensei corrected the expected values only.'}
        </div>
      )}
      {own && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          <StatusBadge status={own.status || 'new'} />
          <span>This invoice vs the sensei’s corrected values{own.status === 'fail' ? ` — ${own.diffs?.length ?? 0} mismatch(es)` : ''}</span>
        </div>
      )}
      {/* The evidence behind that badge: the sensei's corrected values AND the
          raw extraction the proposed prompt produced — check either against
          the PDF, don't take the sensei's word for it. */}
      {(a.ground_truth || own?.extraction) && (
        <div style={{ margin: '10px 0' }}>
          <div style={labelStyle}>Verify the values yourself (against the PDF)</div>
          <DojoSampleView
            key={`${sampleId}:${a.at ?? ''}`}
            sampleId={sampleId}
            expected={a.ground_truth ?? null}
            extraction={own?.extraction ?? null}
            diffs={own?.diffs ?? []}
            status={own?.status === 'pass' ? 'pass' : own?.status === 'fail' ? 'fail' : 'new'}
            readOnly
            current={currentRun ? currentRun.extraction : undefined}
            currentDiffs={currentRun?.diffs ?? []}
            stored={currentRun ? currentRun.expected : undefined}
            labels={{
              stored: 'Current expected values',
              expected: 'Values from this analysis',
              extracted: 'Extracted with proposed prompt',
              current: 'Current prompt',
              storedHint: 'the baseline stored on the sample today — what regression tests against; the analysis values replace it on Apply only if it isn’t admin-owned',
              expectedHint: 'what this analysis read off the PDF — these become the sample’s expected values',
              extractedHint: 'what the PROPOSED prompt actually pulled in the verification run — the pass/fail above compares exactly these two',
              currentHint: 'what the CURRENT prompts pull from this document today — the failing read this proposal fixes; mismatches vs the expected values are highlighted',
            }}
          />
        </div>
      )}
      {/* Sibling sweep: this supplier's BASELINED samples, re-extracted under
          the candidate prompt. candidate_run skips no-baseline samples since
          Aug 2026 — they could neither pass nor fail, and their NEW rows read
          as regression coverage that wasn't there. Analyses stored before
          that change still carry NEW rows in their blobs, so filter them at
          render too: the card only ever shows what the sensei verified.
          Each row expands into the same verify table — the sibling's stored
          baseline beside what the PROPOSED prompt pulled from it, mismatches
          highlighted — so a FAIL explains itself, and the correction box
          below can be told exactly what to protect. */}
      {sib?.samples?.filter((s) => s.status !== 'new').map((s) => (
        <div key={s.id}>
          <button type="button" onClick={() => toggleSibling(s.id)}
            title="show this sample's baseline vs what the proposed prompt extracted from it"
            aria-expanded={openSibling === s.id}
            style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '4px 0', border: 'none', background: 'none', cursor: 'pointer', textAlign: 'left', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
            <Icon icon={openSibling === s.id ? ChevronDown : ChevronRight} size="dense" tone="muted" />
            <StatusBadge status={s.status} />
            <span style={{ minWidth: 0 }}>{s.label} (vs its baseline{s.status === 'fail' ? ` — ${s.diffs?.length ?? 0} mismatch${(s.diffs?.length ?? 0) === 1 ? '' : 'es'}` : ''})</span>
          </button>
          {openSibling === s.id && (
            <div style={{ margin: '4px 0 10px 22px' }}>
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6 }}>
                <Button size="sm" icon={Download} onClick={() => downloadSiblingPdf(s.id, s.label)}
                  title="download this sample's invoice PDF">
                  Download PDF
                </Button>
              </div>
              {/* Analyses stored before Aug 2026 kept only the diffs, not the
                  candidate extraction — for those, the diffs alone still say
                  exactly which fields broke. New runs carry the extraction
                  and get the full table below instead. */}
              {!s.extraction && (s.diffs?.length ?? 0) > 0 && (
                <Notice tone="error">
                  <div style={{ fontWeight: 600, marginBottom: 2 }}>What the proposed prompt broke (this analysis stored only the diffs — re-run the sensei for the full extraction):</div>
                  {(s.diffs ?? []).map((d, i) => (
                    <div key={i}>
                      {d.line != null ? `line ${d.line}${d.description ? ` “${d.description}”` : ''} — ` : ''}{d.field}: expected {JSON.stringify(d.expected ?? null)}, got {JSON.stringify(d.actual ?? null)}
                    </div>
                  ))}
                </Notice>
              )}
              <DojoSampleView
                key={`sib-${s.id}:${a.at ?? ''}`}
                sampleId={s.id}
                expected={s.id in siblingBaselines ? siblingBaselines[s.id] : null}
                extraction={s.extraction ?? null}
                diffs={s.diffs ?? []}
                status={s.status === 'pass' ? 'pass' : s.status === 'fail' ? 'fail' : s.status === 'error' ? 'error' : 'new'}
                readOnly
                labels={{
                  expected: 'Current expected values',
                  extracted: 'Extracted with proposed prompt',
                  expectedHint: 'the baseline stored on this sample — what the candidate run was verified against',
                  extractedHint: 'what the PROPOSED prompt pulled from this sample in the verification run — mismatches vs its baseline are highlighted',
                }}
              />
            </div>
          )}
        </div>
      ))}
      {/* Reply to the thread: a wrong value in the proposal gets corrected
          here — the sensei re-reads the document with the correction as
          authoritative and re-tests before re-proposing. */}
      {(a.thread?.filter((m) => m.role === 'admin').length ?? 0) > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={labelStyle}>Your corrections so far</div>
          {a.thread!.filter((m) => m.role === 'admin').map((m, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, padding: '1px 0', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
              <Icon icon={CornerDownRight} size="dense" tone="muted" style={{ marginTop: 2 }} />
              <span style={{ minWidth: 0 }}>{m.text}</span>
            </div>
          ))}
        </div>
      )}
      {a.status !== 'applied' && (
        <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'flex-start' }}>
          <textarea className="n-input" value={feedback} onChange={(e) => setFeedback(e.target.value)} rows={2}
            aria-label="Correct the sensei"
            placeholder={'Correct the sensei — e.g. "line 4’s unit must stay ‘2x12 pack’, never flattened to ‘24 pack’" — the sensei re-analyses with your correction as authoritative and re-tests'}
            style={{ flex: 1, minWidth: 0 }} />
          <Button size="sm" onClick={() => { onReanalyse(feedback); setFeedback(''); }}
            disabled={!!analysing || !feedback.trim()}
            title="sends your correction to the sensei as authoritative — it re-analyses and re-tests before re-proposing">
            {analysing ? 'Re-analysing…' : 'Send'}
          </Button>
        </div>
      )}
      {a.status !== 'applied' && (() => {
        // A GREEN analysis with no proposed text and no alias changes
        // nothing on Apply — the corrected values were already baselined
        // when it went green — so offering "Apply spec update" is a
        // misleading no-op. (A not-green one keeps "Apply anyway": it can
        // still baseline the corrected values.)
        const hasChange = !!(a.proposed_instructions ?? '').trim() || !!a.alias_of
          || !!a.canonical_name || (a.wrong_aliases?.length ?? 0) > 0;
        const applyable = hasChange || a.status !== 'ready';
        // Dismiss then Apply, on the right — the approval card's order.
        return (
          <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
            <Button onClick={onDismiss}
              title={applyable ? 'decline this proposal without applying it' : 'nothing to apply — clears the proposal'}>
              {applyable ? 'Dismiss proposal' : 'Close — no change needed'}
            </Button>
            {applyable && (
              // A not-green run isn't the obvious next step, so it isn't primary.
              <Button variant={a.status === 'ready' ? 'primary' : 'secondary'} onClick={onApply} disabled={!!applying}
                title={a.status === 'ready'
                  ? (a.alias_of ? `add the alias to '${a.alias_of}' and move this sample there` : 'write the proposed spec text and baseline the corrected values')
                  : 'the candidate run was NOT fully green — applying anyway is your call'}
                style={applying ? { cursor: 'wait' } : undefined}>
                {applying ? 'Applying…' : a.status === 'ready' ? (a.alias_of ? `Add alias to '${a.alias_of}'` : 'Apply spec update') : 'Apply anyway'}
              </Button>
            )}
          </div>
        );
      })()}
    </div>
  );
}
