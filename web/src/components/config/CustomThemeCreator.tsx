import { useState } from 'react';
import { Plus, Trash2, Save, Palette } from 'lucide-react';
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { EmptyState, Field, FieldError, StatusPill, invalidInputClass } from "@/components/settings/kit";
import { cn } from "@/lib/utils";

// Helper to convert Hex to HSL for CSS variables
const hexToHsl = (hex: string): string => {
    let r = 0, g = 0, b = 0;
    if (hex.length === 4) {
        r = parseInt("0x" + hex[1] + hex[1]);
        g = parseInt("0x" + hex[2] + hex[2]);
        b = parseInt("0x" + hex[3] + hex[3]);
    } else if (hex.length === 7) {
        r = parseInt("0x" + hex[1] + hex[2]);
        g = parseInt("0x" + hex[3] + hex[4]);
        b = parseInt("0x" + hex[5] + hex[6]);
    }
    r /= 255;
    g /= 255;
    b /= 255;
    const cmin = Math.min(r, g, b),
        cmax = Math.max(r, g, b),
        delta = cmax - cmin;
    let h = 0, s = 0, l = 0;

    if (delta === 0) h = 0;
    else if (cmax === r) h = ((g - b) / delta) % 6;
    else if (cmax === g) h = (b - r) / delta + 2;
    else h = (r - g) / delta + 4;

    h = Math.round(h * 60);
    if (h < 0) h += 360;
    l = (cmax + cmin) / 2;
    s = delta === 0 ? 0 : delta / (1 - Math.abs(2 * l - 1));
    s = +(s * 100).toFixed(1);
    l = +(l * 100).toFixed(1);

    return `${h} ${s}% ${l}%`;
};

const HEX_PATTERN = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;
const HEX_ERROR = "Use a hex colour like #1a2b3c.";

export interface CustomTheme {
    id: string;
    name: string;
    variables: Record<string, string>;
    bgImage: string;
    createdAt: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringRecord(value: unknown): value is Record<string, string> {
    return isRecord(value) && Object.values(value).every((item) => typeof item === "string");
}

function isCustomTheme(value: unknown): value is CustomTheme {
    return isRecord(value)
        && typeof value.id === "string"
        && typeof value.name === "string"
        && isStringRecord(value.variables)
        && typeof value.bgImage === "string"
        && typeof value.createdAt === "number";
}

function loadCustomThemes(): CustomTheme[] {
    try {
        const saved = localStorage.getItem('limebot-custom-themes');
        if (!saved) return [];
        const parsed: unknown = JSON.parse(saved);
        return Array.isArray(parsed) ? parsed.filter(isCustomTheme) : [];
    } catch (error: unknown) {
        console.error("Failed to parse custom themes", error);
        return [];
    }
}

/** A colour picker paired with its hex text box; the text box flags bad input. */
function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
    const valid = HEX_PATTERN.test(value);
    return (
        <Field label={label} error={valid ? null : HEX_ERROR} className="space-y-1.5">
            <div className="flex gap-2">
                <Input
                    type="color"
                    aria-label={`${label} picker`}
                    className="h-9 w-10 shrink-0 cursor-pointer p-1"
                    value={value.length === 7 && valid ? value : '#000000'}
                    onChange={(e) => onChange(e.target.value)}
                />
                <Input
                    aria-label={`${label} hex value`}
                    aria-invalid={!valid}
                    className={cn("h-9 font-mono text-xs", !valid && invalidInputClass)}
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                />
            </div>
        </Field>
    );
}

interface CustomThemeCreatorProps {
    onThemeSelect: (themeId: string) => void;
    currentThemeId: string;
}

