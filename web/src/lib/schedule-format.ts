/** Pure formatting helpers for the scheduled-jobs page. */

/** "2d 4h 3m 12s". Negative values mean the job is due and about to run. */
export function formatCountdown(seconds: number): string {
    if (seconds < 0) return "Running now";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    // Past a day the seconds are noise.
    const parts = d > 0 ? [`${d}d`, h && `${h}h`, m && `${m}m`] : [h && `${h}h`, m && `${m}m`, `${s}s`];
    return parts.filter(Boolean).join(" ");
}

/** Minutes from UTC as "UTC+5:30", including half-hour zones. */
export function formatTzOffset(offsetMinutes: number): string {
    const sign = offsetMinutes >= 0 ? "+" : "-";
    const abs = Math.abs(offsetMinutes);
    const hours = Math.floor(abs / 60);
    const mins = abs % 60;
    return `UTC${sign}${hours}${mins ? `:${String(mins).padStart(2, "0")}` : ""}`;
}

/** A short, human reading of the common five-field cron shapes; otherwise the raw expression. */
export function describeCron(expr: string): string {
    const parts = expr.trim().split(/\s+/);
    if (parts.length !== 5) return expr;
    const [min, hour, dom, month, dow] = parts;
    const pad = (value: string) => value.padStart(2, "0");
    const clock = /^\d+$/.test(min) && /^\d+$/.test(hour) ? `${pad(hour)}:${pad(min)}` : null;
    if (min.startsWith("*/") && hour === "*" && dom === "*" && month === "*" && dow === "*") return `Every ${min.slice(2)} minutes`;
    if (clock && dom === "*" && month === "*") {
        if (dow === "*") return `Every day at ${clock}`;
        if (dow === "1-5") return `Weekdays at ${clock}`;
        if (dow === "0,6" || dow === "6,0") return `Weekends at ${clock}`;
    }
    return expr;
}
