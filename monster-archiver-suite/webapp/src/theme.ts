export type ThemeId = "nebula" | "ocean" | "ember" | "forest" | "mono";

// Keep this key, and the valid-id list below, in sync with the anti-flash
// script in index.html — that inline script runs before any JS module loads
// (so React never gets a chance to paint the wrong palette first) and can't
// import this file, so it duplicates the same two constants by hand.
export const THEME_STORAGE_KEY = "monster-archiver-theme";

// "nebula" is the default palette baked directly into :root in index.css —
// it has no [data-theme="nebula"] override block, so applying it means
// *removing* the data-theme attribute rather than setting it to a value.
export const DEFAULT_THEME: ThemeId = "nebula";

const OVERRIDE_THEMES: ThemeId[] = ["ocean", "ember", "forest", "mono"];

export interface ThemeMeta {
  id: ThemeId;
  label: string;
  /** That theme's deezer-500 — used as a small preview dot in the picker. */
  swatch: string;
  /** That theme's flow-500 — paired with swatch for a two-tone preview dot. */
  swatchAlt: string;
}

// Hex values copied straight from each [data-theme="..."] block's
// --color-deezer-500 / --color-flow-500 in index.css — if those ever change,
// update here too so the picker's swatches stay accurate previews.
export const THEMES: ThemeMeta[] = [
  { id: "nebula", label: "Nebula", swatch: "#8b2ce8", swatchAlt: "#d926a9" },
  { id: "ocean", label: "Ocean", swatch: "#3b82f6", swatchAlt: "#06b6d4" },
  { id: "ember", label: "Ember", swatch: "#f97316", swatchAlt: "#ef4444" },
  { id: "forest", label: "Forest", swatch: "#22c55e", swatchAlt: "#14b8a6" },
  { id: "mono", label: "Mono", swatch: "#71717a", swatchAlt: "#64748b" },
];

export function getStoredTheme(): ThemeId {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored && (OVERRIDE_THEMES as string[]).includes(stored)) {
      return stored as ThemeId;
    }
  } catch {
    // localStorage unavailable (privacy mode, etc.) — fall back to default.
  }
  return DEFAULT_THEME;
}

// Applies a theme to the live DOM and persists it. Deliberately does not
// dispatch a custom event — anything that needs to react live (see
// AmbientBackground's canvas particles) watches the data-theme attribute
// itself via MutationObserver, so this stays a single source of truth
// instead of two.
export function setTheme(id: ThemeId): void {
  try {
    if (id === DEFAULT_THEME) {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
    } else {
      window.localStorage.setItem(THEME_STORAGE_KEY, id);
    }
  } catch {
    // Non-fatal — the DOM attribute below still applies the theme for this session.
  }

  if (id === DEFAULT_THEME) {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", id);
  }
}
