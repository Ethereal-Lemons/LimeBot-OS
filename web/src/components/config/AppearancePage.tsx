import { useState, useEffect, useRef } from 'react';
import { Palette, Code2, RotateCcw, Copy, Check } from 'lucide-react';

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

export function AppearancePage({ onThemeChange, onTimeThemeSettingsChange }: AppearancePageProps) {
    const [currentTheme, setCurrentTheme] = useState(() => localStorage.getItem('limebot-theme') || 'lime');
    const [timeTheme, setTimeTheme] = useState<TimeThemeSettings>(() => loadTimeThemeSettings());

    const standardThemes = [
        { id: 'lime', name: 'Cyber Lime', color: 'bg-[#84cc16]' },
        { id: 'purple', name: 'Nebula Purple', color: 'bg-[#8b5cf6]' },
        { id: 'blue', name: 'Electric Blue', color: 'bg-[#3b82f6]' },
        { id: 'orange', name: 'Sunset Orange', color: 'bg-[#f97316]' },
        { id: 'red', name: 'Crimson Red', color: 'bg-[#ef4444]' },
        { id: 'pink', name: 'Hot Pink', color: 'bg-[#db2777]' },
    ];

    const specialThemes = [
        { id: 'frutiger-aero', name: 'Frutiger Aero', color: 'bg-gradient-to-br from-cyan-400 to-green-400' },
        { id: 'angelcore-racing', name: 'Angelcore Racing', color: 'bg-gradient-to-br from-black to-zinc-800' },
        { id: 'cyberpunk', name: 'Cyberpunk Neon', color: 'bg-gradient-to-br from-pink-500 to-cyan-500' },
        { id: 'retro-terminal', name: 'Retro Terminal', color: 'bg-black border-2 border-green-500' },
        { id: 'midnight-synth', name: 'Midnight Synthesizer', color: 'bg-gradient-to-br from-indigo-900 to-purple-900 border-2 border-yellow-500' },
        { id: 'paperback', name: 'Paperback Writer', color: 'bg-[#f5f5dc] border-2 border-[#8b4513]' },
        { id: 'synthwave-84', name: 'Synthwave 84', color: 'bg-gradient-to-br from-purple-600 to-pink-500' },
        { id: 'glacier', name: 'Glacier', color: 'bg-gradient-to-br from-blue-100 to-cyan-200' },
        { id: 'coffee-shop', name: 'Coffee Shop', color: 'bg-[#6d4c41]' },
        { id: 'sakura', name: 'Sakura', color: 'bg-gradient-to-br from-pink-200 to-pink-400' },
        { id: 'sunset-mirage', name: 'Sunset Mirage', color: 'bg-gradient-to-br from-orange-300 via-rose-400 to-fuchsia-500' },
        { id: 'emerald-depths', name: 'Emerald Depths', color: 'bg-gradient-to-br from-emerald-700 via-teal-700 to-cyan-900' },
    ];
    const themeOptions = [...standardThemes, ...specialThemes];
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

    return (
        <div className="h-full overflow-y-auto p-6 md:p-8 bg-background/50">
            <div className="max-w-2xl mx-auto space-y-8">
                <header>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <Palette className="h-6 w-6 text-primary" />
                        Appearance
                    </h1>
                    <p className="text-muted-foreground mt-1">
                        Personalize the look and feel of your LimeBot.
                    </p>
                </header>

                <div className="space-y-6">
                    <div className="rounded-xl border border-border bg-card text-card-foreground shadow">
                        <div className="flex flex-col space-y-1.5 p-6">
                            <h3 className="font-semibold leading-none tracking-tight flex items-center gap-2">
                                <Palette className="h-5 w-5 text-primary" />
                                Theme Selection
                            </h3>
                            <p className="text-sm text-muted-foreground">
                                Choose a color scheme for the application.
                            </p>
                        </div>
                        <div className="p-6 pt-0 space-y-6">
                            <div className="space-y-6">
                                {/* Standard Color Themes */}
                                <div>
                                    <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">
                                        Standard Colors
                                    </h4>
                                    <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                                        {standardThemes.map((theme) => (
                                            <ThemeButton
                                                key={theme.id}
                                                theme={theme}
                                                isActive={currentTheme === theme.id}
                                                onClick={() => handleThemeSelect(theme.id)}
                                            />
                                        ))}
                                    </div>
                                </div>

                                {/* Divider */}
                                <div className="relative">
                                    <div className="absolute inset-0 flex items-center">
                                        <span className="w-full border-t" />
                                    </div>
                                    <div className="relative flex justify-center text-xs uppercase">
                                        <span className="bg-card px-2 text-muted-foreground font-semibold">
                                            Special Themes
                                        </span>
                                    </div>
                                </div>

                                {/* Special Color Themes */}
                                <div>
                                    <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                                        {specialThemes.map((theme) => (
                                            <ThemeButton
                                                key={theme.id}
                                                theme={theme}
                                                isActive={currentTheme === theme.id}
                                                onClick={() => handleThemeSelect(theme.id)}
                                                isSpecial
                                            />
                                        ))}
                                    </div>
                                </div>

                                <div className="rounded-lg border bg-muted/20 p-4 space-y-4">
                                    <div className="flex items-center justify-between gap-3">
                                        <div>
                                            <h4 className="text-sm font-semibold">Time-based Themes</h4>
                                            <p className="text-xs text-muted-foreground">
                                                Automatically switch between a day and night theme.
                                            </p>
                                        </div>
                                        <Switch
                                            checked={timeTheme.enabled}
                                            onCheckedChange={(checked) =>
                                                setTimeTheme(prev => ({ ...prev, enabled: checked }))
                                            }
                                        />
                                    </div>

                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                        <div className="space-y-2">
                                            <Label className="text-xs text-muted-foreground">Day Theme</Label>
                                            <Select
                                                value={timeTheme.dayTheme}
                                                onValueChange={(value) => setTimeTheme(prev => ({ ...prev, dayTheme: value }))}
                                            >
                                                <SelectTrigger><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    {themeOptions.map(theme => (
                                                        <SelectItem key={theme.id} value={theme.id}>{theme.name}</SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                        <div className="space-y-2">
                                            <Label className="text-xs text-muted-foreground">Night Theme</Label>
                                            <Select
                                                value={timeTheme.nightTheme}
                                                onValueChange={(value) => setTimeTheme(prev => ({ ...prev, nightTheme: value }))}
                                            >
                                                <SelectTrigger><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    {themeOptions.map(theme => (
                                                        <SelectItem key={theme.id} value={theme.id}>{theme.name}</SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                        <div className="space-y-2">
                                            <Label className="text-xs text-muted-foreground">Day Starts</Label>
                                            <Select
                                                value={String(timeTheme.dayStart)}
                                                onValueChange={(value) => setTimeTheme(prev => ({ ...prev, dayStart: Number(value) }))}
                                            >
                                                <SelectTrigger><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    {hourOptions.map(hour => (
                                                        <SelectItem key={hour.value} value={hour.value}>{hour.label}</SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                        <div className="space-y-2">
                                            <Label className="text-xs text-muted-foreground">Night Starts</Label>
                                            <Select
                                                value={String(timeTheme.nightStart)}
                                                onValueChange={(value) => setTimeTheme(prev => ({ ...prev, nightStart: Number(value) }))}
                                            >
                                                <SelectTrigger><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    {hourOptions.map(hour => (
                                                        <SelectItem key={hour.value} value={hour.value}>{hour.label}</SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>
                                </div>

                                {/* Divider */}
                                <div className="relative">
                                    <div className="absolute inset-0 flex items-center">
                                        <span className="w-full border-t" />
                                    </div>
                                    <div className="relative flex justify-center text-xs uppercase">
                                        <span className="bg-card px-2 text-muted-foreground font-semibold">
                                            My Custom Themes
                                        </span>
                                    </div>
                                </div>

                                {/* Custom Theme Creator */}
                                <CustomThemeCreator
                                    currentThemeId={currentTheme}
                                    onThemeSelect={handleThemeSelect}
                                />
                            </div>
                        </div>
                    </div>
                </div>
                {/* Custom CSS Card */}
                <CustomCssEditor />

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
        <div className="rounded-xl border border-border bg-card text-card-foreground shadow">
            <div className="flex flex-col space-y-1.5 p-6 pb-3">
                <h3 className="font-semibold leading-none tracking-tight flex items-center gap-2">
                    <Code2 className="h-5 w-5 text-primary" />
                    Custom CSS
                </h3>
                <p className="text-sm text-muted-foreground">
                    Inject raw CSS directly into the page. Applied live as you type.
                </p>
            </div>

            <div className="p-6 pt-2 space-y-3">
                <div className="relative">
                    <textarea
                        value={css}
                        onChange={e => handleChange(e.target.value)}
                        placeholder={CSS_PLACEHOLDER}
                        spellCheck={false}
                        className={`
                            w-full min-h-[220px] resize-y rounded-lg border bg-muted/30 p-3
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
                        <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 gap-1.5 text-xs text-muted-foreground"
                            onClick={handleCopy}
                            disabled={!css}
                        >
                            {copied
                                ? <><Check className="h-3 w-3" /> Copied</>
                                : <><Copy className="h-3 w-3" /> Copy</>
                            }
                        </Button>
                        <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 gap-1.5 text-xs text-destructive/70 hover:text-destructive"
                            onClick={handleReset}
                            disabled={!css}
                        >
                            <RotateCcw className="h-3 w-3" />
                            Clear
                        </Button>
                    </div>

                    <Button
                        size="sm"
                        className="h-7 gap-1.5 text-xs"
                        onClick={handleSave}
                        disabled={saved}
                    >
                        <Code2 className="h-3 w-3" />
                        {saved ? 'Saved' : 'Save & Apply'}
                    </Button>
                </div>

                <p className="text-[10px] text-muted-foreground">
                    CSS is saved to your browser and re-applied on every page load.
                    Use browser DevTools to inspect element class names.
                </p>
            </div>
        </div>
    );
}

function ThemeButton({ theme, isActive, onClick, isSpecial }: { theme: any, isActive: boolean, onClick: () => void, isSpecial?: boolean }) {
    return (
        <button
            onClick={onClick}
            className={`
                relative flex items-center gap-3 p-3 rounded-xl border-2 transition-all group
                ${isActive
                    ? 'border-primary bg-primary/5 shadow-md'
                    : 'border-transparent bg-muted/30 hover:border-primary/30'}
                ${isSpecial ? 'h-auto py-4' : ''}
            `}
        >
            <div className={`
                rounded-full shadow-sm ${theme.color}
                ${isSpecial ? 'w-8 h-8 ring-2 ring-offset-2 ring-offset-card ring-primary/20' : 'w-6 h-6'}
            `} />
            <div className="flex flex-col items-start text-left">
                <span className={`font-medium ${isSpecial ? 'text-base' : 'text-sm'}`}>{theme.name}</span>
                {isSpecial && <span className="text-[10px] text-muted-foreground">Full Experience</span>}
            </div>
            {isActive && (
                <Badge variant="secondary" className="absolute right-2 top-2 h-2 w-2 p-0 rounded-full bg-primary" />
            )}
        </button>
    );
}
