import test from "node:test";
import { strict as assert } from "node:assert";

import {
  applyFinalAssistantMessage,
  applyStopTyping,
  upsertToolExecution,
  upsertStreamDelta,
  type ChatMessage,
} from "../src/lib/chat-state.js";

test("stream deltas and final message target the same bot bubble by message_id", () => {
  const initial: ChatMessage[] = [
    { sender: "user", content: "ok do it" },
    { sender: "bot", content: "", isStreaming: true, messageId: "msg-1", turnId: "turn-1" },
  ];

  const streamed = upsertStreamDelta(initial, {
    messageId: "msg-1",
    turnId: "turn-1",
    contentDelta: "Checking changelog...",
  });
  const finalized = applyFinalAssistantMessage(streamed, {
    messageId: "msg-1",
    turnId: "turn-1",
    content: "Checked the changelog.",
    variant: "default",
  });

  assert.equal(finalized.length, 2);
  assert.equal(finalized[1].content, "Checked the changelog.");
  assert.equal(finalized[1].isStreaming, false);
  assert.equal(finalized[1].messageId, "msg-1");
});

test("final replies do not overwrite an older completed bot message when ids differ", () => {
  const initial: ChatMessage[] = [
    { sender: "bot", content: "Old completed reply", isStreaming: false, messageId: "msg-old", turnId: "turn-old" },
    { sender: "user", content: "next task" },
    { sender: "bot", content: "Working", isStreaming: true, messageId: "msg-new", turnId: "turn-new" },
  ];

  const finalized = applyFinalAssistantMessage(initial, {
    messageId: "msg-new",
    turnId: "turn-new",
    content: "Fresh final reply",
    variant: "default",
  });

  assert.equal(finalized.length, 3);
  assert.equal(finalized[0].content, "Old completed reply");
  assert.equal(finalized[2].content, "Fresh final reply");
  assert.equal(finalized[2].messageId, "msg-new");
});

test("stop_typing only clears the targeted streaming assistant message", () => {
  const initial: ChatMessage[] = [
    { sender: "bot", content: "Older stream", isStreaming: true, messageId: "msg-old", turnId: "turn-old" },
    { sender: "bot", content: "Current stream", isStreaming: true, messageId: "msg-now", turnId: "turn-now" },
  ];

  const stopped = applyStopTyping(initial, { messageId: "msg-now", turnId: "turn-now" });

  assert.equal(stopped[0].isStreaming, true);
  assert.equal(stopped[1].isStreaming, false);
});

test("late tool execution is inserted before the final reply for the same turn", () => {
  const initial: ChatMessage[] = [
    { sender: "user", content: "make an image" },
    {
      sender: "bot",
      type: "text",
      content: "The image model failed.",
      isStreaming: false,
      messageId: "msg-final",
      turnId: "turn-image",
    },
  ];

  const updated = upsertToolExecution(initial, {
    turnId: "turn-image",
    toolExecution: {
      tool: "generate_image",
      status: "error",
      args: { prompt: "guinea pig" },
      result: "model failed",
      tool_call_id: "tool-1",
    },
  });

  assert.equal(updated.length, 3);
  assert.equal(updated[1].type, "tool");
  assert.equal(updated[1].toolExecution?.tool, "generate_image");
  assert.equal(updated[2].content, "The image model failed.");
});

test("updated tool execution is moved before an existing final reply for the same turn", () => {
  const initial: ChatMessage[] = [
    { sender: "user", content: "make an image" },
    {
      sender: "bot",
      type: "text",
      content: "The image model failed.",
      isStreaming: false,
      messageId: "msg-final",
      turnId: "turn-image",
    },
    {
      sender: "bot",
      type: "tool",
      content: "",
      turnId: "turn-image",
      toolExecution: {
        tool: "generate_image",
        status: "running",
        args: { prompt: "guinea pig" },
        tool_call_id: "tool-1",
      },
    },
  ];

  const updated = upsertToolExecution(initial, {
    turnId: "turn-image",
    toolExecution: {
      tool: "generate_image",
      status: "error",
      args: { prompt: "guinea pig" },
      result: "model failed",
      tool_call_id: "tool-1",
    },
  });

  assert.equal(updated[1].type, "tool");
  assert.equal(updated[1].toolExecution?.status, "error");
  assert.equal(updated[2].content, "The image model failed.");
});

test("final replies replace a stopped streaming bubble for the same turn", () => {
  const initial: ChatMessage[] = [
    { sender: "bot", content: "Saving...", isStreaming: true, turnId: "turn-save" },
  ];

  const stopped = applyStopTyping(initial, { turnId: "turn-save" });
  const finalized = applyFinalAssistantMessage(stopped, {
    turnId: "turn-save",
    content: "Saved once.",
    variant: "default",
  });

  assert.equal(finalized.length, 1);
  assert.equal(finalized[0].content, "Saved once.");
  assert.equal(finalized[0].isStreaming, false);
});

test("final replies trim repeated sections before rendering", () => {
  const repeated = [
    "Saved, baby.",
    "I'll remember that 244069957187534848 is you on Discord.",
    "Saved, baby.",
    "I'll remember that 244069957187534848 is you on Discord.",
  ].join("\n\n");

  const finalized = applyFinalAssistantMessage([], {
    turnId: "turn-memory",
    content: repeated,
    variant: "default",
  });

  assert.equal(finalized.length, 1);
  assert.equal(
    finalized[0].content,
    ["Saved, baby.", "I'll remember that 244069957187534848 is you on Discord."].join(
      "\n\n"
    )
  );
});
