import type { Metadata } from 'next';
import QuotePage from '../../components/functions/public/QuotePage';

/**
 * /q/<token> — a customer's quote (Norm Functions). Public: no login, no app
 * shell. The token is the only thing in the URL; next.config.ts adds
 * no-referrer and refuses framing for this path.
 */
export const metadata: Metadata = {
  title: 'Your quote',
  description: 'Your function quote',
  robots: { index: false, follow: false },
  referrer: 'no-referrer',
};

export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <QuotePage token={token} />;
}
