'use client';

/**
 * A dollars-and-cents text field whose value is integer cents. The person
 * types "1,234.50"; the form holds 123450. Blank is null. A negative amount
 * is allowed where the form says so (adjustments).
 */
import { useState } from 'react';
import { centsToInput, parseCents } from './money';

export default function MoneyInput({ id, value, onChange, allowNegative = false, disabled = false, placeholder = '0.00', width, testId, ariaLabel }: {
  id?: string;
  value: number | null;
  onChange: (cents: number | null) => void;
  allowNegative?: boolean;
  disabled?: boolean;
  placeholder?: string;
  width?: number | string;
  testId?: string;
  ariaLabel?: string;
}) {
  const [text, setText] = useState(() => centsToInput(value));
  // The field keeps in step with the form when the value changes from
  // outside (a server default, a reset) but never while the person is
  // mid-edit: the text is re-derived only when the cents it stands for
  // differ from the cents the form now holds.
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    if (parseCents(text) !== value) setText(centsToInput(value));
  }
  return (
    <input
      id={id}
      className="n-input"
      inputMode="decimal"
      value={text}
      placeholder={placeholder}
      disabled={disabled}
      aria-label={ariaLabel}
      data-testid={testId}
      onBlur={() => setText(centsToInput(value))}
      onChange={(e) => {
        const raw = e.target.value;
        setText(raw);
        const cents = parseCents(raw);
        onChange(cents === null ? null : allowNegative ? cents : Math.abs(cents));
      }}
      style={{ width: width ?? '100%', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}
    />
  );
}
