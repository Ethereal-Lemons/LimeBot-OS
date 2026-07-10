import test from "node:test";
import { strict as assert } from "node:assert";

import {
  DEFAULT_MODEL_BY_PROVIDER,
  getAdditionalModels,
  getRecommendedModels,
  type LlmModelOption,
} from "../src/lib/llm-models.js";

const models: LlmModelOption[] = [
  { id: "openai/gpt-5.5", name: "GPT-5.5", provider: "openai" },
  { id: "openai/gpt-5.4", name: "GPT-5.4", provider: "openai" },
  { id: "openai/gpt-5.4-mini", name: "GPT-5.4 Mini", provider: "openai" },
  { id: "openai/gpt-5.4-nano", name: "GPT-5.4 Nano", provider: "openai" },
  { id: "openai/gpt-5.3-codex", name: "GPT-5.3-Codex", provider: "openai" },
];

test("OpenAI recommends exact live API model IDs in capability order", () => {
  assert.equal(DEFAULT_MODEL_BY_PROVIDER.openai, "openai/gpt-5.5");
  assert.deepEqual(
    getRecommendedModels(models, "openai").map((model) => model.id),
    [
      "openai/gpt-5.5",
      "openai/gpt-5.4",
      "openai/gpt-5.4-mini",
      "openai/gpt-5.4-nano",
    ],
  );
  assert.deepEqual(
    getAdditionalModels(models, "openai").map((model) => model.id),
    [
      "openai/gpt-5.3-codex",
    ],
  );
});
