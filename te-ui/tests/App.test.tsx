import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/App";
import type { TrendListItem } from "../src/types";
import { installFetch, type Reply } from "./fakeFetch";
import { DUPES, LOWERCASE, QUIET_LUXURY, VERSION, detailOf } from "./fixtures";

const ALL = [LOWERCASE, DUPES, QUIET_LUXURY];

// te-api's campaign copy, per tone, as POST /v1/campaigns/draft-block/preview returns it.
function copyFor(trendId: string, tone: string) {
  return {
    trendId, tone, subjectLine: `${tone} subject`, previewText: `${tone} preview`, headline: `${tone} headline`,
    suggestedCopy: `${tone} block copy`,
  };
}

const bodyOf = (init?: RequestInit) => JSON.parse(String(init?.body ?? "{}"));

type Route = (url: URL, init?: RequestInit) => Reply;

function feed(items: TrendListItem[], extra: Record<string, Route> = {}) {
  const defaults: Record<string, Route> = {
    "POST /v1/campaigns/draft-block/preview": (_url, init) => {
      const b = bodyOf(init);
      return { body: copyFor(b.trendId, b.tone) };
    },
    "GET /v1/trends": (url) => {
      const cat = url.searchParams.get("category");
      return { body: { items: items.filter((t) => !cat || t.category === cat), nextCursor: null }, headers: { "X-Trends-Version": VERSION, "X-Trends-Source": "cache" } };
    },
    "GET /v1/trends/.+": (url) => {
      const t = items.find((i) => url.pathname.endsWith(i.trendId));
      return t ? { body: detailOf(t) } : { status: 404, body: { error: "not_found", message: "trend not found or aged out of the ranking window" } };
    },
  };
  // A test's own routes come first (they're matched in order) and replace a default with the same key.
  return installFetch({ ...extra, ...Object.fromEntries(Object.entries(defaults).filter(([k]) => !(k in extra))) });
}

const rows = () => within(screen.getByRole("list", { name: "Ranked trends" })).getAllByRole("button");

/** The insert button, once the preview has loaded and it's enabled. */
async function insertButton() {
  const b = await screen.findByRole("button", { name: "Insert into campaign" });
  await waitFor(() => expect(b).toBeEnabled());
  return b;
}

