'use client';

import { ArrowLeft } from 'lucide-react';
import Icon from './Icon';

/** "← Recipes" style back navigation, one look everywhere. */
export default function BackLink({ label, onClick, testId, disabled }: { label: string; onClick: () => void; testId?: string; disabled?: boolean }) {
  return (
    <button type="button" className="n-back" onClick={onClick} data-testid={testId} disabled={disabled}>
      <Icon icon={ArrowLeft} size={14} />
      {label}
    </button>
  );
}
