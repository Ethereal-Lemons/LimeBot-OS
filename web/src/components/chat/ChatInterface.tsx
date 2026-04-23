import { useRef, useEffect, useState, memo } from 'react';
import axios from 'axios';
import { API_BASE_URL } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";
import { cn } from "@/lib/utils";
import { Send, Bot, Power, Paperclip, X, User, Plus, Zap, ArrowDown, ShieldAlert, Wifi, WifiOff } from "lucide-react";
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
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AlertTriangle, Info } from "lucide-react";
import { ThinkingBubble } from './ThinkingBubble';
import { toast } from "sonner";
import type { ChatAttachment } from "@/lib/chat-state";


import { ToolTimeline } from './ToolTimeline';
import { AttachmentPreview } from './AttachmentPreview';
import { MarkdownMessage } from './MarkdownMessage';
import { parseSubagentReport, SubagentReportCard } from './SubagentReportCard';

interface Message {
    sender: 'user' | 'bot';
    type?: 'text' | 'tool' | 'confirmation';
    content: string;
    thinking?: string;
    isStreaming?: boolean;
    image?: string | null;
    attachments?: ChatAttachment[];
    toolExecution?: ToolExecution;
    variant?: 'default' | 'destructive' | 'warning';
    messageId?: string;
    turnId?: string;
}

interface ChatInterfaceProps {
    messages: Message[];
    inputValue: string;
    isConnected: boolean;
    isTyping?: boolean;
    botIdentity?: { name: string; avatar: string | null };
    onInputChange: (value: string) => void;
    onSendMessage: (content?: string | null, attachment?: ChatAttachment | null) => void;
    onReconnect: () => void;
    onNewChat?: () => void;
    activeChatId: string;
    autonomousMode?: boolean;
    llmRuntime?: {
        configured_model: string;
        active_model: string;
        fallback_models: string[];
        using_fallback: boolean;
    } | null;
    activityText?: string | null;
}

const QUICK_ACTIONS = [
    { label: "Project status", prompt: "Review the current project state and tell me what needs attention." },
    { label: "Session recap", prompt: "Summarize what you remember about this session and what should happen next." },
    { label: "UI suggestion", prompt: "Inspect the codebase and propose the next UI improvement." },
];

const MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024;
const ACCEPTED_ATTACHMENT_TYPES = [
    "image/*",
    ".pdf",
    ".doc",
    ".docx",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
].join(",");

