import { lazy, memo, Suspense } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { classifyMarkdownCode } from "@/lib/markdown-code";
import { cn } from "@/lib/utils";
import { ChatImage } from "./ChatImage";

const CodeBlock = lazy(() => import("./CodeBlock"));

function withoutMarkdownNode<T extends { node?: unknown }>(props: T): Omit<T, "node"> {
    const domProps = { ...props };
    delete domProps.node;
    return domProps;
}

const normalizeStreamingMarkdown = (content: string) => {
    const value = content || "";
    const fenceCount = (value.match(/```/g) || []).length;
    return fenceCount % 2 === 1 ? `${value}\n\`\`\`` : value;
};

function CodeBlockFallback({ language, value }: { language: string; value: string }) {
    return (
        <div className="rounded-lg my-3 border border-border overflow-hidden text-sm shadow-sm group">
            <div className="bg-zinc-900 px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground border-b border-zinc-800 flex justify-between items-center">
                <span>{language}</span>
            </div>
            <pre className="max-h-[450px] overflow-auto whitespace-pre-wrap bg-zinc-950 px-5 py-4 font-mono text-[13.5px] leading-[1.7] text-zinc-100 custom-scrollbar">
                <code>{value}</code>
            </pre>
        </div>
    );
}

function MarkdownMessageRenderer({
    content,
    isUser,
    isStreaming,
}: {
    content: string;
    isUser: boolean;
    isStreaming?: boolean;
}) {
    if (!content) return null;

    const renderedContent = isStreaming ? normalizeStreamingMarkdown(content) : content;
    // Replace 3 or more consecutive newlines with exactly 2 to prevent huge vertical gaps
    const normalizedContent = renderedContent.replace(/\n{3,}/g, "\n\n");

    return (
        <div className={cn(
            "max-w-none break-words font-sans text-[14.5px] leading-[1.5] text-inherit",
            isStreaming && "streaming-markdown"
        )}>
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                    a: (props) => {
                        const domProps = withoutMarkdownNode(props);
                        const href = typeof domProps.href === "string" ? domProps.href : "";
                        // Belt-and-suspenders: react-markdown already blocks
                        // javascript: via defaultUrlTransform, but guard against
                        // future urlTransform overrides by refusing to render an
                        // executable anchor for anything but safe schemes.
                        const isSafeHref = /^(https?:|mailto:|tel:|\/|#|\.)/i.test(href);
                        if (!isSafeHref) {
                            return <span>{domProps.children}</span>;
                        }
                        return (
                            <a
                                {...domProps}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="font-bold underline text-primary decoration-primary/60 underline-offset-2 hover:decoration-primary hover:brightness-125 transition-all"
                            />
                        );
                    },
                    p: (props) => <p {...withoutMarkdownNode(props)} className="mb-1.5 last:mb-0 leading-[1.5]" />,
                    ul: (props) => <ul {...withoutMarkdownNode(props)} className="list-disc pl-5 my-1.5 space-y-0.5" />,
                    ol: (props) => <ol {...withoutMarkdownNode(props)} className="list-decimal pl-5 my-1.5 space-y-0.5" />,
                    li: (props) => <li {...withoutMarkdownNode(props)} className="leading-[1.45] pl-0.5 text-[14px]" />,
                    pre: ({ children }) => <>{children}</>,
                    code: (componentProps) => {
                        const { className, children, ...props } = withoutMarkdownNode(componentProps);
                        const code = classifyMarkdownCode(className, children);
                        if (!code.value && code.kind === "inline") return null;

                        return code.kind === "inline" ? (
                            <code
                                className={cn(
                                    "rounded-md px-1.5 py-0.5 font-mono text-[12.5px] break-all",
                                    isUser ? "bg-black/20 text-user-bubble-foreground/90" : "bg-muted text-foreground"
                                )}
                                {...props}
                            >
                                {children}
                            </code>
                        ) : (
                            <Suspense fallback={<CodeBlockFallback language={code.language} value={code.value} />}>
                                <CodeBlock language={code.language} value={code.value} />
                            </Suspense>
                        );
                    },
                    table: (props) => (
                        <div className="my-2.5 w-full overflow-x-auto rounded-xl border border-border bg-card/30 backdrop-blur-sm shadow-sm">
                            <table className="w-full text-left text-[13px]" {...withoutMarkdownNode(props)} />
                        </div>
                    ),
                    thead: (props) => <thead className="bg-muted/50 text-muted-foreground border-b border-border" {...withoutMarkdownNode(props)} />,
                    tbody: (props) => <tbody className="divide-y divide-border/30" {...withoutMarkdownNode(props)} />,
                    tr: (props) => <tr className="hover:bg-muted/20 transition-colors" {...withoutMarkdownNode(props)} />,
                    th: (props) => <th className="px-4 py-3 font-bold text-[11px] uppercase tracking-wider opacity-70" {...withoutMarkdownNode(props)} />,
                    td: (props) => <td className="px-4 py-3 align-top" {...withoutMarkdownNode(props)} />,
                    h1: (props) => <h1 className="text-base font-bold mt-2.5 mb-1 text-foreground tracking-tight" {...withoutMarkdownNode(props)} />,
                    h2: (props) => <h2 className="text-[14.5px] font-bold mt-2 mb-0.5 text-foreground/90 tracking-tight" {...withoutMarkdownNode(props)} />,
                    h3: (props) => <h3 className="text-[13.5px] font-bold mt-1.5 mb-0.5 text-foreground/80 tracking-tight" {...withoutMarkdownNode(props)} />,
                    blockquote: (props) => (
                        <blockquote className="my-2.5 rounded-xl border-l-4 border-primary/30 bg-muted/30 px-3.5 py-2 text-muted-foreground/90 italic text-[13.5px]" {...withoutMarkdownNode(props)} />
                    ),
                    img: (props) => {
                        const { src, alt } = withoutMarkdownNode(props);
                        return <ChatImage src={src || ""} alt={alt || ""} />;
                    },
                }}
            >
                {normalizedContent}
            </ReactMarkdown>
            {isStreaming && (
                <span
                    aria-hidden="true"
                    className="ml-1 inline-block h-4 w-1.5 rounded-full bg-current/35 align-middle animate-pulse"
                />
            )}
        </div>
    );
}

export default memo(MarkdownMessageRenderer);
