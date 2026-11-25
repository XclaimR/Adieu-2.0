import { useRef, useState } from "react";
import Photo, { Credit } from "./Photo";
import type { Itinerary, Note } from "../types";
import NoteList from "./NoteList";
import RouteCard from "./RouteCard";

const SLOTS = ["morning", "afternoon", "evening"];
// An evening bar is the outing itself, not a meal that leaves the evening free.
const NIGHT_OUT = /\b(bar|pub|club|cocktails?|brewery)\b/i;

/** Ticket #67: a small mark for each kind of tip (option A: a line under the stop's note). */
function TipMark({ kind }: { kind: string }) {
  const path = {
    book: "M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2zM4 10h16M9 3v4M15 3v4",
    sunset: "M4 18h16M7 18a5 5 0 0 1 10 0M12 7v2M5.6 10.6l1.4 1.4M18.4 10.6 17 12",
    instead: "M7 7h11l-3-3M17 17H6l3 3",
  }[kind] ?? "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2";
  return (
    <svg className="tip-mark" viewBox="0 0 24 24" aria-hidden="true">
      <path d={path} />
    </svg>
  );
}

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
      {(itinerary.route?.length > 0 || itinerary.fly_in) && (
        <RouteCard
          itinerary={itinerary}
          notes={notes}
          onCity={(city) => goTo(Math.max(0, itinerary.days.findIndex((d) => d.city === city.name)))}
        />
      )}
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
                {day.city && <span className="muted"> · {day.city}</span>}
                {day.theme && <span className="muted"> · {day.theme}</span>}
                {day.activities.length > 0 && (
                  <span className="day-total">
                    ≈ {duration(day.activities.reduce((sum, a) => sum + a.minutes, 0))} planned, plus travel
                  </span>
                )}
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
              const free =
                slot &&
                stops.every(
                  ({ a }) =>
                    a.category === "eat" && !(slot === "evening" && NIGHT_OUT.test(`${a.name} ${a.description ?? ""}`)),
                );
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
                              {a.tip && (
                                <span className="tip-line">
                                  <TipMark kind={a.tip_kind ?? "time"} />
                                  {a.tip}
                                </span>
                              )}
                              {a.instead_of && (
                                <span className="tip-line instead">
                                  <TipMark kind="instead" />
                                  Instead of {a.instead_of}, which is crowded
                                </span>
                              )}
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
