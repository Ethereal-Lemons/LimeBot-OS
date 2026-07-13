import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Ban, Check, ChevronDown, RefreshCw, RotateCcw, ShieldCheck, X } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
    countContactNames,
    groupWhatsAppContacts,
    type WhatsAppContactGroup,
    type WhatsAppIdentityMetadata,
} from "@/lib/whatsapp-contact-identity";

interface ContactsData {
    allowed: string[];
    pending: string[];
    blocked: string[];
    identities?: Record<string, WhatsAppIdentityMetadata>;
}

const countryName = (country: string | null) => {
    if (!country) return null;
    try {
        return new Intl.DisplayNames([navigator.language || 'en'], { type: 'region' }).of(country) || country;
    } catch {
        return country;
    }
};

function ContactIdentity({ group, sameNameCount = 1, muted = false }: { group: WhatsAppContactGroup; sameNameCount?: number; muted?: boolean }) {
    const { identity, ids } = group;
    const region = countryName(identity.country);
    return (
        <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
                <span className={`font-semibold ${muted ? 'text-muted-foreground' : 'text-foreground'}`}>{identity.displayName}</span>
                {identity.isVerifiedName && <Badge variant="outline" className="border-primary/30 text-[10px] text-primary"><ShieldCheck className="mr-1 h-3 w-3" />Verified</Badge>}
                {sameNameCount > 1 && <Badge variant="secondary" className="text-[10px]">{sameNameCount} contacts share this name</Badge>}
                {ids.length > 1 && <Badge variant="secondary" className="text-[10px]">{ids.length} linked records</Badge>}
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <span className={`font-mono text-sm ${muted ? 'text-muted-foreground line-through' : 'text-foreground/90'}`}>
                    {identity.formattedNumber || 'Phone number unavailable'}
                </span>
                {region && <Badge variant="outline" className="text-[10px]">{region}</Badge>}
            </div>
            <details className="group/details text-xs text-muted-foreground">
                <summary className="flex w-fit cursor-pointer list-none items-center gap-1 hover:text-foreground">
                    <ChevronDown className="h-3 w-3 transition-transform group-open/details:rotate-180" />
                    Technical details
                </summary>
                <div className="mt-2 space-y-1 rounded-lg border border-border/50 bg-muted/20 p-2 font-mono text-[10px] break-all">
                    {identity.phoneNumber && <div>Canonical number: {identity.phoneNumber}</div>}
                    {identity.technicalId && <div>WhatsApp account ID: {identity.technicalId}</div>}
                    {ids.length > 1 && <div>Linked records: {ids.join(', ')}</div>}
                </div>
            </details>
        </div>
    );
}

