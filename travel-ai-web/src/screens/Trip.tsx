import { useEffect, useRef, useState } from "react";
import { ApiError, api, sendStream } from "../api";
import type { OptionSet, Personality, PickAction, Trip, TripProfile } from "../types";
import PickCard from "../components/PickCard";
import IdeaCard from "../components/IdeaCard";
import DraftCard, { DraftSkeleton } from "../components/DraftCard";
import OptionsPanel from "../components/OptionsPanel";
import ItineraryView from "../components/ItineraryView";
import NoteList from "../components/NoteList";

const DRAFT_COUNT = 3;
const SHOW_PLANS = "__show_plans__";

type PanelView = { view: "options"; set: OptionSet } | { view: "drafts" };

type Live = {
  userText: string;
  steps: string[];
  reply: { text: string; next_question: string | null; answer_options: string[] } | null;
};

function profileFacts(p: TripProfile): string[] {
  const facts = [
    p.destination,
    ...p.mood,
    p.party && (p.party_size ? `${p.party} of ${p.party_size}` : p.party),
    p.kids && "with kids",
    p.duration_days && `${p.duration_days} days`,
    p.start_date,
    p.budget_level && `${p.budget_level} budget`,
    ...p.preferences.map((x) => x.value),
  ];
  return [...new Set(facts.filter((f): f is string => !!f))];
}

/** The trip's personality in plain words: "Slow pace, food is the point". Unknown scales are named, never guessed. */
function PersonalityCard({ personality }: { personality: Personality }) {
  const { traits, unknown, notes } = personality;
  const tripNotes = notes.filter((n) => n.target === "trip");
  const isMoment = (n: (typeof notes)[number]) => n.once && n.kind !== "avoid";
  const moments = tripNotes.filter(isMoment).map((n) => n.text);
  const sentence = traits.map((t, i) => (i ? t.toLowerCase() : t)).join(", ");
  return (
    <div className="personality" title="This trip's personality">
      <span className="personality-label">Trip personality</span>
      <span>{sentence || "Still getting to know this trip"}</span>
      {traits.length > 0 && unknown.length > 0 && (
        <span className="small muted">Not sure yet: {unknown.join(", ").toLowerCase()}</span>
      )}
      <NoteList notes={tripNotes.filter((n) => !isMoment(n))} target="trip" />
      {moments.length > 0 && <span className="small">To fit in: {moments.join(", ")}</span>}
    </div>
  );
}

