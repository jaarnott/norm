import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { ImageResponse } from 'next/og';

// The picture a link to the site shows when it's shared. Rendered once at
// build. The image renderer can't read woff2, so it uses static TTF cuts of
// the same Figtree (app/fonts, made from Figtree-latin.woff2; OFL).
export const alt = "Norm — the best hospitality manager you've ever had. Working 24/7.";
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

export default async function OpenGraphImage() {
  const fonts = join(process.cwd(), 'app/fonts');
  const [extraBold, medium] = await Promise.all([
    readFile(join(fonts, 'Figtree-ExtraBold-latin.ttf')),
    readFile(join(fonts, 'Figtree-Medium-latin.ttf')),
  ]);
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          padding: 72,
          background: '#1f4a36',
          color: '#f6f1e8',
          fontFamily: 'Figtree',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
          <div
            style={{
              width: 64,
              height: 64,
              borderRadius: 16,
              background: '#f6f1e8',
              color: '#1f4a36',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 36,
              fontWeight: 800,
            }}
          >
            N
          </div>
          <div style={{ fontSize: 40, fontWeight: 800, letterSpacing: -1 }}>Norm</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 72, fontWeight: 800, lineHeight: 1.05, letterSpacing: -2 }}>
          <div style={{ display: 'flex' }}>The best hospitality manager</div>
          <div style={{ display: 'flex' }}>you&apos;ve ever had.</div>
          <div style={{ display: 'flex', color: '#f2a93b' }}>Working 24/7.</div>
        </div>
        <div style={{ fontSize: 30, fontWeight: 500, color: '#cfdccf' }}>The AI operations team for hospitality</div>
      </div>
    ),
    {
      ...size,
      fonts: [
        { name: 'Figtree', data: extraBold, weight: 800, style: 'normal' },
        { name: 'Figtree', data: medium, weight: 500, style: 'normal' },
      ],
    },
  );
}
