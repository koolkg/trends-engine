// A tiny fetch stand-in: route by method + path, record every call.
export interface Reply {
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;
  networkError?: boolean;
}
type Handler = (url: URL, init?: RequestInit) => Reply | Promise<Reply>;

export function installFetch(routes: Record<string, Handler>) {
  const calls: { method: string; url: URL; body?: unknown }[] = [];
  const fn = jest.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, "http://te-ui.test");
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (init?.signal?.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
    const key = Object.keys(routes).find((k) => {
      const [m, pattern] = k.split(" ");
      return m === method && new RegExp(`^${pattern}$`).test(url.pathname);
    });
    const reply: Reply = key ? await routes[key](url, init) : { status: 404, body: { error: "not_found", message: "no route" } };
    if (reply.networkError) throw new TypeError("Failed to fetch");
    const status = reply.status ?? 200;
    const headers = Object.fromEntries(Object.entries(reply.headers ?? {}).map(([k, v]) => [k.toLowerCase(), v]));
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: String(status),
      headers: { get: (name: string) => headers[name.toLowerCase()] ?? null },
      json: async () => {
        if (reply.body === undefined) throw new SyntaxError("Unexpected end of JSON input");
        return reply.body;
      },
    } as unknown as Response;
  });
  (globalThis as { fetch: unknown }).fetch = fn;
  return { fn, calls };
}
