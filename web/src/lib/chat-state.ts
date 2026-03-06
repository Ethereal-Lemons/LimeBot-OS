export type ChatToolExecution = {
  tool: string;
  status:
    | 'running'
    | 'completed'
    | 'error'
    | 'pending_confirmation'
    | 'progress'
    | 'waiting_confirmation';
  args: any;
  result?: string;
  tool_call_id: string;
  conf_id?: string;
  logs?: string[];
};

export type ChatConfirmation = {
  id: string;
  action: string;
  description: string;
  details?: string;
  status: 'pending' | 'approved' | 'denied';
};

export type ChatMessage = {
  sender: 'user' | 'bot';
  type?: 'text' | 'tool' | 'confirmation';
  content: string;
  thinking?: string;
  isStreaming?: boolean;
  image?: string | null;
  toolExecution?: ChatToolExecution;
  confirmation?: ChatConfirmation;
  variant?: 'default' | 'destructive' | 'warning';
  messageId?: string;
  turnId?: string;
};

type MessageTarget = {
  messageId?: string | null;
  turnId?: string | null;
};

type StreamDelta = MessageTarget & {
  contentDelta?: string;
  thinkingDelta?: string;
};

type FinalText = MessageTarget & {
  content: string;
  variant: 'default' | 'destructive' | 'warning';
};

const isBotTextMessage = (message: ChatMessage) =>
  message.sender === 'bot' && message.type !== 'tool' && !message.confirmation;

function findMessageIndex(
  messages: ChatMessage[],
  target: MessageTarget,
  options?: { streamingOnly?: boolean }
): number {
  const { messageId, turnId } = target;
  const streamingOnly = options?.streamingOnly ?? false;

  if (messageId) {
    const index = messages.findIndex(
      (message) =>
        isBotTextMessage(message) &&
        message.messageId === messageId &&
        (!streamingOnly || message.isStreaming)
    );
    if (index !== -1) return index;
  }

  if (turnId) {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const message = messages[i];
      if (!isBotTextMessage(message)) continue;
      if (message.turnId !== turnId) continue;
      if (streamingOnly && !message.isStreaming) continue;
      return i;
    }
  }

  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!isBotTextMessage(message)) continue;
    if (!message.isStreaming) continue;
    return i;
  }

  return -1;
}

export function upsertStreamDelta(
  messages: ChatMessage[],
  delta: StreamDelta
): ChatMessage[] {
  const contentDelta = delta.contentDelta ?? '';
  const thinkingDelta = delta.thinkingDelta ?? '';
  if (!contentDelta && !thinkingDelta) return messages;

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

  if (
    nextContent === existing.content &&
    nextThinking === existing.thinking &&
    existing.isStreaming
  ) {
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

export function applyFinalAssistantMessage(
  messages: ChatMessage[],
  payload: FinalText
): ChatMessage[] {
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

export function applyStopTyping(
  messages: ChatMessage[],
  target: MessageTarget
): ChatMessage[] {
  const index = findMessageIndex(messages, target, { streamingOnly: true });
  if (index === -1) return messages;

  const updated = [...messages];
  updated[index] = {
    ...updated[index],
    isStreaming: false,
    messageId: target.messageId || updated[index].messageId,
    turnId: target.turnId || updated[index].turnId,
  };
  return updated;
}
