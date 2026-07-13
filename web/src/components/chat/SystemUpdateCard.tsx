import { Sparkles, Database, ShieldCheck, Zap, RefreshCw } from "lucide-react";

interface SystemUpdateCardProps {
    content: string;
}

export function SystemUpdateCard({ content }: SystemUpdateCardProps) {
    const isPersonaUpdate = content.includes("Persona");
    const title = isPersonaUpdate ? "Persona Core Reconfigured" : "System Memory Consolidated";
    const description = isPersonaUpdate
        ? "AI behavior profiles, identity parameters, and core response guidelines have been dynamically compiled and synchronized."
        : "Workspace system state, dynamic memory indices, and persistent environment buffers have been successfully synchronized.";

    const theme = {
        accent: "hsl(var(--primary))",
        accentMuted: "hsl(var(--primary) / 0.15)",
        accentDim: "hsl(var(--primary) / 0.06)",
        accentGlow: "hsl(var(--primary) / 0.35)",
        accentSoft: "hsl(var(--primary) / 0.08)",
        label: isPersonaUpdate ? "Identity Sync" : "Memory Write",
    };

    return (
        <div
            className="group relative my-3.5 w-full max-w-xl overflow-hidden rounded-2xl transition-all duration-500 hover:-translate-y-0.5"
            style={{
                background: `linear-gradient(135deg, hsl(var(--card)/0.95) 0%, hsl(var(--card)/0.80) 100%)`,
                border: `1px solid ${theme.accentMuted}`,
                boxShadow: `0 0 0 1px ${theme.accentDim}, 0 8px 32px -8px ${theme.accentGlow}, 0 2px 8px rgba(0,0,0,0.25)`,
                backdropFilter: "blur(16px)",
            }}
        >
            {/* Top accent glow bar */}
            <div
                className="absolute top-0 inset-x-0 h-px"
                style={{
                    background: `linear-gradient(90deg, transparent 0%, ${theme.accent} 40%, ${theme.accent} 60%, transparent 100%)`,
                    opacity: 0.7,
                }}
            />

            {/* Ambient background gradient */}
            <div
                className="absolute inset-0 opacity-100 pointer-events-none"
                style={{
                    background: `radial-gradient(ellipse 80% 60% at 90% -10%, ${theme.accentSoft} 0%, transparent 70%)`,
                }}
            />

            {/* Subtle scan-line shimmer */}
            <div
                className="absolute inset-0 opacity-[0.03] pointer-events-none"
                style={{
                    backgroundImage: `repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(255,255,255,0.5) 2px, rgba(255,255,255,0.5) 3px)`,
                }}
            />

            <div className="relative p-4">
                <div className="flex items-start gap-3.5">
                    {/* Icon */}
                    <div
                        className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-all duration-700 group-hover:rotate-[360deg]"
                        style={{
                            background: `linear-gradient(135deg, ${theme.accentMuted} 0%, ${theme.accentDim} 100%)`,
                            border: `1px solid ${theme.accentMuted}`,
                            boxShadow: `0 0 12px -2px ${theme.accentGlow}`,
                            color: theme.accent,
                        }}
                    >
                        {isPersonaUpdate ? (
                            <Sparkles className="h-4.5 w-4.5" style={{ filter: `drop-shadow(0 0 4px ${theme.accent})` }} />
                        ) : (
                            <Database className="h-4.5 w-4.5" style={{ filter: `drop-shadow(0 0 4px ${theme.accent})` }} />
                        )}
                    </div>

                    <div className="flex-1 min-w-0">
                        {/* Header row */}
                        <div className="flex flex-wrap items-center gap-2 mb-2.5">
                            <h4
                                className="text-[12px] font-black uppercase tracking-[0.12em]"
                                style={{
                                    color: theme.accent,
                                    textShadow: `0 0 16px ${theme.accentGlow}`,
                                }}
                            >
                                {title}
                            </h4>

                            {/* Success badge */}
                            <span
                                className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[9px] font-bold tracking-[0.1em]"
                                style={{
                                    background: theme.accentMuted,
                                    border: `1px solid ${theme.accentMuted}`,
                                    color: theme.accent,
                                }}
                            >
                                <span
                                    className="h-1.5 w-1.5 rounded-full animate-pulse"
                                    style={{ background: theme.accent, boxShadow: `0 0 6px ${theme.accent}` }}
                                />
                                SUCCESS
                            </span>

                            {/* Pill label */}
                            <span
                                className="ml-auto text-[9.5px] font-mono tracking-wider opacity-50"
                                style={{ color: theme.accent }}
                            >
                                {theme.label}
                            </span>
                        </div>

                        {/* Description */}
                        <p className="text-[12.5px] leading-[1.65] text-foreground/70 mb-3.5 font-normal">
                            {description}
                        </p>

                        {/* Footer */}
                        <div
                            className="flex items-center gap-5 pt-3"
                            style={{ borderTop: `1px solid ${theme.accentDim}` }}
                        >
                            <div
                                className="flex items-center gap-1.5 text-[10px] font-mono tracking-wide"
                                style={{ color: theme.accent, opacity: 0.55 }}
                            >
                                <ShieldCheck className="h-3 w-3" />
                                <span>Integrity: Validated</span>
                            </div>
                            <div
                                className="h-2.5 w-px"
                                style={{ background: theme.accentMuted }}
                            />
                            <div
                                className="flex items-center gap-1.5 text-[10px] font-mono tracking-wide"
                                style={{ color: theme.accent, opacity: 0.55 }}
                            >
                                <RefreshCw className="h-3 w-3" />
                                <span>Agent Hot-Reloaded</span>
                            </div>
                            <div className="ml-auto flex items-center gap-1" style={{ color: theme.accent, opacity: 0.4 }}>
                                <Zap className="h-2.5 w-2.5" />
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Bottom accent glow bar */}
            <div
                className="absolute bottom-0 inset-x-0 h-px opacity-30"
                style={{
                    background: `linear-gradient(90deg, transparent 0%, ${theme.accent} 50%, transparent 100%)`,
                }}
            />
        </div>
    );
}
