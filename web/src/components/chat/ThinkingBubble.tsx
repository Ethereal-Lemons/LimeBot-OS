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
        <div className="flex flex-col gap-1 mb-2 max-w-[85%]">
            <div
                className={cn(
                    "flex items-center gap-2 text-xs font-medium cursor-pointer transition-colors select-none",
                    isComplete ? "text-muted-foreground/60 hover:text-muted-foreground" : "text-primary/80 animate-pulse"
                )}
                onClick={() => setIsCollapsed(!isCollapsed)}
            >
                <Brain className="w-3 h-3" />
                <span>
                    {isComplete ? "Thought Process" : "Thinking..."}
                </span>
                {isCollapsed ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />}
            </div>

            {!isCollapsed && (
                <div className={cn(
                    "pl-3 ml-1.5 border-l-2 text-xs text-muted-foreground/80 italic overflow-hidden transition-all duration-300 ease-in-out",
                    isComplete ? "border-muted" : "border-primary/30"
                )}>
                    <div className="prose prose-xs dark:prose-invert max-w-none break-words leading-relaxed opacity-90">
                        <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                                p: ({ node, ...props }) => <p {...props} className="mb-1 last:mb-0" />,
                                code: ({ node, ...props }) => <code {...props} className="bg-transparent font-mono text-xs" />
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
