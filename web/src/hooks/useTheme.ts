/**
 * web/src/hooks/useTheme.ts
 * ─────────────────────────
 * All time-based and custom theme logic, extracted from App.tsx.
 *
 * Exports:
 *  - TimeThemeSettings type
 *  - useTheme() hook — returns { handleThemeChange, handleTimeThemeSettingsChange }
 *  - lower-level helpers (re-exported for AppearancePage etc.)
 */

export type TimeThemeSettings = {
    enabled: boolean;
    dayTheme: string;
    nightTheme: string;
    dayStart: number;
    nightStart: number;
};

export const TIME_THEME_STORAGE_KEY = 'limebot-time-theme';
export const DEFAULT_TIME_THEME_SETTINGS: TimeThemeSettings = {
    enabled: false,
    dayTheme: 'glacier',
    nightTheme: 'midnight-synth',
    dayStart: 7,
    nightStart: 19,
};

export function loadTimeThemeSettings(): TimeThemeSettings {
    try {
        const raw = localStorage.getItem(TIME_THEME_STORAGE_KEY);
        if (!raw) return DEFAULT_TIME_THEME_SETTINGS;
        const parsed = JSON.parse(raw);
        return {
            enabled: Boolean(parsed?.enabled),
            dayTheme: typeof parsed?.dayTheme === 'string' ? parsed.dayTheme : DEFAULT_TIME_THEME_SETTINGS.dayTheme,
            nightTheme: typeof parsed?.nightTheme === 'string' ? parsed.nightTheme : DEFAULT_TIME_THEME_SETTINGS.nightTheme,
            dayStart: Number.isInteger(parsed?.dayStart) ? parsed.dayStart : DEFAULT_TIME_THEME_SETTINGS.dayStart,
            nightStart: Number.isInteger(parsed?.nightStart) ? parsed.nightStart : DEFAULT_TIME_THEME_SETTINGS.nightStart,
        };
    } catch {
        return DEFAULT_TIME_THEME_SETTINGS;
    }
}

export function resolveEffectiveTheme(baseTheme: string, settings: TimeThemeSettings): string {
    if (!settings.enabled) return baseTheme;
    const hour = new Date().getHours();
    const dayStart = Math.max(0, Math.min(23, settings.dayStart));
    const nightStart = Math.max(0, Math.min(23, settings.nightStart));

    if (dayStart === nightStart) return settings.dayTheme;

    const isDay =
        dayStart < nightStart
            ? hour >= dayStart && hour < nightStart
            : hour >= dayStart || hour < nightStart;
    return isDay ? settings.dayTheme : settings.nightTheme;
}

export function applyCustomTheme(themeId: string): void {
    try {
        const savedThemes = localStorage.getItem('limebot-custom-themes');
        if (savedThemes) {
            const themes = JSON.parse(savedThemes);
            const theme = themes.find((t: { id: string }) => t.id === themeId);
            if (theme) {
                Object.entries(theme.variables).forEach(([key, value]) => {
                    document.documentElement.style.setProperty(key, value as string);
                });
                if (theme.bgImage) {
                    document.documentElement.style.setProperty('--bg-image', theme.bgImage);
                }
                document.documentElement.setAttribute('data-custom-theme', 'true');
            }
        }
    } catch (e) {
        console.error('Failed to apply custom theme', e);
    }
}

export function clearCustomThemeVars(): void {
    const propsToRemove = [
        '--primary', '--primary-foreground', '--background', '--foreground',
        '--card', '--card-foreground', '--popover', '--popover-foreground',
        '--border', '--input', '--accent', '--accent-foreground',
        '--ring', '--radius', '--muted', '--muted-foreground', '--bg-image',
    ];
    propsToRemove.forEach(prop => document.documentElement.style.removeProperty(prop));
    document.documentElement.removeAttribute('data-custom-theme');
}

export function applyThemeToDom(theme: string): void {
    clearCustomThemeVars();
    if (theme === 'lime') {
        document.documentElement.removeAttribute('data-theme');
    } else if (theme.startsWith('custom-')) {
        document.documentElement.removeAttribute('data-theme');
        applyCustomTheme(theme);
    } else {
        document.documentElement.setAttribute('data-theme', theme);
    }

    // Apply wallpaper overlay if set
    try {
        const wallpaper = localStorage.getItem('limebot-wallpaper');
        if (wallpaper) {
            const wp = JSON.parse(wallpaper);
            if (wp.url) {
                document.documentElement.style.setProperty(
                    '--bg-image',
                    `linear-gradient(rgba(0,0,0,${wp.overlay ?? 0.6}), rgba(0,0,0,${wp.overlay ?? 0.6})), url(${wp.url})`
                );
            }
        }
    } catch { /* ignore */ }
}

export function applyThemeWithSchedule(baseTheme: string): void {
    const settings = loadTimeThemeSettings();
    const effectiveTheme = resolveEffectiveTheme(baseTheme, settings);
    applyThemeToDom(effectiveTheme);
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useTheme() {
    const handleThemeChange = (theme: string) => {
        localStorage.setItem('limebot-theme', theme);
        applyThemeWithSchedule(theme);
    };

    const handleTimeThemeSettingsChange = (settings: TimeThemeSettings) => {
        localStorage.setItem(TIME_THEME_STORAGE_KEY, JSON.stringify(settings));
        const savedTheme = localStorage.getItem('limebot-theme') || 'lime';
        applyThemeWithSchedule(savedTheme);
    };

    return { handleThemeChange, handleTimeThemeSettingsChange };
}
