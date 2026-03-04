import { useState, useEffect, useRef } from 'react';
import { Palette, Code2, RotateCcw, Copy, Check, ChevronDown, Sun, Moon, Clock, ImageIcon, Trash2, ExternalLink } from 'lucide-react';

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CustomThemeCreator } from './CustomThemeCreator';
import { injectCss, CSS_STORAGE_KEY } from '@/lib/css-injector';

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
const DEFAULT_TIME_THEME_SETTINGS: TimeThemeSettings = {
    enabled: false,
    dayTheme: 'glacier',
    nightTheme: 'midnight-synth',
    dayStart: 7,
    nightStart: 19,
};

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

// ── Theme Definitions ────────────────────────────────────────────────────────

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
];

const allThemeOptions = [...standardThemes, ...specialThemes];

// ── Main Component ───────────────────────────────────────────────────────────

export function AppearancePage({ onThemeChange, onTimeThemeSettingsChange }: AppearancePageProps) {
    const [currentTheme, setCurrentTheme] = useState(() => localStorage.getItem('limebot-theme') || 'lime');
    const [timeTheme, setTimeTheme] = useState<TimeThemeSettings>(() => loadTimeThemeSettings());
    const [openSection, setOpenSection] = useState<string | null>(null);

    // Wallpaper state
    const [wallpaperUrl, setWallpaperUrl] = useState('');
    const [wallpaperOverlay, setWallpaperOverlay] = useState(60);
    const [wallpaperActive, setWallpaperActive] = useState(false);

    useEffect(() => {
        try {
            const saved = localStorage.getItem('limebot-wallpaper');
            if (saved) {
                const wp = JSON.parse(saved);
                setWallpaperUrl(wp.url || '');
                setWallpaperOverlay(Math.round((wp.overlay ?? 0.6) * 100));
                setWallpaperActive(!!wp.url);
            }
        } catch { /* ignore */ }
    }, []);

    const hourOptions = Array.from({ length: 24 }, (_, hour) => ({
        value: String(hour),
        label: `${String(hour).padStart(2, '0')}:00`,
    }));

    useEffect(() => {
        localStorage.setItem(TIME_THEME_STORAGE_KEY, JSON.stringify(timeTheme));
        onTimeThemeSettingsChange?.(timeTheme);
    }, [timeTheme, onTimeThemeSettingsChange]);

    const handleThemeSelect = (themeId: string) => {
        setCurrentTheme(themeId);
        onThemeChange?.(themeId);
    };

    const toggleSection = (id: string) => setOpenSection(prev => prev === id ? null : id);

    const applyWallpaper = () => {
        if (!wallpaperUrl.trim()) return;
        const overlayVal = (wallpaperOverlay / 100).toFixed(2);
        const wp = { url: wallpaperUrl.trim(), overlay: parseFloat(overlayVal) };
        localStorage.setItem('limebot-wallpaper', JSON.stringify(wp));
        document.documentElement.style.setProperty(
            '--bg-image',
            `linear-gradient(rgba(0,0,0,${overlayVal}), rgba(0,0,0,${overlayVal})), url(${wp.url})`
        );
        setWallpaperActive(true);
    };

    const removeWallpaper = () => {
        localStorage.removeItem('limebot-wallpaper');
        setWallpaperUrl('');
        setWallpaperActive(false);
        // Re-apply current theme to restore original bg
        onThemeChange?.(currentTheme);
    };

    return (
        <div className="h-full overflow-y-auto p-6 md:p-8 bg-background/50">
            <div className="max-w-2xl mx-auto space-y-5">
                <header>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <Palette className="h-6 w-6 text-primary" />
                        Appearance
                    </h1>
                    <p className="text-muted-foreground text-sm mt-1">
                        Personalize the look and feel of your LimeBot.
                    </p>
                </header>

                {/* ── Color Themes (compact swatch grid) ──────────────── */}
                <div className="rounded-xl border border-border bg-card text-card-foreground shadow">
                    <div className="p-5 pb-4">
                        <h3 className="font-semibold text-sm flex items-center gap-2">
                            <Palette className="h-4 w-4 text-primary" />
                            Color Themes
                        </h3>

                        {/* Standard — compact circular swatches */}
                        <div className="flex flex-wrap gap-3 mt-4">
                            {standardThemes.map((theme) => (
                                <button
                                    key={theme.id}
                                    onClick={() => handleThemeSelect(theme.id)}
                                    title={theme.name}
                                    className={`
                                        group relative w-10 h-10 rounded-full transition-all duration-200
                                        ${theme.color}
                                        ${currentTheme === theme.id
                                            ? 'ring-2 ring-primary ring-offset-2 ring-offset-card scale-110'
                                            : 'hover:scale-105 opacity-80 hover:opacity-100'}
                                    `}
                                >
                                    {currentTheme === theme.id && (
                                        <Check className="absolute inset-0 m-auto h-4 w-4 text-white drop-shadow-md" />
                                    )}
                                    <span className="sr-only">{theme.name}</span>
                                </button>
                            ))}
                        </div>

                        {/* Label row for active theme name */}
                        <p className="text-xs text-muted-foreground mt-2">
                            Active: <span className="text-foreground font-medium">{
                                [...standardThemes, ...specialThemes].find(t => t.id === currentTheme)?.name
                                ?? currentTheme
                            }</span>
                        </p>

                        {/* Special themes divider */}
                        <div className="relative mt-5 mb-3">
                            <div className="absolute inset-0 flex items-center"><span className="w-full border-t" /></div>
                            <div className="relative flex justify-center text-[10px] uppercase tracking-widest">
                                <span className="bg-card px-2 text-muted-foreground font-semibold">Special Themes</span>
                            </div>
                        </div>

                        {/* Special — grid of mini cards */}
                        <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                            {specialThemes.map((theme) => (
                                <button
                                    key={theme.id}
                                    onClick={() => handleThemeSelect(theme.id)}
                                    className={`
                                        relative flex flex-col items-center gap-1.5 p-2.5 rounded-lg border transition-all
                                        ${currentTheme === theme.id
                                            ? 'border-primary bg-primary/5 shadow-sm'
                                            : 'border-transparent bg-muted/20 hover:bg-muted/40 hover:border-primary/20'}
                                    `}
                                >
                                    <div className={`w-7 h-7 rounded-full shadow-sm shrink-0 ${theme.color}`} />
                                    <span className="text-[10px] leading-tight font-medium text-center line-clamp-1">{theme.name}</span>
                                    {currentTheme === theme.id && (
                                        <div className="absolute top-1 right-1 h-1.5 w-1.5 rounded-full bg-primary" />
                                    )}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>

                {/* ── Wallpaper (collapsible) ─────────────────────────── */}
                <div className="rounded-xl border border-border bg-card text-card-foreground shadow">
                    <button
                        onClick={() => toggleSection('wallpaper')}
                        className="w-full flex items-center justify-between p-4 text-left"
                    >
                        <div className="flex items-center gap-2">
                            <ImageIcon className="h-4 w-4 text-primary" />
                            <div>
                                <h3 className="font-semibold text-sm">Wallpaper</h3>
                                <p className="text-[11px] text-muted-foreground">Use a custom background image from any URL.</p>
                            </div>
                        </div>
                        <div className="flex items-center gap-3">
                            {wallpaperActive && <Badge variant="secondary" className="text-[10px] h-5">Active</Badge>}
                            <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${openSection === 'wallpaper' ? 'rotate-180' : ''}`} />
                        </div>
                    </button>
                    {openSection === 'wallpaper' && (
                        <div className="px-4 pb-4 space-y-3 border-t pt-3">
                            <div className="flex gap-2">
                                <Input
                                    value={wallpaperUrl}
                                    onChange={(e) => setWallpaperUrl(e.target.value)}
                                    placeholder="https://example.com/image.jpg"
                                    className="h-8 text-xs font-mono flex-1"
                                />
                                <Button size="sm" className="h-8 text-xs gap-1" onClick={applyWallpaper} disabled={!wallpaperUrl.trim()}>
                                    <ExternalLink className="h-3 w-3" /> Apply
                                </Button>
                            </div>

                            {/* Preview */}
                            {wallpaperUrl.trim() && (
                                <div className="relative rounded-lg overflow-hidden border border-border h-28">
                                    <img
                                        src={wallpaperUrl}
                                        alt="Wallpaper preview"
                                        className="w-full h-full object-cover"
                                        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                                    />
                                    <div
                                        className="absolute inset-0"
                                        style={{ background: `rgba(0,0,0,${wallpaperOverlay / 100})` }}
                                    />
                                    <span className="absolute bottom-1.5 right-2 text-[10px] text-white/60 font-mono">
                                        Overlay {wallpaperOverlay}%
                                    </span>
                                </div>
                            )}

                            {/* Overlay slider */}
                            <div className="space-y-1">
                                <Label className="text-xs text-muted-foreground">Overlay Darkness — {wallpaperOverlay}%</Label>
                                <input
                                    type="range"
                                    min={0}
                                    max={90}
                                    value={wallpaperOverlay}
                                    onChange={(e) => setWallpaperOverlay(Number(e.target.value))}
                                    className="w-full h-1.5 accent-primary cursor-pointer"
                                />
                                <p className="text-[10px] text-muted-foreground">
                                    Higher values make text easier to read. Recommended: 50–70%.
                                </p>
                            </div>

                            {wallpaperActive && (
                                <Button variant="ghost" size="sm" className="h-7 text-xs text-destructive/70 hover:text-destructive gap-1" onClick={removeWallpaper}>
                                    <Trash2 className="h-3 w-3" /> Remove Wallpaper
                                </Button>
                            )}
                        </div>
                    )}
                </div>

                {/* ── Time-based Themes (inline collapsible) ──────────── */}
                <div className="rounded-xl border border-border bg-card text-card-foreground shadow">
                    <button
                        onClick={() => toggleSection('time')}
                        className="w-full flex items-center justify-between p-4 text-left"
                    >
                        <div className="flex items-center gap-2">
                            <Clock className="h-4 w-4 text-primary" />
                            <div>
                                <h3 className="font-semibold text-sm">Time-based Themes</h3>
                                <p className="text-[11px] text-muted-foreground">Auto-switch between day and night.</p>
                            </div>
                        </div>
                        <div className="flex items-center gap-3">
                            {timeTheme.enabled && <Badge variant="secondary" className="text-[10px] h-5">Active</Badge>}
                            <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${openSection === 'time' ? 'rotate-180' : ''}`} />
                        </div>
                    </button>
                    {openSection === 'time' && (
                        <div className="px-4 pb-4 space-y-3 border-t pt-3">
                            <div className="flex items-center justify-between">
                                <Label className="text-xs">Enable time-based switching</Label>
                                <Switch
                                    checked={timeTheme.enabled}
                                    onCheckedChange={(checked) => setTimeTheme(prev => ({ ...prev, enabled: checked }))}
                                />
                            </div>
                            <div className="grid grid-cols-2 gap-3">
                                <div className="space-y-1">
                                    <Label className="text-[11px] text-muted-foreground flex items-center gap-1"><Sun className="h-3 w-3" /> Day Theme</Label>
                                    <Select value={timeTheme.dayTheme} onValueChange={(v) => setTimeTheme(prev => ({ ...prev, dayTheme: v }))}>
                                        <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                                        <SelectContent>{allThemeOptions.map(t => <SelectItem key={t.id} value={t.id}>{t.name}</SelectItem>)}</SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-[11px] text-muted-foreground flex items-center gap-1"><Moon className="h-3 w-3" /> Night Theme</Label>
                                    <Select value={timeTheme.nightTheme} onValueChange={(v) => setTimeTheme(prev => ({ ...prev, nightTheme: v }))}>
                                        <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                                        <SelectContent>{allThemeOptions.map(t => <SelectItem key={t.id} value={t.id}>{t.name}</SelectItem>)}</SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-[11px] text-muted-foreground">Day Starts</Label>
                                    <Select value={String(timeTheme.dayStart)} onValueChange={(v) => setTimeTheme(prev => ({ ...prev, dayStart: Number(v) }))}>
                                        <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                                        <SelectContent>{hourOptions.map(h => <SelectItem key={h.value} value={h.value}>{h.label}</SelectItem>)}</SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-[11px] text-muted-foreground">Night Starts</Label>
                                    <Select value={String(timeTheme.nightStart)} onValueChange={(v) => setTimeTheme(prev => ({ ...prev, nightStart: Number(v) }))}>
                                        <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                                        <SelectContent>{hourOptions.map(h => <SelectItem key={h.value} value={h.value}>{h.label}</SelectItem>)}</SelectContent>
                                    </Select>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* ── Custom Themes (collapsible) ─────────────────────── */}
                <div className="rounded-xl border border-border bg-card text-card-foreground shadow">
                    <button
                        onClick={() => toggleSection('custom')}
                        className="w-full flex items-center justify-between p-4 text-left"
                    >
                        <div className="flex items-center gap-2">
                            <Palette className="h-4 w-4 text-primary" />
                            <h3 className="font-semibold text-sm">Custom Themes</h3>
                        </div>
                        <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${openSection === 'custom' ? 'rotate-180' : ''}`} />
                    </button>
                    {openSection === 'custom' && (
                        <div className="px-4 pb-4 border-t pt-3">
                            <CustomThemeCreator
                                currentThemeId={currentTheme}
                                onThemeSelect={handleThemeSelect}
                            />
                        </div>
                    )}
                </div>

                {/* ── Custom CSS (collapsible) ────────────────────────── */}
                <div className="rounded-xl border border-border bg-card text-card-foreground shadow">
                    <button
                        onClick={() => toggleSection('css')}
                        className="w-full flex items-center justify-between p-4 text-left"
                    >
                        <div className="flex items-center gap-2">
                            <Code2 className="h-4 w-4 text-primary" />
                            <div>
                                <h3 className="font-semibold text-sm">Custom CSS</h3>
                                <p className="text-[11px] text-muted-foreground">Inject raw CSS directly into the page.</p>
                            </div>
                        </div>
                        <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${openSection === 'css' ? 'rotate-180' : ''}`} />
                    </button>
                    {openSection === 'css' && (
                        <div className="px-4 pb-4 border-t pt-3">
                            <CustomCssEditor />
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

// ── Custom CSS Editor ─────────────────────────────────────────────────────────

const CSS_PLACEHOLDER = `/* Paste any CSS here — it's injected live into the page.

Examples:

  /* Change chat bubble color */
  .message-bubble { background: #1a1a2e !important; }

  /* Custom scrollbar */
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-thumb { background: hsl(var(--primary)); border-radius: 2px; }

  /* Glassmorphism sidebar */
  aside { backdrop-filter: blur(12px) !important; background: rgba(0,0,0,0.4) !important; }
*/`;

function CustomCssEditor() {
    const [css, setCss] = useState(() => localStorage.getItem(CSS_STORAGE_KEY) ?? '');
    const [saved, setSaved] = useState(true);
    const [copied, setCopied] = useState(false);
    const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Apply saved CSS on mount
    useEffect(() => { injectCss(css); }, []);

    const handleChange = (value: string) => {
        setCss(value);
        setSaved(false);
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(() => injectCss(value), 300);
    };

    const handleSave = () => {
        localStorage.setItem(CSS_STORAGE_KEY, css);
        injectCss(css);
        setSaved(true);
    };

    const handleReset = () => {
        setCss('');
        localStorage.removeItem(CSS_STORAGE_KEY);
        injectCss('');
        setSaved(true);
    };

    const handleCopy = async () => {
        await navigator.clipboard.writeText(css);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
    };

    return (
        <div className="space-y-3">
            <div className="relative">
                <textarea
                    value={css}
                    onChange={e => handleChange(e.target.value)}
                    placeholder={CSS_PLACEHOLDER}
                    spellCheck={false}
                    className={`
                        w-full min-h-[180px] resize-y rounded-lg border bg-muted/30 p-3
                        font-mono text-xs leading-relaxed text-foreground
                        placeholder:text-muted-foreground/40
                        focus:outline-none focus:ring-2 focus:ring-primary/50
                        transition-colors
                        ${!saved ? 'border-primary/60' : 'border-border'}
                    `}
                />
                {!saved && (
                    <span className="absolute top-2 right-2 text-[10px] text-primary/70 font-mono">
                        unsaved
                    </span>
                )}
            </div>

            <div className="flex items-center justify-between">
                <div className="flex gap-2">
                    <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs text-muted-foreground" onClick={handleCopy} disabled={!css}>
                        {copied ? <><Check className="h-3 w-3" /> Copied</> : <><Copy className="h-3 w-3" /> Copy</>}
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs text-destructive/70 hover:text-destructive" onClick={handleReset} disabled={!css}>
                        <RotateCcw className="h-3 w-3" /> Clear
                    </Button>
                </div>
                <Button size="sm" className="h-7 gap-1.5 text-xs" onClick={handleSave} disabled={saved}>
                    <Code2 className="h-3 w-3" />
                    {saved ? 'Saved' : 'Save & Apply'}
                </Button>
            </div>

            <p className="text-[10px] text-muted-foreground">
                CSS is saved to your browser and re-applied on every page load.
            </p>
        </div>
    );
}
