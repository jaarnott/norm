'use client';

import { use } from 'react';
import AppRunner from '../../components/apps/AppRunner';

export default function AppPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return <AppRunner slug={slug} />;
}
