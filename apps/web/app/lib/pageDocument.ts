// The document the active functional-page component wants attached to the next
// chat message, so the agent knows exactly what the user is looking at (e.g. the
// open recipe: its id, venue, and current lines). A display component publishes
// it here; `sendMessage` reads it and folds it into `page_context`. Module scope,
// so it survives the component remount that sending a message triggers.

let current: Record<string, unknown> | null = null;

export function setPageDocument(doc: Record<string, unknown> | null): void {
  current = doc;
}

export function getPageDocument(): Record<string, unknown> | null {
  return current;
}
