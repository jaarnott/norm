'use client';

/**
 * The venue's proposal image library: photos of the spaces, food and drinks
 * that proposals (and the brand template) can use. An image is referenced in
 * proposal HTML by its public address, `/api/public/files/<file id>` — the
 * "Copy URL" button gives exactly that; other image addresses are stripped
 * when a proposal is saved.
 */
import { useId, useState } from 'react';
import { Pencil, Trash2 } from 'lucide-react';
import Button from '../../ui/Button';
import IconButton from '../../ui/IconButton';
import PageState from '../../ui/PageState';
import { ConfirmDialog } from '../../ui/Dialog';
import { fnSend } from '../shared/api';
import { errorText } from './errors';
import { uploadForm } from './upload';
import type { LibraryImage } from './types';
import { AuthImage, CopyButton, Notice, Panel, ReadOnlyNote, Section, UploadButton, UploadList, help, useUploads } from './ui';

export const publicFileUrl = (fileId: string) => `/api/public/files/${fileId}`;

export default function ImageLibrary({ venueId, images, canWrite, setImages }: {
  venueId: string;
  images: LibraryImage[];
  canWrite: boolean;
  setImages: (update: (list: LibraryImage[]) => LibraryImage[]) => void;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<LibraryImage | null>(null);
  const id = useId();

  const uploads = useUploads(async (file, onProgress) => {
    const form = new FormData();
    form.append('file', file, file.name);
    const image = await uploadForm<LibraryImage>(`/venues/${venueId}/images/upload`, form, onProgress);
    setImages((list) => [...list, image].sort((a, b) => a.name.localeCompare(b.name)));
  });

  const rename = async (img: LibraryImage) => {
    const next = name.trim();
    if (!next || next === img.name) {
      setRenaming(null);
      return;
    }
    setError(null);
    try {
      const saved = await fnSend<LibraryImage>('PATCH', `/images/${img.id}`, { version: img.version, name: next });
      setImages((list) => list.map((x) => (x.id === saved.id ? saved : x)));
      setRenaming(null);
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <Section
      title="Image library"
      meta={`${images.length} image${images.length === 1 ? '' : 's'} for proposals · JPEG, PNG, GIF or WebP up to 10 MB`}
      actions={canWrite ? <UploadButton label="Upload images" onFiles={uploads.start} /> : <ReadOnlyNote />}
    >
      <UploadList items={uploads.items} onDismiss={uploads.dismiss} />
      {error && <Notice tone="error" style={{ marginTop: 8 }}>{error}</Notice>}
      {images.length === 0 && !uploads.items.length ? (
        <Panel><PageState kind="empty" title="No images yet" detail="Upload photos of the venue, its food and drinks to use in proposals." /></Panel>
      ) : (
        <ul style={{ listStyle: 'none', margin: '10px 0 0', padding: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 12 }}>
          {images.map((img) => (
            <li key={img.id} className="n-card" style={{ padding: 8, minWidth: 0 }}>
              <AuthImage fileId={img.file_id} alt={img.caption || img.name} width="100%" height={110} />
              {renaming === img.id ? (
                <div style={{ marginTop: 8 }}>
                  <label className="n-label" htmlFor={`${id}-${img.id}`}>Name</label>
                  <input
                    id={`${id}-${img.id}`}
                    className="n-input"
                    style={{ width: '100%' }}
                    value={name}
                    autoFocus
                    onChange={(e) => setName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') rename(img);
                      if (e.key === 'Escape') setRenaming(null);
                    }}
                  />
                  <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                    <Button size="sm" onClick={() => rename(img)}>Save</Button>
                    <Button size="sm" variant="quiet" onClick={() => setRenaming(null)}>Cancel</Button>
                  </div>
                </div>
              ) : (
                <>
                  <div title={img.name} style={{ marginTop: 6, fontSize: 'var(--fs-sm)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {img.name}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 2, marginTop: 4 }}>
                    <CopyButton text={publicFileUrl(img.file_id)} label="Copy URL" variant="quiet" />
                    {canWrite && (
                      <span style={{ marginLeft: 'auto', display: 'inline-flex' }}>
                        <IconButton icon={Pencil} iconSize={16} label={`Rename ${img.name}`} onClick={() => { setRenaming(img.id); setName(img.name); }} />
                        <IconButton icon={Trash2} iconSize={16} label={`Delete ${img.name}`} onClick={() => setDeleting(img)} />
                      </span>
                    )}
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      <p style={{ ...help, marginTop: 10 }}>
        In a proposal or the brand template, use the copied address as <code>&lt;img src=&quot;…&quot;&gt;</code>.
      </p>
      {deleting && (
        <ConfirmDialog
          title={`Remove ${deleting.name}?`}
          confirmLabel="Remove image"
          busyLabel="Removing…"
          danger
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            try {
              await fnSend('DELETE', `/images/${deleting.id}`);
              setImages((list) => list.filter((x) => x.id !== deleting.id));
            } catch (e) {
              throw new Error(errorText(e));
            }
          }}
        >
          <p style={{ margin: 0 }}>It leaves the library. Proposals already using it keep showing it.</p>
        </ConfirmDialog>
      )}
    </Section>
  );
}
