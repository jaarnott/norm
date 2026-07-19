// Copy the single-file build into the API package, where ui_apps.py serves it
// as the ui://norm/display-block resource. The built HTML is committed so the
// API image needs no JS toolchain.
//
// It also stamps a hash of every source the bundle was built from. The API test
// suite recomputes that hash, so editing RosterTable without rebuilding fails
// CI instead of silently shipping a stale component to Claude.
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..', '..', '..');

// Keep in sync with tests/test_mcp_ui.py::TestBundleFreshness
const SOURCES = [
  'apps/web/app/components/display/GenericTable.tsx',
  'apps/web/app/components/display/RosterEditor.tsx',
  'apps/web/app/components/display/roster/shared.ts',
  'apps/web/app/components/display/roster/WeekGrid.tsx',
  'apps/web/app/components/display/roster/DayTimeline.tsx',
  'apps/web/app/components/display/roster/ShiftModal.tsx',
  'apps/web/app/lib/datetime.ts',
  'apps/web/app/lib/rosterTime.ts',
  'apps/web/app/components/display/roster/grid.ts',
  'apps/mcp-ui/src/registry.ts',
  'apps/mcp-ui/src/main.tsx',
  'apps/api/app/mcp/ui/_bridge.js',
];

function sourceHash() {
  const h = createHash('sha256');
  for (const rel of SOURCES) h.update(readFileSync(join(root, rel)));
  return h.digest('hex');
}

const src = join(here, '..', 'dist', 'index.html');
const dest = join(root, 'apps', 'api', 'app', 'mcp', 'ui', 'display-block.html');

let html = readFileSync(src, 'utf-8');
if (html.includes('src="/assets') || html.includes('href="/assets')) {
  throw new Error('Build is not self-contained — external asset refs remain.');
}
html += `\n<!-- norm-mcp-ui-sources sha256:${sourceHash()} -->\n`;
writeFileSync(dest, html);
console.log(`emitted ${dest} (${(html.length / 1024).toFixed(0)} KB)`);
