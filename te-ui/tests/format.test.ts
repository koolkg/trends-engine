import { categoryLabel, formatDelta, formatWhen, timeAgo, versionDate } from "../src/format";

test("deltas carry a sign", () => {
  expect(formatDelta(984)).toBe("+984%");
  expect(formatDelta(-38)).toBe("-38%");
  expect(formatDelta(0)).toBe("0%");
});

test("ranking version is te-aggregator's computed-at time", () => {
  expect(versionDate("20261006T043546442119Z")?.toISOString()).toBe("2026-10-06T04:35:46.000Z");
  expect(versionDate(null)).toBeNull();
  expect(versionDate("garbage")).toBeNull();
});

test("time ago", () => {
  const now = new Date("2026-10-06T05:00:00Z");
  expect(timeAgo(new Date("2026-10-06T04:59:30Z"), now)).toBe("just now");
  expect(timeAgo(new Date("2026-10-06T04:56:00Z"), now)).toBe("4 min ago");
  expect(timeAgo(new Date("2026-10-06T02:00:00Z"), now)).toBe("3 h ago");
  expect(timeAgo(new Date("2026-10-01T05:00:00Z"), now)).toBe("5 days ago");
});

test("labels", () => {
  expect(categoryLabel("email")).toBe("Email Marketing");
  expect(categoryLabel("other")).toBe("other");
  expect(formatWhen("2026-09-07T17:02:11Z")).toBe("Sep 7, 5:02 PM UTC");
  expect(formatWhen(null)).toBe("unknown");
});
