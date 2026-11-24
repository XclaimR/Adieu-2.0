import { useEffect, useState } from "react";
import { api } from "../api";
import IdeaCard from "../components/IdeaCard";
import Credits from "../components/Credits";
import OptionsPanel from "../components/OptionsPanel";
import type { StarterIdea, TripSummary, User } from "../types";

type Props = { user: User; openTrip: (id: string, firstMessage?: string) => void };

type Slot = "when" | "where" | "who" | "why";

// What each vibe (taste_service.VIBES) suggests for "where" and "mostly for".
const VIBE_WHERE: Record<string, string> = {
  beach: "somewhere warm",
  mountains: "in the mountains",
  city: "in a big city",
  nature: "somewhere wild",
  adventure: "somewhere wild",
};
const VIBE_WHY: Record<string, string> = {
  food: "the food",
  culture: "history and culture",
  beach: "beach time",
  mountains: "hiking",
  city: "city life",
  nightlife: "nights out",
  nature: "nature",
  adventure: "adventure",
  wellness: "rest",
  luxury: "treating ourselves",
  budget: "great value",
  photo: "the views",
};

const CHOICES: Record<Slot, { title: string; options: string[] }> = {
  when: { title: "How long?", options: ["A weekend", "A few days", "A week", "Ten days", "Two weeks"] },
  where: {
    title: "Where?",
    options: ["somewhere warm", "in a big city", "in the mountains", "somewhere wild", "somewhere new to me", "in Japan", "in Portugal"],
  },
  who: { title: "Who's coming?", options: ["on my own", "with my partner", "with friends", "with my family", "with family and kids"] },
  why: { title: "Mostly for…", options: [...new Set(Object.values(VIBE_WHY))] },
};

export default function Home({ user, openTrip }: Props) {
  const [trips, setTrips] = useState<TripSummary[]>([]);
  const vibes = user.taste.vibes;
  const [answers, setAnswers] = useState<Record<Slot, string>>({
    when: "A week",
    where: vibes.map((v) => VIBE_WHERE[v]).find(Boolean) ?? "somewhere new to me",
    who: "with my partner",
    why: vibes.map((v) => VIBE_WHY[v]).find(Boolean) ?? "a bit of everything",
  });
  const [slot, setSlot] = useState<Slot | null>(null);
  const [other, setOther] = useState("");
  const [writing, setWriting] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // null while loading; ideas are a nice-to-have, so a failure just hides the section.
  const [ideas, setIdeas] = useState<StarterIdea[] | null>(null);

  useEffect(() => {
    api.trips().then(setTrips);
    api.starterIdeas().then(setIdeas).catch(() => setIdeas([]));
  }, []);

  // A new trip always starts as a conversation; the sentence (or what the user wrote) is the first message.
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

  const sentence = `${answers.when} ${answers.where} ${answers.who}, mostly for ${answers.why}.`;
  const choose = (value: string) => {
    let v = value.trim();
    // A typed place ("Lisbon") still reads as a sentence: "A week in Lisbon".
    if (slot === "where" && v && !/^(in|to|on|at|around|somewhere|anywhere)\b/i.test(v)) v = `in ${v}`;
    if (slot && v) setAnswers((a) => ({ ...a, [slot]: v }));
    setSlot(null);
    setOther("");
  };
  const blank = (s: Slot) => (
    <button className="blank" onClick={() => setSlot(s)} aria-label={`${CHOICES[s].title} ${answers[s]}`}>
      {answers[s]}
    </button>
  );

  return (
    <main className="page">
      <section className="hero">
        <p className="muted">
          Hi{user.name ? ` ${user.name.split(" ")[0]}` : ""}. Finish the sentence and I'll plan the rest.
        </p>
        {writing ? (
          <form
            className="hero-form"
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim()) start(text.trim());
            }}
          >
            <input
              autoFocus
              aria-label="Your trip in your own words"
              placeholder="“A slow week in Portugal with my partner”"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            <button className="primary" disabled={busy || !text.trim()}>
              Plan it
            </button>
          </form>
        ) : (
          <>
            <h1 className="sentence">
              {blank("when")} {blank("where")} {blank("who")}, mostly for {blank("why")}.
            </h1>
            <p className="small muted">Tap any underlined part to change it.</p>
            <button className="primary plan-it" disabled={busy} onClick={() => start(sentence)}>
              Plan it
            </button>
          </>
        )}
        <div className="row">
          <button disabled={busy} onClick={() => setWriting((w) => !w)}>
            {writing ? "Fill in the sentence" : "Write it myself"}
          </button>
          <button disabled={busy} onClick={() => start("Surprise me")}>
            Surprise me
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </section>

      {trips.length > 0 && (
        <section>
          <h2>Your trips</h2>
          <ul className="trip-list">
            {trips.map((t) => {
              // The title becomes the chosen plan's name, so a different title means a plan was chosen (not the reverse:
              // older trips kept the destination as title).
              const planned = !!t.destination && t.title !== t.destination;
              const days = Math.min(t.duration_days ?? 0, 14);
              return (
                <li key={t.id}>
                  <button className={`ticket ${planned ? "planned" : ""}`} onClick={() => openTrip(t.id)}>
                    <span className="ticket-head">
                      <strong>{t.destination ?? t.title}</strong>
                      {planned && <span className="small">Plan chosen</span>}
                    </span>
                    <span className="small muted">
                      {[planned && t.title, t.duration_days && `${t.duration_days} days`].filter(Boolean).join(", ") ||
                        `Edited ${new Date(t.updated_at).toLocaleDateString()}`}
                    </span>
                    {days > 0 && (
                      <span className="ticket-days" aria-hidden>
                        {Array.from({ length: days }, (_, i) => (
                          <span key={i} />
                        ))}
                      </span>
                    )}
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
      <Credits />

      <OptionsPanel open={!!slot} title={slot ? CHOICES[slot].title : ""} onClose={() => setSlot(null)}>
        {slot && (
          <div className="slot-choices">
            <div className="slot-options">
              {CHOICES[slot].options.map((o) => (
                <button key={o} className={o === answers[slot] ? "on" : ""} aria-pressed={o === answers[slot]} onClick={() => choose(o)}>
                  {o}
                </button>
              ))}
            </div>
            <form
              className="slot-other"
              onSubmit={(e) => {
                e.preventDefault();
                choose(other);
              }}
            >
              <label className="small muted" htmlFor="slot-other">
                Something else
              </label>
              <span className="row">
                <input id="slot-other" value={other} onChange={(e) => setOther(e.target.value)} />
                <button className="primary" disabled={!other.trim()}>
                  Use this
                </button>
              </span>
            </form>
          </div>
        )}
      </OptionsPanel>
    </main>
  );
}
