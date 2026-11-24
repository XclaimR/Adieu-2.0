import { useEffect, useState } from "react";
import { api } from "../api";
import Photo from "../components/Photo";
import IdeaCard from "../components/IdeaCard";
import type { StarterIdea, TripSummary, User } from "../types";

type Props = { user: User; openTrip: (id: string, firstMessage?: string) => void };

export default function Home({ user, openTrip }: Props) {
  const [trips, setTrips] = useState<TripSummary[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // null while loading; ideas are a nice-to-have, so a failure just hides the section.
  const [ideas, setIdeas] = useState<StarterIdea[] | null>(null);

  useEffect(() => {
    api.trips().then(setTrips);
    api.starterIdeas().then(setIdeas).catch(() => setIdeas([]));
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
            placeholder="“A slow week in Portugal with my partner”"
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
            {trips.map((t) => {
              return (
                <li key={t.id}>
                  <button onClick={() => openTrip(t.id)}>
                    <span className="trip-thumb">
                      <Photo name={t.photo_name} fallback="🧭" />
                    </span>
                    <span className="trip-text">
                      <strong>{t.title}</strong>
                      <span className="muted small">
                        {[
                          t.title !== t.destination && t.destination,
                          t.duration_days && `${t.duration_days} days`,
                          `edited ${new Date(t.updated_at).toLocaleDateString()}`,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {ideas !== null && ideas.length === 0 ? null : (
        <section>
          <h2>Ideas for you</h2>
          <div className="idea-grid">
            {ideas === null
              ? Array.from({ length: 3 }, (_, i) => (
                  <div key={i} className="idea skeleton" aria-busy="true">
                    <div className="photo" />
                    <div className="idea-body">
                      <span className="muted small">Finding trips that fit your vibe…</span>
                    </div>
                  </div>
                ))
              : ideas.map((idea) => (
                  <IdeaCard
                    key={idea.title}
                    idea={idea}
                    onChoose={() => start(`Let's do “${idea.title}” in ${idea.destination}`)}
                  />
                ))}
          </div>
        </section>
      )}
    </main>
  );
}
