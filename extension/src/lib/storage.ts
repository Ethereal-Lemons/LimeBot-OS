export type LimeBotExtensionSettings = {
  apiBaseUrl: string;
  wsBaseUrl: string;
  dashboardUrl: string;
  apiKey: string;
  sessionId: string;
  profileId: string;
  profileSessions: Record<string, string>;
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
  const rawProfileId = normalizeText(raw.profileId).toLowerCase();
  const profileId = /^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$/.test(rawProfileId)
    ? rawProfileId
    : "default";
  const profileSessions: Record<string, string> = {};
  if (raw.profileSessions && typeof raw.profileSessions === "object") {
    for (const [key, sessionId] of Object.entries(raw.profileSessions)) {
      if (
        /^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$/.test(key) &&
        typeof sessionId === "string" &&
        sessionId.trim()
      ) {
        profileSessions[key] = sessionId.trim();
      }
    }
  }
  const sessionId = normalizeText(raw.sessionId) || createSessionId();
  profileSessions[profileId] = profileSessions[profileId] || sessionId;

  return {
    apiBaseUrl: normalizeUrl(raw.apiBaseUrl, DEFAULT_API_BASE_URL),
    wsBaseUrl: normalizeUrl(raw.wsBaseUrl, DEFAULT_WS_BASE_URL),
    dashboardUrl: normalizeUrl(raw.dashboardUrl, DEFAULT_DASHBOARD_URL),
    apiKey: normalizeText(raw.apiKey),
    sessionId: profileSessions[profileId],
    profileId,
    profileSessions,
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
  const current = await loadSettings();
  const sessionId = createSessionId();
  return saveSettings({
    sessionId,
    profileSessions: { ...current.profileSessions, [current.profileId]: sessionId },
  });
}

export async function selectAgentProfile(profileId: string) {
  const normalizedId = normalizeText(profileId).toLowerCase();
  if (!/^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$/.test(normalizedId)) {
    throw new Error("Invalid agent profile ID.");
  }
  const current = await loadSettings();
  const profileSessions = {
    ...current.profileSessions,
    [current.profileId]: current.sessionId,
  };
  const sessionId = profileSessions[normalizedId] || createSessionId();
  profileSessions[normalizedId] = sessionId;
  return saveSettings({ profileId: normalizedId, sessionId, profileSessions });
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
