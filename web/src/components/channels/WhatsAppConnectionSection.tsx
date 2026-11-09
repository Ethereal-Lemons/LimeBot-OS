import { useEffect, useState } from "react";
import axios from "axios";
import { RotateCcw } from "lucide-react";
import { QRCodeCanvas } from "qrcode.react";
import { API_BASE_URL, api } from "@/lib/api";
import { classifyRequestError, type RequestErrorInfo } from "@/lib/request-error";
import { cn } from "@/lib/utils";
import { InlineConfirm, RowButton, StatusPill } from "@/components/ui/data-page";
import { describeWhatsAppStatus, type WhatsAppConnectionStatus } from "@/lib/whatsapp-status";
import { WhatsAppIcon } from "./ChannelIcons";


function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isWhatsAppConnectionStatus(value: unknown): value is WhatsAppConnectionStatus {
    return value === "disabled" || value === "disconnected" || value === "connecting" || value === "connected" || value === "scanning";
}

const STEPS = ["Open WhatsApp on your phone", "Go to Settings, then Linked devices", "Tap Link a device and scan this code"];

export function WhatsAppConnectionSection() {
    const [status, setStatus] = useState<WhatsAppConnectionStatus>("disconnected");
    const [qrCode, setQrCode] = useState<string | null>(null);
    const [statusError, setStatusError] = useState<RequestErrorInfo | null>(null);
    const [checkNonce, setCheckNonce] = useState(0);
    const [resetNote, setResetNote] = useState<{ ok: boolean; text: string } | null>(null);

    useEffect(() => {
        const applyStatus = (payload: unknown) => {
            if (!isRecord(payload)) return;
            const nextStatus = payload.status;
            if (isWhatsAppConnectionStatus(nextStatus)) {
                setStatus(nextStatus);
            }
            const qr = payload.qr;
            if (typeof qr === "string" && qr) {
                setQrCode(qr);
                setStatus("scanning");
            } else if (nextStatus === "connected" || nextStatus === "disconnected") {
                setQrCode(null);
            }
        };

        const loadStatus = async () => {
            try {
                const response = await api.get("/api/whatsapp/status");
                applyStatus(response.data);
                setStatusError(null);
            } catch (error: unknown) {
                if (!axios.isAxiosError<unknown>(error) || error.response?.status !== 401) {
                    console.error("Failed to load WhatsApp status", error);
                }
                setStatusError(classifyRequestError(error, "WhatsApp status"));
            }
        };

        void loadStatus();
        const poll = window.setInterval(() => void loadStatus(), 3000);
        const wsUrl = API_BASE_URL.replace("http", "ws");
        const apiKey = localStorage.getItem("limebot_api_key");
        const socketUrl = new URL(`${wsUrl}/ws`, window.location.href);
        if (apiKey) {
            socketUrl.searchParams.set("api_key", apiKey);
        }
        socketUrl.protocol = socketUrl.protocol === "https:" ? "wss:" : socketUrl.protocol === "http:" ? "ws:" : socketUrl.protocol;
        const ws = new WebSocket(socketUrl.toString());

        ws.onerror = (event) => {
            console.error("Channels WS error", event);
            if (ws.readyState !== WebSocket.CLOSING && ws.readyState !== WebSocket.CLOSED) {
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
                    if (["disabled", "disconnected", "connecting", "connected", "scanning"].includes(newStatus)) {
                        setStatus(newStatus);
                    }
                    if (newStatus === "connected" || newStatus === "disconnected") {
                        setQrCode(null);
                    }
                }
            } catch (e) {
                console.error("Error parsing WS message", e);
            }
        };

        return () => {
            window.clearInterval(poll);
            ws.close();
        };
    }, [checkNonce]);

    const handleReset = async () => {
        setResetNote(null);
        try {
            const res = await axios.post(`${API_BASE_URL}/api/whatsapp/reset`);
            const data = res.data;
            if (data.status === "success") {
                setStatus("disconnected");
                setResetNote({ ok: true, text: data.message || "Session reset. A new QR code will appear when the bridge restarts." });
            } else {
                setResetNote({ ok: false, text: data.message || "The bridge could not reset the session." });
            }
        } catch (error) {
            setResetNote({ ok: false, text: classifyRequestError(error, "the WhatsApp bridge").title });
        }
    };

    const showQr = status !== "connected" && !!qrCode;
    const { state, label } = describeWhatsAppStatus(status, showQr, statusError !== null && !qrCode && status !== "connected");

    return (
        <section className="rounded-xl border border-border/60 bg-card/50">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/50 px-4 py-3">
                <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                    <WhatsAppIcon className="h-4 w-4 text-primary" />
                    Device link
                </div>
                <StatusPill state={state} label={label} />
            </div>

            <div className="px-4 py-5">
                {status === "connected" ? (
                    <div className="flex items-center gap-3">
                        <span className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
                            <WhatsAppIcon className="h-5 w-5" />
                        </span>
                        <div>
                            <p className="text-sm font-medium text-foreground">WhatsApp is linked</p>
                            <p className="text-xs text-muted-foreground">Approve who can talk to LimeBot in the contacts list below.</p>
                        </div>
                    </div>
                ) : showQr ? (
                    <div className="flex flex-col items-center gap-5 sm:flex-row sm:items-start">
                        <div className="shrink-0 rounded-xl bg-white p-3 shadow-sm" aria-label="WhatsApp pairing QR code">
                            <QRCodeCanvas value={qrCode as string} size={168} />
                        </div>
                        <ol className="space-y-2.5 text-sm">
                            {STEPS.map((step, index) => (
                                <li key={step} className="flex items-start gap-2.5">
                                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-border text-[10px] font-semibold text-muted-foreground">{index + 1}</span>
                                    <span className="text-foreground/90">{step}</span>
                                </li>
                            ))}
                            <li className="pl-7 text-xs text-muted-foreground">The code refreshes by itself. This page updates the moment you scan it.</li>
                        </ol>
                    </div>
                ) : statusError ? (
                    <div className="text-sm">
                        <p className="font-medium text-foreground">{statusError.title}</p>
                        <p className="mt-0.5 text-xs text-muted-foreground">{statusError.hint} The bridge is checked again every few seconds.</p>
                    </div>
                ) : (
                    <div className="text-sm">
                        <p className="font-medium text-foreground">
                            {status === "connecting" && "Starting the WhatsApp bridge…"}
                            {status === "scanning" && "Waiting for the QR code…"}
                            {status === "disabled" && "WhatsApp is switched off"}
                            {status === "disconnected" && "No phone linked yet"}
                        </p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                            {status === "disabled" || status === "disconnected"
                                ? "Turn the integration on above and save. A QR code appears here once the bridge is up."
                                : "This updates automatically."}
                        </p>
                    </div>
                )}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/50 px-4 py-2.5">
                <p className={cn("min-w-0 flex-1 text-[11px]", resetNote ? (resetNote.ok ? "text-primary" : "text-red-500") : "text-muted-foreground")} role={resetNote ? "status" : undefined}>
                    {resetNote ? resetNote.text : "Reset if pairing is stuck. This unlinks the current phone."}
                </p>
                <div className="flex items-center gap-1">
                    {statusError && (
                        <RowButton onClick={() => setCheckNonce((value) => value + 1)}>
                            <RotateCcw /> Check again
                        </RowButton>
                    )}
                    <InlineConfirm label="Reset session" icon={RotateCcw} prompt="Unlink phone?" confirmLabel="Reset" onConfirm={handleReset} />
                </div>
            </div>
        </section>
    );
}
