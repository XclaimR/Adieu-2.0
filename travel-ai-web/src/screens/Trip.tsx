import { useEffect, useRef, useState } from "react";
import { ApiError, api, sendStream } from "../api";
import type { Note, OptionSet, Personality, PickAction, PlanScales as PlanScalesReading, Trip, TripProfile } from "../types";
import PickCard from "../components/PickCard";
import IdeaCard from "../components/IdeaCard";
import DraftCard, { DraftSkeleton } from "../components/DraftCard";
import OptionsPanel from "../components/OptionsPanel";
import ItineraryView from "../components/ItineraryView";
import Credits from "../components/Credits";
import SwipeDeck, { OPEN_DECK, SWIPE_CHIP } from "../components/SwipeDeck";
import { Dollars, Legend, SCALES, ScaleGrid, scaleWord } from "../components/PlanScales";

const DRAFT_COUNT = 3;
const SHOW_PLANS = "__show_plans__";


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

/** A note as a chip: "Scuba diving", "Avoid: rushed", "Fit in: one big night". */
function noteLabel(n: Note) {
  const kind = n.once && n.kind !== "avoid" ? "Fit in: " : n.kind === "avoid" ? "Avoid: " : n.kind === "must" ? "Must: " : "";
  const label = `${kind}${n.text}${n.source === "inferred" ? " (my guess)" : ""}`;
  return label.charAt(0).toUpperCase() + label.slice(1);
}

type CardProps = {
  personality: Personality;
  reading: PlanScalesReading;
  onChange: (note: Note) => void;
  onRemove: (note: Note) => void;
};

/** The trip's personality: six scales of five notches, the chosen plan in apricot next to what the traveler asked
 * (Ticket #83). Underneath, each note about the whole trip is a chip; tapping one offers to change or remove it
 * (Ticket #101). */
function PersonalityCard({ personality, reading, onChange, onRemove }: CardProps) {
  const tripNotes = personality.notes.filter((n) => n.target === "trip");
  const [openId, setOpenId] = useState<string | null>(null);
  const open = tripNotes.find((n) => n.id === openId);
  const fresh = useFresh(
    SCALES.flatMap(([key]) => {
      const r = reading.scales[key];
      return r && (r.plan ?? r.wish) ? [`${key}:${scaleWord(key, r.plan ?? r.wish)}`] : [];
    }),
  );
  const planned = Object.values(reading.scales).some((r) => r.plan != null);
  return (
    <section className="personality" aria-label="Trip personality">
      <span className="personality-head">
        <span className="personality-label">Trip personality</span>
        {planned && <Legend />}
      </span>
      <ScaleGrid reading={reading} fresh={fresh} />
      {tripNotes.length > 0 && (
        <div className="note-chips">
          <span className="small muted">Notes ({tripNotes.length})</span>
          {tripNotes.map((n) => (
            <button
              key={n.id}
              className="note-chip"
              aria-expanded={n.id === openId}
              onClick={() => setOpenId(n.id === openId ? null : n.id)}
            >
              {noteLabel(n)}
            </button>
          ))}
        </div>
      )}
      {open && (
        <div className="note-actions" role="group" aria-label={`Note: ${noteLabel(open)}`}>
          <button onClick={() => onChange(open)}>Change</button>
          <button
            onClick={() => {
              setOpenId(null);
              onRemove(open);
            }}
          >
            Remove
          </button>
        </div>
      )}
    </section>
  );
}

