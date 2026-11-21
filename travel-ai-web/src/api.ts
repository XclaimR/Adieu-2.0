import type { PickAction, Trip, TripSummary, TripUpdate, TurnEvent, User, Vibe } from "./types";

const BASE = "/api";
const TOKEN_KEY = "adieu.token";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: stay signed in for this tab only */
  }
  memoryToken = token;
}

let memoryToken = readToken();

export const hasToken = () => !!memoryToken;

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(memoryToken ? { Authorization: `Bearer ${memoryToken}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    if (res.status === 401) setToken(null);
    throw new ApiError(res.status, typeof detail === "string" ? detail : "Request failed");
  }
  return res.json() as Promise<T>;
}

type Login = { token: string; user: User };

export const api = {
  authConfig: () => request<{ google_client_id: string | null; dev_login: boolean }>("GET", "/auth/config"),
  googleLogin: (id_token: string) => request<Login>("POST", "/auth/google", { id_token }),
  devLogin: (email: string, name: string) => request<Login>("POST", "/auth/dev", { email, name }),
  me: () => request<User>("GET", "/me"),
  vibes: () => request<Vibe[]>("GET", "/vibes"),
  setVibes: (vibes: string[]) => request<User>("POST", "/me/taste", { vibes }),
  trips: () => request<TripSummary[]>("GET", "/trips"),
  createTrip: () => request<Trip>("POST", "/trips", {}),
  trip: (id: string) => request<Trip>("GET", `/trips/${id}`),
  chooseDraft: (id: string, draftId: string) => request<Trip>("POST", `/trips/${id}/drafts/${draftId}/choose`),
  pickAction: (id: string, placeId: string, action: PickAction) =>
    request<TripUpdate>("POST", `/trips/${id}/picks/${encodeURIComponent(placeId)}`, { action }),
  activityAction: (id: string, day: number, index: number, action: "remove" | "swap") =>
    request<TripUpdate>("POST", `/trips/${id}/itinerary/days/${day}/activities/${index}`, { action }),
  replaceActivity: (id: string, day: number, index: number, placeId: string) =>
    request<Trip>("POST", `/trips/${id}/itinerary/days/${day}/activities/${index}/replace`, { place_id: placeId }),
};

/** Sends a chat message and calls onEvent for each event the server streams back (NDJSON). */
export async function sendStream(tripId: string, message: string, onEvent: (e: TurnEvent) => void) {
  const res = await fetch(`${BASE}/trips/${tripId}/messages/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(memoryToken ? { Authorization: `Bearer ${memoryToken}` } : {}),
    },
    body: JSON.stringify({ message }),
  });
  if (!res.ok || !res.body) {
    if (res.status === 401) setToken(null);
    throw new ApiError(res.status, res.statusText || "Request failed");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line) as TurnEvent);
    if (done) break;
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as TurnEvent);
}

export const photoUrl = (photoName: string | null) => (photoName ? `${BASE}/photos/${photoName}` : null);
