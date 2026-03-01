import { useRef, useEffect, useState, useLayoutEffect, memo } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';
import axios from 'axios';
import { API_BASE_URL } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";
import { cn } from "@/lib/utils";
import { Send, Bot, Power, Paperclip, X, User, Plus, Zap, Check, Copy } from "lucide-react";
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
    AlertDialog,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Loader2 } from "lucide-react";
import { ToolCard, ToolExecution } from './ToolCard';
import { ConfirmationCard, ConfirmationRequest } from './ConfirmationCard';
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AlertTriangle, Info } from "lucide-react";


import { ThinkingBubble } from './ThinkingBubble';
import { ToolTimeline } from './ToolTimeline';

interface Message {
    sender: 'user' | 'bot';
    type?: 'text' | 'tool' | 'confirmation';
    content: string;
    thinking?: string;
    image?: string | null;
    toolExecution?: ToolExecution;
    confirmation?: ConfirmationRequest;
    variant?: 'default' | 'destructive' | 'warning';
}

interface ChatInterfaceProps {
    messages: Message[];
    inputValue: string;
    isConnected: boolean;
    isTyping?: boolean;
    botIdentity?: { name: string; avatar: string | null };
    onInputChange: (value: string) => void;
    onSendMessage: (content?: string | null, image?: string | null) => void;
    onReconnect: () => void;
    onNewChat?: () => void;
    activeChatId: string;
    autonomousMode?: boolean;
}

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
            <div className="max-h-[450px] overflow-y-auto custom-scrollbar">
                <SyntaxHighlighter
                    style={vscDarkPlus}
                    language={language}
                    PreTag="div"
                    customStyle={{ margin: 0, padding: '1.25rem', background: '#09090b', fontSize: '13px', lineHeight: '1.6' }}
                >
                    {value}
                </SyntaxHighlighter>
            </div>
        </div>
    );
});

