import { useEffect, useState } from "react";
import axios from "axios";
import { Monitor, RefreshCw } from "lucide-react";
import { QRCodeCanvas } from "qrcode.react";
import { API_BASE_URL } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { WhatsAppIcon } from "./ChannelIcons";

export function WhatsAppConnectionSection() {
    const [status, setStatus] = useState<"disconnected" | "connecting" | "connected" | "scanning">("disconnected");
    const [resetting, setResetting] = useState(false);
    const [qrCode, setQrCode] = useState<string | null>(null);
    const [alertDialog, setAlertDialog] = useState({
        open: false,
        title: "",
        description: "",
    });

    const showAlert = (title: string, description: string) => {
        setAlertDialog({ open: true, title, description });
    };

    useEffect(() => {
        const wsUrl = API_BASE_URL.replace("http", "ws");
        const apiKey = localStorage.getItem("limebot_api_key");
        const socketUrl = new URL(`${wsUrl}/ws`);
        if (apiKey) {
            socketUrl.searchParams.set("api_key", apiKey);
        }
        const ws = new WebSocket(socketUrl.toString());

        ws.onerror = (event) => {
            console.error("Channels WS error", event);
            if (
                ws.readyState !== WebSocket.CLOSING &&
                ws.readyState !== WebSocket.CLOSED
            ) {
                ws.close();
            }
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === "auth_ok") {
                    return;
                }

                const nextQrCode = data.metadata?.qr || data.qr;

                if (data.type === "whatsapp_qr" || data.metadata?.type === "whatsapp_qr") {
                    if (nextQrCode) {
                        setQrCode(nextQrCode);
                        setStatus("scanning");
                    }
                } else if (data.type === "whatsapp_status" || data.metadata?.type === "whatsapp_status") {
                    const newStatus = data.metadata?.status || data.status;
                    if (newStatus === "connected") {
                        setStatus("connected");
                        setQrCode(null);
                    } else if (newStatus === "disconnected") {
                        setStatus("disconnected");
                        setQrCode(null);
                    }
                }
            } catch (e) {
                console.error("Error parsing WS message", e);
            }
        };

        return () => {
            ws.close();
        };
    }, []);

    const handleReset = async () => {
        setResetting(true);
        try {
            const res = await axios.post(`${API_BASE_URL}/api/whatsapp/reset`);
            const data = res.data;
            if (data.status === "success") {
                setStatus("disconnected");
                showAlert("Session Reset", data.message || "WhatsApp session has been reset successfully.");
            } else {
                showAlert("Reset Failed", data.message || "Failed to reset WhatsApp session.");
            }
        } catch (e) {
            showAlert("Error", "An unexpected error occurred while resetting the session.");
        }
        setResetting(false);
    };

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Monitor className="h-5 w-5 text-[#25D366]" />
                    Device Connection
                </CardTitle>
                <CardDescription>
                    WhatsApp connection is managed via the terminal.
                </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col items-center justify-center p-6 min-h-[120px]">
                {status === "connected" ? (
                    <div className="flex flex-col items-center gap-4 text-green-500">
                        <div className="h-16 w-16 rounded-full bg-green-500/10 flex items-center justify-center">
                            <WhatsAppIcon className="h-8 w-8" />
                        </div>
                        <p className="font-semibold">WhatsApp is Connected</p>
                    </div>
                ) : qrCode ? (
                    <div className="flex flex-col items-center gap-4">
                        <div className="bg-white p-4 rounded shadow-sm">
                            <QRCodeCanvas value={qrCode} size={200} />
                        </div>
                        <p className="text-sm text-muted-foreground">Scan with WhatsApp</p>
                    </div>
                ) : (
                    <div className="text-center text-muted-foreground">
                        <p className="mb-2">Click <strong>Enable</strong> above to start.</p>
                        <p className="text-xs opacity-70">After enabling, the page will refresh and you will see the QR code shortly.</p>
                    </div>
                )}
            </CardContent>
            <div className="px-6 pb-6 flex justify-center">
                <Button
                    variant="outline"
                    size="sm"
                    onClick={handleReset}
                    disabled={resetting}
                    className="text-orange-500 border-orange-500/50 hover:bg-orange-500/10"
                >
                    <RefreshCw className={`h-4 w-4 mr-2 ${resetting ? "animate-spin" : ""}`} />
                    {resetting ? "Resetting..." : "Reset Session"}
                </Button>
            </div>

            <AlertDialog open={alertDialog.open} onOpenChange={(open) => setAlertDialog(prev => ({ ...prev, open }))}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{alertDialog.title}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {alertDialog.description}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogAction>OK</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </Card>
    );
}
