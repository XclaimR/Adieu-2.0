import Photo from "./Photo";
import type { StarterIdea } from "../types";

export default function IdeaCard({ idea, onChoose }: { idea: StarterIdea; onChoose: () => void }) {
  return (
    <button className="idea" onClick={onChoose}>
      <div className="photo">
        <Photo name={idea.photo_name} fallback="🧭" />
      </div>
      <div className="idea-body">
        <strong>{idea.title}</strong>
        <span className="small muted">{idea.destination}</span>
        <p className="small">{idea.pitch}</p>
      </div>
    </button>
  );
}
