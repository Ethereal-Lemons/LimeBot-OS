import { useEffect, useMemo, useState, type FormEvent } from "react";
import { MascotBubble } from "@/components/MascotBubble";
import { SettingsForm } from "@/components/SettingsForm";
import { Markdown } from "@/components/Markdown";
import { captureTabContext, ensureBackendAccess, openDashboard } from "@/lib/browser";
import { useCompanionIdentity } from "@/lib/identity";
import { useLimeBotClient } from "@/lib/limebotClient";
import {
  createNoticeAction,
  createPromptAction,
  createSelectedTextAction,
  createWatchVideoAction,
  isCurrentVideoRequest,
  MAX_SELECTION_CHARS,
  MAX_VISIBLE_TEXT_CHARS,
} from "@/lib/pageContext";
import { loadSettings, enqueuePendingAction, resetSessionId, saveSettings, type LimeBotExtensionSettings } from "@/lib/storage";

export function App() {
  const [settings, setSettings] = useState<LimeBotExtensionSettings | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsMessage, setSettingsMessage] = useState<string | null>(null);
  const [panelBusyAction, setPanelBusyAction] = useState<string | null>(null);

  useEffect(() => {
    void loadSettings().then(setSettings);
  }, []);

  const client = useLimeBotClient(
    settings ?? {
      apiBaseUrl: "http://localhost:8000",
      wsBaseUrl: "ws://localhost:8000",
      dashboardUrl: "http://localhost:5173",
      apiKey: "",
      sessionId: "extension-loading",
      showStatusBubble: true,
    }
  );
  const identity = useCompanionIdentity(
    settings?.apiBaseUrl || "http://localhost:8000",
    settings?.apiKey
  );

  const recentMessages = useMemo(() => client.messages.slice(-8), [client.messages]);

  async function handleSave(nextSettings: LimeBotExtensionSettings) {
    setSettingsMessage(null);
    setPanelBusyAction("save");
    try {
      const granted = await ensureBackendAccess(nextSettings, true);
      if (!granted) {
        setSettingsMessage(`Could not connect to ${identity.name}. Check your URLs.`);
        return;
      }

      const stored = await saveSettings(nextSettings);
      setSettings(stored);
      setSettingsMessage("Settings saved.");
      setShowSettings(false);
    } finally {
      setPanelBusyAction(null);
    }
  }

  async function handleResetSession() {
    setPanelBusyAction("reset-session");
    try {
      const stored = await resetSessionId();
      setSettings(stored);
      setSettingsMessage("Started a fresh companion session.");
    } finally {
      setPanelBusyAction(null);
    }
  }

  async function queueCapturedAction(kind: "ask" | "selection" | "video") {
    setPanelBusyAction(kind);
    try {
      const captured = await captureTabContext(MAX_SELECTION_CHARS, MAX_VISIBLE_TEXT_CHARS);
      if (!captured.ok) {
        await enqueuePendingAction(createNoticeAction("sidepanel", captured.reason, "warning"));
        return;
      }

      const action = kind === "selection"
        ? createSelectedTextAction("sidepanel", captured.page)
        : kind === "video"
          ? createWatchVideoAction("sidepanel", captured.page)
          : createPromptAction("sidepanel", captured.page);

      await enqueuePendingAction(action);
    } finally {
      setPanelBusyAction(null);
    }
  }

  async function handleManualSend(value: string) {
    if (!isCurrentVideoRequest(value)) {
      return client.sendPrompt(value);
    }

    setPanelBusyAction("video-prompt");
    try {
      const captured = await captureTabContext(MAX_SELECTION_CHARS, MAX_VISIBLE_TEXT_CHARS);
      if (!captured.ok) {
        await enqueuePendingAction(createNoticeAction("sidepanel", captured.reason, "warning"));
        return true;
      }
      await enqueuePendingAction(createWatchVideoAction("sidepanel", captured.page, value));
      return true;
    } finally {
      setPanelBusyAction(null);
    }
  }

  if (!settings) {
    return <div className="sidepanel-shell">Loading Companion...</div>;
  }

  return (
    <div className="sidepanel-shell">
      <header className="panel-header">
        <div className="header-copy">
          <p className="eyebrow">{identity.name} Companion</p>
          <h1>{identity.name} in your browser.</h1>
        </div>
        <div className="header-right">
          {settings.showStatusBubble ? (
            <MascotBubble
              status={client.status}
              avatarUrl={identity.avatar}
              botName={identity.name}
            />
          ) : null}
          <button
            className={`gear-button ${showSettings ? "active" : ""}`}
            type="button"
            onClick={() => {
              setSettingsMessage(null);
              setShowSettings((value) => !value);
            }}
            title={showSettings ? "Close settings" : "Open settings"}
          >
            <svg
              viewBox="0 0 24 24"
              width="18"
              height="18"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="gear-icon"
            >
              <circle cx="12" cy="12" r="3"></circle>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
            </svg>
          </button>
        </div>
      </header>

      <section className="status-strip">
        <div className={`status-pill status-${client.status}`}>{client.statusText}</div>
      </section>

      <section className="button-grid panel-actions">
        <button
          className="primary-button"
          type="button"
          onClick={() => {
            void queueCapturedAction("video");
          }}
          disabled={Boolean(panelBusyAction)}
        >
          Watch video
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={() => {
            void queueCapturedAction("ask");
          }}
          disabled={Boolean(panelBusyAction)}
        >
          Ask this page
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={() => {
            void queueCapturedAction("selection");
          }}
          disabled={Boolean(panelBusyAction)}
        >
          Send selected text
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={() => {
            void openDashboard(settings.dashboardUrl);
          }}
          disabled={Boolean(panelBusyAction)}
        >
          Open dashboard
        </button>
      </section>

      {showSettings ? (
        <section className="card">
          <SettingsForm
            settings={settings}
            disabled={Boolean(panelBusyAction)}
            statusMessage={settingsMessage}
            onSave={handleSave}
            onResetSession={handleResetSession}
            botName={identity.name}
          />
        </section>
      ) : null}

      {!settings.apiKey ? (
        <section className="card warning-card">
          <h2>Setup needed</h2>
          <p>
            Add your API key in settings first. Queued page actions will wait until
            the panel can connect.
          </p>
        </section>
      ) : null}

      {client.lastError ? (
        <section className="card warning-card">
          <h2>Attention</h2>
          <p>{client.lastError}</p>
        </section>
      ) : null}

      {client.pendingApprovals.length ? (
        <section className="card">
          <div className="section-heading">
            <h2>Approvals</h2>
            <span>{client.pendingApprovals.length} waiting</span>
          </div>
          <div className="approval-list">
            {client.pendingApprovals.map((execution) => (
              <article className="approval-card" key={execution.toolCallId}>
                <div className="approval-head">
                  <strong>{execution.tool}</strong>
                  <span>{execution.status.replace(/_/g, " ")}</span>
                </div>
                {execution.preview?.summary ? <p>{execution.preview.summary}</p> : null}
                {execution.result ? <p>{execution.result}</p> : null}
                <label className="toggle-row compact-toggle">
                  <input
                    type="checkbox"
                    id={`session-${execution.toolCallId}`}
                  />
                  <span>Always allow this session</span>
                </label>
                <div className="approval-actions">
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={!execution.confId || client.approvalInFlight === execution.confId}
                    onClick={() => {
                      const checkbox = document.getElementById(
                        `session-${execution.toolCallId}`
                      ) as HTMLInputElement | null;
                      if (!execution.confId) return;
                      void client.approveTool(
                        execution.confId,
                        false,
                        checkbox?.checked ?? false
                      );
                    }}
                  >
                    Deny
                  </button>
                  <button
                    className="primary-button"
                    type="button"
                    disabled={!execution.confId || client.approvalInFlight === execution.confId}
                    onClick={() => {
                      const checkbox = document.getElementById(
                        `session-${execution.toolCallId}`
                      ) as HTMLInputElement | null;
                      if (!execution.confId) return;
                      void client.approveTool(
                        execution.confId,
                        true,
                        checkbox?.checked ?? false
                      );
                    }}
                  >
                    Approve
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      <section className="recent-activity">
        <div className="section-heading">
          <h2>Recent activity</h2>
          <span className={`status-pill mini status-${client.isConnected ? "online" : "offline"}`}>
            {client.isConnected ? "Connected" : "Offline"}
          </span>
        </div>
        <div className="message-list">
          {recentMessages.length ? (
            recentMessages.map((message) => (
              <article
                className={`message-card role-${message.role} variant-${message.variant}`}
                key={message.id}
              >
                <div className="message-meta">
                  <strong>{message.role === "assistant" ? identity.name : message.role}</strong>
                  {message.isStreaming ? <span>streaming</span> : null}
                </div>
                <Markdown content={message.content} />
              </article>
            ))
          ) : (
            <p className="empty-state">
              Nothing here yet. Ask {identity.name} about the page, watch the current video, or send a text selection.
            </p>
          )}
        </div>
      </section>

      <section className="card composer-card">
        <ManualComposer
          disabled={!client.canSendPrompt}
          onSend={handleManualSend}
          botName={identity.name}
        />
      </section>
    </div>
  );
}

function ManualComposer({
  disabled,
  onSend,
  botName,
}: {
  disabled: boolean;
  onSend: (value: string) => Promise<boolean>;
  botName: string;
}) {
  const [value, setValue] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = value.trim();
    if (!content) return;
    const sent = await onSend(content);
    if (sent) {
      setValue("");
    }
  }

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <label className="field">
        <span>Message {botName}</span>
        <textarea
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={`Ask ${botName} anything...`}
          rows={4}
        />
      </label>
      <button className="primary-button" type="submit" disabled={disabled}>
        Send
      </button>
    </form>
  );
}
