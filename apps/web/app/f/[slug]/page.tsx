import type { Metadata } from 'next';
import FormPage from '../../components/functions/public/FormPage';
import { HOSTED_FONTS_CSS } from '../../components/functions/shared/fonts';

/**
 * /f/<slug> — a venue's hosted web form (Norm Functions). Public and meant
 * to be framed by venue websites, so next.config.ts leaves framing open here
 * (unlike /q and /e). Norm's hosted fonts load here, so a form theme that
 * names one (Cormorant Garamond, Montserrat…) shows it.
 */
export const metadata: Metadata = {
  title: 'Enquiry form',
  description: 'Send the venue an enquiry',
  robots: { index: false, follow: false },
  referrer: 'no-referrer',
};

export default async function Page({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <>
      <link rel="stylesheet" href={HOSTED_FONTS_CSS} precedence="default" />
      <FormPage slug={slug} />
    </>
  );
}