test("lists the ranking in order and opens the top trend", async () => {
  feed(ALL);
  render(<App refreshMs={0} />);
  await waitFor(() => expect(rows()).toHaveLength(3));
  expect(rows().map((r) => r.textContent)).toEqual([
    expect.stringContaining("#1#lowercase / #brandvoiceSocialSpiking"),
    expect.stringContaining("#2#dupe / #dupealertEcommerce"),
    expect.stringContaining("#3#quietluxuryContent"),
  ]);
  expect(rows()[0]).toHaveAttribute("aria-current", "true");
  expect(within(rows()[2]).getByText("-38%")).toHaveClass("delta down");

  expect(await screen.findByRole("heading", { level: 2, name: "#lowercase / #brandvoice" })).toBeInTheDocument();
  expect(screen.getByText("Sources: X, TikTok, Instagram, Reddit")).toBeInTheDocument();
  expect(screen.getByText(/everything now, even the receipts/)).toBeInTheDocument();
  expect(screen.getByText(/unknown on TikTok/)).toBeInTheDocument(); // sample post without an author
  expect(screen.getByText("#copywriting")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Why it's trending" })).toBeInTheDocument();
  expect(screen.getByText("#lowercase jumped to 31 posts in the 9 PM UTC hour (usually about 3 an hour).")).toBeInTheDocument();
  expect(screen.queryByText(/z-score/)).toBeNull(); // the raw scores stay off the dashboard
});

test("rankings without highlights just skip the section", async () => {
  feed(ALL, { "GET /v1/trends/.+": () => ({ body: { ...detailOf(LOWERCASE), highlights: undefined } }) });
  render(<App refreshMs={0} />);
  await screen.findByRole("heading", { level: 2, name: "#lowercase / #brandvoice" });
  expect(screen.queryByRole("heading", { name: "Why it's trending" })).toBeNull();
});

test("selecting a row shows that trend", async () => {
  feed(ALL);
  render(<App refreshMs={0} />);
  await screen.findByRole("heading", { level: 2, name: "#lowercase / #brandvoice" });
  await userEvent.click(rows()[1]);
  expect(await screen.findByRole("heading", { level: 2, name: "#dupe / #dupealert" })).toBeInTheDocument();
  expect(rows()[1]).toHaveAttribute("aria-current", "true");
});

test("a category chip asks te-api for that category", async () => {
  const { calls } = feed(ALL);
  render(<App refreshMs={0} />);
  await waitFor(() => expect(rows()).toHaveLength(3));
  await userEvent.click(screen.getByRole("button", { name: "Ecommerce" }));
  await waitFor(() => expect(rows()).toHaveLength(1));
  expect(screen.getByRole("button", { name: "Ecommerce" })).toHaveAttribute("aria-pressed", "true");
  expect(calls.filter((c) => c.url.pathname === "/v1/trends").at(-1)!.url.searchParams.get("category")).toBe("ecommerce");
  expect(await screen.findByRole("heading", { level: 2, name: "#dupe / #dupealert" })).toBeInTheDocument();
});

test("an empty ranking says nothing is trending, not that nothing was ranked", async () => {
  feed([]);
  render(<App refreshMs={0} />);
  expect(await screen.findByText(/Nothing trending right now\./)).toBeInTheDocument();
  expect(screen.getByText(/Checked .*spike above their usual pace/)).toBeInTheDocument();
});

test("an empty category says which category is quiet", async () => {
  feed(ALL);   // no email-marketing trends in it
  render(<App refreshMs={0} />);
  await waitFor(() => expect(rows()).toHaveLength(3));
  await userEvent.click(screen.getByRole("button", { name: "Email Marketing" }));
  expect(await screen.findByText(/Nothing trending in Email Marketing right now\./)).toBeInTheDocument();
});

test("before the first ranking it says so", async () => {
  installFetch({ "GET /v1/trends": () => ({ body: { items: [], nextCursor: null } }) });   // no X-Trends-Version
  render(<App refreshMs={0} />);
  expect(await screen.findByText(/No ranking yet\./)).toBeInTheDocument();
});

test("list errors offer a retry", async () => {
  let fail = true;
  installFetch({
    "GET /v1/trends": () => (fail ? { status: 500, body: { error: "internal", message: "internal error" } } : { body: { items: [DUPES], nextCursor: null } }),
    "GET /v1/trends/.+": () => ({ body: detailOf(DUPES) }),
  });
  render(<App refreshMs={0} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load trends: internal error");
  fail = false;
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(rows()).toHaveLength(1));
});

test("a trend that aged out of the ranking says so", async () => {
  feed(ALL, { [`GET /v1/trends/${DUPES.trendId}`]: () => ({ status: 404, body: { error: "not_found", message: "gone" } }) });
  render(<App refreshMs={0} />);
  await waitFor(() => expect(rows()).toHaveLength(3));
  await userEvent.click(rows()[1]);
  expect(await screen.findByText(/aged out of the ranking/)).toBeInTheDocument();
});

test("detail errors other than 404 offer a retry", async () => {
  let fail = true;
  feed(ALL, { [`GET /v1/trends/${LOWERCASE.trendId}`]: () => (fail ? { networkError: true } : { body: detailOf(LOWERCASE) }) });
  render(<App refreshMs={0} />);
  expect(await screen.findByText(/Couldn't load this trend: Can't reach the server/)).toBeInTheDocument();
  fail = false;
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("heading", { level: 2, name: "#lowercase / #brandvoice" })).toBeInTheDocument();
});

test("the campaign block shows exactly what will be inserted, before inserting", async () => {
  const { calls } = feed(ALL);
  render(<App refreshMs={0} />);
  const email = await screen.findByRole("article", { name: "Email preview" });
  expect(within(email).getByText("casual subject")).toBeInTheDocument();
  expect(within(email).getByText("casual preview")).toBeInTheDocument();
  expect(within(email).getByRole("heading", { name: "casual headline" })).toBeInTheDocument();
  expect(within(email).getByText("casual block copy")).toBeInTheDocument();
  expect(within(email).queryByRole("link")).toBeNull(); // no button: the marketer adds their own in Mailchimp
  expect(calls.some((c) => c.url.pathname === "/v1/campaigns/draft-block")).toBe(false); // nothing inserted yet
});

