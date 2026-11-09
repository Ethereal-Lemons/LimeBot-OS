import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * Tracks whether a form differs from what was last loaded or saved.
 *
 * Pass the whole editable value (anything JSON-serialisable). After loading
 * from the server, or after a successful save, call `markSaved()` so the
 * current value becomes the new baseline. `baseline` is the last saved value,
 * which `Discard` can restore.
 */
export function useUnsaved<T>(current: T) {
    const serialised = useMemo(() => JSON.stringify(current), [current]);
    const [baseline, setBaseline] = useState<string | null>(null);
    const currentRef = useRef(serialised);
    useEffect(() => {
        currentRef.current = serialised;
    }, [serialised]);

    const markSaved = useCallback((value?: T) => {
        setBaseline(value === undefined ? currentRef.current : JSON.stringify(value));
    }, []);

    const dirty = baseline !== null && baseline !== serialised;
    const restore = useCallback((): T | undefined => (baseline === null ? undefined : (JSON.parse(baseline) as T)), [baseline]);

    return { dirty, markSaved, restore, ready: baseline !== null };
}

/** Warns before the tab is closed or reloaded while edits are unsaved. */
export function useBeforeUnloadGuard(active: boolean) {
    useEffect(() => {
        if (!active) return;
        const handler = (event: BeforeUnloadEvent) => {
            event.preventDefault();
            event.returnValue = "";
        };
        window.addEventListener("beforeunload", handler);
        return () => window.removeEventListener("beforeunload", handler);
    }, [active]);
}
