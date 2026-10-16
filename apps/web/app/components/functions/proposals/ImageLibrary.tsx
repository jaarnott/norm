'use client';

/**
 * The venue's proposal image library: pick an image to insert (as
 * `/api/public/files/<id>`, the only image URL a proposal keeps), or upload
 * one into the library first.
 */
import { useEffect, useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import Button from '../../ui/Button';
import Dialog from '../../ui/Dialog';
import PageState from '../../ui/PageState';
import { fnGet, fnUpload } from '../shared/api';
import type { FnVenue } from '../shared/meta';
import { errorText, Notice, ReadOnlyHint } from '../inbox/bits';
import { PUBLIC_FILES } from './proposalDoc';
import type { LibraryImage } from './types';

export default function ImageLibrary({
  venues,
  venueId: initialVenue,
  canWrite,
  onPick,
  onClose,
}: {
  venues: FnVenue[];
  venueId: string | null;
  canWrite: boolean;
  onPick: (image: LibraryImage) => void;
  onClose: () => void;
}) {
  const [venueId, setVenueId] = useState(initialVenue ?? venues[0]?.id ?? '');
  const [tick, setTick] = useState(0);
  const key = `${venueId}|${tick}`;
  const [loaded, setLoaded] = useState<{ key: string; rows?: LibraryImage[]; error?: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const venue = key.slice(0, key.indexOf('|'));
    if (!venue) return;
    let alive = true;
    fnGet<{ rows: LibraryImage[] }>(`/venues/${encodeURIComponent(venue)}/images`)
      .then((r) => alive && setLoaded({ key, rows: r.rows }))
      .catch((err) => alive && setLoaded({ key, error: errorText(err) }));
    return () => {
      alive = false;
    };
  }, [key]);

  const upload = async (file: File) => {
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const image = await fnUpload<LibraryImage>(`/venues/${encodeURIComponent(venueId)}/images/upload`, form);
      setTick((t) => t + 1);
      onPick(image);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setUploading(false);
    }
  };

  const rows = loaded?.key === key ? loaded.rows : undefined;
  return (
    <Dialog title="Insert an image" width={640} onClose={onClose} closeButton>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        {venues.length > 1 && (
          <select className="n-select" aria-label="Venue library" value={venueId} onChange={(e) => setVenueId(e.target.value)}>
            {venues.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        )}
        <input
          ref={fileInput}
          type="file"
          accept="image/jpeg,image/png,image/gif,image/webp"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) upload(file);
          }}
        />
        <Button icon={Upload} onClick={() => fileInput.current?.click()} disabled={!canWrite || uploading || !venueId}>
          {uploading ? 'Uploading…' : 'Upload an image'}
        </Button>
        {!canWrite && <ReadOnlyHint />}
      </div>
      {error && (
        <div style={{ marginBottom: 12 }}>
          <Notice tone="error">{error}</Notice>
        </div>
      )}
      {loaded?.key === key && loaded.error ? (
        <PageState kind="error" title="Couldn’t load the library" detail={loaded.error} />
      ) : !rows ? (
        <PageState kind="loading" title="Loading images…" />
      ) : rows.length === 0 ? (
        <PageState kind="empty" title="No images in this venue’s library yet" detail="Upload one — JPEG, PNG, GIF or WebP, up to 10 MB." />
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 10 }}>
          {rows.map((img) => (
            <li key={img.id}>
              <button
                type="button"
                onClick={() => onPick(img)}
                title={img.caption || img.name || undefined}
                style={{
                  display: 'block',
                  width: '100%',
                  padding: 0,
                  border: '1px solid var(--line)',
                  borderRadius: 'var(--radius)',
                  background: 'var(--surface)',
                  overflow: 'hidden',
                  cursor: 'pointer',
                  textAlign: 'left',
                  font: 'inherit',
                }}
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- Norm's own public file route; no optimiser for it */}
                <img
                  src={`${PUBLIC_FILES}${encodeURIComponent(img.file_id)}`}
                  alt={img.name || ''}
                  loading="lazy"
                  style={{ display: 'block', width: '100%', height: 100, objectFit: 'cover', background: 'var(--surface-alt)' }}
                />
                <span style={{ display: 'block', padding: '6px 8px', fontSize: 'var(--fs-xs)', color: 'var(--text-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {img.name || 'Untitled'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
