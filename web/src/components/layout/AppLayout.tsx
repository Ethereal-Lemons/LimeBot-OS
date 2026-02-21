import { Sidebar } from "./Sidebar";
import { useState } from "react";
import { Menu, X } from "lucide-react";
import { Button } from "@/components/ui/button";

interface AppLayoutProps {
    children: React.ReactNode;
    botIdentity?: { name: string; avatar: string | null };
    activeView?: string;
    onNavigate?: (view: string) => void;
}

export function AppLayout({ children, botIdentity, activeView, onNavigate }: AppLayoutProps) {
    const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

    const handleNavigate = (view: string) => {
        onNavigate?.(view);
        setMobileMenuOpen(false); // Close menu on navigation
    };

    return (
        <div className="flex h-screen bg-transparent overflow-hidden relative">
            {/* Desktop Sidebar */}
            <Sidebar
                className="hidden md:flex"
                botIdentity={botIdentity}
                activeView={activeView}
                onNavigate={onNavigate}
            />

            {/* Mobile Header */}
            <div className="md:hidden absolute top-0 left-0 right-0 z-50 flex items-center p-4 bg-background/80 backdrop-blur-md border-b border-border">
                <Button variant="ghost" size="icon" onClick={() => setMobileMenuOpen(true)}>
                    <Menu className="h-6 w-6" />
                </Button>
                <span className="ml-3 font-bold text-lg flex items-center gap-2">
                    <img src="/limesimple.png" alt="" className="h-6 w-6" />
                    {botIdentity?.name || "LimeBot"}
                </span>
            </div>

            {/* Mobile Sidebar Drawer */}
            {mobileMenuOpen && (
                <div className="fixed inset-0 z-50 md:hidden">
                    {/* Backdrop */}
                    <div
                        className="absolute inset-0 bg-background/80 backdrop-blur-sm"
                        onClick={() => setMobileMenuOpen(false)}
                    />

                    {/* Drawer Content */}
                    <div className="absolute left-0 top-0 bottom-0 w-3/4 max-w-sm bg-card border-r border-border p-4 shadow-2xl animate-in slide-in-from-left duration-200">
                        <div className="flex justify-end mb-2">
                            <Button variant="ghost" size="icon" onClick={() => setMobileMenuOpen(false)}>
                                <X className="h-5 w-5" />
                            </Button>
                        </div>
                        <Sidebar
                            className="flex h-full w-full m-0 border-none shadow-none"
                            botIdentity={botIdentity}
                            activeView={activeView}
                            onNavigate={handleNavigate}
                        />
                    </div>
                </div>
            )}

            <main className="flex-1 h-full min-w-0 pt-16 md:pt-4 md:py-4 md:pr-4 relative z-0">
                <div className="h-full rounded-none md:rounded-2xl overflow-hidden shadow-none md:shadow-2xl border-t md:border border-border bg-card">
                    {children}
                </div>
            </main>
        </div>
    );
}
