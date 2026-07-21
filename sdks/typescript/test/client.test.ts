import { describe, expect, it, vi } from "vitest";
import { CommClient, CommError, Message } from "../src/index.js";

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

describe("CommClient", () => {
  it("requires an API key", () => {
    delete process.env.COMM_API_KEY;
    expect(() => new CommClient({ baseUrl: "http://gw" })).toThrow(CommError);
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

  it("connectGitHub maps App credentials and installGitHub uses install endpoint", async () => {
    const { client, calls } = makeClient({
      "POST /v1/connections/github": () =>
        json({ id: "gh1", status: "pending_oauth", authorize_url: "https://github/install" }),
      "POST /v1/connections/github/install": () =>
        json({ id: "gh2", status: "pending_oauth", authorize_url: "https://github/shared" }),
    });
    const connected = await client.connectGitHub({
      githubAppId: "123",
      githubAppSlug: "my-app",
      githubPrivateKey: "pem",
      githubWebhookSecret: "secret",
      customerId: "cus_1",
    });
    const installed = await client.installGitHub({ displayName: "Review Agent" });

    expect(connected.authorize_url).toBe("https://github/install");
    expect(installed.authorize_url).toBe("https://github/shared");
    expect(calls[0].body).toMatchObject({
      github_app_id: "123",
      github_app_slug: "my-app",
      github_private_key: "pem",
      github_webhook_secret: "secret",
      receive_mode: "mentions",
      customer_id: "cus_1",
    });
    expect(calls[1].path).toBe("/v1/connections/github/install");
    expect(calls[1].body).toMatchObject({
      display_name: "Review Agent",
      receive_mode: "mentions",
    });
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
    expect(replies[0]).toEqual({ text: "echo: hi", html: null });
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
