'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';
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

const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  pass: { bg: '#d1fae5', fg: '#065f46' },
  fail: { bg: '#fee2e2', fg: '#991b1b' },
  error: { bg: '#fee2e2', fg: '#991b1b' },
  new: { bg: '#fdf6e7', fg: '#8a6d3b' },
};

function StatusBadge({ status }: { status: string }) {
  const c = STATUS_COLORS[status] || STATUS_COLORS.new;
  return (
    <span style={{ fontSize: '0.62rem', fontWeight: 700, padding: '1px 7px', borderRadius: 4, background: c.bg, color: c.fg, whiteSpace: 'nowrap' }}>
      {status.toUpperCase()}
    </span>
  );
}

const labelStyle: React.CSSProperties = { fontSize: '0.75rem', fontWeight: 600, color: '#888', textTransform: 'uppercase', marginBottom: 4, display: 'block' };

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
    <div style={{ border: '1px solid #e6d9b8', borderRadius: 8, background: '#fffdf6', padding: '10px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <strong style={{ fontSize: '0.82rem' }}>Sensei proposal</strong>
        <StatusBadge status={a.status === 'ready' ? 'pass' : a.status === 'failed' ? 'error' : 'new'} />
        {a.status === 'applied' && a.auto_applied && (
          <span title="a green sensei proposal for a brand-new supplier (no existing prompt) — applied automatically"
            style={{ fontSize: '0.62rem', fontWeight: 700, color: '#065f46', background: '#d1fae5', border: '1px solid #a7dcc4', borderRadius: 4, padding: '1px 7px', whiteSpace: 'nowrap' }}>
            auto-applied
          </span>
        )}
        {a.model && <span style={{ fontSize: '0.62rem', color: '#999' }}>{a.model}</span>}
        <button type="button" onClick={onClose}
          style={{ marginLeft: 'auto', fontSize: '0.66rem', border: 'none', background: 'none', color: '#999', cursor: 'pointer' }}>✕</button>
      </div>
      {a.error && <div style={{ fontSize: '0.72rem', color: '#c0392b', marginBottom: 6 }}>{a.error}</div>}
      {a.rationale && (
        <div style={{ fontSize: '0.74rem', color: '#4a4a4a', marginBottom: 8, whiteSpace: 'pre-wrap' }}>{a.rationale}</div>
      )}
      {(a.ground_truth_violations?.length ?? 0) > 0 && (
        <div style={{ fontSize: '0.72rem', color: '#991b1b', background: '#fee2e2', border: '1px solid #f5c6c6', borderRadius: 6, padding: '6px 10px', marginBottom: 8 }}>
          The sensei&rsquo;s corrected values fail the document&rsquo;s own arithmetic — treat this proposal with suspicion:
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {a.ground_truth_violations!.map((v, i) => <li key={i}>{v}</li>)}
          </ul>
        </div>
      )}
      {(a.layout_facts?.length ?? 0) > 0 && (
        <ul style={{ margin: '0 0 8px', paddingLeft: 18, fontSize: '0.7rem', color: '#666' }}>
          {a.layout_facts!.map((f, i) => <li key={i}>{f}</li>)}
        </ul>
      )}
      {a.alias_of && (
        <div style={{ fontSize: '0.72rem', color: '#1d4ed8', background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 6, padding: '6px 10px', marginBottom: 8 }}>
          Same layout as existing spec <strong>{a.alias_of}</strong> — Apply adds this supplier as an alias on that spec
          and moves this sample there. No new spec is created.
        </div>
      )}
      {a.canonical_name && (
        <div style={{ fontSize: '0.72rem', color: '#065f46', background: '#ecfdf5', border: '1px solid #a7f3d0', borderRadius: 6, padding: '6px 10px', marginBottom: 8 }}>
          Rename this spec to <strong>{a.canonical_name}</strong> — specs are shared by every venue, so they
          are named for the business, not for one account&rsquo;s spelling of it.
        </div>
      )}
      {(a.wrong_aliases?.length ?? 0) > 0 && (
        <div style={{ fontSize: '0.72rem', color: '#991b1b', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 6, padding: '6px 10px', marginBottom: 8 }}>
          Misfiled {a.wrong_aliases!.length === 1 ? 'alias' : 'aliases'} to remove — each one routes that
          supplier&rsquo;s invoices through the wrong prompt:
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {a.wrong_aliases!.map((w) => (
              <li key={`${w.spec_id}:${w.alias}`}><strong>{w.alias}</strong> on the {w.spec} spec</li>
            ))}
          </ul>
        </div>
      )}
      {(a.proposed_instructions ?? '').trim() ? (
        <div style={{ marginBottom: 8 }}>
          <div style={{ ...labelStyle, marginBottom: 2 }}>
            {a.alias_of ? `Proposed spec text for '${a.alias_of}' (replaces its current instructions)` : 'Proposed spec text (replaces the current instructions)'}
          </div>
          <pre style={{ fontSize: '0.7rem', whiteSpace: 'pre-wrap', background: '#fff', border: '1px solid #eee', borderRadius: 6, padding: '8px 10px', margin: 0, fontFamily: 'inherit' }}>{a.proposed_instructions}</pre>
        </div>
      ) : (
        <div style={{ fontSize: '0.7rem', color: '#777', marginBottom: 8 }}>
          {a.alias_of
            ? `No text change — '${a.alias_of}' already covers this layout as written.`
            : a.spec_not_needed
              ? 'The current prompts already read this document correctly — no spec change needed; the corrected values were baselined.'
              : 'No spec change proposed — the sensei corrected the expected values only.'}
        </div>
      )}
      {own && (
        <div style={{ fontSize: '0.7rem', color: '#555', marginBottom: 2, display: 'flex', gap: 6, alignItems: 'center' }}>
          <StatusBadge status={own.status || 'new'} />
          <span>this invoice vs the sensei’s corrected values{own.status === 'fail' ? ` — ${own.diffs?.length ?? 0} mismatch(es)` : ''}</span>
        </div>
      )}
      {/* The evidence behind that badge: the sensei's corrected values AND the
          raw extraction the proposed prompt produced — check either against
          the PDF, don't take the sensei's word for it. */}
      {(a.ground_truth || own?.extraction) && (
        <div style={{ margin: '8px 0' }}>
          <div style={{ ...labelStyle, marginBottom: 4 }}>Verify the values yourself (against the PDF)</div>
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
              expected: 'Values from this Analysis',
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
            style={{ fontSize: '0.7rem', color: '#555', display: 'flex', gap: 6, alignItems: 'center', padding: '2px 0', border: 'none', background: 'none', cursor: 'pointer', fontFamily: 'inherit', width: '100%', textAlign: 'left' }}>
            <span style={{ fontSize: '0.6rem', color: '#999', width: 10 }}>{openSibling === s.id ? '▾' : '▸'}</span>
            <StatusBadge status={s.status} />
            <span>{s.label} (vs its baseline{s.status === 'fail' ? ` — ${s.diffs?.length ?? 0} mismatch${(s.diffs?.length ?? 0) === 1 ? '' : 'es'}` : ''})</span>
          </button>
          {openSibling === s.id && (
            <div style={{ margin: '4px 0 10px 16px' }}>
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 4 }}>
                <button type="button" onClick={() => downloadSiblingPdf(s.id, s.label)}
                  title="download this sample's invoice PDF"
                  style={{ fontSize: '0.64rem', padding: '2px 9px', border: '1px solid #d8d4cc', borderRadius: 4, background: '#fff', color: '#666', cursor: 'pointer', fontFamily: 'inherit' }}>
                  ⤓ Download PDF
                </button>
              </div>
              {/* Analyses stored before Aug 2026 kept only the diffs, not the
                  candidate extraction — for those, the diffs alone still say
                  exactly which fields broke. New runs carry the extraction
                  and get the full table below instead. */}
              {!s.extraction && (s.diffs?.length ?? 0) > 0 && (
                <div style={{ border: '1px solid #f0c0ba', borderRadius: 6, background: '#fdf3f2', padding: '6px 10px', marginBottom: 6, fontSize: '0.7rem', color: '#7a2e24' }}>
                  <div style={{ fontWeight: 700, marginBottom: 2 }}>What the proposed prompt broke (this analysis stored only the diffs — re-run the sensei for the full extraction):</div>
                  {(s.diffs ?? []).map((d, i) => (
                    <div key={i}>
                      {d.line != null ? `line ${d.line}${d.description ? ` “${d.description}”` : ''} — ` : ''}{d.field}: expected {JSON.stringify(d.expected ?? null)}, got {JSON.stringify(d.actual ?? null)}
                    </div>
                  ))}
                </div>
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
        <div style={{ marginTop: 8 }}>
          <div style={{ ...labelStyle, marginBottom: 2 }}>Your corrections so far</div>
          {a.thread!.filter((m) => m.role === 'admin').map((m, i) => (
            <div key={i} style={{ fontSize: '0.7rem', color: '#555', padding: '1px 0' }}>↳ {m.text}</div>
          ))}
        </div>
      )}
      {a.status !== 'applied' && (
        <div style={{ display: 'flex', gap: 6, marginTop: 8, alignItems: 'flex-start' }}>
          <textarea value={feedback} onChange={(e) => setFeedback(e.target.value)} rows={2}
            placeholder={'Correct the sensei — e.g. "line 4’s unit must stay ‘2x12 pack’, never flattened to ‘24 pack’" — the sensei re-analyses with your correction as authoritative and re-tests'}
            style={{ flex: 1, fontSize: '0.7rem', padding: '5px 8px', border: '1px solid #d8d4cc', borderRadius: 6, fontFamily: 'inherit', resize: 'vertical' }} />
          <button type="button" onClick={() => { onReanalyse(feedback); setFeedback(''); }}
            disabled={!!analysing || !feedback.trim()}
            title="sends your correction to the sensei as authoritative — it re-analyses and re-tests before re-proposing"
            style={{ fontSize: '0.72rem', padding: '5px 12px', border: '1px solid #b78a2f', borderRadius: 6, background: '#fff', color: '#8a6d3b', cursor: analysing || !feedback.trim() ? 'default' : 'pointer', whiteSpace: 'nowrap', opacity: feedback.trim() ? 1 : 0.5 }}>
            {analysing ? 'Re-analysing…' : 'Send'}
          </button>
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
        return (
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            {applyable && (
              <button type="button" onClick={onApply} disabled={!!applying}
                title={a.status === 'ready'
                  ? (a.alias_of ? `add the alias to '${a.alias_of}' and move this sample there` : 'write the proposed spec text and baseline the corrected values')
                  : 'the candidate run was NOT fully green — applying anyway is your call'}
                style={{ fontSize: '0.72rem', padding: '5px 14px', border: 'none', borderRadius: 6, background: a.status === 'ready' ? '#2e7d4f' : '#b78a2f', color: '#fff', cursor: applying ? 'wait' : 'pointer' }}>
                {applying ? 'Applying…' : a.status === 'ready' ? (a.alias_of ? `Add alias to '${a.alias_of}'` : 'Apply spec update') : 'Apply anyway'}
              </button>
            )}
            <button type="button" onClick={onDismiss}
              title={applyable ? 'decline this proposal without applying it' : 'nothing to apply — clears the proposal'}
              style={{ fontSize: '0.72rem', padding: '5px 12px', border: '1px solid #ccc', borderRadius: 6, background: '#fff', color: '#666', cursor: 'pointer' }}>
              {applyable ? 'Dismiss proposal' : 'Close — no change needed'}
            </button>
          </div>
        );
      })()}
    </div>
  );
}
