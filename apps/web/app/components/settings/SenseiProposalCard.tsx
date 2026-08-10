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
    siblings?: { samples?: { id: string; label: string; status: string; diffs?: unknown[] }[]; passed?: number; failed?: number; errors?: number; new?: number };
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
  // The CURRENT prompts' extraction (the sample's last run) — the failing
  // read this proposal is fixing. Shown as a third toggle in the verify
  // table so before/proposed/corrected sit side by side.
  const [currentRun, setCurrentRun] = useState<{ extraction: ExtractionDoc | null; diffs: DojoDiff[] } | null>(null);
  useEffect(() => {
    let cancelled = false;
    setCurrentRun(null);
    (async () => {
      try {
        const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/last-run`);
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setCurrentRun({ extraction: data.extraction ?? null, diffs: data.diffs ?? [] });
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
            labels={{
              expected: 'Values from this Analysis',
              extracted: 'Extracted with proposed prompt',
              current: 'Current prompt',
              expectedHint: 'what this analysis read off the PDF — these become the sample’s expected values',
              extractedHint: 'what the PROPOSED prompt actually pulled in the verification run — the pass/fail above compares exactly these two',
              currentHint: 'what the CURRENT prompts pull from this document today — the failing read this proposal fixes; mismatches vs the expected values are highlighted',
            }}
          />
        </div>
      )}
      {sib?.samples?.map((s) => (
        <div key={s.id} style={{ fontSize: '0.7rem', color: '#555', display: 'flex', gap: 6, alignItems: 'center', padding: '1px 0' }}>
          <StatusBadge status={s.status} />
          <span>{s.label} (existing baseline)</span>
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
        const hasChange = !!(a.proposed_instructions ?? '').trim() || !!a.alias_of;
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
