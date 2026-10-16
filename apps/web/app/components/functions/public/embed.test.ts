/**
 * The static embeds (public/embed/forms.js, track.js) run on venue websites,
 * outside the bundler, so these tests load the shipped files themselves into
 * a bare sandbox — what passes here is what a venue site runs.
 *
 * The validation messages are Norm's own (services/functions/forms.py
 * validate_answers): the form must refuse exactly what the server refuses,
 * in the same words, so a person never sees one message in the browser and
 * another after sending.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';

const EMBED_DIR = fileURLToPath(new URL('../../../../public/embed/', import.meta.url));
const FORMS_JS = readFileSync(`${EMBED_DIR}forms.js`, 'utf8');
const TRACK_JS = readFileSync(`${EMBED_DIR}track.js`, 'utf8');
const KEY = 'norm_attr_v1';
const DAY = 24 * 60 * 60 * 1000;

type Field = {
  key: string;
  label: string;
  type: string;
  required?: boolean;
  options?: unknown;
};

interface NormForms {
  version: string;
  validate(fields: Field[], values: Record<string, unknown>): Record<string, string>;
  serialise(fields: Field[], values: Record<string, unknown>, honeypot?: string, honeypotValue?: string): Record<string, unknown>;
  themeVars(theme: unknown): [string, string, string][];
  attribution(): Record<string, string>;
}

type Store = {
  data: Map<string, string>;
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
  removeItem(k: string): void;
};

function storage(initial: Record<string, string> = {}): Store {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (k) => (data.has(k) ? data.get(k)! : null),
    setItem: (k, v) => void data.set(k, String(v)),
    removeItem: (k) => void data.delete(k),
  };
}

const PAGE_KEYS = ['location', 'localStorage', 'history', 'addEventListener'];

/** Run an embed in a sandbox that looks enough like a venue page. */
function run(source: string, opts: { href?: string; referrer?: string; store?: Store } = {}) {
  const href = opts.href ?? 'https://venue.example/functions';
  const url = new URL(href);
  const store = opts.store ?? storage();
  const document = {
    readyState: 'complete',
    referrer: opts.referrer ?? '',
    currentScript: { src: 'https://bettercallnorm.com/embed/forms.js' },
    querySelectorAll: () => [],
    getElementsByTagName: () => [],
    getElementById: () => null,
    addEventListener: () => undefined,
  };
  const window: Record<string, unknown> = {
    location: { href, search: url.search, host: url.host },
    localStorage: store,
    history: { pushState() {}, replaceState() {} },
    addEventListener: () => undefined,
  };
  runInNewContext(source, { window, document });
  return { window, store };
}

const forms = () => run(FORMS_JS).window.NormForms as NormForms;
/** Values made inside the sandbox, as plain data of this realm. */
const plain = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

const FIELDS: Field[] = [
  { key: 'contact_section', label: 'contact_section', type: 'section' },
  { key: 'name', label: 'Name', type: 'name', required: true },
  { key: 'email', label: 'Email', type: 'email', required: true },
  { key: 'phone', label: 'Phone', type: 'phone' },
  { key: 'company', label: 'Company', type: 'text' },
  { key: 'occasion', label: 'Occasion', type: 'long_text', required: true },
  { key: 'guests', label: 'Guests', type: 'number', required: true, options: { min: 2, max: 400 } },
  { key: 'event_date', label: 'Event date', type: 'date' },
  { key: 'dates', label: 'Preferred dates', type: 'dates' },
  { key: 'times', label: 'Start / finish', type: 'time_range', required: true },
  {
    key: 'kind',
    label: 'Type of event',
    type: 'choice',
    required: true,
    options: [
      { value: 'seated_dining', label: 'Seated dining' },
      { value: 'group_booking', label: 'Group booking' },
    ],
  },
  { key: 'extras', label: 'Extras', type: 'checkbox', options: [{ value: 'dj', label: 'DJ' }, { value: 'av', label: 'A/V' }] },
  { key: 'terms', label: 'I agree to the terms', type: 'checkbox', required: true },
];

const GOOD = {
  name: { first: ' Casey ', last: 'Client' },
  email: ' Casey@Example.com ',
  phone: '021 555 0101',
  company: '',
  occasion: 'A 40th',
  guests: '60',
  event_date: '2026-12-04',
  dates: ['2026-12-04', '', '2026-12-05', '2026-12-04'],
  times: { start: '19:00', end: '23:00' },
  kind: 'group_booking',
  extras: ['dj'],
  terms: true,
};