test("picking a tone rewrites the preview", async () => {
  const { calls } = feed(ALL);
  render(<App refreshMs={0} />);
  await screen.findByText("casual subject");
  await userEvent.click(screen.getByRole("button", { name: "Professional" }));
  expect(await screen.findByText("professional subject")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Professional" })).toHaveAttribute("aria-pressed", "true");
  expect(calls.filter((c) => c.url.pathname.endsWith("/preview")).at(-1)!.body).toMatchObject({ tone: "professional" });
});

test("insert into campaign: shows what was inserted, the campaign, and a link to the draft", async () => {
  const { calls } = feed(ALL, {
    "POST /v1/campaigns/draft-block": (_url, init) => {
      const b = bodyOf(init);
      return { status: 201, body: { ...copyFor(b.trendId, b.tone), blockId: "9b1e", status: "draft", campaignId: "a1b2c3d4e5",
                                    previewUrl: "http://localhost:8090/archive/a1b2c3d4e5" } };
    },
  });
  render(<App refreshMs={0} />);
  await screen.findByText("casual subject");
  await userEvent.click(screen.getByRole("button", { name: "Expert" }));
  await screen.findByText("expert subject");
  await userEvent.click(await insertButton());
  expect(await screen.findByRole("button", { name: "Inserted" })).toBeDisabled();
  expect(screen.getByText(/Inserted into draft campaign/)).toHaveTextContent("Inserted into draft campaign a1b2c3d4e5 in Mailchimp.");
  expect(screen.getByRole("link", { name: "Open the draft" })).toHaveAttribute("href", "http://localhost:8090/archive/a1b2c3d4e5");
  expect(screen.getByText("expert block copy")).toBeInTheDocument();
  expect(screen.getByText('Added "#lowercase / #brandvoice" to a draft Mailchimp campaign')).toHaveClass("toast show");
  expect(calls.find((c) => c.url.pathname === "/v1/campaigns/draft-block")!.body).toEqual({ trendId: LOWERCASE.trendId, campaignId: null, tone: "expert" });
});

test("insert into campaign: Mailchimp unreachable", async () => {
  feed(ALL, {
    "POST /v1/campaigns/draft-block": () => ({ status: 502, body: { error: "mailchimp_unavailable", message: "Mailchimp can't be reached right now. Try again shortly." } }),
  });
  render(<App refreshMs={0} />);
  await userEvent.click(await insertButton());
  expect(await screen.findByRole("alert")).toHaveTextContent("Mailchimp can't be reached right now. Try again shortly.");
  expect(screen.getByRole("button", { name: "Insert into campaign" })).toBeEnabled();
});

test("a failed preview offers a retry, and insert waits for it", async () => {
  let fail = true;
  feed(ALL, {
    "POST /v1/campaigns/draft-block/preview": (_url, init) =>
      fail ? { status: 500, body: { error: "internal", message: "internal error" } } : { body: copyFor(bodyOf(init).trendId, "casual") },
  });
  render(<App refreshMs={0} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't prepare the campaign block: internal error");
  expect(screen.getByRole("button", { name: "Insert into campaign" })).toBeDisabled();
  fail = false;
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("casual subject")).toBeInTheDocument();
  await insertButton();
});

test("insert state resets when another trend is opened", async () => {
  feed(ALL, { "POST /v1/campaigns/draft-block": () => ({ status: 502, body: { error: "mailchimp_unavailable", message: "down" } }) });
  render(<App refreshMs={0} />);
  await userEvent.click(await insertButton());
  await screen.findByRole("alert");
  await userEvent.click(rows()[1]);
  await screen.findByRole("heading", { level: 2, name: "#dupe / #dupealert" });
  expect(screen.queryByRole("alert")).toBeNull();
});

test("show more appends the next page", async () => {
  const { calls } = installFetch({
    "GET /v1/trends": (url) =>
      url.searchParams.get("cursor")
        ? { body: { items: [QUIET_LUXURY], nextCursor: null } }
        : { body: { items: [LOWERCASE, DUPES], nextCursor: "eyJvZmZzZXQiOjJ9" } },
    "GET /v1/trends/.+": () => ({ body: detailOf(LOWERCASE) }),
  });
  render(<App refreshMs={0} pageSize={2} />);
  await waitFor(() => expect(rows()).toHaveLength(2));
  await userEvent.click(screen.getByRole("button", { name: "Show more" }));
  await waitFor(() => expect(rows()).toHaveLength(3));
  expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  expect(calls.find((c) => c.url.searchParams.get("cursor"))!.url.searchParams.get("limit")).toBe("2");
});

test("shows when the ranking was computed, and refreshes on a timer", async () => {
  jest.useFakeTimers({ now: new Date("2026-10-06T04:39:50Z") });
  try {
    const { calls } = feed(ALL);
    render(<App refreshMs={60_000} />);
    expect(await screen.findByText("Updated 4 min ago")).toBeInTheDocument();
    const before = calls.filter((c) => c.url.pathname === "/v1/trends").length;
    await act(async () => {
      jest.advanceTimersByTime(60_000);
    });
    await waitFor(() => expect(calls.filter((c) => c.url.pathname === "/v1/trends").length).toBe(before + 1));
    expect(rows()[0]).toHaveAttribute("aria-current", "true"); // selection kept across refreshes
  } finally {
    jest.useRealTimers();
  }
});
