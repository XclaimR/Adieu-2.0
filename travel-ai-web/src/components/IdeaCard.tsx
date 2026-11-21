import { photoUrl } from "../api";
import type { StarterIdea } from "../types";

export default function IdeaCard({ idea, onChoose }: { idea: StarterIdea; onChoose: () => void }) {
  const src = photoUrl(idea.photo_name);
  return (
    <button className="idea" onClick={onChoose}>
      <div className="photo">{src ? <img src={src} alt="" loading="lazy" /> : <span>🧭</span>}</div>
      <div className="idea-body">
        <strong>{idea.title}</strong>
        <span className="small muted">{idea.destination}</span>
        <p className="small">{idea.pitch}</p>
      </div>
    </button>
  );
}
