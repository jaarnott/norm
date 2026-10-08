'use client';

import type { CSSProperties } from 'react';
import type { LucideIcon } from 'lucide-react';
import Icon, { type IconSize, type IconTone } from './Icon';
import { resolveAppIcon, type AppIconSource } from './appIcons';

/** An app's line icon, wherever an app is listed. Never its stored emoji. */
export default function AppIcon({ app, size = 'menu', tone = 'muted', fallback, style }: {
  app: AppIconSource;
  size?: IconSize;
  tone?: IconTone;
  fallback?: LucideIcon;
  style?: CSSProperties;
}) {
  return <Icon icon={resolveAppIcon(app, fallback)} size={size} tone={tone} style={style} />;
}
