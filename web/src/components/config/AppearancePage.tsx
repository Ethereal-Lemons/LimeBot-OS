import { useState, useEffect, useRef } from 'react';
import { Palette, Code2, RotateCcw, Copy, Check, Sun, Moon, Clock, ImageIcon, Trash2, AlertTriangle } from 'lucide-react';

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CustomThemeCreator } from './CustomThemeCreator';
import { injectCss, CSS_STORAGE_KEY } from '@/lib/css-injector';
import { Field, SaveBar, SettingsPage, SettingsSection, StatusPill, invalidInputClass } from '@/components/settings/kit';
import { useSaveState } from '@/hooks/useSaveState';
import { useBeforeUnloadGuard } from '@/hooks/useUnsaved';
import { cn } from '@/lib/utils';

interface AppearancePageProps {
    onThemeChange?: (theme: string) => void;
    onTimeThemeSettingsChange?: (settings: TimeThemeSettings) => void;
}

type TimeThemeSettings = {
    enabled: boolean;
    dayTheme: string;
    nightTheme: string;
    dayStart: number;
    nightStart: number;
};

const TIME_THEME_STORAGE_KEY = 'limebot-time-theme';
const WALLPAPER_STORAGE_KEY = 'limebot-wallpaper';
const DEFAULT_TIME_THEME_SETTINGS: TimeThemeSettings = {
    enabled: false,
    dayTheme: 'glacier',
    nightTheme: 'midnight-synth',
    dayStart: 7,
    nightStart: 19,
};

type WallpaperSettings = {
    url: string;
    overlay: number;
};

const DEFAULT_WALLPAPER_SETTINGS: WallpaperSettings = {
    url: '',
    overlay: 60,
};

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function loadWallpaperSettings(): WallpaperSettings {
    try {
        const saved = localStorage.getItem(WALLPAPER_STORAGE_KEY);
        if (!saved) return DEFAULT_WALLPAPER_SETTINGS;
        const parsed: unknown = JSON.parse(saved);
        if (!isRecord(parsed)) return DEFAULT_WALLPAPER_SETTINGS;
        const url = typeof parsed.url === 'string' ? parsed.url : '';
        const overlay = typeof parsed.overlay === 'number' && Number.isFinite(parsed.overlay)
            ? Math.round(parsed.overlay * 100)
            : DEFAULT_WALLPAPER_SETTINGS.overlay;
        return { url, overlay };
    } catch {
        return DEFAULT_WALLPAPER_SETTINGS;
    }
}

