import { useEffect, useState } from "react";
import { ApiError, fetchTrend } from "../api";
import type { TrendDetail as Detail } from "../types";
import { categoryLabel, formatDelta, formatWhen, platformLabel } from "../format";
import { CampaignBlock } from "./CampaignBlock";
import { Sparkline } from "./Sparkline";

interface Props {
  trendId: string | null;
  /** Bumped when a new ranking lands, so the open detail is re-read too. */
  version: string | null;
  onNotify: (message: string) => void;
}

type Load = { state: "loading" } | { state: "ready"; trend: Detail } | { state: "gone" } | { state: "error"; message: string };

export function TrendDetail({ trendId, version, onNotify }: Props) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!trendId) return;
    const ctl = new AbortController();
    setLoad((prev) => (prev.state === "ready" && prev.trend.trendId === trendId ? prev : { state: "loading" }));
    fetchTrend(trendId, ctl.signal)
      .then((trend) => setLoad({ state: "ready", trend }))
      .catch((e) => {
        if ((e as Error).name === "AbortError") return;
        const err = e as ApiError;
        setLoad(err.status === 404 ? { state: "gone" } : { state: "error", message: err.message });
      });
    return () => ctl.abort();
  }, [trendId, version, attempt]);

  if (!trendId) return <div className="trend-detail" />;
  if (load.state === "loading") {
    return (
      <div className="trend-detail" aria-busy="true">
        <p className="state">Loading trend…</p>
      </div>
    );
  }
  if (load.state === "gone") {
    return (
      <div className="trend-detail">
        <p className="state">This trend has aged out of the ranking. Pick another one from the list.</p>
      </div>
    );
  }
  if (load.state === "error") {
    return (
      <div className="trend-detail">
        <div className="state error" role="alert">
          <p>Couldn't load this trend: {load.message}</p>
          <button type="button" className="link-btn" onClick={() => setAttempt((n) => n + 1)}>Try again</button>
        </div>
      </div>
    );
  }

  const t = load.trend;
  return (
    <div className="trend-detail">
      <span className="cat-tag">{categoryLabel(t.category)}</span>
      {t.velocityLabel === "spike" && <span className="badge spike">Spiking</span>}
      <h2>{t.name}</h2>
      <div className="trend-meta">
        <span>First seen: {formatWhen(t.firstSeenAt)}</span>
        <span>Sources: {t.sources.map(platformLabel).join(", ") || "none"}</span>
        <span>{formatDelta(t.deltaPct)} vs. usual</span>
      </div>
      <p className="summary">{t.summary}</p>
      <div className="growth">
        <Sparkline points={t.growth.map((g) => g.score)} positive={t.deltaPct >= 0} width={240} height={48} />
      </div>
      {t.highlights && t.highlights.length > 0 && (
        <>
          <h3 className="section">Why it's trending</h3>
          <ul className="why">
            {t.highlights.map((h) => (
              <li key={h}>{h}</li>
            ))}
          </ul>
        </>
      )}
      <h3 className="section">Sample posts</h3>
      {t.samplePosts.length === 0 && <p className="state">No sample posts.</p>}
      {t.samplePosts.map((p, i) => (
        <blockquote className="sample-post" key={i}>
          “{p.text}”
          <footer>
            {p.author ?? "unknown"} on {platformLabel(p.source)} · {formatWhen(p.postedAt)}
          </footer>
        </blockquote>
      ))}
      {t.relatedTerms.length > 0 && (
        <>
          <h3 className="section">Related</h3>
          <div className="related-tags">
            {t.relatedTerms.map((r) => (
              <span className="rtag" key={r}>{r}</span>
            ))}
          </div>
        </>
      )}
      <CampaignBlock key={t.trendId} trendId={t.trendId} trendName={t.name} onNotify={onNotify} />
    </div>
  );
}
