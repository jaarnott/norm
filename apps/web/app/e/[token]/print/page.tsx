import type { Metadata } from 'next';
import PrintPage from '../../../components/functions/public/PrintPage';

/** /e/<token>/print — the printable event sheet (portal session required). */
export const metadata: Metadata = {
  title: 'Event sheet',
  description: 'Your function details, ready to print',
  robots: { index: false, follow: false },
  referrer: 'no-referrer',
};

export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <PrintPage token={token} />;
}
