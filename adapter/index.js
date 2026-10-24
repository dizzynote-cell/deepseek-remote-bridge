// SPDX-License-Identifier: MIT
// Copyright (c) 2026 xiyannan — derived from Codex Remote Bridge design notes.
// Project: DeepSeek Remote Bridge
//
// Host adapter plugin: mounted into a DeepSeek Harness runtime through a
// generated profile patch. It serves a small loopback JSON API for the parts of
// the bridge that the official SDK stdio wire does not cover:
//   * native account balance          (ctx.deepseekAccount)
//   * session listing and history     (ctx.sessionQuery)
//   * human approvals and questions   (approval/request, user-questions/request)
//
// The plugin only uses documented Cordis seams plus node: builtins, so it does
// not pin any @deepseek-ai package version. It never writes to stdout because
// stdout carries the SDK JSON-RPC frames.

import http from "node:http";

export const name = "dsh-remote-bridge-adapter";

const VERSION = "1.0.0";

function log(message) {
  // stderr only: stdout is the SDK protocol channel.
  try {
    process.stderr.write(`[remote-bridge-adapter] ${message}\n`);
  } catch {}
}

function json(res, status, body) {
  const payload = Buffer.from(JSON.stringify(body), "utf8");
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": payload.length,
    "cache-control": "no-store",
  });
  res.end(payload);
}

function readBody(req, limit = 8 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(new Error("body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      const text = Buffer.concat(chunks).toString("utf8");
      if (!text) return resolve({});
      try {
        resolve(JSON.parse(text));
      } catch (error) {
        reject(new Error("invalid JSON body"));
      }
    });
    req.on("error", reject);
  });
}

function clampInt(value, fallback, min, max) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

/** Pending human interactions keyed by bridge-visible id. */
class InteractionQueue {
  constructor() {
    this.items = new Map();
    this.counter = 0;
  }

  add(record) {
    this.counter += 1;
    const id = `${record.kind}-${record.openedAt.toString(36)}-${this.counter}`;
    const entry = { id, ...record };
    this.items.set(id, entry);
    return entry;
  }

  list(sessionId) {
    const now = Date.now();
    return [...this.items.values()]
      .filter((entry) => !entry.settled && (sessionId === undefined || entry.sessionId === sessionId))
      .map((entry) => ({
        id: entry.id,
        kind: entry.kind,
        sessionId: entry.sessionId,
        toolName: entry.toolName ?? null,
        callId: entry.callId ?? null,
        reason: entry.reason ?? null,
        questions: entry.questions ?? null,
        actions: entry.kind === "approval" ? ["accept", "decline"] : ["answer"],
        openedAt: entry.openedAt,
        deadline: entry.deadline,
        ageMs: now - entry.openedAt,
      }));
  }

  settle(id, payload) {
    const entry = this.items.get(id);
    if (entry === undefined) return false;
    if (entry.settled) return false;
    entry.settled = true;
    this.items.delete(id);
    entry.resolve(payload);
    return true;
  }
}

function normalizeQuestion(request) {
  return (request.questions ?? []).map((question) => ({
    id: String(question.id ?? ""),
    question: String(question.question ?? ""),
    header: question.header === undefined ? null : String(question.header),
    multiple: question.multiple === true || question.multiSelect === true,
    detail: question.detail === undefined ? null : String(question.detail),
    options: (question.options ?? []).map((option) => ({
      label: String(option.label ?? ""),
      description: option.description === undefined ? null : String(option.description),
    })),
  }));
}

function normalizeAnswers(raw, questions) {
  const byId = new Map(questions.map((question) => [question.id, question]));
  const submitted = Array.isArray(raw) ? raw : [];
  const answers = [];
  for (const item of submitted) {
    const id = String(item?.id ?? "");
    if (!byId.has(id)) continue;
    const selected = Array.isArray(item.selected) ? item.selected.map(String) : [];
    const custom = typeof item.custom === "string" && item.custom.trim() !== "" ? item.custom : undefined;
    answers.push({ id, selected, ...(custom === undefined ? {} : { custom }) });
  }
  return answers;
}

