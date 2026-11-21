import { useEffect, useState } from "react";
import { api } from "../api";
import type { TripSummary, User } from "../types";

type Props = { user: User; openTrip: (id: string, firstMessage?: string) => void };

export default function Home({ user, openTrip }: Props) {
  const [trips, setTrips] = useState<TripSummary[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.trips().then(setTrips);
  }, []);

  // A new trip always starts as a conversation; what the user typed becomes the first message.
  const start = async (firstMessage?: string) => {
    setBusy(true);
    setError(null);
    try {
      const trip = await api.createTrip();
      openTrip(trip.id, firstMessage);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <main className="page">
      <section className="hero">
        <h1>Where to next{user.name ? `, ${user.name.split(" ")[0]}` : ""}?</h1>
        <form
          className="hero-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) start(text.trim());
          }}
        >
          <input
            placeholder="“A slow week in Lisbon with my partner”"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button className="primary" disabled={busy || !text.trim()}>
            Plan it
          </button>
        </form>
        <div className="row">
          <button className="ghost" disabled={busy} onClick={() => start("Surprise me")}>
            ✨ Surprise me
          </button>
          <button className="ghost" disabled={busy} onClick={() => start()}>
            Just chat
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </section>

      {trips.length > 0 && (
        <section>
          <h2>Your trips</h2>
          <ul className="trip-list">
            {trips.map((t) => (
              <li key={t.id}>
                <button onClick={() => openTrip(t.id)}>
                  <strong>{t.title}</strong>
                  <span className="muted small">
                    {t.destination ?? "No destination yet"} · {new Date(t.updated_at).toLocaleDateString()}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
