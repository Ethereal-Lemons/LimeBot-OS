import { parsePhoneNumberFromString } from 'libphonenumber-js';

export interface WhatsAppIdentityMetadata {
    push_name?: string;
    verified_name?: string;
    alt_id?: string;
}

export interface WhatsAppContactIdentity {
    id: string;
    displayName: string;
    phoneNumber: string | null;
    formattedNumber: string | null;
    country: string | null;
    countryCallingCode: string | null;
    technicalId: string | null;
    isVerifiedName: boolean;
}

export interface WhatsAppContactGroup {
    key: string;
    ids: string[];
    identity: WhatsAppContactIdentity;
}

const digitsFromId = (value?: string) => String(value || '').split('@', 1)[0].replace(/\D/g, '');

const parseCandidate = (value?: string) => {
    const digits = digitsFromId(value);
    if (digits.length < 7 || digits.length > 15) return null;
    const parsed = parsePhoneNumberFromString(`+${digits}`);
    return parsed?.isValid() ? parsed : null;
};

export function describeWhatsAppContact(id: string, metadata: WhatsAppIdentityMetadata = {}): WhatsAppContactIdentity {
    const alternatePhone = parseCandidate(metadata.alt_id);
    const primaryPhone = parseCandidate(id);
    const phone = alternatePhone || primaryPhone;
    const primaryDigits = digitsFromId(id);
    const phoneDigits = phone?.number.replace('+', '') || null;
    const technicalId = !phone || primaryDigits !== phoneDigits ? id : null;

    return {
        id,
        displayName: metadata.verified_name || metadata.push_name || 'Unknown contact',
        phoneNumber: phone?.number || null,
        formattedNumber: phone?.formatInternational() || null,
        country: phone?.country || null,
        countryCallingCode: phone?.countryCallingCode || null,
        technicalId,
        isVerifiedName: Boolean(metadata.verified_name),
    };
}

export function groupWhatsAppContacts(
    ids: string[],
    identities: Record<string, WhatsAppIdentityMetadata> = {},
): WhatsAppContactGroup[] {
    const groups = new Map<string, WhatsAppContactGroup>();
    for (const id of ids) {
        const identity = describeWhatsAppContact(id, identities[id]);
        const key = identity.phoneNumber || `id:${id}`;
        const existing = groups.get(key);
        if (existing) {
            existing.ids.push(id);
            if (existing.identity.displayName === 'Unknown contact' && identity.displayName !== 'Unknown contact') {
                existing.identity = identity;
            }
        } else {
            groups.set(key, { key, ids: [id], identity });
        }
    }
    return [...groups.values()];
}

export function countContactNames(groups: WhatsAppContactGroup[]): Map<string, number> {
    const counts = new Map<string, number>();
    for (const group of groups) {
        const normalized = group.identity.displayName.trim().toLocaleLowerCase();
        if (!normalized || normalized === 'unknown contact') continue;
        counts.set(normalized, (counts.get(normalized) || 0) + 1);
    }
    return counts;
}
