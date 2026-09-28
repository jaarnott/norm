'use client';

import { use } from 'react';
import AppRunner from '../../components/apps/AppRunner';

/**
 * /apps/<slug> — one app, full page. The query string is where to start:
 * `?job=Head%20Chef` hands the app its component's inputs, `component=` picks
 * a component when there are several — so a link can open Hiring on a pipeline
 * the same way Norm's open_app card does.
 */
export default function AppPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { slug } = use(params);
  const query = use(searchParams);
  const inputs: Record<string, string> = {};
  for (const [k, v] of Object.entries(query)) {
    if (k !== 'component' && typeof v === 'string' && v) inputs[k] = v;
  }
  const component = typeof query.component === 'string' ? query.component : undefined;
  return <AppRunner slug={slug} component={component} inputs={inputs} />;
}
