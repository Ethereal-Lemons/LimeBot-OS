export interface ConfigSecretInfo {
    configured: boolean;
    masked: string;
    last4?: string;
}

export type ConfigSecretsMap = Record<string, ConfigSecretInfo>;

export interface ConfigApiResponse<TEnv = Record<string, any>> {
    env?: TEnv;
    secrets?: ConfigSecretsMap;
}

const EMPTY_SECRET: ConfigSecretInfo = {
    configured: false,
    masked: "",
    last4: "",
};

export function getSecretInfo(secrets: ConfigSecretsMap | undefined, key: string): ConfigSecretInfo {
    return secrets?.[key] || EMPTY_SECRET;
}

export function getSecretPlaceholder(
    secrets: ConfigSecretsMap | undefined,
    key: string,
    fallback: string,
): string {
    const info = getSecretInfo(secrets, key);
    return info.configured ? `Stored (${info.masked || "configured"})` : fallback;
}
