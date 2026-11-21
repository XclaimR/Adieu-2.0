# Adieu product plan

Agreed 2026-11-21. Adieu is a one-stop travel planner that understands the traveler. Free while it's used by friends and family; paid services are fine once it goes to production. Build work is tracked on the [board](https://github.com/users/XclaimR/projects/1).

## The four pillars

### A. "It gets me"
- Every trip has its own **personality**: six sliding scales, each with a confidence level.

  | Scale | From ↔ to |
  |---|---|
  | Pace | slow ↔ packed |
  | Money goes to | the stay ↔ experiences |
  | Energy | chill ↔ nightlife |
  | Crowds | hidden gems ↔ the icons |
  | Food | fuel ↔ the reason for the trip |
  | Comfort | backpacker ↔ luxury |

- Words like "fun" are never mapped to a scale automatically. The app **asks**, adaptively, the question that removes the most uncertainty for this trip, and stops when confident (usually 4–6 questions, at most 8). "Show me plans" is always one tap away, and it keeps learning with questions about the plan itself.
- Questions are mostly concrete scenarios with tap answers, mixed with photo this-or-that. Free text is always accepted, with a "something else…" option.
- Your lasting taste is the low-confidence starting point for each new trip. Only lasting traits feed back from a trip.
- Instagram: personal accounts can't connect (Meta removed that API), and Meta's open embed API returns no captions (tested 2026-11-21). So **screenshots**, read by a **local** model, are the main route; a pasted link is accepted and answered with a request for a screenshot. Adieu keeps what it learned (with where it came from), never the raw screenshots. A "What Adieu knows about me" page allows editing, deleting and "forget everything".
- Group trips: the organizer answers for now. Later, each friend answers from a share link and the plan handles where the group splits.

### B. "Worth the hype?"
A personal verdict on viral and famous places: *worth it / worth it if… / skip*, for this traveler. It covers what and where it is, how to get there, how long it takes, when to go to beat the crowds, what people say (Reddit threads and the web via Gemini search, with sources) and an alternative. It's triggered on demand, then as badges on plan stops, then as a "viral right now" list.

### C. "You'd never have found this"
Local favorites that aren't tourist spots, events unique to the place, festivals, concerts by artists you like, and small workshops, all during your dates, offered as "would you be into this?". Every specific event has a source link and a date; general tips are labelled as tips. Nothing is auto-added.

### D. "The plan just works"
- Multi-city order, nights per city and transport between cities, then stop order and travel times within each day.
- Best time to go: months that suit this personality when dates are open, and a heads-up (weather, crowds, holidays, festivals) when dates are fixed.

## Prices and booking
Pre-filled links to Google Flights, Booking.com, Klook and GetYourGuide for now; Adieu picks the airports, the area to stay and the shortlist. Partner programs with live prices come at production.

## Stack
| Need | Now (free) | Production |
|---|---|---|
| Chat, drafts, search | Gemini free tier (with Google Search) | Gemini paid tier |
| Private material (screenshots, Instagram) | Local model via Ollama | same, or a private hosted model |
| Opinions and events | Gemini search (reaches Reddit threads and blogs through Google), Ticketmaster | paid search, partner feeds |
| Places, photos, routes | Google Places / Routes free allowances | paid usage |
| Hosting | Laptop + Cloudflare Tunnel → Oracle Cloud free tier | own cloud account (e.g. AWS) |

Each data source sits behind one service, so switching to a paid provider is a config change.

## Quality
- Six test travelers (bachelor group to Japan, couple on a slow food week, family beach holiday, solo hiker, budget backpacker, luxury weekend), with pass rules for each pillar.
- An AI traveler agent drives the real app in character, and a judge scores the result.
- Real friends rate each pick and question 👍/👎.

## Build order
0. Foundation: Gemini free tier, hosting, test travelers and the traveler agent
1. A: trip personality, adaptive questions, "What Adieu knows"
2. Instagram links and screenshots
3. D: routes and best time to go
4. B: worth the hype
5. C: events
6. Booking links and group share links
