import { useState } from "react";
import { Key, ArrowRight, ShieldAlert } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";

interface AuthKeyModalProps {
    isOpen: boolean;
    onSuccess: (key: string) => void;
}

export function AuthKeyModal({ isOpen, onSuccess }: AuthKeyModalProps) {
    const [key, setKey] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    const checkKey = async () => {
        setLoading(true);
        setError(null);
        try {
            await axios.get(`${API_BASE_URL}/api/identity`, {
                headers: { 'X-API-Key': key }
            });
            onSuccess(key);
        } catch (err: any) {
            if (err.response?.status === 401 || err.response?.status === 403) {
                setError("Invalid API Key. Please try again.");
            } else {
                setError("Failed to verify key. Check connection.");
            }
        } finally {
            setLoading(false);
        }
    };

    return (
        <Dialog open={isOpen} onOpenChange={() => { }}>
            <DialogContent className="sm:max-w-md bg-background/95 backdrop-blur border-primary/20">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2 text-xl">
                        <ShieldAlert className="w-6 h-6 text-primary" />
                        Authentication Required
                    </DialogTitle>
                    <DialogDescription>
                        This LimeBot instance is protected. Please enter the <strong>APP_API_KEY</strong> found in your <code>.env</code> file or configured during setup.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-4">
                    <div className="space-y-2">
                        <div className="relative">
                            <Key className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                            <Input
                                placeholder="Enter Access Key..."
                                type="password"
                                className="pl-9"
                                value={key}
                                onChange={(e) => {
                                    setKey(e.target.value);
                                    setError(null);
                                }}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter') checkKey();
                                }}
                            />
                        </div>
                        {error && (
                            <p className="text-sm text-destructive font-medium animate-in fade-in slide-in-from-top-1">
                                {error}
                            </p>
                        )}
                    </div>
                </div>

                <DialogFooter className="sm:justify-between gap-2">
                    <p className="text-[10px] text-muted-foreground self-center">
                        Don't have it? Check the host machine.
                    </p>
                    <Button onClick={checkKey} disabled={loading || !key} className="w-full sm:w-auto font-bold">
                        {loading ? "Verifying..." : (
                            <>
                                Access Bot <ArrowRight className="ml-2 h-4 w-4" />
                            </>
                        )}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
