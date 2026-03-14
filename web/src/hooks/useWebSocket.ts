/**
 * web/src/hooks/useWebSocket.ts
 * ─────────────────────────────
 * WebSocket connection, stream buffering, message dispatch, send & new-chat
 * handlers, all extracted from App.tsx.
 *
 * Exports:
 *  - useWebSocket(options) — returns all WS-related state and callbacks
 */

import { useState, useRef, useEffect } from 'react';
import { WS_BASE_URL } from '@/lib/api';
import {
    applyFinalAssistantMessage,
    applyStopTyping,
    type ChatAttachment,
    type ChatMessage,
    upsertStreamDelta,
} from '@/lib/chat-state';
import type { ToolExecution } from '@/components/chat/ToolCard';

type Message = ChatMessage & {
    toolExecution?: ToolExecution;
};

const normalizeIncomingAttachments = (value: unknown): ChatAttachment[] | undefined => {
    if (!Array.isArray(value)) return undefined;

    const attachments = value
        .map((item) => {
            if (!item || typeof item !== 'object') return null;
            const attachment = item as Record<string, unknown>;
            const name = typeof attachment.name === 'string' ? attachment.name : 'attachment';
            const mimeType =
                typeof attachment.mimeType === 'string'
                    ? attachment.mimeType
                    : typeof attachment.mime_type === 'string'
                        ? attachment.mime_type
                        : 'application/octet-stream';
            const kind =
                attachment.kind === 'image' || attachment.kind === 'document'
                    ? attachment.kind
                    : mimeType.startsWith('image/')
                        ? 'image'
                        : 'document';
            const url =
                typeof attachment.url === 'string'
                    ? attachment.url
                    : typeof attachment.data_url === 'string'
                        ? attachment.data_url
                        : '';

            if (!url) return null;
            return { name, mimeType, kind, url } satisfies ChatAttachment;
        })
        .filter((item): item is ChatAttachment => Boolean(item));

    return attachments.length > 0 ? attachments : undefined;
};

interface UseWebSocketOptions {
    /** Called when the server reports an identity update. */
    onIdentityUpdated: () => void;
    /** Called when a rate-limit event arrives. */
    onRateLimit: () => void;
    /** Called when a ghost-activity message arrives. */
    onActivity: (text: string) => void;
    /** Called when ghost activity should be cleared. */
    onActivityClear: () => void;
}

type SendMessageOptions = {
    echoUserMessage?: boolean;
};

const STREAM_FLUSH_INTERVAL_MS = 40;

