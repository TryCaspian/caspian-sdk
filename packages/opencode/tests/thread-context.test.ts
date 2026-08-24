import { describe, expect, test } from "bun:test";
import { ThreadContextStore } from "../src/thread-context.ts";

function remember(store: ThreadContextStore, i: number) {
  return store.remember({
    openCodeSessionId: `sess_${i}`,
    messageId: `msg_${i}`,
    conversationId: `conv_${i}`,
    channel: "email",
  });
}

describe("ThreadContextStore bounded growth", () => {
  test("does not grow past 2000 distinct threads", () => {
    const store = new ThreadContextStore();
    for (let i = 1; i <= 5000; i++) remember(store, i);

    // The oldest threads must have been evicted, not retained forever.
    expect(store.getBySession("sess_1")).toBeUndefined();
    expect(store.getByConversation("conv_1")).toBeUndefined();
    // The most recent thread must still be resolvable.
    expect(store.getBySession("sess_5000")).toBeDefined();
    expect(store.getByConversation("conv_5000")).toBeDefined();
  });

  test("keeps every entry when under the cap (no premature eviction)", () => {
    const store = new ThreadContextStore();
    for (let i = 1; i <= 1999; i++) remember(store, i);
    expect(store.getBySession("sess_1")).toBeDefined();
    expect(store.getBySession("sess_1999")).toBeDefined();
  });

  test("evicts exactly the oldest entry one message past the cap", () => {
    const store = new ThreadContextStore();
    for (let i = 1; i <= 2000; i++) remember(store, i);
    remember(store, 2001);
    expect(store.getBySession("sess_1")).toBeUndefined();
    expect(store.getBySession("sess_2")).toBeDefined();
    expect(store.getByConversation("conv_1")).toBeUndefined();
    expect(store.getByConversation("conv_2")).toBeDefined();
  });

  test("re-remembering an existing session does not inflate the map size", () => {
    const store = new ThreadContextStore();
    for (let i = 1; i <= 1999; i++) remember(store, i);
    // Update an existing key in place - must not count as new growth.
    store.remember({
      openCodeSessionId: "sess_1",
      messageId: "msg_1_updated",
      conversationId: "conv_1",
      channel: "email",
    });
    for (let i = 1; i <= 1999; i++) {
      expect(store.getBySession(`sess_${i}`)).toBeDefined();
    }
  });
});
