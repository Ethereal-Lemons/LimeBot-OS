import { Card } from "@/components/ui/card";
import { ShieldAlert } from "lucide-react";
import { cn } from "@/lib/utils";

export interface ConfirmationRequest {
    id: string;
    action: string;
    description: string;
    details?: string;
    status: 'pending' | 'approved' | 'denied';
}

interface ConfirmationCardProps {
    request: ConfirmationRequest;
}

export function ConfirmationCard({ request }: ConfirmationCardProps) {
    const isPending = request.status === 'pending';

    return (
        <Card className={cn(
            "bg-amber-950/30 border-amber-500/30 overflow-hidden my-2",
            !isPending && "opacity-60"
        )}>
            <div className="p-4">
                <div className="flex items-start gap-3">
                    <div className="p-2 rounded-lg bg-amber-500/20 text-amber-400">
                        <ShieldAlert className="h-5 w-5" />
                    </div>

                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                            <span className="font-semibold text-sm text-amber-200">
                                Confirmation Required
                            </span>
                            {!isPending && (
                                <span className={cn(
                                    "text-xs px-2 py-0.5 rounded-full font-medium",
                                    request.status === 'approved'
                                        ? "bg-green-500/20 text-green-400"
                                        : "bg-red-500/20 text-red-400"
                                )}>
                                    {request.status === 'approved' ? 'Approved' : 'Denied'}
                                </span>
                            )}
                        </div>

                        <p className="text-sm text-amber-100/80 mb-1">
                            {request.action}: <span className="font-mono">{request.description}</span>
                        </p>

                        {request.details && (
                            <p className="text-xs text-amber-200/50 font-mono truncate">
                                {request.details}
                            </p>
                        )}
                    </div>
                </div>

                {isPending && (
                    <div className="flex gap-2 mt-4 ml-11 text-xs text-amber-200/50 italic">
                        Reply to the chat to approve or deny.
                    </div>
                )}
            </div>
        </Card>
    );
}
