import { useEffect, useState } from "react";
import { api } from "../api";
import type { User, Vibe } from "../types";
import Credits from "../components/Credits";

type Props = { initial: string[]; onDone: (u: User) => void; onSkip: () => void };

export default function VibePicker({ initial, onDone, onSkip }: Props) {
  const [vibes, setVibes] = useState<Vibe[]>([]);
  const [chosen, setChosen] = useState<Set<string>>(new Set(initial));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.vibes().then(setVibes);
  }, []);

  const toggle = (id: string) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const save = async () => {
    setSaving(true);
    try {
      onDone(await api.setVibes([...chosen]));
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="page narrow">
      <h1>What's your travel vibe?</h1>
      <p className="muted">Tap everything that sounds like you. It helps me pick places you'll actually love.</p>
      <div className="vibe-grid">
        {vibes.map((v) => (
          <button
            key={v.id}
            className={`vibe ${chosen.has(v.id) ? "on" : ""}`}
            onClick={() => toggle(v.id)}
            aria-pressed={chosen.has(v.id)}
          >
            <span className="vibe-emoji">{v.emoji}</span>
            <strong>{v.label}</strong>
            <span className="small muted">{v.description}</span>
          </button>
        ))}
      </div>
      <div className="row end">
        <button className="ghost" onClick={onSkip}>
          Skip for now
        </button>
        <button className="primary" disabled={saving || chosen.size === 0} onClick={save}>
          {saving ? "Saving…" : chosen.size ? `Save ${chosen.size} ${chosen.size === 1 ? "vibe" : "vibes"}` : "Save vibes"}
        </button>
      </div>
      <Credits />
    </main>
  );
}
