import { resolve } from 'node:path';
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { viteSingleFile } from 'vite-plugin-singlefile';

// A SEPARATE config from vite.config.ts on purpose. That file is listed in
// emit.mjs SOURCES, so its bytes are hashed into display-block.html — editing
// it to add a second target would mark the committed MCP App bundle stale and
// force it to be rebuilt for a change that cannot affect it. The artifact
// build shares the components, not the build definition.
const ARTIFACT_API = resolve(__dirname, 'src/artifact-api.ts');
const WEB_API = resolve(__dirname, '../web/app/lib/api.ts');

// Same swap as the sandbox build, different destination: apps/web's `lib/api`
// speaks to Norm's REST API with a session token, which an artifact does not
// have. Match on the RESOLVED path so every relative-import spelling is caught.
function artifactApi(): Plugin {
  return {
    name: 'norm-artifact-api',
    enforce: 'pre',
    async resolveId(source, importer) {
      if (!importer || importer === ARTIFACT_API) return null;
      if (!/\/lib\/api(\.ts)?$/.test(source)) return null;
      const resolved = await this.resolve(source, importer, { skipSelf: true });
      return resolved && resolved.id === WEB_API ? ARTIFACT_API : null;
    },
  };
}

// One file, no external origins: claude.ai serves artifacts under a strict CSP
// that blocks every other host — no CDN, no font, no fetch.
export default defineConfig({
  plugins: [artifactApi(), react(), viteSingleFile()],
  build: {
    outDir: 'dist-artifact',
    rollupOptions: { input: resolve(__dirname, 'artifact.html') },
    assetsInlineLimit: 100000000,
    chunkSizeWarningLimit: 4000,
  },
});
