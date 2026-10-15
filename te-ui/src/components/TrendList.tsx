import type { Category, TrendListItem } from "../types";
import { categoryLabel, formatDelta, timeAgo } from "../format";
import { Sparkline } from "./Sparkline";

interface Props {
  items: TrendListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  /** When te-aggregator published the ranking shown (null: none published yet). */
  updated?: Date | null;
  category?: Category | null;
}

export function TrendList({ items, selectedId, onSelect, loading, error, onRetry, hasMore, loadingMore, onLoadMore,
                           updated = null, category = null }: Props) {
  if (loading && items.length === 0) {
    return (
      <div className="trend-list" aria-busy="true">
        <p className="state">Loading trends…</p>
      </div>
    );
  }
  if (error && items.length === 0) {
    return (
      <div className="trend-list">
        <div className="state error" role="alert">
          <p>Couldn't load trends: {error}</p>
          <button type="button" className="link-btn" onClick={onRetry}>Try again</button>
        </div>
      </div>
    );
  }
  if (items.length === 0) {
    // Two different situations: te-aggregator hasn't published a ranking yet, or it
    // has (it runs every few minutes) and nothing is above its usual pace right now.
    return (
      <div className="trend-list">
        {updated ? (
          <p className="state">
            Nothing trending{category ? ` in ${categoryLabel(category)}` : ""} right now.
            <br />
            Checked {timeAgo(updated)}. Trends show up here when posts spike above their usual pace.
          </p>
        ) : (
          <p className="state">
            No ranking yet.
            <br />
            te-aggregator publishes the first one a few minutes after posts start coming in.
          </p>
        )}
      </div>
    );
  }
  return (
    <div className="trend-list">
      <ol className="rows" aria-label="Ranked trends">
        {items.map((t) => {
          const positive = t.deltaPct >= 0;
          return (
            <li key={t.trendId}>
              <button
                type="button"
                className={`trend-row${t.trendId === selectedId ? " selected" : ""}`}
                aria-current={t.trendId === selectedId ? "true" : undefined}
                onClick={() => onSelect(t.trendId)}
              >
                <span className="rank">#{t.rank}</span>
                <span className="title">
                  <span className="name">{t.name}</span>
                  <span className="cat">
                    {categoryLabel(t.category)}
                    {t.velocityLabel === "spike" && <span className="badge spike">Spiking</span>}
                  </span>
                </span>
                <Sparkline points={t.sparkline} positive={positive} />
                <span className={`delta${positive ? "" : " down"}`}>{formatDelta(t.deltaPct)}</span>
              </button>
            </li>
          );
        })}
      </ol>
      {error && <p className="state error" role="alert">Refresh failed: {error}</p>}
      {hasMore && (
        <button type="button" className="link-btn more" onClick={onLoadMore} disabled={loadingMore}>
          {loadingMore ? "Loading…" : "Show more"}
        </button>
      )}
    </div>
  );
}
