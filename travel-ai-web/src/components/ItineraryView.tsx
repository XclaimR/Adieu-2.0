import { useRef, useState } from "react";
import Photo, { Credit } from "./Photo";
import type { Itinerary, Note } from "../types";
import NoteList from "./NoteList";

const SLOTS = ["morning", "afternoon", "evening"];

/** "45 min", "1.5 h", "2 h": to the nearest half hour, since these are estimates. */
export function duration(minutes: number) {
  if (minutes < 60) return `${Math.round(minutes / 15) * 15 || 15} min`;
  return `${Math.round(minutes / 30) / 2} h`;
}

type Props = {
  itinerary: Itinerary;
  notes: Note[];
  busy: boolean;
  onSwap: (day: number, index: number) => void;
  onRemove: (day: number, index: number) => void;
  /** Starts a chat message for changes that only the conversation can make (moves, a whole day). */
  onAsk: (text: string) => void;
};

/** The chosen plan: day tabs over the days, which swipe on phones and sit side by side on wide screens.
 * Tap a stop to swap, move or remove it. */
export default function ItineraryView({ itinerary, notes, busy, onSwap, onRemove, onAsk }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const [current, setCurrent] = useState(0);
  const daysRef = useRef<HTMLDivElement>(null);

  const goTo = (i: number) => {
    const el = daysRef.current?.children[i] as HTMLElement | undefined;
    daysRef.current?.scrollTo({ left: el ? el.offsetLeft - daysRef.current.offsetLeft : 0, behavior: "smooth" });
    setCurrent(i);
  };
  const onScroll = () => {
    const box = daysRef.current;
    if (!box) return;
    const kids = Array.from(box.children) as HTMLElement[];
    const i = kids.findIndex((k) => k.offsetLeft - box.offsetLeft + k.offsetWidth / 2 > box.scrollLeft);
    if (i >= 0 && i !== current) setCurrent(i);
  };

  return (
    <div className="timeline">
      {itinerary.stay && (
        <section className="stay-card">
          {itinerary.stay.photo_name && <Photo name={itinerary.stay.photo_name} fallback="" />}
          <div>
            <span className="muted small">Where you'll stay</span>
            <strong>{itinerary.stay.name}</strong>
            <NoteList notes={notes} target="s1" />
            <span className="links small">
              {itinerary.stay.maps_url && (
                <a href={itinerary.stay.maps_url} target="_blank" rel="noreferrer">
                  Map ↗
                </a>
              )}
              {itinerary.stay.source_url && (
                <a href={itinerary.stay.source_url} target="_blank" rel="noreferrer">
                  Wikivoyage ↗
                </a>
              )}
              <Credit name={itinerary.stay.photo_name} />
            </span>
          </div>
        </section>
      )}

      <nav className="day-tabs" aria-label="Days">
        {itinerary.days.map((day, i) => (
          <button
            key={day.day_number}
            className={i === current ? "on" : ""}
            aria-current={i === current ? "true" : undefined}
            onClick={() => goTo(i)}
          >
            <strong>{day.day_number}</strong>
            <span>{day.theme ?? `Day ${day.day_number}`}</span>
          </button>
        ))}
      </nav>

      <div className="days" ref={daysRef} onScroll={onScroll}>
        {itinerary.days.map((day) => (
          <section key={day.day_number} className="day" aria-label={`Day ${day.day_number}`}>
            <div className="day-head">
              <h3>
                Day {day.day_number}
                {day.theme && <span className="muted"> · {day.theme}</span>}
                <span className="day-total">
                  ≈ {duration(day.activities.reduce((sum, a) => sum + a.minutes, 0))} planned, plus travel
                </span>
              </h3>
              <button className="ghost small" disabled={busy} onClick={() => onAsk(`On day ${day.day_number}, `)}>
                Change this day
              </button>
            </div>
            <NoteList notes={notes} target={`d${day.day_number}`} />
            {[...SLOTS, null].map((slot) => {
              const stops = day.activities
                .map((a, i) => ({ a, i }))
                .filter(({ a }) => (slot ? a.time_of_day === slot : !SLOTS.includes(a.time_of_day ?? "")));
              if (!stops.length) return null;
              // Only a meal here (e.g. its neighbour was removed): say the rest is free, not a whole slot of lunch.
              const free = slot && stops.every(({ a }) => a.category === "eat");
              return (
                <section key={slot ?? "any"} className="slot" aria-label={slot ?? "Any time"}>
                  <h4 className="tod">{slot ?? "Any time"}</h4>
                  <ol>
                    {stops.map(({ a, i }) => {
                      const key = `${day.day_number}-${i}`;
                      return (
                        <li key={key} className={selected === key ? "selected" : ""}>
                          <button
                            className="activity"
                            aria-expanded={selected === key}
                            onClick={() => setSelected(selected === key ? null : key)}
                          >
                            <span className="thumb">
                              <Photo name={a.photo_name} fallback="📍" />
                            </span>
                            <span className="activity-text">
                              <strong>{a.name}</strong>
                              {a.description && <span className="small muted">{a.description}</span>}
                              {a.for_note && <span className="trait">For: {a.for_note}</span>}
                              <NoteList notes={notes.filter((n) => n.text !== a.for_note)} target={a.id} />
                            </span>
                            <span className="dur-chip" aria-label={`about ${duration(a.minutes)}`}>
                              {duration(a.minutes)}
                            </span>
                          </button>
                          {selected === key && (
                            <div className="activity-actions">
                              <button className="primary" disabled={busy} onClick={() => onSwap(day.day_number, i)}>
                                Swap
                              </button>
                              <button disabled={busy} onClick={() => onAsk(`Move ${a.name} to day `)}>
                                Move
                              </button>
                              <button
                                className="ghost"
                                disabled={busy}
                                onClick={() => {
                                  setSelected(null);
                                  onRemove(day.day_number, i);
                                }}
                              >
                                Remove
                              </button>
                              <span className="links small">
                                {a.maps_url && (
                                  <a href={a.maps_url} target="_blank" rel="noreferrer">
                                    Map ↗
                                  </a>
                                )}
                                {a.source_url && (
                                  <a href={a.source_url} target="_blank" rel="noreferrer">
                                    Wikivoyage ↗
                                  </a>
                                )}
                                <Credit name={a.photo_name} />
                              </span>
                            </div>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                  {free && <p className="slot-free small muted">Rest of the {slot} is free</p>}
                </section>
              );
            })}
          </section>
        ))}
      </div>
    </div>
  );
}
