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

/**
 * Photo cards for travelers who can't describe their trip style. Swipe, the buttons or the arrow keys
 * (→ that's me, ← not for me, ↓ not sure). Any way out ends the deck: `onDone` gets the trip with the summary
 * said in the chat, or null if nothing was answered.
 */
export default function SwipeDeck({ tripId, onDone }: { tripId: string; onDone: (trip: Trip | null) => void }) {
  const [deck, setDeck] = useState<Deck | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dx, setDx] = useState(0);
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
    try {
      setDeck(await api.swipe(tripId, deck.card.id, a));
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
    const a: Answer | undefined = { ArrowRight: "right", ArrowLeft: "left", ArrowDown: "skip" }[e.key] as Answer;
    if (a) {
      e.preventDefault();
      answer(a);
    } else if (e.key === "Escape") finish();
  };

  const card = deck?.card;
  const done = deck && !card;
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
          {deck && (
            <span className="small muted">
              {Math.min(deck.answered + 1, deck.limit)} of up to {deck.limit}
            </span>
          )}
        </header>
        <p id="swipe-help" className="small muted">
          Swipe right if it's you, left if it's not. Keys: → that's me, ← not for me, ↓ not sure.
        </p>

        <p className="swipe-sr" aria-live="polite">
          {card ? `${card.title}. That's me, or not for me?` : done ? "That's plenty to go on." : ""}
        </p>
        {card && (
          <figure
            key={card.id}
            className={`swipe-card ${dx > THROW / 2 ? "yes" : dx < -THROW / 2 ? "no" : ""}`}
            style={{ transform: `translateX(${dx}px) rotate(${dx / 20}deg)` }}
            onPointerDown={(e) => {
              start.current = e.clientX;
              e.currentTarget.setPointerCapture(e.pointerId);
            }}
            onPointerMove={(e) => start.current !== null && setDx(e.clientX - start.current)}
            onPointerUp={() => {
              start.current = null;
              if (Math.abs(dx) > THROW) answer(dx > 0 ? "right" : "left");
              else setDx(0);
            }}
            onPointerCancel={() => {
              start.current = null;
              setDx(0);
            }}
          >
            <img src={card.photo} alt="" draggable={false} />
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
            <strong>That's plenty to go on.</strong>
            <span className="muted">Let's see what fits you.</span>
          </div>
        )}
        {!deck && !error && <div className="swipe-done muted">Loading…</div>}
        {error && <p className="error">{error}</p>}

        {card && (
          <div className="swipe-buttons">
            <button disabled={busy} onClick={() => answer("left")}>
              ✕ Not for me
            </button>
            <button className="ghost" disabled={busy} onClick={() => answer("skip")}>
              Not sure
            </button>
            <button className="primary" disabled={busy} onClick={() => answer("right")}>
              ♥ That's me
            </button>
          </div>
        )}
        <button className={done ? "primary swipe-finish" : "ghost swipe-finish"} disabled={busy} onClick={finish}>
          {deck?.answered ? "✨ Show me plans" : "Not now"}
        </button>
      </div>
    </div>
  );
}
