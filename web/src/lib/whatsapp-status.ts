import type { RowState } from "./tool-state.js";

export type WhatsAppConnectionStatus = "disabled" | "disconnected" | "connecting" | "connected" | "scanning";

/** The pairing status as a tool-row glyph state plus one word. */
export function describeWhatsAppStatus(
    status: WhatsAppConnectionStatus,
    hasQr: boolean,
    unreachable: boolean
): { state: RowState; label: string } {
    if (unreachable) return { state: "failed", label: "Bridge unreachable" };
    if (status === "connected") return { state: "done", label: "Linked" };
    if (hasQr) return { state: "waiting", label: "Waiting for scan" };
    if (status === "connecting" || status === "scanning") return { state: "running", label: "Connecting" };
    if (status === "disabled") return { state: "planned", label: "Disabled" };
    return { state: "planned", label: "Not linked" };
}
