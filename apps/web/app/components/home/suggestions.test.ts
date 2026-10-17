import { describe, expect, it } from 'vitest';
import { ChefHat, Lightbulb, MessageCircle, Package } from 'lucide-react';
import { suggestionIcon } from './Suggestions';

describe('suggestionIcon', () => {
  it('marks an insight as Norm noticing something', () => {
    expect(suggestionIcon({ kind: 'insight', member: 'time_attendance' })).toBe(Lightbulb);
  });
  it('uses the team member’s own icon otherwise', () => {
    expect(suggestionIcon({ kind: 'regular', member: 'procurement' })).toBe(Package);
    expect(suggestionIcon({ kind: 'discover', member: 'executive_chef' })).toBe(ChefHat);
  });
  it('falls back for Norm itself', () => {
    expect(suggestionIcon({ kind: 'discover', member: 'norm' })).toBe(MessageCircle);
  });
});
