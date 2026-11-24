import { useState } from "react";
import Photo, { Credit } from "./Photo";
import type { Itinerary, Note } from "../types";
import NoteList from "./NoteList";

type Props = {
  itinerary: Itinerary;
  notes: Note[];
  busy: boolean;
  onSwap: (day: number, index: number) => void;
  onRemove: (day: number, index: number) => void;
};

/** The chosen plan as a day-by-day timeline. Tap an activity to swap or remove it. */
export default function ItineraryView({ itinerary, notes, busy, onSwap, onRemove }: Props) {
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div className="timeline">
      <header className="timeline-head">
        <span className="muted small">Your trip to {itinerary.destination}</span>
        <h1>{itinerary.title ?? "Your plan"}</h1>
      </header>

      {itinerary.stay && (
        <section className="stay-card">
          {itinerary.stay.photo_name && <Photo name={itinerary.stay.photo_name} fallback="🛏" />}
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

      {itinerary.days.map((day) => (
        <section key={day.day_number} className="day">
          <h3>
            Day {day.day_number}
            {day.theme && <span className="muted"> · {day.theme}</span>}
          </h3>
          <NoteList notes={notes} target={`d${day.day_number}`} />
          <ol>
            {day.activities.map((a, i) => {
              const key = `${day.day_number}-${i}`;
              return (
                <li key={key} className={selected === key ? "selected" : ""}>
                  <button className="activity" onClick={() => setSelected(selected === key ? null : key)}>
                    <span className="thumb">
                      <Photo name={a.photo_name} fallback="📍" />
                    </span>
                    <span className="activity-text">
                      {a.time_of_day && <span className="tod">{a.time_of_day}</span>}
                      <strong>{a.name}</strong>
                      {a.description && <span className="small muted">{a.description}</span>}
                      {a.for_note && <span className="trait">For: {a.for_note}</span>}
                      <NoteList notes={notes.filter((n) => n.text !== a.for_note)} target={a.id} />
                    </span>
                  </button>
                  {selected === key && (
                    <div className="activity-actions">
                      <button disabled={busy} onClick={() => onSwap(day.day_number, i)}>
                        ⇄ Swap
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
                      {a.maps_url && (
                        <a className="small" href={a.maps_url} target="_blank" rel="noreferrer">
                          Map ↗
                        </a>
                      )}
                      {a.source_url && (
                        <a className="small" href={a.source_url} target="_blank" rel="noreferrer">
                          Wikivoyage ↗
                        </a>
                      )}
                      <Credit name={a.photo_name} />
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </div>
  );
}
