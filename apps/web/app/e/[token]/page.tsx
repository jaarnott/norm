import type { Metadata } from 'next';
import PortalPage from '../../components/functions/public/PortalPage';

/**
 * /e/<token> — a client's event portal (Norm Functions). Public: no login,
 * no app shell; sign-in is by a code emailed to an allowed address. The
 * token is the only thing in the URL; next.config.ts adds no-referrer and
 * refuses framing for this path.
 */
export const metadata: Metadata = {
  title: 'Your event',
  description: 'Your function details',
  robots: { index: false, follow: false },
  referrer: 'no-referrer',
};

export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <PortalPage token={token} />;
}
