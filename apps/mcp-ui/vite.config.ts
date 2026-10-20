import { resolve } from 'node:path';
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { viteSingleFile } from 'vite-plugin-singlefile';

// The web app's `lib/api` talks to Norm's REST API with a session token —
// meaningless inside a sandboxed MCP App iframe. Swap it for the shim that
// routes the same calls through the MCP host (tools/call). Matching on the
// RESOLVED path (not the import specifier) catches every relative-import
// spelling in apps/web.
const SANDBOX_API = resolve(__dirname, 'src/sandbox-api.ts');
const WEB_API = resolve(__dirname, '../web/app/lib/api.ts');

function sandboxApi(): Plugin {
  return {
    name: 'norm-sandbox-api',
    enforce: 'pre',
    async resolveId(source, importer) {
      if (!importer || importer === SANDBOX_API) return null;
      if (!/\/lib\/api(\.ts)?$/.test(source)) return null;
      const resolved = await this.resolve(source, importer, { skipSelf: true });
      return resolved && resolved.id === WEB_API ? SANDBOX_API : null;
    },
  };
}

// Everything inlines into ONE html file: MCP Apps are served as a single
// resource over JSON-RPC, and the host's sandbox blocks external origins.
export default defineConfig({
  plugins: [sandboxApi(), react(), viteSingleFile()],
  build: { outDir: 'dist', assetsInlineLimit: 100000000, chunkSizeWarningLimit: 4000 },
});
