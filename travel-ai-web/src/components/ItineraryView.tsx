import { useState } from "react";
import { photoUrl } from "../api";
import type { Itinerary } from "../types";

type Props = {
  itinerary: Itinerary;
  busy: boolean;
  onSwap: (day: number, index: number) => void;
  onRemove: (day: number, index: number) => void;
};

/** The chosen plan as a day-by-day timeline. Tap an activity to swap or remove it. */
export default function ItineraryView({ itinerary, busy, onSwap, onRemove }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const stayPhoto = photoUrl(itinerary.stay?.photo_name ?? null);

  return (
    <div className="timeline">
      <header className="timeline-head">
        <span className="muted small">Your trip to {itinerary.destination}</span>
        <h1>{itinerary.title ?? "Your plan"}</h1>
      </header>

      {itinerary.stay && (
        <section className="stay-card">
          {stayPhoto && <img src={stayPhoto} alt="" />}
          <div>
            <span className="muted small">Where you'll stay</span>
            <strong>{itinerary.stay.name}</strong>
            {itinerary.stay.maps_url && (
              <a className="small" href={itinerary.stay.maps_url} target="_blank" rel="noreferrer">
                Map ↗
              </a>
            )}
          </div>
        </section>
      )}

      {itinerary.days.map((day) => (
        <section key={day.day_number} className="day">
          <h3>
            Day {day.day_number}
            {day.theme && <span className="muted"> · {day.theme}</span>}
          </h3>
          <ol>
            {day.activities.map((a, i) => {
              const key = `${day.day_number}-${i}`;
              const photo = photoUrl(a.photo_name);
              return (
                <li key={key} className={selected === key ? "selected" : ""}>
                  <button className="activity" onClick={() => setSelected(selected === key ? null : key)}>
                    {photo ? <img src={photo} alt="" loading="lazy" /> : <span className="thumb">📍</span>}
                    <span className="activity-text">
                      {a.time_of_day && <span className="tod">{a.time_of_day}</span>}
                      <strong>{a.name}</strong>
                      {a.description && <span className="small muted">{a.description}</span>}
                      {a.for_note && <span className="trait">For: {a.for_note}</span>}
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
