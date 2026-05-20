import { memo, useState } from "react";
import { Check, Copy } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import { cn } from "@/lib/utils";
import { ChatImage } from "./ChatImage";

const normalizeStreamingMarkdown = (content: string) => {
    const value = content || "";
    const fenceCount = (value.match(/```/g) || []).length;
    return fenceCount % 2 === 1 ? `${value}\n\`\`\`` : value;
};

const MemoizedCodeBlock = memo(({ language, value }: { language: string; value: string }) => {
    const [copied, setCopied] = useState(false);

    const handleCopy = () => {
        navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className="rounded-lg my-3 border border-border overflow-hidden text-sm shadow-sm group">
            <div className="bg-zinc-900 px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground border-b border-zinc-800 flex justify-between items-center">
                <span>{language}</span>
                <button
                    onClick={handleCopy}
                    className="opacity-0 group-hover:opacity-100 transition-all duration-200 hover:text-foreground flex items-center gap-1.5 bg-zinc-800/50 px-2 py-0.5 rounded border border-white/5"
                >
                    {copied ? (
                        <>
                            <Check className="h-3 w-3 text-green-500" />
                            <span className="text-green-500">Copied!</span>
                        </>
                    ) : (
                        <>
                            <Copy className="h-3 w-3" />
                            <span>Copy</span>
                        </>
                    )}
                </button>
            </div>
            <div className="max-h-[450px] overflow-x-auto overflow-y-auto custom-scrollbar">
                <SyntaxHighlighter
                    style={vscDarkPlus}
                    language={language}
                    PreTag="div"
                    wrapLongLines={true}
                    customStyle={{ margin: 0, padding: "1.1rem 1.25rem", background: "#09090b", fontSize: "13.5px", lineHeight: "1.7" }}
                >
                    {value}
                </SyntaxHighlighter>
            </div>
        </div>
    );
});

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
                    a: ({ node, ...props }) => (
                        <a
                            {...props}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="font-bold underline text-primary decoration-primary/60 underline-offset-2 hover:decoration-primary hover:brightness-125 transition-all"
                        />
                    ),
                    p: ({ node, ...props }) => <p {...props} className="mb-2 last:mb-0 leading-[1.5]" />,
                    ul: ({ node, ...props }) => <ul {...props} className="list-disc pl-5 my-2 space-y-0.5" />,
                    ol: ({ node, ...props }) => <ol {...props} className="list-decimal pl-5 my-2 space-y-0.5" />,
                    li: ({ node, ...props }) => <li {...props} className="leading-[1.45] pl-0.5 text-[14px]" />,
                    code: ({ node, className, children, ...props }: any) => {
                        const codeContent = String(children || "").trim();
                        if (!codeContent) return null;
                        const match = /language-(\w+)/.exec(className || "");

                        return !match ? (
                            <code
                                className={cn(
                                    "rounded-md px-1.5 py-0.5 font-mono text-[12.5px] break-all",
                                    isUser ? "bg-black/30 text-white" : "bg-muted text-foreground"
                                )}
                                {...props}
                            >
                                {children}
                            </code>
                        ) : (
                            <MemoizedCodeBlock language={match[1]} value={codeContent} />
                        );
                    },
                    table: ({ node, ...props }) => (
                        <div className="my-3 w-full overflow-x-auto rounded-xl border border-border bg-card/30 backdrop-blur-sm shadow-sm">
                            <table className="w-full text-left text-[13px]" {...props} />
                        </div>
                    ),
                    thead: ({ node, ...props }) => <thead className="bg-muted/50 text-muted-foreground border-b border-border" {...props} />,
                    tbody: ({ node, ...props }) => <tbody className="divide-y divide-border/30" {...props} />,
                    tr: ({ node, ...props }) => <tr className="hover:bg-muted/20 transition-colors" {...props} />,
                    th: ({ node, ...props }) => <th className="px-4 py-3 font-bold text-[11px] uppercase tracking-wider opacity-70" {...props} />,
                    td: ({ node, ...props }) => <td className="px-4 py-3 align-top" {...props} />,
                    h1: ({ node, ...props }) => <h1 className="text-base font-bold mt-3 mb-1 text-foreground tracking-tight" {...props} />,
                    h2: ({ node, ...props }) => <h2 className="text-[14.5px] font-bold mt-2.5 mb-1 text-foreground/90 tracking-tight" {...props} />,
                    h3: ({ node, ...props }) => <h3 className="text-[13.5px] font-bold mt-2 mb-0.5 text-foreground/80 tracking-tight" {...props} />,
                    blockquote: ({ node, ...props }) => (
                        <blockquote className="my-3 rounded-xl border-l-4 border-primary/30 bg-muted/30 px-3.5 py-2 text-muted-foreground/90 italic text-[13.5px]" {...props} />
                    ),
                    img: ({ node, ...props }: any) => <ChatImage src={props.src || ""} alt={props.alt || ""} />,
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
