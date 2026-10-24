import { describe, expect, it } from 'vitest';
import { AppWindow, Banknote, Blocks, BookOpen, ChefHat, GraduationCap, Music, PiggyBank, Share2, TrendingUp, Truck, UserRoundSearch, Wallet } from 'lucide-react';
import { resolveAppIcon } from './appIcons';

describe('resolveAppIcon', () => {
  it('maps the built-in apps by slug, ignoring the emoji they still store', () => {
    expect(resolveAppIcon({ slug: 'training', icon: '🎓' })).toBe(GraduationCap);
    // hiring.json says 🧑‍🍳 and the catalog says 🧑‍💻 — the slug decides.
    expect(resolveAppIcon({ slug: 'hiring', icon: '🧑‍🍳' })).toBe(UserRoundSearch);
    expect(resolveAppIcon({ slug: 'hiring', icon: '🧑‍💻' })).toBe(UserRoundSearch);
    expect(resolveAppIcon({ slug: 'performance', icon: '📈' })).toBe(TrendingUp);
  });

  it('gives the Finance job apps three icons of their own', () => {
    expect(resolveAppIcon({ slug: 'payroll', icon: '💵' })).toBe(Wallet);
    expect(resolveAppIcon({ slug: 'entertainers' })).toBe(Music);
    expect(resolveAppIcon({ slug: 'budget', icon: '📈' })).toBe(PiggyBank);
    // …none of them Bills' or Profit & Loss'.
    expect(resolveAppIcon({ slug: 'bills' })).toBe(Banknote);
    expect(resolveAppIcon({ slug: 'profit-and-loss' })).toBe(TrendingUp);
  });

  it('takes a lucide name the app chose, in any spelling', () => {
    expect(resolveAppIcon({ slug: 'my-app', icon: 'chef-hat' })).toBe(ChefHat);
    expect(resolveAppIcon({ slug: 'my-app', icon: 'ChefHat' })).toBe(ChefHat);
    expect(resolveAppIcon({ slug: 'my-app', icon: 'book_open' })).toBe(BookOpen);
    // A trailing number gets lucide's hyphen: Share2 → share-2.
    expect(resolveAppIcon({ slug: 'my-app', icon: 'Share2' })).toBe(Share2);
  });

  it('strips the custom: prefix the team payload uses', () => {
    expect(resolveAppIcon({ slug: 'custom:bidfood-app' })).toBe(Truck);
    expect(resolveAppIcon({ slug: 'custom:anything', app_slug: 'training' })).toBe(GraduationCap);
  });

  it('maps legacy emoji with or without the variation selector', () => {
    expect(resolveAppIcon({ slug: 'old-app', icon: '🚚' })).toBe(Truck);
    expect(resolveAppIcon({ slug: 'old-app', icon: '🗓️' })).not.toBe(AppWindow);
  });

  it("falls back to the caller's icon, then AppWindow — never an emoji", () => {
    expect(resolveAppIcon({ slug: 'mystery', icon: '🦄' }, Blocks)).toBe(Blocks);
    expect(resolveAppIcon({ slug: 'mystery', icon: '🦄' })).toBe(AppWindow);
    expect(resolveAppIcon({})).toBe(AppWindow);
  });
});
