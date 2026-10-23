import type { Metadata } from 'next';
import PortalPage from '../../components/finance/entertainers/public/PortalPage';

/**
 * /p/<token> — a performer's portal (Loaded Payroll · Entertainers). Public:
 * no login, no app shell. The token is the only thing in the URL; the page
 * carries noindex and no-referrer in its head (and next.config.ts should
 * add the same headers plus frame-ancestors 'none' for this path — see
 * build-notes.md). It shows venue, date, times and status only — never an
 * amount, a rate, a bank account, an IRD number or contact details.
 */
export const metadata: Metadata = {
  title: 'Your performances',
  description: 'Confirm your performances and upload invoices',
  robots: { index: false, follow: false, nocache: true },
  referrer: 'no-referrer',
};

export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <PortalPage token={token} />;
}