export default function TripScreen({ tripId, firstMessage }: { tripId: string; firstMessage?: string }) {
  const [trip, setTrip] = useState<Trip | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [panel, setPanel] = useState<OptionSet | null>(null);
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
  const [deckOpen, setDeckOpen] = useState(firstMessage === OPEN_DECK);
  const logRef = useRef<HTMLDivElement>(null);
  const plansRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const sentFirst = useRef(false);

  useEffect(() => {
    api.trip(tripId).then(setTrip).catch((e) => setError(e.message));
  }, [tripId]);

  useEffect(() => {
    if (trip && firstMessage && firstMessage !== OPEN_DECK && !sentFirst.current) {
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

  const openPanel = (set: OptionSet) => {
    setPanel(set);
    setPanelOpen(true);
  };
  // Plans live on the board; bring them into view once React has drawn them.
  const showPlans = () => setTimeout(() => plansRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  // Moves and day changes go through chat: write the start of the message and let the traveler finish it.
  const ask = (text: string) => {
    setInput(text);
    setChatOpen(true);
    setTimeout(() => inputRef.current?.focus(), 50);
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
            openPanel(ev.options);
            break;
          case "draft":
            setDrafting(ev.index + 1 < DRAFT_COUNT);
            setTrip((t) => t && { ...t, drafts: ev.index === 0 ? [ev.draft] : [...t.drafts, ev.draft] });
            if (ev.index === 0) showPlans();
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
          // The server replaces the old plans, and the new ones only arrive once reviewed.
          setTrip((t) => t && { ...t, drafts: [] });
          showPlans();
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
      if (options) openPanel(options);
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
        if (options) openPanel(options);
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

  const removeNote = (note: Note) =>
    act(async () => {
      if (!trip) return;
      setTrip(await api.removeNote(trip.id, note.id));
      setToast(`Removed “${note.text}”`);
    });

  const freshFacts = useFresh(trip ? profileFacts(trip.profile) : null);

  if (!trip) return <div className="center muted">{error ?? "Loading trip…"}</div>;


  const facts = profileFacts(trip.profile).filter((f) => f !== trip.profile.destination);
  const liked = new Set(trip.liked_place_ids);
  const skipped = new Set(trip.skipped_place_ids);
  const question = live?.reply ? live.reply.next_question : busy ? null : trip.next_question;
  const chips = live ? [] : trip.answer_options;
  // "Last options" reopens this session's panel, else what the server remembers.
  const lastPanel = panel ?? trip.panel;
  const showPlanList = !trip.itinerary && (drafting || trip.drafts.length > 0 || !!trip.no_plans);
  const canShowPlans = !!trip.profile.destination && !trip.itinerary && !busy && !trip.conflict;
  const title = trip.itinerary?.title ?? trip.profile.destination ?? "Your next trip";
  const scales = trip.scales;
  const lastIsAssistant = !live && trip.messages[trip.messages.length - 1]?.role === "assistant";

  let panelBody: React.ReactNode = null;
  if (panel?.kind === "destinations") {
    panelBody = (
      <div className="option-list">
        {panel.ideas.map((idea) => (
          <IdeaCard
            key={idea.title}
            idea={idea}
            onChoose={() => {
              setPanelOpen(false);
              send(`Let's go to ${idea.destination}`);
            }}
          />
        ))}
        {panel.ideas.length === 0 && <p className="muted">No ideas this time. Try asking in a different way.</p>}
      </div>
    );
  } else if (panel) {
    const set = panel;
    panelBody = (
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
    <main className={`trip ${chatOpen ? "chat-open" : ""}`}>
      <section className="board">
        <header className="trip-head">
          <div className="head-top">
            <div>
              {trip.itinerary && <span className="trip-sub">Your trip to {trip.itinerary.destination}</span>}
              <h1>{title}</h1>
            </div>
            {scales && (scales.cost.plan || scales.cost.wish) && (
              <div className="cost-top">
                <span className="cost-label">Trip cost</span>
                <Dollars cost={scales.cost} />
                {(scales.why || scales.cost.wish) && (
                  <span className="cost-why">
                    {scales.why && <span className="cost-reason">{scales.why}. </span>}
                    {scales.cost.wish && `You asked ${"$".repeat(scales.cost.wish)}.`}
                  </span>
                )}
              </div>
            )}
          </div>
          {scales && (
            <PersonalityCard
              personality={trip.personality}
              reading={scales}
              onChange={(n) => ask(`Change my note “${n.text}” to `)}
              onRemove={removeNote}
            />
          )}
        </header>

        <div className="board-body">
          {(facts.length > 0 || (!panelOpen && lastPanel)) && (
            <div className="facts" title="What I know about this trip">
              {facts.map((f) => (
                <span key={f} className={`fact ${freshFacts.has(f) ? "fresh" : ""}`}>
                  {f}
                </span>
              ))}
              {!panelOpen && lastPanel && (
                <button className="reopen-panel" onClick={() => openPanel(lastPanel)}>
                  Last options
                </button>
              )}
            </div>
          )}

          {trip.itinerary ? (
            <ItineraryView
              itinerary={trip.itinerary}
              notes={trip.personality.notes}
              busy={busy}
              onSwap={(d, i) => activity(d, i, "swap")}
              onRemove={(d, i) => activity(d, i, "remove")}
              onAsk={ask}
            />
          ) : (
            <WeekSlots days={trip.profile.duration_days} />
          )}

          {showPlanList && (
            <section className="plans" ref={plansRef} aria-label="Plans">
              <h2>{drafting && trip.drafts.length === 0 ? "Putting your plans together" : "Pick a starting plan"}</h2>
              <div className="draft-list">
                {!drafting && trip.drafts.length === 0 && (
                  <div className="no-plans" role="status">
                    <strong className="draft-title">No plans yet</strong>
                    {/* No reason saved means the connection or server dropped before it could say. */}
                    <p>{trip.no_plans?.reason ?? "I couldn't finish your plans. Please try again."}</p>
                    <button
                      className={trip.no_plans?.retry_now === false ? undefined : "primary"}
                      disabled={busy}
                      onClick={() => send(SHOW_PLANS)}
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
                      label={`Writing and checking plan ${trip.drafts.length + i + 1} of ${DRAFT_COUNT}…`}
                    />
                  ))}
              </div>
            </section>
          )}

          <Credits />
        </div>
      </section>

      <section className="chat" aria-label="Conversation">
        <button className="chat-toggle" onClick={() => setChatOpen((o) => !o)} aria-expanded={chatOpen}>
          {chatOpen ? "Hide conversation" : "Conversation"}
        </button>
        <div className="log" ref={logRef}>
          {trip.messages.map((m, i) => (
            <div
              key={i}
              className={`msg ${m.role} ${lastIsAssistant && i === trip.messages.length - 1 ? "current" : ""}`}
            >
              {m.content}
            </div>
          ))}
          {!live && trip.conflict && (
            <div className="clash">
              <div className="then">
                <span className="when">You said earlier</span>
                <q>{trip.conflict.earlier}</q>
              </div>
              <div className="vs">
                <span title="doesn't fit with">≠</span>
              </div>
              <div className="now">
                <span className="when">You said now</span>
                <q>{trip.conflict.now}</q>
              </div>
            </div>
          )}
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
            <div className="msg assistant current">
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
            {chips.map((c, i) =>
              trip.conflict ? (
                <button key={c} className={`chip-choice ${i === 0 ? "keep" : ""}`} onClick={() => send(c)}>
                  <b>{c}</b>
                  <span>{i === 0 ? "Nothing changes" : `Forgets “${trip.conflict.earlier}”`}</span>
                </button>
              ) : (
                <button key={c} onClick={() => (c === SWIPE_CHIP ? setDeckOpen(true) : send(c))}>
                  {c}
                </button>
              ),
            )}
            {canShowPlans && (
              <button className="chip-plans" onClick={() => send(SHOW_PLANS)}>
                {trip.drafts.length > 0 ? "New plans" : "Show me plans now"}
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
            ref={inputRef}
            aria-label="Message"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={question ? "Or say it your way…" : "Tell me what to change…"}
            disabled={busy}
          />
          <button className="primary" disabled={busy || !input.trim()}>
            Send
          </button>
        </form>
      </section>

      {toast && <div className="toast">{toast}</div>}
      {deckOpen && (
        <SwipeDeck
          tripId={trip.id}
          onDone={(t) => {
            setDeckOpen(false);
            if (t) {
              setTrip(t);
              send(SHOW_PLANS); // the summary is said; carry on as usual: destination ideas or plans
            }
          }}
        />
      )}

      <OptionsPanel open={panelOpen && !!panel} title={panel?.title ?? "Options"} onClose={() => setPanelOpen(false)}>
        {panelBody}
        <Credits />
      </OptionsPanel>
    </main>
  );
}

/** The trip's days before there is a plan: empty slots that a chosen plan fills. */
function WeekSlots({ days }: { days: number | null }) {
  const n = Math.min(days ?? 0, 14);
  return (
    <section className="week" aria-label="Your days">
      <h2>{n ? `Your ${n} days` : "Your days"}</h2>
      {n > 0 && (
        <ol className="slots" aria-hidden>
          {Array.from({ length: n }, (_, i) => (
            <li key={i}>{i + 1}</li>
          ))}
        </ol>
      )}
      <p className="small muted">
        {n ? "They fill in when you choose a plan." : "Tell me how long you're going and your days appear here."}
      </p>
    </section>
  );
}

/** Keys that appeared since the last change, for a few seconds, so a new answer visibly lands.
 * `null` means not loaded yet: what is there on first load is not new. */
function useFresh(keys: string[] | null): Set<string> {
  const seen = useRef<Set<string> | null>(null);
  const timer = useRef<number>();
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const signature = keys?.join("|") ?? null;
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    if (!keys) return;
    const added = seen.current ? keys.filter((k) => !seen.current!.has(k)) : [];
    seen.current = new Set(keys);
    if (!added.length) return;
    setFresh(new Set(added));
    // Not cancelled by a later change with nothing new, or the glow would never go out.
    clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setFresh(new Set()), 3000);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);
  return fresh;
}
