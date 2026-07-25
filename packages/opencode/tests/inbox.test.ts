import { describe, expect, test } from "bun:test";
import { DEFAULT_CONFIG } from "../src/config.ts";
import {
  formatInboxSnapshot,
  listInbox,
  summarizeMessage,
  type InboxListPort,
} from "../src/inbox.ts";

function fakePort(opts?: {
  connections?: Array<{
    id: string;
    channel: string;
    status: string;
    address?: string;
  }>;
}): InboxListPort {
  const connections = opts?.connections ?? [
    {
      id: "conn_email",
      channel: "email",
      status: "active",
      address: "agent@agents.trycaspianai.com",
    },
    {
      id: "conn_slack",
      channel: "slack",
      status: "active",
      address: "C123",
    },
  ];

  return {
    listConnections: async () => connections,
    listConversations: async (connectionId) => {
      if (connectionId === "conn_email") {
        return [
          {
            id: "conv_1",
            subject: "Hello",
            peer: "human@gmail.com",
            updated_at: "2026-07-25T01:00:00Z",
          },
        ];
      }
      if (connectionId === "conn_slack") {
        return [
          {
            id: "conv_slack",
            title: "#general",
            updated_at: "2026-07-25T02:00:00Z",
          },
        ];
      }
      return [];
    },
    listMessages: async (conversationId) => {
      if (conversationId === "conv_1") {
        return [
          {
            id: "m1",
            from: "human@gmail.com",
            subject: "Hello",
            text: "Testing inbox",
            direction: "inbound",
          },
        ];
      }
      return [
        {
          id: "m2",
          text: "slack hi",
          sender: { name: "bob" },
        },
      ];
    },
  };
}

describe("listInbox", () => {
  test("lists email only by default config channels", async () => {
    const snap = await listInbox(fakePort(), DEFAULT_CONFIG);
    expect(snap.connections.map((c) => c.channel)).toEqual(["email"]);
    expect(snap.conversations).toHaveLength(1);
    expect(snap.conversations[0]?.peer).toBe("human@gmail.com");
    expect(snap.conversations[0]?.messages[0]?.text).toBe("Testing inbox");
  });

  test("includes other channels when requested", async () => {
    const snap = await listInbox(fakePort(), DEFAULT_CONFIG, {
      channels: ["email", "slack"],
    });
    expect(snap.connections).toHaveLength(2);
    expect(snap.conversations.map((c) => c.channel).sort()).toEqual([
      "email",
      "slack",
    ]);
  });

  test("formatInboxSnapshot is readable", async () => {
    const snap = await listInbox(fakePort(), DEFAULT_CONFIG);
    const text = formatInboxSnapshot(snap);
    expect(text).toContain("Caspian inbox");
    expect(text).toContain("agent@agents.trycaspianai.com");
    expect(text).toContain("Testing inbox");
  });

  test("summarizeMessage extracts sender/text", () => {
    expect(
      summarizeMessage({
        sender: { address: "a@b.com" },
        text: "hi",
      }),
    ).toMatchObject({ from: "a@b.com", text: "hi" });
  });
});
