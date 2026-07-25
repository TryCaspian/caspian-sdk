/**
 * OpenCode plugin — Caspian email bridge (bidirectional).
 *
 * Inbound: someone emails your Caspian inbox → OpenCode session → reply
 * Outbound: /caspian:email → email; /caspian:telegram → Telegram DM
 */

import { type Plugin, tool } from "@opencode-ai/plugin";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { CaspianOpenCodeBridge } from "./bridge.js";
import { resolveConfig, type PluginConfig } from "./config.js";
import {
  notifyInbound,
  showOpenCodeToast,
  showSystemNotification,
} from "./notify.js";
import { ensureCredentials } from "./onboard.js";
import type { OpenCodePort } from "./ports.js";

function loadRawConfig(): Record<string, unknown> {
  const path = join(homedir(), ".config", "opencode", "caspian.json");
  if (!existsSync(path)) return {};
  try {
    return JSON.parse(readFileSync(path, "utf-8")) as Record<string, unknown>;
  } catch (err) {
    console.warn("[caspian-opencode] failed to parse caspian.json", err);
    return {};
  }
}

function asOpenCodePort(client: unknown): OpenCodePort {
  return client as OpenCodePort;
}

async function log(
  client: Plugin extends (input: infer I) => unknown
    ? I extends { client: infer C }
      ? C
      : never
    : never,
  level: "debug" | "info" | "warn" | "error",
  message: string,
  extra?: Record<string, unknown>,
): Promise<void> {
  try {
    await client.app.log({
      body: { service: "caspian-opencode", level, message, extra },
    });
    return;
  } catch {
    // fall through
  }
  const line = `[caspian-opencode] ${message}`;
  if (level === "error") console.error(line, extra ?? "");
  else if (level === "warn") console.warn(line, extra ?? "");
  else console.info(line, extra ?? "");
}

/** Process-wide guard — OpenCode can load the same plugin module more than once. */
let pluginStarted = false;

