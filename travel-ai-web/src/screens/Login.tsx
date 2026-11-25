import { useEffect, useRef, useState } from "react";
import { api, setToken } from "../api";
import type { User } from "../types";
import { Logo } from "../components/Logo";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (opts: { client_id: string; callback: (r: { credential: string }) => void }) => void;
          renderButton: (el: HTMLElement, opts: Record<string, unknown>) => void;
        };
      };
    };
  }
}

export default function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const [config, setConfig] = useState<{ google_client_id: string | null; dev_login: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const buttonRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.authConfig().then(setConfig).catch(() => setError("Can't reach the server."));
  }, []);

  useEffect(() => {
    const clientId = config?.google_client_id;
    if (!clientId || !buttonRef.current) return;

    let tries = 0;
    const timer = setInterval(() => {
      // The Google script loads async; wait for it.
      if (!window.google && tries++ < 50) return;
      clearInterval(timer);
      if (!window.google || !buttonRef.current) return setError("Couldn't load Google sign-in.");
      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: async ({ credential }) => {
          try {
            const { token, user } = await api.googleLogin(credential);
            setToken(token);
            onLogin(user);
          } catch (e) {
            setError((e as Error).message);
          }
        },
      });
      window.google.accounts.id.renderButton(buttonRef.current, { theme: "outline", size: "large", shape: "pill" });
    }, 100);
    return () => clearInterval(timer);
  }, [config, onLogin]);

  const devLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const { token, user } = await api.devLogin(email, email.split("@")[0]);
      setToken(token);
      onLogin(user);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="login">
      <h1 className="logo">
        <Logo />
      </h1>
      <p className="tagline">Trips that feel like they were planned by a friend who knows you.</p>

      {config?.google_client_id && <div ref={buttonRef} className="google-btn" />}

      {config?.dev_login && (
        <form className="dev-login" onSubmit={devLogin}>
          <span className="muted small">Developer sign-in</span>
          <input type="email" required placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          <button className="primary">Continue</button>
        </form>
      )}

      {config && !config.google_client_id && !config.dev_login && (
        <p className="error">Sign-in isn't configured. Set GOOGLE_OAUTH_CLIENT_ID on the server.</p>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
