import { describe, expect, it } from 'vitest';
import { defaultMessage, editableDocument, imageTag, optionBadge, spliceText } from './proposalDoc';

describe('proposal editor helpers', () => {
  it('builds a locked-down editable document whose CSS cannot close its style tag', () => {
    const doc = editableDocument('<h1>Hi</h1>', 'h1{color:red}</style><script>x()</script>', 'https://bettercallnorm.com');
    expect(doc).toContain("img-src https://bettercallnorm.com/api/public/files/;");
    expect(doc).toContain("default-src 'none'");
    expect(doc).not.toContain('</style><script>');
    expect(doc).toContain('\\3c /style>');
    expect(doc).toContain('<body><h1>Hi</h1></body>');
  });

  it('loads fonts only from Norm’s hosted set', () => {
    const doc = editableDocument('<h1>Hi</h1>', '', 'https://bettercallnorm.com');
    expect(doc).toContain("font-src https://bettercallnorm.com/fonts/brand/;");
    expect(doc).toContain("style-src 'unsafe-inline' https://bettercallnorm.com/fonts/brand/;");
    expect(doc).toContain('<link rel="stylesheet" href="https://bettercallnorm.com/fonts/brand/fonts.css">');
  });

  it('makes library image tags the sanitiser keeps', () => {
    expect(imageTag('abc-123', 'Deck "night"')).toBe(
      '<img src="/api/public/files/abc-123" alt="Deck &quot;night&quot;" style="max-width:100%;height:auto">',
    );
  });

  it('splices text at a selection', () => {
    expect(spliceText('<p>ab</p>', 4, 5, 'X')).toEqual({ text: '<p>aX</p>', caret: 5 });
    expect(spliceText('abc', 10, 12, '!')).toEqual({ text: 'abc!', caret: 4 });
  });

  it('labels what the customer can do with an option', () => {
    expect(optionBadge({ state: 'approved', confirmable: true })).toEqual({ tone: 'ok', label: 'Can confirm' });
    expect(optionBadge({ state: 'proposed', confirmable: false })).toEqual({ tone: 'warn', label: 'Needs approval' });
    expect(optionBadge({ state: 'proposed', confirmable: true }).label).toBe('Can confirm'); // area needs no approval
    expect(optionBadge({ state: 'confirmed', confirmable: false }).label).toBe('Confirmed');
  });

  it('starts the covering email with the quote link placeholder', () => {
    expect(defaultMessage('proposal', 'Casey')).toContain('{{quote_link}}');
    expect(defaultMessage('proposal', '')).toContain('Hi there,');
    expect(defaultMessage('email', 'Tom & Jo')).toBe('<p>Hi Tom &amp; Jo,</p><p></p>');
  });
});
