export interface AgentProfilePolicy {
    allowed_tools: string[] | null;
    allowed_paths: string[] | null;
    approval_policy: string | null;
}

export interface AgentProfile {
    profile_id: string;
    display_name: string;
    model: string | null;
    resolved_model: string;
    archived: boolean;
    policy: AgentProfilePolicy;
    created_at: number;
    updated_at: number;
}

export const ACTIVE_PROFILE_STORAGE_KEY = "limebot_active_profile";

export function getStoredProfileId(): string {
    try {
        return sessionStorage.getItem(ACTIVE_PROFILE_STORAGE_KEY) || "default";
    } catch {
        return "default";
    }
}

export function storeProfileId(profileId: string): void {
    try {
        sessionStorage.setItem(ACTIVE_PROFILE_STORAGE_KEY, profileId || "default");
    } catch {
        // The current tab still keeps the selection when storage is unavailable.
    }
}
