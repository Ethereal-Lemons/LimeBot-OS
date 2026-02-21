import {
    MessageSquare,
    LayoutDashboard,
    Settings,
    Users,
    Activity,
    Zap,
    FileText,
    Palette,
    User2,
    Brain
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";

interface SidebarProps {
    className?: string;
    botIdentity?: { name: string; avatar: string | null };
    activeView?: string;
    onNavigate?: (view: string) => void;
}

export function Sidebar({ className, botIdentity, activeView = 'chat', onNavigate }: SidebarProps) {
    const handleNav = (view: string) => {
        onNavigate?.(view);
    };

    const navItems = [
        { id: 'chat', icon: MessageSquare, label: "Chat" },
        { id: 'overview', icon: LayoutDashboard, label: "Overview" },
        { id: 'memory', icon: Brain, label: "Memory" },
        { id: 'channels', icon: Activity, label: "Channels" },
        { id: 'logs', icon: FileText, label: "System Logs" },
        { id: 'instances', icon: Users, label: "Instances" },
        { id: 'cron', icon: Zap, label: "Cron Jobs" },
    ];

    const agentItems = [
        { id: 'skills', icon: Zap, label: "Skills" },
        // { icon: Box, label: "Nodes", active: false },
    ];

    const configItems = [
        { id: 'persona', icon: User2, label: "Persona" },
        { id: 'appearance', icon: Palette, label: "Appearance" },
        { id: 'config', icon: Settings, label: "Configuration" }
    ];

    return (
        <div className={cn("hidden h-[calc(100vh-2rem)] m-4 w-64 flex-col rounded-2xl bg-card border border-border md:flex shadow-xl", className)}>
            <div className="flex h-20 items-center px-6">
                <a
                    className="flex items-center gap-3 font-bold text-xl cursor-pointer group"
                    onClick={() => onNavigate?.('persona')}
                >
                    <Avatar className="h-10 w-10 shadow-md shadow-primary/10 bg-transparent transition-transform group-hover:scale-105">
                        <AvatarImage src={botIdentity?.avatar || "/limesimple.png"} className="object-cover" />
                        <AvatarFallback className="bg-primary text-primary-foreground text-xs">LB</AvatarFallback>
                    </Avatar>
                    <span className="text-foreground group-hover:text-primary transition-colors">{botIdentity?.name || "LimeBot"}</span>
                </a>
            </div>
            <div className="flex-1 overflow-auto py-4 px-3">
                <nav className="grid items-start gap-1">
                    <div className="px-3 py-2 text-[10px] font-bold text-muted-foreground uppercase tracking-widest">
                        Chat
                    </div>
                    {navItems.map((item) => (
                        <NavItem
                            key={item.id}
                            icon={item.icon}
                            label={item.label}
                            active={activeView === item.id}
                            onClick={() => handleNav(item.id)}
                        />
                    ))}

                    <div className="mt-6 px-3 py-2 text-[10px] font-bold text-muted-foreground uppercase tracking-widest">
                        Agent
                    </div>
                    {agentItems.map((item) => (
                        <NavItem
                            key={item.id}
                            icon={item.icon}
                            label={item.label}
                            active={activeView === item.id}
                            onClick={() => handleNav(item.id)}
                        />
                    ))}

                    <div className="mt-6 px-3 py-2 text-[10px] font-bold text-muted-foreground uppercase tracking-widest">
                        Settings
                    </div>
                    {configItems.map((item) => (
                        <NavItem
                            key={item.id}
                            icon={item.icon}
                            label={item.label}
                            active={activeView === item.id}
                            onClick={() => handleNav(item.id)}
                        />
                    ))}
                </nav>
            </div>
            <div className="mt-auto p-4 m-2 rounded-xl bg-muted/30 border border-border">
                <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-primary">
                        <Users className="w-4 h-4" />
                    </div>
                    <div className="flex flex-col">
                        <span className="text-xs font-medium text-foreground">Admin User</span>
                        <span className="text-[10px] text-muted-foreground">Online</span>
                    </div>
                </div>
            </div>
        </div>
    );
}

function NavItem({ icon: Icon, label, active, onClick }: { icon: any, label: string, active?: boolean, onClick?: () => void }) {
    return (
        <button
            onClick={onClick}
            className={cn(
                "group w-full flex items-center gap-3 rounded-xl px-3 py-2.5 transition-all duration-200",
                active
                    ? "bg-primary/10 text-primary shadow-sm"
                    : "text-muted-foreground hover:bg-white/5 hover:text-foreground"
            )}
        >
            <Icon className={cn("h-4 w-4 transition-transform group-hover:scale-110", active && "fill-current")} />
            <span className="font-medium text-sm">{label}</span>
            {active && <div className="ml-auto w-1 h-1 rounded-full bg-primary" />}
        </button>
    );
}