import { photoUrl } from "../api";
import type { Pick, PickAction } from "../types";

const TIER_LABEL = { budget: "Budget", mid: "Mid-range", luxury: "Luxury" } as const;

type Props = {
  pick: Pick;
  liked: boolean;
  onAction: (action: PickAction) => void;
  /** When set, the card is a swap alternative: one "Use this" button instead of like/add. */
  onUse?: () => void;
};

export default function PickCard({ pick, liked, onAction, onUse }: Props) {
  const src = photoUrl(pick.photo_name);
  const facts = [pick.rating && `★ ${pick.rating}`, pick.price_level, pick.type_label].filter(Boolean).join(" · ");
  return (
    <article className={`pick ${liked ? "liked" : ""}`}>
      <div className="photo">{src ? <img src={src} alt="" loading="lazy" /> : <span>📍</span>}</div>
      <div className="pick-body">
        <div className="pick-head">
          <strong>{pick.name}</strong>
          <span className="small muted">{facts}</span>
          {pick.category === "stay" && pick.tier && !pick.price_level && (
            <span className="tier" title="Estimated from the type of place">
              {TIER_LABEL[pick.tier]} (est.)
            </span>
          )}
        </div>
        {pick.why && <p className="why">{pick.why}</p>}
        {pick.trait && <span className="trait">For you: {pick.trait.toLowerCase()}</span>}
        <div className="pick-actions">
          {onUse ? (
            <button className="primary" onClick={onUse}>
              Use this
            </button>
          ) : (
            <>
              <button
                className={liked ? "liked-btn" : ""}
                onClick={() => onAction(liked ? "unlike" : "like")}
                aria-pressed={liked}
                title="More places like this"
              >
                {liked ? "♥" : "♡"} Like
              </button>
              <button onClick={() => onAction("add")} title="Add to my trip">
                ＋ Add
              </button>
              <button className="ghost" onClick={() => onAction("skip")} title="Not for me">
                ✕
              </button>
              {pick.category === "stay" && (
                <button className="ghost small" onClick={() => onAction("too_pricey")}>
                  Too pricey
                </button>
              )}
            </>
          )}
          {pick.maps_url && (
            <a className="small" href={pick.maps_url} target="_blank" rel="noreferrer">
              Map ↗
            </a>
          )}
        </div>
      </div>
    </article>
  );
}
