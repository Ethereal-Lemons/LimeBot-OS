import { useRef, useEffect, useState, useLayoutEffect } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';
import axios from 'axios';
import { API_BASE_URL } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";
import { cn } from "@/lib/utils";
import { Send, Bot, Power, Paperclip, X, User, Plus, Zap } from "lucide-react";
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


    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [messages, selectedImage]);

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
            const res = await axios.post(`http://localhost:8000/api/control/${action}`);
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
            const res = await axios.get('http://localhost:8000/api/skills');
            setSkills(res.data.skills);
        } catch (err) {
            console.error("Failed to fetch skills:", err);
        } finally {
            setSkillsLoading(false);
        }
    };

    const toggleSkill = async (skillId: string, currentStatus: boolean) => {
        // Optimistic update
        setSkills(prev => prev.map(s => s.id === skillId ? { ...s, active: !currentStatus } : s));

        try {
            const res = await axios.post(`http://localhost:8000/api/skills/${skillId}/toggle`, { enable: !currentStatus });
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
                                                    </div>
                                                    <p className="text-xs text-muted-foreground truncate" title={skill.description}>
                                                        {skill.description}
                                                    </p>
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <Button
                                                        variant={skill.active ? "destructive" : "default"}
                                                        size="sm"
                                                        className="h-7 text-xs"
                                                        onClick={() => toggleSkill(skill.id, skill.active)}
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
                        {messages.slice(-visibleCount).map((msg, index) => (
                            <div
                                key={index}
                                className={cn(
                                    "flex w-full gap-4 max-w-[85%]",
                                    msg.sender === 'user' ? "ml-auto flex-row-reverse" : ""
                                )}
                            >
                                <Avatar className="h-8 w-8 mt-1 shrink-0">
                                    {msg.sender === 'bot' ? (
                                        <>
                                            <AvatarImage src={botIdentity?.avatar || undefined} className="object-cover" />
                                            <AvatarFallback className="bg-primary text-primary-foreground text-xs">LB</AvatarFallback>
                                        </>
                                    ) : (
                                        <AvatarFallback className="bg-muted text-muted-foreground">
                                            <User className="h-4 w-4" />
                                        </AvatarFallback>
                                    )}
                                </Avatar>

                                {msg.type === 'tool' && msg.toolExecution ? (
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
                                ) : msg.type === 'confirmation' && msg.confirmation ? (
                                    <ConfirmationCard
                                        request={msg.confirmation}
                                    />
                                ) : msg.variant && msg.variant !== 'default' ? (
                                    <Alert variant={msg.variant} className="max-w-xl shadow-md">
                                        {msg.variant === 'destructive' ? (
                                            <AlertTriangle className="h-4 w-4" />
                                        ) : (
                                            <Info className="h-4 w-4" />
                                        )}
                                        <AlertTitle className="ml-2">
                                            {msg.variant === 'destructive' ? 'Error' : 'Warning'}
                                        </AlertTitle>
                                        <AlertDescription className="ml-2 mt-2">
                                            {msg.content}
                                        </AlertDescription>
                                    </Alert>
                                ) : (
                                    <div className={cn(
                                        "relative flex flex-col gap-1 px-4 py-3 text-sm min-w-0",
                                        msg.sender === 'user'
                                            ? "bg-zinc-800 text-white rounded-xl rounded-tr-sm"
                                            : "bg-muted text-foreground rounded-xl rounded-tl-sm",
                                        // Special styling for confirmation messages (hidden if possible but keeping styles just in case)
                                        msg.content.includes('[CONFIRM_SESSION]') && "bg-purple-900/20 border border-purple-500/30 text-purple-200 py-1 px-3 rounded-full text-xs w-fit self-end hidden",
                                        msg.content.includes('[CONFIRM_EXECUTION]') && "bg-amber-900/20 border border-amber-500/30 text-amber-200 py-1 px-3 rounded-full text-xs w-fit self-end hidden"
                                    )}>
                                        {!msg.content.includes('[CONFIRM_SESSION]') && !msg.content.includes('[CONFIRM_EXECUTION]') && (
                                            <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-wide opacity-60 mb-1">
                                                {msg.sender === 'user'
                                                    ? <span>You</span>
                                                    : <span className="text-primary">{botIdentity?.name || "LimeBot"}</span>
                                                }
                                            </div>
                                        )}

                                        {/* Thinking Bubble */}
                                        {msg.thinking && (
                                            <ThinkingBubble
                                                content={msg.thinking}
                                                isComplete={!!msg.content}
                                                defaultCollapsed={!!msg.content}
                                            />
                                        )}

                                        <div className="leading-relaxed whitespace-pre-wrap break-words">
                                            {msg.image && (
                                                <ChatImage src={msg.image} alt="Uploaded content" />
                                            )}
                                            <div className="prose prose-sm dark:prose-invert max-w-none break-words leading-relaxed">
                                                <ReactMarkdown
                                                    remarkPlugins={[remarkGfm]}
                                                    components={{
                                                        a: ({ node, ...props }) => (
                                                            <a
                                                                {...props}
                                                                target="_blank"
                                                                rel="noopener noreferrer"
                                                                className={cn(
                                                                    "font-bold underline break-all",
                                                                    msg.sender === 'user'
                                                                        ? "text-primary hover:text-primary/80 decoration-primary/50"
                                                                        : "text-primary hover:opacity-80 decoration-primary/30"
                                                                )}
                                                            />
                                                        ),
                                                        p: ({ node, ...props }) => <p {...props} className="mb-1 last:mb-0" />,
                                                        code: ({ node, className, children, ...props }: any) => {
                                                            // Hide empty code blocks
                                                            const content = String(children || '').trim();
                                                            if (!content) return null;

                                                            const match = /language-(\w+)/.exec(className || '');

                                                            return !match ? (
                                                                <code className={cn("bg-muted/50 px-1 py-0.5 rounded font-mono text-xs", msg.sender === 'user' ? "bg-black/40" : "bg-muted")} {...props}>
                                                                    {children}
                                                                </code>
                                                            ) : (
                                                                <div className="rounded-lg my-2 border border-border overflow-hidden text-sm">
                                                                    <div className="bg-zinc-900 px-3 py-1 text-xs text-muted-foreground border-b border-zinc-800 flex justify-between items-center">
                                                                        <span>{match[1]}</span>
                                                                    </div>
                                                                    <SyntaxHighlighter
                                                                        {...props}
                                                                        style={vscDarkPlus}
                                                                        language={match[1]}
                                                                        PreTag="div"
                                                                        customStyle={{ margin: 0, padding: '1rem', background: '#18181b' }} // zinc-950
                                                                    >
                                                                        {content}
                                                                    </SyntaxHighlighter>
                                                                </div>
                                                            );
                                                        },
                                                        table: ({ node, ...props }) => (
                                                            <div className="my-4 w-full overflow-y-auto rounded-lg border border-border bg-card/50 shadow-sm">
                                                                <table className="w-full text-left text-sm" {...props} />
                                                            </div>
                                                        ),
                                                        thead: ({ node, ...props }) => (
                                                            <thead className="bg-muted/50 text-muted-foreground font-medium border-b border-border" {...props} />
                                                        ),
                                                        tbody: ({ node, ...props }) => <tbody className="divide-y divide-border/50" {...props} />,
                                                        tr: ({ node, ...props }) => <tr className="hover:bg-muted/30 transition-colors" {...props} />,
                                                        th: ({ node, ...props }) => <th className="px-4 py-3 font-semibold text-xs uppercase tracking-wider text-muted-foreground/80" {...props} />,
                                                        td: ({ node, ...props }) => <td className="px-4 py-2 align-top text-foreground/90" {...props} />,

                                                        // Headings
                                                        h1: ({ node, ...props }) => <h1 className="text-2xl font-bold mt-6 mb-4 pb-2 border-b border-border text-foreground" {...props} />,
                                                        h2: ({ node, ...props }) => <h2 className="text-xl font-semibold mt-5 mb-3 text-foreground/90" {...props} />,
                                                        h3: ({ node, ...props }) => <h3 className="text-lg font-medium mt-4 mb-2 text-foreground/90" {...props} />,
                                                        h4: ({ node, ...props }) => <h4 className="text-base font-medium mt-3 mb-2 text-foreground/80" {...props} />,

                                                        // Block elements
                                                        blockquote: ({ node, ...props }) => (
                                                            <blockquote className="border-l-4 border-primary/40 pl-4 py-1 my-4 italic text-muted-foreground bg-muted/20 rounded-r" {...props} />
                                                        ),
                                                        ul: ({ node, ...props }) => <ul className="list-disc pl-5 mb-3 space-y-1 text-foreground/90" {...props} />,
                                                        ol: ({ node, ...props }) => <ol className="list-decimal pl-5 mb-3 space-y-1 text-foreground/90" {...props} />,
                                                        li: ({ node, ...props }) => <li className="pl-1" {...props} />,

                                                        // Hide horizontal rules (often left behind after stripping tags)
                                                        hr: () => <hr className="my-6 border-border" />,

                                                        // Images
                                                        img: ({ node, ...props }: any) => (
                                                            <ChatImage src={props.src || ''} alt={props.alt || ''} />
                                                        ),
                                                    }}
                                                >
                                                    {msg.content}
                                                </ReactMarkdown>
                                            </div>
                                        </div>

                                        <span className={cn(
                                            "absolute -bottom-5 text-[9px] font-medium opacity-40 text-black",
                                            msg.sender === 'user' ? "right-1" : "left-1"
                                        )}>
                                            {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                        </span>
                                    </div>
                                )}


                            </div>
                        ))}
                    </div>

                    {/* Typing Indicator */}
                    {isTyping && (
                        <div className="flex w-full gap-4 max-w-[85%]">
                            <Avatar className="h-8 w-8 mt-1 shrink-0">
                                <AvatarImage src={botIdentity?.avatar || undefined} className="object-cover" />
                                <AvatarFallback className="bg-primary text-primary-foreground text-xs">LB</AvatarFallback>
                            </Avatar>
                            <div className="bg-muted text-foreground rounded-xl rounded-tl-sm px-4 py-3 flex items-center gap-1">
                                <div className="w-2 h-2 rounded-full bg-primary/50 animate-bounce [animation-delay:-0.3s]"></div>
                                <div className="w-2 h-2 rounded-full bg-primary/50 animate-bounce [animation-delay:-0.15s]"></div>
                                <div className="w-2 h-2 rounded-full bg-primary/50 animate-bounce"></div>
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
                                disabled={!isConnected || (!inputValue.trim() && !selectedImage)}
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
                    <span>LimeBot v1.0.0</span>
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
