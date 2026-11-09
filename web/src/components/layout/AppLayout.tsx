import { Sidebar } from "./Sidebar";
import { useState } from "react";
import { Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { BotVisual } from "@/components/pet/BotVisual";

interface AppLayoutProps {
    children: React.ReactNode;
    botIdentity?: { name: string; avatar: string | null };
    activeView?: string;
    onNavigate?: (view: string) => void;
    pageTitle?: string;
    pageDescription?: string;
    runtimeStatus?: {
        isConnected: boolean;
        autonomousMode: boolean;
        pendingApprovals: number;
    };
}

export function AppLayout({
    children,
    botIdentity,
    activeView,
    onNavigate,
    pageTitle,
    pageDescription,
    runtimeStatus,
}: AppLayoutProps) {
    const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

    const handleNavigate = (view: string) => {
        onNavigate?.(view);
        setMobileMenuOpen(false); // Close menu on navigation
    };

    const connected = !!runtimeStatus?.isConnected;

    return (
        <div className="relative flex h-screen overflow-hidden bg-background">
            {/* Desktop sidebar: full height, collapsible to an icon rail */}
            <Sidebar
                botIdentity={botIdentity}
                activeView={activeView}
                onNavigate={onNavigate}
                runtimeStatus={runtimeStatus}
            />

            {/* Mobile header */}
            <header className="absolute inset-x-0 top-0 z-40 border-b border-border/60 bg-background/85 backdrop-blur-md md:hidden">
                <div className="flex items-center gap-2 px-2 py-2">
                    <Button variant="ghost" size="icon" onClick={() => setMobileMenuOpen(true)} aria-label="Open menu">
                        <Menu className="h-5 w-5" />
                    </Button>
                    <BotVisual avatar={botIdentity?.avatar} size="tiny" />
                    <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-semibold leading-tight text-foreground">
                            {pageTitle || botIdentity?.name || "LimeBot"}
                        </div>
                        {pageDescription && (
                            <div className="truncate text-[11px] leading-tight text-muted-foreground">{pageDescription}</div>
                        )}
                    </div>
                    <span className="relative mr-2 flex h-2.5 w-2.5" title={connected ? "Connected" : "Reconnecting"}>
                        <span className={cn("relative inline-flex h-2.5 w-2.5 rounded-full", connected ? "bg-primary" : "bg-amber-500")} />
                    </span>
                </div>
            </header>

            {/* Mobile drawer */}
            {mobileMenuOpen && (
                <div className="fixed inset-0 z-50 md:hidden">
                    <div
                        className="absolute inset-0 bg-background/70 backdrop-blur-sm animate-in fade-in duration-150"
                        onClick={() => setMobileMenuOpen(false)}
                    />
                    <div className="absolute inset-y-0 left-0 flex w-[82%] max-w-xs flex-col border-r border-border bg-card shadow-2xl animate-in slide-in-from-left duration-200">
                        <div className="flex shrink-0 justify-end p-2">
                            <Button variant="ghost" size="icon" onClick={() => setMobileMenuOpen(false)} aria-label="Close menu">
                                <X className="h-5 w-5" />
                            </Button>
                        </div>
                        <Sidebar
                            forceExpanded
                            className="flex min-h-0 w-full flex-1 border-none bg-transparent"
                            botIdentity={botIdentity}
                            activeView={activeView}
                            onNavigate={handleNavigate}
                            runtimeStatus={runtimeStatus}
                        />
                    </div>
                </div>
            )}

            <main className="relative z-0 h-full min-w-0 flex-1 pt-12 md:pt-0">
                <div className="h-full overflow-hidden bg-card/30">{children}</div>
            </main>
        </div>
    );
}
