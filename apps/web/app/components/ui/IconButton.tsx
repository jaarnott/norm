'use client';

import type { ButtonHTMLAttributes } from 'react';
import type { LucideIcon } from 'lucide-react';
import Icon from './Icon';

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  icon: LucideIcon;
  /** Required: an icon-only button needs a name for screen readers. */
  label: string;
  iconSize?: number;
}

/** An icon-only button (close, collapse, more…): 32px target, 40px on touch. */
export default function IconButton({ icon, label, iconSize = 18, className, type = 'button', ...rest }: IconButtonProps) {
  return (
    <button type={type} aria-label={label} title={label} className={['n-icon-btn', className ?? ''].filter(Boolean).join(' ')} {...rest}>
      <Icon icon={icon} size={iconSize} />
    </button>
  );
}
