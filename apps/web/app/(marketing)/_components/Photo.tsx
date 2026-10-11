/**
 * A venue photo from public/marketing, in WebP at the widths listed (made from
 * the venues' own photo library). `sizes` says how wide it is drawn, so the
 * browser picks the smallest file that stays sharp.
 */
export default function Photo({ name, widths, ratio, alt, sizes, className, eager = false }: {
  name: string;
  widths: number[];
  /** Height ÷ width of the original, so the space is held before it loads. */
  ratio: number;
  alt: string;
  sizes: string;
  className?: string;
  eager?: boolean;
}) {
  const largest = widths[widths.length - 1];
  return (
    // eslint-disable-next-line @next/next/no-img-element -- pre-sized WebP files; no optimiser in the standalone image
    <img
      src={`/marketing/${name}-${largest}.webp`}
      srcSet={widths.map(w => `/marketing/${name}-${w}.webp ${w}w`).join(', ')}
      sizes={sizes}
      width={largest}
      height={Math.round(largest * ratio)}
      alt={alt}
      loading={eager ? 'eager' : 'lazy'}
      decoding="async"
      className={className ? `mkt-photo ${className}` : 'mkt-photo'}
    />
  );
}
