import { useEffect, useState } from "react";
import { ApiError, insertIntoCampaign, previewCampaignBlock } from "../api";
import { TONES, type CampaignCopy, type DraftBlock, type Tone } from "../types";

interface Props {
  trendId: string;
  trendName: string;
  onNotify: (message: string) => void;
}

type Preview = { state: "loading" } | { state: "ready"; copy: CampaignCopy } | { state: "error"; message: string };
type Insert = { state: "idle" | "sending" } | { state: "done"; block: DraftBlock } | { state: "error"; message: string };

const TONE_LABELS: Record<Tone, string> = { casual: "Casual", professional: "Professional", expert: "Expert" };

/** Shows exactly what "Insert into campaign" puts into Mailchimp (subject line,
 *  preview text, headline and the content block) before it's sent,
 *  in the tone the marketer picks. After inserting it shows what was inserted
 *  and links to the draft in Mailchimp. */
export function CampaignBlock({ trendId, trendName, onNotify }: Props) {
  const [tone, setTone] = useState<Tone>("casual");
  const [preview, setPreview] = useState<Preview>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [insert, setInsert] = useState<Insert>({ state: "idle" });

  useEffect(() => {
    const ctl = new AbortController();
    setPreview({ state: "loading" });
    previewCampaignBlock(trendId, tone, ctl.signal)
      .then((copy) => setPreview({ state: "ready", copy }))
      .catch((e) => {
        if ((e as Error).name !== "AbortError") setPreview({ state: "error", message: (e as ApiError).message });
      });
    return () => ctl.abort();
  }, [trendId, tone, attempt]);

  const chooseTone = (t: Tone) => {
    if (t === tone) return;
    setTone(t);
    setInsert({ state: "idle" }); // a new tone is a new block; it can be inserted too
  };

  async function doInsert() {
    setInsert({ state: "sending" });
    try {
      const block = await insertIntoCampaign(trendId, tone);
      setInsert({ state: "done", block });
      onNotify(`Added "${trendName}" to a draft Mailchimp campaign`);
    } catch (e) {
      setInsert({ state: "error", message: (e as ApiError).message });
    }
  }

  // After inserting, show what Mailchimp actually got (te-api's answer), not the preview.
  const copy: CampaignCopy | null = insert.state === "done" ? insert.block : preview.state === "ready" ? preview.copy : null;
  const label = { idle: "Insert into campaign", sending: "Inserting…", done: "Inserted", error: "Insert into campaign" }[insert.state];

  return (
    <section className="campaign" aria-label="Campaign block">
      <h3 className="section">Campaign block</h3>
      <p className="campaign-hint">This is exactly what goes into a new draft campaign in Mailchimp.</p>
      <div className="tones" role="group" aria-label="Tone">
        {TONES.map((t) => (
          <button key={t} type="button" className={`chip small${t === tone ? " active" : ""}`} aria-pressed={t === tone}
                  onClick={() => chooseTone(t)} disabled={insert.state === "sending"}>
            {TONE_LABELS[t]}
          </button>
        ))}
      </div>

      {preview.state === "loading" && !copy && <p className="state">Writing the copy…</p>}
      {preview.state === "error" && !copy && (
        <div className="state error" role="alert">
          <p>Couldn't prepare the campaign block: {preview.message}</p>
          <button type="button" className="link-btn" onClick={() => setAttempt((n) => n + 1)}>Try again</button>
        </div>
      )}
      {copy && (
        <article className="email" aria-label="Email preview">
          <dl className="email-meta">
            <div><dt>Subject</dt><dd>{copy.subjectLine}</dd></div>
            <div><dt>Preview text</dt><dd>{copy.previewText}</dd></div>
          </dl>
          <div className="email-body">
            <h4>{copy.headline}</h4>
            <p>{copy.suggestedCopy}</p>
          </div>
        </article>
      )}

      <div className="insert">
        <button type="button" className="insert-btn" onClick={doInsert}
                disabled={!copy || insert.state === "sending" || insert.state === "done"}>
          {label}
        </button>
        {insert.state === "done" && (
          <p className="insert-note" role="status">
            Inserted into draft campaign <code>{insert.block.campaignId}</code> in Mailchimp.{" "}
            {insert.block.previewUrl && (
              <a href={insert.block.previewUrl} target="_blank" rel="noreferrer">Open the draft</a>
            )}
          </p>
        )}
        {insert.state === "error" && <p className="insert-note warn" role="alert">{insert.message}</p>}
      </div>
    </section>
  );
}
