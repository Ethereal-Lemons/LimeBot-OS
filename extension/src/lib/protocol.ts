export type MessageVariant = "default" | "warning" | "destructive";
export type CompanionStatus =
  | "offline"
  | "idle"
  | "thinking"
  | "working"
  | "approval"
  | "warning"
  | "celebrating";

export type ToolExecutionStatus =
  | "planned"
  | "running"
  | "completed"
  | "error"
  | "pending_confirmation"
  | "progress"
  | "waiting_confirmation";

export type ToolPreview = {
  kind?: string;
  summary?: string;
  command?: string;
  cwd?: string;
  affected_paths?: string[];
  args_preview?: string;
};

export type ToolExecution = {
  tool: string;
  status: ToolExecutionStatus;
  args: unknown;
  toolCallId: string;
  confId?: string;
  policyProfile?: string;
  decisionReason?: string;
  result?: string;
  preview?: ToolPreview;
  updatedAt: number;
};

export type ConversationMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  variant: MessageVariant;
  isStreaming?: boolean;
  turnId?: string;
  messageId?: string;
  createdAt: number;
};

type MessageTarget = {
  messageId?: string | null;
  turnId?: string | null;
};

export type ToolExecutionPayload = {
  tool: string;
  status: ToolExecutionStatus;
  args: unknown;
  tool_call_id: string;
  conf_id?: string;
  policy_profile?: string;
  decision_reason?: string;
  result?: string;
  preview?: ToolPreview;
};

export type AppWorkspaceSummary = {
  workspace_id: string;
  title: string;
  origin: string;
  status: string;
  session_key: string;
  chat_id: string;
  updated_at: number;
};

export type AppServerState = {
  version: string;
  boot_id: string;
  timestamp: number;
  workspaces: AppWorkspaceSummary[];
  tasks: Array<Record<string, unknown>>;
  pending_approvals: Array<Record<string, unknown>>;
  channels: Array<{ name: string; running: boolean }>;
  runtime: {
    model: string;
    readiness: Record<string, unknown>;
    inbound_queue: number;
    outbound_queue: number;
    app_clients: number;
  };
};

export type WorkspaceEvent = {
  type: "workspace_event";
  workspace_id: string;
  session_key: string;
  event: string;
  payload: Record<string, unknown>;
  timestamp: number;
};

function createId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function isAssistantMessage(message: ConversationMessage) {
  return message.role === "assistant";
}

function findAssistantMessageIndex(messages: ConversationMessage[], target: MessageTarget, streamingOnly = false) {
  if (target.messageId) {
    const index = messages.findIndex(
      (message) =>
        isAssistantMessage(message) &&
        message.messageId === target.messageId &&
        (!streamingOnly || message.isStreaming)
    );
    if (index !== -1) {
      return index;
    }
  }

  if (target.turnId) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (!isAssistantMessage(message)) continue;
      if (message.turnId !== target.turnId) continue;
      if (streamingOnly && !message.isStreaming) continue;
      return index;
    }
  }

  if (streamingOnly) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (isAssistantMessage(message) && message.isStreaming) {
        return index;
      }
    }
  }

  return -1;
}

export function createUserMessage(content: string): ConversationMessage {
  return {
    id: createId("user"),
    role: "user",
    content,
    variant: "default",
    createdAt: Date.now(),
  };
}

export function createSystemMessage(content: string, variant: MessageVariant = "default"): ConversationMessage {
  return {
    id: createId("system"),
    role: "system",
    content,
    variant,
    createdAt: Date.now(),
  };
}

