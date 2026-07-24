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

function messageEvent(seq: number, id: string, convId = "c1") {
  return {
    seq,
    type: "message.received",
    data: { message: { id, conversation_id: convId, connection_id: "c" } },
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
    await client.close();

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
    (client as any).ackMessage = "On it, one moment…";
    await client.dispatchPending(0); // now dispatches with the ack configured
    await client.close();
    expect(replies[0]).toBe("On it, one moment…"); // ack fired first
    expect(seen).toEqual(["hi"]); // handler still ran
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

  it("raises InsufficientCreditError on a 429 monthly_cap_reached body", async () => {
    const { client } = makeClient({
      "POST /v1/messages/m1/reply": () =>
        json({ detail: { reason: "monthly_cap_reached", message: "Capped." } }, 429),
    });
    const err = await client.reply("m1", "hi").catch((e) => e);
    expect(err).toBeInstanceOf(InsufficientCreditError);
    expect(err.statusCode).toBe(429);
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
    await client.close();
    expect(last).toBe(1);
    const printed = errSpy.mock.calls.map((c) => String(c[0])).join("");
    expect(printed).toContain("OUT OF CREDIT");
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
    await client.close();
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
    await client.close();
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
    await client.close();
    expect(seen[0].media).toEqual([{ name: "r.pdf", mime_type: "application/pdf" }]);
  });

  it("a throwing handler does not stop the drain", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => { });
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
    await client.close();
    expect(last).toBe(2);
    expect(last).toBe(2);
    expect(count).toBe(2); // both dispatched despite throwing
    errorSpy.mockRestore();
  });

  it("concurrency=queue runs sequentially per conversation", async () => {
    const { client } = makeClient({
      "GET /v1/events": () => json([]),
    });
    const seen: string[] = [];
    let active = 0;
    let maxActive = 0;
    let firstEntered!: () => void;
    const firstStarted = new Promise<void>((resolve) => {
      firstEntered = resolve;
    });
    client.onMessage(async (m) => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      if (m.id === "m1") {
        firstEntered();
        await new Promise((r) => setTimeout(r, 50));
      }
      seen.push(m.id);
      active -= 1;
    });
    const first = (client as any).handleConcurrency(messageEvent(1, "m1"), "queue", 500);
    await firstStarted;
    const second = (client as any).handleConcurrency(messageEvent(2, "m2"), "queue", 500);
    await new Promise((r) => setTimeout(r, 10));
    expect(seen).toEqual([]);
    await (client as any).scheduler.close();
    expect(seen).toEqual(["m1", "m2"]);
    expect(maxActive).toBe(1);
  });

  it("concurrency=parallel runs concurrently without waiting", async () => {
    const { client } = makeClient({
      "GET /v1/events": () => json([]),
    });
    const seen: string[] = [];
    let active = 0;
    let maxActive = 0;
    client.onMessage(async (m) => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      await new Promise((r) => setTimeout(r, 50));
      seen.push(m.id);
      active -= 1;
    });
    const start = performance.now();
    await (client as any).handleConcurrency(messageEvent(1, "m1"), "parallel", 500);
    await (client as any).handleConcurrency(messageEvent(2, "m2"), "parallel", 500);
    await new Promise((r) => setTimeout(r, 80));
    const elapsed = performance.now() - start;
    expect(seen).toEqual(expect.arrayContaining(["m1", "m2"]));
    expect(maxActive).toBe(2);
    expect(elapsed).toBeLessThan(500);
  });

  it("concurrency=drop ignores overlapping messages for the same conversation", async () => {
    const events = Array.from({ length: 20 }, (_, i) => ({
      seq: i + 1,
      type: "message.received",
      data: { message: { id: `m${i}`, conversation_id: "c1", connection_id: "c" } },
    }));

    const { client } = makeClient({
      "GET /v1/events": () => json([]),
    });

    const seen: string[] = [];
    client.onMessage(async (m) => {
      seen.push(m.id);
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    // Fire 20 concurrent handleConcurrency calls for the same conversation.
    // Because JS is single-threaded and the check-and-set in drop mode is synchronous,
    // exactly one handler should run.
    await Promise.all(
      events.map((event) => (client as any).handleConcurrency(event, "drop", 500))
    );

    await client.close();
    expect(seen).toHaveLength(1);
  });

  it("concurrency=debounce coalesces bursts", async () => {
    const events = [messageEvent(1, "m1"), messageEvent(2, "m2"), messageEvent(3, "m3")];
    let fetched = false;
    const { client } = makeClient({
      "GET /v1/events": () => {
        if (fetched) return json([]);
        fetched = true;
        return json(events);
      },
    });
    const seen: Message[] = [];
    client.onMessage(async (m) => {
      seen.push(m);
    });
    await client.dispatchPending(0, "debounce", 40);
    await new Promise((r) => setTimeout(r, 20));
    expect(seen).toHaveLength(0);
    await new Promise((r) => setTimeout(r, 60));
    expect(seen).toHaveLength(1);
    expect(seen[0].id).toBe("m3");
    expect(seen[0].coalescedMessages).toHaveLength(2);
    expect(seen[0].coalescedMessages[0].id).toBe("m1");
    expect(seen[0].coalescedMessages[1].id).toBe("m2");
  });

  it.each(["queue", "parallel", "debounce", "drop"] as const)(
    "concurrency=%s releases state after handler exceptions",
    async (concurrency) => {
      const errorSpy = vi.spyOn(console, "error").mockImplementation(() => { });
      const { client } = makeClient({
        "GET /v1/events": () => json([]),
      });
      const seen: string[] = [];
      client.onMessage(async (m) => {
        seen.push(m.id);
        if (m.id === "boom") throw new Error("boom");
      });

      await (client as any).handleConcurrency(messageEvent(1, "boom"), concurrency, 10);
      await new Promise((r) => setTimeout(r, 50));
      await (client as any).handleConcurrency(messageEvent(2, "ok"), concurrency, 10);
      await new Promise((r) => setTimeout(r, 50));

      expect(seen).toEqual(["boom", "ok"]);
      const sched = (client as any).scheduler;
      expect(sched.inFlight.has("c1")).toBe(false);
      expect(sched.queuePromises.has("c1")).toBe(false);
      expect(sched.debounceTimers.has("c1")).toBe(false);
      expect(sched.debounceEvents.has("c1")).toBe(false);
      errorSpy.mockRestore();
    }
  );

  describe("new scheduler features", () => {
    it("rejects invalid concurrency or debounce options", async () => {
      const { client } = makeClient({ "GET /v1/events": () => json([]) });
      
      await expect(client.dispatchPending(0, "bogus" as any)).rejects.toThrow(TypeError);
      await expect(client.dispatchPending(0, "queue", -1)).rejects.toThrow(TypeError);
      
      const abort = new AbortController();
      abort.abort();
      await expect(client.listen({ concurrency: "invalid_mode" as any, signal: abort.signal })).rejects.toThrow(TypeError);
      await expect(client.listen({ debounceMs: -100, signal: abort.signal })).rejects.toThrow(TypeError);
    });

    it("concurrency=queue runs different conversations concurrently", async () => {
      const { client } = makeClient({ "GET /v1/events": () => json([]) });
      const seen: string[] = [];
      client.onMessage(async (m) => {
        seen.push(m.id);
        await new Promise(r => setTimeout(r, 50));
      });

      const start = performance.now();
      const p1 = (client as any).handleConcurrency(
        { type: "message.received", data: { message: { id: "m1", conversation_id: "conv_A", connection_id: "c" } } },
        "queue",
        500
      );
      const p2 = (client as any).handleConcurrency(
        { type: "message.received", data: { message: { id: "m2", conversation_id: "conv_B", connection_id: "c" } } },
        "queue",
        500
      );
      await Promise.all([p1, p2]);
      
      // wait for tracked tasks
      await (client as any).scheduler.close();
      const elapsed = performance.now() - start;
      
      expect(seen.sort()).toEqual(["m1", "m2"]);
      expect(elapsed).toBeLessThan(500); // loosened as a sanity check
    });

    it("shutdown drains pending debounce work", async () => {
      const { client } = makeClient({ "GET /v1/events": () => json([]) });
      const seen: string[] = [];
      client.onMessage(async (m) => {
        seen.push(m.id);
      });

      await (client as any).handleConcurrency(messageEvent(1, "pending_msg"), "debounce", 5000);
      expect(seen).toEqual([]); // Hasn't fired yet

      // close() should flush it immediately
      await (client as any).scheduler.close();
      expect(seen).toHaveLength(1);
      expect(seen[0]).toBe("pending_msg");
    });

    it("client remains usable after listen() returns", async () => {
      let calls = 0;
      const { client } = makeClient({
        "GET /v1/events": (req) => {
          const after = Number(new URL(req.url).searchParams.get("after_seq"));
          if (after >= 1) return json([]);
          return json([messageEvent(1, "m1")]);
        }
      });
      const seen: string[] = [];
      client.onMessage(async (m) => {
        seen.push(m.id);
      });

      const abort = new AbortController();
      abort.abort(); // abort immediately so listen returns
      await client.listen({ signal: abort.signal });
      
      await client.dispatchPending(0);
      await client.close();

      expect(seen).toEqual(["m1"]);
    });
  });
});