export function useWebSocket({
    onIdentityUpdated,
    onRateLimit,
    onActivity,
    onActivityClear,
}: UseWebSocketOptions) {
    const [messages, setMessages] = useState<Message[]>([]);
    const [inputValue, setInputValue] = useState('');
    const [isConnected, setIsConnected] = useState(false);
    const [isTyping, setIsTyping] = useState(false);
    const [sessionId, setSessionId] = useState(() => crypto.randomUUID());

    const ws = useRef<WebSocket | null>(null);
    const reconnectTimerRef = useRef<number | null>(null);
    const mountedRef = useRef(true);
    const sessionIdRef = useRef(sessionId);
    const streamFlushTimerRef = useRef<number | null>(null);
    const streamBufferRef = useRef<{
        chatId: string | null;
        messageId: string | null;
        turnId: string | null;
        content: string;
        thinking: string;
    }>({
        chatId: null,
        messageId: null,
        turnId: null,
        content: '',
        thinking: '',
    });

    // Keep sessionIdRef in sync
    useEffect(() => {
        sessionIdRef.current = sessionId;
        clearStreamFlushTimer();
        streamBufferRef.current = { chatId: null, messageId: null, turnId: null, content: '', thinking: '' };
    }, [sessionId]);

    // ── Stream buffer helpers ─────────────────────────────────────────────

    const clearStreamFlushTimer = () => {
        if (streamFlushTimerRef.current !== null) {
            window.clearTimeout(streamFlushTimerRef.current);
            streamFlushTimerRef.current = null;
        }
    };

    const clearReconnectTimer = () => {
        if (reconnectTimerRef.current !== null) {
            window.clearTimeout(reconnectTimerRef.current);
            reconnectTimerRef.current = null;
        }
    };

    const flushStreamBuffer = () => {
        clearStreamFlushTimer();
        const pending = streamBufferRef.current;
        if (!pending.chatId) return;

        const { chatId, messageId, turnId, content, thinking } = pending;
        streamBufferRef.current = { chatId: null, messageId: null, turnId: null, content: '', thinking: '' };

        if (chatId !== sessionIdRef.current) return;
        if (!content && !thinking) return;

        setMessages(prev =>
            upsertStreamDelta(prev, {
                messageId,
                turnId,
                contentDelta: content,
                thinkingDelta: thinking,
            })
        );
    };

    const queueStreamDelta = (
        chatId: string | undefined,
        messageId: string | undefined,
        turnId: string | undefined,
        contentDelta = '',
        thinkingDelta = ''
    ) => {
        if (!chatId || chatId !== sessionIdRef.current) return;

        if (
            streamBufferRef.current.chatId &&
            (
                streamBufferRef.current.chatId !== chatId ||
                streamBufferRef.current.messageId !== (messageId || null)
            )
        ) {
            flushStreamBuffer();
        }

        if (!streamBufferRef.current.chatId) {
            streamBufferRef.current.chatId = chatId;
            streamBufferRef.current.messageId = messageId || null;
            streamBufferRef.current.turnId = turnId || null;
        }
        if (contentDelta) streamBufferRef.current.content += contentDelta;
        if (thinkingDelta) streamBufferRef.current.thinking += thinkingDelta;

        if (streamFlushTimerRef.current === null) {
            streamFlushTimerRef.current = window.setTimeout(() => {
                streamFlushTimerRef.current = null;
                flushStreamBuffer();
            }, STREAM_FLUSH_INTERVAL_MS);
        }
    };

    // ── WebSocket connect ─────────────────────────────────────────────────

    const connectWebSocket = () => {
        if (!mountedRef.current) return;
        if (
            ws.current?.readyState === WebSocket.OPEN ||
            ws.current?.readyState === WebSocket.CONNECTING
        ) {
            return;
        }

        const apiKey = localStorage.getItem('limebot_api_key');
        const wsUrl = `${WS_BASE_URL}/ws?api_key=${encodeURIComponent(apiKey || '')}`;

        clearReconnectTimer();

        const socket = new WebSocket(wsUrl);
        ws.current = socket;

        socket.onopen = () => {
            if (socket !== ws.current || !mountedRef.current) {
                socket.close();
                return;
            }
            console.log('Connected to LimeBot');
            setIsConnected(true);
        };

        socket.onerror = (event) => {
            if (socket !== ws.current || !mountedRef.current) {
                return;
            }
            console.error('WebSocket error:', event);
        };

        socket.onclose = () => {
            if (socket !== ws.current) {
                return;
            }
            ws.current = null;
            console.log('Disconnected from LimeBot');
            setIsConnected(false);
            clearStreamFlushTimer();
            streamBufferRef.current = { chatId: null, messageId: null, turnId: null, content: '', thinking: '' };
            if (!mountedRef.current) {
                return;
            }
            reconnectTimerRef.current = window.setTimeout(() => {
                reconnectTimerRef.current = null;
                console.log('Attempting to reconnect...');
                connectWebSocket();
            }, 3000);
        };

        socket.onmessage = (event) => {
            if (socket !== ws.current) {
                return;
            }
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'auth_ok') {
                    setIsConnected(true);
                    return;
                }
                const isMaintenanceEvent = data.type === 'maintenance' || data.metadata?.is_maintenance;
                const eventChatId: string | undefined = typeof data.chat_id === 'string' ? data.chat_id : undefined;
                const eventTurnId: string | undefined =
                    typeof data.turn_id === 'string'
                        ? data.turn_id
                        : typeof data.metadata?.turn_id === 'string'
                            ? data.metadata.turn_id
                            : undefined;
                const eventMessageId: string | undefined =
                    typeof data.message_id === 'string'
                        ? data.message_id
                        : typeof data.metadata?.message_id === 'string'
                            ? data.metadata.message_id
                            : undefined;
                const activeSessionId = sessionIdRef.current;
                const streamType = data.metadata?.type;

                if (isMaintenanceEvent) {
                    onActivity(data.content || '✨ Background task complete');
                    setTimeout(onActivityClear, 4000);
                    return;
                }

                if (eventChatId !== activeSessionId) return;

                if (streamType === 'chunk') {
                    queueStreamDelta(eventChatId, eventMessageId, eventTurnId, data.content || '', '');
                    return;
                }
                if (streamType === 'thinking') {
                    queueStreamDelta(eventChatId, eventMessageId, eventTurnId, '', data.content || '');
                    return;
                }

                flushStreamBuffer();

                if (data.type === 'message' || data.type === 'full_content') {
                    setIsTyping(false);
                    let variant: 'default' | 'destructive' | 'warning' = 'default';
                    if (data.metadata?.is_error) variant = 'destructive';
                    if (data.metadata?.is_warning) variant = 'warning';

                    setMessages(prev =>
                        applyFinalAssistantMessage(prev, {
                            messageId: eventMessageId,
                            turnId: eventTurnId,
                            content: data.content,
                            variant,
                            image: typeof data.metadata?.image === 'string' ? data.metadata.image : null,
                            attachments: normalizeIncomingAttachments(data.metadata?.attachments),
                        })
                    );

                    if (data.metadata?.identity_updated) {
                        onIdentityUpdated();
                    }
                } else if (data.type === 'cancellation' || data.metadata?.is_cancellation) {
                    setIsTyping(false);
                    setMessages(prev => prev.map(m => {
                        if (m.type === 'tool' && m.toolExecution?.status === 'running') {
                            return {
                                ...m,
                                toolExecution: { ...m.toolExecution, status: 'error', result: 'Cancelled by user.' },
                            };
                        }
                        return m;
                    }));
                } else if (data.type === 'stop_typing' || data.metadata?.type === 'stop_typing') {
                    setIsTyping(false);
                    setMessages(prev => applyStopTyping(prev, { messageId: eventMessageId, turnId: eventTurnId }));
                } else if (data.type === 'typing' || data.metadata?.type === 'typing') {
                    setIsTyping(true);
                } else if (data.type === 'rate_limit_error') {
                    console.error('Rate Limit Error:', data.metadata?.details);
                    onRateLimit();
                } else if (data.type === 'tool_execution') {
                    const toolData = data.metadata;
                    setMessages(prev => {
                        const existingIndex = prev.findIndex(m =>
                            m.type === 'tool' && m.toolExecution?.tool_call_id === toolData.tool_call_id
                        );
                        if (existingIndex !== -1) {
                            const newMessages = [...prev];
                            const existingExec = newMessages[existingIndex].toolExecution!;
                            let updatedLogs = existingExec.logs || [];
                            if (toolData.status === 'progress') {
                                updatedLogs = [...updatedLogs, data.content];
                            }
                            newMessages[existingIndex] = {
                                ...newMessages[existingIndex],
                                toolExecution: {
                                    ...existingExec,
                                    status: toolData.status === 'progress' ? existingExec.status : toolData.status,
                                    result: toolData.result,
                                    conf_id: toolData.conf_id || existingExec.conf_id,
                                    logs: updatedLogs,
                                    preview: toolData.preview || existingExec.preview,
                                },
                            };
                            return newMessages;
                        } else {
                            return [...prev, {
                                sender: 'bot',
                                type: 'tool',
                                content: '',
                                toolExecution: {
                                    tool: toolData.tool,
                                    status: toolData.status,
                                    args: toolData.args,
                                    tool_call_id: toolData.tool_call_id,
                                    conf_id: toolData.conf_id,
                                    logs: [],
                                    preview: toolData.preview,
                                },
                            }];
                        }
                    });
                } else if (data.metadata?.type === 'activity') {
                    console.log('👻 Activity:', data.metadata.text);
                    onActivity(data.metadata.text);
                    setTimeout(onActivityClear, 4000);
                }
            } catch (error) {
                console.error('Error parsing message:', error);
            }
        };
    };

    // ── Send / new chat ───────────────────────────────────────────────────

    const handleSendMessage = (
        contentOverride?: string | null,
        attachment?: ChatAttachment | null,
        options: SendMessageOptions = {}
    ) => {
        const finalContent = contentOverride || inputValue.trim();
        if ((!finalContent && !attachment) || !ws.current || ws.current.readyState !== WebSocket.OPEN || isTyping) return;
        const { echoUserMessage = true } = options;

        setIsTyping(true);
        const attachments = attachment
            ? [
                {
                    name: attachment.name,
                    mimeType: attachment.mimeType,
                    kind: attachment.kind,
                    data_url: attachment.url,
                },
            ]
            : [];
        const image = attachment?.kind === 'image' ? attachment.url : null;

        ws.current.send(JSON.stringify({ content: finalContent, image, attachments, chat_id: sessionId }));
        if (echoUserMessage) {
            setMessages(prev => [
                ...prev,
                {
                    sender: 'user',
                    content: finalContent,
                    image,
                    attachments: attachment ? [attachment] : undefined,
                },
            ]);
        }
        setInputValue('');
    };

    const handleNewChat = () => {
        flushStreamBuffer();
        clearStreamFlushTimer();
        streamBufferRef.current = { chatId: null, messageId: null, turnId: null, content: '', thinking: '' };
        const newId = crypto.randomUUID();
        setSessionId(newId);
        setMessages([]);
        setIsTyping(false);
        console.log('Started new session:', newId);
    };

    // ── Cleanup on unmount ────────────────────────────────────────────────

    useEffect(() => {
        mountedRef.current = true;

        return () => {
            mountedRef.current = false;
            clearReconnectTimer();
            clearStreamFlushTimer();
            streamBufferRef.current = { chatId: null, messageId: null, turnId: null, content: '', thinking: '' };
            ws.current?.close();
            ws.current = null;
        };
    }, []);

    return {
        messages,
        setMessages,
        inputValue,
        setInputValue,
        isConnected,
        isTyping,
        sessionId,
        connectWebSocket,
        handleSendMessage,
        handleNewChat,
        ws,
    };
}