export function upsertAssistantChunk(
  messages: ConversationMessage[],
  target: MessageTarget,
  contentDelta: string
) {
  if (!contentDelta) return messages;

  const index = findAssistantMessageIndex(messages, target, true);
  if (index === -1) {
    const nextMessage: ConversationMessage = {
      id: createId("assistant"),
      role: "assistant",
      content: contentDelta,
      variant: "default",
      isStreaming: true,
      messageId: target.messageId ?? undefined,
      turnId: target.turnId ?? undefined,
      createdAt: Date.now(),
    };
    return [
      ...messages,
      nextMessage,
    ];
  }

  const updated = [...messages];
  updated[index] = {
    ...updated[index],
    content: `${updated[index].content}${contentDelta}`,
    isStreaming: true,
  };
  return updated;
}

export function finalizeAssistantMessage(
  messages: ConversationMessage[],
  target: MessageTarget,
  content: string,
  variant: MessageVariant
) {
  const index = findAssistantMessageIndex(messages, target);
  if (index === -1) {
    const nextMessage: ConversationMessage = {
      id: createId("assistant"),
      role: "assistant",
      content,
      variant,
      isStreaming: false,
      messageId: target.messageId ?? undefined,
      turnId: target.turnId ?? undefined,
      createdAt: Date.now(),
    };
    return [
      ...messages,
      nextMessage,
    ];
  }

  const updated = [...messages];
  updated[index] = {
    ...updated[index],
    content,
    variant,
    isStreaming: false,
    messageId: target.messageId ?? updated[index].messageId,
    turnId: target.turnId ?? updated[index].turnId,
  };
  return updated;
}

export function stopAssistantStreaming(messages: ConversationMessage[], target: MessageTarget) {
  const index = findAssistantMessageIndex(messages, target, true);
  if (index === -1) return messages;
  const updated = [...messages];
  updated[index] = {
    ...updated[index],
    isStreaming: false,
  };
  return updated;
}

export function upsertToolExecution(executions: ToolExecution[], payload: ToolExecutionPayload) {
  const nextItem: ToolExecution = {
    tool: payload.tool,
    status: payload.status,
    args: payload.args,
    toolCallId: payload.tool_call_id,
    confId: payload.conf_id,
    policyProfile: payload.policy_profile,
    decisionReason: payload.decision_reason,
    result: payload.result,
    preview: payload.preview,
    updatedAt: Date.now(),
  };

  const index = executions.findIndex((execution) => execution.toolCallId === payload.tool_call_id);
  if (index === -1) {
    return [nextItem, ...executions].slice(0, 12);
  }

  const updated = [...executions];
  updated[index] = {
    ...updated[index],
    ...nextItem,
  };
  return updated;
}

export function deriveCompanionStatus(input: {
  isConnected: boolean;
  isTyping: boolean;
  toolExecutions: ToolExecution[];
  celebratingUntil: number | null;
  lastErrorAt: number | null;
}) {
  if (!input.isConnected) return "offline" satisfies CompanionStatus;

  const now = Date.now();
  if (input.lastErrorAt && now - input.lastErrorAt < 6000) {
    return "warning" satisfies CompanionStatus;
  }

  if (input.celebratingUntil && input.celebratingUntil > now) {
    return "celebrating" satisfies CompanionStatus;
  }

  if (
    input.toolExecutions.some(
      (execution) =>
        execution.status === "pending_confirmation" || execution.status === "waiting_confirmation"
    )
  ) {
    return "approval" satisfies CompanionStatus;
  }

  if (input.isTyping) {
    return "thinking" satisfies CompanionStatus;
  }

  if (
    input.toolExecutions.some(
      (execution) =>
        execution.status === "running" ||
        execution.status === "planned" ||
        execution.status === "progress"
    )
  ) {
    return "working" satisfies CompanionStatus;
  }

  return "idle" satisfies CompanionStatus;
}

export function statusLabel(status: CompanionStatus) {
  switch (status) {
    case "offline":
      return "Offline";
    case "thinking":
      return "Thinking";
    case "working":
      return "Working";
    case "approval":
      return "Approval needed";
    case "warning":
      return "Needs attention";
    case "celebrating":
      return "Done";
    case "idle":
    default:
      return "Ready";
  }
}
