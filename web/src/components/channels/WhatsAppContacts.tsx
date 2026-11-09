import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { api } from "@/lib/api";
import { Ban, Check, ChevronRight, RefreshCw, RotateCcw, ShieldCheck, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import { classifyRequestError, requestFailureMessage } from "@/lib/request-error";
import { toast } from "sonner";
import {
    countContactNames,
    groupWhatsAppContacts,
    type WhatsAppContactGroup,
    type WhatsAppIdentityMetadata,
} from "@/lib/whatsapp-contact-identity";
import { Chip, EmptyState, InlineConfirm, RequestError, RowButton, RowList, SectionLabel, SkeletonRows } from "@/components/ui/data-page";

interface ContactsData {
    allowed: string[];
    pending: string[];
    blocked: string[];
    identities?: Record<string, WhatsAppIdentityMetadata>;
}

type ContactAction = "approve" | "deny" | "unallow";

const countryName = (country: string | null) => {
    if (!country) return null;
    try {
        return new Intl.DisplayNames([navigator.language || "en"], { type: "region" }).of(country) || country;
    } catch {
        return country;
    }
};

function ContactIdentity({ group, sameNameCount = 1, muted = false }: { group: WhatsAppContactGroup; sameNameCount?: number; muted?: boolean }) {
    const { identity, ids } = group;
    const region = countryName(identity.country);
    return (
        <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className={cn("truncate text-sm font-medium", muted ? "text-muted-foreground" : "text-foreground")}>{identity.displayName}</span>
                {identity.isVerifiedName && (
                    <Chip tone="primary">
                        <ShieldCheck className="h-3 w-3" />
                        Verified
                    </Chip>
                )}
                {sameNameCount > 1 && <Chip tone="amber">{sameNameCount} share this name</Chip>}
                {ids.length > 1 && <Chip>{ids.length} linked records</Chip>}
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                <span className={cn("font-mono", muted && "line-through")}>{identity.formattedNumber || "Phone number unavailable"}</span>
                {region && <span>· {region}</span>}
            </div>
            <details className="group/details mt-1 text-[11px] text-muted-foreground">
                <summary className="flex w-fit cursor-pointer list-none items-center gap-1 hover:text-foreground">
                    <ChevronRight className="h-3 w-3 transition-transform group-open/details:rotate-90" />
                    Technical details
                </summary>
                <div className="mt-1.5 space-y-1 break-all rounded-lg border border-border/50 bg-background/60 p-2 font-mono text-[10px]">
                    {identity.phoneNumber && <div>Canonical number: {identity.phoneNumber}</div>}
                    {identity.technicalId && <div>WhatsApp account ID: {identity.technicalId}</div>}
                    {ids.length > 1 && <div>Linked records: {ids.join(", ")}</div>}
                </div>
            </details>
        </div>
    );
}

export function WhatsAppContacts() {
    const [contacts, setContacts] = useState<ContactsData>({ allowed: [], pending: [], blocked: [] });
    const [loaded, setLoaded] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<unknown>(null);
    const [busyGroups, setBusyGroups] = useState<string[]>([]);
    const [showBlocked, setShowBlocked] = useState(false);

    const fetchContacts = useCallback(async (silent = false) => {
        if (!silent) setLoading(true);
        try {
            const res = await api.get("/api/whatsapp/contacts");
            setContacts(res.data);
            setLoaded(true);
            setError(null);
        } catch (err) {
            if (!(axios.isAxiosError(err) && err.response?.status === 401)) {
                setError(err);
            }
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void fetchContacts();
        const interval = setInterval(() => void fetchContacts(true), 5000);
        return () => clearInterval(interval);
    }, [fetchContacts]);

    const pendingGroups = useMemo(() => groupWhatsAppContacts(contacts.pending, contacts.identities), [contacts.pending, contacts.identities]);
    const allowedGroups = useMemo(() => groupWhatsAppContacts(contacts.allowed, contacts.identities), [contacts.allowed, contacts.identities]);
    const blockedGroups = useMemo(() => groupWhatsAppContacts(contacts.blocked, contacts.identities), [contacts.blocked, contacts.identities]);
    const nameCounts = useMemo(() => countContactNames([...pendingGroups, ...allowedGroups, ...blockedGroups]), [pendingGroups, allowedGroups, blockedGroups]);
    const sameNameCount = (group: WhatsAppContactGroup) => nameCounts.get(group.identity.displayName.trim().toLocaleLowerCase()) || 1;

    const handleAction = async (action: ContactAction, group: WhatsAppContactGroup) => {
        setBusyGroups((current) => [...current, group.key]);
        try {
            let latest: ContactsData | null = null;
            for (const chatId of group.ids) {
                const res = await api.post(`/api/whatsapp/contacts/${action}`, { chat_id: chatId });
                if (res.data.status === "success" && res.data.contacts) latest = res.data.contacts;
            }
            if (latest) setContacts(latest);
        } catch (err) {
            console.error(err);
            const verb = action === "approve" ? "approve" : action === "deny" ? "block" : "move";
            toast.error(requestFailureMessage(`Could not ${verb} ${group.identity.displayName}`, err));
        } finally {
            setBusyGroups((current) => current.filter((key) => key !== group.key));
        }
    };

    const blockButton = (group: WhatsAppContactGroup) => (
        <InlineConfirm
            label="Block"
            icon={Ban}
            prompt="Block contact?"
            confirmLabel="Block"
            disabled={busyGroups.includes(group.key)}
            onConfirm={() => handleAction("deny", group)}
        />
    );

    const info = error ? classifyRequestError(error, "WhatsApp contacts") : null;

    return (
        <section className="space-y-5 rounded-xl border border-border/60 bg-card/50 p-4">
            <div className="flex items-start justify-between gap-3">
                <div>
                    <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                        <Users className="h-4 w-4 text-muted-foreground" />
                        Contacts
                    </h3>
                    <p className="mt-0.5 text-xs text-muted-foreground">Only approved numbers can talk to LimeBot on WhatsApp.</p>
                </div>
                <RowButton onClick={() => void fetchContacts()} disabled={loading} aria-label="Refresh contacts" title="Refresh">
                    <RefreshCw className={cn(loading && "animate-spin")} />
                </RowButton>
            </div>

            {info && <RequestError compact={loaded} info={info} onRetry={() => void fetchContacts()} retrying={loading} />}

            {!loaded && !info ? (
                <SkeletonRows count={2} />
            ) : (
                loaded && (
                    <>
                        <div className="space-y-2">
                            <SectionLabel action={pendingGroups.length > 0 ? <Chip tone="amber">{pendingGroups.length} waiting</Chip> : undefined}>Pending requests</SectionLabel>
                            {pendingGroups.length === 0 ? (
                                <p className="rounded-lg border border-dashed border-border/70 px-4 py-4 text-center text-xs text-muted-foreground">
                                    No one is waiting. New numbers that message LimeBot show up here for approval.
                                </p>
                            ) : (
                                <RowList className="border-amber-400/30">
                                    {pendingGroups.map((group) => (
                                        <div key={group.key} className="flex flex-col gap-2.5 bg-amber-500/[0.04] px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
                                            <ContactIdentity group={group} sameNameCount={sameNameCount(group)} />
                                            <div className="flex shrink-0 items-center gap-1.5">
                                                <button
                                                    type="button"
                                                    onClick={() => void handleAction("approve", group)}
                                                    disabled={busyGroups.includes(group.key)}
                                                    className="inline-flex h-7 items-center gap-1.5 rounded-md bg-primary px-3 text-[11px] font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                                                >
                                                    <Check className="h-3.5 w-3.5" /> Approve
                                                </button>
                                                {blockButton(group)}
                                            </div>
                                        </div>
                                    ))}
                                </RowList>
                            )}
                        </div>

                        <div className="space-y-2">
                            <SectionLabel action={<span className="text-[10px] text-muted-foreground">{allowedGroups.length}</span>}>Approved</SectionLabel>
                            {allowedGroups.length === 0 ? (
                                <EmptyState icon={Users} title="No approved contacts yet" hint="Approve a pending request to let that number chat with LimeBot." className="py-6" />
                            ) : (
                                <RowList className="max-h-[22rem] overflow-y-auto">
                                    {allowedGroups.map((group) => (
                                        <div key={group.key} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
                                            <ContactIdentity group={group} sameNameCount={sameNameCount(group)} />
                                            <div className="flex shrink-0 items-center gap-1">
                                                <RowButton onClick={() => void handleAction("unallow", group)} disabled={busyGroups.includes(group.key)} title="Move back to pending">
                                                    <RotateCcw /> Revoke
                                                </RowButton>
                                                {blockButton(group)}
                                            </div>
                                        </div>
                                    ))}
                                </RowList>
                            )}
                        </div>

                        {blockedGroups.length > 0 && (
                            <div className="space-y-2">
                                <button
                                    type="button"
                                    onClick={() => setShowBlocked((value) => !value)}
                                    aria-expanded={showBlocked}
                                    className="flex w-full items-center gap-1.5 text-left text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80 hover:text-foreground"
                                >
                                    <ChevronRight className={cn("h-3 w-3 transition-transform", showBlocked && "rotate-90")} />
                                    Blocked
                                    <span className="font-normal">{blockedGroups.length}</span>
                                </button>
                                {showBlocked && (
                                    <RowList className="max-h-[16rem] overflow-y-auto bg-muted/20">
                                        {blockedGroups.map((group) => (
                                            <div key={group.key} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
                                                <ContactIdentity group={group} sameNameCount={sameNameCount(group)} muted />
                                                <RowButton tone="primary" className="shrink-0" onClick={() => void handleAction("approve", group)} disabled={busyGroups.includes(group.key)}>
                                                    <Check /> Unblock
                                                </RowButton>
                                            </div>
                                        ))}
                                    </RowList>
                                )}
                            </div>
                        )}
                    </>
                )
            )}
        </section>
    );
}
