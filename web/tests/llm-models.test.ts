import test from "node:test";
import { strict as assert } from "node:assert";

import {
  DEFAULT_MODEL_BY_PROVIDER,
  getAdditionalModels,
  getRecommendedModels,
  type LlmModelOption,
} from "../src/lib/llm-models.js";

const models: LlmModelOption[] = [
  { id: "openai/gpt-6-astra", name: "GPT-6 Astra", provider: "openai" },
  { id: "openai/gpt-6.1-sol", name: "GPT-6.1 Sol", provider: "openai" },
  { id: "openai/gpt-6.1-terra", name: "GPT-6.1 Terra", provider: "openai" },
  { id: "openai/gpt-6-luna", name: "GPT-6 Luna", provider: "openai" },
];

test("OpenAI recommends exact live API model IDs in capability order", () => {
  assert.equal(DEFAULT_MODEL_BY_PROVIDER.openai, "openai/gpt-6.1-sol");
  assert.deepEqual(
    getRecommendedModels(models, "openai").map((model) => model.id),
    [
      "openai/gpt-6.1-sol",
      "openai/gpt-6-astra",
      "openai/gpt-6.1-terra",
      "openai/gpt-6-luna",
    ],
  );
  assert.deepEqual(getAdditionalModels(models, "openai").map((model) => model.id), []);
});

test("Anthropic recommends active direct API model IDs", () => {
  const activeIds = [
    "anthropic/claude-opus-5",
    "anthropic/claude-sonnet-5",
    "anthropic/claude-fable-5",
    "anthropic/claude-opus-4-8",
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-opus-4-7",
    "anthropic/claude-opus-4-6",
    "anthropic/claude-haiku-4-5-20251001",
    "anthropic/claude-sonnet-4-5-20250929",
    "anthropic/claude-opus-4-5-20251101",
  ];
  const anthropicModels = activeIds.map((id) => ({
    id,
    name: id,
    provider: "anthropic",
  }));

  assert.equal(DEFAULT_MODEL_BY_PROVIDER.anthropic, "anthropic/claude-opus-5");
  assert.deepEqual(
    getRecommendedModels(anthropicModels, "anthropic").map((model) => model.id),
    activeIds,
  );
});

test("DeepSeek recommends the current V4 API models first", () => {
  const deepseekModels: LlmModelOption[] = [
    { id: "deepseek/deepseek-v4-flash", name: "DeepSeek V4 Flash", provider: "deepseek" },
    { id: "deepseek/deepseek-v4-pro", name: "DeepSeek V4 Pro", provider: "deepseek" },
    { id: "deepseek/deepseek-v3.2", name: "DeepSeek V3.2", provider: "deepseek" },
    { id: "deepseek/deepseek-chat", name: "DeepSeek Chat (legacy)", provider: "deepseek" },
  ];

  assert.equal(DEFAULT_MODEL_BY_PROVIDER.deepseek, "deepseek/deepseek-v4-flash");
  assert.deepEqual(
    getRecommendedModels(deepseekModels, "deepseek").map((model) => model.id),
    [
      "deepseek/deepseek-v4-flash",
      "deepseek/deepseek-v4-pro",
      "deepseek/deepseek-v3.2",
      "deepseek/deepseek-chat",
    ],
  );
});
