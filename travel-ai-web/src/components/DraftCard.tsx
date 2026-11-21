import { useEffect, useState } from "react";
import { photoUrl } from "../api";
import type { Draft } from "../types";

export default function DraftCard({ draft, onChoose }: { draft: Draft; onChoose: () => void }) {
  const src = photoUrl(draft.photo_name);
  const days = draft.itinerary.days;
  return (
    <article className="draft">
      <div className="photo">{src ? <img src={src} alt="" loading="lazy" /> : <span>🗺️</span>}</div>
      <div className="draft-body">
        <strong className="draft-title">{draft.title}</strong>
        <p className="small muted">{draft.summary}</p>
        <ol className="draft-days">
          {days.map((d) => (
            <li key={d.day_number}>
              <span className="tod">Day {d.day_number}</span>
              <span>
                {d.theme && <em>{d.theme}: </em>}
                {d.activities.map((a) => a.name).join(" · ")}
              </span>
            </li>
          ))}
        </ol>
        {draft.itinerary.stay && <p className="small">🛏 {draft.itinerary.stay.name}</p>}
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