const MemoizedMessageItem = memo(({
    msg,
    botIdentity,
    handleToolConfirmSideChannel,
    onSendMessage,
    showAvatar,
    showHeader
}: {
    msg: Message;
    botIdentity: ChatInterfaceProps['botIdentity'];
    handleToolConfirmSideChannel: (confId: string, approved: boolean, sessionWhitelist: boolean) => Promise<void>;
    onSendMessage: ChatInterfaceProps['onSendMessage'];
    showAvatar: boolean;
    showHeader: boolean;
}) => {
    const isUser = msg.sender === 'user';
    const isBot = msg.sender === 'bot';

    if (msg.content?.includes('[CONFIRM_SESSION]') || msg.content?.includes('[CONFIRM_EXECUTION]')) {
        return null; // Hidden confirmation trace messages
    }

    return (
        <div className={cn(
            "flex w-full gap-4",
            isUser ? "ml-auto flex-row-reverse max-w-[85%]" : "max-w-[90%]"
        )}>
            {showAvatar ? (
                <Avatar className="h-9 w-9 mt-1 shrink-0 border border-border shadow-sm">
                    {isBot ? (
                        <>
                            <AvatarImage src={botIdentity?.avatar || undefined} className="object-cover" />
                            <AvatarFallback className="bg-primary text-primary-foreground text-xs font-bold">Bot</AvatarFallback>
                        </>
                    ) : (
                        <>
                            <AvatarImage src={undefined} />
                            <AvatarFallback className="bg-secondary text-secondary-foreground">
                                <User className="h-5 w-5" />
                            </AvatarFallback>
                        </>
                    )}
                </Avatar>
            ) : (
                <div className="h-9 w-9 mt-1 shrink-0" />
            )}

            <div className={cn(
                "flex flex-col gap-1 min-w-0 flex-1",
                isUser ? "items-end" : "items-start"
            )}>
                {!isUser && showHeader && (
                    <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest opacity-50 mb-0.5 ml-1">
                        <span className="text-primary">{botIdentity?.name || "LimeBot"}</span>
                        <span className="h-1 w-1 rounded-full bg-muted-foreground/30" />
                        <span>{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                    </div>
                )}

                {msg.type === 'tool' && msg.toolExecution ? (
                    <div className="w-full max-w-2xl">
                        <ToolCard
                            execution={msg.toolExecution}
                            onConfirmSideChannel={handleToolConfirmSideChannel}
                            onConfirm={(_id, approved) => {
                                if (approved) {
                                    const argsStr = JSON.stringify(msg.toolExecution?.args).slice(0, 500);
                                    onSendMessage(`[CONFIRM_EXECUTION] Proceed with ${msg.toolExecution?.tool} using args: ${argsStr}`);
                                } else {
                                    onSendMessage(`Cancel execution of ${msg.toolExecution?.tool}`);
                                }
                            }}
                            onConfirmSession={(_id) => {
                                const argsStr = JSON.stringify(msg.toolExecution?.args).slice(0, 500);
                                onSendMessage(`[CONFIRM_SESSION] Proceed with ${msg.toolExecution?.tool} using args: ${argsStr}`);
                            }}
                        />
                    </div>
                ) : msg.type === 'confirmation' && msg.confirmation ? (
                    <div className="w-full max-w-2xl">
                        <ConfirmationCard request={msg.confirmation} />
                    </div>
                ) : msg.variant && msg.variant !== 'default' ? (
                    <Alert variant={msg.variant} className="max-w-xl shadow-sm border-l-4">
                        {msg.variant === 'destructive' ? <AlertTriangle className="h-4 w-4" /> : <Info className="h-4 w-4" />}
                        <AlertTitle className="ml-2 font-bold">{msg.variant === 'destructive' ? 'Error' : 'Warning'}</AlertTitle>
                        <AlertDescription className="ml-2 mt-1 text-xs opacity-90">{msg.content}</AlertDescription>
                    </Alert>
                ) : (
                    <div className={cn(
                        "relative group px-3.5 py-2 text-[14px] leading-tight transition-all duration-300",
                        isUser
                            ? "bg-zinc-800 text-white rounded-2xl rounded-tr-none shadow-sm hover:bg-zinc-700/90 hover:shadow-md"
                            : "bg-muted/80 backdrop-blur-sm text-foreground rounded-2xl rounded-tl-none border shadow-sm hover:bg-muted/90 hover:border-primary/30 hover:shadow-md"
                    )}>
                        {/* Thinking Bubble */}
                        {msg.thinking && (
                            <ThinkingBubble
                                content={msg.thinking}
                                isComplete={!!msg.content}
                                defaultCollapsed={!!msg.content}
                            />
                        )}

                        <div className="whitespace-pre-wrap break-words">
                            {msg.image && (
                                <ChatImage src={msg.image} alt="Uploaded content" />
                            )}
                            <div className="prose prose-sm dark:prose-invert max-w-none break-words leading-tight text-inherit">
                                <ReactMarkdown
                                    remarkPlugins={[remarkGfm]}
                                    components={{
                                        a: ({ node, ...props }) => (
                                            <a
                                                {...props}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="font-bold underline text-primary decoration-primary/30 hover:decoration-primary"
                                            />
                                        ),
                                        p: ({ node, ...props }) => <p {...props} className="last:mb-1" />,
                                        code: ({ node, className, children, ...props }: any) => {
                                            const content = String(children || '').trim();
                                            if (!content) return null;
                                            const match = /language-(\w+)/.exec(className || '');

                                            return !match ? (
                                                <code className={cn("bg-muted/50 px-1.5 py-0.5 rounded font-mono text-[12px]", isUser ? "bg-black/30" : "bg-zinc-200/50 dark:bg-zinc-800/50")} {...props}>
                                                    {children}
                                                </code>
                                            ) : (
                                                <MemoizedCodeBlock language={match[1]} value={content} />
                                            );
                                        },
                                        table: ({ node, ...props }) => (
                                            <div className="my-4 w-full overflow-x-auto rounded-xl border border-border bg-card/30 backdrop-blur-sm shadow-sm">
                                                <table className="w-full text-left text-[13px]" {...props} />
                                            </div>
                                        ),
                                        thead: ({ node, ...props }) => <thead className="bg-muted/50 text-muted-foreground border-b border-border" {...props} />,
                                        tbody: ({ node, ...props }) => <tbody className="divide-y divide-border/30" {...props} />,
                                        tr: ({ node, ...props }) => <tr className="hover:bg-muted/20 transition-colors" {...props} />,
                                        th: ({ node, ...props }) => <th className="px-4 py-3 font-bold text-[11px] uppercase tracking-wider opacity-70" {...props} />,
                                        td: ({ node, ...props }) => <td className="px-4 py-3 align-top" {...props} />,
                                        h1: ({ node, ...props }) => <h1 className="text-xl font-bold mt-4 mb-2 border-b border-border/50 pb-1" {...props} />,
                                        h2: ({ node, ...props }) => <h2 className="text-lg font-bold mt-3 mb-2" {...props} />,
                                        h3: ({ node, ...props }) => <h3 className="text-md font-bold mt-2 mb-1" {...props} />,
                                        blockquote: ({ node, ...props }) => (
                                            <blockquote className="border-l-4 border-primary/30 pl-4 py-1 my-3 italic text-muted-foreground bg-primary/5 rounded-r" {...props} />
                                        ),
                                        img: ({ node, ...props }: any) => <ChatImage src={props.src || ''} alt={props.alt || ''} />,
                                    }}
                                >
                                    {msg.content}
                                </ReactMarkdown>
                            </div>
                        </div>

                        {isUser && (
                            <div className="absolute -left-12 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-40 transition-opacity text-[9px] font-medium uppercase tracking-tighter hidden md:block">
                                {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
});

export function ChatInterface({
    messages,
    inputValue,
    isConnected,
    isTyping,
    botIdentity,
    onInputChange,
    onSendMessage,
    onNewChat,
    activeChatId,
    autonomousMode,
}: ChatInterfaceProps) {
    const scrollRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [selectedImage, setSelectedImage] = useState<string | null>(null);
    const [visibleCount, setVisibleCount] = useState(10);
    const lastScrollHeightRef = useRef(0);
    const isLoadingHistoryRef = useRef(false);
    const isAtBottomRef = useRef(true);


    useEffect(() => {
        if (!scrollRef.current) return;
        const lastMsg = messages[messages.length - 1];
        const shouldStick = isAtBottomRef.current || lastMsg?.sender === 'user';
        if (shouldStick) {
            scrollRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [messages, selectedImage]);

    const hasThinking = [...messages].reverse().some(
        (m) => m.sender === 'bot' && !!m.thinking
    );
    const runningTool = [...messages].reverse().find(
        (m) => m.type === 'tool' && m.toolExecution?.status === 'running'
    );
    const waitingTool = [...messages].reverse().find(
        (m) => m.type === 'tool' && (m.toolExecution?.status === 'waiting_confirmation' || m.toolExecution?.status === 'pending_confirmation')
    );

    const handleKeyPress = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    const handleSend = () => {
        if (inputValue.trim() || selectedImage) {
            onSendMessage(null, selectedImage);
            setSelectedImage(null);
            // Reset height of textarea (hacky but works for simple auto-grow)
            const textarea = document.querySelector('textarea');
            if (textarea) textarea.style.height = 'auto';
        }
    };

    const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file) {
            const reader = new FileReader();
            reader.onloadend = () => {
                setSelectedImage(reader.result as string);
            };
            reader.readAsDataURL(file);
        }
    };

    const handlePaste = (e: React.ClipboardEvent) => {
        const items = e.clipboardData.items;
        for (let i = 0; i < items.length; i++) {
            if (items[i].type.indexOf('image') !== -1) {
                const blob = items[i].getAsFile();
                if (blob) {
                    const reader = new FileReader();
                    reader.onload = (event) => {
                        setSelectedImage(event.target?.result as string);
                    };
                    reader.readAsDataURL(blob);
                    e.preventDefault();
                }
            }
        }
    };

    const [powerModalOpen, setPowerModalOpen] = useState(false);
    const [powerLoading, setPowerLoading] = useState(false);

    const handlePowerAction = async (action: 'restart' | 'shutdown') => {
        setPowerLoading(true);
        try {
            const res = await axios.post(`${API_BASE_URL}/api/control/${action}`);
            console.log(res.data);
            // Allow some time for the server to react before closing/resetting
            setTimeout(() => {
                setPowerLoading(false);
                setPowerModalOpen(false);
                if (action === 'restart') {
                    // Trigger a reconnect attempt or reload page
                    window.location.reload();
                }
            }, 2000);
        } catch (err) {
            console.error(`Failed to ${action}:`, err);
            setPowerLoading(false);
        }
    };

    const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
        const viewport = e.currentTarget;
        const atBottom = viewport.scrollHeight - (viewport.scrollTop + viewport.clientHeight) < 80;
        isAtBottomRef.current = atBottom;
        if (viewport.scrollTop === 0 && messages.length > visibleCount) {
            isLoadingHistoryRef.current = true;
            lastScrollHeightRef.current = viewport.scrollHeight;
            setVisibleCount(prev => Math.min(messages.length, prev + 10));
        }
    };

    useLayoutEffect(() => {
        if (isLoadingHistoryRef.current) {
            const viewport = document.querySelector('[data-radix-scroll-area-viewport]') as HTMLDivElement;
            if (viewport) {
                viewport.scrollTop = viewport.scrollHeight - lastScrollHeightRef.current;
            }
            isLoadingHistoryRef.current = false;
        }
    }, [visibleCount]);

    // --- SKILLS MANAGEMENT ---
    const [skillsModalOpen, setSkillsModalOpen] = useState(false);
    const [skills, setSkills] = useState<any[]>([]);
    const [skillsLoading, setSkillsLoading] = useState(false);

    const fetchSkills = async () => {
        setSkillsLoading(true);
        try {
            const res = await axios.get(`${API_BASE_URL}/api/skills`);
            setSkills(res.data.skills);
        } catch (err) {
            console.error("Failed to fetch skills:", err);
        } finally {
            setSkillsLoading(false);
        }
    };

    const toggleSkill = async (skillId: string, currentStatus: boolean, missingDeps: boolean) => {
        if (missingDeps && !currentStatus) {
            return;
        }
        // Optimistic update
        setSkills(prev => prev.map(s => s.id === skillId ? { ...s, active: !currentStatus } : s));

        try {
            const res = await axios.post(`${API_BASE_URL}/api/skills/${skillId}/toggle`, { enable: !currentStatus });
            console.log(res.data);
            if (res.data.status === "success") {
                // Backend restarts automatically
            }
        } catch (err) {
            console.error("Failed to toggle skill:", err);
            // Revert
            setSkills(prev => prev.map(s => s.id === skillId ? { ...s, active: currentStatus } : s));
        }
    };

    const handleToolConfirmSideChannel = async (confId: string, approved: boolean, sessionWhitelist: boolean) => {
        try {
            const apiKey = localStorage.getItem('limebot_api_key');
            await axios.post(`${API_BASE_URL}/api/confirm-tool`, {
                conf_id: confId,
                approved,
                session_whitelist: sessionWhitelist
            }, {
                headers: {
                    'Content-Type': 'application/json',
                    ...(apiKey ? { 'x-api-key': apiKey } : {})
                }
            });
        } catch (err) {
            console.error("Failed to confirm tool via side-channel:", err);
        }
    };

    useEffect(() => {
        if (skillsModalOpen) {
            fetchSkills();
        }
    }, [skillsModalOpen]);

    const formatMissingDeps = (skill: any) => {
        const missing = skill?.missing_deps || {};
        const python = missing.python || [];
        const node = missing.node || [];
        const binaries = missing.binaries || [];
        const parts: string[] = [];
        if (python.length) parts.push(`python: ${python.join(", ")}`);
        if (node.length) parts.push(`node: ${node.join(", ")}`);
        if (binaries.length) parts.push(`binaries: ${binaries.join(", ")}`);
        return parts.join(" | ");
    };

    const formatRequiredDeps = (skill: any) => {
        const required = skill?.required_deps || {};
        const python = required.python || [];
        const node = required.node || [];
        const binaries = required.binaries || [];
        const parts: string[] = [];
        if (python.length) parts.push(`python: ${python.join(", ")}`);
        if (node.length) parts.push(`node: ${node.join(", ")}`);
        if (binaries.length) parts.push(`binaries: ${binaries.join(", ")}`);
        return parts.join(" | ");
    };


    return (
        <div className="flex flex-col h-full bg-background relative">
            {/* Chat Header - Hidden on mobile, shown on md+ */}
            <header className="hidden md:flex h-16 items-center gap-4 px-6 border-b border-border bg-card z-10 transition-all duration-200">
                <div className="flex-1">
                    <h1 className="text-lg font-bold text-foreground">Chat</h1>
                    <p className="text-xs text-muted-foreground">
                        Direct Gateway Session
                    </p>
                </div>
                <div className="flex items-center gap-3">
                    <div className={cn("px-3 py-1 rounded-full text-[10px] font-bold tracking-wide border flex items-center gap-2",
                        isConnected
                            ? "bg-primary/10 text-primary border-primary/20"
                            : "bg-muted text-muted-foreground border-border"
                    )}>
                        <div className={cn("w-1.5 h-1.5 rounded-full", isConnected ? "bg-primary" : "bg-muted-foreground")} />
                        {isConnected ? "ONLINE" : "OFFLINE"}
                    </div>

                    <div className="h-6 w-px bg-border mx-1" />

                    {autonomousMode && (
                        <div className="px-2 py-1 rounded bg-yellow-500/10 text-yellow-500 border border-yellow-500/20 text-[10px] font-bold tracking-wide flex items-center gap-1.5 animate-pulse">
                            <span className="relative flex h-1.5 w-1.5">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-yellow-500 opacity-75"></span>
                                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-yellow-500"></span>
                            </span>
                            AUTONOMOUS
                        </div>
                    )}

                    {/* Skills Button */}
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setSkillsModalOpen(true)}
                        title="Manage Skills"
                        className="h-8 w-8 text-muted-foreground hover:text-foreground"
                    >
                        <Zap className="h-4 w-4" />
                    </Button>

                    {/* Mobile Header Spacer/Compact View */}
                    <header className="md:hidden flex h-14 items-center justify-between px-4 border-b border-border bg-card/60 backdrop-blur-sm z-10">
                        {/* Left side empty for hamburger menu space */}
                        <div className="w-8"></div>

                        <div className="flex items-center gap-2">
                            <div className={cn("px-2 py-0.5 rounded-full text-[10px] font-bold border flex items-center gap-1.5",
                                isConnected
                                    ? "bg-primary/5 text-primary border-primary/10"
                                    : "bg-muted text-muted-foreground border-border"
                            )}>
                                <div className={cn("w-1 h-1 rounded-full", isConnected ? "bg-primary" : "bg-muted-foreground")} />
                                {isConnected ? "ON" : "OFF"}
                            </div>
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => setSkillsModalOpen(true)}
                                className="h-8 w-8 -mr-2 text-muted-foreground"
                            >
                                <Zap className="h-4 w-4" />
                            </Button>
                        </div>
                    </header>

                    <AlertDialog open={skillsModalOpen} onOpenChange={setSkillsModalOpen}>
                        <AlertDialogContent className="border-primary/20 max-w-2xl max-h-[80vh] overflow-hidden flex flex-col p-0">
                            <AlertDialogHeader className="px-6 pt-6 pb-2">
                                <AlertDialogTitle className="flex items-center gap-2">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-zap text-primary"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" /></svg>
                                    Skill Management
                                </AlertDialogTitle>
                                <AlertDialogDescription>
                                    Enable or disable skills to optimize context usage.
                                    <br /><span className="text-xs text-yellow-500 font-medium">⚠️ Toggling a skill triggers a backend restart.</span>
                                </AlertDialogDescription>
                            </AlertDialogHeader>

                            <div className="flex-1 overflow-y-auto px-6 py-2">
                                {skillsLoading ? (
                                    <div className="flex justify-center py-8">
                                        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                                    </div>
                                ) : (
                                    <div className="grid grid-cols-1 gap-3">
                                        {skills.map(skill => (
                                            <div key={skill.id} className={cn(
                                                "flex items-center justify-between p-3 rounded-lg border transition-all",
                                                skill.active
                                                    ? "bg-primary/5 border-primary/20"
                                                    : "bg-muted/50 border-border opacity-70 hover:opacity-100"
                                            )}>
                                                <div className="flex-1 min-w-0 mr-4">
                                                    <div className="flex items-center gap-2 mb-1">
                                                        <span className="font-semibold text-sm">{skill.name}</span>
                                                        {!skill.active && <span className="text-[10px] bg-muted-foreground/20 text-muted-foreground px-1.5 rounded">DISABLED</span>}
                                                        {skill.deps_ok === false && (
                                                            <span
                                                                className="text-[10px] bg-red-500/10 text-red-500 px-1.5 rounded border border-red-500/20"
                                                                title={formatMissingDeps(skill)}
                                                            >
                                                                MISSING DEPS
                                                            </span>
                                                        )}
                                                    </div>
                                                    <p className="text-xs text-muted-foreground truncate" title={skill.description}>
                                                        {skill.description}
                                                    </p>
                                                    {formatRequiredDeps(skill) && (
                                                        <div className="mt-2 text-[10px] text-muted-foreground font-mono bg-muted/40 rounded px-2 py-1 border border-border/60">
                                                            <span className="uppercase tracking-wide text-[8px] text-muted-foreground/70 block mb-0.5">Dependencies</span>
                                                            <div className="whitespace-pre-wrap break-words">{formatRequiredDeps(skill)}</div>
                                                        </div>
                                                    )}
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <Button
                                                        variant={skill.active ? "destructive" : "default"}
                                                        size="sm"
                                                        className="h-7 text-xs"
                                                        disabled={skill.deps_ok === false && !skill.active}
                                                        onClick={() => toggleSkill(skill.id, skill.active, skill.deps_ok === false)}
                                                    >
                                                        {skill.active ? "Disable" : "Enable"}
                                                    </Button>
                                                </div>
                                            </div>
                                        ))}
                                        {skills.length === 0 && !skillsLoading && (
                                            <div className="text-center text-muted-foreground text-sm py-4">
                                                No skills found.
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>

                            <AlertDialogFooter className="p-6 pt-2">
                                <AlertDialogCancel>Close</AlertDialogCancel>
                            </AlertDialogFooter>
                        </AlertDialogContent>
                    </AlertDialog>

                    {onNewChat && (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={onNewChat}
                            className="h-8 gap-2 text-primary border-primary/20 hover:bg-primary/10 hover:text-primary"
                        >
                            <Plus className="h-4 w-4" />
                            <span className="font-semibold text-xs">New Session</span>
                        </Button>
                    )}

                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setPowerModalOpen(true)}
                        title="Power Controls"
                        className={cn("h-8 w-8 text-muted-foreground hover:text-foreground", isConnected ? "hover:text-red-500" : "")}
                    >
                        <Power className="h-4 w-4" />
                    </Button>

                    <AlertDialog open={powerModalOpen} onOpenChange={setPowerModalOpen}>
                        <AlertDialogContent className="border-primary/20">
                            <AlertDialogHeader>
                                <AlertDialogTitle className="flex items-center gap-2">
                                    <Power className="h-5 w-5 text-primary" />
                                    System Control
                                </AlertDialogTitle>
                                <AlertDialogDescription>
                                    Manage the LimeBot backend process.
                                </AlertDialogDescription>
                            </AlertDialogHeader>

                            <div className="grid grid-cols-2 gap-4 py-4">
                                <Button
                                    variant="outline"
                                    className="h-24 flex flex-col gap-2 hover:bg-primary/10 hover:border-primary/50 hover:text-primary"
                                    onClick={() => handlePowerAction('restart')}
                                    disabled={powerLoading}
                                >
                                    {powerLoading ? <Loader2 className="h-6 w-6 animate-spin" /> : <Power className="h-6 w-6" />}
                                    <span className="font-bold">Restart</span>
                                    <span className="text-xs text-muted-foreground font-normal">Reload backend & config</span>
                                </Button>

                                <Button
                                    variant="outline"
                                    className="h-24 flex flex-col gap-2 hover:bg-red-500/10 hover:border-red-500/50 hover:text-red-500"
                                    onClick={() => handlePowerAction('shutdown')}
                                    disabled={powerLoading}
                                >
                                    {powerLoading ? <Loader2 className="h-6 w-6 animate-spin" /> : <Power className="h-6 w-6" />}
                                    <span className="font-bold">Shutdown</span>
                                    <span className="text-xs text-muted-foreground font-normal">Stop process completely</span>
                                </Button>
                            </div>

                            <AlertDialogFooter>
                                <AlertDialogCancel disabled={powerLoading}>Cancel</AlertDialogCancel>
                            </AlertDialogFooter>
                        </AlertDialogContent>
                    </AlertDialog>
                </div>
            </header>

            {/* Chat Messages */}
            <div className="flex-1 overflow-hidden relative">
                <ScrollArea className="h-full p-4 md:p-8" onScroll={handleScroll}>
                    <div className="flex flex-col gap-8 max-w-4xl mx-auto pb-4">
                        {messages.length === 0 && (
                            <div className="flex flex-col items-center justify-center min-h-[50vh] text-center space-y-4">
                                <Avatar className="h-20 w-20 shadow-xl shadow-primary/20">
                                    <AvatarImage src={botIdentity?.avatar || undefined} className="object-cover" />
                                    <AvatarFallback className="bg-primary/10 text-primary">
                                        <Bot className="h-10 w-10" />
                                    </AvatarFallback>
                                </Avatar>
                                <div className="space-y-1">
                                    <h3 className="font-semibold text-lg text-foreground">
                                        {botIdentity?.name ? `${botIdentity.name} is Ready` : "System Ready"}
                                    </h3>
                                    <p className="text-sm text-muted-foreground max-w-xs mx-auto">
                                        I am active and listening. Start a secure session below.
                                    </p>
                                </div>
                            </div>
                        )}
                        {(() => {
                            const visible = messages.slice(-visibleCount);
                            const items: Array<
                                | { kind: 'message'; msg: Message; showAvatar: boolean; showHeader: boolean; key: string }
                                | { kind: 'tool_timeline'; executions: ToolExecution[]; key: string }
                            > = [];

                            for (let i = 0; i < visible.length; i++) {
                                const msg = visible[i];
                                if (msg.type === 'tool' && msg.toolExecution) {
                                    const start = i;
                                    const toolGroup: ToolExecution[] = [];
                                    while (i < visible.length && visible[i].type === 'tool' && visible[i].toolExecution) {
                                        toolGroup.push(visible[i].toolExecution as ToolExecution);
                                        i++;
                                    }
                                    const count = toolGroup.length;
                                    if (count >= 3) {
                                        items.push({
                                            kind: 'tool_timeline',
                                            executions: toolGroup,
                                            key: `${activeChatId}-tools-${start}`,
                                        });
                                    } else {
                                        for (let j = 0; j < toolGroup.length; j++) {
                                            const m = visible[start + j];
                                            items.push({
                                                kind: 'message',
                                                msg: m,
                                                showAvatar: j === 0,
                                                showHeader: j === 0,
                                                key: `${activeChatId}-${start + j}`,
                                            });
                                        }
                                    }
                                    i -= 1;
                                    continue;
                                }

                                const prev = items.length > 0 && items[items.length - 1].kind === 'message'
                                    ? (items[items.length - 1] as { kind: 'message'; msg: Message }).msg
                                    : null;
                                const showAvatar = !prev || prev.sender !== msg.sender || prev.type !== msg.type;
                                const showHeader = showAvatar && msg.sender === 'bot';

                                items.push({
                                    kind: 'message',
                                    msg,
                                    showAvatar,
                                    showHeader,
                                    key: `${activeChatId}-${i}`,
                                });
                            }

                            return items.map((item) => {
                                if (item.kind === 'tool_timeline') {
                                    return (
                                        <ToolTimeline
                                            key={item.key}
                                            executions={item.executions}
                                            botIdentity={botIdentity}
                                            onConfirmSideChannel={handleToolConfirmSideChannel}
                                        />
                                    );
                                }

                                return (
                                    <MemoizedMessageItem
                                        key={item.key}
                                        msg={item.msg}
                                        botIdentity={botIdentity}
                                        handleToolConfirmSideChannel={handleToolConfirmSideChannel}
                                        onSendMessage={onSendMessage}
                                        showAvatar={item.showAvatar}
                                        showHeader={item.showHeader}
                                    />
                                );
                            });
                        })()}
                    </div>

                    {/* Typing Indicator */}
                    {isTyping && (
                        <div className="flex w-full gap-4 max-w-[85%]">
                            <Avatar className={cn(
                                "h-8 w-8 mt-1 shrink-0",
                                (isTyping || runningTool || waitingTool) && "lime-avatar-pulse"
                            )}>
                                <AvatarImage src={botIdentity?.avatar || undefined} className="object-cover" />
                                <AvatarFallback className="bg-primary text-primary-foreground text-xs">LB</AvatarFallback>
                            </Avatar>
                            <div className="flex flex-col gap-2">
                                {hasThinking && (
                                    <div className="lime-activity-ribbon">
                                        <div className={cn("lime-step", hasThinking && "is-active")}>Memory</div>
                                        <div className={cn(
                                            "lime-step",
                                            (runningTool || waitingTool) && "is-active"
                                        )}>Tools</div>
                                        <div className={cn(
                                            "lime-step",
                                            !(runningTool || waitingTool) && hasThinking && "is-active"
                                        )}>Compose</div>
                                    </div>
                                )}
                                <div className="bg-muted text-foreground rounded-xl rounded-tl-sm px-4 py-3 flex items-center gap-1">
                                    <div className="w-2 h-2 rounded-full bg-primary/50 animate-bounce [animation-delay:-0.3s]"></div>
                                    <div className="w-2 h-2 rounded-full bg-primary/50 animate-bounce [animation-delay:-0.15s]"></div>
                                    <div className="w-2 h-2 rounded-full bg-primary/50 animate-bounce"></div>
                                </div>
                            </div>
                        </div>
                    )}
                    <div ref={scrollRef} />
                </ScrollArea>
            </div>

            {/* Input Area */}
            <div className="p-6 bg-background border-t border-border relative z-20">
                <div className="max-w-4xl mx-auto relative group">
                    {/* Image Preview */}
                    {selectedImage && (
                        <div className="absolute bottom-full left-0 mb-4 bg-background border border-border p-2 rounded-xl shadow-lg animate-in fade-in slide-in-from-bottom-2">
                            <div className="relative">
                                <img src={selectedImage} alt="Preview" className="h-24 w-auto rounded-lg object-cover" />
                                <button
                                    onClick={() => setSelectedImage(null)}
                                    className="absolute -top-2 -right-2 bg-destructive text-white rounded-full p-1 shadow-sm hover:bg-destructive/90 transition-colors"
                                >
                                    <X className="w-3 h-3" />
                                </button>
                            </div>
                        </div>
                    )}

                    <div className="relative flex items-end bg-muted/50 rounded-xl">
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-10 w-10 mb-1 ml-1 text-muted-foreground hover:text-foreground"
                            onClick={() => fileInputRef.current?.click()}
                        >
                            <Paperclip className="h-5 w-5" />
                        </Button>
                        <input
                            type="file"
                            ref={fileInputRef}
                            className="hidden"
                            accept="image/*"
                            onChange={handleFileSelect}
                        />

                        {/* Stop Button (visible when typing or executing tools) */}
                        {(isTyping || messages.some(m => m.type === 'tool' && m.toolExecution?.status === 'running')) && (
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-10 w-10 mb-1 text-destructive hover:text-destructive hover:bg-destructive/10 animate-in fade-in zoom-in duration-200"
                                onClick={async () => {
                                    if (!activeChatId) return;
                                    try {
                                        const apiKey = localStorage.getItem('limebot_api_key');
                                        await axios.post(`${API_BASE_URL}/api/chat/${activeChatId}/stop`, {}, {
                                            headers: {
                                                'Content-Type': 'application/json',
                                                ...(apiKey ? { 'x-api-key': apiKey } : {})
                                            }
                                        });
                                        // Specific UI feedback handled by state or toast if needed
                                    } catch (e) {
                                        console.error("Failed to stop generation:", e);
                                    }
                                }}
                                title="Stop generating"
                            >
                                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-square"><rect width="18" height="18" x="3" y="3" rx="2" /></svg>
                            </Button>
                        )}

                        <Textarea
                            placeholder="Type a message (or paste an image)..."
                            value={inputValue}
                            onChange={(e) => onInputChange(e.target.value)}
                            onKeyDown={handleKeyPress}
                            onPaste={handlePaste}
                            disabled={!isConnected}
                            className="flex-1 min-h-[50px] max-h-[200px] border-0 bg-transparent py-4 px-4 focus-visible:ring-0 focus-visible:ring-offset-0 placeholder:text-muted-foreground text-sm resize-none"
                            rows={1}
                            onInput={(e: React.FormEvent<HTMLTextAreaElement>) => {
                                const target = e.target as HTMLTextAreaElement;
                                target.style.height = 'auto';
                                target.style.height = `${target.scrollHeight}px`;
                            }}
                        />
                        <div className="pb-2 pr-2">
                            <Button
                                onClick={handleSend}
                                disabled={!isConnected || isTyping || (!inputValue.trim() && !selectedImage)}
                                size="icon"
                                className={cn(
                                    "h-9 w-9 rounded-lg transition-all duration-200",
                                    (inputValue.trim() || selectedImage)
                                        ? "bg-primary hover:bg-primary/90 text-primary-foreground"
                                        : "bg-transparent text-muted-foreground hover:bg-muted-foreground/10"
                                )}
                            >
                                <Send className="h-4 w-4" />
                                <span className="sr-only">Send</span>
                            </Button>
                        </div>
                    </div>
                </div>
                <div className="max-w-4xl mx-auto mt-2 flex justify-between px-1 text-[10px] font-medium text-muted-foreground">
                    <span>Secure Channel</span>
                    <span>LimeBot v1.0.2</span>
                </div>
            </div>
        </div>
    );
}

function ChatImage({ src, alt }: { src: string, alt: string }) {
    const [error, setError] = useState(false);

    if (error) return null;

    return (
        <img
            src={src}
            alt={alt}
            className="max-w-full rounded-lg mb-2 max-h-64 object-cover"
            onError={() => setError(true)}
        />
    );
}
