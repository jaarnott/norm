'use client';

import type { ButtonHTMLAttributes, ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import Icon from './Icon';

/**
 * The five button roles. Primary is near-black everywhere (one per screen);
 * tan is for selection and links, never a fill under white text. Styling and
 * hover/focus/touch states live in tokens.css (.n-btn) so they reach Claude's
 * MCP bundle too.
 */
export type ButtonVariant = 'primary' | 'secondary' | 'quiet' | 'danger' | 'link';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** xs is for dense review rows (the receive-invoice card); sm and md elsewhere. */
  size?: 'xs' | 'sm' | 'md';
  icon?: LucideIcon;
  children?: ReactNode;
}

export default function Button({ variant = 'secondary', size = 'md', icon, className, type = 'button', children, ...rest }: ButtonProps) {
  const classes = ['n-btn', `n-btn--${variant}`, size === 'md' ? '' : `n-btn--${size}`, className ?? ''].filter(Boolean).join(' ');
  return (
    <button type={type} className={classes} {...rest}>
      {icon && <Icon icon={icon} size={size === 'md' ? 16 : size === 'sm' ? 14 : 12} strokeWidth={variant === 'primary' ? 2 : 1.75} />}
      {children}
    </button>
  );
}
