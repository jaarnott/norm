/**
 * Turn the single-file artifact build into a page the Artifact tool accepts.
 *
 * Vite emits a complete HTML document. The Artifact tool wraps the file it is
 * given in its own `<!doctype html><head>…</head><body>` skeleton, so the
 * document tags have to come off — what is left is the <style> from <head>
 * followed by the <body> contents, which are legal as body-level content and
 * keep the page self-contained.
 *
 * The <title> is preserved: it names the artifact in the browser tab and in
 * the gallery, and it must stay stable across redeploys.
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, '..', 'dist-artifact', 'artifact.html');
const dest = join(here, '..', 'dist-artifact', 'roster.html');

const html = readFileSync(src, 'utf-8');

// A build that still points at /assets would 404 behind the artifact CSP —
// the failure is silent (an unstyled or dead page), so fail loudly here.
if (html.includes('src="/assets') || html.includes('href="/assets')) {
  throw new Error('Build is not self-contained — external asset refs remain.');
}
// Same for any absolute origin: the CSP blocks every host but claude.ai's own.
const external = html.match(/\b(?:src|href)="https?:\/\/[^"]+"/g);
if (external) {
  throw new Error(`Build references external origins: ${external.join(', ')}`);
}

const title = html.match(/<title>([\s\S]*?)<\/title>/i)?.[1]?.trim();
// vite-plugin-singlefile hoists the inlined bundle into <head>, so the style
// and script blocks are collected from the WHOLE document rather than from
// <body> — taking the body alone silently emitted a 4 KB page with no app.
const styles = [...html.matchAll(/<style[\s\S]*?<\/style>/gi)].map((m) => m[0]);
const scripts = [...html.matchAll(/<script[\s\S]*?<\/script>/gi)].map((m) => m[0]);
const body = html.match(/<body[^>]*>([\s\S]*)<\/body>/i)?.[1];

if (!body) throw new Error('Could not find <body> in the build output.');
if (!styles.length) throw new Error('Build produced no inline <style> — CSS would be missing.');
if (!scripts.length) throw new Error('Build produced no inline <script> — the app would be missing.');

// Scripts go LAST, after #root exists. They are type="module" and so deferred
// anyway, but ordering it correctly means the page does not depend on that.
const page = [title ? `<title>${title}</title>` : null, ...styles, body.trim(), ...scripts]
  .filter(Boolean)
  .join('\n');

mkdirSync(dirname(dest), { recursive: true });
writeFileSync(dest, `${page}\n`);
console.log(`emitted ${dest} (${(page.length / 1024).toFixed(0)} KB)`);
