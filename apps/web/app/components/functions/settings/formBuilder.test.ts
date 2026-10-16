import { describe, expect, it } from 'vitest';
import { answerText, choicesToText, fieldProblems, keyFromLabel, sourceSummary, textToChoices } from './formFields';
import { looksLikeColour, px, sixDigitHex, snake, themeStyles, tokenLabel } from './formTheme';
import type { FormField } from './types';

describe('form field keys', () => {
  it('makes a key from a label, unique among the others', () => {
    expect(keyFromLabel('Event date (approx.)', [])).toBe('event_date_approx');
    expect(keyFromLabel('Email', ['email'])).toBe('email_2');
    expect(keyFromLabel('Email', ['email', 'email_2'])).toBe('email_3');
    expect(keyFromLabel('2nd choice', [])).toBe('field_2nd_choice');
    expect(keyFromLabel('Café', [])).toBe('cafe');
    expect(keyFromLabel('', [])).toBe('field');
  });
});

describe('choices', () => {
  it('round-trips one choice per line, with an optional value', () => {
    const options = [
      { label: 'Seated Dining Event', value: 'seated_dining' },
      { label: 'Bar', value: 'Bar' },
    ];
    const text = choicesToText(options);
    expect(text).toBe('seated_dining | Seated Dining Event\nBar');
    expect(textToChoices(text)).toEqual([
      { value: 'seated_dining', label: 'Seated Dining Event' },
      { value: 'Bar', label: 'Bar' },
    ]);
    expect(textToChoices('\n  Deck \n\n')).toEqual([{ value: 'Deck', label: 'Deck' }]);
    expect(choicesToText({ min: 1, max: 10 })).toBe('');
  });
});

describe('field checks', () => {
  const f = (over: Partial<FormField>): FormField => ({
    key: 'name', label: 'Name', type: 'name', required: true, options: null, placeholder: null, help_text: null, maps_to: null, ...over,
  });

  it('catches what the API would refuse', () => {
    expect(fieldProblems([f({}), f({ key: 'email', label: 'Email', type: 'email' })])).toEqual([]);
    expect(fieldProblems([f({ label: ' ' })])).toEqual(['Field 1 needs a label.']);
    expect(fieldProblems([f({}), f({ label: 'Name again' })])).toEqual(['Two fields share the key “name”.']);
    expect(fieldProblems([f({ key: 'Bad Key' })])[0]).toMatch(/key must start with a letter/);
    expect(fieldProblems([f({ key: 'kind', label: 'Kind', type: 'choice', options: [] })])).toEqual(['Kind needs at least one choice.']);
    // A section may leave its key blank (the API names it section_<n>).
    expect(fieldProblems([f({ key: '', label: 'About you', type: 'section' })])).toEqual([]);
  });
});

describe('submissions', () => {
  it('shows answers as text', () => {
    expect(answerText({ first: 'Diana', last: 'Robson' })).toBe('Diana Robson');
    expect(answerText(['2026-12-09', '2026-12-11'])).toBe('2026-12-09, 2026-12-11');
    expect(answerText({ start: '12:00', finish: '15:00' })).toBe('12:00 – 15:00');
    expect(answerText(true)).toBe('Yes');
    expect(answerText(null)).toBe('');
    expect(answerText(17)).toBe('17');
  });

  it('summarises where a lead came from', () => {
    expect(sourceSummary({ utm_source: 'facebook', utm_medium: 'paid', utm_campaign: 'xmas' })).toBe('facebook · paid · xmas');
    expect(sourceSummary({ gbraid: 'x', referrer: 'https://www.google.com/' })).toBe('Google Ads');
    expect(sourceSummary({ referrer: 'https://www.google.com/' })).toBe('google.com');
    expect(sourceSummary({})).toBe('Direct');
    expect(sourceSummary(null)).toBe('Direct');
  });
});

describe('form theme', () => {
  it('reads sizes as pixels and labels tokens', () => {
    expect(px(14)).toBe('14px');
    expect(px('20')).toBe('20px');
    expect(px('1.5rem')).toBe('1.5rem');
    expect(px('')).toBeUndefined();
    expect(tokenLabel('inputBorderColor')).toBe('Input border color');
    expect(tokenLabel('brand_color')).toBe('Brand color');
    expect(looksLikeColour('#006C54')).toBe(true);
    expect(looksLikeColour('hsl(3, 62%, 30%)')).toBe(false);
    expect(sixDigitHex('#AbC')).toBe('#aabbcc');
  });

  it('turns imported Orbit tokens into preview styles', () => {
    const s = themeStyles({ background: '#006C54', buttonBackground: '#ffffff', buttonTextColor: '#006C54', inputRadius: 999, headingSize: 28, labelTransform: 'none', buttonTransform: 'capitalize' });
    expect(s.form.background).toBe('#006C54');
    expect(s.button.background).toBe('#ffffff');
    expect(s.button.color).toBe('#006C54');
    expect(s.input.borderRadius).toBe('999px');
    expect(s.textarea.borderRadius).toBe('18px');
    expect(s.heading.fontSize).toBe('28px');
    expect(s.button.textTransform).toBe('capitalize');
  });

  it('reads forms.js snake_case names, which win over an Orbit camelCase twin', () => {
    expect(snake('inputBorderColor')).toBe('input_border_color');
    expect(snake('background')).toBe('background');
    const s = themeStyles({ input_radius: '4', inputRadius: 999, button_background: '#123456', text: '#222222', font: 'Georgia, serif' });
    expect(s.input.borderRadius).toBe('4px');
    expect(s.button.background).toBe('#123456');
    expect(s.form.color).toBe('#222222');
    expect(s.form.fontFamily).toBe('Georgia, serif');
  });

  it('uses primary / brand colour for the button when there is no button token', () => {
    expect(themeStyles({ primary: '#1c2b39', radius: '8px' }).button.background).toBe('#1c2b39');
    expect(themeStyles({ brand_color: '#49556A' }).button.background).toBe('#49556A');
    expect(themeStyles(null).button.background).toBe('#1a1a1a');
  });
});
