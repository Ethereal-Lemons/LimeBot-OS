import { useState, useEffect } from 'react';
import { Card } from "@/components/ui/card";
import {
    FileText,
    Save,
    Terminal,
    FolderOpen,
    Search,
    Cpu,
    CheckCircle2,
    XCircle,
    Loader2,
    ChevronDown,
    ChevronUp,
    ShieldAlert
} from "lucide-react";
import { cn } from "@/lib/utils";

export interface ToolExecution {
    tool: string;
    status: 'running' | 'completed' | 'error' | 'pending_confirmation' | 'progress' | 'waiting_confirmation';
    args: any;
    result?: string;
    tool_call_id: string;
    conf_id?: string;
    logs?: string[];
}

interface ToolCardProps {
    execution: ToolExecution;
    onConfirm?: (toolCallId: string, approved: boolean) => void;
    onConfirmSession?: (toolCallId: string) => void;
    onConfirmSideChannel?: (confId: string, approved: boolean, sessionWhitelist: boolean) => void;
}



export function ToolCard({ execution, onConfirm, onConfirmSession, onConfirmSideChannel }: ToolCardProps) {
    const [isExpanded, setIsExpanded] = useState(false);
    const [confirmationStatus] = useState<'pending' | 'approved' | 'denied' | null>(null);

    // Auto-expand when logs arrive
    useEffect(() => {
        if (execution.logs && execution.logs.length > 0 && !isExpanded && execution.status === 'running') {
            setIsExpanded(true);
        }
    }, [execution.logs, execution.status]);

    // Check if the action is blocked by backend
    const isBlocked = execution.status === 'waiting_confirmation' || execution.result?.includes("ACTION BLOCKED: Confirmation Required");

    // Needs confirmation if:
    // 1. Backend signaled waiting_confirmation status (New Way)
    // 2. Completed but blocked (Old Way - for compatibility)
    const needsConfirmation = isBlocked;



    const getIcon = () => {
        if (needsConfirmation && confirmationStatus === null) {
            return <ShieldAlert className="h-4 w-4 text-amber-400" />;
        }
        switch (execution.tool) {
            case 'read_file': return <FileText className="h-4 w-4" />;
            case 'write_file': return <Save className="h-4 w-4" />;
            case 'run_command': return <Terminal className="h-4 w-4" />;
            case 'list_dir': return <FolderOpen className="h-4 w-4" />;
            case 'search_web': return <Search className="h-4 w-4" />;
            default: return <Cpu className="h-4 w-4" />;
        }
    };

    const getSummary = () => {
        const args = execution.args;
        if (!args) return '';

        switch (execution.tool) {
            case 'read_file': return `Reading ${getFileName(args.path)}`;
            case 'write_file': return `Writing ${getFileName(args.path)}`;
            case 'run_command': return `> ${args.command}`;
            case 'list_dir': return `Listing ${args.path}`;
            case 'search_web': return `Searching "${args.query}"`;
            default: return JSON.stringify(args).slice(0, 50);
        }
    };

    const getFileName = (path: string) => {
        if (!path) return 'file';
        return path.split(/[/\\]/).pop();
    };

    const cardClasses = cn(
        "bg-slate-900 border-slate-800 text-slate-300 overflow-hidden",
        needsConfirmation && confirmationStatus === null && "border-amber-500/50 bg-amber-950/20"
    );

    return (
        <div className="my-2 max-w-[80%]">
            <Card className={cardClasses}>
                <div
                    className="flex items-center gap-3 p-3 cursor-pointer hover:bg-slate-800/50 transition-colors"
                    onClick={() => setIsExpanded(!isExpanded)}
                >
                    <div className={cn(
                        "p-2 rounded-md",
                        needsConfirmation && confirmationStatus === null
                            ? "bg-amber-500/20"
                            : "bg-slate-800",
                        execution.status === 'running' && !needsConfirmation && "animate-pulse"
                    )}>
                        {getIcon()}
                    </div>

                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                            <span className={cn(
                                "font-medium text-sm",
                                needsConfirmation && confirmationStatus === null
                                    ? "text-amber-200"
                                    : "text-slate-200"
                            )}>
                                {needsConfirmation && confirmationStatus === null
                                    ? "⚠️ Confirmation Required"
                                    : execution.tool}
                            </span>
                            <span className="text-xs text-slate-500">
                                {confirmationStatus === 'approved' ? 'Approved' :
                                    confirmationStatus === 'denied' ? 'Denied' :
                                        needsConfirmation && confirmationStatus === null ? 'Blocked' :
                                            execution.status === 'running' ? 'Running...' :
                                                execution.status === 'completed' ? 'Completed' : 'Failed'}
                            </span>
                        </div>
                        <div className="text-xs text-slate-400 truncate font-mono">
                            {getSummary()}
                        </div>
                    </div>

                    <div className="flex items-center gap-2 text-slate-500">
                        {execution.status === 'running' && !needsConfirmation && <Loader2 className="h-4 w-4 animate-spin" />}
                        {execution.status === 'completed' && !isBlocked && <CheckCircle2 className="h-4 w-4 text-green-500" />}
                        {execution.status === 'error' && <XCircle className="h-4 w-4 text-red-500" />}
                        {confirmationStatus === 'approved' && <CheckCircle2 className="h-4 w-4 text-green-500" />}
                        {confirmationStatus === 'denied' && <XCircle className="h-4 w-4 text-red-500" />}

                        {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                    </div>
                </div>

                {needsConfirmation && confirmationStatus === null && (
                    <div className="flex gap-2 p-2 pt-2 border-t border-amber-500/30 bg-amber-950/10">
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                if (execution.conf_id && onConfirmSideChannel) {
                                    onConfirmSideChannel(execution.conf_id, true, false);
                                } else {
                                    onConfirm?.(execution.tool_call_id, true);
                                }
                            }}
                            className="bg-amber-500/20 hover:bg-amber-500/30 text-amber-500 px-3 py-1 rounded text-xs font-semibold"
                        >
                            Allow Once
                        </button>
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                if (execution.conf_id && onConfirmSideChannel) {
                                    onConfirmSideChannel(execution.conf_id, true, true);
                                } else {
                                    onConfirmSession?.(execution.tool_call_id);
                                }
                            }}
                            className="bg-purple-500/20 hover:bg-purple-500/30 text-purple-400 px-3 py-1 rounded text-xs font-semibold"
                            title="Allow all executions of this tool for the rest of this session"
                        >
                            Allow Session
                        </button>
                        <div className="flex-1" />
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                if (execution.conf_id && onConfirmSideChannel) {
                                    onConfirmSideChannel(execution.conf_id, false, false);
                                } else {
                                    onConfirm?.(execution.tool_call_id, false);
                                }
                            }}
                            className="bg-red-500/20 hover:bg-red-500/30 text-red-500 px-3 py-1 rounded text-xs font-semibold"
                        >
                            Deny
                        </button>
                    </div>
                )}

                {isExpanded && (
                    <div className="p-3 pt-0 border-t border-slate-800/50 bg-slate-950/30">
                        <div className="mt-2 text-xs font-mono whitespace-pre-wrap text-slate-400 max-h-60 overflow-y-auto">
                            {execution.logs && execution.logs.length > 0 && (
                                <div className="mb-3 flex flex-col gap-1">
                                    {execution.logs.map((log, i) => (
                                        <div key={i} className="text-primary/80 border-l border-primary/30 pl-2">
                                            {log}
                                        </div>
                                    ))}
                                    {execution.status === 'running' && (
                                        <div className="flex items-center gap-2 text-primary/40 animate-pulse mt-1">
                                            <div className="w-1 h-1 bg-primary rounded-full" />
                                            Listening for updates...
                                        </div>
                                    )}
                                </div>
                            )}

                            <div className="mb-2">
                                <span className="text-slate-500 select-none">$ args: </span>
                                {JSON.stringify(execution.args, null, 2)}
                            </div>
                            {execution.result && (
                                <div>
                                    <span className="text-slate-500 select-none">$ result: </span>
                                    {execution.result}
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </Card>
        </div>
    );
}
