import { describe, expect, it } from 'vitest';
import { FnApiError } from '../shared/api';
import { ACCESSIBLE_DEFAULTS, contrastIssues, contrastRatio, parseHex } from './contrast';
import { fieldGroups, fullMatrix, overridesOf, sameMatrix } from './enabledFields';
import { errorFromBody, errorText, isStale } from './errors';
import { buildPreview, defaultCss, esc, fillTokens, previewDocument, type PreviewInput } from './brandPreview';
import { safeHref, tagAction, plainText } from './richText';
import { imageProblem } from './upload';

describe('enabled fields matrix', () => {
  const catalogue = [
    { section: 'details', key: 'details.min_spend', label: 'Min spend' },
    { section: 'details', key: 'details.av', label: 'A/V' },
    { section: 'details', key: 'details.av.in_house_mic', label: 'In-house mic' },
    { section: 'food', key: 'food.allergies', label: 'Allergies' },
  ];
  const keys = catalogue.map((c) => c.key);

  it('groups the catalogue by section and marks sub-fields', () => {
    const groups = fieldGroups(catalogue);
    expect(groups.map((g) => g.title)).toEqual(['Details', 'Food']);
    expect(groups[0].rows.map((r) => [r.key, r.depth, r.parent])).toEqual([
      ['details.min_spend', 0, null],
      ['details.av', 0, null],
      ['details.av.in_house_mic', 1, 'details.av'],
    ]);
  });

  it('reads a missing key as on and stores only what is switched off', () => {
    const stored = { seated_dining: { 'details.av': false, 'food.allergies': true } };
    const matrix = fullMatrix(stored, ['seated_dining', 'group_booking'], keys);
    expect(matrix.seated_dining).toEqual({
      'details.min_spend': true,
      'details.av': false,
      'details.av.in_house_mic': true,
      'food.allergies': true,
    });
    expect(Object.values(matrix.group_booking).every(Boolean)).toBe(true);
    expect(overridesOf(matrix)).toEqual({ seated_dining: { 'details.av': false } });
    expect(sameMatrix(matrix, fullMatrix(overridesOf(matrix), ['seated_dining', 'group_booking'], keys))).toBe(true);
    const changed = { ...matrix, group_booking: { ...matrix.group_booking, 'food.allergies': false } };
    expect(sameMatrix(matrix, changed)).toBe(false);
  });
});

describe('brand colours', () => {
  it('parses hex colours', () => {
    expect(parseHex('#fff')).toEqual({ r: 255, g: 255, b: 255 });
    expect(parseHex('#0E5340')).toEqual({ r: 14, g: 83, b: 64 });
    expect(parseHex('#0E5340ff')).toEqual({ r: 14, g: 83, b: 64 });
    expect(parseHex('green')).toBeNull();
    expect(parseHex(null)).toBeNull();
  });

  it('computes WCAG contrast', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 5);
    expect(contrastRatio('#777777', '#ffffff')).toBeCloseTo(4.48, 2);
    expect(contrastRatio('#b08d57', '#ffffff')).toBeCloseTo(3.09, 1);
    expect(contrastRatio('red', '#fff')).toBeNull();
  });

  it('flags the pairs a proposal would struggle to read, using server defaults for unset colours', () => {
    // Nothing set: Norm's default accent (#b08d57) is too faint for small labels.
    expect(contrastIssues({}).map((i) => `${i.fg}/${i.bg}`)).toEqual(['accent/background']);
    expect(contrastIssues(ACCESSIBLE_DEFAULTS)).toEqual([]);
    // Glass Goose's imported palette passes.
    expect(contrastIssues({ primary: '#0E5340', accent: '#0E5340', panel: '#0E5340', text: '#1F3D2F', background: '#FFFFFF' })
      .map((i) => `${i.fg}/${i.bg}`)).toEqual(['text/panel']);
    const pale = contrastIssues({ text: '#cccccc', background: '#ffffff' });
    expect(pale[0].message).toMatch(/Body text on the background is 1\.6:1/);
  });
});

