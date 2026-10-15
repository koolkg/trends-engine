import { ApiError, fetchTrend, fetchTrends, insertIntoCampaign, previewCampaignBlock } from "../src/api";
import { installFetch } from "./fakeFetch";
import { LOWERCASE, VERSION, detailOf } from "./fixtures";

test("list: query string, items, cursor and the ranking headers", async () => {
  const { calls } = installFetch({
    "GET /v1/trends": () => ({ body: { items: [LOWERCASE], nextCursor: "eyJvZmZzZXQiOjIwfQ" }, headers: { "X-Trends-Version": VERSION, "X-Trends-Source": "cache" } }),
  });
  const page = await fetchTrends({ category: "social", cursor: "abc", limit: 5 });
  expect(page).toEqual({ items: [LOWERCASE], nextCursor: "eyJvZmZzZXQiOjIwfQ", version: VERSION, source: "cache" });
  expect(Object.fromEntries(calls[0].url.searchParams)).toEqual({ limit: "5", category: "social", cursor: "abc" });
});

test("list: no category or cursor params when not given", async () => {
  const { calls } = installFetch({ "GET /v1/trends": () => ({ body: { items: [], nextCursor: null } }) });
  const page = await fetchTrends();
  expect(calls[0].url.search).toBe("?limit=20");
  expect(page.version).toBeNull();
});

test("errors keep te-api's code and message", async () => {
  installFetch({ "GET /v1/trends": () => ({ status: 400, body: { error: "bad_category", message: "category must be one of …" } }) });
  await expect(fetchTrends()).rejects.toMatchObject({ status: 400, code: "bad_category", message: "category must be one of …" });
});

test("a non-JSON error body still becomes an ApiError", async () => {
  installFetch({ "GET /v1/trends": () => ({ status: 502 }) });
  const err = await fetchTrends().catch((e) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect(err.status).toBe(502);
});

test("network failure is reported as such", async () => {
  installFetch({ "GET /v1/trends": () => ({ networkError: true }) });
  await expect(fetchTrends()).rejects.toMatchObject({ status: 0, code: "network" });
});

test("detail: id is URL-encoded; 404 surfaces as status 404", async () => {
  const { calls } = installFetch({
    [`GET /v1/trends/${LOWERCASE.trendId}`]: () => ({ body: detailOf(LOWERCASE) }),
    "GET /v1/trends/.+": () => ({ status: 404, body: { error: "not_found", message: "trend not found or aged out" } }),
  });
  expect((await fetchTrend(LOWERCASE.trendId)).relatedTerms).toHaveLength(3);
  await expect(fetchTrend("a/b")).rejects.toMatchObject({ status: 404 });
  expect(calls[1].url.pathname).toBe("/v1/trends/a%2Fb");
});

test("insert: posts the design doc's request body", async () => {
  const { calls } = installFetch({
    "POST /v1/campaigns/draft-block": () => ({ status: 201, body: { blockId: "blk_1", suggestedCopy: "hi", status: "draft" } }),
  });
  expect(await insertIntoCampaign(LOWERCASE.trendId)).toEqual({ blockId: "blk_1", suggestedCopy: "hi", status: "draft" });
  expect(calls[0].body).toEqual({ trendId: LOWERCASE.trendId, campaignId: null, tone: "casual" });
});

test("insert: te-api's errors pass through with their code", async () => {
  installFetch({
    "POST /v1/campaigns/draft-block": () => ({ status: 502, body: { error: "mailchimp_unavailable", message: "Mailchimp can't be reached right now." } }),
  });
  await expect(insertIntoCampaign("x")).rejects.toMatchObject({ status: 502, code: "mailchimp_unavailable", message: "Mailchimp can't be reached right now." });
  installFetch({ "POST /v1/campaigns/draft-block": () => ({ status: 404, body: { error: "trend_not_found", message: "trend not found" } }) });
  await expect(insertIntoCampaign("x")).rejects.toMatchObject({ status: 404, code: "trend_not_found" });
});

test.each([502, 503, 504])("insert: a bare %i from the proxy means te-api itself is down", async (status) => {
  installFetch({ "POST /v1/campaigns/draft-block": () => ({ status }) });
  await expect(insertIntoCampaign("x")).rejects.toMatchObject({ code: "unavailable" });
});

test("insert: tone is sent through", async () => {
  const { calls } = installFetch({ "POST /v1/campaigns/draft-block": () => ({ status: 201, body: { blockId: "b", suggestedCopy: "c", status: "draft" } }) });
  await insertIntoCampaign("x", "expert");
  expect(calls[0].body).toEqual({ trendId: "x", campaignId: null, tone: "expert" });
});

test("preview: posts the same body as insert, to the preview route", async () => {
  const copy = { trendId: "x", tone: "professional", subjectLine: "s", previewText: "p", headline: "h", suggestedCopy: "b" };
  const { calls } = installFetch({ "POST /v1/campaigns/draft-block/preview": () => ({ body: copy }) });
  expect(await previewCampaignBlock("x", "professional")).toEqual(copy);
  expect(calls[0].body).toEqual({ trendId: "x", campaignId: null, tone: "professional" });
});