export const CaspianPlugin: Plugin = async (input) => {
  const directory = input.directory ?? process.cwd();
  let config: PluginConfig = resolveConfig(loadRawConfig());
  const { client } = input;

  if (!config.switches.enabled) {
    await log(client, "info", "disabled via config");
    return {};
  }

  if (pluginStarted) {
    await log(
      client,
      "warn",
      "Caspian plugin already running in this process — skipping second init (prevents duplicate email replies)",
    );
    return {};
  }
  pluginStarted = true;

  try {
    const onboarded = await ensureCredentials({
      apiKey: config.apiKey,
      baseUrl: config.baseUrl,
      cwd: directory,
      projectName: config.displayName || "opencode",
      autoCreate: config.switches.autoOnboard,
    });

    if (!onboarded) {
      await log(
        client,
        "warn",
        "No CASPIAN_API_KEY and autoOnboard is off — plugin idle. Run: caspian init && caspian connect email",
      );
      return {};
    }

    if (onboarded.created) {
      await log(client, "info", "Created Caspian sandbox project (zero signup)", {
        source: onboarded.source,
        baseUrl: onboarded.baseUrl,
      });
      // First-run UX: surface success even when user never opened logs.
      void showOpenCodeToast(client, {
        title: "Caspian ready",
        message:
          onboarded.source === "sandbox-api"
            ? "Sandbox project created (no CLI needed). Connecting email…"
            : "Credentials created. Connecting email…",
        variant: "success",
      });
    } else {
      await log(client, "info", "Using existing Caspian credentials", {
        source: onboarded.source,
      });
    }

    config = resolveConfig(loadRawConfig(), process.env);
    config.apiKey = onboarded.apiKey;
    config.baseUrl = onboarded.baseUrl;
  } catch (err) {
    await log(client, "error", "Onboarding failed — plugin idle", {
      err: String(err),
    });
    void showOpenCodeToast(client, {
      title: "Caspian onboard failed",
      message: "Could not create sandbox credentials. Check network / gateway.",
      variant: "error",
    });
    return {};
  }

  const bridge = new CaspianOpenCodeBridge({
    config,
    opencode: asOpenCodePort(input.client),
    onInboundNotify: (info) => notifyInbound(client, config.notify, info),
  });

  // CP: start listen first. Connect/ensureEmail is NonCP — never block the
  // inbound switch on listConnections / connectEmail hangs.
  await log(
    client,
    "info",
    `Starting Caspian listen loop (channels: ${config.channels.join(", ")})`,
  );

  void bridge.start().catch(async (err) => {
    await log(client, "error", "Listen loop exited", { err: String(err) });
  });

  void (async () => {
    try {
      const address = await Promise.race([
        bridge.ensureEmail(),
        new Promise<null>((resolve) =>
          setTimeout(() => resolve(null), 15_000),
        ),
      ]);
      if (address) {
        await log(client, "info", `Email inbox ready: ${address}`, {
          connectionId: bridge.inboxConnectionId ?? undefined,
        });
        void showOpenCodeToast(client, {
          title: "Caspian inbox ready",
          message: `Email the agent at ${address}`,
          variant: "success",
        });
      } else if (!bridge.inboxAddress) {
        await log(
          client,
          "warn",
          "No email inbox yet (or connect timed out) — check CASPIAN_API_KEY / connectEmail",
        );
      }
    } catch (err) {
      await log(client, "warn", "ensureEmail failed (non-cp)", {
        err: String(err),
      });
    }
  })();

  return {
    tool: {
      caspian_reply_email: tool({
        description:
          "Reply on the CURRENT Caspian conversation for this OpenCode session " +
          "(email or Telegram). Prefer when the session shows [caspian:email] or " +
          "[caspian:telegram]. On a fresh inbound turn, answer in assistant text instead " +
          "(plugin auto-delivers). Not for brand-new emails — use caspian_send_email. " +
          "For email only: if unsure thread vs new mail, ASK first.",
        args: {
          body: tool.schema
            .string()
            .describe("Plain-text reply body (no need to quote the prior mail)"),
          messageId: tool.schema
            .string()
            .optional()
            .describe(
              "Optional Caspian message id to reply to (defaults to last inbound in this session)",
            ),
        },
        async execute(args, context) {
          const thread = bridge.threadForSession(context.sessionID);
          if (!thread && !args.messageId) {
            return {
              title: "Caspian reply — need choice",
              output: [
                "No inbound Caspian conversation is bound to this OpenCode session.",
                "",
                "Email: ask whether to open an email session or send a NEW email with caspian_send_email.",
                "Telegram: open/wait for the telegram session that received the message.",
              ].join("\n"),
            };
          }
          try {
            const result = await bridge.replyEmail({
              body: args.body,
              openCodeSessionId: context.sessionID,
              messageId: args.messageId,
            });
            const ch = thread?.channel ?? "email";
            return {
              title: `Reply → ${result.to ?? ch}`,
              output: [
                `Replied on Caspian ${ch} conversation.`,
                result.to ? `To: ${result.to}` : null,
                ch === "email" && result.subject
                  ? `Subject: ${result.subject}`
                  : null,
                `Caspian message: ${result.messageId}`,
                result.conversationId
                  ? `Conversation: ${result.conversationId}`
                  : null,
                `OpenCode session: ${context.sessionID}`,
              ]
                .filter(Boolean)
                .join("\n"),
              metadata: {
                mode: "reply",
                channel: ch,
                messageId: result.messageId,
                conversationId: result.conversationId,
                to: result.to,
                openCodeSessionId: context.sessionID,
              },
            };
          } catch (err) {
            return {
              title: "Caspian reply failed",
              output: String(err),
            };
          }
        },
      }),
      caspian_send_email: tool({
        description:
          "Start a NEW separate email via Caspian (initiate). " +
          "NOT for Telegram and NOT for answering an existing thread — use caspian_reply_email for that. " +
          "If this OpenCode session has inbound mail ([caspian:email]), you MUST ask the user: " +
          "reply on this thread, or send a separate new email? " +
          "If the session is [caspian:telegram], do not use this tool to answer Telegram. " +
          "Requires to + body + a real subject (never bare 'Re:' or empty).",
        args: {
          to: tool.schema
            .string()
            .describe("Recipient email address, e.g. friend@gmail.com"),
          body: tool.schema
            .string()
            .describe("Plain-text email body to send"),
          subject: tool.schema
            .string()
            .describe(
              "Required subject for a NEW email. Use a real topic, not bare 'Re:'.",
            ),
          connectionId: tool.schema
            .string()
            .optional()
            .describe(
              "Optional Caspian connection id to send from (overrides config)",
            ),
          confirmNewEmail: tool.schema
            .boolean()
            .optional()
            .describe(
              "Set true only after the user confirmed they want a NEW email (not a thread reply).",
            ),
        },
        async execute(args, context) {
          const thread = bridge.threadForSession(context.sessionID);
          if (
            thread &&
            thread.channel &&
            thread.channel !== "email" &&
            args.confirmNewEmail !== true
          ) {
            return {
              title: "Caspian send — wrong channel",
              output: [
                `This OpenCode session is a Caspian ${thread.channel} conversation, not email.`,
                "To answer on that chat, use caspian_reply_email (or just reply in assistant text on inbound).",
                "To send a separate NEW email anyway, call again with confirmNewEmail=true + to + subject + body.",
              ].join("\n"),
            };
          }
          if (
            thread &&
            (!thread.channel || thread.channel === "email") &&
            args.confirmNewEmail !== true
          ) {
            return {
              title: "Caspian send — confirm first",
              output: [
                "This OpenCode session is tied to an inbound email thread:",
                thread.from ? `  From: ${thread.from}` : null,
                thread.subject ? `  Subject: ${thread.subject}` : null,
                `  Conversation: ${thread.conversationId}`,
                "",
                "Ask the user which they want:",
                "1) Reply on this thread → call caspian_reply_email",
                "2) Separate NEW email → call caspian_send_email again with confirmNewEmail=true, to, subject, body",
                "",
                "Do not send until they choose.",
              ]
                .filter(Boolean)
                .join("\n"),
            };
          }
          try {
            const result = await bridge.sendEmail({
              to: args.to,
              body: args.body,
              subject: args.subject,
              connectionId: args.connectionId,
              openCodeSessionId: context.sessionID,
            });
            return {
              title: `Email → ${result.to}`,
              output: [
                `Sent NEW email via Caspian (new thread).`,
                `From: ${result.fromAddress || "(inbox)"}`,
                `To: ${result.to}`,
                `Subject: ${args.subject}`,
                `Connection: ${result.connectionId}`,
                `OpenCode session: ${context.sessionID}`,
                config.threading.sessionFooter
                  ? `Session footer stamped — replies sync to this TUI session.`
                  : null,
              ]
                .filter(Boolean)
                .join("\n"),
              metadata: {
                mode: "initiate",
                connectionId: result.connectionId,
                from: result.fromAddress,
                to: result.to,
                subject: args.subject,
                openCodeSessionId: context.sessionID,
                caspianConversationId: result.caspianConversationId,
              },
            };
          } catch (err) {
            return {
              title: "Caspian send failed",
              output: String(err),
            };
          }
        },
      }),
      caspian_send_telegram: tool({
        description:
          "Send a Telegram message via Caspian to @username or chat id. " +
          "Use for /caspian:telegram. Prefer this over email tools for Telegram. " +
          "Always mention Telegram's rule: the user must message the bot first " +
          "before a bot can DM them. If this session is already a telegram thread " +
          "with that user, prefer caspian_reply_email instead.",
        args: {
          to: tool.schema
            .string()
            .describe("Telegram @username or numeric chat id, e.g. @dipanshuhappy"),
          body: tool.schema
            .string()
            .describe("Plain-text message to send"),
          connectionId: tool.schema
            .string()
            .optional()
            .describe("Optional Caspian telegram connection id"),
        },
        async execute(args, context) {
          const thread = bridge.threadForSession(context.sessionID);
          if (
            thread?.channel === "telegram" &&
            thread.from &&
            args.to &&
            thread.from.replace(/^@/, "").toLowerCase() ===
              args.to.replace(/^@/, "").toLowerCase()
          ) {
            // Same peer as bound thread — reply is cleaner than re-initiate.
            try {
              const result = await bridge.replyEmail({
                body: args.body,
                openCodeSessionId: context.sessionID,
              });
              return {
                title: `Telegram reply → ${thread.from}`,
                output: [
                  `Replied on existing Telegram conversation.`,
                  `To: ${thread.from}`,
                  `Caspian message: ${result.messageId}`,
                  "",
                  "Note (Telegram): bots cannot start a private chat. The recipient must message your bot first before DMs will work.",
                ].join("\n"),
              };
            } catch {
              // fall through to sendTelegram
            }
          }
          try {
            const result = await bridge.sendTelegram({
              to: args.to,
              body: args.body,
              connectionId: args.connectionId,
              openCodeSessionId: context.sessionID,
            });
            return {
              title: `Telegram → ${result.to}`,
              output: [
                result.mode === "conversation"
                  ? `Sent Telegram message on an existing conversation.`
                  : `Queued Telegram cold-start (initiate).`,
                result.botAddress ? `Bot: ${result.botAddress}` : null,
                `To: ${result.to}`,
                `Connection: ${result.connectionId}`,
                result.conversationId
                  ? `Conversation: ${result.conversationId}`
                  : null,
                `OpenCode session: ${context.sessionID}`,
                "",
                result.note,
              ]
                .filter(Boolean)
                .join("\n"),
              metadata: {
                mode: result.mode,
                channel: "telegram",
                connectionId: result.connectionId,
                to: result.to,
                conversationId: result.conversationId,
                openCodeSessionId: context.sessionID,
              },
            };
          } catch (err) {
            return {
              title: "Caspian Telegram send failed",
              output: String(err),
            };
          }
        },
      }),
      caspian_send_discord: tool({
        description:
          "Post a Discord message via Caspian to a channel snowflake id. " +
          "Use for /caspian:discord. Always mention: invite the bot and use " +
          "Copy Channel ID (Developer Mode). If this session is already a " +
          "[caspian:discord] thread, prefer caspian_reply_email for on-thread reply.",
        args: {
          to: tool.schema
            .string()
            .describe("Discord channel snowflake id, e.g. 123456789012345678"),
          body: tool.schema
            .string()
            .describe("Plain-text message to post"),
          connectionId: tool.schema
            .string()
            .optional()
            .describe("Optional Caspian discord connection id"),
        },
        async execute(args, context) {
          const thread = bridge.threadForSession(context.sessionID);
          if (thread?.channel === "discord" && !args.connectionId) {
            try {
              const result = await bridge.replyEmail({
                body: args.body,
                openCodeSessionId: context.sessionID,
              });
              return {
                title: `Discord reply → thread`,
                output: [
                  `Replied on existing Discord conversation.`,
                  thread.from ? `Peer: ${thread.from}` : null,
                  `Caspian message: ${result.messageId}`,
                  "",
                  "Note (Discord): outbound /caspian:discord uses a channel snowflake id.",
                ]
                  .filter(Boolean)
                  .join("\n"),
              };
            } catch {
              // fall through
            }
          }
          try {
            const result = await bridge.sendDiscord({
              to: args.to,
              body: args.body,
              connectionId: args.connectionId,
              openCodeSessionId: context.sessionID,
            });
            return {
              title: `Discord → ${result.to}`,
              output: [
                result.mode === "conversation"
                  ? `Sent Discord message on an existing conversation.`
                  : `Posted to Discord channel via initiate.`,
                result.botAddress ? `Bot: ${result.botAddress}` : null,
                `Channel id: ${result.to}`,
                `Connection: ${result.connectionId}`,
                result.conversationId
                  ? `Conversation: ${result.conversationId}`
                  : null,
                `OpenCode session: ${context.sessionID}`,
                "",
                result.note,
              ]
                .filter(Boolean)
                .join("\n"),
              metadata: {
                mode: result.mode,
                channel: "discord",
                connectionId: result.connectionId,
                to: result.to,
                conversationId: result.conversationId,
                openCodeSessionId: context.sessionID,
              },
            };
          } catch (err) {
            return {
              title: "Caspian Discord send failed",
              output: String(err),
            };
          }
        },
      }),
      caspian_test_toast: tool({
        description:
          "DUMMY test tool: show an OpenCode TUI toast (and optional macOS/Linux " +
          "system notification). Use when the user runs /caspian:toast, loads the " +
          "caspian-toast-test skill, or asks to test Caspian toast UI. Does not send email.",
        args: {
          title: tool.schema
            .string()
            .optional()
            .describe("Toast title (default: Caspian toast test)"),
          message: tool.schema
            .string()
            .optional()
            .describe("Toast message body"),
          variant: tool.schema
            .string()
            .optional()
            .describe("info | success | warning | error (default info)"),
          system: tool.schema
            .boolean()
            .optional()
            .describe("Also fire a desktop/system notification (default true)"),
        },
        async execute(args) {
          const variantRaw = (args.variant ?? "info").toLowerCase();
          const variant = (
            ["info", "success", "warning", "error"] as const
          ).includes(variantRaw as "info")
            ? (variantRaw as "info" | "success" | "warning" | "error")
            : "info";
          const payload = {
            title: args.title?.trim() || "Caspian toast test",
            message:
              args.message?.trim() ||
              "Dummy toast from caspian_test_toast — UI OK if you see this.",
            variant,
          };
          const toastOk = await showOpenCodeToast(client, payload);
          const systemOk =
            args.system === false
              ? false
              : await showSystemNotification(payload);
          return {
            title: "Toast test",
            output: [
              `OpenCode toast: ${toastOk ? "shown" : "FAILED (tui.showToast unavailable)"}`,
              `System notification: ${
                args.system === false
                  ? "skipped"
                  : systemOk
                    ? "fired"
                    : "FAILED / unsupported"
              }`,
              `Title: ${payload.title}`,
              `Message: ${payload.message}`,
              `Variant: ${payload.variant}`,
            ].join("\n"),
          };
        },
      }),
      caspian_inbox: tool({
        description:
          "Caspian Inbox (caspian:Inbox): list connections and recent " +
          "conversations/messages across configured channels (email and others). " +
          "Use when the user asks for inbox, unread mail, or channel messages. " +
          "Pass list=true (default) to fetch messages; list=false for address only.",
        args: {
          list: tool.schema
            .boolean()
            .optional()
            .describe(
              "If true (default), list conversations/messages. If false, only show inbox address.",
            ),
          channels: tool.schema
            .string()
            .optional()
            .describe(
              "Comma-separated channel filter, e.g. email or email,slack. Default: plugin config channels.",
            ),
          conversationLimit: tool.schema
            .number()
            .optional()
            .describe("Max conversations per connection (default 10, max 50)"),
          messageLimit: tool.schema
            .number()
            .optional()
            .describe("Max messages per conversation (default 5, max 50)"),
        },
        async execute(args, context) {
          const addr =
            bridge.inboxAddress ||
            "(unknown — check logs or run: caspian status)";
          const header = [
            `Agent inbox: ${addr}`,
            bridge.inboxConnectionId
              ? `Connection: ${bridge.inboxConnectionId}`
              : null,
            `OpenCode session: ${context.sessionID}`,
            `Session footer sync: ${config.threading.sessionFooter ? "on" : "off"}`,
            `Configured channels: ${config.channels.join(", ") || "(none)"}`,
          ]
            .filter(Boolean)
            .join("\n");

          if (args.list === false) {
            return { title: "Caspian inbox", output: header };
          }

          const channels = args.channels
            ? args.channels
                .split(",")
                .map((c) => c.trim().toLowerCase())
                .filter(Boolean)
            : undefined;

          try {
            const listing = await bridge.formatInbox({
              channels,
              conversationLimit: args.conversationLimit,
              messageLimit: args.messageLimit,
            });
            return {
              title: "Caspian inbox",
              output: `${header}\n\n${listing}`,
            };
          } catch (err) {
            return {
              title: "Caspian inbox",
              output: `${header}\n\nFailed to list messages: ${String(err)}`,
            };
          }
        },
      }),
      caspian_connections: tool({
        description:
          "List Caspian connections and plugin admit channels from caspian.json. " +
          "Use before connect-email / connect-telegram / connect-discord to see what is already linked.",
        args: {},
        async execute() {
          try {
            const status = await bridge.connectionStatus();
            const lines = status.connections.length
              ? status.connections.map(
                  (c) =>
                    `- ${c.channel} ${c.status} id=${c.id}` +
                    (c.address ? ` address=${c.address}` : ""),
                )
              : ["(no connections)"];
            return {
              title: "Caspian connections",
              output: [
                "Caspian connections:",
                ...lines,
                "",
                `Plugin admit channels (caspian.json): ${status.pluginChannels.join(", ") || "(none)"}`,
                `In-memory admit (this process): ${config.channels.join(", ") || "(none)"}`,
                status.restartHint,
              ].join("\n"),
            };
          } catch (err) {
            return {
              title: "Caspian connections",
              output: `Failed: ${String(err)}`,
            };
          }
        },
      }),
      caspian_connect_email: tool({
        description:
          "Connect Caspian email (or reuse existing) and enable email in " +
          "~/.config/opencode/caspian.json channels. Tell the user to restart " +
          "OpenCode if restartRequired is true so admit picks up channels.",
        args: {
          displayName: tool.schema
            .string()
            .optional()
            .describe("Display name for a new email connection"),
        },
        async execute(args) {
          try {
            const result = await bridge.connectEmailChannel({
              displayName: args.displayName,
            });
            const lines = [
              result.alreadyConnected
                ? "Email already connected on Caspian."
                : "Email connected on Caspian.",
              `Address: ${result.address || "(unknown)"}`,
              result.connectionId
                ? `Connection id: ${result.connectionId}`
                : null,
              `Plugin channels: ${result.pluginChannels.join(", ")}`,
              result.restartRequired
                ? "RESTART REQUIRED: quit and relaunch OpenCode so admit loads channels from caspian.json."
                : "Admit already included email in this process (still restart if you changed other config).",
            ].filter(Boolean);
            return {
              title: "Caspian connect email",
              output: lines.join("\n"),
            };
          } catch (err) {
            return {
              title: "Caspian connect email",
              output: `Failed: ${String(err)}`,
            };
          }
        },
      }),
      caspian_connect_telegram: tool({
        description:
          "Connect Caspian Telegram from TELEGRAM_BOT_TOKEN (env / .env / " +
          "~/.config/opencode/caspian.env) and enable telegram in caspian.json " +
          "channels. Never store the token in caspian.json. Tell user to restart " +
          "OpenCode when telegram was newly added to channels.",
        args: {
          displayName: tool.schema
            .string()
            .optional()
            .describe("Display name for the Telegram connection"),
        },
        async execute(args) {
          try {
            const result = await bridge.connectTelegramChannel({
              cwd: process.cwd(),
              displayName: args.displayName,
            });
            if (!result.ok) {
              return {
                title: "Caspian connect telegram",
                output: result.setup,
              };
            }
            const lines = [
              result.alreadyConnected
                ? "Telegram already connected on Caspian."
                : "Telegram connected on Caspian.",
              `Connection id: ${result.connectionId}`,
              result.address ? `Address: ${result.address}` : null,
              result.tokenSource
                ? `Token source: ${result.tokenSource}`
                : null,
              `Plugin channels: ${result.pluginChannels.join(", ")}`,
              result.restartRequired
                ? "RESTART REQUIRED: quit and relaunch OpenCode so admit includes telegram (otherwise Telegram events are rejected)."
                : "telegram already in admit channels for a fresh process; restart if this process still shows email-only.",
            ].filter(Boolean);
            return {
              title: "Caspian connect telegram",
              output: lines.join("\n"),
            };
          } catch (err) {
            return {
              title: "Caspian connect telegram",
              output: `Failed: ${String(err)}`,
            };
          }
        },
      }),
      caspian_connect_discord: tool({
        description:
          "Connect Caspian Discord: reuse active connection, BYO DISCORD_BOT_TOKEN " +
          "(env / .env / caspian.env), or one-click installDiscord() (authorize URL). " +
          "Enables discord in caspian.json channels. Non-CP connect — tell user to " +
          "restart OpenCode so admit includes discord. Never store the token in caspian.json.",
        args: {
          displayName: tool.schema
            .string()
            .optional()
            .describe("Display name for the Discord bot / install"),
          preferInstall: tool.schema
            .boolean()
            .optional()
            .describe(
              "If true, use one-click installDiscord even when DISCORD_BOT_TOKEN is set",
            ),
        },
        async execute(args) {
          try {
            const result = await bridge.connectDiscordChannel({
              cwd: process.cwd(),
              displayName: args.displayName,
              preferInstall: args.preferInstall,
            });
            if (!result.ok) {
              return {
                title: "Caspian connect discord",
                output: result.error,
              };
            }
            const lines = [
              result.alreadyConnected
                ? "Discord connection already present on Caspian."
                : result.mode === "install"
                  ? "Discord install started (OAuth)."
                  : "Discord connected with bot token.",
              `Mode: ${result.mode}`,
              `Connection id: ${result.connectionId}`,
              `Status: ${result.status}`,
              result.address ? `Address: ${result.address}` : null,
              result.tokenSource ? `Token source: ${result.tokenSource}` : null,
              result.authorizeUrl
                ? `Authorize URL (open in browser, invite bot to server):\n  ${result.authorizeUrl}`
                : null,
              `Plugin channels: ${result.pluginChannels.join(", ")}`,
              result.restartRequired
                ? "RESTART REQUIRED: quit and relaunch OpenCode so admit includes discord (otherwise Discord events are rejected at the blast-radius wall)."
                : "discord already in admit channels for a fresh process; restart if this process still omits discord.",
              result.status !== "active"
                ? "Finish OAuth / invite until connection status is active, then restart OpenCode."
                : null,
            ].filter(Boolean);
            return {
              title: "Caspian connect discord",
              output: lines.join("\n"),
            };
          } catch (err) {
            return {
              title: "Caspian connect discord",
              output: `Failed: ${String(err)}`,
            };
          }
        },
      }),
    },
    event: async ({ event }) => {
      if (event.type === "session.idle") {
        // reserved
      }
    },
  };
};

export default CaspianPlugin;

export { CaspianOpenCodeBridge } from "./bridge.js";
export { resolveConfig } from "./config.js";
export { ensureCredentials, mintSandboxKey, initViaCli } from "./onboard.js";
export { handleInbound } from "./pipeline.js";
export {
  admits,
  toEnvelope,
  formatInboundPrompt,
  formatEmailPrompt,
} from "./email.js";
export { sendEmail, formatOutboundText } from "./outbound.js";
export {
  sessionMapKey,
  sessionTitle,
  DEFAULT_THREADING,
} from "./threading.js";
export {
  appendSessionFooter,
  extractSessionId,
  stripSessionFooter,
} from "./session-footer.js";
export {
  formatInboxSnapshot,
  listInbox,
} from "./inbox.js";
export {
  DEFAULT_THINKING,
  extractAssistantText,
  thinkingEnabledForChannel,
} from "./thinking.js";
export {
  TELEGRAM_BOT_DM_NOTE,
  normalizeTelegramRecipient,
  sendTelegram,
} from "./telegram-send.js";
export {
  DISCORD_CHANNEL_NOTE,
  normalizeDiscordRecipient,
  sendDiscord,
} from "./discord-send.js";
