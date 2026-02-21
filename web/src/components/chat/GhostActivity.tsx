import { useEffect, useState } from 'react';
import { Brain, Database, Save } from 'lucide-react';
import { cn } from '@/lib/utils';

interface GhostActivityProps {
    activity: { text: string } | null;
}

export function GhostActivity({ activity }: GhostActivityProps) {
    const [visible, setVisible] = useState(false);
    const [text, setText] = useState("");
    const [Icon, setIcon] = useState<any>(Brain);

    useEffect(() => {
        if (activity && activity.text) {
            setText(activity.text);
            setVisible(true);

            // Choose icon based on text
            if (activity.text.toLowerCase().includes("save")) setIcon(Save);
            else if (activity.text.toLowerCase().includes("memory")) setIcon(Database);
            else setIcon(Brain);
        } else {
            setVisible(false);
        }
    }, [activity]);

    return (
        <div className={cn(
            "fixed bottom-24 left-1/2 -translate-x-1/2 z-50 transition-all duration-500 ease-out pointer-events-none",
            visible ? "translate-y-0 opacity-100" : "translate-y-8 opacity-0"
        )}>
            <div className="flex items-center gap-3 px-4 py-2.5 bg-background/80 backdrop-blur-xl border border-primary/20 rounded-full shadow-2xl shadow-primary/10 text-foreground text-xs font-medium">
                <div className="relative flex items-center justify-center w-5 h-5 rounded-full bg-primary/10 text-primary">
                    {visible && <div className="absolute inset-0 rounded-full bg-primary/20 animate-ping opacity-75"></div>}
                    <Icon className="w-3.5 h-3.5 relative z-10" />
                </div>

                <span className="tracking-wide">{text}</span>

                <div className="flex gap-0.5 ml-2">
                    <div className="w-1 h-1 bg-primary/50 rounded-full animate-bounce [animation-delay:-0.3s]"></div>
                    <div className="w-1 h-1 bg-primary/50 rounded-full animate-bounce [animation-delay:-0.15s]"></div>
                    <div className="w-1 h-1 bg-primary/50 rounded-full animate-bounce"></div>
                </div>
            </div>
        </div>
    );
}
