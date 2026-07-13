import { lazy, memo, Suspense } from "react";
import { cn } from "@/lib/utils";
import { shouldRenderRichMarkdown } from "@/lib/stream-rendering";

const MarkdownMessageRenderer = lazy(() => import("./MarkdownMessageRenderer"));

function MarkdownFallback({
    content,
    isStreaming,
}: {
    content: string;
    isStreaming?: boolean;
}) {
    if (!content) return null;

    return (
        <div className={cn(
            "max-w-none whitespace-pre-wrap break-words font-sans text-[15px] leading-[1.5] text-inherit",
            isStreaming && "streaming-markdown"
        )}>
            {content}
            {isStreaming && (
                <span
                    aria-hidden="true"
                    className="ml-1 inline-block h-4 w-1.5 rounded-full bg-current/35 align-middle animate-pulse"
                />
            )}
        </div>
    );
}

export const MarkdownMessage = memo((props: {
    content: string;
    isUser: boolean;
    isStreaming?: boolean;
}) => {
    if (!shouldRenderRichMarkdown(props.isStreaming)) {
        return <MarkdownFallback content={props.content} isStreaming />;
    }
    return (
        <Suspense fallback={<MarkdownFallback content={props.content} />}>
            <MarkdownMessageRenderer {...props} isStreaming={false} />
        </Suspense>
    );
});
