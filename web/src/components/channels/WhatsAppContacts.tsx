import { useState, useEffect } from 'react';
import axios from 'axios';
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { RefreshCw, Check, X, Ban } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";

interface ContactsData {
    allowed: string[];
    pending: string[];
    blocked: string[];
    identities?: Record<string, { push_name?: string; verified_name?: string; alt_id?: string }>;
}

export function WhatsAppContacts() {
    const [contacts, setContacts] = useState<ContactsData>({ allowed: [], pending: [], blocked: [] });
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const fetchContacts = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.get('/api/whatsapp/contacts');
            setContacts(res.data);
        } catch (err) {
            if (axios.isAxiosError(err) && err.response?.status === 401) {
                // Ignore 401 spam
            } else {
                setError(err instanceof Error ? err.message : 'Unknown error');
            }
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchContacts();
        // Poll every 5 seconds for new requests
        const interval = setInterval(fetchContacts, 5000);
        return () => clearInterval(interval);
    }, []);

    const handleAction = async (action: 'approve' | 'deny' | 'unallow', chatId: string) => {
        try {
            const res = await api.post(`/api/whatsapp/contacts/${action}`, {
                chat_id: chatId
            });

            const data = res.data;
            if (data.status === 'success' && data.contacts) {
                setContacts(data.contacts);
            }
        } catch (err) {
            console.error(err);
            setError(`Failed to ${action} contact`);
        }
    };

    const getDisplayName = (id: string) => {
        const identity = contacts.identities?.[id];
        if (identity?.push_name) return identity.push_name;
        if (identity?.verified_name) return identity.verified_name;
        return null;
    };

    const getAltDisplay = (id: string) => {
        const alt = contacts.identities?.[id]?.alt_id;
        if (!alt) return null;
        // Strip suffix if it's there
        return alt.split('@')[0];
    };

    return (
        <Card className="mt-6">
            <CardHeader>
                <div className="flex items-center justify-between">
                    <div>
                        <CardTitle>WhatsApp Contacts</CardTitle>
                        <CardDescription>Manage allowed and pending contacts</CardDescription>
                    </div>
                    <Button variant="ghost" size="icon" onClick={fetchContacts} disabled={loading}>
                        <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                    </Button>
                </div>
            </CardHeader>
            <CardContent>
                {error && (
                    <div className="mb-4 p-2 bg-destructive/10 text-destructive text-sm rounded">
                        {error}
                    </div>
                )}

                <div className="space-y-6">
                    {/* Pending Requests */}
                    <div>
                        <h3 className="text-sm font-medium mb-2 flex items-center gap-2">
                            Pending Requests
                            {contacts.pending.length > 0 && (
                                <Badge variant="destructive" className="h-5 px-1.5 min-w-5 flex justify-center">{contacts.pending.length}</Badge>
                            )}
                        </h3>
                        {contacts.pending.length === 0 ? (
                            <div className="text-sm text-muted-foreground italic">No pending requests</div>
                        ) : (
                            <div className="space-y-2">
                                {contacts.pending.map(id => (
                                    <div key={id} className="flex items-center justify-between p-2 rounded-md border bg-card">
                                        <div className="flex flex-col">
                                            {getDisplayName(id) && (
                                                <span className="font-semibold text-sm">{getDisplayName(id)}</span>
                                            )}
                                            <div className="flex items-center gap-1">
                                                <span className="font-mono text-xs text-muted-foreground">{id}</span>
                                                {getAltDisplay(id) && (
                                                    <Badge variant="outline" className="text-[10px] h-4 px-1">{getAltDisplay(id)}</Badge>
                                                )}
                                            </div>
                                        </div>
                                        <div className="flex gap-2">
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="h-8 w-8 p-0 text-green-600 border-green-200 hover:bg-green-50 hover:text-green-700 dark:border-green-800 dark:hover:bg-green-900/30"
                                                onClick={() => handleAction('approve', id)}
                                                title="Approve"
                                            >
                                                <Check className="h-4 w-4" />
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="h-8 w-8 p-0 text-red-600 border-red-200 hover:bg-red-50 hover:text-red-700 dark:border-red-800 dark:hover:bg-red-900/30"
                                                onClick={() => handleAction('deny', id)}
                                                title="Deny"
                                            >
                                                <X className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Allowed Contacts */}
                    <div>
                        <h3 className="text-sm font-medium mb-2">Allowed Contacts ({contacts.allowed.length})</h3>
                        <ScrollArea className="h-[200px] rounded-md border p-4">
                            {contacts.allowed.length === 0 ? (
                                <div className="text-sm text-muted-foreground italic">No allowed contacts</div>
                            ) : (
                                <div className="space-y-2">
                                    {contacts.allowed.map(id => (
                                        <div key={id} className="flex items-center justify-between p-2 hover:bg-muted/30 rounded-md">
                                            <div className="flex flex-col">
                                                {getDisplayName(id) && (
                                                    <span className="font-semibold text-sm">{getDisplayName(id)}</span>
                                                )}
                                                <div className="flex items-center gap-1">
                                                    <span className="font-mono text-xs text-muted-foreground">{id}</span>
                                                    {getAltDisplay(id) && (
                                                        <Badge variant="outline" className="text-[10px] h-4 px-1">{getAltDisplay(id)}</Badge>
                                                    )}
                                                </div>
                                            </div>
                                            <div className="flex gap-1">
                                                <Button
                                                    size="sm"
                                                    variant="ghost"
                                                    className="h-8 w-8 p-0 text-muted-foreground hover:text-blue-600"
                                                    onClick={() => handleAction('unallow', id)}
                                                    title="Move back to Pending"
                                                >
                                                    <RefreshCw className="h-4 w-4" />
                                                </Button>
                                                <Button
                                                    size="sm"
                                                    variant="ghost"
                                                    className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                                                    onClick={() => handleAction('deny', id)}
                                                    title="Block"
                                                >
                                                    <Ban className="h-4 w-4" />
                                                </Button>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </ScrollArea>
                    </div>

                    {/* Blocked Contacts (Collapsible or simple list) */}
                    {contacts.blocked.length > 0 && (
                        <div>
                            <h3 className="text-sm font-medium mb-2 text-muted-foreground">Blocked Contacts ({contacts.blocked.length})</h3>
                            <ScrollArea className="h-[100px] rounded-md border p-4 bg-muted/50">
                                <div className="space-y-2">
                                    {contacts.blocked.map(id => (
                                        <div key={id} className="flex items-center justify-between">
                                            <span className="font-mono text-sm text-muted-foreground line-through">{id}</span>
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                className="h-8 w-8 p-0 text-muted-foreground hover:text-green-600"
                                                onClick={() => handleAction('approve', id)}
                                                title="Unblock (Approve)"
                                            >
                                                <Check className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    ))}
                                </div>
                            </ScrollArea>
                        </div>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
