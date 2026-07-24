import { describe, expect, it, vi } from "vitest";
import {
  AccountRequiredError,
  CommClient,
  CommError,
  InsufficientCreditError,
  Interaction,
  Message,
  Reaction,
} from "../src/index.js";

/** Build a client whose fetch is driven by a route table. */
function makeClient(routes: Record<string, (req: Request) => Response | Promise<Response>>) {
  const calls: { method: string; path: string; body: any }[] = [];
  const fetchImpl = (async (url: any, init: any = {}) => {
    const u = new URL(url.toString());
    const key = `${init.method ?? "GET"} ${u.pathname}`;
    calls.push({
      method: init.method ?? "GET",
      path: u.pathname,
      body: init.body ? JSON.parse(init.body) : undefined,
    });
    const handler = routes[key];
    if (!handler) return new Response("not found", { status: 404 });
    return handler(new Request(u, init));
  }) as unknown as typeof fetch;

  const client = new CommClient({ apiKey: "comm_test", baseUrl: "http://gw", fetch: fetchImpl });
  return { client, calls };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function messageEvent(seq: number, conversationId: string, text: string) {
  return {
    seq,
    type: "message.received",
    data: {
      message: {
        id: `m${seq}`,
        conversation_id: conversationId,
        connection_id: "cn",
        text,
      },
    },
  };
}

describe("CommClient", () => {
  it("requires an API key", () => {
    delete process.env.CASPIAN_API_KEY;
    delete process.env.COMM_API_KEY;
    expect(() => new CommClient({ baseUrl: "http://gw" })).toThrow(CommError);
  });

  it("reads CASPIAN_API_KEY, and falls back to legacy COMM_API_KEY", async () => {
    // Capture the bearer token actually sent, to prove which env var resolved.
    async function resolvedKey(): Promise<string> {
      let seen = "";
      const fetchImpl = (async (url: any, init: any = {}) => {
        seen = (init.headers?.Authorization as string) ?? "";
        return new Response(JSON.stringify({ id: "cus_1" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }) as unknown as typeof fetch;
      const client = new CommClient({ baseUrl: "http://gw", fetch: fetchImpl });
      await client.createCustomer("Acme");
      return seen.replace(/^Bearer /, "");
    }

    delete process.env.CASPIAN_API_KEY;
    delete process.env.COMM_API_KEY;
    process.env.COMM_API_KEY = "legacy_key"; // legacy var still works
    expect(await resolvedKey()).toBe("legacy_key");
    process.env.CASPIAN_API_KEY = "new_key"; // new var preferred even when both set
    expect(await resolvedKey()).toBe("new_key");
    delete process.env.CASPIAN_API_KEY;
    delete process.env.COMM_API_KEY;
  });

  it("sends bearer auth and parses JSON", async () => {
    let seenAuth = "";
    const { client } = makeClient({
      "POST /v1/customers": (req) => {
        seenAuth = req.headers.get("authorization") ?? "";
        return json({ id: "cus_1", name: "Acme" });
      },
    });
    const cust = await client.createCustomer("Acme");
    expect(cust).toEqual({ id: "cus_1", name: "Acme" });
    expect(seenAuth).toBe("Bearer comm_test");
  });

  it("connectEmail waits out provisioning then returns active", async () => {
    let polls = 0;
    const { client, calls } = makeClient({
      "POST /v1/connections/email": () => json({ id: "conn_1", status: "provisioning" }),
      "GET /v1/connections/conn_1": () => {
        polls += 1;
        return json({ id: "conn_1", status: polls >= 2 ? "active" : "provisioning", address: "a@x" });
      },
    });
    const conn = await client.connectEmail({ pollInterval: 0.001 });
    expect(conn.status).toBe("active");
    expect(conn.address).toBe("a@x");
    // body maps camelCase -> snake_case
    expect(calls[0].body).toMatchObject({ customer_id: null, agent_id: null, domain: null });
  });

  it("connectEmail with wait: false returns immediately without polling", async () => {
    const { client, calls } = makeClient({
      "POST /v1/connections/email": () => json({ id: "conn_1", status: "provisioning" }),
    });
    const conn = await client.connectEmail({ wait: false });
    expect(conn.status).toBe("provisioning");
    
    // Verify no polling requests occurred (only the POST should be present)
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].path).toBe("/v1/connections/email");
  });

  it("connectEmail throws CommError on provisioning failure", async () => {
    const { client } = makeClient({
      "POST /v1/connections/email": () => json({ id: "conn_1", status: "provisioning" }),
      "GET /v1/connections/conn_1": () => {
        return json({ id: "conn_1", status: "failed", error: "DNS verification failed" });
      },
    });
    
    const err = await client
      .connectEmail({ pollInterval: 0.001 })
      .catch((e) => e);
    expect(err).toBeInstanceOf(CommError);
    expect(err.statusCode).toBe(502);
    expect(err.detail).toBe("provisioning failed: DNS verification failed");
  });

  it("maps camelCase channel fields to snake_case on the wire", async () => {
    const { client, calls } = makeClient({
      "POST /v1/connections/telegram": () => json({ id: "c", status: "active" }),
    });
    await client.connectTelegram({ botToken: "123:abc", customerId: "cus_9" });
    expect(calls[0].body).toMatchObject({ bot_token: "123:abc", customer_id: "cus_9" });
  });

  it("connectSlack does not wait and returns an authorize_url", async () => {
    const { client, calls } = makeClient({
      "POST /v1/connections/slack": () =>
        json({ id: "c", status: "pending_oauth", authorize_url: "https://slack/oauth" }),
    });
    const conn = await client.connectSlack({ slackClientId: "cid", slackClientSecret: "sec", slackSigningSecret: "sig" });
    expect(conn.authorize_url).toBe("https://slack/oauth");
    expect(calls[0].body).toMatchObject({ slack_client_id: "cid", slack_signing_secret: "sig" });
  });

  it("throws CommError with detail on 4xx", async () => {
    const { client } = makeClient({
      "POST /v1/connections/x": () => json({ detail: "sign in first" }, 402),
    });
    await expect(client.connectX({ accessToken: "a", userId: "1" })).rejects.toMatchObject({
      statusCode: 402,
      detail: "sign in first",
    });
  });

  it("behaviorPrompt / channelGuide return text", async () => {
    const { client } = makeClient({
      "GET /v1/behavior-prompt": () => new Response("# guide\n## Slack"),
      "GET /v1/channels/slack/guide": () => new Response("## Slack\n- threads"),
    });
    expect(await client.behaviorPrompt()).toContain("Slack");
    expect(await client.channelGuide("slack")).toContain("threads");
  });

  it("dispatchPending builds a Message and runs handlers; reply hits the API", async () => {
    const replies: any[] = [];
    const { client } = makeClient({
      "GET /v1/events": (req) => {
        const after = Number(new URL(req.url).searchParams.get("after_seq"));
        if (after >= 1) return json([]);
        return json([
          {
            seq: 1,
            type: "message.received",
            data: {
              customer_id: "cus_1",
              agent_id: "agt_1",
              message: {
                id: "msg_1",
                conversation_id: "conv_1",
                connection_id: "conn_1",
                channel: "slack",
                text: "hi",
              },
            },
          },
        ]);
      },
      "POST /v1/messages/msg_1/typing": () => json({ ok: true }),
      "POST /v1/messages/msg_1/reply": (req) => {
        return req
          .json()
          .then((b) => {
            replies.push(b);
            return json({ delivered: true });
          });
      },
    });

    const seen: Message[] = [];
    client.onMessage(async (m) => {
      seen.push(m);
      await m.reply(`echo: ${m.text}`);
    });
    const last = await client.dispatchPending(0);

    expect(last).toBe(1);
    expect(seen).toHaveLength(1);
    expect(seen[0].channel).toBe("slack");
    expect(seen[0].conversationId).toBe("conv_1");
    expect(replies[0]).toEqual({ text: "echo: hi", html: null, blocks: null, media: null });
  });

  it("reply and sendMessage forward blocks in the request body", async () => {
    const bodies: any[] = [];
    const { client } = makeClient({
      "POST /v1/messages/m1/reply": (req) =>
        req.json().then((b) => {
          bodies.push(b);
          return json({ delivered: true });
        }),
      "POST /v1/conversations/c1/messages": (req) =>
        req.json().then((b) => {
          bodies.push(b);
          return json({ delivered: true });
        }),
    });
    const blocks = [
      { type: "heading", text: "Order shipped" },
      { type: "buttons", buttons: [{ label: "Track", url: "https://x/track" }] },
    ];
    await client.reply("m1", "Order shipped", null, blocks as any);
    await client.sendMessage("c1", null, null, blocks as any);
    expect(bodies[0]).toEqual({ text: "Order shipped", html: null, blocks, media: null });
    expect(bodies[1]).toEqual({ text: null, html: null, blocks, media: null });
  });

  it("listen({ ack }) sends an instant ack reply before the handler runs", async () => {
    const replies: string[] = [];
    const { client } = makeClient({
      "GET /v1/events": (req) => {
        const after = Number(new URL(req.url).searchParams.get("after_seq"));
        if (after >= 1) return json([]);
        return json([
          {
            seq: 1,
            type: "message.received",
            data: { message: { id: "m1", conversation_id: "c", connection_id: "cn", text: "hi" } },
          },
        ]);
      },
      "POST /v1/messages/m1/typing": () => json({}),
      "POST /v1/messages/m1/reply": (req) =>
        req.json().then((b: any) => {
          replies.push(b.text);
          return json({});
        }),
    });
    const seen: string[] = [];
    client.onMessage((m) => {
      seen.push(m.text ?? "");
    });
    const ac = new AbortController();
    ac.abort();
    await client.listen({ ack: "On it, one moment…", signal: ac.signal }); // sets ack, returns (aborted)
    await client.dispatchPending(0); // now dispatches with the ack configured
    expect(replies[0]).toBe("On it, one moment…"); // ack fired first
    expect(seen).toEqual(["hi"]); // handler still ran
  });

  it("listen queues each conversation without blocking the others", async () => {
    const ac = new AbortController();
    let releaseFirst!: () => void;
    const firstBlocked = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    let stopScheduled = false;
    const { client } = makeClient({
      "GET /v1/events": (req) => {
        const after = Number(new URL(req.url).searchParams.get("after_seq"));
        if (after === 0) {
          return json([
            messageEvent(1, "conv_1", "first"),
            messageEvent(2, "conv_1", "second"),
            messageEvent(3, "conv_2", "other"),
          ]);
        }
        if (!stopScheduled) {
          stopScheduled = true;
          setTimeout(releaseFirst, 20);
          setTimeout(() => ac.abort(), 60);
        }
        return json([]);
      },
    });
    const seen: string[] = [];
    client.onMessage(async (message) => {
      if (message.text === "first") await firstBlocked;
      seen.push(message.text ?? "");
    });

    await client.listen({ fromSeq: 0, pollInterval: 0.001, signal: ac.signal });

    expect(seen).toEqual(["other", "first", "second"]);
  });

  it("listen debounce keeps the latest message without overlapping handlers", async () => {
    const ac = new AbortController();
    let latestStarted = false;
    let followupSent = false;
    let stopScheduled = false;
    let releaseLatest!: () => void;
    const latestBlocked = new Promise<void>((resolve) => {
      releaseLatest = resolve;
    });
    const { client } = makeClient({
      "GET /v1/events": (req) => {
        const after = Number(new URL(req.url).searchParams.get("after_seq"));
        if (after === 0) {
          return json([
            messageEvent(1, "conv_1", "first"),
            messageEvent(2, "conv_1", "second"),
            messageEvent(3, "conv_1", "latest"),
          ]);
        }
        if (after === 3 && latestStarted && !followupSent) {
          followupSent = true;
          return json([messageEvent(4, "conv_1", "after")]);
        }
        if (after >= 4 && !stopScheduled) {
          stopScheduled = true;
          setTimeout(releaseLatest, 20);
          setTimeout(() => ac.abort(), 60);
        }
        return json([]);
      },
    });
    const seen: string[] = [];
    client.onMessage(async (message) => {
      if (message.text === "latest") {
        latestStarted = true;
        await latestBlocked;
      }
      seen.push(message.text ?? "");
    });

    await client.listen({
      fromSeq: 0,
      pollInterval: 0.001,
      signal: ac.signal,
      concurrency: "debounce",
      debounceMs: 10,
    });

    expect(seen).toEqual(["latest", "after"]);
  });

  it("listen drop ignores messages while a conversation is busy", async () => {
    const ac = new AbortController();
    let releaseFirst!: () => void;
    const firstBlocked = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    let stopScheduled = false;
    const { client } = makeClient({
      "GET /v1/events": (req) => {
        const after = Number(new URL(req.url).searchParams.get("after_seq"));
        if (after === 0) {
          return json([
            messageEvent(1, "conv_1", "first"),
            messageEvent(2, "conv_1", "second"),
            messageEvent(3, "conv_1", "third"),
          ]);
        }
        if (!stopScheduled) {
          stopScheduled = true;
          setTimeout(releaseFirst, 20);
          setTimeout(() => ac.abort(), 60);
        }
        return json([]);
      },
    });
    const seen: string[] = [];
    client.onMessage(async (message) => {
      if (message.text === "first") await firstBlocked;
      seen.push(message.text ?? "");
    });

    await client.listen({
      fromSeq: 0,
      pollInterval: 0.001,
      signal: ac.signal,
      concurrency: "drop",
    });

    expect(seen).toEqual(["first"]);
  });

  it("listen parallel allows handlers in one conversation to overlap", async () => {
    const ac = new AbortController();
    let releaseFirst!: () => void;
    const firstBlocked = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    let firstStarted = false;
    let secondFinished = false;
    let stopScheduled = false;
    const { client } = makeClient({
      "GET /v1/events": (req) => {
        const after = Number(new URL(req.url).searchParams.get("after_seq"));
        if (after === 0) {
          return json([
            messageEvent(1, "conv_1", "first"),
            messageEvent(2, "conv_1", "second"),
          ]);
        }
        if (!stopScheduled) {
          stopScheduled = true;
          setTimeout(releaseFirst, 20);
          setTimeout(() => ac.abort(), 60);
        }
        return json([]);
      },
    });
    const seen: string[] = [];
    client.onMessage(async (message) => {
      if (message.text === "first") {
        firstStarted = true;
        await firstBlocked;
      }
      seen.push(message.text ?? "");
      if (message.text === "second") secondFinished = true;
    });

    await client.listen({
      fromSeq: 0,
      pollInterval: 0.001,
      signal: ac.signal,
      concurrency: "parallel",
    });

    expect(firstStarted).toBe(true);
    expect(secondFinished).toBe(true);
    expect(seen).toEqual(["second", "first"]);
  });

  it("listen validates overlap options", async () => {
    const { client } = makeClient({});
    await expect(
      client.listen({ fromSeq: 0, concurrency: "invalid" as any }),
    ).rejects.toThrow("concurrency");
    await expect(client.listen({ fromSeq: 0, debounceMs: -1 })).rejects.toThrow("debounceMs");
  });

  it("raises AccountRequiredError on a 401 account_required body", async () => {
    const { client } = makeClient({
      "POST /v1/connections/x": () =>
        json(
          {
            detail: {
              reason: "account_required",
              message: "Sign in to use paid channels.",
              login_options: [{ start: "/v1/auth/device/start" }],
            },
          },
          401,
        ),
    });
    const err = await client
      .connectX({ accessToken: "a", userId: "1" })
      .catch((e) => e);
    expect(err).toBeInstanceOf(AccountRequiredError);
    expect(err).toBeInstanceOf(CommError);
    expect(err.reason).toBe("account_required");
    expect(err.detail).toBe("Sign in to use paid channels.");
    expect(err.loginOptions).toEqual([{ start: "/v1/auth/device/start" }]);
  });

  it("raises InsufficientCreditError on a 402 insufficient_credit body", async () => {
    const { client } = makeClient({
      "POST /v1/messages/m1/reply": () =>
        json(
          {
            detail: {
              reason: "insufficient_credit",
              message: "Out of credit.",
              balance_cents: 42,
              payment_options: [{ url: "https://pay/1", create: { body: { amount_cents: 5000 } } }],
            },
          },
          402,
        ),
    });
    const err = await client.reply("m1", "hi").catch((e) => e);
    expect(err).toBeInstanceOf(InsufficientCreditError);
    expect(err.statusCode).toBe(402);
    expect(err.balanceCents).toBe(42);
    expect(err.paymentOptions[0].url).toBe("https://pay/1");
  });

  it("uses the suggested payment option amount when topping up without an amount", async () => {
    const { client, calls } = makeClient({
      "POST /v1/messages/m1/reply": () =>
        json(
          {
            detail: {
              reason: "insufficient_credit",
              message: "Out of credit.",
              payment_options: [{ create: { body: { amount_cents: 5000 } } }],
            },
          },
          402,
        ),
      "POST /v1/billing/topup": () => json({ checkout_url: "https://pay/1" }),
    });
    const err = await client.reply("m1", "hi").catch((e) => e);
    expect(err).toBeInstanceOf(InsufficientCreditError);

    await err.topUp();

    const topup = calls.find((c) => c.path === "/v1/billing/topup");
    expect(topup?.body).toEqual({ amount_cents: 5000 });
  });

  it("raises InsufficientCreditError on a 429 monthly_cap_reached body", async () => {
    const { client } = makeClient({
      "POST /v1/messages/m1/reply": () =>
        json({ detail: { reason: "monthly_cap_reached", message: "Capped." } }, 429),
    });
    const err = await client.reply("m1", "hi").catch((e) => e);
    expect(err).toBeInstanceOf(InsufficientCreditError);
    expect(err.statusCode).toBe(429);
  });

  it("raises InsufficientCreditError on a 429 channel_cap_reached body", async () => {
    const { client } = makeClient({
      "POST /v1/messages/m1/reply": () =>
        json({ detail: { reason: "channel_cap_reached", message: "Channel capped." } }, 429),
    });
    const err = await client.reply("m1", "hi").catch((e) => e);
    expect(err).toBeInstanceOf(InsufficientCreditError);
    expect(err.statusCode).toBe(429);
    expect(err.reason).toBe("channel_cap_reached");
  });

  it("billing methods hit the right endpoints with snake_case bodies", async () => {
    const { client, calls } = makeClient({
      "GET /v1/billing": () => json({ balance_cents: 100 }),
      "POST /v1/billing/topup": () => json({ checkout_url: "https://pay" }),
      "PUT /v1/billing/limits": () => json({ ok: true }),
      "PUT /v1/billing/autopay": () => json({ ok: true }),
    });
    await client.billing();
    await client.topUp();
    await client.setSpendLimits({ monthlyCapCents: 10000, channelCaps: { whatsapp: 5000 } });
    await client.setAutopay({ thresholdCents: 500, topupCents: 2000, monthlyCapCents: 10000 });

    const topup = calls.find((c) => c.path === "/v1/billing/topup");
    expect(topup?.body).toEqual({ amount_cents: 2000 });
    const limits = calls.find((c) => c.path === "/v1/billing/limits");
    expect(limits?.body).toEqual({ monthly_cap_cents: 10000, channel_caps: { whatsapp: 5000 } });
    const autopay = calls.find((c) => c.path === "/v1/billing/autopay");
    expect(autopay?.body).toMatchObject({ enabled: true, threshold_cents: 500, topup_cents: 2000 });
  });

  it("out-of-credit in a handler warns but does not stop the drain", async () => {
    const errSpy = vi.spyOn(process.stderr, "write").mockImplementation(() => true);
    const { client } = makeClient({
      "GET /v1/events": (req) => {
        const after = Number(new URL(req.url).searchParams.get("after_seq"));
        if (after >= 1) return json([]);
        return json([
          {
            seq: 1,
            type: "message.received",
            data: { message: { id: "m1", conversation_id: "c", connection_id: "cn", text: "hi" } },
          },
        ]);
      },
      "POST /v1/messages/m1/typing": () => json({}),
    });
    client.onMessage(() => {
      throw new InsufficientCreditError(
        402,
        { reason: "insufficient_credit", message: "Out of credit.", balance_cents: 0 },
        client,
      );
    });
    const last = await client.dispatchPending(0);
    expect(last).toBe(1);
    const printed = errSpy.mock.calls.map((c) => String(c[0])).join("");
    expect(printed).toContain("OUT OF CREDIT");
    errSpy.mockRestore();
  });

  it("account-required in a handler warns but does not stop the drain", async () => {
    const errSpy = vi.spyOn(process.stderr, "write").mockImplementation(() => true);
    const { client } = makeClient({
      "GET /v1/events": (req) => {
        const after = Number(new URL(req.url).searchParams.get("after_seq"));
        if (after >= 2) return json([]);
        const message = (seq: number) => ({
          seq,
          type: "message.received",
          data: {
            message: {
              id: `m${seq}`,
              conversation_id: "c",
              connection_id: "cn",
              text: "hi",
            },
          },
        });
        return json([message(1), message(2)]);
      },
      "POST /v1/messages/m1/typing": () => json({}),
      "POST /v1/messages/m2/typing": () => json({}),
    });
    let handled = 0;
    client.onMessage(() => {
      handled += 1;
      throw new AccountRequiredError(
        401,
        { reason: "account_required", message: "Sign in to use paid channels." },
        client,
      );
    });

    const last = await client.dispatchPending(0);

    expect(last).toBe(2);
    expect(handled).toBe(2);
    const printed = errSpy.mock.calls.map((c) => String(c[0])).join("");
    expect(printed).toContain("SIGN-IN REQUIRED");
    expect(printed).toContain("Sign in to use paid channels.");
    errSpy.mockRestore();
  });

  it("reply, sendMessage and react forward media / emoji", async () => {
    const bodies: any[] = [];
    const { client, calls } = makeClient({
      "POST /v1/messages/m1/reply": (req) =>
        req.json().then((b) => (bodies.push(b), json({ ok: true }))),
      "POST /v1/conversations/c1/messages": (req) =>
        req.json().then((b) => (bodies.push(b), json({ ok: true }))),
      "POST /v1/messages/m1/react": (req) =>
        req.json().then((b) => (bodies.push(b), json({ ok: true, reacted: true }))),
    });
    const media = [{ url: "https://x/i.png", mime_type: "image/png", name: "i.png" }];
    await client.reply("m1", "here", null, null, media as any);
    await client.sendMessage("c1", null, null, null, media as any);
    await client.react("m1", "👍");
    expect(bodies[0]).toEqual({ text: "here", html: null, blocks: null, media });
    expect(bodies[1]).toEqual({ text: null, html: null, blocks: null, media });
    expect(bodies[2]).toEqual({ emoji: "👍" });
    expect(calls.map((c) => c.path)).toContain("/v1/messages/m1/react");
  });

  it("onInteraction dispatches a button tap and reply routes to the source message", async () => {
    const replies: any[] = [];
    const { client } = makeClient({
      "GET /v1/events": (req) => {
        const after = Number(new URL(req.url).searchParams.get("after_seq"));
        if (after >= 1) return json([]);
        return json([
          {
            seq: 1,
            type: "interaction.received",
            data: {
              connection_id: "conn_1",
              customer_id: "cus_1",
              agent_id: "agt_1",
              conversation_id: "conv_1",
              value: "reorder_123",
              source_message: { id: "msg_9" },
              sender: { address: "u" },
            },
          },
        ]);
      },
      "POST /v1/messages/msg_9/reply": (req) =>
        req.json().then((b) => (replies.push(b), json({ delivered: true }))),
    });
    const seen: Interaction[] = [];
    client.onInteraction(async (i) => {
      seen.push(i);
      await i.reply(`got ${i.value}`);
    });
    const last = await client.dispatchPending(0);
    expect(last).toBe(1);
    expect(seen).toHaveLength(1);
    expect(seen[0].value).toBe("reorder_123");
    expect(seen[0].sourceMessage?.id).toBe("msg_9");
    expect(replies[0].text).toBe("got reorder_123");
  });

  it("onReaction dispatches an emoji reaction", async () => {
    const { client } = makeClient({
      "GET /v1/events": (req) => {
        const after = Number(new URL(req.url).searchParams.get("after_seq"));
        if (after >= 1) return json([]);
        return json([
          {
            seq: 1,
            type: "reaction.received",
            data: {
              connection_id: "conn_1",
              emoji: "thumbsup",
              action: "added",
              source_message: { id: "msg_9" },
            },
          },
        ]);
      },
    });
    const seen: Reaction[] = [];
    client.onReaction((r) => {
      seen.push(r);
    });
    await client.dispatchPending(0);
    expect(seen).toHaveLength(1);
    expect(seen[0].emoji).toBe("thumbsup");
    expect(seen[0].action).toBe("added");
  });

  it("a message carries received media to the handler", async () => {
    const { client } = makeClient({
      "GET /v1/events": (req) => {
        const after = Number(new URL(req.url).searchParams.get("after_seq"));
        if (after >= 1) return json([]);
        return json([
          {
            seq: 1,
            type: "message.received",
            data: {
              message: {
                id: "m1",
                conversation_id: "c",
                connection_id: "cn",
                text: "see attached",
                media: [{ name: "r.pdf", mime_type: "application/pdf" }],
              },
            },
          },
        ]);
      },
      "POST /v1/messages/m1/typing": () => json({}),
    });
    const seen: Message[] = [];
    client.onMessage((m) => {
      seen.push(m);
    });
    await client.dispatchPending(0);
    expect(seen[0].media).toEqual([{ name: "r.pdf", mime_type: "application/pdf" }]);
  });

  it("a throwing handler does not stop the drain", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { client } = makeClient({
      "GET /v1/events": (req) => {
        const after = Number(new URL(req.url).searchParams.get("after_seq"));
        if (after >= 2) return json([]);
        const mk = (seq: number) => ({
          seq,
          type: "message.received",
          data: { message: { id: `m${seq}`, conversation_id: "c", connection_id: "cn", text: "x" } },
        });
        return json([mk(1), mk(2)]);
      },
      "POST /v1/messages/m1/typing": () => json({}),
      "POST /v1/messages/m2/typing": () => json({}),
    });
    let count = 0;
    client.onMessage(() => {
      count += 1;
      throw new Error("boom");
    });
    const last = await client.dispatchPending(0);
    expect(last).toBe(2);
    expect(count).toBe(2); // both dispatched despite throwing
    errorSpy.mockRestore();
  });
});
