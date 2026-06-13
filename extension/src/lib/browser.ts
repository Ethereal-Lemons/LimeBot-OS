import type { LimeBotExtensionSettings } from "@/lib/storage";

export type CapturedPageContext = {
  title: string;
  url: string;
  selectedText: string;
  visibleText: string;
};

export type TabContextResult =
  | {
      ok: true;
      tabId: number;
      windowId: number;
      page: CapturedPageContext;
    }
  | {
      ok: false;
      windowId?: number;
      reason: string;
    };

const SUPPORTED_PROTOCOLS = new Set(["http:", "https:"]);
const NETWORK_PROTOCOLS = new Set(["http:", "https:", "ws:", "wss:"]);

function cleanHostName(url: URL) {
  return url.hostname.includes(":") ? `[${url.hostname}]` : url.hostname;
}

export function createPermissionPattern(rawUrl: string) {
  const url = new URL(rawUrl);
  if (!NETWORK_PROTOCOLS.has(url.protocol)) {
    throw new Error(`Unsupported URL protocol: ${url.protocol}`);
  }
  return `${url.protocol}//${cleanHostName(url)}/*`;
}

export function isSupportedPageUrl(rawUrl: string | undefined) {
  if (!rawUrl) return false;
  try {
    const url = new URL(rawUrl);
    return SUPPORTED_PROTOCOLS.has(url.protocol);
  } catch {
    return false;
  }
}

export async function ensureNetworkAccess(urls: string[], interactive: boolean) {
  const patterns = Array.from(
    new Set(
      urls
        .filter(Boolean)
        .map((url) => createPermissionPattern(url))
    )
  );

  if (!patterns.length) {
    return true;
  }

  const contains = await chrome.permissions.contains({ origins: patterns });
  if (contains) {
    return true;
  }

  if (!interactive) {
    return false;
  }

  return chrome.permissions.request({ origins: patterns });
}

export async function ensureBackendAccess(settings: LimeBotExtensionSettings, interactive: boolean) {
  return ensureNetworkAccess([settings.apiBaseUrl, settings.wsBaseUrl], interactive);
}

export async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

export function supportsNativeSidePanel() {
  return Boolean(chrome.sidePanel?.open);
}

export async function openCompanionSurface(windowId?: number) {
  if (supportsNativeSidePanel() && typeof windowId === "number") {
    await chrome.sidePanel.open({ windowId });
    return "sidepanel";
  }

  await chrome.tabs.create({ url: chrome.runtime.getURL("sidepanel.html?surface=tab") });
  return "tab";
}

export async function openDashboard(url: string) {
  await chrome.tabs.create({ url });
}

function capturePageSnapshot(selectionLimit: number, visibleTextLimit: number): CapturedPageContext {
  const collapseWhitespace = (value: string) => value.replace(/\s+/g, " ").trim();
  const clip = (value: string, limit: number) => {
    if (value.length <= limit) return value;
    return `${value.slice(0, limit).trim()}\n\n[Truncated at ${limit} characters.]`;
  };

  const selectedText = collapseWhitespace(window.getSelection?.()?.toString() ?? "");
  const visibleText = collapseWhitespace(document.body?.innerText ?? "");

  return {
    title: collapseWhitespace(document.title || ""),
    url: window.location.href,
    selectedText: clip(selectedText, selectionLimit),
    visibleText: clip(visibleText, visibleTextLimit),
  };
}

export async function captureTabContext(
  selectionLimit: number,
  visibleTextLimit: number,
  tabArg?: chrome.tabs.Tab
): Promise<TabContextResult> {
  const tab = tabArg ?? (await getActiveTab());
  const tabId = tab?.id;
  if (typeof tabId !== "number") {
    return { ok: false, reason: "No active browser tab was available." };
  }

  const tabUrl = tab.url;
  if (!tabUrl || !isSupportedPageUrl(tabUrl)) {
    return {
      ok: false,
      windowId: tab.windowId,
      reason: "This page cannot be inspected. Try a normal http or https webpage.",
    };
  }

  const granted = await ensureNetworkAccess([tabUrl], true);
  if (!granted) {
    return {
      ok: false,
      windowId: tab.windowId,
      reason: "Page access is required for this site before the context can be read.",
    };
  }

  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: capturePageSnapshot,
      args: [selectionLimit, visibleTextLimit],
    });

    if (!result?.result) {
      return {
        ok: false,
        windowId: tab.windowId,
        reason: "The page did not return any readable content.",
      };
    }

    return {
      ok: true,
      tabId,
      windowId: tab.windowId,
      page: result.result,
    };
  } catch (error) {
    console.error("Failed to capture page context", error);
    return {
      ok: false,
      windowId: tab.windowId,
      reason: "This page blocked script access. Browser-internal pages and some PDFs are not available.",
    };
  }
}
