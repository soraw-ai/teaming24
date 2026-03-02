/**
 * Teaming24 OpenClaw Plugin
 *
 * Registers teaming24_* tools for the OpenClaw LLM agent so users can
 * trigger multi-agent tasks, query the distributed network, and check
 * wallet state from any connected channel (WhatsApp, Telegram, Slack, etc.).
 *
 * Tools registered:
 *   teaming24_execute   — Multi-agent task execution with live progress stream
 *   teaming24_delegate  — Delegate task to a specific network peer
 *   teaming24_wallet    — Query wallet balance, transactions, or summary
 *   teaming24_network   — List connected peers or node status
 *
 * Installation:
 *   1. Copy this directory to ~/.openclaw/extensions/teaming24/
 *   2. Add to openclaw.json:
 *        "plugins": { "entries": { "teaming24": { "enabled": true } } }
 *   3. Restart OpenClaw
 *
 * Configuration (openclaw.json plugins.entries.teaming24.config):
 *   {
 *     "base_url": "http://localhost:8000",   // Teaming24 API URL
 *     "token": "",                           // X-OpenClaw-Token (blank = no auth)
 *     "stream_progress": true,               // Show step-by-step progress
 *     "progress_interval_ms": 2000           // Min ms between progress messages
 *   }
 */

// ── Types ─────────────────────────────────────────────────────────────────────

interface PluginConfig {
  base_url?: string;
  token?: string;
  stream_progress?: boolean;
  progress_interval_ms?: number;
}

interface ContentBlock {
  type: "text";
  text: string;
}

interface ToolResult {
  content: ContentBlock[];
}

// OpenClaw plugin API (minimal typing — full types in @openclaw/sdk if available)
interface PluginApi {
  config: PluginConfig;
  registerTool(opts: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
    execute(
      id: string,
      params: Record<string, unknown>,
      ctx?: { sessionKey?: string }
    ): Promise<ToolResult>;
  }): void;
  /** Send a message to a session (if available on the plugin API) */
  sessions?: {
    send?(sessionKey: string, message: string): Promise<void>;
  };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function text(content: string): ToolResult {
  return { content: [{ type: "text", text: content }] };
}

/** Format number without trailing zeros (0.01 not 0.0100). */
function formatNum(num: number, maxDecimals = 6): string {
  return num.toFixed(maxDecimals).replace(/\.?0+$/, "");
}

/**
 * Return the configured Teaming24 base URL (no trailing slash).
 * Extracted to avoid duplication between callApi and executeStream.
 */
function getBaseUrl(api: PluginApi): string {
  return (api.config.base_url || "http://localhost:8000").replace(/\/$/, "");
}

/**
 * Build request headers for Teaming24 API calls.
 * Extracted to avoid duplication between callApi and executeStream.
 */
function buildHeaders(api: PluginApi): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = api.config.token || "";
  if (token) {
    headers["X-OpenClaw-Token"] = token;
  }
  return headers;
}

/**
 * Convert a backend timestamp to milliseconds.
 * Backend sends Unix seconds (float); JS Date expects milliseconds.
 * Pattern from project memory: ts > 1e12 ? ts : ts * 1000
 */
function toMs(ts: number): number {
  return ts > 1e12 ? ts : ts * 1000;
}

async function callApi(
  api: PluginApi,
  path: string,
  options: RequestInit = {}
): Promise<unknown> {
  const base = getBaseUrl(api);
  const headers = buildHeaders(api);
  const resp = await fetch(`${base}${path}`, { ...options, headers });
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status}: ${body.slice(0, 200)}`);
  }
  return resp.json();
}

/**
 * Call POST /api/openclaw/execute and return a generator that yields
 * SSE events as they arrive.
 *
 * Each yielded value is { event, data } — e.g.:
 *   { event: "started",   data: { task_id: "t-..." } }
 *   { event: "step",      data: { agent, action, content } }
 *   { event: "progress",  data: { progress: {...} } }
 *   { event: "completed", data: { result, cost } }
 *   { event: "failed",    data: { error } }
 */
async function* executeStream(
  api: PluginApi,
  prompt: string,
  sessionKey: string | undefined
): AsyncGenerator<{ event: string; data: Record<string, unknown> }> {
  const base = getBaseUrl(api);
  const headers = buildHeaders(api);

  const resp = await fetch(`${base}/api/openclaw/execute`, {
    method: "POST",
    headers,
    // session_key (snake_case) is the backend field name; sessionKey is the TS variable.
    body: JSON.stringify({ prompt, session_key: sessionKey }),
  });

  if (!resp.ok || !resp.body) {
    const body = await resp.text().catch(() => "");
    throw new Error(`Execute failed HTTP ${resp.status}: ${body.slice(0, 200)}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Parse SSE frames: "event: X\ndata: {...}\n\n"
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const lines = frame.split("\n").filter((l) => l.trim());
      let event = "message";
      let dataStr = "";
      for (const line of lines) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataStr = line.slice(5).trim();
        else if (line.startsWith(": ")) continue; // keepalive comment — skip
      }
      if (!dataStr) continue;
      try {
        const data = JSON.parse(dataStr);
        yield { event, data };
      } catch {
        // non-JSON data line, skip
      }
    }
  }
}

