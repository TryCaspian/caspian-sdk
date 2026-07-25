import { describe, expect, test } from "bun:test";
import { resolveConfig } from "../src/config.ts";
import {
  admitsIdentity,
  resolveSendConnection,
} from "../src/identity.ts";
import { formatOutboundText, sendEmail } from "../src/outbound.ts";
import { CircuitBreaker } from "../src/reliability/circuit.ts";
import { Metrics } from "../src/reliability/metrics.ts";
import { admits, toEnvelope } from "../src/email.ts";

describe("formatOutboundText", () => {
  test("prefixes subject when present", () => {
    expect(
      formatOutboundText({ to: "a@b.com", body: "hi", subject: "Hello" }),
    ).toBe("Subject: Hello\n\nhi");
  });

  test("omits blank / bare Re: subject", () => {
    expect(
      formatOutboundText({ to: "a@b.com", body: "hi", subject: "Re:" }),
    ).toBe("hi");
  });

  test("sendEmail rejects blank subject", async () => {
    await expect(
      sendEmail(
        {
          identity: { listenConnectionIds: [], listenAddresses: [] },
          listConnections: async () => [
            {
              id: "conn_a",
              channel: "email",
              status: "active",
              address: "a@agents.test",
              capabilities: ["initiate"],
            },
          ],
          initiate: async () => ({ conversation_id: "c1" }),
          circuit: new CircuitBreaker({
            failureThreshold: 5,
            coolDownMs: 1000,
            successThreshold: 1,
          }),
          metrics: new Metrics(),
        },
        { to: "x@y.com", body: "hi", subject: "Re:" },
      ),
    ).rejects.toThrow(/subject/i);
  });
});

describe("identity listen filter", () => {
  test("admits all when filters empty", () => {
    expect(
      admitsIdentity(
        { listenConnectionIds: [], listenAddresses: [] },
        { connectionId: "conn_1", inboxAddress: "a@agents.test" },
      ),
    ).toBe(true);
  });

  test("filters by connectionId", () => {
    const identity = {
      connectionId: "conn_wanted",
      listenConnectionIds: [] as string[],
      listenAddresses: [] as string[],
    };
    expect(
      admitsIdentity(identity, { connectionId: "conn_wanted" }),
    ).toBe(true);
    expect(
      admitsIdentity(identity, { connectionId: "conn_other" }),
    ).toBe(false);
  });

  test("admits() applies email identity", () => {
    const cfg = resolveConfig({
      email: { address: "agent@agents.trycaspianai.com" },
    });
    const ok = toEnvelope({
      id: "m",
      conversationId: "c",
      channel: "email",
      connectionId: "conn_1",
      inboxAddress: "agent@agents.trycaspianai.com",
      text: "hi",
      sender: { address: "u@x.com" },
    });
    const bad = toEnvelope({
      id: "m2",
      conversationId: "c2",
      channel: "email",
      connectionId: "conn_1",
      inboxAddress: "other@agents.trycaspianai.com",
      text: "hi",
      sender: { address: "u@x.com" },
    });
    expect(admits(cfg, ok)).toBe(true);
    expect(admits(cfg, bad)).toBe(false);
  });
});

describe("resolveSendConnection", () => {
  const conns = [
    {
      id: "conn_a",
      channel: "email",
      status: "active",
      address: "a@agents.test",
      capabilities: ["initiate"],
    },
    {
      id: "conn_b",
      channel: "email",
      status: "active",
      address: "b@agents.test",
      capabilities: ["initiate"],
    },
  ];

  test("picks by connectionId", () => {
    const hit = resolveSendConnection(
      {
        connectionId: "conn_b",
        listenConnectionIds: [],
        listenAddresses: [],
      },
      conns,
    );
    expect(hit?.id).toBe("conn_b");
  });

  test("picks by address", () => {
    const hit = resolveSendConnection(
      {
        address: "a@agents.test",
        listenConnectionIds: [],
        listenAddresses: [],
      },
      conns,
    );
    expect(hit?.address).toBe("a@agents.test");
  });
});

describe("sendEmail", () => {
  test("initiates via selected connection", async () => {
    const calls: Array<{ id: string; to: string; text: string }> = [];
    const metrics = new Metrics();
    const result = await sendEmail(
      {
        identity: {
          connectionId: "conn_a",
          listenConnectionIds: [],
          listenAddresses: [],
        },
        listConnections: async () => [
          {
            id: "conn_a",
            channel: "email",
            status: "active",
            address: "agent@agents.test",
            capabilities: ["initiate"],
          },
        ],
        initiate: async (id, to, text) => {
          calls.push({ id, to, text });
          return { queued: true };
        },
        circuit: new CircuitBreaker({
          failureThreshold: 5,
          coolDownMs: 1000,
          successThreshold: 1,
        }),
        metrics,
      },
      {
        to: "dipanshuhappy@gmail.com",
        body: "I am alive",
        subject: "ping",
      },
    );

    expect(result.ok).toBe(true);
    expect(result.to).toBe("dipanshuhappy@gmail.com");
    expect(calls[0]?.to).toBe("dipanshuhappy@gmail.com");
    expect(calls[0]?.text).toContain("I am alive");
    expect(calls[0]?.text).toContain("Subject: ping");
    expect(metrics.get("outbound.send_ok")).toBe(1);
  });

  test("rejects bad recipient", async () => {
    await expect(
      sendEmail(
        {
          identity: { listenConnectionIds: [], listenAddresses: [] },
          listConnections: async () => [],
          initiate: async () => ({}),
          circuit: new CircuitBreaker({
            failureThreshold: 5,
            coolDownMs: 1000,
            successThreshold: 1,
          }),
          metrics: new Metrics(),
        },
        { to: "not-an-email", body: "hi" },
      ),
    ).rejects.toThrow("Invalid recipient");
  });
});
