export type LimeBotExtensionSettings = {
  apiBaseUrl: string;
  wsBaseUrl: string;
  dashboardUrl: string;
  apiKey: string;
  sessionId: string;
  showStatusBubble: boolean;
};

export type PendingPromptAction = {
  id: string;
  kind: "prompt";
  source: "popup" | "sidepanel" | "context-menu";
  prompt: string;
  displayText: string;
  createdAt: number;
};

export type PendingNoticeAction = {
  id: string;
  kind: "notice";
  source: "popup" | "sidepanel" | "context-menu";
  message: string;
  level: "info" | "warning";
  createdAt: number;
};

export type PendingAction = PendingPromptAction | PendingNoticeAction;

const SETTINGS_KEY = "limebot-extension-settings";
const PENDING_ACTIONS_KEY = "limebot-extension-pending-actions";

export const DEFAULT_API_BASE_URL = "http://localhost:8000";
export const DEFAULT_WS_BASE_URL = "ws://localhost:8000";
export const DEFAULT_DASHBOARD_URL = "http://localhost:5173";

function createSessionId() {
  return `extension-${crypto.randomUUID()}`;
}

function trimTrailingSlash(value: string) {
  return value.replace(/\/+$/, "");
}

function normalizeText(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeUrl(value: unknown, fallback: string) {
  const raw = normalizeText(value);
  return raw ? trimTrailingSlash(raw) : fallback;
}

function isPendingPromptAction(value: unknown): value is PendingPromptAction {
  if (!value || typeof value !== "object") return false;
  const action = value as Record<string, unknown>;
  return (
    action.kind === "prompt" &&
    typeof action.id === "string" &&
    typeof action.prompt === "string" &&
    typeof action.displayText === "string" &&
    typeof action.createdAt === "number"
  );
}

function isPendingNoticeAction(value: unknown): value is PendingNoticeAction {
  if (!value || typeof value !== "object") return false;
  const action = value as Record<string, unknown>;
  return (
    action.kind === "notice" &&
    typeof action.id === "string" &&
    typeof action.message === "string" &&
    typeof action.createdAt === "number"
  );
}

function normalizePendingAction(value: unknown): PendingAction | null {
  if (isPendingPromptAction(value)) return value;
  if (isPendingNoticeAction(value)) {
    return {
      ...value,
      level: value.level === "warning" ? "warning" : "info",
    };
  }
  return null;
}

export function normalizeSettings(value: unknown): LimeBotExtensionSettings {
  const raw = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;

  return {
    apiBaseUrl: normalizeUrl(raw.apiBaseUrl, DEFAULT_API_BASE_URL),
    wsBaseUrl: normalizeUrl(raw.wsBaseUrl, DEFAULT_WS_BASE_URL),
    dashboardUrl: normalizeUrl(raw.dashboardUrl, DEFAULT_DASHBOARD_URL),
    apiKey: normalizeText(raw.apiKey),
    sessionId: normalizeText(raw.sessionId) || createSessionId(),
    showStatusBubble: raw.showStatusBubble !== false,
  };
}

export async function loadSettings() {
  const stored = await chrome.storage.local.get(SETTINGS_KEY);
  return normalizeSettings(stored[SETTINGS_KEY]);
}

export async function saveSettings(nextPartial: Partial<LimeBotExtensionSettings>) {
  const current = await loadSettings();
  const next = normalizeSettings({ ...current, ...nextPartial });
  await chrome.storage.local.set({ [SETTINGS_KEY]: next });
  return next;
}

export async function resetSessionId() {
  return saveSettings({ sessionId: createSessionId() });
}

export async function loadPendingActions() {
  const stored = await chrome.storage.local.get(PENDING_ACTIONS_KEY);
  const rawQueue = Array.isArray(stored[PENDING_ACTIONS_KEY]) ? stored[PENDING_ACTIONS_KEY] : [];
  return rawQueue
    .map((entry) => normalizePendingAction(entry))
    .filter((entry): entry is PendingAction => Boolean(entry));
}

export async function replacePendingActions(actions: PendingAction[]) {
  await chrome.storage.local.set({ [PENDING_ACTIONS_KEY]: actions });
}

export async function enqueuePendingAction(action: PendingAction) {
  const queue = await loadPendingActions();
  queue.push(action);
  await replacePendingActions(queue);
  return queue;
}

export async function clearPendingActions() {
  await replacePendingActions([]);
}

export const STORAGE_KEYS = {
  settings: SETTINGS_KEY,
  pendingActions: PENDING_ACTIONS_KEY,
};
