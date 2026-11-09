import type { TurnStatus } from "@/lib/chat-state";
import { ToolRow, type ToolRowHandlers } from "./tool-ui";

export interface ConfirmationPreview {
    kind: string;
    summary?: string;
    path?: string;
    mode?: string;
    content_preview?: string;
    diff?: string;
    diff_error?: string;
    command?: string;
    cwd?: string;
    risk_flags?: string[];
    affected_paths?: string[];
    target_type?: string;
    args_preview?: string;
}

export interface ToolExecution {
    tool: string;
    profile_id?: string;
    profile_name?: string;
    status: 'planned' | 'running' | 'completed' | 'error' | 'pending_confirmation' | 'progress' | 'waiting_confirmation';
    args: Record<string, unknown>;
    result?: string;
    tool_call_id: string;
    conf_id?: string;
    turnStatus?: TurnStatus;
    logs?: string[];
    preview?: ConfirmationPreview;
}

interface ToolCardProps extends ToolRowHandlers {
    execution: ToolExecution;
}

/** One tool call, shown as a single compact row that expands for details. */
export function ToolCard({ execution, onConfirm, onConfirmSession, onConfirmSideChannel }: ToolCardProps) {
    return (
        <div className="my-1 overflow-hidden rounded-xl border border-border/60 bg-card/50 shadow-sm">
            <ToolRow
                execution={execution}
                handlers={{ onConfirm, onConfirmSession, onConfirmSideChannel }}
                isLast
            />
        </div>
    );
}
