export type ChatToolExecution = {
  tool: string;
  status:
    | 'planned'
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
  preview?: {
    kind: string;
    summary?: string;
    path?: string;
    mode?: string;
    content_preview?: string;
    diff?: string;
    diff_error?: string;
    command?: string;
    cwd?: string;
    risk_flags?: string[];
    affected_paths?: string[];
    target_type?: string;
    args_preview?: string;
  };
};

export type ChatConfirmation = {
  id: string;
  action: string;
  description: string;
  details?: string;
  status: 'pending' | 'approved' | 'denied';
};

export type ChatAttachment = {
  name: string;
  mimeType: string;
  kind: 'image' | 'document';
  url: string;
};

export type ChatMessage = {
  sender: 'user' | 'bot';
  type?: 'text' | 'tool' | 'confirmation';
  content: string;
  thinking?: string;
  isStreaming?: boolean;
  image?: string | null;
  attachments?: ChatAttachment[];
  toolExecution?: ChatToolExecution;
  confirmation?: ChatConfirmation;
  variant?: 'default' | 'destructive' | 'warning';
  messageId?: string;
  turnId?: string;
  voiceUrl?: string;
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
  image?: string | null;
  attachments?: ChatAttachment[];
  voiceUrl?: string;
};

type ToolUpdate = MessageTarget & {
  content?: string;
  toolExecution: ChatToolExecution;
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
    content: payload.content,
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

function findFinalMessageIndexForTurn(
  messages: ChatMessage[],
  turnId?: string | null
): number {
  if (!turnId) return -1;
  return messages.findIndex(
    (message) =>
      isBotTextMessage(message) &&
      message.turnId === turnId &&
      !message.isStreaming
  );
}

function moveToolBeforeFinalMessage(
  messages: ChatMessage[],
  toolIndex: number,
  turnId?: string | null
): ChatMessage[] {
  const finalIndex = findFinalMessageIndexForTurn(messages, turnId);
  if (finalIndex === -1 || toolIndex < finalIndex) return messages;

  const updated = [...messages];
  const [toolMessage] = updated.splice(toolIndex, 1);
  const insertionIndex = toolIndex < finalIndex ? finalIndex - 1 : finalIndex;
  updated.splice(insertionIndex, 0, toolMessage);
  return updated;
}

export function upsertToolExecution(
  messages: ChatMessage[],
  update: ToolUpdate
): ChatMessage[] {
  const execution = update.toolExecution;
  const existingIndex = messages.findIndex(
    (message) =>
      message.type === 'tool' &&
      message.toolExecution?.tool_call_id === execution.tool_call_id
  );

  if (existingIndex !== -1) {
    const existingMessage = messages[existingIndex];
    const existingExec = existingMessage.toolExecution!;
    const logs =
      execution.status === 'progress'
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
        status:
          execution.status === 'progress'
            ? existingExec.status
            : execution.status,
        result: execution.result,
        conf_id: execution.conf_id || existingExec.conf_id,
        logs,
        preview: execution.preview || existingExec.preview,
      },
    };
    return moveToolBeforeFinalMessage(
      updated,
      existingIndex,
      update.turnId || existingMessage.turnId
    );
  }

  const newMessage: ChatMessage = {
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
  if (finalIndex === -1) return [...messages, newMessage];

  const updated = [...messages];
  updated.splice(finalIndex, 0, newMessage);
  return updated;
}
