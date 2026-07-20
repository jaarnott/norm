// window.NormApp is defined by _bridge.js (side-effect import in main.tsx).
// One declaration, shared by every module in the bundle — main.tsx and
// sandbox-api.ts must not re-declare it or the types fork.
declare interface Window {
  NormApp: {
    onResult: (cb: (params: {
      structuredContent?: Record<string, unknown>;
      content?: { type: string; text?: string }[];
      isError?: boolean;
    }) => void) => void;
    callTool: (name: string, args?: unknown) => Promise<unknown>;
    openLink: (url: string) => void;
    reportSize: () => void;
    truncationMessage: (d: unknown) => string | null;
    unwrap: (d: unknown) => unknown;
  };
}
