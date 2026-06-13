import { useEffect, useState } from "react";

type CompanionIdentity = {
  name: string;
  avatar: string | null;
};

const DEFAULT_IDENTITY: CompanionIdentity = {
  name: "LimeBot",
  avatar: null,
};

function normalizeAvatarUrl(apiBaseUrl: string, avatar: unknown) {
  if (typeof avatar !== "string") return null;
  const value = avatar.trim();
  if (!value) return null;
  if (/^(https?:|data:|blob:)/i.test(value)) return value;
  const base = apiBaseUrl.replace(/\/+$/, "");
  if (value.startsWith("/")) return `${base}${value}`;
  return `${base}/${value}`;
}

export function useCompanionIdentity(apiBaseUrl: string, apiKey?: string) {
  const [identity, setIdentity] = useState<CompanionIdentity>(DEFAULT_IDENTITY);

  useEffect(() => {
    if (!apiBaseUrl) {
      setIdentity(DEFAULT_IDENTITY);
      return;
    }

    let cancelled = false;

    const refreshIdentity = async () => {
      try {
        const response = await fetch(`${apiBaseUrl.replace(/\/+$/, "")}/api/identity`, {
          headers: apiKey ? { "X-API-Key": apiKey } : undefined,
        });

        if (!response.ok) {
          throw new Error(`Identity request failed with status ${response.status}`);
        }

        const data = (await response.json()) as Record<string, unknown>;
        if (cancelled) return;

        setIdentity({
          name: typeof data.name === "string" && data.name.trim() ? data.name.trim() : "LimeBot",
          avatar: normalizeAvatarUrl(apiBaseUrl, data.avatar),
        });
      } catch (error) {
        if (!cancelled) {
          console.error("Failed to load LimeBot identity for the extension companion", error);
          setIdentity(DEFAULT_IDENTITY);
        }
      }
    };

    void refreshIdentity();
    const interval = window.setInterval(refreshIdentity, 15000);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [apiBaseUrl, apiKey]);

  return identity;
}
