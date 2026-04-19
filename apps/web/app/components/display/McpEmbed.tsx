'use client';

import { useEffect, useRef, useState, useMemo } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';

/**
 * Renders an iframe for MCP resource embeds (e.g., Orbit Marketing calendar).
 *
 * Handles:
 * - Iframe rendering with the embed URL
 * - postMessage bridge (resize, action, navigate, state_update)
 * - Theme token injection via URL params
 * - Container hint layout (full_page, inline_card)
 */
export default function McpEmbed({ data, props, onAction }: DisplayBlockProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(400);
  const [navigatedSrc, setNavigatedSrc] = useState<string | null>(null);

  // URL can come from data.url (display block from tool loop) or data._embed[0].url (page load)
  const embedList = data?._embed as Array<Record<string, unknown>> | undefined;
  const url = (data?.url as string) || (embedList?.[0]?.url as string) || '';
  const containerHint = (props?.container_hint as string)
    || (embedList?.[0]?.container_hint as string)
    || 'inline_card';
  const connectorName = (props?.connector_name as string) || '';

  // Build the embed URL with theme tokens (pure computation, no side effects)
  const themedSrc = useMemo(() => {
    if (!url) return null;
    try {
      const embedUrl = new URL(url);
      const tokens: Record<string, string> = {
        'norm-primary': '#c4a882',
        'norm-mode': 'light',
        'norm-radius': '8px',
        'norm-font-family': '-apple-system, BlinkMacSystemFont, sans-serif',
      };
      for (const [key, value] of Object.entries(tokens)) {
        if (value && !embedUrl.searchParams.has(key)) {
          embedUrl.searchParams.set(key, value);
        }
      }
      return embedUrl.toString();
    } catch {
      return url;
    }
  }, [url]);

  // Use navigated src if set (from postMessage navigate), otherwise themed initial src
  const iframeSrc = navigatedSrc ?? themedSrc;

  // Listen for postMessage from the embed
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      const msg = event.data;
      if (!msg || msg.source !== 'orbit-embed') return;

      switch (msg.type) {
        case 'resize':
          if (msg.payload?.height && typeof msg.payload.height === 'number') {
            setHeight(Math.min(msg.payload.height, 1200));
          }
          break;

        case 'action':
          if (onAction && msg.payload?.action) {
            onAction({
              type: 'mcp_action',
              connector_name: connectorName,
              action: msg.payload.action,
              params: msg.payload,
            });
          }
          break;

        case 'navigate':
          if (msg.payload?.target && iframeSrc) {
            try {
              const baseUrl = new URL(iframeSrc);
              const origin = baseUrl.origin;
              const newPath = `/embed/marketing/${msg.payload.target}`;
              const newUrl = new URL(newPath, origin);
              // Carry over venue_id and theme params
              for (const [k, v] of baseUrl.searchParams.entries()) {
                newUrl.searchParams.set(k, v);
              }
              // Add target-specific params
              if (msg.payload.params) {
                for (const [k, v] of Object.entries(msg.payload.params)) {
                  newUrl.searchParams.set(k, String(v));
                }
              }
              setNavigatedSrc(newUrl.toString());
            } catch { /* ignore invalid navigate */ }
          }
          break;

        case 'state_update':
          // Data changed in the embed — could refresh or notify
          break;
      }
    };

    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [onAction, connectorName, iframeSrc]);

  // Don't render until we have a valid src
  if (!iframeSrc) return null;

  const isFullPage = containerHint === 'full_page';

  return (
    <div style={{
      width: '100%',
      borderRadius: isFullPage ? 0 : 12,
      overflow: 'hidden',
      border: isFullPage ? 'none' : '1px solid #e5e5e5',
      backgroundColor: '#fff',
    }}>
      <iframe
        ref={iframeRef}
        src={iframeSrc}
        style={{
          width: '100%',
          height: isFullPage ? Math.max(height, 600) : Math.min(height, 500),
          border: 'none',
          display: 'block',
        }}
        sandbox="allow-scripts allow-same-origin allow-popups allow-forms"
        loading="lazy"
        title={`${connectorName} embed`}
      />
    </div>
  );
}
