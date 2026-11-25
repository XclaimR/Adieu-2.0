import { useEffect, useState } from "react";
import { api, hasToken, setToken } from "./api";
import type { User } from "./types";
import Login from "./screens/Login";
import VibePicker from "./screens/VibePicker";
import Home from "./screens/Home";
import TripScreen from "./screens/Trip";
import { Logo } from "./components/Logo";

type Screen = { name: "home" } | { name: "vibes" } | { name: "trip"; id: string; firstMessage?: string };

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
          <Logo />
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
    </div>
  );
}
