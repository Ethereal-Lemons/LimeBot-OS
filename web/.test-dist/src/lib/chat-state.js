const isBotTextMessage = (message) => message.sender === 'bot' && message.type !== 'tool' && !message.confirmation;
function findMessageIndex(messages, target, options) {
    const { messageId, turnId } = target;
    const streamingOnly = options?.streamingOnly ?? false;
    if (messageId) {
        const index = messages.findIndex((message) => isBotTextMessage(message) &&
            message.messageId === messageId &&
            (!streamingOnly || message.isStreaming));
        if (index !== -1)
            return index;
    }
    if (turnId) {
        for (let i = messages.length - 1; i >= 0; i -= 1) {
            const message = messages[i];
            if (!isBotTextMessage(message))
                continue;
            if (message.turnId !== turnId)
                continue;
            if (streamingOnly && !message.isStreaming)
                continue;
            return i;
        }
    }
    for (let i = messages.length - 1; i >= 0; i -= 1) {
        const message = messages[i];
        if (!isBotTextMessage(message))
            continue;
        if (!message.isStreaming)
            continue;
        return i;
    }
    return -1;
}
export function upsertStreamDelta(messages, delta) {
    const contentDelta = delta.contentDelta ?? '';
    const thinkingDelta = delta.thinkingDelta ?? '';
    if (!contentDelta && !thinkingDelta)
        return messages;
    const index = findMessageIndex(messages, delta, { streamingOnly: true });
    if (index === -1) {
        return [
            ...messages,
            {
                sender: 'bot',
                type: 'text',
                content: contentDelta,
                thinking: thinkingDelta || undefined,
                isStreaming: true,
                messageId: delta.messageId || undefined,
                turnId: delta.turnId || undefined,
            },
        ];
    }
    const existing = messages[index];
    const nextContent = `${existing.content}${contentDelta}`;
    const nextThinking = thinkingDelta
        ? `${existing.thinking || ''}${thinkingDelta}`
        : existing.thinking;
    if (nextContent === existing.content &&
        nextThinking === existing.thinking &&
        existing.isStreaming) {
        return messages;
    }
    const updated = [...messages];
    updated[index] = {
        ...existing,
        type: 'text',
        content: nextContent,
        thinking: nextThinking,
        isStreaming: true,
        messageId: delta.messageId || existing.messageId,
        turnId: delta.turnId || existing.turnId,
    };
    return updated;
}
export function applyFinalAssistantMessage(messages, payload) {
    const index = findMessageIndex(messages, payload);
    if (index === -1) {
        return [
            ...messages,
            {
                sender: 'bot',
                content: payload.content,
                variant: payload.variant,
                type: 'text',
                isStreaming: false,
                messageId: payload.messageId || undefined,
                turnId: payload.turnId || undefined,
            },
        ];
    }
    const updated = [...messages];
    updated[index] = {
        ...updated[index],
        content: payload.content,
        variant: payload.variant,
        type: 'text',
        isStreaming: false,
        messageId: payload.messageId || updated[index].messageId,
        turnId: payload.turnId || updated[index].turnId,
    };
    return updated;
}
export function applyStopTyping(messages, target) {
    const index = findMessageIndex(messages, target, { streamingOnly: true });
    if (index === -1)
        return messages;
    const updated = [...messages];
    updated[index] = {
        ...updated[index],
        isStreaming: false,
        messageId: target.messageId || updated[index].messageId,
        turnId: target.turnId || updated[index].turnId,
    };
    return updated;
}
