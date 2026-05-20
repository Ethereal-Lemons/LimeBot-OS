import { Sparkles, Database, CheckCircle2, ShieldCheck, Cpu } from "lucide-react";
import { cn } from "@/lib/utils";

interface SystemUpdateCardProps {
    content: string;
}

export function SystemUpdateCard({ content }: SystemUpdateCardProps) {
    const isPersonaUpdate = content.includes("Persona");
    const title = isPersonaUpdate ? "Persona Core Reconfigured" : "System Memory Consolidated";
    const description = isPersonaUpdate
        ? "AI behavior profiles, identity parameters, and core response guidelines have been dynamically compiled and synchronized."
        : "Workspace system state, dynamic memory indices, and persistent environment buffers have been successfully synchronized.";

    const themeColors = isPersonaUpdate
        ? {
              border: "border-emerald-500/30 dark:border-emerald-400/20 border-l-emerald-500 dark:border-l-emerald-400",
              bg: "bg-emerald-500/[0.04] dark:bg-emerald-500/[0.02]",
              glow: "shadow-[0_4px_24px_-4px_rgba(16,185,129,0.12)]",
              text: "text-emerald-700 dark:text-emerald-400 font-extrabold tracking-tight",
              iconBg: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20",
              badge: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20",
              pillLabel: "Identity Sync"
          }
        : {
              border: "border-cyan-500/30 dark:border-cyan-400/20 border-l-cyan-500 dark:border-l-cyan-400",
              bg: "bg-cyan-500/[0.04] dark:bg-cyan-500/[0.02]",
              glow: "shadow-[0_4px_24px_-4px_rgba(6,182,212,0.12)]",
              text: "text-cyan-700 dark:text-cyan-400 font-extrabold tracking-tight",
              iconBg: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border border-cyan-500/20",
              badge: "bg-cyan-500/10 text-cyan-700 dark:text-cyan-400 border border-cyan-500/20",
              pillLabel: "Storage Write"
          };

    return (
        <div
            className={cn(
                "group relative my-3.5 w-full max-w-xl overflow-hidden rounded-xl border border-l-4 p-4.5 transition-all duration-300 hover:-translate-y-0.5",
                themeColors.border,
                "bg-card/90 dark:bg-card/75",
                themeColors.bg,
                themeColors.glow,
                "backdrop-blur-md shadow-lg"
            )}
        >
            {/* Soft decorative background pulse */}
            <div className={cn(
                "absolute -right-16 -top-16 h-36 w-36 rounded-full blur-[60px] transition-all duration-500 group-hover:scale-125 opacity-30",
                isPersonaUpdate ? "bg-emerald-500/20" : "bg-cyan-500/20"
            )} />

            <div className="relative flex items-start gap-4">
                <div className={cn(
                    "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg shadow-sm transition-transform duration-500 group-hover:rotate-[360deg]",
                    themeColors.iconBg
                )}>
                    {isPersonaUpdate ? (
                        <Sparkles className="h-5 w-5 animate-pulse" />
                    ) : (
                        <Database className="h-5 w-5" />
                    )}
                </div>

                <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 mb-2">
                        <h4 className={cn("text-[14px] uppercase tracking-wide", themeColors.text)}>
                            {title}
                        </h4>
                        <span className={cn(
                            "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[9px] font-bold tracking-wider",
                            themeColors.badge
                        )}>
                            <CheckCircle2 className="h-3 w-3" />
                            SUCCESS
                        </span>
                        <span className="text-[10px] text-muted-foreground/60 font-mono ml-auto">
                            {themeColors.pillLabel}
                        </span>
                    </div>

                    <p className="text-[13px] leading-[1.6] text-foreground/80 dark:text-foreground/90 font-medium font-sans mb-3.5">
                        {description}
                    </p>

                    <div className="flex items-center gap-4 text-[10.5px] font-mono text-muted-foreground/65 dark:text-muted-foreground/50 border-t border-border/40 pt-3">
                        <div className="flex items-center gap-1.5">
                            <ShieldCheck className="h-3.5 w-3.5 text-foreground/40" />
                            <span>Integrity: Validated</span>
                        </div>
                        <div className="h-3 w-px bg-border/40" />
                        <div className="flex items-center gap-1.5">
                            <Cpu className="h-3.5 w-3.5 text-foreground/40" />
                            <span>Agent Hot-Reloaded</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