describe('forms.js validate — mirrors forms.validate_answers', () => {
  it('accepts a complete form', () => {
    expect(plain(forms().validate(FIELDS, GOOD))).toEqual({});
  });

  it("says what is missing in the server's words", () => {
    const errors = plain(forms().validate(FIELDS, { name: { first: '', last: ' ' }, terms: false }));
    expect(errors).toEqual({
      name: 'Name is required.',
      email: 'Email is required.',
      occasion: 'Occasion is required.',
      guests: 'Guests is required.',
      times: 'Start / finish is required.',
      kind: 'Type of event is required.',
      terms: 'I agree to the terms must be ticked.',
    });
  });

  it('treats whitespace as blank', () => {
    expect(plain(forms().validate(FIELDS, { ...GOOD, occasion: '   ' })).occasion).toBe('Occasion is required.');
  });

  it('checks each type the way the server does', () => {
    const errors = plain(
      forms().validate(FIELDS, {
        ...GOOD,
        email: 'casey@example',
        phone: 'call me',
        guests: '1',
        event_date: '2026-02-30',
        dates: ['2026-12-04', 'next week'],
        times: { start: '7pm', end: '23:00' },
        kind: 'wedding',
        extras: ['fireworks'],
      }),
    );
    expect(errors).toEqual({
      email: 'Email is not an email address.',
      phone: 'Phone is not a phone number.',
      guests: 'Guests is too small.',
      event_date: 'Event date needs a date like 2026-12-04.',
      dates: 'Preferred dates needs a date like 2026-12-04.',
      times: 'Start / finish needs times like 19:30.',
      kind: 'Type of event is not one of the choices.',
      extras: 'Extras is not one of the choices.',
    });
  });

  it('needs both ends of a required time range', () => {
    expect(plain(forms().validate(FIELDS, { ...GOOD, times: { start: '19:00', end: '' } })).times).toBe(
      'Start / finish needs a start and an end time.',
    );
  });

  it('enforces the text limits', () => {
    const errors = plain(
      forms().validate(FIELDS, { ...GOOD, company: 'x'.repeat(501), occasion: 'y'.repeat(5001), phone: '1'.repeat(41) }),
    );
    expect(errors).toEqual({
      company: 'Company is too long.',
      occasion: 'Occasion is too long.',
      phone: 'Phone is too long.',
    });
  });

  it('caps numbers and the count of dates like the server', () => {
    const NUM: Field[] = [{ key: 'n', label: 'N', type: 'number' }, { key: 'd', label: 'D', type: 'dates' }];
    expect(plain(forms().validate(NUM, { n: '2e9' }))).toEqual({ n: 'N must be a number.' });
    const days = (n: number) => Array.from({ length: n }, (_, i) => new Date(Date.UTC(2027, 0, 1 + i)).toISOString().slice(0, 10));
    expect(plain(forms().validate(NUM, { d: days(20) }))).toEqual({});
    expect(plain(forms().validate(NUM, { d: days(21) })).d).toBe('D has too many dates.');
  });

  it('applies the size caps before the format checks, as the server does', () => {
    const errors = plain(
      forms().validate(FIELDS, {
        ...GOOD,
        name: { first: 'x'.repeat(101), last: 'Client' },
        email: `${'a'.repeat(250)}@example.com`,
        event_date: '2026-12-04T19:00',
        times: { start: '19:00:00:00', end: '23:00' },
      }),
    );
    expect(errors).toEqual({
      name: 'Name is too long.',
      email: 'Email is too long.',
      event_date: 'Event date is too long.',
      times: 'Start / finish is too long.',
    });
  });
});

describe('forms.js serialise — the submit payload', () => {
  it("sends answered fields in the API's shapes, plus the honeypot", () => {
    expect(plain(forms().serialise(FIELDS, GOOD, 'website', ''))).toEqual({
      name: { first: 'Casey', last: 'Client' },
      email: 'Casey@Example.com',
      phone: '021 555 0101',
      occasion: 'A 40th',
      guests: 60,
      event_date: '2026-12-04',
      dates: ['2026-12-04', '2026-12-05'],
      times: { start: '19:00', end: '23:00' },
      kind: 'group_booking',
      extras: ['dj'],
      terms: true,
      website: '',
    });
  });

  it('leaves out blank answers and keeps a lone tick box as a boolean', () => {
    expect(plain(forms().serialise(FIELDS, { terms: false }, 'hp_1', 'spam'))).toEqual({ terms: false, hp_1: 'spam' });
  });

  it('never sends a section', () => {
    expect(Object.keys(plain(forms().serialise(FIELDS, { contact_section: 'x' })))).not.toContain('contact_section');
  });
});

