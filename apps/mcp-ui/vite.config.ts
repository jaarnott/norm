import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { viteSingleFile } from 'vite-plugin-singlefile';

// Everything inlines into ONE html file: MCP Apps are served as a single
// resource over JSON-RPC, and the host's sandbox blocks external origins.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: { outDir: 'dist', assetsInlineLimit: 100000000, chunkSizeWarningLimit: 4000 },
});