function loadTimeThemeSettings(): TimeThemeSettings {
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

const standardThemes = [
    { id: 'lime', name: 'Cyber Lime', color: 'bg-[#84cc16]' },
    { id: 'purple', name: 'Nebula', color: 'bg-[#8b5cf6]' },
    { id: 'blue', name: 'Electric', color: 'bg-[#3b82f6]' },
    { id: 'orange', name: 'Sunset', color: 'bg-[#f97316]' },
    { id: 'red', name: 'Crimson', color: 'bg-[#dc2626]' },
    { id: 'pink', name: 'Hot Pink', color: 'bg-[#db2777]' },
    { id: 'autumn-harvest', name: 'Autumn', color: 'bg-gradient-to-br from-amber-500 to-orange-600' },
    { id: 'arctic-aurora', name: 'Aurora', color: 'bg-gradient-to-br from-teal-400 to-cyan-500' },
];

const specialThemes = [
    { id: 'frutiger-aero', name: 'Frutiger Aero', color: 'bg-gradient-to-br from-cyan-400 to-green-400', tag: 'light' },
    { id: 'angelcore-racing', name: 'Angelcore', color: 'bg-gradient-to-br from-zinc-900 to-black', tag: 'dark' },
    { id: 'cyberpunk', name: 'Cyberpunk', color: 'bg-gradient-to-br from-pink-500 to-cyan-500', tag: 'neon' },
    { id: 'retro-terminal', name: 'Terminal', color: 'bg-black border-2 border-green-500', tag: 'retro' },
    { id: 'midnight-synth', name: 'Midnight Synth', color: 'bg-gradient-to-br from-indigo-900 to-purple-900 border border-yellow-500/60', tag: 'dark' },
    { id: 'paperback', name: 'Paperback', color: 'bg-[#f5f5dc] border border-[#8b4513]/40', tag: 'light' },
    { id: 'synthwave-84', name: 'Synthwave', color: 'bg-gradient-to-br from-purple-600 to-pink-500', tag: 'neon' },
    { id: 'glacier', name: 'Glacier', color: 'bg-gradient-to-br from-blue-100 to-cyan-200', tag: 'light' },
    { id: 'coffee-shop', name: 'Coffee Shop', color: 'bg-[#6d4c41]', tag: 'light' },
    { id: 'sakura', name: 'Sakura', color: 'bg-gradient-to-br from-pink-200 to-pink-400', tag: 'light' },
    { id: 'sunset-mirage', name: 'Sunset Mirage', color: 'bg-gradient-to-br from-orange-300 via-rose-400 to-fuchsia-500', tag: 'light' },
    { id: 'emerald-depths', name: 'Emerald Depths', color: 'bg-gradient-to-br from-emerald-700 via-teal-700 to-cyan-900', tag: 'dark' },
    { id: 'windows-7', name: 'Windows 7 Aero', color: 'bg-gradient-to-br from-blue-400 via-blue-500 to-blue-700 border border-white/40', tag: 'full' },
    { id: 'windows-95', name: 'Windows 95', color: 'bg-[#c0c0c0] border-2 border-t-white border-l-white border-b-[#808080] border-r-[#808080]', tag: 'full' },
    { id: 'evangelion', name: 'Evangelion', color: 'bg-gradient-to-br from-orange-500 via-orange-600 to-purple-800', tag: 'full' },
    { id: 'ghibli-garden', name: 'Ghibli Garden', color: 'bg-gradient-to-br from-green-300 via-lime-200 to-amber-100 border border-green-700/30', tag: 'full' },
    { id: 'noir-dossier', name: 'Noir Dossier', color: 'bg-gradient-to-br from-zinc-950 via-stone-800 to-amber-900 border border-amber-400/50', tag: 'dark' },
    { id: 'solar-flare', name: 'Solar Flare', color: 'bg-gradient-to-br from-yellow-300 via-orange-400 to-rose-500', tag: 'light' },
    { id: 'circuit-board', name: 'Circuit Board', color: 'bg-gradient-to-br from-slate-950 via-emerald-950 to-lime-700 border border-lime-400/50', tag: 'dark' },
    { id: 'prism-desk', name: 'Prism Desk', color: 'bg-gradient-to-br from-white via-cyan-100 to-fuchsia-200 border border-cyan-300/60', tag: 'light' },
];

const allThemeOptions = [...standardThemes, ...specialThemes];

const hourOptions = Array.from({ length: 24 }, (_, hour) => ({
    value: String(hour),
    label: `${String(hour).padStart(2, '0')}:00`,
}));

/**
 * A light syntax check for the custom CSS box: unclosed comments and
 * unbalanced braces are the mistakes that silently disable the rules after
 * them. This only warns; it never blocks a save.
 */
function findCssProblem(css: string): string | null {
    const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
    if (withoutComments.includes('/*')) return 'A /* comment is never closed, so everything after it is ignored.';
    const stripped = withoutComments.replace(/(["'])(?:\\.|(?!\1)[^\\\n])*\1/g, '');
    let depth = 0;
    for (const char of stripped) {
        if (char === '{') depth += 1;
        else if (char === '}') {
            depth -= 1;
            if (depth < 0) return 'There is a closing } with no matching {.';
        }
    }
    if (depth > 0) return 'A { is never closed, so the rules after it may not apply.';
    return null;
}

export function AppearancePage({
    onThemeChange,
    onTimeThemeSettingsChange,
}: AppearancePageProps) {
    const [currentTheme, setCurrentTheme] = useState(() => localStorage.getItem('limebot-theme') || 'lime');
    const [timeTheme, setTimeTheme] = useState<TimeThemeSettings>(() => loadTimeThemeSettings());

    // Wallpaper: what is applied now vs. what is being edited.
    const [appliedWallpaper, setAppliedWallpaper] = useState(loadWallpaperSettings);
    const [wallpaperUrl, setWallpaperUrl] = useState(appliedWallpaper.url);
    const [wallpaperOverlay, setWallpaperOverlay] = useState(appliedWallpaper.overlay);
    const [failedPreviewUrl, setFailedPreviewUrl] = useState('');

    // Custom CSS: previewed live while typing, kept once saved.
    const [savedCss, setSavedCss] = useState(() => localStorage.getItem(CSS_STORAGE_KEY) ?? '');
    const [css, setCss] = useState(savedCss);
    const savedCssRef = useRef(savedCss);
    const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [copied, setCopied] = useState(false);

    const { saving, error: saveError, savedAt, run, clearError } = useSaveState('your appearance settings');

    useEffect(() => {
        localStorage.setItem(TIME_THEME_STORAGE_KEY, JSON.stringify(timeTheme));
        onTimeThemeSettingsChange?.(timeTheme);
    }, [timeTheme, onTimeThemeSettingsChange]);

    useEffect(() => {
        savedCssRef.current = savedCss;
    }, [savedCss]);

    // The editor used to be mounted only while its section was open; it still
    // injects the stored CSS on arrival, and puts the saved CSS back if the
    // page is left with an unsaved preview.
    useEffect(() => {
        injectCss(savedCssRef.current);
        return () => {
            if (previewTimer.current) clearTimeout(previewTimer.current);
            injectCss(savedCssRef.current);
        };
    }, []);

    const handleThemeSelect = (themeId: string) => {
        setCurrentTheme(themeId);
        onThemeChange?.(themeId);
    };

    const handleCssChange = (value: string) => {
        setCss(value);
        if (previewTimer.current) clearTimeout(previewTimer.current);
        previewTimer.current = setTimeout(() => injectCss(value), 300);
    };

    const clearCss = () => {
        if (previewTimer.current) clearTimeout(previewTimer.current);
        setCss('');
        setSavedCss('');
        localStorage.removeItem(CSS_STORAGE_KEY);
        injectCss('');
    };

    const copyCss = async () => {
        try {
            await navigator.clipboard.writeText(css);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch (err) {
            console.error('Could not copy CSS:', err);
        }
    };

    const removeWallpaper = () => {
        localStorage.removeItem(WALLPAPER_STORAGE_KEY);
        setWallpaperUrl('');
        setAppliedWallpaper((current) => ({ ...current, url: '' }));
        onThemeChange?.(currentTheme);
    };

    // ── dirty state and validation ──────────────────────────────────────────

    const wallpaperText = wallpaperUrl.trim();
    const wallpaperActive = appliedWallpaper.url !== '';
    const wallpaperDirty = wallpaperText !== appliedWallpaper.url
        || (wallpaperText !== '' && wallpaperOverlay !== appliedWallpaper.overlay);
    const wallpaperError = wallpaperText === '' && wallpaperActive
        ? 'Enter an image address, or use Remove wallpaper.'
        : null;
    const previewFailed = wallpaperText !== '' && failedPreviewUrl === wallpaperText;

    const cssDirty = css !== savedCss;
    const cssProblem = findCssProblem(css);

    const dirty = wallpaperDirty || cssDirty;
    useBeforeUnloadGuard(dirty);

    const pending = [wallpaperDirty ? 'wallpaper' : '', cssDirty ? 'custom CSS' : ''].filter(Boolean);

    const handleSave = async () => {
        if (wallpaperError) return;
        await run(async () => {
            if (cssDirty) {
                localStorage.setItem(CSS_STORAGE_KEY, css);
                injectCss(css);
                setSavedCss(css);
            }
            if (wallpaperDirty && wallpaperText) {
                const overlayVal = (wallpaperOverlay / 100).toFixed(2);
                const wp = { url: wallpaperText, overlay: parseFloat(overlayVal) };
                localStorage.setItem(WALLPAPER_STORAGE_KEY, JSON.stringify(wp));
                document.documentElement.style.setProperty(
                    '--bg-image',
                    `linear-gradient(rgba(0,0,0,${overlayVal}), rgba(0,0,0,${overlayVal})), url(${wp.url})`
                );
                setAppliedWallpaper({ url: wallpaperText, overlay: wallpaperOverlay });
                setWallpaperUrl(wallpaperText);
            }
        });
    };

    const handleDiscard = () => {
        if (previewTimer.current) clearTimeout(previewTimer.current);
        setCss(savedCss);
        injectCss(savedCss);
        setWallpaperUrl(appliedWallpaper.url);
        setWallpaperOverlay(appliedWallpaper.overlay);
        clearError();
    };

    const activeThemeName = allThemeOptions.find(t => t.id === currentTheme)?.name
        ?? (currentTheme.startsWith('custom-') ? 'Custom theme' : currentTheme);

    const sameThemes = timeTheme.dayTheme === timeTheme.nightTheme;
    const sameHours = timeTheme.dayStart === timeTheme.nightStart;

    return (
        <SettingsPage
            title="Appearance"
            icon={Palette}
            description="Personalize the look and feel of your LimeBot. Themes and the schedule apply instantly; wallpaper and custom CSS apply when you save."
            saveBar={
                <SaveBar
                    dirty={dirty}
                    saving={saving}
                    onSave={() => void handleSave()}
                    onDiscard={handleDiscard}
                    error={saveError}
                    onDismissError={clearError}
                    disabled={!!wallpaperError}
                    savedAt={savedAt}
                    saveLabel="Save & apply"
                    unsavedLabel={wallpaperError ? 'Fix the wallpaper address to save' : pending.length ? `Unsaved: ${pending.join(' and ')}` : undefined}
                />
            }
        >
            <SettingsSection
                icon={Palette}
                title="Color theme"
                description="Pick an accent or a full theme. It changes right away."
                badge={<StatusPill tone="ok">{activeThemeName}</StatusPill>}
            >
                <div className="flex flex-wrap gap-3" role="group" aria-label="Color themes">
                    {standardThemes.map((theme) => (
                        <button
                            type="button"
                            key={theme.id}
                            onClick={() => handleThemeSelect(theme.id)}
                            title={theme.name}
                            aria-pressed={currentTheme === theme.id}
                            className={cn(
                                'group relative h-9 w-9 rounded-full transition-all duration-200',
                                theme.color,
                                currentTheme === theme.id
                                    ? 'scale-110 ring-2 ring-primary ring-offset-2 ring-offset-background'
                                    : 'opacity-80 hover:scale-105 hover:opacity-100',
                            )}
                        >
                            {currentTheme === theme.id && (
                                <Check className="absolute inset-0 m-auto h-4 w-4 text-white drop-shadow-md" />
                            )}
                            <span className="sr-only">{theme.name}</span>
                        </button>
                    ))}
                </div>

                <div className="space-y-2">
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Special themes</p>
                    <div className="grid grid-cols-2 gap-1 sm:grid-cols-3" role="group" aria-label="Special themes">
                        {specialThemes.map((theme) => (
                            <button
                                type="button"
                                key={theme.id}
                                onClick={() => handleThemeSelect(theme.id)}
                                aria-pressed={currentTheme === theme.id}
                                className={cn(
                                    'flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors',
                                    currentTheme === theme.id
                                        ? 'bg-primary/10 font-medium text-primary'
                                        : 'text-foreground hover:bg-muted/60',
                                )}
                            >
                                <span className={cn('h-5 w-5 shrink-0 rounded-full', theme.color)} />
                                <span className="min-w-0 flex-1 truncate">{theme.name}</span>
                                {currentTheme === theme.id && <Check className="h-3.5 w-3.5 shrink-0" />}
                            </button>
                        ))}
                    </div>
                </div>
            </SettingsSection>

            <SettingsSection
                icon={Clock}
                title="Time-based themes"
                description="Switch between a day and a night theme automatically."
                badge={timeTheme.enabled ? <StatusPill tone="ok">Active</StatusPill> : undefined}
            >
                <Field label="Switch by time of day" htmlFor="time_theme_enabled" inline>
                    <Switch
                        id="time_theme_enabled"
                        checked={timeTheme.enabled}
                        onCheckedChange={(checked) => setTimeTheme(prev => ({ ...prev, enabled: checked }))}
                    />
                </Field>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <Field label={<span className="inline-flex items-center gap-1.5"><Sun className="h-3.5 w-3.5 text-muted-foreground" /> Day theme</span>} htmlFor="time_day_theme">
                        <Select value={timeTheme.dayTheme} onValueChange={(v) => setTimeTheme(prev => ({ ...prev, dayTheme: v }))}>
                            <SelectTrigger id="time_day_theme"><SelectValue /></SelectTrigger>
                            <SelectContent>{allThemeOptions.map(t => <SelectItem key={t.id} value={t.id}>{t.name}</SelectItem>)}</SelectContent>
                        </Select>
                    </Field>
                    <Field label={<span className="inline-flex items-center gap-1.5"><Moon className="h-3.5 w-3.5 text-muted-foreground" /> Night theme</span>} htmlFor="time_night_theme">
                        <Select value={timeTheme.nightTheme} onValueChange={(v) => setTimeTheme(prev => ({ ...prev, nightTheme: v }))}>
                            <SelectTrigger id="time_night_theme"><SelectValue /></SelectTrigger>
                            <SelectContent>{allThemeOptions.map(t => <SelectItem key={t.id} value={t.id}>{t.name}</SelectItem>)}</SelectContent>
                        </Select>
                    </Field>
                    <Field label="Day starts" htmlFor="time_day_start">
                        <Select value={String(timeTheme.dayStart)} onValueChange={(v) => setTimeTheme(prev => ({ ...prev, dayStart: Number(v) }))}>
                            <SelectTrigger id="time_day_start"><SelectValue /></SelectTrigger>
                            <SelectContent>{hourOptions.map(h => <SelectItem key={h.value} value={h.value}>{h.label}</SelectItem>)}</SelectContent>
                        </Select>
                    </Field>
                    <Field label="Night starts" htmlFor="time_night_start">
                        <Select value={String(timeTheme.nightStart)} onValueChange={(v) => setTimeTheme(prev => ({ ...prev, nightStart: Number(v) }))}>
                            <SelectTrigger id="time_night_start"><SelectValue /></SelectTrigger>
                            <SelectContent>{hourOptions.map(h => <SelectItem key={h.value} value={h.value}>{h.label}</SelectItem>)}</SelectContent>
                        </Select>
                    </Field>
                </div>
                {timeTheme.enabled && (sameThemes || sameHours) && (
                    <p role="status" className="flex items-start gap-1.5 text-xs text-amber-500">
                        <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
                        {sameThemes
                            ? 'Day and night use the same theme, so switching will not change anything.'
                            : 'Day and night start at the same hour, so one of them will never apply.'}
                    </p>
                )}
            </SettingsSection>

            <SettingsSection
                icon={ImageIcon}
                title="Wallpaper"
                description="Use a custom background image from any URL."
                badge={wallpaperActive ? <StatusPill tone="ok">Active</StatusPill> : undefined}
            >
                <Field label="Image address" htmlFor="wallpaper_url" error={wallpaperError}>
                    <Input
                        id="wallpaper_url"
                        value={wallpaperUrl}
                        onChange={(e) => setWallpaperUrl(e.target.value)}
                        placeholder="https://example.com/image.jpg"
                        aria-invalid={!!wallpaperError}
                        className={cn('font-mono text-xs', wallpaperError && invalidInputClass)}
                    />
                    {previewFailed && (
                        <p role="status" className="flex items-start gap-1.5 text-xs text-amber-500">
                            <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
                            Could not load an image from this address. Check the link, or whether the site allows other pages to show its images.
                        </p>
                    )}
                </Field>

                {wallpaperText && !previewFailed && (
                    <div className="relative h-28 overflow-hidden rounded-md border border-border/50">
                        <img
                            src={wallpaperText}
                            alt="Wallpaper preview"
                            className="h-full w-full object-cover"
                            onError={() => setFailedPreviewUrl(wallpaperText)}
                        />
                        <div
                            className="absolute inset-0"
                            style={{ background: `rgba(0,0,0,${wallpaperOverlay / 100})` }}
                        />
                        <span className="absolute bottom-1.5 right-2 font-mono text-[10px] text-white/60">
                            Overlay {wallpaperOverlay}%
                        </span>
                    </div>
                )}

                <Field
                    label={`Overlay darkness - ${wallpaperOverlay}%`}
                    htmlFor="wallpaper_overlay"
                    hint="Higher values make text easier to read. Recommended: 50-70%."
                >
                    <input
                        id="wallpaper_overlay"
                        type="range"
                        min={0}
                        max={90}
                        value={wallpaperOverlay}
                        onChange={(e) => setWallpaperOverlay(Number(e.target.value))}
                        className="h-1.5 w-full cursor-pointer accent-primary"
                    />
                </Field>

                {wallpaperActive && (
                    <Button type="button" variant="ghost" size="sm" className="-ml-2 h-8 gap-1.5 px-2 text-xs text-red-500 hover:bg-red-500/10 hover:text-red-500" onClick={removeWallpaper}>
                        <Trash2 className="h-3.5 w-3.5" /> Remove wallpaper
                    </Button>
                )}
            </SettingsSection>

            <SettingsSection
                icon={Code2}
                title="Custom CSS"
                description="Inject raw CSS directly into the page. It previews live as you type and is kept in your browser once saved."
                badge={cssDirty ? <StatusPill tone="warn">Unsaved</StatusPill> : savedCss ? <StatusPill tone="ok">Active</StatusPill> : undefined}
            >
                <Field label="Stylesheet" htmlFor="custom_css">
                    <textarea
                        id="custom_css"
                        value={css}
                        onChange={e => handleCssChange(e.target.value)}
                        placeholder={CSS_PLACEHOLDER}
                        spellCheck={false}
                        aria-invalid={!!cssProblem}
                        className={cn(
                            'min-h-[180px] w-full resize-y rounded-md border bg-muted/30 p-3 font-mono text-xs leading-relaxed text-foreground',
                            'placeholder:text-muted-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/50 transition-colors',
                            cssDirty ? 'border-primary/60' : 'border-border/60',
                        )}
                    />
                    {cssProblem && (
                        <p role="status" className="flex items-start gap-1.5 text-xs text-amber-500">
                            <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
                            {cssProblem}
                        </p>
                    )}
                </Field>
                <div className="-ml-2 flex gap-1">
                    <Button type="button" variant="ghost" size="sm" className="h-8 gap-1.5 px-2 text-xs text-muted-foreground" onClick={() => void copyCss()} disabled={!css}>
                        {copied ? <><Check className="h-3.5 w-3.5" /> Copied</> : <><Copy className="h-3.5 w-3.5" /> Copy</>}
                    </Button>
                    <Button type="button" variant="ghost" size="sm" className="h-8 gap-1.5 px-2 text-xs text-muted-foreground hover:text-red-500" onClick={clearCss} disabled={!css && !savedCss}>
                        <RotateCcw className="h-3.5 w-3.5" /> Clear
                    </Button>
                </div>
            </SettingsSection>

            <SettingsSection
                icon={Palette}
                title="Custom themes"
                description="Build your own colour theme. New themes are saved to your browser straight away."
            >
                <CustomThemeCreator
                    currentThemeId={currentTheme}
                    onThemeSelect={handleThemeSelect}
                />
            </SettingsSection>
        </SettingsPage>
    );
}

const CSS_PLACEHOLDER = `/* Paste any CSS here - it's injected live into the page.

Examples:

  /* Change chat bubble color */
  .message-bubble { background: #1a1a2e !important; }

  /* Custom scrollbar */
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-thumb { background: hsl(var(--primary)); border-radius: 2px; }

  /* Glassmorphism sidebar */
  aside { backdrop-filter: blur(12px) !important; background: rgba(0,0,0,0.4) !important; }
*/`;
