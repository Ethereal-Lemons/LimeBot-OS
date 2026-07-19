const isBotTextMessage = (message) => message.sender === 'bot' && message.type !== 'tool' && !message.confirmation;
const isUserTextMessage = (message) => message.sender === 'user' && message.type !== 'tool' && !message.confirmation;
function dedupeRepeatedSections(content) {
    if (!content)
        return content;
    const trimmed = content.trim();
    const length = trimmed.length;
    if (length > 80 && length % 2 === 0) {
        const half = length / 2;
        if (trimmed.slice(0, half) === trimmed.slice(half)) {
            return trimmed.slice(0, half);
        }
    }
    if (length <= 80)
        return trimmed;
    const paragraphs = trimmed
        .split(/\n\s*\n/)
        .map((paragraph) => paragraph.trim())
        .filter(Boolean);
    const count = paragraphs.length;
    if (count >= 4 && count % 2 === 0) {
        const half = count / 2;
        const firstHalf = paragraphs.slice(0, half);
        const secondHalf = paragraphs.slice(half);
        if (firstHalf.join('\n\n') === secondHalf.join('\n\n')) {
            return firstHalf.join('\n\n');
        }
    }
    if (count >= 3) {
        const half = Math.floor(count / 2);
        if (half >= 2) {
            const firstHalf = paragraphs.slice(0, half);
            const trailingHalf = paragraphs.slice(count - half);
            if (firstHalf.join('\n\n') === trailingHalf.join('\n\n')) {
                return paragraphs.slice(0, count - half).join('\n\n');
            }
        }
    }
    return trimmed;
}
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
    const content = dedupeRepeatedSections(payload.content);
    const index = findMessageIndex(messages, payload);
    if (index === -1) {
        return [
            ...messages,
            {
                sender: 'bot',
                content,
                variant: payload.variant,
                type: 'text',
                isStreaming: false,
                image: payload.image ?? null,
                attachments: payload.attachments,
                voiceUrl: payload.voiceUrl,
                messageId: payload.messageId || undefined,
                turnId: payload.turnId || undefined,
            },
        ];
    }
    const updated = [...messages];
    updated[index] = {
        ...updated[index],
        content,
        variant: payload.variant,
        type: 'text',
        isStreaming: false,
        image: payload.image ?? updated[index].image ?? null,
        attachments: payload.attachments ?? updated[index].attachments,
        voiceUrl: payload.voiceUrl ?? updated[index].voiceUrl,
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
export function getUserTurnIndex(messages, targetMessageId) {
    let userTurnIndex = 0;
    for (const message of messages) {
        if (!isUserTextMessage(message))
            continue;
        if (message.messageId === targetMessageId) {
            return userTurnIndex;
        }
        userTurnIndex += 1;
    }
    return -1;
}
export function applyUserMessageEdit(messages, targetMessageId, nextContent, nextMessageId) {
    const targetIndex = messages.findIndex((message) => isUserTextMessage(message) && message.messageId === targetMessageId);
    if (targetIndex === -1)
        return messages;
    const updated = messages.slice(0, targetIndex + 1);
    const existing = updated[targetIndex];
    updated[targetIndex] = {
        ...existing,
        content: nextContent,
        messageId: nextMessageId,
    };
    return updated;
}
function findFinalMessageIndexForTurn(messages, turnId) {
    if (!turnId)
        return -1;
    return messages.findIndex((message) => isBotTextMessage(message) &&
        message.turnId === turnId &&
        !message.isStreaming);
}
function moveToolBeforeFinalMessage(messages, toolIndex, turnId) {
    const finalIndex = findFinalMessageIndexForTurn(messages, turnId);
    if (finalIndex === -1 || toolIndex < finalIndex)
        return messages;
    const updated = [...messages];
    const [toolMessage] = updated.splice(toolIndex, 1);
    const insertionIndex = toolIndex < finalIndex ? finalIndex - 1 : finalIndex;
    updated.splice(insertionIndex, 0, toolMessage);
    return updated;
}
export function upsertToolExecution(messages, update) {
    const execution = update.toolExecution;
    const existingIndex = messages.findIndex((message) => message.type === 'tool' &&
        message.toolExecution?.tool_call_id === execution.tool_call_id);
    if (existingIndex !== -1) {
        const existingMessage = messages[existingIndex];
        const existingExec = existingMessage.toolExecution;
        const logs = execution.status === 'progress'
            ? [...(existingExec.logs || []), update.content || '']
            : existingExec.logs || [];
        const updated = [...messages];
        updated[existingIndex] = {
            ...existingMessage,
            messageId: update.messageId || existingMessage.messageId,
            turnId: update.turnId || existingMessage.turnId,
            toolExecution: {
                ...existingExec,
                ...execution,
                status: execution.status === 'progress'
                    ? existingExec.status
                    : execution.status,
                result: execution.result,
                conf_id: execution.conf_id || existingExec.conf_id,
                logs,
                preview: execution.preview || existingExec.preview,
            },
        };
        return moveToolBeforeFinalMessage(updated, existingIndex, update.turnId || existingMessage.turnId);
    }
    const newMessage = {
        sender: 'bot',
        type: 'tool',
        content: '',
        messageId: update.messageId || undefined,
        turnId: update.turnId || undefined,
        toolExecution: {
            ...execution,
            logs: execution.logs || [],
        },
    };
    const finalIndex = findFinalMessageIndexForTurn(messages, update.turnId);
    if (finalIndex === -1)
        return [...messages, newMessage];
    const updated = [...messages];
    updated.splice(finalIndex, 0, newMessage);
    return updated;
}
const changeSetKey = (changeSet, target) => changeSet.id || target.turnId || `${changeSet.status}:${changeSet.summary}`;
export function upsertChangeSet(messages, update) {
    const key = changeSetKey(update.changeSet, update);
    const index = messages.findIndex((message) => message.type === 'changeset' &&
        message.changeSet &&
        changeSetKey(message.changeSet, message) === key);
    if (index === -1) {
        return [
            ...messages,
            {
                sender: 'bot',
                type: 'changeset',
                content: '',
                messageId: update.messageId || undefined,
                turnId: update.turnId || undefined,
                changeSet: update.changeSet,
            },
        ];
    }
    const updated = [...messages];
    const prior = updated[index].changeSet;
    const terminalStatuses = new Set(['verified', 'failed', 'blocked']);
    const preserveTerminal = terminalStatuses.has(prior.status) && !terminalStatuses.has(update.changeSet.status);
    updated[index] = {
        ...updated[index],
        messageId: update.messageId || updated[index].messageId,
        turnId: update.turnId || updated[index].turnId,
        changeSet: {
            ...prior,
            ...update.changeSet,
            status: preserveTerminal ? prior.status : update.changeSet.status,
            verification: update.changeSet.verification || updated[index].changeSet?.verification,
        },
    };
    return updated;
}
