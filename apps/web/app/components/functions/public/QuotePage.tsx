'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { CalendarDays, Check, MapPin, Printer, Users } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import { dateLabel } from '../shared/format';
import ProposalFrame, { printProposal } from './ProposalFrame';
import PublicShell, { LinkGone, LoadFailed, Notice, PublicLoading } from './PublicShell';
import { brandOf } from './brand';
import { PublicApiError, friendlyError, publicGet, publicSend, tokenPath } from './publicApi';
import { optionMoney, optionStatus, optionTitle, optionWhen, optionWhere, quoteBanner } from './quoteModel';
import type { QuoteOption, QuotePayload } from './types';

type Load =
  | { kind: 'loading' }
  | { kind: 'gone' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; quote: QuotePayload };

/** A UTC fact as a date where the customer is ("11 Oct 2026"). */
function dayOf(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short', year: 'numeric' });
}

/**
 * /q/<token> — the customer's quote: the venue's proposal (sandboxed HTML),
 * then Norm's own panel of the options that proposal offered, each with
 * Confirm / Decline drawn from the option records — confirming one books it
 * (the server creates the event, once). Every dead link is the same "not
 * available" page.
 */
export default function QuotePage({ token }: { token: string }) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [justConfirmed, setJustConfirmed] = useState<string | null>(null);
  const frameRef = useRef<HTMLIFrameElement>(null);

  const fetchQuote = useCallback(async () => {
    try {
      const quote = await publicGet<QuotePayload>(`/quote/${tokenPath(token)}`);
      setLoad({ kind: 'ready', quote });
    } catch (err) {
      if (err instanceof PublicApiError && err.notFound) setLoad({ kind: 'gone' });
      else setLoad((prev) => (prev.kind === 'ready' ? prev : { kind: 'error', message: friendlyError(err) }));
    }
  }, [token]);

  useEffect(() => {
    void fetchQuote();
  }, [fetchQuote]);

  if (load.kind === 'loading') return <PublicLoading label="Loading your quote…" />;
  if (load.kind === 'gone') return <LinkGone />;
  if (load.kind === 'error') {
    return <LoadFailed message={load.message} onRetry={() => { setLoad({ kind: 'loading' }); void fetchQuote(); }} />;
  }

  const { quote } = load;
  const brand = brandOf(quote.venue);
  const venueName = quote.venue?.name;
  const banner = quoteBanner(quote);
  const sent = dayOf(quote.proposal?.sent_at);
  const greeting = quote.enquiry.contact_first_name ? `Hi ${quote.enquiry.contact_first_name}` : 'Hello';

  return (
    <PublicShell
      brand={brand}
      actions={quote.proposal ? <Button size="sm" icon={Printer} onClick={() => printProposal(frameRef.current)}>Print or save as PDF</Button> : undefined}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <section aria-labelledby="quote-title">
          <h1 id="quote-title" style={{ margin: 0, fontSize: 'var(--fs-xl)', fontWeight: 700, lineHeight: 1.25, color: 'var(--text)' }}>
            {quote.enquiry.subject || 'Your quote'}
          </h1>
          <p style={{ margin: '4px 0 10px', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
            {greeting}{venueName ? ` — here's your quote from ${venueName}.` : " — here's your quote."}
          </p>
          <div className="fp-facts">
            {quote.enquiry.guests ? <span className="fp-fact"><Icon icon={Users} size={14} tone="muted" />{quote.enquiry.guests} guests</span> : null}
            {quote.enquiry.booking_label ? <span className="fp-fact">{quote.enquiry.booking_label}</span> : null}
            {sent ? <span className="fp-fact">Sent {sent}</span> : null}
            {quote.proposal?.valid_until ? <span className="fp-fact">Valid until {dateLabel(quote.proposal.valid_until)}</span> : null}
          </div>
        </section>

        {banner && <Notice tone={banner.tone}>{banner.text}</Notice>}

        {quote.proposal ? (
          <section aria-label="Proposal" className="n-card fp-proposal-card" style={{ overflow: 'hidden' }}>
            <ProposalFrame frameRef={frameRef} document={quote.proposal.document} title={venueName ? `Proposal from ${venueName}` : 'Proposal'} />
          </section>
        ) : (
          <Notice tone="info">Your quote is on its way — the venue will email you as soon as it&apos;s ready.</Notice>
        )}

        {quote.options.length > 0 && (
          <OptionsPanel
            token={token}
            quote={quote}
            justConfirmed={justConfirmed}
            onUpdate={(next, confirmedId) => {
              setLoad({ kind: 'ready', quote: next });
              if (confirmedId) setJustConfirmed(confirmedId);
            }}
            onStale={() => void fetchQuote()}
          />
        )}
      </div>
    </PublicShell>
  );
}

function OptionsPanel({ token, quote, justConfirmed, onUpdate, onStale }: {
  token: string;
  quote: QuotePayload;
  justConfirmed: string | null;
  onUpdate: (quote: QuotePayload, confirmedId?: string) => void;
  onStale: () => void;
}) {
  const actionable = quote.options.some((o) => o.can_confirm);
  const several = quote.options.length > 1;
  return (
    <section aria-labelledby="options-title" style={{ marginTop: 8 }}>
      <h2 id="options-title" style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, color: 'var(--text)' }}>
        {actionable ? (several ? 'Choose and confirm your option' : 'Confirm your booking') : several ? 'Your options' : 'Your option'}
      </h2>
      <p style={{ margin: '4px 0 12px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        {actionable ? 'Confirming books it — the venue is told straight away. ' : ''}Amounts are {quote.money.tax_label}.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {quote.options.map((option, i) => (
          <OptionCard
            key={option.id}
            token={token}
            option={option}
            index={i}
            quote={quote}
            justConfirmed={justConfirmed === option.id}
            onUpdate={onUpdate}
            onStale={onStale}
          />
        ))}
      </div>
    </section>
  );
}

