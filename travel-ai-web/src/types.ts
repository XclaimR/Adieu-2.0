export type Taste = { vibes: string[]; likes: string[]; dislikes: string[] };

export type User = {
  id: string;
  email: string;
  name: string | null;
  picture: string | null;
  taste: Taste;
  has_taste: boolean;
};

export type Vibe = { id: string; label: string; emoji: string; description: string };

export type Category = "stay" | "eat" | "do" | "hidden_gems";

export type Pick = {
  place_id: string;
  name: string;
  category: Category;
  address: string | null;
  rating: number | null;
  rating_count: number | null;
  price_level: string | null;
  tier: "budget" | "mid" | "luxury" | null;
  type_label: string | null;
  summary: string | null;
  photo_name: string | null;
  maps_url: string | null;
  /** The Wikivoyage guide the description comes from. */
  source_url: string | null;
  why: string | null;
  trait: string | null;
};

export type PhotoCredit = { by: string; licence: string; source: string };

export type StarterIdea = { title: string; destination: string; pitch: string; photo_name: string | null };

export type OptionSet = {
  id: string;
  kind: "destinations" | Category | "swap";
  title: string;
  items: Pick[];
  ideas: StarterIdea[];
  swap_target: { day_number: number; index: number } | null;
};

export type Preference = { key: string; value: string; source: string };

export type TripProfile = {
  destination: string | null;
  mood: string[];
  start_date: string | null;
  duration_days: number | null;
  party: string | null;
  party_size: number | null;
  kids: boolean | null;
  budget_level: string | null;
  preferences: Preference[];
};

export type Activity = {
  id: string;
  name: string;
  category: string;
  time_of_day: string | null;
  description: string | null;
  place_id: string | null;
  photo_name: string | null;
  maps_url: string | null;
  source_url: string | null;
  for_note: string | null;
};

export type Itinerary = {
  destination: string;
  title: string | null;
  stay: Pick | null;
  days: { day_number: number; theme: string | null; activities: Activity[] }[];
};

export type Draft = {
  id: string;
  flavor: string;
  title: string;
  summary: string;
  photo_name: string | null;
  trait: string | null;
  unplanned: string[];
  warnings: string[];
  itinerary: Itinerary;
};

/** Why the last try made no drafts. */
export type NoPlans = { reason: string; retry_now: boolean };

export type Scale = { value: number; confidence: number; source: string | null };

/** Six scales with confidence; `traits` are the known ones in plain words, `unknown` the scale labels still open. */
export type Note = {
  id: string;
  /** What it is about: "trip", a day ("d2"), the stay ("s1") or an activity id. */
  target: string;
  kind: "must" | "avoid" | "want";
  text: string;
  source: "stated" | "inferred";
  once: boolean;
};

export type Personality = { scales: Record<string, Scale>; notes: Note[]; traits: string[]; unknown: string[] };

export type Message = { role: "user" | "assistant"; content: string; created_at: string };

export type Trip = {
  id: string;
  title: string;
  profile: TripProfile;
  personality: Personality;
  destination_name: string | null;
  panel: OptionSet | null;
  drafts: Draft[];
  no_plans: NoPlans | null;
  liked_place_ids: string[];
  skipped_place_ids: string[];
  next_question: string | null;
  answer_options: string[];
  itinerary: Itinerary | null;
  messages: Message[];
};

export type TripSummary = {
  id: string;
  title: string;
  destination: string | null;
  duration_days: number | null;
  photo_name: string | null;
  updated_at: string;
};

export type TripUpdate = { trip: Trip; options: OptionSet | null };

export type TurnEvent =
  | { type: "progress"; text: string }
  | { type: "reply"; text: string; next_question: string | null; answer_options: string[] }
  | { type: "note"; text: string }
  | { type: "options"; options: OptionSet }
  | { type: "draft"; index: number; draft: Draft }
  | { type: "done"; trip: Trip }
  | { type: "error"; detail: string };

export type PickAction = "like" | "unlike" | "skip" | "too_pricey" | "add";
