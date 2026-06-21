const SETUP_PROGRESS_KEY = "limebot_setup_progress_v1";
export function saveSetupProgress(storage, progress) {
    storage.setItem(SETUP_PROGRESS_KEY, JSON.stringify(progress));
}
export function loadSetupProgress(storage) {
    const raw = storage.getItem(SETUP_PROGRESS_KEY);
    if (!raw)
        return null;
    try {
        const value = JSON.parse(raw);
        if ((value.phase !== "restarting" && value.phase !== "reconnecting") ||
            typeof value.restartToken !== "string" ||
            !value.restartToken ||
            typeof value.previousBootId !== "string" ||
            !value.previousBootId ||
            typeof value.model !== "string" ||
            typeof value.startedAt !== "number") {
            storage.removeItem(SETUP_PROGRESS_KEY);
            return null;
        }
        return value;
    }
    catch {
        storage.removeItem(SETUP_PROGRESS_KEY);
        return null;
    }
}
export function clearSetupProgress(storage) {
    storage.removeItem(SETUP_PROGRESS_KEY);
}
export function setupRetryDelay(attempt) {
    return Math.min(500 * 2 ** Math.max(0, attempt), 3000);
}
export function normalizeSetupError(payload) {
    const value = (payload && typeof payload === "object" ? payload : {});
    const code = typeof value.code === "string" ? value.code : "setup_failed";
    const fallbackMessages = {
        invalid_credentials: "The provider rejected these credentials. Check the key or sign in again.",
        quota_exceeded: "This provider account has no available quota. Choose another provider or update its plan.",
        model_unavailable: "The selected model is not available for this account.",
        provider_timeout: "The provider did not respond in time. Retry when the connection is stable.",
        provider_unreachable: "LimeBot could not reach the selected model.",
        restart_timeout: "LimeBot saved the setup but did not come back online in time.",
    };
    return {
        code,
        message: typeof value.message === "string" && value.message.trim()
            ? value.message
            : fallbackMessages[code] || "LimeBot could not finish setup.",
        retryable: typeof value.retryable === "boolean" ? value.retryable : true,
    };
}
