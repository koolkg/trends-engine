import { useCallback, useEffect, useState } from "react";
import { fetchTrends } from "./api";
import type { Category, TrendListItem } from "./types";
import { timeAgo, versionDate } from "./format";
import { CategoryChips } from "./components/CategoryChips";
import { TrendDetail } from "./components/TrendDetail";
import { TrendList } from "./components/TrendList";
import { Toast } from "./components/Toast";

interface Props {
  /** How often to re-read the ranking (te-aggregator republishes at least every 5 min). 0 = never. */
  refreshMs?: number;
  pageSize?: number;
}

export function App({ refreshMs = 60_000, pageSize = 20 }: Props) {
  const [category, setCategory] = useState<Category | null>(null);
  const [items, setItems] = useState<TrendListItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [version, setVersion] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  const [, setTick] = useState(0); // re-renders "Updated N min ago"

  // First page: on load, on a category change, on retry, and on every refresh tick.
  useEffect(() => {
    const ctl = new AbortController();
    setLoading(true);
    fetchTrends({ category, limit: pageSize, signal: ctl.signal })
      .then((page) => {
        setItems(page.items);
        setNextCursor(page.nextCursor);
        setVersion(page.version);
        setError(null);
        setSelectedId((cur) => (cur && page.items.some((t) => t.trendId === cur) ? cur : page.items[0]?.trendId ?? null));
      })
      .catch((e) => {
        if ((e as Error).name !== "AbortError") setError((e as Error).message);
      })
      .finally(() => {
        if (!ctl.signal.aborted) setLoading(false);
      });
    return () => ctl.abort();
  }, [category, pageSize, reload]);

  useEffect(() => {
    if (!refreshMs) return;
    const t = setInterval(() => {
      setReload((n) => n + 1);
      setTick((n) => n + 1);
    }, refreshMs);
    return () => clearInterval(t);
  }, [refreshMs]);

  const loadMore = async () => {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const page = await fetchTrends({ category, cursor: nextCursor, limit: pageSize });
      setItems((cur) => [...cur, ...page.items.filter((t) => !cur.some((c) => c.trendId === t.trendId))]);
      setNextCursor(page.nextCursor);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoadingMore(false);
    }
  };

  const changeCategory = (c: Category | null) => {
    if (c === category) return;
    setItems([]);
    setSelectedId(null);
    setCategory(c);
  };

  const clearToast = useCallback(() => setToast(null), []);
  const updated = versionDate(version);

  return (
    <div className="page">
      <div className="dash">
        <header className="dash-top">
          <h1 className="brand">
            Trends <span>Engine</span>
          </h1>
          <span className="updated">{updated ? `Updated ${timeAgo(updated)}` : loading ? "Loading…" : "Not updated yet"}</span>
        </header>
        <CategoryChips active={category} onChange={changeCategory} />
        <main className="dash-body">
          <TrendList
            items={items}
            selectedId={selectedId}
            onSelect={setSelectedId}
            loading={loading}
            error={error}
            onRetry={() => setReload((n) => n + 1)}
            hasMore={nextCursor !== null}
            loadingMore={loadingMore}
            onLoadMore={loadMore}
            updated={updated}
            category={category}
          />
          <TrendDetail trendId={selectedId} version={version} onNotify={setToast} />
        </main>
      </div>
      <Toast message={toast} onDone={clearToast} />
    </div>
  );
}
