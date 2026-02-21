import { useState, useEffect } from 'react';
import { Plus, Trash2, Save } from 'lucide-react';
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";

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

// ... (rest of the component code)

export interface CustomTheme {
    id: string;
    name: string;
    variables: Record<string, string>;
    bgImage: string;
    createdAt: number;
}

interface CustomThemeCreatorProps {
    onThemeSelect: (themeId: string) => void;
    currentThemeId: string;
}

export function CustomThemeCreator({ onThemeSelect, currentThemeId }: CustomThemeCreatorProps) {
    const [themes, setThemes] = useState<CustomTheme[]>([]);
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

    // Load themes from localStorage
    useEffect(() => {
        const saved = localStorage.getItem('limebot-custom-themes');
        if (saved) {
            try {
                setThemes(JSON.parse(saved));
            } catch (e) {
                console.error("Failed to parse custom themes", e);
            }
        }
    }, []);

    const saveThemes = (newThemes: CustomTheme[]) => {
        setThemes(newThemes);
        localStorage.setItem('limebot-custom-themes', JSON.stringify(newThemes));
    };

    const handleCreate = () => {
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
            name,
            variables,
            bgImage,
            createdAt: Date.now()
        };

        saveThemes([...themes, newTheme]);
        setIsCreating(false);
        onThemeSelect(id);
    };

    const handleDelete = (id: string, e: React.MouseEvent) => {
        e.stopPropagation();
        const newThemes = themes.filter(t => t.id !== id);
        saveThemes(newThemes);
        if (currentThemeId === id) {
            onThemeSelect('lime'); // Fallback to default
        }
    };

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
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    My Custom Themes
                </h4>
                {!isCreating && (
                    <Button variant="outline" size="sm" onClick={() => setIsCreating(true)} className="h-8 gap-2">
                        <Plus className="h-3.5 w-3.5" />
                        Create New
                    </Button>
                )}
            </div>

            {/* Existing Themes List */}
            {!isCreating && themes.length > 0 && (
                <div className="grid grid-cols-2 md:grid-cols-2 gap-4">
                    {themes.map(theme => (
                        <button
                            key={theme.id}
                            onClick={() => onThemeSelect(theme.id)}
                            className={`
                                relative flex items-center gap-3 p-3 rounded-xl border-2 transition-all group text-left
                                ${currentThemeId === theme.id
                                    ? 'border-primary bg-primary/5 shadow-md'
                                    : 'border-transparent bg-muted/30 hover:border-primary/30'}
                            `}
                        >
                            <div
                                className="w-8 h-8 rounded-full shadow-sm ring-2 ring-offset-2 ring-offset-card ring-primary/20"
                                style={{ background: theme.bgImage }}
                            />
                            <div className="flex flex-col items-start flex-1 min-w-0">
                                <span className="font-medium text-base truncate w-full">{theme.name}</span>
                                <span className="text-[10px] text-muted-foreground">Custom Theme</span>
                            </div>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity absolute right-2 top-2"
                                onClick={(e) => handleDelete(theme.id, e)}
                            >
                                <Trash2 className="h-3 w-3 text-destructive" />
                            </Button>
                            {currentThemeId === theme.id && (
                                <Badge variant="secondary" className="absolute right-2 bottom-2 h-2 w-2 p-0 rounded-full bg-primary" />
                            )}
                        </button>
                    ))}
                </div>
            )}

            {/* Creator Form */}
            {isCreating && (
                <Card className="p-4 border-2 border-primary/20 bg-muted/10 animate-in fade-in zoom-in-95 duration-200">
                    <div className="space-y-6">
                        <div className="space-y-2">
                            <Label>Theme Name</Label>
                            <Input
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                placeholder="e.g. Midnight Runner"
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div className="space-y-4">
                                <Label className="text-xs uppercase text-muted-foreground font-bold">Base Colors</Label>
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-1.5">
                                        <Label className="text-xs">Background</Label>
                                        <div className="flex gap-2">
                                            <Input type="color" className="w-10 h-8 p-1 cursor-pointer" value={colors.background} onChange={e => setColors({ ...colors, background: e.target.value })} />
                                            <Input className="h-8 font-mono text-xs" value={colors.background} onChange={e => setColors({ ...colors, background: e.target.value })} />
                                        </div>
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label className="text-xs">Foreground (Text)</Label>
                                        <div className="flex gap-2">
                                            <Input type="color" className="w-10 h-8 p-1 cursor-pointer" value={colors.foreground} onChange={e => setColors({ ...colors, foreground: e.target.value })} />
                                            <Input className="h-8 font-mono text-xs" value={colors.foreground} onChange={e => setColors({ ...colors, foreground: e.target.value })} />
                                        </div>
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label className="text-xs">Card / Surface</Label>
                                        <div className="flex gap-2">
                                            <Input type="color" className="w-10 h-8 p-1 cursor-pointer" value={colors.card} onChange={e => setColors({ ...colors, card: e.target.value })} />
                                            <Input className="h-8 font-mono text-xs" value={colors.card} onChange={e => setColors({ ...colors, card: e.target.value })} />
                                        </div>
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label className="text-xs">Borders</Label>
                                        <div className="flex gap-2">
                                            <Input type="color" className="w-10 h-8 p-1 cursor-pointer" value={colors.border} onChange={e => setColors({ ...colors, border: e.target.value })} />
                                            <Input className="h-8 font-mono text-xs" value={colors.border} onChange={e => setColors({ ...colors, border: e.target.value })} />
                                        </div>
                                    </div>
                                </div>

                                <Label className="text-xs uppercase text-muted-foreground font-bold mt-4 block">Accents</Label>
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-1.5">
                                        <Label className="text-xs">Primary</Label>
                                        <div className="flex gap-2">
                                            <Input type="color" className="w-10 h-8 p-1 cursor-pointer" value={colors.primary} onChange={e => setColors({ ...colors, primary: e.target.value })} />
                                            <Input className="h-8 font-mono text-xs" value={colors.primary} onChange={e => setColors({ ...colors, primary: e.target.value })} />
                                        </div>
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label className="text-xs">Secondary Accent</Label>
                                        <div className="flex gap-2">
                                            <Input type="color" className="w-10 h-8 p-1 cursor-pointer" value={colors.accent} onChange={e => setColors({ ...colors, accent: e.target.value })} />
                                            <Input className="h-8 font-mono text-xs" value={colors.accent} onChange={e => setColors({ ...colors, accent: e.target.value })} />
                                        </div>
                                    </div>
                                </div>

                                <div className="space-y-3 pt-2">
                                    <div className="flex justify-between">
                                        <Label className="text-xs">Border Radius: {radius}rem</Label>
                                    </div>
                                    <input
                                        type="range"
                                        min="0" max="2" step="0.1"
                                        className="w-full h-2 bg-secondary rounded-lg appearance-none cursor-pointer accent-primary"
                                        value={radius}
                                        onChange={(e) => setRadius(parseFloat(e.target.value))}
                                    />
                                </div>
                            </div>

                            <div className="space-y-4">
                                <Label className="text-xs uppercase text-muted-foreground font-bold">Base Options</Label>
                                <div className="flex items-center justify-between rounded-lg border p-3 shadow-sm">
                                    <div className="space-y-0.5">
                                        <Label className="text-sm">Dark Mode Base</Label>
                                        <div className="text-[10px] text-muted-foreground">
                                            Start with dark defaults
                                        </div>
                                    </div>
                                    <Switch
                                        checked={baseMode === 'dark'}
                                        onCheckedChange={(checked) => setBaseMode(checked ? 'dark' : 'light')}
                                    />
                                </div>
                            </div>

                            <div className="space-y-4">
                                <Label className="text-xs uppercase text-muted-foreground font-bold">Background Gradient</Label>
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-1.5">
                                        <Label className="text-xs">From Color</Label>
                                        <div className="flex gap-2">
                                            <Input type="color" className="w-10 h-8 p-1 cursor-pointer" value={gradientStart} onChange={e => setGradientStart(e.target.value)} />
                                        </div>
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label className="text-xs">To Color</Label>
                                        <div className="flex gap-2">
                                            <Input type="color" className="w-10 h-8 p-1 cursor-pointer" value={gradientEnd} onChange={e => setGradientEnd(e.target.value)} />
                                        </div>
                                    </div>
                                </div>

                                <div className="space-y-1.5">
                                    <Label className="text-xs">Direction</Label>
                                    <Select value={gradientDir} onValueChange={setGradientDir}>
                                        <SelectTrigger>
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

                                <Label className="text-xs pt-2 block">Live Preview</Label>
                                <div
                                    className="rounded-xl border shadow-lg p-4 h-48 flex flex-col justify-center items-center gap-3 relative overflow-hidden"
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

                        <div className="flex justify-end gap-2 pt-2">
                            <Button variant="ghost" onClick={() => setIsCreating(false)}>Cancel</Button>
                            <Button onClick={handleCreate} className="gap-2">
                                <Save className="h-4 w-4" />
                                Save Custom Theme
                            </Button>
                        </div>
                    </div>
                </Card>
            )}
        </div>
    );
}
