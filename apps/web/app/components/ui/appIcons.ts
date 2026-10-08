import {
  AppWindow, Award, Banknote, Beer, Bell, Blocks, BookOpen, Boxes, CalendarClock, CalendarDays,
  ChartColumn, ChartColumnBig, ChartLine, ChefHat, ClipboardList, Clock, Coffee, Compass,
  ConciergeBell, Contact, CookingPot, FolderOpen, GraduationCap, HandPlatter, Handshake, Heart,
  LayoutDashboard, Mail, Martini, Megaphone, Package, Puzzle, Receipt, Salad, Share2, ShieldCheck,
  ShoppingCart, Smartphone, Soup, Sparkles, Star, Store, Target, TrendingUp, Truck, UserRound,
  UserRoundSearch, Users, Utensils, UtensilsCrossed, Wine,
  type LucideIcon,
} from 'lucide-react';

/**
 * One answer to "which icon does this app get?", wherever an app is drawn —
 * the menu, AppRunner, the Apps page, the Team page. Apps store `icon` as free
 * text: built-ins and the catalog hold emoji today, and a newer app may hold a
 * lucide name. We never print the emoji (colour pictographs are out of the
 * design); every route below ends in a line icon.
 *
 * Order: a lucide name the app chose → the app's slug → a known emoji (older
 * custom apps) → the caller's fallback (its team member's icon) → AppWindow.
 */

/** Curated lucide names an app may choose (kebab-case, as lucide names them). */
const BY_NAME: Record<string, LucideIcon> = {
  'app-window': AppWindow, award: Award, banknote: Banknote, beer: Beer, bell: Bell, blocks: Blocks,
  'book-open': BookOpen, boxes: Boxes, 'calendar-clock': CalendarClock, 'calendar-days': CalendarDays,
  'chart-column': ChartColumn, 'chart-column-big': ChartColumnBig, 'chart-line': ChartLine,
  'chef-hat': ChefHat, 'clipboard-list': ClipboardList, clock: Clock, coffee: Coffee, compass: Compass,
  'concierge-bell': ConciergeBell, contact: Contact, 'cooking-pot': CookingPot, 'folder-open': FolderOpen,
  'graduation-cap': GraduationCap, 'hand-platter': HandPlatter, handshake: Handshake, heart: Heart,
  'layout-dashboard': LayoutDashboard, mail: Mail, martini: Martini, megaphone: Megaphone,
  package: Package, puzzle: Puzzle, receipt: Receipt, salad: Salad, 'share-2': Share2,
  'shield-check': ShieldCheck, 'shopping-cart': ShoppingCart, smartphone: Smartphone, soup: Soup,
  sparkles: Sparkles, star: Star, store: Store, target: Target, 'trending-up': TrendingUp, truck: Truck,
  'user-round': UserRound, 'user-round-search': UserRoundSearch, users: Users, utensils: Utensils,
  'utensils-crossed': UtensilsCrossed, wine: Wine,
};

/** Built-in and catalog apps, by slug (live catalog slugs, `custom:` stripped). */
const BY_SLUG: Record<string, LucideIcon> = {
  training: GraduationCap,
  hiring: UserRoundSearch,
  'norm-core': Compass,
  'loaded-stock': Boxes,
  'loaded-kitchen': BookOpen,
  'loaded-time': CalendarClock,
  'loaded-reports': ChartLine,
  'saved-reports': FolderOpen,
  'bamboohr-app': Contact,
  'brevo-app': Mail,
  'metricool-app': Share2,
  'bidfood-app': Truck,
  'app-builder': Puzzle,
};

/** Emoji that older apps were given, mapped to their nearest line icon. */
const BY_EMOJI: Record<string, LucideIcon> = {
  '🎓': GraduationCap, '🧑‍🍳': ChefHat, '👨‍🍳': ChefHat, '👩‍🍳': ChefHat, '🧑‍💻': UserRoundSearch,
  '🧑‍💼': UserRound, '👥': Users, '📊': ChartColumn, '📈': TrendingUp, '🗂': FolderOpen,
  '📁': FolderOpen, '🧭': Compass, '📦': Package, '🚚': Truck, '📗': BookOpen, '📘': BookOpen,
  '📖': BookOpen, '🗓': CalendarDays, '📅': CalendarDays, '⏱': Clock, '⏰': Clock, '🎋': Contact,
  '✉': Mail, '📧': Mail, '📱': Smartphone, '🧩': Puzzle, '🛒': ShoppingCart, '📣': Megaphone,
  '🍺': Beer, '🍷': Wine, '🍸': Martini, '☕': Coffee, '🍽': Utensils, '🥗': Salad, '🍲': Soup,
  '📋': ClipboardList, '🧾': Receipt, '💵': Banknote, '⭐': Star, '✨': Sparkles, '🏪': Store,
  '🔔': Bell, '🛎': ConciergeBell, '❤': Heart, '🤝': Handshake, '🎯': Target, '🏅': Award,
  '🛡': ShieldCheck,
};

/** 'GraduationCap' / 'graduation-cap' / 'graduation_cap' → 'graduation-cap';
 *  'Share2' → 'share-2' (lucide puts a hyphen before a trailing number). */
function toKebab(name: string): string {
  return name
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .replace(/([a-zA-Z])(\d)/g, '$1-$2')
    .replace(/[\s_]+/g, '-')
    .toLowerCase();
}

export interface AppIconSource {
  slug?: string | null;
  /** The team payload carries `custom:<slug>` in `slug` and the bare slug here. */
  app_slug?: string | null;
  icon?: string | null;
}

export function resolveAppIcon(app: AppIconSource, fallback?: LucideIcon): LucideIcon {
  const slug = (app.app_slug || app.slug || '').replace(/^custom:/, '');
  const raw = (app.icon || '').trim();
  if (raw) {
    const named = BY_NAME[toKebab(raw)];
    if (named) return named;
  }
  if (slug && BY_SLUG[slug]) return BY_SLUG[slug];
  if (raw) {
    // Variation selectors (U+FE0F) come and go with how the emoji was typed.
    const emoji = BY_EMOJI[raw.replace(/️/g, '')];
    if (emoji) return emoji;
  }
  return fallback ?? AppWindow;
}