// ── Plugin entry ──────────────────────────────────────────────────────────────

export default function (api: PluginApi) {
  const streamProgress = api.config.stream_progress !== false;
  const progressInterval = Math.max(
    200,
    Number(api.config.progress_interval_ms ?? 2000)
  );

  // ── teaming24_execute ───────────────────────────────────────────────────────
  api.registerTool({
    name: "teaming24_execute",
    description:
      "Execute a complex multi-agent task using Teaming24's Organizer → Coordinator → Workers pipeline. " +
      "Includes automatic payment gate check, cost tracking, and distributed network delegation. " +
      "Best for: research, coding, data analysis, web scraping, multi-step workflows. " +
      "Provides live step-by-step progress while the agents work.",
    parameters: {
      type: "object",
      properties: {
        prompt: {
          type: "string",
          description: "Detailed task description",
        },
      },
      required: ["prompt"],
    },
    async execute(_id, { prompt }, ctx) {
      const sessionKey = ctx?.sessionKey;
      const steps: string[] = [];
      let result = "";
      let lastProgressAt = 0;

      try {
        for await (const { event, data } of executeStream(api, String(prompt), sessionKey)) {
          if (event === "started") {
            if (streamProgress && sessionKey && api.sessions?.send) {
              await api.sessions.send(
                sessionKey,
                `🚀 Task started (ID: ${data.task_id})\nOrganizer → Coordinator → Workers pipeline running…`
              );
            }
          } else if (event === "step") {
            const agent = String(data.agent || "Agent");
            const action = String(data.action || "processing");
            const content = String(data.content || "");
            const stepMsg = `**[${agent}]** \`${action}\`${content ? `\n${content.slice(0, 200)}` : ""}`;
            steps.push(stepMsg);

            if (streamProgress && sessionKey && api.sessions?.send) {
              const now = Date.now();
              if (now - lastProgressAt >= progressInterval) {
                await api.sessions.send(sessionKey, stepMsg);
                lastProgressAt = now;
              }
            }
          } else if (event === "progress") {
            if (streamProgress && sessionKey && api.sessions?.send) {
              const prog = data.progress as Record<string, unknown> | undefined;
              const now = Date.now();
              const pct = typeof prog?.percentage === "number" ? prog.percentage : Number(prog?.percentage ?? -1);
              // Only send at 25 % intervals and when throttle allows.
              if (pct > 0 && Number.isFinite(pct) && pct % 25 === 0 && now - lastProgressAt >= progressInterval) {
                await api.sessions.send(
                  sessionKey,
                  `📊 Progress: ${pct}% — ${prog?.phase || ""}`
                );
                lastProgressAt = now;
              }
            }
          } else if (event === "completed") {
            result = String(data.result || "");
            const cost = data.cost as Record<string, unknown> | undefined;
            if (cost && (cost.total_tokens || cost.cost_usd)) {
              const costLine = cost.cost_usd
                ? `\n\n*Cost: $${cost.cost_usd} USDC*`
                : `\n\n*Tokens used: ${cost.total_tokens}*`;
              result += costLine;
            }
          } else if (event === "failed") {
            throw new Error(String(data.error || "Task failed"));
          }
        }
      } catch (err) {
        return text(`Task execution failed: ${err instanceof Error ? err.message : String(err)}`);
      }

      if (!result && steps.length > 0) {
        result = steps.join("\n\n");
      }
      return text(result || "Task completed (no output)");
    },
  });

  // ── teaming24_delegate ──────────────────────────────────────────────────────
  api.registerTool({
    name: "teaming24_delegate",
    description:
      "Delegate a task to a specific node on the Teaming24 distributed network. " +
      "Use teaming24_network first to discover available peers and their capabilities.",
    parameters: {
      type: "object",
      properties: {
        prompt: { type: "string", description: "Task description" },
        node_id: {
          type: "string",
          description: "Target node ID or name (from teaming24_network)",
        },
      },
      required: ["prompt", "node_id"],
    },
    async execute(_id, { prompt, node_id }) {
      try {
        const data = (await callApi(api, "/api/openclaw/delegate", {
          method: "POST",
          body: JSON.stringify({ prompt, node_id }),
        })) as Record<string, unknown>;

        const result = String(data.result || "");
        const peer = String(data.node_id || node_id);
        return text(`[Delegated to ${peer}]\n\n${result}`);
      } catch (err) {
        return text(`Delegation failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
  });

  // ── teaming24_wallet ────────────────────────────────────────────────────────
  api.registerTool({
    name: "teaming24_wallet",
    description:
      "Query the Teaming24 wallet: balance, recent transactions, or financial summary. " +
      "Wallet holds USDC on Base blockchain; used for x402 AN-to-AN payments.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["balance", "transactions", "summary"],
          description: "What to query (default: balance)",
        },
        limit: {
          type: "integer",
          description: "Number of recent transactions to return (default: 10)",
        },
      },
    },
    async execute(_id, { action = "balance", limit = 10 }) {
      try {
        const params = new URLSearchParams({
          action: String(action),
          limit: String(Number(limit) || 10),
        });
        const data = (await callApi(api, `/api/openclaw/wallet?${params}`)) as Record<
          string,
          unknown
        >;

        if (action === "balance") {
          const addr = String(data.address || "not configured");
          const shortAddr =
            addr.length > 10 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr;
          return text(
            `**Teaming24 Wallet**\n` +
              `Balance: ${data.balance} ${data.currency}\n` +
              `Network: ${data.network}\n` +
              `Address: ${shortAddr}\n` +
              `Configured: ${data.is_configured ? "✓" : "✗"}`
          );
        }

        if (action === "transactions") {
          const txs = (data.transactions as unknown[]) || [];
          if (txs.length === 0) return text("No transactions found.");
          const total = Number(data.total ?? 0);
          const lines = txs.map((t) => {
            const tx = t as Record<string, unknown>;
            // Backend sends Unix seconds; convert to ms for JS Date.
            const tsRaw = Number(tx.timestamp ?? 0);
            const tsMs = toMs(tsRaw);
            const dateStr = tsMs ? new Date(tsMs).toISOString().slice(0, 16) : "?";
            return `[${dateStr}] ${String(tx.type || "?").padEnd(8)} $${formatNum(
              Number(tx.amount ?? 0),
              6
            )}  ${tx.description || ""}`;
          });
          return text(`**Recent Transactions** (${total} total)\n\n${lines.join("\n")}`);
        }

        if (action === "summary") {
          return text(
            `**Wallet Summary**\n` +
              `Balance:       ${formatNum(Number(data.balance ?? 0))} ${data.currency}\n` +
              `Total income:  ${formatNum(Number(data.total_income ?? 0))} USDC\n` +
              `Total expense: ${formatNum(Number(data.total_expense ?? 0))} USDC\n` +
              `Net profit:    ${formatNum(Number(data.net_profit ?? 0))} USDC\n` +
              `Transactions:  ${Number(data.transaction_count ?? 0)}`
          );
        }

        return text(JSON.stringify(data, null, 2));
      } catch (err) {
        return text(`Wallet query failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
  });

  // ── teaming24_network ───────────────────────────────────────────────────────
  api.registerTool({
    name: "teaming24_network",
    description:
      "List connected peers on the Teaming24 distributed network, or get local node status. " +
      "Use this before teaming24_delegate to discover available nodes and their capabilities.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["peers", "status"],
          description: "peers = list connected nodes; status = local node info (default: peers)",
        },
      },
    },
    async execute(_id, { action = "peers" }) {
      try {
        const data = (await callApi(
          api,
          `/api/openclaw/network?action=${encodeURIComponent(String(action))}`
        )) as Record<string, unknown>;

        if (action === "peers") {
          const peers = (data.peers as unknown[]) || [];
          if (peers.length === 0)
            return text(
              "No connected peers found.\n\nMake sure LAN discovery is enabled in Teaming24 settings."
            );
          const lines = peers.map((p) => {
            const peer = p as Record<string, unknown>;
            const caps = Array.isArray(peer.capabilities)
              ? peer.capabilities.join(", ")
              : String(peer.capability || "");
            return (
              `**${peer.name || peer.id}** (${peer.host}:${peer.port})\n` +
              `  Capabilities: ${caps || "unknown"}\n` +
              `  Region: ${peer.region || "local"}`
            );
          });
          return text(`**Connected Peers** (${Number(data.count ?? 0)})\n\n${lines.join("\n\n")}`);
        }

        if (action === "status") {
          return text(
            `**Local Node Status**\n` +
              `Status:      ${data.status}\n` +
              `Node ID:     ${data.node_id}\n` +
              `Node Name:   ${data.node_name}\n` +
              `Peers:       ${data.peer_count}\n` +
              `Discovering: ${data.is_discovering ? "✓" : "✗"}`
          );
        }

        return text(JSON.stringify(data, null, 2));
      } catch (err) {
        return text(`Network query failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
  });
}