const WORD_DOCUMENT_MIME_TYPES = new Set([
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);

function StatusChip({
    label,
    value,
    tone = 'default',
}: {
    label: string;
    value: string;
    tone?: 'default' | 'good' | 'warn';
}) {
    return (
        <div
            className={cn(
                "inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]",
                tone === 'good' && "border-emerald-500/20 bg-emerald-500/10 text-emerald-500",
                tone === 'warn' && "border-amber-500/20 bg-amber-500/10 text-amber-500",
                tone === 'default' && "border-border bg-background/70 text-muted-foreground"
            )}
        >
            <span>{label}</span>
            <span className="normal-case tracking-normal text-foreground">{value}</span>
        </div>
    );
}

function isSubagentReportMessage(msg?: Message | null): boolean {
    if (!msg || msg.sender !== 'bot') return false;
    return !!parseSubagentReport(msg.content || '');
}

function isSpawnAgentToolMessage(msg?: Message | null): boolean {
    return msg?.type === 'tool' && msg.toolExecution?.tool === 'spawn_agent';
}

function isBotTextLikeMessage(msg?: Message | null): boolean {
    return !!msg && msg.sender === 'bot' && msg.type !== 'tool' && !msg.isStreaming;
}

type ToolExecutionWithTurn = ToolExecution & {
    turnId?: string;
};

function shouldHideSpawnAgentGroup(toolGroup: ToolExecutionWithTurn[], nextMessage?: Message | null): boolean {
    if (!toolGroup.length || !toolGroup.every((execution) => execution.tool === 'spawn_agent')) {
        return false;
    }
    if (isSubagentReportMessage(nextMessage)) {
        return true;
    }
    if (!isBotTextLikeMessage(nextMessage)) {
        return false;
    }

    const nextTurnId = String(nextMessage?.turnId || '').trim();
    if (!nextTurnId) {
        return false;
    }

    return toolGroup.some((execution) => {
        const executionTurnId = String(execution.turnId || '').trim();
        return executionTurnId && executionTurnId === nextTurnId;
    });
}

function isSubagentOrchestrationThought(
    msg?: Message | null,
    prevMsg?: Message | null,
    nextMsg?: Message | null
): boolean {
    if (!msg || msg.sender !== 'bot' || !msg.thinking) return false;
    if (String(msg.content || '').trim()) return false;
    if (msg.attachments?.length || msg.image) return false;

    return (
        isSpawnAgentToolMessage(prevMsg) ||
        isSpawnAgentToolMessage(nextMsg) ||
        isSubagentReportMessage(prevMsg) ||
        isSubagentReportMessage(nextMsg)
    );
}

const UnreadSeparator = ({ count }: { count: number }) => (
    <div className="flex items-center gap-3 py-1">
        <div className="h-px flex-1 bg-primary/20" />
        <div className="rounded-full border border-primary/20 bg-primary/10 px-3 py-1 text-[10px] font-bold uppercase tracking-widest text-primary shadow-sm">
            {count <= 1 ? "New Message" : `${count} New Messages`}
        </div>
        <div className="h-px flex-1 bg-primary/20" />
    </div>
);

function TypingIndicator({ botIdentity }: { botIdentity?: { name: string; avatar: string | null } }) {
    return (
        <div className="flex w-full max-w-[48rem] gap-3 animate-in fade-in slide-in-from-bottom-2 duration-200">
            <Avatar className="mt-0.5 h-8 w-8 shrink-0 border border-border/70 shadow-sm">
                <AvatarImage src={botIdentity?.avatar || undefined} className="object-cover" />
                <AvatarFallback className="bg-primary/10 text-primary text-xs font-semibold">Bot</AvatarFallback>
            </Avatar>
            <div className="flex items-center">
                <div className="flex items-center gap-1 rounded-2xl bg-muted/50 px-4 py-3 text-muted-foreground">
                    <span
                        className="h-2 w-2 rounded-full bg-muted-foreground/60"
                        style={{ animation: 'typing-dot 1.2s infinite ease-in-out', animationDelay: '0ms' }}
                    />
                    <span
                        className="h-2 w-2 rounded-full bg-muted-foreground/60"
                        style={{ animation: 'typing-dot 1.2s infinite ease-in-out', animationDelay: '200ms' }}
                    />
                    <span
                        className="h-2 w-2 rounded-full bg-muted-foreground/60"
                        style={{ animation: 'typing-dot 1.2s infinite ease-in-out', animationDelay: '400ms' }}
                    />
                </div>
            </div>
        </div>
    );
}

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
    const renderableAttachments = msg.attachments?.length
        ? msg.attachments
        : msg.image
            ? [{ name: 'Uploaded image', mimeType: 'image/*', kind: 'image', url: msg.image } satisfies ChatAttachment]
            : [];
    const subagentReport = !isUser ? parseSubagentReport(msg.content) : null;

    if (msg.content?.includes('[CONFIRM_SESSION]') || msg.content?.includes('[CONFIRM_EXECUTION]') || msg.type === 'confirmation') {
        return null; // Hidden confirmation trace messages
    }

    return (
        <div className={cn(
            "flex w-full gap-3",
            isUser ? "justify-end" : "max-w-[48rem]"
        )}>
            {isBot && showAvatar ? (
                <Avatar className="mt-0.5 h-8 w-8 shrink-0 border border-border/70 shadow-sm">
                    {isBot ? (
                        <>
                            <AvatarImage src={botIdentity?.avatar || undefined} className="object-cover" />
                            <AvatarFallback className="bg-primary/10 text-primary text-xs font-semibold">Bot</AvatarFallback>
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
            ) : isBot ? (
                <div className="h-8 w-8 shrink-0" />
            ) : (
                <div className="hidden" />
            )}

            <div className={cn(
                "flex min-w-0 flex-1 flex-col gap-1",
                isUser ? "items-end" : "items-start"
            )}>
                {!isUser && showHeader && (
                    <div className="ml-0.5 text-[11px] font-medium text-muted-foreground/70">
                        {botIdentity?.name || "LimeBot"}
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
                ) : msg.variant && msg.variant !== 'default' ? (
                    <Alert variant={msg.variant} className="max-w-xl shadow-sm border-l-4">
                        {msg.variant === 'destructive' ? <AlertTriangle className="h-4 w-4" /> : <Info className="h-4 w-4" />}
                        <AlertTitle className="ml-2 font-bold">{msg.variant === 'destructive' ? 'Error' : 'Warning'}</AlertTitle>
                        <AlertDescription className="ml-2 mt-1 text-xs opacity-90">{msg.content}</AlertDescription>
                    </Alert>
                ) : (
                    <>
                        {!isUser && msg.thinking && (
                            <ThinkingBubble
                                content={msg.thinking}
                                isComplete={!msg.isStreaming}
                                defaultCollapsed={!msg.isStreaming && !!msg.content}
                            />
                        )}

                        {(msg.content || renderableAttachments.length > 0) && (
                            <div className={cn(
                                "relative max-w-full overflow-hidden transition-all duration-200",
                                isUser
                                    ? "group max-w-[min(82%,30rem)] rounded-2xl rounded-tr-none bg-zinc-800 px-3.5 py-2 text-[14px] leading-tight text-white shadow-sm transition-all duration-300 hover:bg-zinc-700/90 hover:shadow-md"
                                    : "w-full bg-transparent px-0 py-0 text-foreground"
                            )}>
                                <div className="max-w-full overflow-x-auto whitespace-pre-wrap break-words">
                                    {renderableAttachments.map((attachment) => (
                                        <AttachmentPreview
                                            key={`${attachment.kind}:${attachment.name}:${attachment.url.slice(0, 24)}`}
                                            attachment={attachment}
                                        />
                                    ))}
                                    {subagentReport ? (
                                        <SubagentReportCard report={subagentReport} />
                                    ) : (
                                        <MarkdownMessage
                                            content={msg.content}
                                            isUser={isUser}
                                            isStreaming={msg.isStreaming}
                                        />
                                    )}
                                </div>
                            </div>
                        )}
                    </>
                )
                }
            </div >
        </div >
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
    llmRuntime,
    activityText,
}: ChatInterfaceProps) {
    const scrollAreaRef = useRef<HTMLDivElement>(null);
    const scrollRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [selectedAttachment, setSelectedAttachment] = useState<ChatAttachment | null>(null);
    const [isAtBottom, setIsAtBottom] = useState(true);
    const [unreadAnchorIndex, setUnreadAnchorIndex] = useState<number | null>(null);
    const [unreadCount, setUnreadCount] = useState(0);
    const isAtBottomRef = useRef(true);
    const prevMessageCountRef = useRef(messages.length);

    const getScrollViewport = () =>
        scrollAreaRef.current?.querySelector('[data-radix-scroll-area-viewport]') as HTMLDivElement | null;

    const clearUnread = () => {
        setUnreadAnchorIndex(null);
        setUnreadCount(0);
    };

    const syncBottomState = (forceClearUnread = false) => {
        const viewport = getScrollViewport();
        if (!viewport) return;

        const atBottom = viewport.scrollHeight - (viewport.scrollTop + viewport.clientHeight) <= 96;
        isAtBottomRef.current = atBottom;
        setIsAtBottom(atBottom);

        if (atBottom || forceClearUnread) {
            clearUnread();
        }
    };

    const scrollToLatest = (behavior: ScrollBehavior = 'smooth') => {
        const viewport = getScrollViewport();
        if (viewport) {
            viewport.scrollTo({ top: viewport.scrollHeight, behavior });
        } else if (scrollRef.current) {
            scrollRef.current.scrollIntoView({ behavior });
        }
        isAtBottomRef.current = true;
        setIsAtBottom(true);
        clearUnread();

        requestAnimationFrame(() => {
            requestAnimationFrame(() => syncBottomState(true));
        });
    };


    useEffect(() => {
        if (!scrollRef.current) return;
        const lastMsg = messages[messages.length - 1];
        const shouldStick = isAtBottomRef.current || lastMsg?.sender === 'user';
        if (shouldStick) {
            scrollToLatest(lastMsg?.sender === 'user' ? 'smooth' : 'auto');
            return;
        }
        requestAnimationFrame(() => syncBottomState(false));
    }, [messages, selectedAttachment]);

    useEffect(() => {
        const previousCount = prevMessageCountRef.current;
        const nextCount = messages.length;

        if (nextCount < previousCount) {
            clearUnread();
        } else if (nextCount > previousCount && !isAtBottomRef.current) {
            setUnreadAnchorIndex((current) => current ?? previousCount);
        }

        prevMessageCountRef.current = nextCount;
    }, [messages.length]);

    useEffect(() => {
        if (unreadAnchorIndex === null) {
            if (unreadCount !== 0) {
                setUnreadCount(0);
            }
            return;
        }

        const nextUnreadCount = Math.max(messages.length - unreadAnchorIndex, 0);
        if (nextUnreadCount !== unreadCount) {
            setUnreadCount(nextUnreadCount);
        }
    }, [messages.length, unreadAnchorIndex, unreadCount]);

    useEffect(() => {
        setIsAtBottom(true);
        isAtBottomRef.current = true;
        clearUnread();
        prevMessageCountRef.current = messages.length;
    }, [activeChatId]);

    const runningTool = [...messages].reverse().find(
        (m) => m.type === 'tool' && (m.toolExecution?.status === 'running' || m.toolExecution?.status === 'planned')
    );
    const waitingTool = [...messages].reverse().find(
        (m) => m.type === 'tool' && (m.toolExecution?.status === 'waiting_confirmation' || m.toolExecution?.status === 'pending_confirmation')
    );
    const runningToolCount = messages.filter(
        (m) => m.type === 'tool' && (m.toolExecution?.status === 'running' || m.toolExecution?.status === 'planned')
    ).length;
    const waitingToolCount = messages.filter(
        (m) => m.type === 'tool' && (m.toolExecution?.status === 'waiting_confirmation' || m.toolExecution?.status === 'pending_confirmation')
    ).length;
    const sessionLabel = activeChatId.slice(0, 8).toUpperCase();
    const waitingExecution = waitingTool?.toolExecution;
    const runningExecution = runningTool?.toolExecution;
    const runtimeModelLabel = llmRuntime?.active_model?.split('/').pop() || llmRuntime?.active_model || '';

    let railTitle = "Ready";
    let railTone: 'default' | 'good' | 'warn' = isConnected ? 'good' : 'default';

    if (!isConnected) {
        railTitle = "Gateway reconnecting";
        railTone = 'default';
    } else if (waitingExecution) {
        railTitle = waitingToolCount > 1 ? `${waitingToolCount} approvals needed` : "Approval required";
        railTone = 'warn';
    } else if (runningExecution) {
        railTitle = runningToolCount > 1 ? `Executing ${runningToolCount} tools` : "Executing tool";
        railTone = 'good';
    } else if (activityText) {
        railTitle = "Working";
        railTone = 'good';
    } else if (isTyping) {
        railTitle = "Drafting response";
        railTone = 'good';
    }

    const showComposerPrompts =
        !inputValue.trim() &&
        !selectedAttachment &&
        !isTyping &&
        messages.length < 4;

    const clearSelectedAttachment = () => {
        setSelectedAttachment(null);
        if (fileInputRef.current) {
            fileInputRef.current.value = '';
        }
    };

    const fileToAttachment = (file: File) =>
        new Promise<ChatAttachment>((resolve, reject) => {
            const displayName = file.name || (file.type.startsWith('image/') ? 'pasted-image.png' : 'attachment');
            if (file.size > MAX_ATTACHMENT_BYTES) {
                reject(new Error("Attachments are limited to 8 MB."));
                return;
            }

            const lowerName = displayName.toLowerCase();
            const mimeType = file.type || (
                lowerName.endsWith('.pdf')
                    ? 'application/pdf'
                    : lowerName.endsWith('.docx')
                        ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                        : lowerName.endsWith('.doc')
                            ? 'application/msword'
                            : 'application/octet-stream'
            );
            const isSupportedImage = mimeType.startsWith('image/');
            const isSupportedDocument =
                mimeType === 'application/pdf' ||
                WORD_DOCUMENT_MIME_TYPES.has(mimeType) ||
                /\.(pdf|doc|docx)$/i.test(file.name);

            if (!isSupportedImage && !isSupportedDocument) {
                reject(new Error("Only images, PDF, DOC, and DOCX files are supported."));
                return;
            }

            const reader = new FileReader();
            reader.onerror = () => reject(new Error("Failed to read the selected file."));
            reader.onloadend = () => {
                if (typeof reader.result !== 'string') {
                    reject(new Error("Failed to load the selected file."));
                    return;
                }
                resolve({
                    name: displayName,
                    mimeType,
                    kind: isSupportedImage ? 'image' : 'document',
                    url: reader.result,
                });
            };
            reader.readAsDataURL(file);
        });

    const handleKeyPress = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    const handleSend = () => {
        if (inputValue.trim() || selectedAttachment) {
            onSendMessage(null, selectedAttachment);
            clearSelectedAttachment();
            if (textareaRef.current) {
                textareaRef.current.style.height = 'auto';
            }
        }
    };

    const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        try {
            setSelectedAttachment(await fileToAttachment(file));
        } catch (error) {
            const description = error instanceof Error ? error.message : "Failed to attach file.";
            toast.error("Attachment rejected", { description });
            clearSelectedAttachment();
        }
    };

    const handlePaste = async (e: React.ClipboardEvent) => {
        const items = e.clipboardData.items;
        for (let i = 0; i < items.length; i++) {
            if (items[i].type.indexOf('image') !== -1) {
                const blob = items[i].getAsFile();
                if (blob) {
                    try {
                        setSelectedAttachment(await fileToAttachment(blob));
                    } catch (error) {
                        const description = error instanceof Error ? error.message : "Failed to attach pasted image.";
                        toast.error("Attachment rejected", { description });
                    }
                    e.preventDefault();
                    break;
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
        if (atBottom) {
            setIsAtBottom(true);
            clearUnread();
        } else if (isAtBottom) {
            setIsAtBottom(false);
        }
    };

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
            <header className="hidden md:flex h-14 items-center gap-3 px-6 border-b border-border bg-card z-10 transition-all duration-200">
                <div className="flex-1 min-w-0">
                    <h1 className="text-base font-bold text-foreground leading-tight">Chat</h1>
                </div>

                {/* Inline status chips */}
                <div className="flex items-center gap-2">
                    <StatusChip
                        label="Gateway"
                        value={isConnected ? "Live" : "Reconnecting"}
                        tone={isConnected ? 'good' : 'default'}
                    />
                    <StatusChip
                        label="Mode"
                        value={autonomousMode ? "Autonomous" : "Guarded"}
                        tone={autonomousMode ? 'warn' : 'default'}
                    />
                    {llmRuntime?.using_fallback && runtimeModelLabel && (
                        <StatusChip
                            label="AI"
                            value={`Fallback · ${runtimeModelLabel}`}
                            tone="warn"
                        />
                    )}
                    <StatusChip label="Session" value={sessionLabel} />
                    {waitingToolCount > 0 && (
                        <StatusChip label="Approvals" value={String(waitingToolCount)} tone="warn" />
                    )}
                    {runningToolCount > 0 && (
                        <StatusChip label="Tools" value={String(runningToolCount)} tone="good" />
                    )}
                </div>

                {/* Compact rail status pill */}
                <div
                    className={cn(
                        "flex items-center gap-2 rounded-full border px-3 py-1.5 text-[11px] transition-all",
                        railTone === 'good' && "border-primary/20 bg-primary/5 text-primary",
                        railTone === 'warn' && "border-amber-500/20 bg-amber-500/5 text-amber-500",
                        railTone === 'default' && "border-border bg-card/70 text-muted-foreground"
                    )}
                >
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center">
                        {!isConnected ? (
                            <WifiOff className="h-3.5 w-3.5" />
                        ) : waitingExecution ? (
                            <ShieldAlert className="h-3.5 w-3.5" />
                        ) : isTyping || runningExecution || activityText ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                            <Wifi className="h-3.5 w-3.5" />
                        )}
                    </span>
                    <span className="font-semibold">{railTitle}</span>
                    <span className="uppercase tracking-[0.14em] opacity-60">
                        {waitingExecution ? "review" : runningExecution || isTyping || activityText ? "live" : "idle"}
                    </span>
                </div>

                <div className="flex items-center gap-2">

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
                <ScrollArea ref={scrollAreaRef} className="h-full p-4 md:p-8" onScroll={handleScroll}>
                    <div className="mx-auto flex max-w-[48rem] flex-col gap-8 pb-10 font-sans">
                        {messages.length === 0 && (
                            <div className="flex flex-col items-center justify-center py-16 text-center">
                                <Avatar className="h-14 w-14 shadow-lg shadow-primary/20">
                                    <AvatarImage src={botIdentity?.avatar || undefined} className="object-cover" />
                                    <AvatarFallback className="bg-primary/10 text-primary">
                                        <Bot className="h-7 w-7" />
                                    </AvatarFallback>
                                </Avatar>
                                <div className="mt-4 space-y-1">
                                    <div className="text-[10px] font-bold uppercase tracking-[0.24em] text-primary/70">
                                        Session {sessionLabel}
                                    </div>
                                    <h3 className="text-lg font-semibold text-foreground">
                                        {botIdentity?.name ? `${botIdentity.name} is ready` : "LimeBot is ready"}
                                    </h3>
                                    <p className="text-xs text-muted-foreground">
                                        Ask anything, or pick a suggestion below.
                                    </p>
                                </div>
                            </div>
                        )}
                        {(() => {
                            const items: Array<
                                | { kind: 'message'; msg: Message; showAvatar: boolean; showHeader: boolean; key: string; absoluteIndex: number }
                                | { kind: 'tool_timeline'; executions: ToolExecution[]; key: string; absoluteIndex: number }
                            > = [];

                            for (let i = 0; i < messages.length; i++) {
                                const msg = messages[i];
                                const prevRaw = i > 0 ? messages[i - 1] : null;
                                const nextRaw = i + 1 < messages.length ? messages[i + 1] : null;

                                if (isSubagentOrchestrationThought(msg, prevRaw, nextRaw)) {
                                    continue;
                                }

                                if (msg.type === 'tool' && msg.toolExecution) {
                                    const start = i;
                                    const toolGroup: ToolExecutionWithTurn[] = [];
                                    while (i < messages.length && messages[i].type === 'tool' && messages[i].toolExecution) {
                                        toolGroup.push({
                                            ...(messages[i].toolExecution as ToolExecution),
                                            turnId: messages[i].turnId,
                                        });
                                        i++;
                                    }
                                    const nextMessage = i < messages.length ? messages[i] : null;
                                    if (shouldHideSpawnAgentGroup(toolGroup, nextMessage)) {
                                        i -= 1;
                                        continue;
                                    }
                                    const count = toolGroup.length;
                                    if (count >= 3) {
                                        items.push({
                                            kind: 'tool_timeline',
                                            executions: toolGroup,
                                            key: `${activeChatId}-tools-${start}`,
                                            absoluteIndex: start,
                                        });
                                    } else {
                                        for (let j = 0; j < toolGroup.length; j++) {
                                            const m = messages[start + j];
                                            items.push({
                                                kind: 'message',
                                                msg: m,
                                                showAvatar: j === 0,
                                                showHeader: j === 0,
                                                key: `${activeChatId}-${start + j}`,
                                                absoluteIndex: start + j,
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
                                    absoluteIndex: i,
                                });
                            }

                            return (
                                <>
                                    {items.map((item) => (
                                        <div key={item.key} className="contents">
                                            {item.absoluteIndex === unreadAnchorIndex && unreadCount > 0 && (
                                                <UnreadSeparator count={unreadCount} />
                                            )}
                                            {item.kind === 'tool_timeline' ? (
                                                <ToolTimeline
                                                    executions={item.executions}
                                                    botIdentity={botIdentity}
                                                    onConfirmSideChannel={handleToolConfirmSideChannel}
                                                />
                                            ) : (
                                                <MemoizedMessageItem
                                                    msg={item.msg}
                                                    botIdentity={botIdentity}
                                                    handleToolConfirmSideChannel={handleToolConfirmSideChannel}
                                                    onSendMessage={onSendMessage}
                                                    showAvatar={item.showAvatar}
                                                    showHeader={item.showHeader}
                                                />
                                            )}
                                        </div>
                                    ))}
                                </>
                            );
                        })()}
                    </div>

                    {/* ChatGPT-style typing indicator */}
                    {isTyping && (() => {
                        const last = messages[messages.length - 1];
                        const botAlreadyStreaming = last?.sender === 'bot' && last?.isStreaming;
                        return !botAlreadyStreaming ? (
                            <TypingIndicator botIdentity={botIdentity} />
                        ) : null;
                    })()}

                    <div ref={scrollRef} />
                </ScrollArea>

                {!isAtBottom && messages.length > 0 && (
                    <div className="pointer-events-none absolute bottom-5 right-5 z-20 flex flex-col items-end gap-2">
                        {unreadCount > 0 && (
                            <div className="pointer-events-auto rounded-full border border-primary/20 bg-background/95 px-3 py-1 text-[10px] font-bold uppercase tracking-widest text-primary shadow-lg backdrop-blur">
                                {unreadCount <= 1 ? "1 New Message" : `${unreadCount} New Messages`}
                            </div>
                        )}
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => scrollToLatest('smooth')}
                            className="pointer-events-auto h-9 rounded-full border-primary/20 bg-background/95 pl-3 pr-3 shadow-lg backdrop-blur hover:bg-background"
                        >
                            <ArrowDown className="mr-1.5 h-4 w-4" />
                            Jump to latest
                        </Button>
                    </div>
                )}
            </div>

            {/* Input Area */}
            <div className="p-4 bg-background border-t border-border relative z-20">
                <div className="mx-auto max-w-[48rem] relative group font-sans">
                    <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px]">
                        <StatusChip
                            label="Compose"
                            value="Enter sends"
                        />
                        <StatusChip
                            label="Line break"
                            value="Shift+Enter"
                        />
                        {waitingToolCount > 0 && (
                            <StatusChip label="Pending approval" value={String(waitingToolCount)} tone="warn" />
                        )}
                        {selectedAttachment && (
                            <StatusChip label="Attachment" value={selectedAttachment.name} tone="good" />
                        )}
                    </div>

                    {/* Attachment Preview */}
                    {selectedAttachment && (
                        <div className="absolute bottom-full left-0 mb-4 bg-background border border-border p-2 rounded-xl shadow-lg animate-in fade-in slide-in-from-bottom-2">
                            <div className="relative">
                                <div className="max-w-sm">
                                    <AttachmentPreview attachment={selectedAttachment} compact={selectedAttachment.kind !== 'image'} />
                                </div>
                                <button
                                    onClick={clearSelectedAttachment}
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
                            accept={ACCEPTED_ATTACHMENT_TYPES}
                            onChange={handleFileSelect}
                        />

                        {/* Stop Button (visible when typing or executing tools) */}
                        {(isTyping || messages.some(m => m.type === 'tool' && (m.toolExecution?.status === 'running' || m.toolExecution?.status === 'planned'))) && (
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
                            ref={textareaRef}
                            placeholder={
                                !isConnected
                                    ? "Waiting for the gateway to reconnect..."
                                    : waitingToolCount > 0
                                        ? "Approve or deny the waiting action below, or send a clarification..."
                                        : "Ask LimeBot to inspect, plan, code, or explain..."
                            }
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
                                disabled={!isConnected || isTyping || (!inputValue.trim() && !selectedAttachment)}
                                size="icon"
                                className={cn(
                                    "h-9 w-9 rounded-lg transition-all duration-200",
                                    (inputValue.trim() || selectedAttachment)
                                        ? "bg-primary hover:bg-primary/90 text-primary-foreground"
                                        : "bg-transparent text-muted-foreground hover:bg-muted-foreground/10"
                                )}
                            >
                                <Send className="h-4 w-4" />
                                <span className="sr-only">Send</span>
                            </Button>
                        </div>
                    </div>

                    {showComposerPrompts && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                            {QUICK_ACTIONS.map(({ label, prompt }) => (
                                <button
                                    key={label}
                                    type="button"
                                    onClick={() => onSendMessage(prompt)}
                                    className="rounded-full border border-border bg-background px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-primary/30 hover:bg-primary/5 hover:text-foreground"
                                >
                                    {label}
                                </button>
                            ))}
                        </div>
                    )}
                </div>
                <div className="mx-auto mt-2 flex max-w-[48rem] flex-wrap justify-between gap-2 px-1 text-[10px] font-medium text-muted-foreground">
                    <span>
                        {waitingToolCount > 0
                            ? "Pending approvals are handled directly in the tool timeline."
                            : "Secure channel with guarded tool execution."}
                    </span>
                    <span>LimeBot v1.0.8</span>
                </div>
            </div>
        </div>
    );
}
