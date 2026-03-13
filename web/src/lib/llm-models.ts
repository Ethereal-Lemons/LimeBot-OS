export type LlmModelOption = {
  id: string;
  name: string;
  provider: string;
};

export const DEFAULT_MODEL_BY_PROVIDER: Record<string, string> = {
  gemini: "gemini/gemini-3.1-flash-lite-preview",
  openai: "openai/gpt-4o",
  anthropic: "anthropic/claude-3-7-sonnet-20250219",
  xai: "xai/grok-2-1212",
  deepseek: "deepseek/deepseek-v3.2",
  qwen: "qwen/qwen-plus",
  nvidia: "nvidia/moonshotai/kimi-k2-instruct",
};

export const PROVIDER_LABELS: Record<string, string> = {
  gemini: "Google Gemini",
  openai: "OpenAI",
  anthropic: "Anthropic Claude",
  xai: "xAI (Grok)",
  deepseek: "DeepSeek",
  qwen: "Qwen (DashScope)",
  nvidia: "NVIDIA",
  custom: "Custom / Local",
};

const FEATURED_MODEL_IDS_BY_PROVIDER: Record<string, string[]> = {
  gemini: [
    "gemini/gemini-3.1-flash-lite-preview",
    "gemini/gemini-2.0-flash",
    "gemini/gemini-1.5-flash",
  ],
  openai: ["openai/gpt-4o", "openai/gpt-4o-mini"],
  anthropic: [
    "anthropic/claude-3-7-sonnet-20250219",
    "anthropic/claude-3-5-sonnet-20241022",
  ],
  xai: ["xai/grok-2-1212"],
  deepseek: ["deepseek/deepseek-v3.2", "deepseek/deepseek-chat"],
  qwen: ["qwen/qwen-plus", "qwen/qwen-max", "qwen/qwen-flash"],
  nvidia: [
    "nvidia/moonshotai/kimi-k2-instruct",
    "nvidia/openai/gpt-oss-120b",
    "nvidia/meta/llama-4-maverick-17b-128e-instruct",
    "nvidia/qwen/qwen3-next-80b-a3b-instruct",
    "nvidia/deepseek-ai/deepseek-v3.2",
    "nvidia/meta/llama-3.3-70b-instruct",
  ],
};

export function getModelProvider(modelId?: string): string {
  const normalized = String(modelId || "").trim();
  if (!normalized.includes("/")) {
    return "custom";
  }
  return normalized.split("/")[0] || "custom";
}

export function getProviderModels(
  models: LlmModelOption[],
  provider: string,
): LlmModelOption[] {
  const seen = new Set<string>();
  return models
    .filter((model) => model.provider === provider)
    .sort((a, b) => a.name.localeCompare(b.name))
    .filter((model) => {
      if (seen.has(model.id)) {
        return false;
      }
      seen.add(model.id);
      return true;
    });
}

export function getRecommendedModels(
  models: LlmModelOption[],
  provider: string,
): LlmModelOption[] {
  const providerModels = getProviderModels(models, provider);
  const byId = new Map(providerModels.map((model) => [model.id, model]));
  const orderedIds = FEATURED_MODEL_IDS_BY_PROVIDER[provider] || [];

  const picks = orderedIds
    .map((id) => byId.get(id))
    .filter((model): model is LlmModelOption => Boolean(model));

  if (picks.length > 0) {
    return picks;
  }

  return providerModels.slice(0, 6);
}

export function getAdditionalModels(
  models: LlmModelOption[],
  provider: string,
): LlmModelOption[] {
  const recommendedIds = new Set(
    getRecommendedModels(models, provider).map((model) => model.id),
  );
  return getProviderModels(models, provider).filter(
    (model) => !recommendedIds.has(model.id),
  );
}

export function getInitialModelForProvider(
  models: LlmModelOption[],
  provider: string,
): string {
  const recommended = getRecommendedModels(models, provider);
  if (recommended.length > 0) {
    return recommended[0].id;
  }

  const providerModels = getProviderModels(models, provider);
  if (providerModels.length > 0) {
    return providerModels[0].id;
  }

  return DEFAULT_MODEL_BY_PROVIDER[provider] || "";
}

export function getVisibleModels(
  models: LlmModelOption[],
  provider: string,
  selectedModelId: string,
  showAll: boolean,
): LlmModelOption[] {
  const recommended = getRecommendedModels(models, provider);
  const additional = getAdditionalModels(models, provider);
  const visible = showAll ? [...recommended, ...additional] : [...recommended];

  if (
    selectedModelId &&
    !visible.some((model) => model.id === selectedModelId)
  ) {
    const selected = getProviderModels(models, provider).find(
      (model) => model.id === selectedModelId,
    );
    if (selected) {
      visible.unshift(selected);
    }
  }

  const seen = new Set<string>();
  return visible.filter((model) => {
    if (seen.has(model.id)) {
      return false;
    }
    seen.add(model.id);
    return true;
  });
}
