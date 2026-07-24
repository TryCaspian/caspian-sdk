import { describe, expect, it } from "vitest";
import { CommClient, Message } from "../src/index.js";

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

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

describe("Streaming", () => {
  it("uses post_edit when edit_outbound is supported", async () => {
    const { client, calls } = makeClient({
      "GET /v1/connections/conn_1": () => json({ capabilities: ["edit_outbound"] }),
      "POST /v1/messages/msg_1/reply": () => json({ id: "reply_1" }),
      "PATCH /v1/messages/reply_1": () => json({ id: "reply_1" }),
    });

    // @ts-expect-error accessing private constructor
    const msg = new Message("msg_1", "conv_1", "conn_1", "cus_1", "agt_1", "slack", null, null, "hi", null, client, []);
    
    const stream = await msg.stream({ editIntervalMs: 10 });
    await stream.append("chunk1");
    await sleep(20);
    await stream.append(" chunk2");
    await sleep(20);
    await stream.append(" chunk3");
    
    // Assert a PATCH has happened before finalize()
    expect(calls.some(c => c.method === "PATCH")).toBe(true);
    // Ensure the last call before finalize was a PATCH
    expect(calls[calls.length - 1].method).toBe("PATCH");
    
    await stream.finalize();

    // GET connection, POST reply, PATCH reply (x1 or x2 depending on timing), final PATCH
    expect(calls.length).toBeGreaterThanOrEqual(4);
    expect(calls[0].method).toBe("GET");
    expect(calls[0].path).toBe("/v1/connections/conn_1");
    
    expect(calls[1].method).toBe("POST");
    expect(calls[1].path).toBe("/v1/messages/msg_1/reply");
    expect(calls[1].body.text).toBe("chunk1");

    const lastCall = calls[calls.length - 1];
    expect(lastCall.method).toBe("PATCH");
    expect(lastCall.path).toBe("/v1/messages/reply_1");
    expect(lastCall.body.text).toBe("chunk1 chunk2 chunk3");
  });

  it("uses final_only when edit_outbound is unsupported", async () => {
    const { client, calls } = makeClient({
      "GET /v1/connections/conn_1": () => json({ capabilities: [] }), // missing edit_outbound
      "POST /v1/messages/msg_1/reply": () => json({ id: "reply_1" }),
    });

    // @ts-expect-error accessing private constructor
    const msg = new Message("msg_1", "conv_1", "conn_1", "cus_1", "agt_1", "email", null, null, "hi", null, client, []);
    
    const stream = await msg.stream({ editIntervalMs: 10 });
    await stream.append("chunk1");
    await sleep(20);
    await stream.append(" chunk2");
    await sleep(20);
    await stream.append(" chunk3");
    await stream.finalize();

    expect(calls.length).toBe(2);
    expect(calls[0].method).toBe("GET");
    expect(calls[0].path).toBe("/v1/connections/conn_1");
    
    expect(calls[1].method).toBe("POST");
    expect(calls[1].path).toBe("/v1/messages/msg_1/reply");
    expect(calls[1].body.text).toBe("chunk1 chunk2 chunk3");
  });
});
