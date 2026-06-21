import { memo, useState } from "react";
import { Check, Copy } from "lucide-react";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import css from "react-syntax-highlighter/dist/esm/languages/prism/css";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import jsx from "react-syntax-highlighter/dist/esm/languages/prism/jsx";
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown";
import markup from "react-syntax-highlighter/dist/esm/languages/prism/markup";
import powershell from "react-syntax-highlighter/dist/esm/languages/prism/powershell";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql";
import tsx from "react-syntax-highlighter/dist/esm/languages/prism/tsx";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

const languages = {
    bash,
    css,
    javascript,
    json,
    jsx,
    markdown,
    markup,
    powershell,
    python,
    sql,
    tsx,
    typescript,
    yaml,
};

Object.entries(languages).forEach(([name, grammar]) => {
    SyntaxHighlighter.registerLanguage(name, grammar);
});

SyntaxHighlighter.alias("javascript", ["js"]);
SyntaxHighlighter.alias("typescript", ["ts"]);
SyntaxHighlighter.alias("bash", ["sh", "shell"]);
SyntaxHighlighter.alias("markup", ["html", "xml"]);
SyntaxHighlighter.alias("markdown", ["md"]);
SyntaxHighlighter.alias("powershell", ["ps1"]);
SyntaxHighlighter.alias("yaml", ["yml"]);

const supportedLanguages = new Set([
    ...Object.keys(languages),
    "js", "ts", "sh", "shell", "html", "xml", "md", "ps1", "yml",
]);

function CodeBlock({ language, value }: { language: string; value: string }) {
    const [copied, setCopied] = useState(false);
    const highlightedLanguage = supportedLanguages.has(language.toLowerCase())
        ? language.toLowerCase()
        : "text";

    const handleCopy = async () => {
        await navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className="rounded-lg my-3 border border-border overflow-hidden text-sm shadow-sm group">
            <div className="bg-zinc-900 px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground border-b border-zinc-800 flex justify-between items-center">
                <span>{language}</span>
                <button
                    type="button"
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
                    language={highlightedLanguage}
                    PreTag="div"
                    wrapLongLines
                    customStyle={{ margin: 0, padding: "1.1rem 1.25rem", background: "#09090b", fontSize: "13.5px", lineHeight: "1.7" }}
                >
                    {value}
                </SyntaxHighlighter>
            </div>
        </div>
    );
}

export default memo(CodeBlock);
