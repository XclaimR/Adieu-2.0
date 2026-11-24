import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Deck, Trip } from "../types";
import "./SwipeDeck.css";

/** The chip the server offers to unsure travelers; tapping it opens the deck instead of sending a message. */
export const SWIPE_CHIP = "Swipe to find my style";
/** Home passes this as the trip's first message to open the deck straight away. */
export const OPEN_DECK = "__swipe__";

type Answer = "right" | "left" | "skip";
const THROW = 90; // px of drag that counts as a swipe
const FLY = 480; // px a tapped answer sends the card, so buttons and keys show the same stamp as a swipe

/**
 * Photo cards for travelers who can't describe their trip style. Swipe, the buttons or the arrow keys
 * (→ that's me, ← not for me, ↓ not sure). Any way out ends the deck: `onDone` gets the trip with the summary
 * said in the chat, or null if nothing was answered. Full-screen sheet on phones, a centred card on desktop.
 */
export default function SwipeDeck({ tripId, onDone }: { tripId: string; onDone: (trip: Trip | null) => void }) {
  const [deck, setDeck] = useState<Deck | null>(null);
  // This visit's answers, for the progress ticks; earlier visits' answers show as plain ticks.
  const [trail, setTrail] = useState<Answer[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dx, setDx] = useState(0);
  const [dragging, setDragging] = useState(false);
  const start = useRef<number | null>(null);
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.deck(tripId).then(setDeck).catch((e) => setError(e.message));
    dialog.current?.focus();
  }, [tripId]);

  const answer = async (a: Answer) => {
    if (!deck?.card || busy) return;
    setBusy(true);
    setError(null);
    setDx(a === "right" ? FLY : a === "left" ? -FLY : 0);
    try {
      const next = await api.swipe(tripId, deck.card.id, a);
      setTrail((t) => [...t, a]);
      setDeck(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setDx(0);
    }
  };

  const finish = async () => {
    if (busy) return;
    if (!deck?.answered) return onDone(null);
    setBusy(true);
    try {
      onDone(await api.finishSwipes(tripId));
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    const a = ({ ArrowRight: "right", ArrowLeft: "left", ArrowDown: "skip" } as Record<string, Answer>)[e.key];
    if (a) {
      e.preventDefault();
      answer(a);
    } else if (e.key === "Escape") finish();
  };

  const release = () => {
    start.current = null;
    setDragging(false);
  };

  const card = deck?.card;
  const done = deck && !card;
  const lean = dx > THROW / 3 ? "yes" : dx < -THROW / 3 ? "no" : "";
  const earlier = (deck?.answered ?? 0) - trail.length;
  return (
    <div className="swipe-backdrop">
      <div
        className="swipe-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="swipe-title"
        aria-describedby="swipe-help"
        tabIndex={-1}
        ref={dialog}
        onKeyDown={onKey}
      >
        <header className="swipe-head">
          <h2 id="swipe-title">Find your trip style</h2>
          <button className="swipe-out" disabled={busy} onClick={finish}>
            {deck?.answered ? "Show me plans" : "Not now"}
          </button>
        </header>

        {deck && (
          <ol className="swipe-ticks" aria-label={`${deck.answered} of up to ${deck.limit} answered`}>
            {Array.from({ length: deck.limit }, (_, i) => (
              <li key={i} className={i < earlier ? "earlier" : (trail[i - earlier] ?? "")} />
            ))}
          </ol>
        )}

        <p className="swipe-sr" aria-live="polite">
          {card ? `${card.title}. That's me, or not for me?` : done ? "That's enough to go on." : ""}
        </p>

        {card && (
          <figure
            key={card.id}
            className={`swipe-card ${lean} ${dragging ? "dragging" : ""}`}
            style={{ transform: `translateX(${dx}px) rotate(${dx / 24}deg)` }}
            onPointerDown={(e) => {
              start.current = e.clientX;
              setDragging(true);
              e.currentTarget.setPointerCapture(e.pointerId);
            }}
            onPointerMove={(e) => start.current !== null && setDx(e.clientX - start.current)}
            onPointerUp={() => {
              release();
              if (Math.abs(dx) > THROW) answer(dx > 0 ? "right" : "left");
              else setDx(0);
            }}
            onPointerCancel={() => {
              release();
              setDx(0);
            }}
          >
            <img src={card.photo} alt="" draggable={false} />
            <span className="swipe-stamp" aria-hidden>
              {lean === "yes" ? "That's me" : lean === "no" ? "Not me" : ""}
            </span>
            <figcaption>
              <strong>{card.title}</strong>
              <span className="swipe-credit">
                Photo: {card.credit.by},{" "}
                <a href={card.credit.source} target="_blank" rel="noreferrer">
                  {card.credit.licence}
                </a>
              </span>
            </figcaption>
          </figure>
        )}
        {done && (
          <div className="swipe-done" role="status">
            <strong>That's enough to go on.</strong>
            <button className="primary" disabled={busy} onClick={finish}>
              Show me plans
            </button>
          </div>
        )}
        {!deck && !error && <div className="swipe-done muted">Loading…</div>}
        {error && <p className="error">{error}</p>}

        {card && (
          <>
            <p id="swipe-help" className="swipe-help">
              Swipe right if it's you, left if it's not.
              <span className="swipe-keys"> Or use ← → and ↓ for not sure.</span>
            </p>
            <div className="swipe-buttons">
              <button className="swipe-round no" disabled={busy} onClick={() => answer("left")} aria-label="Not for me">
                ✕
              </button>
              <button className="swipe-skip" disabled={busy} onClick={() => answer("skip")}>
                Not sure
              </button>
              <button className="swipe-round yes" disabled={busy} onClick={() => answer("right")} aria-label="That's me">
                ✓
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
