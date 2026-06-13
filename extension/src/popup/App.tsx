import { useEffect, useState } from "react";
import { SettingsForm } from "@/components/SettingsForm";
import {
  captureTabContext,
  ensureBackendAccess,
  getActiveTab,
  supportsNativeSidePanel,
} from "@/lib/browser";
import { useCompanionIdentity } from "@/lib/identity";
import {
  createNoticeAction,
  createPromptAction,
  createSelectedTextAction,
  MAX_SELECTION_CHARS,
  MAX_VISIBLE_TEXT_CHARS,
} from "@/lib/pageContext";
import {
  enqueuePendingAction,
  loadSettings,
  resetSessionId,
  saveSettings,
  type LimeBotExtensionSettings,
} from "@/lib/storage";

async function requestPanelOpen(windowId: number) {
  chrome.runtime.sendMessage({ type: "open-side-panel", windowId });
}

export function App() {
  const [settings, setSettings] = useState<LimeBotExtensionSettings | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const nativeSidePanel = supportsNativeSidePanel();

  useEffect(() => {
    void loadSettings().then(setSettings);
  }, []);

  const identity = useCompanionIdentity(
    settings?.apiBaseUrl || "http://localhost:8000",
    settings?.apiKey
  );

  async function handleSave(nextSettings: LimeBotExtensionSettings) {
    setBusyAction("save");
    setStatusMessage(null);

    try {
      const granted = await ensureBackendAccess(nextSettings, true);
      if (!granted) {
        setStatusMessage(`Could not connect to ${identity.name}. Check your URLs.`);
        return;
      }

      const stored = await saveSettings(nextSettings);
      setSettings(stored);
      setStatusMessage("Settings saved.");
      setShowSettings(false);
    } finally {
      setBusyAction(null);
    }
  }

  async function handleResetSession() {
    setBusyAction("reset-session");
    try {
      const stored = await resetSessionId();
      setSettings(stored);
      setStatusMessage("Started a fresh companion session.");
    } finally {
      setBusyAction(null);
    }
  }

  async function queuePageAction(kind: "ask" | "selection") {
    setBusyAction(kind);
    setStatusMessage(null);

    try {
      const captured = await captureTabContext(MAX_SELECTION_CHARS, MAX_VISIBLE_TEXT_CHARS);
      if (!captured.ok) {
        setStatusMessage(captured.reason);
        if (typeof captured.windowId === "number") {
          await enqueuePendingAction(createNoticeAction("popup", captured.reason, "warning"));
          await requestPanelOpen(captured.windowId);
        }
        return;
      }

      const action =
        kind === "selection"
          ? createSelectedTextAction("popup", captured.page)
          : createPromptAction("popup", captured.page);

      await enqueuePendingAction(action);
      await requestPanelOpen(captured.windowId);
      setStatusMessage(
        action.kind === "notice"
          ? action.message
          : `Sent to ${identity.name} in ${nativeSidePanel ? "side panel" : "companion"}.`
      );
    } finally {
      setBusyAction(null);
    }
  }

  async function handleOpenPanel() {
    setBusyAction("open");
    try {
      const tab = await getActiveTab();
      if (typeof tab?.windowId === "number") {
        await requestPanelOpen(tab.windowId);
      } else {
        setStatusMessage(
          `No browser window was available to open the ${nativeSidePanel ? "panel" : "companion"}.`
        );
      }
    } finally {
      setBusyAction(null);
    }
  }

  if (!settings) {
    return <div className="popup-shell">Loading Companion...</div>;
  }

  return (
    <div className="popup-shell">
      <header className="popup-header">
        <div className="brand">
          <span className="brand-dot" />
          <span className="brand-name">{identity.name}</span>
        </div>
        <button
          className={`gear-button ${showSettings ? "active" : ""}`}
          type="button"
          onClick={() => {
            setStatusMessage(null);
            setShowSettings(!showSettings);
          }}
          title={showSettings ? "Back" : "Settings"}
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
      </header>

      {showSettings ? (
        <SettingsForm
          settings={settings}
          disabled={Boolean(busyAction)}
          statusMessage={statusMessage}
          onSave={handleSave}
          onResetSession={handleResetSession}
          botName={identity.name}
        />
      ) : (
        <>
          <div className="hero-card">
            <div className="hero-copy">
              <p className="eyebrow">{identity.name} Companion</p>
              <h1>Page help, selections, & approvals</h1>
              <p className="lede">
                Keep {identity.name} beside your browsing without living in a separate pop-out.
              </p>
            </div>
            <img
              className="hero-logo"
              src={identity.avatar || "/limeLogo.png"}
              alt={`${identity.name} avatar`}
            />
          </div>

          <div className="button-grid">
            <button
              className="primary-button"
              type="button"
              onClick={() => {
                void handleOpenPanel();
              }}
              disabled={Boolean(busyAction)}
            >
              {nativeSidePanel ? "Open side panel" : "Open companion"}
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                void queuePageAction("ask");
              }}
              disabled={Boolean(busyAction)}
            >
              Ask this page
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                void queuePageAction("selection");
              }}
              disabled={Boolean(busyAction)}
            >
              Send selected text
            </button>
          </div>

          {statusMessage ? <p className="popup-status-note">{statusMessage}</p> : null}
        </>
      )}
    </div>
  );
}
