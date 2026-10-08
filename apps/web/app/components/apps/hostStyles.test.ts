import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { HOST_CSS, HOST_TOKENS } from './hostStyles';

/** The `:root { … }` custom properties of styles/tokens.css. */
function webTokens(): Record<string, string> {
  const css = readFileSync(join(__dirname, '../../styles/tokens.css'), 'utf-8');
  const block = css.slice(css.indexOf(':root {'), css.indexOf('}', css.indexOf(':root {')));
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim().toLowerCase();
  return out;
}

describe('the style contract apps get from their host', () => {
  it('uses the same palette, scale and radii as the web app', () => {
    const web = webTokens();
    for (const [name, value] of Object.entries(HOST_TOKENS)) {
      expect(web[name], `--${name} is not in tokens.css`).toBeDefined();
      expect(value.toLowerCase(), `--norm-${name} has drifted from tokens.css`).toBe(web[name]);
    }
  });

  it('keeps element defaults at zero specificity, so an app’s own CSS wins', () => {
    // Every bare-element rule is wrapped in :where(); a bare `body {` or
    // `button {` would outrank an app that styles them by element too.
    expect(HOST_CSS).not.toMatch(/(^|[\s}])(body|button|input|table|h1|a)\s*\{/);
    expect(HOST_CSS).toContain(':where(body)');
  });
});
