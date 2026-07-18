/**
 * The display-block MCP App.
 *
 * One app for every tool that maps to a Norm display component. The server
 * sends `{component, data, props}` as structuredContent; we look the component
 * up in the sandbox-safe registry and render the real thing.
 *
 * The postMessage protocol is NOT reimplemented here — it imports the same
 * _bridge.js the hand-written apps use, so there is one handshake to maintain.
 */
import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { REGISTRY, type BlockProps } from './registry';
// Side-effect import: defines window.NormApp.
import '../../api/app/mcp/ui/_bridge.js';

declare global {
  interface Window {
    NormApp: {
      onResult: (cb: (params: ToolResult) => void) => void;
      callTool: (name: string, args?: unknown) => Promise<unknown>;
      openLink: (url: string) => void;
      reportSize: () => void;
      truncationMessage: (d: unknown) => string | null;
      unwrap: (d: unknown) => unknown;
    };
  }
}

interface ToolResult {
  structuredContent?: Record<string, unknown>;
  content?: { type: string; text?: string }[];
  isError?: boolean;
}

interface Block {
  component: string;
  data: Record<string, unknown>;
  props?: Record<string, unknown>;
}

function readBlock(params: ToolResult): { block?: Block; error?: string } {
  if (params?.isError) return { error: 'Norm returned an error for this request.' };

  let payload: unknown = params?.structuredContent;
  if (payload == null && Array.isArray(params?.content)) {
    const t = params.content.find((c) => c?.type === 'text');
    if (t?.text) { try { payload = JSON.parse(t.text); } catch { /* not json */ } }
  }
  if (payload == null) return { error: 'No data to display.' };

  const trunc = window.NormApp.truncationMessage(payload);
  if (trunc) return { error: trunc };

  const p = payload as Record<string, unknown>;
  // The server wraps display-block tools; anything else is raw data we can
  // still show as a table.
  if (typeof p.component === 'string' && p.data && typeof p.data === 'object') {
    return { block: { component: p.component, data: p.data as Record<string, unknown>,
                      props: p.props as Record<string, unknown> | undefined } };
  }
  return { block: { component: 'generic_table', data: window.NormApp.unwrap(p) as Record<string, unknown> } };
}

function App() {
  const [block, setBlock] = useState<Block | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    window.NormApp.onResult((params) => {
      const { block: b, error: e } = readBlock(params);
      if (e) { setError(e); setBlock(null); } else { setBlock(b!); setError(null); }
    });
  }, []);

  // Re-report height whenever what we drew changes.
  useEffect(() => { window.NormApp.reportSize(); }, [block, error]);

  if (error) return <p className="err">{error}</p>;
  if (!block) return <p className="err">Loading…</p>;

  const Component = REGISTRY[block.component];
  if (!Component) {
    // Never leave a blank card: fall back to the universal table.
    const Fallback = REGISTRY.generic_table;
    return <Fallback data={block.data} props={block.props} />;
  }
  const props: BlockProps = { data: block.data, props: block.props };
  return <Component {...props} />;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode><App /></StrictMode>,
);
