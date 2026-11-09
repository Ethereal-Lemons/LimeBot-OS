import { useEffect, useMemo } from "react";
import { MemoryPage } from "@/components/memory/MemoryPage";
import { CronPage } from "@/components/cron/CronPage";
import { BrowserSessionsPanel } from "@/components/browser/BrowserSessionsPanel";
import { ChannelsPage } from "@/components/channels/ChannelsPage";
import { Toaster } from "@/components/ui/sonner";
import { applyThemeWithSchedule } from "@/hooks/useTheme";
import { installMockBackend, type Scenario } from "./mock-backend";

/**
 * Data-page fixtures. Open `?ui=pages&page=memory|cron|browser|channels`
 * with optional `&state=ok|empty|loading|network|server`, `&theme=<name>`,
 * `&mode=light`. The mock backend answers every request in memory.
 */

const PAGES = ["memory", "channels", "cron", "browser"] as const;
const STATES: Scenario[] = ["ok", "empty", "loading", "network", "server"];

const params = () => new URLSearchParams(window.location.search);

export function PagesGallery() {
    const query = params();
    const page = (PAGES as readonly string[]).includes(query.get("page") || "") ? (query.get("page") as (typeof PAGES)[number]) : "memory";
    const scenario = (STATES as string[]).includes(query.get("state") || "") ? (query.get("state") as Scenario) : "ok";
    const theme = query.get("theme") || "lime";
    const light = query.get("mode") === "light";
    const tab = query.get("tab");

    // Must run before the page's first request, so install during render (once).
    useMemo(() => installMockBackend(scenario), [scenario]);

    useEffect(() => {
        applyThemeWithSchedule(theme);
        document.documentElement.classList.toggle("dark", !light);
    }, [theme, light]);

    const link = (over: Record<string, string>) => {
        const next = new URLSearchParams(window.location.search);
        Object.entries(over).forEach(([key, value]) => next.set(key, value));
        return `?${next.toString()}`;
    };

    return (
        <div className="flex h-screen flex-col bg-background text-foreground">
            <nav className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border/60 px-4 py-2 text-xs">
                <span className="font-semibold">Pages gallery</span>
                <span className="flex gap-2">
                    {PAGES.map((name) => (
                        <a key={name} href={link({ page: name, tab: "" })} className={name === page ? "font-semibold text-primary" : "text-muted-foreground hover:text-foreground"}>
                            {name}
                        </a>
                    ))}
                </span>
                <span className="flex gap-2 text-muted-foreground">
                    state:
                    {STATES.map((name) => (
                        <a key={name} href={link({ state: name })} className={name === scenario ? "font-semibold text-primary" : "hover:text-foreground"}>
                            {name}
                        </a>
                    ))}
                </span>
                <span className="text-muted-foreground">theme: {theme}{light ? " (light)" : ""}</span>
            </nav>
            <main className="min-h-0 flex-1 bg-card/30">
                {page === "memory" && <MemoryPage onNavigate={() => undefined} />}
                {page === "cron" && <CronPage />}
                {page === "browser" && <BrowserSessionsPanel />}
                {page === "channels" && <ChannelsPage initialTab={tab === "telegram" || tab === "whatsapp" ? tab : "discord"} />}
            </main>
            <Toaster position="top-right" />
        </div>
    );
}
