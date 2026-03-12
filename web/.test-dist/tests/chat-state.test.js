import test from "node:test";
import { strict as assert } from "node:assert";
import { applyFinalAssistantMessage, applyStopTyping, upsertStreamDelta, } from "../src/lib/chat-state.js";
test("stream deltas and final message target the same bot bubble by message_id", () => {
    const initial = [
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
    const initial = [
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
    const initial = [
        { sender: "bot", content: "Older stream", isStreaming: true, messageId: "msg-old", turnId: "turn-old" },
        { sender: "bot", content: "Current stream", isStreaming: true, messageId: "msg-now", turnId: "turn-now" },
    ];
    const stopped = applyStopTyping(initial, { messageId: "msg-now", turnId: "turn-now" });
    assert.equal(stopped[0].isStreaming, true);
    assert.equal(stopped[1].isStreaming, false);
});
