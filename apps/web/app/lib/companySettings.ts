/** Settings → Company: the organisation's Functions switch
 *  (organizations.functions_mode) and the choices behind its fields. */

export type FunctionsMode = 'off' | 'shadow' | 'live';

/** Each mode in one line, then what switching to it does (the confirm). */
export const FUNCTIONS_MODES: { id: FunctionsMode; label: string; description: string; consequence: string }[] = [
  {
    id: 'off',
    label: 'Off',
    description: 'Hidden for everyone: no Functions pages and no Functions tools.',
    consequence: 'Its pages and tools disappear for everyone. Nothing is deleted — you can switch it back on at any time.',
  },
  {
    id: 'shadow',
    label: 'Shadow',
    description: 'A read-only copy of your Orbit functions, refreshed each night — nothing can be changed or sent.',
    consequence: 'Everyone in the organisation sees Functions, read-only. Changes, emails and customer pages stay off until it goes live.',
  },
  {
    id: 'live',
    label: 'Live',
    description: 'Norm runs your functions: everyone can create, edit and send, and customer links work.',
    consequence: 'Staff can change records and send emails from Norm, customer links start working, and the nightly import from Orbit stops.',
  },
];

/** The browser's IANA zones, sorted, always including the saved one — an
 *  older browser's list may lack it, and a select must show what is set. */
export function timeZoneOptions(current: string, supported: readonly string[]): string[] {
  const zones = new Set(supported);
  if (current) zones.add(current);
  return [...zones].sort();
}

/** "NZD — New Zealand Dollar" where the browser can name the currency, else
 *  the bare code. */
export function currencyLabel(code: string, names?: { of(code: string): string | undefined }): string {
  let name: string | undefined;
  try {
    name = names?.of(code);
  } catch {
    name = undefined;
  }
  return name && name !== code ? `${code} — ${name}` : code;
}
