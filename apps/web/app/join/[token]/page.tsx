import type { Metadata } from 'next';
import JoinPage from '../../components/finance/entertainers/public/JoinPage';

/**
 * /join/<token> — a performer's onboarding form (the Entertainers App):
 * the personal link Norm emails when a booker adds them, a
 * band member's own link, or the declaration-only link. Public: no login,
 * no app shell; noindex and no-referrer in the head (next.config.ts should
 * add the same headers plus frame-ancestors 'none' — see build-notes.md).
 * There is no form without a token: a bare /join is a 404.
 */
export const metadata: Metadata = {
  title: 'Performer setup',
  description: 'Set up how you are paid',
  robots: { index: false, follow: false, nocache: true },
  referrer: 'no-referrer',
};

export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <JoinPage token={token} />;
}
