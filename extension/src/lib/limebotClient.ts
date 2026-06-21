import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createSystemMessage,
  createUserMessage,
  deriveCompanionStatus,
  finalizeAssistantMessage,
  statusLabel,
  stopAssistantStreaming,
  type CompanionStatus,
  type ConversationMessage,
  type ToolExecution,
  type ToolExecutionPayload,
  upsertAssistantChunk,
  upsertToolExecution,
} from "@/lib/protocol";
import {
  loadPendingActions,
  replacePendingActions,
  STORAGE_KEYS,
  type LimeBotExtensionSettings,
  type PendingAction,
} from "@/lib/storage";

type SendPromptOptions = {
  displayText?: string;
};

function buildWsUrl(baseUrl: string, apiKey: string) {
  return `${baseUrl.replace(/\/+$/, "")}/ws?api_key=${encodeURIComponent(apiKey)}`;
}

function isToolExecutionPayload(value: unknown): value is ToolExecutionPayload {
  if (!value || typeof value !== "object") {
    return false;
  }

  const payload = value as Record<string, unknown>;
  return (
    typeof payload.tool === "string" &&
    typeof payload.status === "string" &&
    typeof payload.tool_call_id === "string"
  );
}

export function useLimeBotClient(settings: LimeBotExtensionSettings) {
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [toolExecutions, setToolExecutions] = useState<ToolExecution[]>([]);
  const [queuedActions, setQueuedActions] = useState<PendingAction[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const [lastErrorAt, setLastErrorAt] = useState<number | null>(null);
  const [celebratingUntil, setCelebratingUntil] = useState<number | null>(null);
  const [approvalInFlight, setApprovalInFlight] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const sessionIdRef = useRef(settings.sessionId);
  const queueBusyRef = useRef(false);

  useEffect(() => {
    sessionIdRef.current = settings.sessionId;
    setMessages([]);
    setToolExecutions([]);
    setIsTyping(false);
    setCelebratingUntil(null);
    setLastError(null);
    setLastErrorAt(null);
  }, [settings.sessionId]);

  useEffect(() => {
    let mounted = true;

    loadPendingActions().then((actions) => {
      if (mounted) {
        setQueuedActions(actions);
      }
    });

    const handleStorageChange = (
      changes: Record<string, chrome.storage.StorageChange>,
      areaName: string
    ) => {
      if (areaName !== "local") return;
      const queueChange = changes[STORAGE_KEYS.pendingActions];
      if (!queueChange) return;
      const nextValue = Array.isArray(queueChange.newValue) ? queueChange.newValue : [];
      setQueuedActions(
        nextValue.filter((item): item is PendingAction => Boolean(item && typeof item === "object"))
      );
    };

    chrome.storage.onChanged.addListener(handleStorageChange);
    return () => {
      mounted = false;
      chrome.storage.onChanged.removeListener(handleStorageChange);
    };
  }, []);

  useEffect(() => {
    if (!settings.apiKey) {
      setIsConnected(false);
      wsRef.current?.close();
      wsRef.current = null;
      return;
    }

    let disposed = false;

    const cleanupReconnectTimer = () => {
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const connect = () => {
      cleanupReconnectTimer();
      const socket = new WebSocket(buildWsUrl(settings.wsBaseUrl, settings.apiKey));
      wsRef.current = socket;

      socket.onopen = () => {
        if (disposed || wsRef.current !== socket) return;
        setIsConnected(true);
        setLastError(null);
      };

      socket.onerror = () => {
        if (disposed || wsRef.current !== socket) return;
        setLastError("Connection failed.");
        setLastErrorAt(Date.now());
      };

      socket.onclose = () => {
        if (wsRef.current === socket) {
          wsRef.current = null;
        }
        if (disposed) return;
        setIsConnected(false);
        cleanupReconnectTimer();
        reconnectTimerRef.current = window.setTimeout(() => {
          reconnectTimerRef.current = null;
          connect();
        }, 2500);
      };

      socket.onmessage = (event) => {
        if (disposed || wsRef.current !== socket) return;

        try {
          const data = JSON.parse(event.data) as {
            type?: string;
            chat_id?: string;
            turn_id?: string;
            message_id?: string;
            content?: string;
            metadata?: Record<string, unknown>;
          };

          if (data.type === "auth_ok") {
            setIsConnected(true);
            return;
          }

          const chatId = typeof data.chat_id === "string" ? data.chat_id : undefined;
          if (chatId && chatId !== sessionIdRef.current) {
            return;
          }

          const target = {
            turnId: typeof data.turn_id === "string" ? data.turn_id : undefined,
            messageId: typeof data.message_id === "string" ? data.message_id : undefined,
          };

          const streamType = data.metadata?.type;

          if (streamType === "chunk") {
            setIsTyping(true);
            setMessages((previous) => upsertAssistantChunk(previous, target, data.content ?? ""));
            return;
          }

          if (streamType === "thinking") {
            setIsTyping(true);
            setMessages((previous) =>
              upsertAssistantChunk(previous, target, data.content ? `Thinking: ${data.content}` : "")
            );
            return;
          }

          if (data.type === "typing" || streamType === "typing") {
            setIsTyping(true);
            return;
          }

          if (data.type === "stop_typing" || streamType === "stop_typing") {
            setIsTyping(false);
            setMessages((previous) => stopAssistantStreaming(previous, target));
            return;
          }

          if (data.type === "tool_execution" && isToolExecutionPayload(data.metadata)) {
            const toolPayload = data.metadata;
            setToolExecutions((previous) => upsertToolExecution(previous, toolPayload));
            if (toolPayload.status === "error") {
              setLastError(toolPayload.result || `${toolPayload.tool} failed.`);
              setLastErrorAt(Date.now());
            }
            return;
          }

          if (data.type === "message" || data.type === "full_content") {
            const variant =
              data.metadata?.is_error
                ? "destructive"
                : data.metadata?.is_warning
                  ? "warning"
                  : "default";

            setMessages((previous) =>
              finalizeAssistantMessage(previous, target, data.content ?? "", variant)
            );
            setIsTyping(false);

            if (variant !== "default") {
              setLastError(data.content ?? "An error occurred.");
              setLastErrorAt(Date.now());
            } else {
              setCelebratingUntil(Date.now() + 2000);
            }
            return;
          }

          if (data.type === "rate_limit_error") {
            setLastError("Rate limit reached.");
            setLastErrorAt(Date.now());
          }
        } catch (error) {
          console.error("Failed to parse socket message", error);
        }
      };
    };

    connect();

    return () => {
      disposed = true;
      cleanupReconnectTimer();
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [settings.apiKey, settings.wsBaseUrl]);

  const canSendPrompt =
    Boolean(settings.apiKey) &&
    Boolean(wsRef.current && wsRef.current.readyState === WebSocket.OPEN) &&
    !isTyping;

  const sendPrompt = useCallback(async (content: string, options?: SendPromptOptions) => {
    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return false;
    }

    socket.send(
      JSON.stringify({
        content,
        chat_id: settings.sessionId,
        sender_id: "web-user",
      })
    );

    setMessages((previous) => [
      ...previous,
      createUserMessage(options?.displayText || content),
    ]);
    setIsTyping(true);
    return true;
  }, [settings.sessionId]);

  useEffect(() => {
    if (!queuedActions.length || queueBusyRef.current) {
      return;
    }

    const [nextAction, ...rest] = queuedActions;
    if (nextAction.kind === "notice") {
      queueBusyRef.current = true;
      setMessages((previous) => [
        ...previous,
        createSystemMessage(
          nextAction.message,
          nextAction.level === "warning" ? "warning" : "default"
        ),
      ]);
      void replacePendingActions(rest).finally(() => {
        queueBusyRef.current = false;
      });
      return;
    }

    if (!canSendPrompt) {
      return;
    }

    queueBusyRef.current = true;
    void sendPrompt(nextAction.prompt, { displayText: nextAction.displayText })
      .then((sent) => {
        if (!sent) return;
        return replacePendingActions(rest);
      })
      .finally(() => {
        queueBusyRef.current = false;
      });
  }, [canSendPrompt, queuedActions, sendPrompt]);

  async function approveTool(confId: string, approved: boolean, sessionWhitelist: boolean) {
    setApprovalInFlight(confId);
    try {
      const response = await fetch(
        `${settings.apiBaseUrl}/api/app/approvals/${encodeURIComponent(confId)}`,
        {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": settings.apiKey,
        },
        body: JSON.stringify({
          approved,
          session_whitelist: sessionWhitelist,
          source: "extension",
        }),
        }
      );

      if (!response.ok) {
        throw new Error(`Approval request failed with status ${response.status}`);
      }
    } catch (error) {
      console.error("Approval request failed", error);
      setLastError("The approval request could not be sent.");
      setLastErrorAt(Date.now());
    } finally {
      setApprovalInFlight(null);
    }
  }

  const status = useMemo<CompanionStatus>(
    () =>
      deriveCompanionStatus({
        isConnected,
        isTyping,
        toolExecutions,
        celebratingUntil,
        lastErrorAt,
      }),
    [celebratingUntil, isConnected, isTyping, lastErrorAt, toolExecutions]
  );

  const statusText = statusLabel(status);
  const pendingApprovals = toolExecutions.filter(
    (execution) =>
      execution.status === "pending_confirmation" || execution.status === "waiting_confirmation"
  );

  return {
    messages,
    toolExecutions,
    pendingApprovals,
    isConnected,
    isTyping,
    canSendPrompt,
    status,
    statusText,
    lastError,
    approvalInFlight,
    sendPrompt,
    approveTool,
  };
}
