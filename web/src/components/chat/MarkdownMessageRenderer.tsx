import { lazy, memo, Suspense } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { classifyMarkdownCode } from "@/lib/markdown-code";
import { compactMarkdownSpacing } from "@/lib/markdown-spacing";
import { cn } from "@/lib/utils";
import { ChatImage } from "./ChatImage";

const CodeBlock = lazy(() => import("./CodeBlock"));

function withoutMarkdownNode<T extends { node?: unknown }>(props: T): Omit<T, "node"> {
    const domProps = { ...props };
    delete domProps.node;
    return domProps;
}

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

    // Keep ordinary paragraph breaks, but turn blank-line-separated list items
    // into tight lists. Models frequently emit loose Markdown lists, which can
    // otherwise make a short answer consume most of the viewport.
    const normalizedContent = compactMarkdownSpacing(content);

    return (
        <div className={cn(
            "chat-markdown max-w-none break-words font-sans text-[15px] leading-[1.5] text-inherit",
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
                    p: (props) => <p {...withoutMarkdownNode(props)} className="mb-2 last:mb-0 leading-[1.55] text-foreground/95" />,
                    ul: (props) => <ul {...withoutMarkdownNode(props)} className="my-2 list-disc space-y-1 pl-5 marker:text-primary/75" />,
                    ol: (props) => <ol {...withoutMarkdownNode(props)} className="my-2 list-decimal space-y-1 pl-5 marker:font-semibold marker:text-primary/80" />,
                    li: (props) => <li {...withoutMarkdownNode(props)} className="pl-1 text-[14.5px] leading-[1.5] [&>p]:m-0" />,
                    strong: (props) => <strong {...withoutMarkdownNode(props)} className="font-semibold text-foreground" />,
                    em: (props) => <em {...withoutMarkdownNode(props)} className="text-foreground/85" />,
                    del: (props) => <del {...withoutMarkdownNode(props)} className="text-muted-foreground decoration-destructive/60" />,
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
                        <div className="my-3 w-full overflow-x-auto rounded-xl border border-border/80 bg-card/50 shadow-sm">
                            <table className="w-full text-left text-[13px]" {...withoutMarkdownNode(props)} />
                        </div>
                    ),
                    thead: (props) => <thead className="bg-muted/50 text-muted-foreground border-b border-border" {...withoutMarkdownNode(props)} />,
                    tbody: (props) => <tbody className="divide-y divide-border/30" {...withoutMarkdownNode(props)} />,
                    tr: (props) => <tr className="odd:bg-muted/10 transition-colors hover:bg-primary/5" {...withoutMarkdownNode(props)} />,
                    th: (props) => <th className="px-4 py-2.5 text-[11px] font-bold uppercase tracking-wider text-foreground/75" {...withoutMarkdownNode(props)} />,
                    td: (props) => <td className="px-4 py-2.5 align-top leading-relaxed" {...withoutMarkdownNode(props)} />,
                    h1: (props) => <h1 className="mb-2 mt-5 border-b border-border/70 pb-2 text-lg font-bold tracking-tight text-foreground first:mt-0" {...withoutMarkdownNode(props)} />,
                    h2: (props) => <h2 className="mb-1.5 mt-4 border-l-2 border-primary pl-2.5 text-base font-semibold tracking-tight text-foreground first:mt-0" {...withoutMarkdownNode(props)} />,
                    h3: (props) => <h3 className="mb-1 mt-3 text-[14.5px] font-semibold tracking-tight text-foreground/90 first:mt-0" {...withoutMarkdownNode(props)} />,
                    blockquote: (props) => (
                        <blockquote className="my-3 rounded-r-xl border-l-2 border-primary bg-primary/5 px-4 py-2.5 text-[14px] text-foreground/80" {...withoutMarkdownNode(props)} />
                    ),
                    hr: (props) => <hr className="my-4 border-border/70" {...withoutMarkdownNode(props)} />,
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
