import type { TurnStatus } from "./chat-state.js";

/** The only fields these helpers read, so they stay testable without React. */
export interface ToolLike {
    status: string;
    result?: string;
}

export type RowState = "planned" | "running" | "done" | "failed" | "waiting" | "approved" | "denied";

export function needsApproval(execution: ToolLike): boolean {
    return (
        execution.status === "waiting_confirmation" ||
        execution.status === "pending_confirmation" ||
        !!execution.result?.includes("ACTION BLOCKED: Confirmation Required")
    );
}

export function rowState(execution: ToolLike, decision: "approved" | "denied" | null): RowState {
    if (decision === "denied") return "denied";
    if (needsApproval(execution) && !decision) return "waiting";
    // Approved, and the backend has not reported progress yet.
    if (decision === "approved" && needsApproval(execution)) return "approved";
    if (execution.status === "error") return "failed";
    if (execution.status === "completed") return decision === "approved" ? "approved" : "done";
    if (execution.status === "running" || execution.status === "progress") return "running";
    return "planned";
}

export function summarizeTurn(executions: ToolLike[], turnStatus?: TurnStatus) {
    const states = executions.map((execution) => rowState(execution, null));
    const running = states.filter((state) => state === "running" || state === "planned").length;
    const waiting = states.filter((state) => state === "waiting").length;
    const failed = states.filter((state) => state === "failed").length;
    const done = states.filter((state) => state === "done" || state === "approved").length;
    // The turn's terminal event is authoritative: a tool that failed and was
    // then recovered must not paint the whole group red.
    const recovered = turnStatus === "completed" && failed > 0;
    return { total: executions.length, running, waiting, failed: recovered ? 0 : failed, done, recovered, turnStatus };
}
