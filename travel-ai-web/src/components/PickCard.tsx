import Photo from "./Photo";
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
  // Open data has no ratings or prices; old Google picks still have theirs.
  const facts = [pick.rating && `★ ${pick.rating}`, pick.price_level, pick.type_label, pick.address]
    .filter(Boolean)
    .join(" · ");
  return (
    <article className={`pick ${liked ? "liked" : ""}`}>
      <div className="photo">
        <Photo name={pick.photo_name} fallback="📍" withCredit />
      </div>
      <div className="pick-body">
        <div className="pick-head">
          <strong>{pick.name}</strong>
          <span className="small muted">{facts}</span>
          {pick.category === "stay" && !pick.price_level && (
            <span className="tier" title="Estimated from the type of place and the guide; no prices are known">
              {pick.tier ? `${TIER_LABEL[pick.tier]} (est.)` : "Price unknown"}
            </span>
          )}
        </div>
        {pick.summary && (
          <p className="small summary">
            {pick.summary}{" "}
            {pick.source_url && (
              <a href={pick.source_url} target="_blank" rel="noreferrer" title="Text: Wikivoyage, CC BY-SA 4.0">
                Wikivoyage ↗
              </a>
            )}
          </p>
        )}
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