export function CustomThemeCreator({ onThemeSelect, currentThemeId }: CustomThemeCreatorProps) {
    const [themes, setThemes] = useState<CustomTheme[]>(loadCustomThemes);
    const [isCreating, setIsCreating] = useState(false);

    // Editor State
    const [name, setName] = useState('My New Theme');
    const [baseMode, setBaseMode] = useState<'light' | 'dark'>('dark');

    // Colors (Hex for picker)
    const [colors, setColors] = useState({
        primary: '#ec4899', // Pink-500
        background: '#09090b', // Zinc-950
        foreground: '#fafafa', // Zinc-50
        card: '#18181b', // Zinc-900
        border: '#27272a', // Zinc-800
        accent: '#f472b6', // Pink-400
    });

    const [radius, setRadius] = useState(0.5);
    const [gradientStart, setGradientStart] = useState('#2e1065'); // violet-950
    const [gradientEnd, setGradientEnd] = useState('#4c1d95'); // violet-900
    const [gradientDir, setGradientDir] = useState('135deg');

    const nameError = name.trim() ? null : "Give the theme a name.";
    const hasBadColor = Object.values(colors).some((value) => !HEX_PATTERN.test(value));
    const canCreate = !nameError && !hasBadColor;

    const saveThemes = (newThemes: CustomTheme[]) => {
        setThemes(newThemes);
        localStorage.setItem('limebot-custom-themes', JSON.stringify(newThemes));
    };

    const handleCreate = () => {
        if (!canCreate) return;
        const id = `custom-${Date.now()}`;

        // Construct CSS variables
        const variables: Record<string, string> = {
            '--primary': hexToHsl(colors.primary),
            '--primary-foreground': baseMode === 'dark' ? '0 0% 100%' : '0 0% 100%',

            '--background': hexToHsl(colors.background),
            '--foreground': hexToHsl(colors.foreground),

            '--card': `${hexToHsl(colors.card)} / ${baseMode === 'dark' ? '0.8' : '0.9'}`,
            '--card-foreground': hexToHsl(colors.foreground),

            '--popover': `${hexToHsl(colors.card)} / 0.9`,
            '--popover-foreground': hexToHsl(colors.foreground),

            '--border': hexToHsl(colors.border),
            '--input': hexToHsl(colors.border),

            '--accent': hexToHsl(colors.accent),
            '--accent-foreground': baseMode === 'dark' ? '0 0% 100%' : '0 0% 100%',

            '--ring': hexToHsl(colors.primary),
            '--radius': `${radius}rem`,

            '--muted': baseMode === 'dark' ? '240 3.7% 15.9%' : '240 4.8% 95.9%',
            '--muted-foreground': baseMode === 'dark' ? '240 5% 64.9%' : '240 3.8% 46.1%',
        };

        const bgImage = `linear-gradient(${gradientDir}, ${gradientStart}, ${gradientEnd})`;

        const newTheme: CustomTheme = {
            id,
            name: name.trim(),
            variables,
            bgImage,
            createdAt: Date.now()
        };

        saveThemes([...themes, newTheme]);
        setIsCreating(false);
        onThemeSelect(id);
    };

    const handleDelete = (id: string) => {
        const newThemes = themes.filter(t => t.id !== id);
        saveThemes(newThemes);
        if (currentThemeId === id) {
            onThemeSelect('lime'); // Fallback to default
        }
    };

    const setColor = (key: keyof typeof colors) => (value: string) => setColors((prev) => ({ ...prev, [key]: value }));

    // Preview Styles
    const previewStyle = {
        '--background': hexToHsl(colors.background),
        '--foreground': hexToHsl(colors.foreground),
        '--primary': hexToHsl(colors.primary),
        '--card': hexToHsl(colors.card),
        '--border': hexToHsl(colors.border),
        '--radius': `${radius}rem`,
        backgroundImage: `linear-gradient(${gradientDir}, ${gradientStart}, ${gradientEnd})`,
    } as React.CSSProperties;

    return (
        <div className="space-y-5">
            {!isCreating && (
                <>
                    {themes.length > 0 ? (
                        <ul className="divide-y divide-border/40">
                            {themes.map(theme => (
                                <li key={theme.id} className="flex items-center gap-1 py-1.5 first:pt-0">
                                    <button
                                        type="button"
                                        onClick={() => onThemeSelect(theme.id)}
                                        className={cn(
                                            "flex min-w-0 flex-1 items-center gap-3 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted/60",
                                            currentThemeId === theme.id && "bg-primary/10",
                                        )}
                                    >
                                        <span
                                            className="h-6 w-6 shrink-0 rounded-full ring-1 ring-border"
                                            style={{ background: theme.bgImage }}
                                        />
                                        <span className="min-w-0 flex-1 truncate text-sm font-medium">{theme.name}</span>
                                        {currentThemeId === theme.id && <StatusPill tone="ok">Active</StatusPill>}
                                    </button>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8 shrink-0 text-muted-foreground hover:bg-red-500/10 hover:text-red-500"
                                        onClick={() => handleDelete(theme.id)}
                                        title={`Delete ${theme.name}`}
                                        aria-label={`Delete ${theme.name}`}
                                    >
                                        <Trash2 className="h-3.5 w-3.5" />
                                    </Button>
                                </li>
                            ))}
                        </ul>
                    ) : (
                        <EmptyState
                            icon={Palette}
                            title="No custom themes yet"
                            description="Pick your own colours, corner radius and background gradient, then switch to it like any other theme."
                            className="py-6"
                        />
                    )}
                    <Button type="button" variant="outline" size="sm" onClick={() => setIsCreating(true)} className="gap-2">
                        <Plus className="h-3.5 w-3.5" />
                        Create new theme
                    </Button>
                </>
            )}

            {isCreating && (
                <div className="space-y-6 animate-in fade-in duration-200">
                    <Field label="Theme name" htmlFor="custom_theme_name" error={nameError}>
                        <Input
                            id="custom_theme_name"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            placeholder="e.g. Midnight Runner"
                            aria-invalid={!!nameError}
                            className={cn(nameError && invalidInputClass)}
                        />
                    </Field>

                    <div className="grid grid-cols-1 gap-x-8 gap-y-6 md:grid-cols-2">
                        <div className="space-y-4">
                            <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Base colors</p>
                            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                                <ColorField label="Background" value={colors.background} onChange={setColor('background')} />
                                <ColorField label="Foreground (text)" value={colors.foreground} onChange={setColor('foreground')} />
                                <ColorField label="Card / surface" value={colors.card} onChange={setColor('card')} />
                                <ColorField label="Borders" value={colors.border} onChange={setColor('border')} />
                            </div>

                            <p className="pt-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Accents</p>
                            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                                <ColorField label="Primary" value={colors.primary} onChange={setColor('primary')} />
                                <ColorField label="Secondary accent" value={colors.accent} onChange={setColor('accent')} />
                            </div>

                            <div className="space-y-2 pt-2">
                                <Label htmlFor="custom_theme_radius" className="text-xs">Border radius: {radius}rem</Label>
                                <input
                                    id="custom_theme_radius"
                                    type="range"
                                    min="0" max="2" step="0.1"
                                    className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-secondary accent-primary"
                                    value={radius}
                                    onChange={(e) => setRadius(parseFloat(e.target.value))}
                                />
                            </div>
                        </div>

                        <div className="space-y-4">
                            <Field
                                label="Dark mode base"
                                htmlFor="custom_theme_dark"
                                hint="Start with dark defaults"
                                inline
                            >
                                <Switch
                                    id="custom_theme_dark"
                                    checked={baseMode === 'dark'}
                                    onCheckedChange={(checked) => setBaseMode(checked ? 'dark' : 'light')}
                                />
                            </Field>

                            <p className="pt-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Background gradient</p>
                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-1.5">
                                    <Label htmlFor="custom_theme_from" className="text-xs">From color</Label>
                                    <Input id="custom_theme_from" type="color" className="h-9 w-10 cursor-pointer p-1" value={gradientStart} onChange={e => setGradientStart(e.target.value)} />
                                </div>
                                <div className="space-y-1.5">
                                    <Label htmlFor="custom_theme_to" className="text-xs">To color</Label>
                                    <Input id="custom_theme_to" type="color" className="h-9 w-10 cursor-pointer p-1" value={gradientEnd} onChange={e => setGradientEnd(e.target.value)} />
                                </div>
                            </div>

                            <div className="space-y-1.5">
                                <Label htmlFor="custom_theme_dir" className="text-xs">Direction</Label>
                                <Select value={gradientDir} onValueChange={setGradientDir}>
                                    <SelectTrigger id="custom_theme_dir">
                                        <SelectValue placeholder="Select direction" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="to bottom">To Bottom</SelectItem>
                                        <SelectItem value="to top">To Top</SelectItem>
                                        <SelectItem value="to right">To Right</SelectItem>
                                        <SelectItem value="to left">To Left</SelectItem>
                                        <SelectItem value="45deg">45 Degrees</SelectItem>
                                        <SelectItem value="135deg">135 Degrees</SelectItem>
                                        <SelectItem value="225deg">225 Degrees</SelectItem>
                                        <SelectItem value="315deg">315 Degrees</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>

                            <p className="pt-2 text-xs text-muted-foreground">Live preview</p>
                            <div
                                className="relative flex h-44 flex-col items-center justify-center gap-3 overflow-hidden rounded-md border border-border/50 p-4"
                                style={previewStyle}
                            >
                                {/* Mock Card */}
                                <div
                                    className="w-full max-w-[200px] p-4 rounded-[var(--radius)] border border-[hsl(var(--border))] bg-[hsl(var(--card))]"
                                    style={{ color: 'hsl(var(--foreground))' }}
                                >
                                    <div className="h-2 w-16 rounded-full bg-[hsl(var(--primary))] mb-3" />
                                    <div className="h-2 w-full rounded-full bg-[hsl(var(--foreground))/0.2] mb-2" />
                                    <div className="h-2 w-2/3 rounded-full bg-[hsl(var(--foreground))/0.2]" />

                                    <div className="mt-4 flex gap-2">
                                        <div className="h-6 w-12 rounded-[calc(var(--radius)-4px)] bg-[hsl(var(--primary))]" />
                                        <div className="h-6 w-12 rounded-[calc(var(--radius)-4px)] border border-[hsl(var(--border))]" />
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    {!canCreate && <FieldError message="Fix the highlighted values to save this theme." />}

                    <div className="flex justify-end gap-2">
                        <Button type="button" variant="ghost" size="sm" onClick={() => setIsCreating(false)}>Cancel</Button>
                        <Button type="button" size="sm" onClick={handleCreate} disabled={!canCreate} className="gap-2">
                            <Save className="h-3.5 w-3.5" />
                            Save custom theme
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}