describe('brand preview', () => {
  const input: PreviewInput = {
    venueName: 'Glass Goose',
    wordmark: '',
    tagline: 'Rooftop bar',
    logoUrl: null,
    footer: '78 Federal St',
    validDays: 14,
    colours: { primary: '#0E5340' },
    fonts: { heading: 'Georgia, serif' },
    inclusions: ['A dedicated functions manager', '  '],
    terms: [{ title: 'Minimum spend', body: 'Not a hire fee.' }, { title: '', body: '<b>Cakeage</b> applies' }],
    signoff: { title: '', body: '', contact: 'Ash | Functions' },
    templateHtml: '',
    templateCss: '',
    currency: 'NZD',
    taxLabel: 'incl. GST',
    bookingLabel: 'Large event (exclusive)',
    areas: [{ name: 'Baxter’s', description: 'Private bar', capacity_seated: 40, capacity_standing: 80 }],
  };

  it('fills known tokens and leaves unknown ones as written', () => {
    expect(fillTokens('Hi {{ client_name }} — {{nope}}', { client_name: 'Sam' })).toBe('Hi Sam — {{nope}}');
    expect(fillTokens('', {})).toBe('');
  });

  it('escapes text values', () => {
    expect(esc('<b>"Fish" & chips</b>')).toBe('&lt;b&gt;&quot;Fish&quot; &amp; chips&lt;/b&gt;');
  });

  it('renders Norm’s layout in the brand’s colours when there is no template', () => {
    const doc = buildPreview(input, 'https://norm.test');
    expect(doc).toContain('color: #0E5340');
    expect(doc).toContain('<div class="wordmark">Glass Goose</div>');
    expect(doc).toContain('Rooftop bar');
    expect(doc).toContain('A dedicated functions manager');
    expect(doc).toContain('&lt;b&gt;Cakeage&lt;/b&gt; applies'); // terms are text, not markup
    expect(doc).toContain('Baxter’s');
    expect(doc).toContain('$6,000');
    expect(doc).toContain('This proposal is valid for 14 days.');
  });

  it('renders the brand template with sample values instead when there is one', () => {
    const doc = buildPreview({ ...input, templateHtml: '<h1>{{event_name}}</h1>{{logo}} valid until {{valid_until}}', templateCss: 'h1 { color: red }' }, 'https://norm.test');
    expect(doc).toContain('<h1>End-of-year awards night</h1>');
    expect(doc).toContain('valid until Friday 20 November 2026');
    expect(doc).toContain('h1 { color: red }');
    expect(doc).not.toContain('.proposal .cover');
  });

  it('locks the document down: CSP to Norm’s public images, no early </style>', () => {
    const doc = previewDocument('<p>x</p>', 'a{} </style><script>alert(1)</script>', 'https://norm.test');
    expect(doc).toContain("default-src &#x27;none&#x27;; img-src https://norm.test/api/public/files/ blob:");
    expect(doc).not.toContain('</style><script>');
    expect(doc).toContain('<base target="_blank">');
  });

  it('falls back to the server defaults for unset colours and fonts', () => {
    const css = defaultCss({}, {});
    expect(css).toContain('color: #1f2a44');
    expect(css).toContain('font-family: system-ui, sans-serif');
  });
});

describe('reply template cleaning policy', () => {
  it('keeps formatting, drops dangerous elements, unwraps the rest', () => {
    expect(tagAction('P')).toBe('keep');
    expect(tagAction('strong')).toBe('keep');
    expect(tagAction('script')).toBe('drop');
    expect(tagAction('img')).toBe('drop');
    expect(tagAction('iframe')).toBe('drop');
    expect(tagAction('font')).toBe('unwrap');
  });

  it('keeps only web, mail, phone and placeholder links', () => {
    expect(safeHref('https://lazeppa.co.nz')).toBe('https://lazeppa.co.nz');
    expect(safeHref('mailto:events@x.nz')).toBe('mailto:events@x.nz');
    expect(safeHref('{{quote_link}}')).toBe('{{quote_link}}');
    expect(safeHref('javascript:alert(1)')).toBeNull();
    expect(safeHref(' JavaScript:alert(1)')).toBeNull();
    expect(safeHref('data:text/html,hi')).toBeNull();
    expect(safeHref('')).toBeNull();
  });

  it('reads the text of a template for list previews', () => {
    expect(plainText('<p>Hi {{client_name}},</p><p>Thanks &amp; see you<br>soon</p>')).toBe('Hi {{client_name}}, Thanks & see you soon');
  });
});

describe('errors and uploads', () => {
  it('reads every error shape the API sends', () => {
    const svc = errorFromBody(409, { detail: { code: 'conflict', message: 'This was changed by someone else since you opened it — reload and try again.' } });
    expect(svc.code).toBe('conflict');
    expect(isStale(svc)).toBe(true);
    expect(errorFromBody(422, { detail: [{ msg: 'Field required' }, { msg: 'Bad type' }] }).message).toBe('Field required; Bad type');
    expect(errorFromBody(403, { detail: 'Not allowed' }).message).toBe('Not allowed');
    expect(errorFromBody(413, null, 'Payload Too Large').message).toBe('That file is too big to upload.');
    expect(errorFromBody(500, null, 'Internal Server Error').message).toBe('Internal Server Error');
  });

  it('phrases failures for people', () => {
    expect(errorText(new FnApiError(409, 'not_live', 'Functions is in shadow mode', null))).toBe('Functions is in shadow mode');
    expect(errorText(new TypeError('Failed to fetch'))).toMatch(/couldn’t be reached/);
    expect(isStale(new FnApiError(409, 'conflict', 'That form address is taken — choose another.', null))).toBe(false);
  });

  it('checks images lightly before uploading', () => {
    expect(imageProblem({ name: 'deck.jpg', type: 'image/jpeg', size: 2_000_000 })).toBeNull();
    expect(imageProblem({ name: 'menu.pdf', type: 'application/pdf', size: 10 })).toMatch(/isn’t a JPEG/);
    expect(imageProblem({ name: 'big.png', type: 'image/png', size: 11 * 1024 * 1024 })).toMatch(/over 10 MB/);
    expect(imageProblem({ name: 'noext', type: '', size: 10 })).toBeNull(); // the API judges the bytes
  });
});
