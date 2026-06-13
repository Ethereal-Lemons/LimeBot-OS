import { useEffect, useState, type FormEvent } from "react";
import type { LimeBotExtensionSettings } from "@/lib/storage";

type SettingsFormProps = {
  settings: LimeBotExtensionSettings;
  disabled?: boolean;
  statusMessage?: string | null;
  onSave: (settings: LimeBotExtensionSettings) => Promise<void> | void;
  onResetSession: () => Promise<void> | void;
  botName?: string;
};

export function SettingsForm({
  settings,
  disabled = false,
  statusMessage,
  onSave,
  onResetSession,
  botName = "LimeBot",
}: SettingsFormProps) {
  const [draft, setDraft] = useState(settings);

  useEffect(() => {
    setDraft(settings);
  }, [settings]);

  const updateField = <K extends keyof LimeBotExtensionSettings>(
    key: K,
    value: LimeBotExtensionSettings[K]
  ) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onSave(draft);
  }

  return (
    <form className="settings-form" onSubmit={handleSubmit}>
      <label className="field">
        <span>API Key</span>
        <textarea
          value={draft.apiKey}
          onChange={(event) => updateField("apiKey", event.target.value)}
          placeholder={`Paste APP_API_KEY from ${botName}`}
          rows={2}
          disabled={disabled}
        />
      </label>

      <label className="toggle-row">
        <input
          checked={draft.showStatusBubble}
          onChange={(event) => updateField("showStatusBubble", event.target.checked)}
          type="checkbox"
          disabled={disabled}
        />
        <span>Show companion avatar bubble</span>
      </label>

      <details className="advanced-settings-details">
        <summary className="advanced-settings-summary">
          <span>Connection Settings (Advanced)</span>
          <svg className="chevron-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
        </summary>
        
        <div className="advanced-settings-content">
          <label className="field">
            <span>Dashboard URL</span>
            <input
              value={draft.dashboardUrl}
              onChange={(event) => updateField("dashboardUrl", event.target.value)}
              placeholder="http://localhost:5173"
              disabled={disabled}
            />
          </label>

          <label className="field">
            <span>API Base URL</span>
            <input
              value={draft.apiBaseUrl}
              onChange={(event) => updateField("apiBaseUrl", event.target.value)}
              placeholder="http://localhost:8000"
              disabled={disabled}
            />
          </label>

          <label className="field">
            <span>WebSocket URL</span>
            <input
              value={draft.wsBaseUrl}
              onChange={(event) => updateField("wsBaseUrl", event.target.value)}
              placeholder="ws://localhost:8000"
              disabled={disabled}
            />
          </label>

          <div className="session-row">
            <div>
              <span className="session-label">Session ID</span>
              <code className="session-id">{draft.sessionId}</code>
            </div>
            <button
              className="ghost-button"
              type="button"
              onClick={() => {
                void onResetSession();
              }}
              disabled={disabled}
            >
              Reset session
            </button>
          </div>
        </div>
      </details>

      {statusMessage ? <p className="settings-note">{statusMessage}</p> : null}

      <button className="primary-button" type="submit" disabled={disabled}>
        Save Settings
      </button>
    </form>
  );
}
