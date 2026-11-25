import type { City, Flight, Itinerary, Leg, Note } from "../types";
import NoteList from "./NoteList";

/** "~2h 40m" or "~30 min": these are estimates. */
function hours(minutes: number) {
  if (minutes < 60) return `~${Math.round(minutes / 5) * 5 || 5} min`;
  const m = Math.round((minutes % 60) / 5) * 5;
  return `~${Math.floor(minutes / 60)}h${m ? ` ${String(m).padStart(2, "0")}m` : ""}`;
}

function icon(mode: string) {
  if (/fl(y|ight)|plane|air/i.test(mode)) return "✈️";
  if (/ferry|boat/i.test(mode)) return "⛴️";
  if (/bus|coach/i.test(mode)) return "🚌";
  if (/car|drive/i.test(mode)) return "🚗";
  return /bullet|shinkansen|high.?speed/i.test(mode) ? "🚄" : "🚆";
}

function LegLine({ leg }: { leg: Leg }) {
  return (
    <span className="leg small muted">
      <span aria-hidden="true">{icon(leg.mode)} </span>
      {leg.mode} {hours(leg.minutes)}
      {leg.cost && ` · ${leg.cost}`}
      {leg.note && ` · ${leg.note}`}
    </span>
  );
}

/** Ticket #81: home at either end of the line, with the flight (or ride) to or from it. */
function Home({ flight, way }: { flight: Flight; way: "in" | "out" }) {
  const flying = /fl(y|ight)|plane|air/i.test(flight.mode);
  const what = flying ? (way === "in" ? "Fly in" : "Fly out") : way === "in" ? `Arrive by ${flight.mode}` : `Leave by ${flight.mode}`;
  return (
    <li className={`home ${way}`}>
      <span className="dot" aria-hidden="true" />
      <span className="home-city">{way === "in" ? flight.frm : flight.to}</span>
      <span className="small muted">
        {what} {flying && <span aria-hidden="true">✈ </span>}
        {hours(flight.minutes)}
      </span>
    </li>
  );
}

type Props = { itinerary: Itinerary; notes: Note[]; onCity: (city: City) => void };

/** Ticket #27: the cities in order with their nights and hotel, and how to get from one to the next. */
export default function RouteCard({ itinerary, notes, onCity }: Props) {
  const nights = itinerary.route.reduce((sum, c) => sum + c.nights, 0);
  const legs = itinerary.route.flatMap((c) => (c.leg ? [c.leg] : []));
  const fares = legs.length > 0 && legs.every((leg) => leg.cost); // without the model, legs have no fare
  return (
    <section className="route-card" aria-label="Your route">
      <h3>
        Your route <span className="muted">· {nights} nights</span>
      </h3>
      <ol>
        {itinerary.fly_in && <Home flight={itinerary.fly_in} way="in" />}
        {itinerary.route.map((city, i) => (
          <li key={city.name} className={i === itinerary.route.length - 1 && itinerary.fly_out ? "before-home" : undefined}>
            <span className="dot" aria-hidden="true" />
            {city.leg && <LegLine leg={city.leg} />}
            <button className="city" onClick={() => onCity(city)} aria-label={`${city.name}: go to its first day`}>
              {city.name}
            </button>
            <span className="nights">
              {city.nights} {city.nights === 1 ? "night" : "nights"}
            </span>
            {city.stay ? (
              <span className="hotel small">
                <span aria-hidden="true">🛏 </span>
                {city.stay.maps_url ? (
                  <a href={city.stay.maps_url} target="_blank" rel="noreferrer">
                    {city.stay.name}
                  </a>
                ) : (
                  city.stay.name
                )}
              </span>
            ) : (
              <span className="hotel small muted none">
                No listed stay here yet ·{" "}
                <a
                  href={`https://www.openstreetmap.org/search?query=${encodeURIComponent(`hotels in ${city.name}`)}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Find hotels on OpenStreetMap ↗
                </a>
              </span>
            )}
            <NoteList notes={notes} target={`s${i + 1}`} />
          </li>
        ))}
        {itinerary.fly_out && <Home flight={itinerary.fly_out} way="out" />}
      </ol>
      <p className="small muted route-note">
        {itinerary.route_tip && <>🎫 {itinerary.route_tip} </>}
        {fares ? "Times and fares are rough estimates." : "Times are rough estimates."}
      </p>
    </section>
  );
}
