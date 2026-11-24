import { useEffect, useState } from "react";
import { api, hasToken, setToken } from "./api";
import type { User } from "./types";
import Login from "./screens/Login";
import VibePicker from "./screens/VibePicker";
import Home from "./screens/Home";
import TripScreen from "./screens/Trip";

type Screen = { name: "home" } | { name: "vibes" } | { name: "trip"; id: string; firstMessage?: string };

/** The open data's licences ask for these credits. */
function Credits() {
  return (
    <details className="credits small muted">
      <summary>Data credits</summary>
      <p>
        Places and descriptions: <a href="https://en.wikivoyage.org/" target="_blank" rel="noreferrer">Wikivoyage</a>{" "}
        contributors, <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noreferrer">CC BY-SA 4.0</a>.
        Places and map data: <a href="https://overturemaps.org/" target="_blank" rel="noreferrer">Overture Maps Foundation</a>{" "}
        (<a href="https://docs.overturemaps.org/attribution/" target="_blank" rel="noreferrer">CDLA-Permissive-2.0, Apache-2.0
        incl. Foursquare's NOTICE, CC0</a>); ©{" "}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>, ODbL.
        Destinations: <a href="https://www.wikidata.org/" target="_blank" rel="noreferrer">Wikidata</a> (CC0) and{" "}
        <a href="https://www.geonames.org/" target="_blank" rel="noreferrer">GeoNames</a> (CC BY 4.0). Photos: Wikimedia
        Commons, credited on each photo. No ratings or prices: tiers marked "est." are estimates.
      </p>
    </details>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(hasToken());
  const [screen, setScreen] = useState<Screen>({ name: "home" });

  useEffect(() => {
    if (!hasToken()) return;
    api
      .me()
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="center muted">Loading…</div>;

  if (!user) {
    return (
      <Login
        onLogin={(u) => {
          setUser(u);
          setScreen(u.has_taste ? { name: "home" } : { name: "vibes" });
        }}
      />
    );
  }

  const signOut = () => {
    setToken(null);
    setUser(null);
  };

  return (
    <div className="app">
      <header className="topbar">
        <button className="brand" onClick={() => setScreen({ name: "home" })}>
          Adieu
        </button>
        <span className="spacer" />
        <button className="ghost" onClick={() => setScreen({ name: "vibes" })}>
          My vibe
        </button>
        <button className="ghost" onClick={signOut} title={user.email}>
          {user.picture ? <img className="avatar" src={user.picture} alt="" /> : "Sign out"}
        </button>
      </header>

      {screen.name === "vibes" && (
        <VibePicker
          initial={user.taste.vibes}
          onDone={(u) => {
            setUser(u);
            setScreen({ name: "home" });
          }}
          onSkip={() => setScreen({ name: "home" })}
        />
      )}
      {screen.name === "home" && (
        <Home user={user} openTrip={(id, firstMessage) => setScreen({ name: "trip", id, firstMessage })} />
      )}
      {screen.name === "trip" && <TripScreen key={screen.id} tripId={screen.id} firstMessage={screen.firstMessage} />}
      <Credits />
    </div>
  );
}
