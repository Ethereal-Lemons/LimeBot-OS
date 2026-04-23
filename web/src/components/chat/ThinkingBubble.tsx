import { useState } from 'react';
import { Brain, ChevronDown, ChevronUp } from 'lucide-react';
import { cn } from "@/lib/utils";
import { MarkdownMessage } from "./MarkdownMessage";

interface ThinkingBubbleProps {
    content: string;
    isComplete: boolean;
    defaultCollapsed?: boolean;
}

export function ThinkingBubble({ content, isComplete, defaultCollapsed = false }: ThinkingBubbleProps) {
    const [isCollapsed, setIsCollapsed] = useState(defaultCollapsed);

    if (!content) return null;

    return (
        <div className="mb-3 flex max-w-[48rem] flex-col gap-2 font-sans">
            <div
                className={cn(
                    "flex cursor-pointer select-none items-center gap-2 text-[11px] font-medium tracking-[0.02em] transition-colors",
                    isComplete ? "text-muted-foreground/70 hover:text-foreground" : "text-primary/80 animate-pulse"
                )}
                onClick={() => setIsCollapsed(!isCollapsed)}
            >
                <Brain className="h-3.5 w-3.5" />
                <span>
                    {isComplete ? "Thought Process" : "Thinking..."}
                </span>
                {isCollapsed ? <ChevronDown className="h-3 w-3" /> : <ChevronUp className="h-3 w-3" />}
            </div>

            {!isCollapsed && (
                <div className={cn(
                    "overflow-hidden rounded-2xl border px-4 py-3 text-[12px] leading-6 text-muted-foreground transition-all duration-300 ease-in-out",
                    isComplete ? "border-border/70 bg-muted/20" : "border-primary/20 bg-primary/5"
                )}>
                    <div className="text-[12px] leading-6 opacity-90">
                        <MarkdownMessage content={content} isUser={false} />
                    </div>
                </div>
            )}
        </div>
    );
}