export function apply(ctx, config) {
  const port = clampInt(config?.port, 0, 0, 65535);
  const token = typeof config?.token === "string" ? config.token : "";
  const approvalTimeout = clampInt(config?.approvalTimeoutMs, 900000, 1000, 24 * 3600 * 1000);
  const questionTimeout = clampInt(config?.questionTimeoutMs, 900000, 1000, 24 * 3600 * 1000);
  const clientVersion = typeof config?.clientVersion === "string" ? config.clientVersion : VERSION;
  const queue = new InteractionQueue();
  const startedAt = Date.now();

  if (port === 0) {
    log("adapter disabled: no port configured");
    return;
  }
  if (token === "") {
    log("adapter disabled: no token configured");
    return;
  }

  /** Resolve the exact live root agent's session id for an interaction. */
  const sessionIdOf = (agent) => {
    const id = agent?.session?.id;
    return id === undefined ? null : String(id);
  };

  async function decideApproval(request) {
    const sessionId = sessionIdOf(request.agent);
    const deadline = Date.now() + approvalTimeout;
    const entry = queue.add({
      kind: "approval",
      sessionId,
      toolName: request.toolName === undefined ? null : String(request.toolName),
      callId: request.callId === undefined ? null : String(request.callId),
      reason: request.reason === undefined ? null : String(request.reason),
      openedAt: Date.now(),
      deadline,
      settled: false,
      resolve: null,
    });
    return await new Promise((resolve) => {
      let finished = false;
      const finish = (outcome) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        request.signal?.removeEventListener?.("abort", onAbort);
        queue.items.delete(entry.id);
        resolve(outcome);
      };
      entry.resolve = (payload) => {
        const outcome = typeof payload?.outcome === "string" ? payload.outcome : "rejected";
        finish(["allowed-once", "rejected", "cancelled", "unavailable"].includes(outcome) ? outcome : "rejected");
      };
      const timer = setTimeout(() => {
        log(`approval ${entry.id} timed out without a human decision`);
        finish("unavailable");
      }, approvalTimeout);
      timer.unref?.();
      const onAbort = () => finish("cancelled");
      if (request.signal?.aborted) return finish("cancelled");
      request.signal?.addEventListener?.("abort", onAbort, { once: true });
    });
  }

  async function askHuman(request) {
    const sessionId = sessionIdOf(request.agent);
    const questions = normalizeQuestion(request);
    if (questions.length === 0) throw new Error("no questions to answer");
    const deadline = Date.now() + questionTimeout;
    const entry = queue.add({
      kind: "questions",
      sessionId,
      toolName: "ask_user_question",
      callId: null,
      reason: null,
      questions,
      openedAt: Date.now(),
      deadline,
      settled: false,
      resolve: null,
    });
    return await new Promise((resolve, reject) => {
      let finished = false;
      const finish = (fn) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        request.signal?.removeEventListener?.("abort", onAbort);
        queue.items.delete(entry.id);
        fn();
      };
      entry.resolve = (payload) => {
        const answers = normalizeAnswers(payload?.answers, questions);
        if (answers.length === 0) {
          finish(() => reject(new Error("the submitted answer batch named no open question")));
          return;
        }
        finish(() => resolve({ answers }));
      };
      const timer = setTimeout(() => {
        log(`question ${entry.id} timed out without a human answer`);
        finish(() => reject(new Error("the bridge user did not answer before the deadline")));
      }, questionTimeout);
      timer.unref?.();
      const onAbort = () => finish(() => reject(new Error("the question was withdrawn")));
      if (request.signal?.aborted) return onAbort();
      request.signal?.addEventListener?.("abort", onAbort, { once: true });
    });
  }

  ctx.on("approval/request", (request, next) => {
    if (sessionIdOf(request.agent) === null) return next();
    return decideApproval(request);
  });

  ctx.on("user-questions/request", (request, next) => {
    if (sessionIdOf(request.agent) === null) return next();
    return askHuman(request);
  });

  async function readBalance(query) {
    const account = ctx.get("deepseekAccount");
    if (account === undefined || account === null) {
      return {
        available: false,
        status: "unsupported",
        source: "harness-account",
        updatedAt: Math.floor(Date.now() / 1000),
        stale: false,
        entries: [],
        detail: { reason: "the composed runtime has no DeepSeek account provider" },
      };
    }
    const client = {
      version: clientVersion,
      locale: typeof query.get("locale") === "string" && query.get("locale") !== "" ? query.get("locale") : "zh-CN",
      timezoneOffsetSeconds: clampInt(query.get("tzOffset"), 0, -86400, 86400),
    };
    let result = null;
    let failure = null;
    try {
      result = await account.getBalance(client);
    } catch (error) {
      failure = error;
    }
    const updatedAt = Math.floor(Date.now() / 1000);
    if (result === null || result === undefined) {
      return {
        available: false,
        // A null result is the provider's signed-out or rejected-credential
        // answer; a thrown failure keeps its own explicit status.
        status: failure === null ? "auth_required" : "unavailable",
        source: "harness-account",
        updatedAt,
        stale: false,
        entries: [],
        detail: {
          reason: failure === null ? "the runtime account provider returned no balance for the stored credential" : "the account balance request failed",
        },
      };
    }
    if (result.status !== "ready") {
      return {
        available: false,
        status: typeof result.status === "string" ? result.status : "unavailable",
        source: "harness-account",
        updatedAt,
        stale: false,
        entries: [],
        detail: {},
      };
    }
    const entries = [];
    for (const wallet of Array.isArray(result.value) ? result.value : []) {
      entries.push({ currency: String(wallet.currency), wallet: "normal", amount: String(wallet.balance) });
    }
    for (const wallet of Array.isArray(result.bonusWallets) ? result.bonusWallets : []) {
      entries.push({ currency: String(wallet.currency), wallet: "bonus", amount: String(wallet.balance) });
    }
    return {
      available: true,
      status: "ok",
      source: "harness-account",
      updatedAt,
      stale: false,
      entries,
      detail: {
        walletKinds: ["normal", "bonus"],
        note: "Wallet amounts are Platform decimal strings; wallets of different kinds or currencies must not be summed.",
      },
    };
  }

  async function readSessions(query) {
    const query_service = ctx.get("sessionQuery");
    if (query_service === undefined) throw new Error("the composed runtime has no session query service");
    const limit = clampInt(query.get("limit"), 200, 1, 1000);
    const includeChildren = query.get("children") === "1";
    const records = await query_service.listSessions();
    const filtered = records.filter((record) => includeChildren || record.header?.parentSession === undefined);
    const trimmed = filtered.slice(0, limit);
    const ids = trimmed.map((record) => String(record.header.id));
    let titles = new Map();
    if (ids.length > 0) {
      try {
        const snapshots = await query_service.readTitleSnapshots(ids);
        titles = new Map(
          snapshots.map((result, index) => {
            if (result.status !== "fulfilled") return [ids[index], null];
            const snapshot = result.value?.title;
            const text = typeof snapshot === "string" ? snapshot : snapshot?.title;
            return [ids[index], typeof text === "string" && text !== "" ? text : null];
          }),
        );
      } catch (error) {
        log(`title snapshot read failed: ${error?.message ?? error}`);
      }
    }
    return {
      sessions: trimmed.map((record) => {
        const header = record.header ?? {};
        const id = String(header.id);
        return {
          id,
          createdAt: Number(header.createdAt ?? 0),
          cwd: header.cwd === undefined ? null : String(header.cwd),
          parentSessionId: header.parentSession === undefined ? null : String(header.parentSession),
          live: record.live === true,
          persisted: record.persisted === true,
          title: titles.get(id) ?? null,
          agentPreset: header.agentPreset === undefined ? null : String(header.agentPreset),
        };
      }),
      total: filtered.length,
    };
  }

  async function readSession(sessionId) {
    const query_service = ctx.get("sessionQuery");
    if (query_service === undefined) throw new Error("the composed runtime has no session query service");
    const result = await query_service.readSession(sessionId);
    return {
      header: result.session,
      inheritedEventCount: result.inheritedEventCount ?? 0,
      events: result.events,
    };
  }

  function permissionService() {
    const service = ctx.get("permissionPresets");
    if (service === undefined) throw new Error("the composed runtime has no permission preset service");
    return service;
  }

  function liveSession(sessionId) {
    const sessions = ctx.get("sessions");
    const session = sessions?.get(sessionId);
    if (session === undefined || session === null) {
      const error = new Error("session is not live in this runtime; send a prompt first");
      error.status = 409;
      error.code = "session_not_live";
      throw error;
    }
    return session;
  }

  function readPermissions(query) {
    const service = permissionService();
    const catalog = service.catalog();
    const sessionId = query.get("sessionId");
    let current = null;
    if (sessionId) {
      try {
        current = service.current(liveSession(sessionId));
      } catch (error) {
        if (error?.code === "session_not_live") current = null;
        else throw error;
      }
    }
    return {
      options: catalog.options ?? [],
      defaultPreset: catalog.defaultPreset ?? null,
      current,
    };
  }

  function applyPermission(body) {
    const service = permissionService();
    const sessionId = typeof body?.sessionId === "string" ? body.sessionId : "";
    const preset = typeof body?.preset === "string" ? body.preset : "";
    if (sessionId === "") throw new Error("permission change requires a sessionId");
    if (preset === "") throw new Error("permission change requires a preset");
    const session = liveSession(sessionId);
    service.set(session, preset);
    return { current: service.current(session), preset };
  }

  const server = http.createServer(async (req, res) => {
    try {
      if (req.headers["x-bridge-token"] !== token) {
        json(res, 401, { error: "unauthorized" });
        return;
      }
      const url = new URL(req.url ?? "/", "http://127.0.0.1");
      const path = url.pathname;
      if (req.method === "GET" && path === "/health") {
        json(res, 200, { ok: true, version: VERSION, pid: process.pid, startedAt, uptimeMs: Date.now() - startedAt });
        return;
      }
      if (req.method === "GET" && path === "/balance") {
        json(res, 200, await readBalance(url.searchParams));
        return;
      }
      if (req.method === "GET" && path === "/sessions") {
        json(res, 200, await readSessions(url.searchParams));
        return;
      }
      if (req.method === "GET" && path.startsWith("/session/")) {
        const sessionId = decodeURIComponent(path.slice("/session/".length));
        if (sessionId === "") throw new Error("missing session id");
        json(res, 200, await readSession(sessionId));
        return;
      }
      if (req.method === "GET" && path === "/permissions") {
        json(res, 200, readPermissions(url.searchParams));
        return;
      }
      if (req.method === "POST" && path === "/permission") {
        const body = await readBody(req);
        json(res, 200, applyPermission(body));
        return;
      }
      if (req.method === "GET" && path === "/interactions") {
        const sessionId = url.searchParams.get("sessionId");
        json(res, 200, { interactions: queue.list(sessionId === null ? undefined : sessionId) });
        return;
      }
      if (req.method === "POST" && path.startsWith("/interaction/")) {
        const id = decodeURIComponent(path.slice("/interaction/".length));
        const body = await readBody(req);
        const settled = queue.settle(id, body);
        if (!settled) {
          json(res, 404, { error: "unknown_or_settled_interaction" });
          return;
        }
        json(res, 200, { ok: true });
        return;
      }
      json(res, 404, { error: "not_found" });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      log(`request failed: ${message}`);
      json(res, Number.isInteger(error?.status) ? error.status : 500, {
        error: "adapter_error",
        code: typeof error?.code === "string" ? error.code : "",
        message,
      });
    }
  });

  server.on("error", (error) => log(`http server error: ${error?.message ?? error}`));

  ctx.effect(
    () => {
      server.listen(port, "127.0.0.1", () => {
        log(`listening on 127.0.0.1:${port} (pid ${process.pid})`);
      });
      return () => {
        for (const entry of [...queue.items.values()]) {
          try {
            entry.settled = true;
            entry.resolve?.({ outcome: "cancelled" });
          } catch {}
        }
        queue.items.clear();
        try {
          server.close();
        } catch {}
      };
    },
    "remote-bridge-adapter.http",
  );
}
