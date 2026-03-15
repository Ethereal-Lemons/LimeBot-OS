import { useState } from 'react';
import { Brain, ChevronDown, ChevronUp } from 'lucide-react';
import { cn } from "@/lib/utils";
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

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
                    <div className="prose prose-sm dark:prose-invert max-w-none break-words font-sans leading-6 opacity-90 prose-p:my-0 prose-p:leading-6">
                        <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                                p: ({ node, ...props }) => <p {...props} className="mb-2 last:mb-0" />,
                                code: ({ node, ...props }) => <code {...props} className="rounded bg-background/60 px-1 py-0.5 font-mono text-[11px]" />
                            }}
                        >
                            {content}
                        </ReactMarkdown>
                    </div>
                </div>
            )}
        </div>
    );
}
