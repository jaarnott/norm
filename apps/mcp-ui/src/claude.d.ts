/**
 * The slice of `window.claude` this artifact uses.
 *
 * The authoritative definitions are the platform-served ones for the runtime
 * contract the page is published against; this is a deliberately small local
 * copy so `tsc --noEmit` can check the call sites. Keep it a SUBSET — widening
 * it here does not widen what the runtime provides.
 *
 * The member is optional by design: MCP is a per-view grant, so
 * `window.claude.mcp` can be absent on an older runtime generation.
 * `window.claude.mcp !== undefined` is the availability gate; probing with a
 * call is not (it can throw synchronously in an unserved frame).
 */
declare namespace Claude {
  namespace mcp {
    interface McpError {
      code: string;
      server?: string;
      message: string;
      retryable?: boolean;
      retryAfterMs?: number;
      result?: unknown;
    }

    interface CallToolResult {
      content: { type: string; [k: string]: unknown }[];
      structuredContent?: unknown;
      /** The JSON answer — read this rather than digging through `content`. */
      payload?: unknown;
      /** Present only on a result served from cache. `storedAt` is when that
       *  result was produced; drive "as at" indicators from it, not the clock. */
      cache?: { storedAt: number; revalidating: boolean };
    }

    type WatchEvent =
      | { type: 'data'; result: CallToolResult }
      | { type: 'error'; error: McpError };

    type Unsubscribe = () => void;

    interface CacheOptions {
      staleTime?: number;
      gcTime?: number;
      refresh?: boolean;
    }

    function callTool(
      server: string,
      tool: string,
      input?: unknown,
      options?: { cache?: false | CacheOptions; signal?: AbortSignal },
    ): Promise<CallToolResult>;

    function watchTool(
      server: string,
      tool: string,
      input: unknown,
      handler: (ev: WatchEvent) => void,
      options?: { cache?: { staleTime?: number; gcTime?: number }; refetchInterval?: number },
    ): Unsubscribe;

    function invalidate(server?: string, tool?: string, input?: unknown): Promise<void>;
  }
}

interface Claude {
  mcp?: typeof Claude.mcp;
}

interface Window {
  claude: Claude;
}
