'use client';

/**
 * The exact email that went out, in a sandboxed iframe: `sandbox=""` so
 * nothing in it can run or reach Norm's page, and its own CSP with no
 * scripts, no connections and no referrer. Orbit-era rows have no body —
 * Orbit kept only the send date — and say so. A money email (approved
 * amount, invoice query) opens only for `payroll:write` holders; the API
 * 403s and the dialog says who may open it.
 */
import { useEffect, useMemo, useState } from 'react';
import { ExternalLink, Send } from 'lucide-react';
import Dialog from '../../../ui/Dialog';
import Button from '../../../ui/Button';
import Icon from '../../../ui/Icon';
import PageState from '../../../ui/PageState';
import { EntApiError, entGet, entSend, errorText } from '../api';
import { when } from '../format';
import { RESENDABLE_KINDS, RECIPIENT_LABEL, emailKindLabel } from '../logic';
import { EmailStatusBadge, Fact, FactGrid, Notice } from '../ui';
import type { EmailBody, EmailLink } from '../types';

function emailDocument(html: string): string {
  const csp = "default-src 'none'; img-src https: http: data: cid:; style-src 'unsafe-inline'; font-src https: data:; base-uri 'none'; form-action 'none'";
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    `<meta http-equiv="Content-Security-Policy" content="${csp}">` +
    '<meta name="referrer" content="no-referrer"><style>' +
    "html,body{margin:0;padding:12px;background:#fff}body{font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;font-size:14px;line-height:1.5;color:#1a1a1a;overflow-wrap:anywhere}" +
    'img{max-width:100%;height:auto}table{max-width:100%}pre{white-space:pre-wrap}' +
    `</style></head><body>${html}</body></html>`
  );
}

export default function EmailBodyDialog({ link, timeZone, onClose, onResent }: {
  link: EmailLink;
  timeZone?: string;
  onClose: () => void;
  onResent?: () => void;
}) {
  const [body, setBody] = useState<EmailBody | null>(null);
  const [error, setError] = useState<{ text: string; forbidden: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const fromOrbit = link.source === 'orbit';

  useEffect(() => {
    if (fromOrbit) return;
    let alive = true;
    entGet<EmailBody>(`/emails/${link.id}`).then(
      (b) => { if (alive) setBody(b); },
      (e: unknown) => { if (alive) setError({ text: errorText(e), forbidden: e instanceof EntApiError && e.forbidden }); },
    );
    return () => { alive = false; };
  }, [link.id, fromOrbit]);

  const srcDoc = useMemo(() => (body ? emailDocument(body.html_body) : ''), [body]);

  const resend = async () => {
    setBusy(true);
    setNote(null);
    try {
      const res = await entSend<{ sent: boolean; message?: string }>('POST', `/emails/${link.id}/resend`);
      setNote(res.sent ? 'Sent again.' : res.message ?? "It wasn't sent — try again later.");
      if (res.sent) onResent?.();
    } catch (e) {
      setNote(errorText(e));
    }
    setBusy(false);
  };

  return (
    <Dialog title={link.subject || emailKindLabel(link.kind)} width={720} onClose={onClose} closeButton>
      <FactGrid min={150}>
        <Fact label="Sent">{when(link.sent_at, timeZone)}</Fact>
        <Fact label="To">{link.recipient_email ? `${link.recipient_email} (${RECIPIENT_LABEL[link.recipient_kind] ?? link.recipient_kind})` : RECIPIENT_LABEL[link.recipient_kind]}</Fact>
        <Fact label="Kind">{emailKindLabel(link.kind)}</Fact>
        <Fact label="Delivery"><EmailStatusBadge status={link.status} /></Fact>
        <Fact label="Link opened">{link.link_opened_at ? when(link.link_opened_at, timeZone) : 'Not yet'}</Fact>
        {link.performer_name && <Fact label="Performer">{link.performer_name}</Fact>}
        {link.venue_name && <Fact label="Venue">{link.venue_name}</Fact>}
      </FactGrid>
      {link.shadow_redirected && (
        <div style={{ marginTop: 12 }}>
          <Notice tone="warn">Sent in shadow mode: it went to the organisation&rsquo;s payroll contact, not the performer.</Notice>
        </div>
      )}
      <div style={{ marginTop: 14 }}>
        {fromOrbit ? (
          <Notice tone="neutral">From Orbit &mdash; date only. Orbit kept no copy of what it sent.</Notice>
        ) : error ? (
          error.forbidden ? (
            <Notice tone="info">This email carries money figures, so only a finance person (payroll access) can open it.</Notice>
          ) : (
            <PageState kind="error" title="Couldn’t open the email" detail={error.text} />
          )
        ) : !body ? (
          <PageState kind="loading" title="Opening the email…" />
        ) : (
          <iframe
            title={`Email: ${link.subject ?? emailKindLabel(link.kind)}`}
            sandbox=""
            referrerPolicy="no-referrer"
            srcDoc={srcDoc}
            style={{ display: 'block', width: '100%', height: 420, border: '1px solid var(--line)', borderRadius: 'var(--radius)', background: '#fff' }}
          />
        )}
      </div>
      {note && <div style={{ marginTop: 10, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{note}</div>}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 16 }}>
        {!fromOrbit && RESENDABLE_KINDS.has(link.kind) && (
          <Button icon={Send} onClick={resend} disabled={busy}>{busy ? 'Sending…' : 'Send again'}</Button>
        )}
        <Button variant="primary" onClick={onClose}>Close</Button>
      </div>
      {body && !fromOrbit && (
        <div style={{ marginTop: 8, fontSize: 'var(--fs-xs)', color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
          <Icon icon={ExternalLink} size={12} /> Links inside the email are shown but not clickable here.
        </div>
      )}
    </Dialog>
  );
}
