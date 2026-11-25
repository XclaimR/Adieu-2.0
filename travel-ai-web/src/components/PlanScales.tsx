import type { PlanScales as Reading, ScaleReading } from "../types";

// Mirrors plan_scales.CARD and personality_service.SCALES: [key, label, left pole, right pole, middle].
export const SCALES: [string, string, string, string, string][] = [
  ["pace", "Pace", "Slow pace", "Packed days", "Steady pace"],
  ["experiences", "Experiences", "Hotel time", "Out exploring", "Some of each"],
  ["energy", "Energy", "Chill evenings", "Nightlife", "Some nights out"],
  ["crowds", "Crowds", "Hidden gems", "The icons", "A mix"],
  ["food", "Food", "Food is fuel", "Food is the point", "Good food matters"],
  ["comfort", "Comfort", "Cheap stays", "Splurge on stays", "Mid-range stays"],
];
const STEPS = [1, 2, 3, 4, 5];

/** A step 1-5 in words: 1-2 the left pole, 3 the middle, 4-5 the right. */
export function scaleWord(key: string, at: number | null): string {
  const s = SCALES.find(([k]) => k === key)!;
  return at == null ? "Not sure" : at < 3 ? s[2] : at > 3 ? s[3] : s[4];
}

/** Five notches: apricot is the plan, a cream outline is what the traveler asked. */
function Notches({ r }: { r: ScaleReading }) {
  return (
    <span className="notches" aria-hidden>
      {STEPS.map((i) => (
        <span key={i} className={`notch ${i === r.plan ? "plan" : ""} ${i === r.wish ? "wish" : ""}`} />
      ))}
    </span>
  );
}

/** The six scales. Without a plan only the wishes show; `fresh` keys glow for a moment. */
export function ScaleGrid({ reading, brief, fresh }: { reading: Reading; brief?: boolean; fresh?: Set<string> }) {
  return (
    <span className="fingerprint">
      {SCALES.map(([key, label]) => {
        const r = reading.scales[key] ?? { plan: null, wish: null };
        const word = scaleWord(key, r.plan ?? r.wish);
        const wished = scaleWord(key, r.wish);
        const asked = r.plan != null && r.wish != null &&
          (r.plan === r.wish ? "As you asked" : wished === word ? "Close to what you asked" : `You asked: ${wished.toLowerCase()}`);
        const said = r.plan != null ? `this plan ${word.toLowerCase()} (${r.plan} of 5)${r.wish != null ? `, you asked ${wished.toLowerCase()} (${r.wish} of 5)` : ""}` : word.toLowerCase();
        return (
          <span
            key={key}
            className={`scale ${r.plan ?? r.wish ? "known" : ""} ${fresh?.has(`${key}:${word}`) ? "fresh" : ""}`}
            role="img"
            aria-label={`${label}: ${said}`}
          >
            <span className="scale-text">
              <span>{label}</span>
              <span className="scale-word">{word}</span>
            </span>
            <Notches r={r} />
            {asked && !brief && <span className="scale-wish">{asked}</span>}
          </span>
        );
      })}
    </span>
  );
}

/** The $ to $$$$$ badge for the whole trip, in the same five cells. */
export function Dollars({ cost }: { cost: ScaleReading }) {
  const label = [cost.plan && `this plan ${cost.plan} of 5`, cost.wish && `you asked ${cost.wish}`].filter(Boolean).join(", ");
  return (
    <span className="dollars" role="img" aria-label={`Trip cost: ${label}`}>
      {STEPS.map((i) => (
        <span key={i} className={`${cost.plan && i <= cost.plan ? "plan" : ""} ${i === cost.wish ? "wish" : ""}`} aria-hidden>
          $
        </span>
      ))}
    </span>
  );
}

/** Legend for the two kinds of marker. */
export function Legend() {
  return (
    <span className="legend" aria-hidden>
      <span>
        <i className="notch plan" />
        This plan
      </span>
      <span>
        <i className="notch wish" />
        What you asked
      </span>
    </span>
  );
}
