import { captureTabContext, openCompanionSurface, supportsNativeSidePanel } from "@/lib/browser";
import {
  createNoticeAction,
  createPromptAction,
  createSelectedTextAction,
  createWatchVideoAction,
  MAX_SELECTION_CHARS,
  MAX_VISIBLE_TEXT_CHARS,
} from "@/lib/pageContext";
import { enqueuePendingAction } from "@/lib/storage";

const MENU_OPEN_PANEL = "limebot-open-panel";
const MENU_ASK_PAGE = "limebot-ask-page";
const MENU_SEND_SELECTION = "limebot-send-selection";
const MENU_WATCH_VIDEO = "limebot-watch-video";

async function registerContextMenus() {
  await chrome.contextMenus.removeAll();

  chrome.contextMenus.create({
    id: MENU_OPEN_PANEL,
    title: supportsNativeSidePanel() ? "Open side panel" : "Open companion",
    contexts: ["action"],
  });

  chrome.contextMenus.create({
    id: MENU_WATCH_VIDEO,
    title: "Watch this video",
    contexts: ["page", "video", "action"],
  });

  chrome.contextMenus.create({
    id: MENU_ASK_PAGE,
    title: "Ask about this page",
    contexts: ["page", "selection", "action"],
  });

  chrome.contextMenus.create({
    id: MENU_SEND_SELECTION,
    title: "Send selected text",
    contexts: ["selection", "action"],
  });
}

async function queueActionAndOpenPanel(
  action: ReturnType<typeof createPromptAction> | ReturnType<typeof createSelectedTextAction> | ReturnType<typeof createWatchVideoAction>,
  windowId?: number
) {
  await enqueuePendingAction(action);
  await openCompanionSurface(windowId);
}

chrome.runtime.onInstalled.addListener(() => {
  void registerContextMenus();
});

chrome.runtime.onStartup.addListener(() => {
  void registerContextMenus();
});

chrome.runtime.onMessage.addListener((message: { type?: string; windowId?: number }) => {
  if (message.type === "open-side-panel") {
    void openCompanionSurface(message.windowId);
  }
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  void (async () => {
    if (info.menuItemId === MENU_OPEN_PANEL) {
      await openCompanionSurface(tab?.windowId);
      return;
    }

    const captured = await captureTabContext(MAX_SELECTION_CHARS, MAX_VISIBLE_TEXT_CHARS, tab);
    if (!captured.ok) {
      await enqueuePendingAction(
        createNoticeAction("context-menu", captured.reason, "warning")
      );
      await openCompanionSurface(captured.windowId);
      return;
    }

    if (info.menuItemId === MENU_SEND_SELECTION) {
      await queueActionAndOpenPanel(
        createSelectedTextAction("context-menu", {
          ...captured.page,
          selectedText: info.selectionText?.trim() || captured.page.selectedText,
        }),
        captured.windowId
      );
      return;
    }

    if (info.menuItemId === MENU_WATCH_VIDEO) {
      await queueActionAndOpenPanel(
        createWatchVideoAction("context-menu", captured.page),
        captured.windowId
      );
      return;
    }

    if (info.menuItemId === MENU_ASK_PAGE) {
      await queueActionAndOpenPanel(
        createPromptAction("context-menu", captured.page),
        captured.windowId
      );
    }
  })();
});
