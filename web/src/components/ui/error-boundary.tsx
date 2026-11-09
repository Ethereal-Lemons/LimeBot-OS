import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { toast } from "sonner";

interface ErrorBoundaryProps {
    children: ReactNode;
    /** Changing this value clears a previous error, for example the active view. */
    resetKey?: string;
    /** Short name of the area that failed, shown in the message. */
    area?: string;
}

interface ErrorBoundaryState {
    error: Error | null;
}

/**
 * One broken view should not blank the whole dashboard. This keeps the shell
 * (sidebar, connection state) alive and offers a retry and the details.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
    state: ErrorBoundaryState = { error: null };

    static getDerivedStateFromError(error: Error): ErrorBoundaryState {
        return { error };
    }

    componentDidCatch(error: Error, info: ErrorInfo) {
        console.error(`[ui] ${this.props.area || "view"} crashed:`, error, info.componentStack);
    }

    componentDidUpdate(previous: ErrorBoundaryProps) {
        if (this.state.error && previous.resetKey !== this.props.resetKey) {
            this.setState({ error: null });
        }
    }

    private copyDetails = async () => {
        const error = this.state.error;
        if (!error) return;
        const text = `${error.name}: ${error.message}\n${error.stack || ""}`.trim();
        try {
            await navigator.clipboard.writeText(text);
            toast.success("Error details copied");
        } catch {
            toast.error("Could not copy to the clipboard");
        }
    };

    render() {
        const { error } = this.state;
        if (!error) return this.props.children;
        return (
            <div className="flex h-full min-h-[40vh] items-center justify-center p-6">
                <div role="alert" className="w-full max-w-md space-y-3 rounded-xl border border-red-400/30 bg-red-500/[0.06] p-5">
                    <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                        <AlertTriangle className="h-4 w-4 text-red-400" />
                        {this.props.area ? `${this.props.area} hit a problem` : "This page hit a problem"}
                    </div>
                    <p className="text-xs leading-relaxed text-muted-foreground">
                        The rest of LimeBot is still running. Try loading the page again; if it keeps failing, copy the details
                        so the cause can be found.
                    </p>
                    <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border/60 bg-background/70 p-2 font-mono text-[11px] text-muted-foreground">
                        {error.message || String(error)}
                    </pre>
                    <div className="flex items-center gap-2">
                        <button
                            type="button"
                            onClick={() => this.setState({ error: null })}
                            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
                        >
                            <RotateCcw className="h-3.5 w-3.5" />
                            Try again
                        </button>
                        <button
                            type="button"
                            onClick={this.copyDetails}
                            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
                        >
                            Copy details
                        </button>
                    </div>
                </div>
            </div>
        );
    }
}
