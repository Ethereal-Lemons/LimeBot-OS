import { useCallback, useRef, useState } from "react";
import { classifySaveError, type SaveErrorInfo } from "@/lib/save-error";

/**
 * Wraps one save action: tracks the in-flight flag, the last classified
 * failure and the time of the last success. `run` resolves to true on success
 * so callers can mark the form clean only when the server accepted it.
 */
export function useSaveState(subject = "your changes") {
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<SaveErrorInfo | null>(null);
    const [savedAt, setSavedAt] = useState<number | null>(null);
    const inFlight = useRef(false);

    const run = useCallback(
        async (action: () => Promise<unknown>): Promise<boolean> => {
            if (inFlight.current) return false;
            inFlight.current = true;
            setSaving(true);
            setError(null);
            try {
                await action();
                setSavedAt(Date.now());
                return true;
            } catch (caught) {
                console.error("Save failed:", caught);
                setError(classifySaveError(caught, subject));
                return false;
            } finally {
                inFlight.current = false;
                setSaving(false);
            }
        },
        [subject],
    );

    const clearError = useCallback(() => setError(null), []);
    return { saving, error, savedAt, run, clearError, setError };
}
