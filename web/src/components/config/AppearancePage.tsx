import { useState } from 'react';
import { Palette } from 'lucide-react';

import { Badge } from "@/components/ui/badge";
import { CustomThemeCreator } from './CustomThemeCreator';

interface AppearancePageProps {
    onThemeChange?: (theme: string) => void;
}

export function AppearancePage({ onThemeChange }: AppearancePageProps) {
    const [currentTheme, setCurrentTheme] = useState(() => localStorage.getItem('limebot-theme') || 'lime');

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
    ];

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