describe('forms.js themeVars — theme to custom properties', () => {
  it("maps Orbit's imported camelCase style profile", () => {
    const vars = plain(
      forms().themeVars({ brand_color: '#49556A', inputRadius: 999, buttonTransform: 'uppercase', sectionTitleSpacing: 0.08, sectionTitleMarginTop: '20' }),
    );
    expect(vars).toEqual(
      expect.arrayContaining([
        ['--nf-button-bg', '#49556A', 'color'],
        ['--nf-accent', '#49556A', 'color'],
        ['--nf-input-radius', '999px', 'border-radius'],
        ['--nf-button-transform', 'uppercase', 'text-transform'],
        ['--nf-section-spacing', '0.08em', 'letter-spacing'],
        ['--nf-section-mt', '20px', 'margin-top'],
      ]),
    );
  });

  it('lets a specific key win over a shorthand', () => {
    const vars = plain(forms().themeVars({ primary: '#111111', button_background: '#222222' }));
    const button = vars.filter(([name]) => name === '--nf-button-bg').map(([, value]) => value);
    expect(button[button.length - 1]).toBe('#222222');
  });

  it("ends a brand font list on a generic family, so a font the page lacks isn't the browser's serif", () => {
    const vars = plain(forms().themeVars({ sectionTitleFont: 'BlackRunters', bodyFont: 'Georgia, serif' }));
    expect(vars).toEqual(
      expect.arrayContaining([
        ['--nf-section-font', 'BlackRunters, sans-serif', 'font-family'],
        ['--nf-font', 'Georgia, serif', 'font-family'],
      ]),
    );
  });

  it('drops anything that could escape a declaration, and unknown keys', () => {
    expect(plain(forms().themeVars({ primary: 'red;}body{display:none', foreground: { x: 1 }, made_up: 'x', radius: '8px' }))).toEqual([
      ['--nf-input-radius', '8px', 'border-radius'],
      ['--nf-button-radius', '8px', 'border-radius'],
    ]);
  });
});

describe('first-touch attribution — track.js and forms.js agree', () => {
  // Both embeds capture with the same rules and share the storage key.
  const readers: [string, (opts: Parameters<typeof run>[1]) => Record<string, string>][] = [
    [
      'track.js',
      (opts) => {
        const { window } = run(TRACK_JS, opts);
        return plain((window.NormTrack as { get(): Record<string, string> }).get());
      },
    ],
    ['forms.js', (opts) => plain((run(FORMS_JS, opts).window.NormForms as NormForms).attribution())],
  ];

  for (const [name, read] of readers) {
    describe(name, () => {
      it('stores an ad click with its landing page and referrer', () => {
        const store = storage();
        const got = read({
          href: 'https://venue.example/functions?gclid=abc123&utm_source=google&utm_medium=cpc',
          referrer: 'https://www.google.com/',
          store,
        });
        expect(got).toEqual({
          gclid: 'abc123',
          utm_source: 'google',
          utm_medium: 'cpc',
          landing_page: 'https://venue.example/functions?gclid=abc123&utm_source=google&utm_medium=cpc',
          referrer: 'https://www.google.com/',
        });
        expect(JSON.parse(store.data.get(KEY)!).saved_at).toBeTypeOf('number');
      });

      it('keeps the first touch on later visits, even tagged ones', () => {
        const store = storage();
        read({ href: 'https://venue.example/?utm_source=facebook', store });
        const got = read({ href: 'https://venue.example/menu?utm_source=google&gclid=zzz', store });
        expect(got.utm_source).toBe('facebook');
        expect(got.gclid).toBeUndefined();
      });

      it('lets a tagged visit replace an untagged first touch', () => {
        const store = storage();
        read({ href: 'https://venue.example/', referrer: 'https://www.google.com/', store });
        expect(read({ href: 'https://venue.example/?gclid=later', store }).gclid).toBe('later');
      });

      it("ignores the site's own pages as a referrer", () => {
        expect(read({ href: 'https://venue.example/contact', referrer: 'https://venue.example/' })).toEqual({
          landing_page: 'https://venue.example/contact',
        });
      });

      it('forgets a first touch after 90 days', () => {
        const old = JSON.stringify({ utm_source: 'old', landing_page: 'x', saved_at: Date.now() - 91 * DAY });
        expect(read({ href: 'https://venue.example/?utm_source=new', store: storage({ [KEY]: old }) }).utm_source).toBe('new');
      });

      it('survives blocked or corrupt storage', () => {
        const blocked: Store = {
          data: new Map(),
          getItem: () => { throw new Error('denied'); },
          setItem: () => { throw new Error('denied'); },
          removeItem: () => undefined,
        };
        expect(read({ href: 'https://venue.example/?utm_campaign=xmas', store: blocked }).utm_campaign).toBe('xmas');
        expect(read({ href: 'https://venue.example/?utm_campaign=xmas', store: storage({ [KEY]: '{not json' }) }).utm_campaign).toBe('xmas');
      });
    });
  }
});

describe('embeds load cleanly', () => {
  it('forms.js adds one global and tolerates being loaded twice', () => {
    const { window } = run(FORMS_JS);
    const first = window.NormForms;
    runInNewContext(FORMS_JS, { window, document: { readyState: 'complete', querySelectorAll: () => [] } });
    expect(window.NormForms).toBe(first);
    expect(Object.keys(window).filter((k) => !PAGE_KEYS.includes(k))).toEqual(['NormForms']);
  });

  it('track.js adds one global', () => {
    const { window } = run(TRACK_JS);
    expect(Object.keys(window).filter((k) => !PAGE_KEYS.includes(k))).toEqual(['NormTrack']);
  });
});
