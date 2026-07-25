/**
 * Inbound email alerts — OpenCode toast + OS notification.
 * Runs from the server plugin (no tui.json / TUI plugin).
 */

import { spawn } from "node:child_process";

export interface NotifyConfig {
  /** Master switch. Default true. */
  enabled: boolean;
  /** In-app OpenCode toast. Default true. */
  toast: boolean;
  /** macOS Notification Center / Linux notify-send. Default true. */
  system: boolean;
}

export const DEFAULT_NOTIFY: NotifyConfig = {
  enabled: true,
  toast: true,
  system: true,
};

export interface NotifyPayload {
  title: string;
  message: string;
  variant?: "info" | "success" | "warning" | "error";
}

type ToastClient = {
  tui?: {
    showToast?: (args: unknown) => Promise<unknown>;
  };
};

/** Escape for AppleScript string literals. */
export function escapeAppleScript(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

export function formatInboundNotify(info: {
  channel?: string;
  from?: string;
  subject?: string | null;
  text?: string;
}): NotifyPayload {
  const channel = (info.channel || "email").toLowerCase();
  const from = info.from?.trim() || "unknown";
  const subject = info.subject?.trim() || "";
  const preview = (info.text || "").replace(/\s+/g, " ").trim().slice(0, 80);

  if (channel === "telegram") {
    return {
      title: "Caspian Telegram",
      message: preview ? `From ${from} — ${preview}` : `From ${from}`,
      variant: "info",
    };
  }

  if (channel === "discord") {
    return {
      title: "Caspian Discord",
      message: preview ? `From ${from} — ${preview}` : `From ${from}`,
      variant: "info",
    };
  }

  // Bare "Re:" already resolved upstream when possible; keep fallback here.
  const label =
    !subject || /^(?:\(no subject\)|re\s*:?\s*)$/i.test(subject)
      ? preview || "(no subject)"
      : subject;
  return {
    title: channel === "email" ? "Caspian email" : `Caspian ${channel}`,
    message: `From ${from} — ${label}`,
    variant: "info",
  };
}

/** Best-effort OpenCode TUI toast (server SDK). */
export async function showOpenCodeToast(
  client: unknown,
  payload: NotifyPayload,
): Promise<boolean> {
  const tui = (client as ToastClient).tui;
  if (!tui?.showToast) return false;
  const body = {
    title: payload.title,
    message: payload.message,
    variant: payload.variant ?? "info",
    duration: 5000,
  };
  try {
    // Prefer body shape (REST); fall back to flat params (some SDK wrappers).
    await tui.showToast({ body });
    return true;
  } catch {
    try {
      await tui.showToast(body);
      return true;
    } catch {
      return false;
    }
  }
}

/** Escape for PowerShell single-quoted strings. */
export function escapePowerShell(s: string): string {
  return s.replace(/'/g, "''");
}

/**
 * Fire a desktop notification without blocking the critical path.
 * - macOS: osascript Notification Center
 * - Linux: notify-send (libnotify; install if missing)
 * - Windows: PowerShell balloon tip (System.Windows.Forms.NotifyIcon)
 */
export function showSystemNotification(payload: NotifyPayload): Promise<boolean> {
  return new Promise((resolve) => {
    const title = payload.title.slice(0, 120);
    const message = payload.message.slice(0, 240);

    if (process.platform === "darwin") {
      const script = `display notification "${escapeAppleScript(message)}" with title "${escapeAppleScript(title)}" sound name "Glass"`;
      const child = spawn("osascript", ["-e", script], {
        stdio: "ignore",
        detached: true,
      });
      child.unref();
      child.on("error", () => resolve(false));
      child.on("close", (code) => resolve(code === 0));
      return;
    }

    if (process.platform === "linux") {
      const child = spawn("notify-send", ["-a", "Caspian", title, message], {
        stdio: "ignore",
        detached: true,
      });
      child.unref();
      child.on("error", () => resolve(false));
      child.on("close", (code) => resolve(code === 0));
      return;
    }

    if (process.platform === "win32") {
      // Balloon tip — works without extra modules (Windows.Forms).
      const ps = [
        "Add-Type -AssemblyName System.Windows.Forms",
        "Add-Type -AssemblyName System.Drawing",
        "$n = New-Object System.Windows.Forms.NotifyIcon",
        "$n.Icon = [System.Drawing.SystemIcons]::Information",
        "$n.Visible = $true",
        `$n.ShowBalloonTip(5000, '${escapePowerShell(title)}', '${escapePowerShell(message)}', [System.Windows.Forms.ToolTipIcon]::Info)`,
        "Start-Sleep -Seconds 6",
        "$n.Dispose()",
      ].join("; ");
      const child = spawn(
        "powershell.exe",
        ["-NoProfile", "-NonInteractive", "-Command", ps],
        { stdio: "ignore", detached: true, windowsHide: true },
      );
      child.unref();
      child.on("error", () => resolve(false));
      child.on("close", (code) => resolve(code === 0));
      return;
    }

    resolve(false);
  });
}

export async function notifyInbound(
  client: unknown,
  cfg: NotifyConfig,
  info: {
    channel?: string;
    from?: string;
    subject?: string | null;
    text?: string;
  },
): Promise<void> {
  if (!cfg.enabled) return;
  const payload = formatInboundNotify(info);
  const jobs: Promise<unknown>[] = [];
  if (cfg.toast) jobs.push(showOpenCodeToast(client, payload));
  if (cfg.system) jobs.push(showSystemNotification(payload));
  await Promise.allSettled(jobs);
}
