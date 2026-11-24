import { useEffect, useState } from "react";
import Photo from "./Photo";
import type { Draft } from "../types";

// The three flavors DraftService writes (draft_service.FLAVORS).
const FLAVOR: Record<string, string> = { easy: "Slow and easy", classic: "The highlights", local: "Like a local" };

export default function DraftCard({ draft, onChoose }: { draft: Draft; onChoose: () => void }) {
  const days = draft.itinerary.days;
  const first = days[0];
  const last = days[days.length - 1];
  const label = (d: typeof first) => d.theme ?? d.activities[0]?.name ?? `Day ${d.day_number}`;
  return (
    <article className="draft">
      {draft.photo_name && (
        <div className="photo">
          <Photo name={draft.photo_name} fallback="" withCredit />
        </div>
      )}
      <div className="draft-body">
        <span className="flavor">{FLAVOR[draft.flavor] ?? draft.flavor}</span>
        <strong className="draft-title">{draft.title}</strong>
        {days.length > 1 && (
          <div className="route" role="img" aria-label={`${days.length} days, from ${label(first)} to ${label(last)}`}>
            <span className="route-stops">
              {days.map((d) => (
                <span key={d.day_number} />
              ))}
            </span>
            <span className="route-ends small muted">
              <span>{label(first)}</span>
              <span>{label(last)}</span>
            </span>
          </div>
        )}
        <p className="small muted">{draft.summary}</p>
        {draft.trait && <span className="trait">For you: {draft.trait.toLowerCase()}</span>}
        {draft.itinerary.stay && <p className="small">Staying at {draft.itinerary.stay.name}</p>}
        {draft.itinerary.route?.length > 1 && (
          <p className="small">{draft.itinerary.route.map((c) => `${c.name} ${c.nights}n`).join(" → ")}</p>
        )}
        {draft.warnings.length > 0 && (
          <div className="small check">
            <strong>Check first:</strong>
            <ul>
              {draft.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </div>
        )}
        {draft.unplanned.length > 0 && <p className="small muted">Not fitted in yet: {draft.unplanned.join(", ")}</p>}
        <details className="draft-more">
          <summary>See the days</summary>
          <ol className="draft-days">
            {days.map((d) => (
              <li key={d.day_number}>
                <span className="tod">Day {d.day_number}</span>
                <span>
                  {d.theme && <em>{d.theme}: </em>}
                  {d.activities.map((a) => a.name).join(", ")}
                </span>
              </li>
            ))}
          </ol>
        </details>
        <button className="primary" onClick={onChoose}>
          Choose this plan
        </button>
      </div>
    </article>
  );
}

export function DraftSkeleton({ label, since }: { label: string; since: number }) {
  // A ticking clock shows the wait is progressing; drafts can take a minute or more on local models.
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const secs = Math.max(0, Math.round((now - since) / 1000));
  return (
    <article className="draft skeleton" aria-busy="true">
      <div className="photo" />
      <div className="draft-body">
        <span className="muted">
          {label} <span className="small">{Math.floor(secs / 60)}:{String(secs % 60).padStart(2, "0")}</span>
        </span>
        <span className="line" />
        <span className="line short" />
        <span className="line" />
      </div>
    </article>
  );
}