type Step = 'idle' | 'confirm' | 'decline';

function OptionCard({ token, option, index, quote, justConfirmed, onUpdate, onStale }: {
  token: string;
  option: QuoteOption;
  index: number;
  quote: QuotePayload;
  justConfirmed: boolean;
  onUpdate: (quote: QuotePayload, confirmedId?: string) => void;
  onStale: () => void;
}) {
  const [step, setStep] = useState<Step>('idle');
  const [note, setNote] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A second click while the first is in flight must not send twice.
  const inFlight = useRef(false);
  const title = optionTitle(option, index);
  const status = optionStatus(option, quote, justConfirmed);
  const where = optionWhere(option);
  const lines = optionMoney(option, quote.money.currency);
  const noteId = `note-${option.id}`;

  const respond = async (kind: 'confirm' | 'decline') => {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setError(null);
    try {
      const next = await publicSend<QuotePayload>(
        'POST',
        `/quote/${tokenPath(token)}/options/${encodeURIComponent(option.id)}/${kind}`,
        note.trim() ? { note: note.trim().slice(0, 2000) } : {},
      );
      setStep('idle');
      setNote('');
      onUpdate(next, kind === 'confirm' ? option.id : undefined);
    } catch (err) {
      setError(friendlyError(err));
      // The quote moved under us (another tab, the venue): show where it is now.
      if (err instanceof PublicApiError && [403, 404, 409].includes(err.status)) onStale();
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  };

  const highlighted = option.state === 'confirmed';
  return (
    <article
      aria-labelledby={`opt-${option.id}`}
      className="n-card fp-avoid-break"
      style={{ padding: 16, borderColor: highlighted ? 'var(--ok)' : undefined, borderWidth: highlighted ? 2 : 1 }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0, flex: '1 1 240px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <h3 id={`opt-${option.id}`} style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>{title}</h3>
            {status.badge && <Badge tone={status.badge.tone}>{status.badge.text}</Badge>}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 8 }}>
            <span className="fp-fact"><Icon icon={CalendarDays} size={14} tone="muted" />{optionWhen(option)}</span>
            {where && <span className="fp-fact"><Icon icon={MapPin} size={14} tone="muted" />{where}</span>}
          </div>
        </div>
        {lines.length > 0 && (
          <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'auto auto', columnGap: 12, rowGap: 2, fontSize: 'var(--fs-base)', fontVariantNumeric: 'tabular-nums' }}>
            {lines.map((line) => (
              <div key={line.label} style={{ display: 'contents' }}>
                <dt style={{ color: 'var(--muted)' }}>{line.label}</dt>
                <dd style={{ margin: 0, textAlign: 'right', fontWeight: line.strong ? 600 : 400, color: 'var(--text)' }}>{line.value}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      {status.note && (
        <p role={justConfirmed ? 'status' : undefined} style={{ margin: '12px 0 0', fontSize: 'var(--fs-base)', color: option.state === 'confirmed' ? 'var(--ok)' : 'var(--text-soft)', display: 'flex', gap: 6 }}>
          {option.state === 'confirmed' && <Icon icon={Check} size={16} style={{ marginTop: 2 }} />}
          <span>{status.note}</span>
        </p>
      )}

      {error && (
        <div role="alert" style={{ marginTop: 12, padding: '10px 12px', borderRadius: 'var(--radius)', background: 'var(--error-bg)', color: 'var(--error)', fontSize: 'var(--fs-base)' }}>
          {error}
        </div>
      )}

      {step === 'idle' && (option.can_confirm || option.can_decline) && (
        <div className="fp-no-print" style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
          {option.can_confirm && (
            <Button variant="primary" onClick={() => { setStep('confirm'); setError(null); }}>
              {option.state === 'declined' ? 'Confirm this option after all' : 'Confirm this option'}
            </Button>
          )}
          {option.can_decline && (
            <Button variant="quiet" onClick={() => { setStep('decline'); setError(null); }}>Decline</Button>
          )}
        </div>
      )}

      {step !== 'idle' && (
        <div className="fp-no-print" style={{ marginTop: 14, padding: 14, borderRadius: 'var(--radius)', background: 'var(--surface-alt)' }}>
          <p style={{ margin: '0 0 10px', fontSize: 'var(--fs-base)', color: 'var(--text)', fontWeight: 600 }}>
            {step === 'confirm'
              ? `Confirm ${title} for ${optionWhen(option)}${lines[0] ? ` — ${lines[0].label.toLowerCase()} ${lines[0].value}` : ''}?`
              : `Decline ${title}?`}
          </p>
          <label htmlFor={noteId} className="n-label">
            {step === 'confirm' ? 'Anything the venue should know? (optional)' : 'Let the venue know why (optional)'}
          </label>
          <textarea
            id={noteId}
            className="n-input"
            rows={3}
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            style={{ width: '100%' }}
            disabled={pending}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <Button variant={step === 'confirm' ? 'primary' : 'secondary'} onClick={() => void respond(step)} disabled={pending} aria-busy={pending || undefined}>
              {pending ? (step === 'confirm' ? 'Confirming…' : 'Declining…') : step === 'confirm' ? 'Yes, confirm' : 'Decline option'}
            </Button>
            <Button variant="quiet" onClick={() => { setStep('idle'); setError(null); }} disabled={pending}>Cancel</Button>
          </div>
        </div>
      )}
    </article>
  );
}
