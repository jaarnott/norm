/**
 * Stands in for web-only components inside the MCP App bundle (vite.config's
 * webOnly plugin). The invoice-copy pane needs pdf.js and a worker file the
 * sandboxed iframe can't load, and the card never shows it when embedded —
 * inlining ~1.5 MB of pdf.js for a button that isn't there would only make
 * every card slower to open in Claude.
 */
export default function WebOnly(): null {
  return null;
}
