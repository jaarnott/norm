'use client';

/** Settings → Templates: pre-order menu templates and reply templates. */
import { useState } from 'react';
import type { FnMeta } from '../shared/meta';
import MenuTemplates from './MenuTemplates';
import ReplyTemplates from './ReplyTemplates';
import { Segmented } from './ui';

export type TemplateKind = 'menus' | 'replies';

export default function TemplatesTab({ meta, venueId, initialKind = 'menus', onKindChange }: {
  meta: FnMeta;
  venueId: string;
  initialKind?: TemplateKind;
  onKindChange?: (kind: TemplateKind) => void;
}) {
  const [kind, setKind] = useState<TemplateKind>(initialKind);
  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Segmented
          label="Template type"
          items={[{ id: 'menus', label: 'Pre-order menus' }, { id: 'replies', label: 'Reply templates' }]}
          value={kind}
          onChange={(k) => { setKind(k); onKindChange?.(k); }}
        />
      </div>
      {kind === 'menus'
        ? <MenuTemplates key={venueId} venueId={venueId} meta={meta} canWrite={meta.can_write} />
        : <ReplyTemplates key={venueId} venueId={venueId} meta={meta} canWrite={meta.can_write} />}
    </div>
  );
}