export default function TripScreen({ tripId, firstMessage }: { tripId: string; firstMessage?: string }) {
  const [trip, setTrip] = useState<Trip | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [panel, setPanel] = useState<PanelView | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [draftingSince, setDraftingSince] = useState(0);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // A message that failed before the server saved it, so the user can resend it.
  const [retryText, setRetryText] = useState<string | null>(null);
  const streamAbort = useRef<AbortController | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const sentFirst = useRef(false);

  useEffect(() => {
    api.trip(tripId).then(setTrip).catch((e) => setError(e.message));
  }, [tripId]);

  useEffect(() => {
    if (trip && firstMessage && !sentFirst.current) {
      sentFirst.current = true;
      send(firstMessage);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trip?.id]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [trip?.messages.length, live?.steps.length, live?.reply, chatOpen]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(t);
  }, [toast]);

  const openPanel = (view: PanelView) => {
    setPanel(view);
    setPanelOpen(true);
  };

  const send = async (text: string) => {
    if (!text.trim() || busy || !trip) return;
    setInput("");
    setError(null);
    setRetryText(null);
    setBusy(true);
    setLive({ userText: text === SHOW_PLANS ? "Show me some plans" : text, steps: [], reply: null });
    // Once the reply arrives the server has saved the message, so a failure after that isn't resent.
    let saved = false;
    const failed = (detail: string) => {
      setError(detail);
      if (!saved) {
        setRetryText(text);
        if (text !== SHOW_PLANS) setInput(text);
      }
    };
    const abort = new AbortController();
    streamAbort.current = abort;
    try {
      await sendStream(trip.id, text, abort.signal, (ev) => {
        switch (ev.type) {
          case "progress":
            setLive((l) => l && { ...l, steps: [...l.steps, ev.text] });
            break;
          case "reply":
            saved = true;
            setLive((l) => l && { ...l, reply: ev });
            break;
          case "note":
            setLive((l) => l && l.reply && { ...l, reply: { ...l.reply, text: `${l.reply.text} ${ev.text}` } });
            break;
          case "options":
            openPanel({ view: "options", set: ev.options });
            break;
          case "draft":
            setDrafting(ev.index + 1 < DRAFT_COUNT);
            setTrip((t) => t && { ...t, drafts: ev.index === 0 ? [ev.draft] : [...t.drafts, ev.draft] });
            if (ev.index === 0) openPanel({ view: "drafts" });
            break;
          case "done":
            setTrip(ev.trip);
            break;
          case "error":
            failed(ev.detail);
            break;
        }
        // Drafting starts with this progress step; show placeholders right away.
        if (ev.type === "progress" && ev.text.startsWith("Gathering places")) {
          setDrafting(true);
          setDraftingSince(Date.now());
          openPanel({ view: "drafts" });
        }
      });
    } catch (e) {
      // fetch throws a bare TypeError when the network or server is down.
      if (!abort.signal.aborted) failed(e instanceof ApiError ? e.message : "I can't reach Adieu right now — please try again.");
    } finally {
      streamAbort.current = null;
      setBusy(false);
      setDrafting(false);
      setLive(null);
    }
  };

  const act = async (fn: () => Promise<void>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const pickAction = (placeId: string, action: PickAction) =>
    act(async () => {
      if (!trip) return;
      const { trip: updated, options } = await api.pickAction(trip.id, placeId, action);
      setTrip(updated);
      if (options) openPanel({ view: "options", set: options });
      if (action === "add") setToast("Added to your trip");
      if (action === "like" && !options) setToast("Noted — more like this");
    });

  const chooseDraft = (draftId: string) =>
    act(async () => {
      if (!trip) return;
      // The turn is already saved; stop waiting for the remaining drafts.
      streamAbort.current?.abort();
      setTrip(await api.chooseDraft(trip.id, draftId));
      setPanelOpen(false);
    });

  const activity = (day: number, index: number, action: "remove" | "swap") =>
    act(async () => {
      if (!trip) return;
      setBusy(true);
      try {
        const { trip: updated, options } = await api.activityAction(trip.id, day, index, action);
        setTrip(updated);
        if (options) openPanel({ view: "options", set: options });
      } finally {
        setBusy(false);
      }
    });

  const replace = (set: OptionSet, placeId: string) =>
    act(async () => {
      if (!trip || !set.swap_target) return;
      setTrip(await api.replaceActivity(trip.id, set.swap_target.day_number, set.swap_target.index, placeId));
      setPanelOpen(false);
      setToast("Swapped");
    });

  if (!trip) return <div className="center muted">{error ?? "Loading trip…"}</div>;

  const facts = profileFacts(trip.profile);
  const liked = new Set(trip.liked_place_ids);
  const skipped = new Set(trip.skipped_place_ids);
  const question = live?.reply ? live.reply.next_question : busy ? null : trip.next_question;
  const chips = live ? [] : trip.answer_options;
  // What "Last options" reopens: this session's panel, else what the server remembers.
  const plansToShow = drafting || trip.drafts.length > 0 || !!trip.no_plans;
  let lastPanel: PanelView | null = panel?.view === "drafts" && !plansToShow ? null : panel;
  if (!lastPanel && trip.panel) lastPanel = { view: "options", set: trip.panel };
  if (!lastPanel && (trip.drafts.length > 0 || trip.no_plans) && !trip.itinerary) lastPanel = { view: "drafts" };
  const canShowPlans = !!trip.profile.destination && !trip.itinerary && !busy;

  let panelTitle = "Options";
  let panelBody: React.ReactNode = null;
  if (panel?.view === "drafts") {
    panelTitle = "Pick a starting plan";
    panelBody = (
      <div className="draft-list">
        {!drafting && trip.drafts.length === 0 && (
          <div className="no-plans" role="status">
            <strong className="draft-title">No plans yet</strong>
            {/* No reason saved means the connection or server dropped before it could say. */}
            <p>{trip.no_plans?.reason ?? "I couldn't finish your plans. Please try again."}</p>
            <button
              className={trip.no_plans?.retry_now === false ? undefined : "primary"}
              disabled={busy}
              onClick={() => {
                setPanelOpen(false);
                send(SHOW_PLANS);
              }}
            >
              {trip.no_plans?.retry_now === false ? "Check again later" : "Try again"}
            </button>
          </div>
        )}
        {trip.drafts.map((d) => (
          <DraftCard key={d.id} draft={d} onChoose={() => chooseDraft(d.id)} />
        ))}
        {drafting &&
          Array.from({ length: DRAFT_COUNT - trip.drafts.length }, (_, i) => (
            <DraftSkeleton
              key={i}
              since={draftingSince}
              label={`Drafting plan ${trip.drafts.length + i + 1} of ${DRAFT_COUNT} from real places…`}
            />
          ))}
      </div>
    );
  } else if (panel?.view === "options") {
    const set = panel.set;
    panelTitle = set.title;
    panelBody =
      set.kind === "destinations" ? (
        <div className="option-list">
          {set.ideas.map((idea) => (
            <IdeaCard
              key={idea.title}
              idea={idea}
              onChoose={() => {
                setPanelOpen(false);
                send(`Let's go to ${idea.destination}`);
              }}
            />
          ))}
        </div>
      ) : (
        <div className="option-list">
          {set.items
            .filter((p) => !skipped.has(p.place_id))
            .map((p) => (
              <PickCard
                key={p.place_id}
                pick={p}
                liked={liked.has(p.place_id)}
                onAction={(a) => pickAction(p.place_id, a)}
                onUse={set.kind === "swap" ? () => replace(set, p.place_id) : undefined}
              />
            ))}
          {set.items.length === 0 && <p className="muted">Nothing matched. Try asking in a different way.</p>}
        </div>
      );
  }

  return (
    <main className={`trip ${trip.itinerary ? "with-itinerary" : "chat-only"} ${chatOpen ? "chat-open" : ""}`}>
      {trip.itinerary && (
        <section className="main-plan">
          <ItineraryView
            itinerary={trip.itinerary}
            notes={trip.personality.notes}
            busy={busy}
            onSwap={(d, i) => activity(d, i, "swap")}
            onRemove={(d, i) => activity(d, i, "remove")}
          />
        </section>
      )}

      <section className="chat">
        {trip.itinerary && (
          <button className="chat-toggle" onClick={() => setChatOpen((o) => !o)}>
            {chatOpen ? "▾ Hide chat" : "▴ Chat about your plan"}
          </button>
        )}
        {facts.length > 0 && (
          <div className="facts" title="What I know about this trip">
            {facts.map((f) => (
              <span key={f} className="fact">
                {f}
              </span>
            ))}
          </div>
        )}
        <PersonalityCard personality={trip.personality} />
        <div className="log" ref={logRef}>
          {trip.messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              {m.content}
            </div>
          ))}
          {live && <div className="msg user">{live.userText}</div>}
          {live && live.steps.length > 0 && (
            <ul className="steps">
              {live.steps.map((s, i) => (
                <li key={i} className={i === live.steps.length - 1 && busy ? "pulse" : "done"}>
                  {i === live.steps.length - 1 && busy ? "•" : "✓"} {s}
                </li>
              ))}
            </ul>
          )}
          {live?.reply && (
            <div className="msg assistant">
              {live.reply.text}
              {live.reply.next_question && `\n\n${live.reply.next_question}`}
            </div>
          )}
          {error && (
            <div className="msg error">
              {error}
              {retryText && !busy && (
                <button className="retry" onClick={() => send(retryText)}>
                  Try again
                </button>
              )}
            </div>
          )}
        </div>

        {!busy && (chips.length > 0 || canShowPlans) && (
          <div className="chips">
            {chips.map((c) => (
              <button key={c} onClick={() => send(c)}>
                {c}
              </button>
            ))}
            {canShowPlans && (
              <button className="chip-plans" onClick={() => send(SHOW_PLANS)}>
                ✨ Just show me some plans
              </button>
            )}
          </div>
        )}

        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={question ? "Type an answer, or anything else…" : "Tell me more…"}
            disabled={busy}
          />
          <button className="primary" disabled={busy || !input.trim()}>
            Send
          </button>
        </form>
      </section>

      {!panelOpen && lastPanel && (
        <button className="reopen-panel" onClick={() => openPanel(lastPanel)}>
          {lastPanel.view === "drafts" ? "◧ Plans" : "◧ Last options"}
        </button>
      )}
      {toast && <div className="toast">{toast}</div>}

      <OptionsPanel open={panelOpen && !!panel} title={panelTitle} onClose={() => setPanelOpen(false)}>
        {panelBody}
      </OptionsPanel>
    </main>
  );
}