export function WhatsAppContacts() {
    const [contacts, setContacts] = useState<ContactsData>({ allowed: [], pending: [], blocked: [] });
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [busyGroups, setBusyGroups] = useState<string[]>([]);

    const fetchContacts = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.get('/api/whatsapp/contacts');
            setContacts(res.data);
        } catch (err) {
            if (!(axios.isAxiosError(err) && err.response?.status === 401)) {
                setError(err instanceof Error ? err.message : 'Unknown error');
            }
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchContacts();
        const interval = setInterval(fetchContacts, 5000);
        return () => clearInterval(interval);
    }, []);

    const pendingGroups = useMemo(() => groupWhatsAppContacts(contacts.pending, contacts.identities), [contacts.pending, contacts.identities]);
    const allowedGroups = useMemo(() => groupWhatsAppContacts(contacts.allowed, contacts.identities), [contacts.allowed, contacts.identities]);
    const blockedGroups = useMemo(() => groupWhatsAppContacts(contacts.blocked, contacts.identities), [contacts.blocked, contacts.identities]);
    const nameCounts = useMemo(() => countContactNames([...pendingGroups, ...allowedGroups, ...blockedGroups]), [pendingGroups, allowedGroups, blockedGroups]);

    const handleAction = async (action: 'approve' | 'deny' | 'unallow', group: WhatsAppContactGroup) => {
        setBusyGroups((current) => [...current, group.key]);
        setError(null);
        try {
            let latest: ContactsData | null = null;
            for (const chatId of group.ids) {
                const res = await api.post(`/api/whatsapp/contacts/${action}`, { chat_id: chatId });
                if (res.data.status === 'success' && res.data.contacts) latest = res.data.contacts;
            }
            if (latest) setContacts(latest);
        } catch (err) {
            console.error(err);
            setError(`Failed to ${action} ${group.identity.displayName}`);
        } finally {
            setBusyGroups((current) => current.filter((key) => key !== group.key));
        }
    };

    const sameNameCount = (group: WhatsAppContactGroup) => nameCounts.get(group.identity.displayName.trim().toLocaleLowerCase()) || 1;

    return (
        <Card className="mt-6">
            <CardHeader>
                <div className="flex items-start justify-between gap-4">
                    <div>
                        <CardTitle>WhatsApp Contacts</CardTitle>
                        <CardDescription className="mt-1">
                            Numbers are normalized internationally. WhatsApp LIDs stay hidden under technical details.
                        </CardDescription>
                    </div>
                    <Button variant="ghost" size="icon" onClick={fetchContacts} disabled={loading} title="Refresh contacts">
                        <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                    </Button>
                </div>
            </CardHeader>
            <CardContent className="space-y-7">
                {error && <div className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}

                <section>
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                        <h3 className="text-sm font-semibold">Pending requests</h3>
                        {pendingGroups.length > 0 && <Badge variant="destructive">{pendingGroups.length}</Badge>}
                        {contacts.pending.length !== pendingGroups.length && <span className="text-xs text-muted-foreground">from {contacts.pending.length} WhatsApp records</span>}
                    </div>
                    {pendingGroups.length === 0 ? (
                        <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">No pending requests</div>
                    ) : (
                        <div className="space-y-2">
                            {pendingGroups.map((group) => {
                                const busy = busyGroups.includes(group.key);
                                return (
                                    <div key={group.key} className="flex flex-col gap-4 rounded-xl border bg-card p-4 md:flex-row md:items-center md:justify-between">
                                        <ContactIdentity group={group} sameNameCount={sameNameCount(group)} />
                                        <div className="flex shrink-0 gap-2">
                                            <Button size="sm" variant="outline" className="border-primary/30 text-primary hover:bg-primary/10 hover:text-primary" onClick={() => handleAction('approve', group)} disabled={busy}>
                                                <Check className="mr-1.5 h-4 w-4" />Approve
                                            </Button>
                                            <Button size="sm" variant="outline" className="border-destructive/30 text-destructive hover:bg-destructive/10" onClick={() => handleAction('deny', group)} disabled={busy}>
                                                <X className="mr-1.5 h-4 w-4" />Block
                                            </Button>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </section>

                <section>
                    <h3 className="mb-3 text-sm font-semibold">Allowed contacts ({allowedGroups.length})</h3>
                    <ScrollArea className="h-[260px] rounded-xl border">
                        {allowedGroups.length === 0 ? (
                            <div className="p-5 text-sm text-muted-foreground">No allowed contacts</div>
                        ) : (
                            <div className="divide-y divide-border/60">
                                {allowedGroups.map((group) => {
                                    const busy = busyGroups.includes(group.key);
                                    return (
                                        <div key={group.key} className="flex flex-col gap-4 p-4 md:flex-row md:items-center md:justify-between">
                                            <ContactIdentity group={group} sameNameCount={sameNameCount(group)} />
                                            <div className="flex shrink-0 gap-2">
                                                <Button size="sm" variant="ghost" onClick={() => handleAction('unallow', group)} disabled={busy} title="Return to pending">
                                                    <RotateCcw className="mr-1.5 h-4 w-4" />Pending
                                                </Button>
                                                <Button size="sm" variant="ghost" className="text-destructive hover:bg-destructive/10 hover:text-destructive" onClick={() => handleAction('deny', group)} disabled={busy} title="Block contact">
                                                    <Ban className="mr-1.5 h-4 w-4" />Block
                                                </Button>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </ScrollArea>
                </section>

                {blockedGroups.length > 0 && (
                    <section>
                        <h3 className="mb-3 text-sm font-semibold text-muted-foreground">Blocked contacts ({blockedGroups.length})</h3>
                        <ScrollArea className="h-[180px] rounded-xl border bg-muted/20">
                            <div className="divide-y divide-border/60">
                                {blockedGroups.map((group) => (
                                    <div key={group.key} className="flex flex-col gap-4 p-4 md:flex-row md:items-center md:justify-between">
                                        <ContactIdentity group={group} sameNameCount={sameNameCount(group)} muted />
                                        <Button size="sm" variant="ghost" className="shrink-0 text-primary hover:bg-primary/10 hover:text-primary" onClick={() => handleAction('approve', group)} disabled={busyGroups.includes(group.key)}>
                                            <Check className="mr-1.5 h-4 w-4" />Unblock
                                        </Button>
                                    </div>
                                ))}
                            </div>
                        </ScrollArea>
                    </section>
                )}
            </CardContent>
        </Card>
    );
}
