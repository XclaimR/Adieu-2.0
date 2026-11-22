import type { Note } from "../types";

/** What the traveler said about one part of the trip. Guesses are marked. */
export default function NoteList({ notes, target }: { notes: Note[]; target: string }) {
  return (
    <>
      {notes
        .filter((n) => n.target === target)
        .map((n) => (
          <span key={n.id} className="small muted">
            {n.kind === "want" ? "Note" : n.kind === "avoid" ? "Avoid" : "Must"}: {n.text}
            {n.source === "inferred" && " (my guess)"}
          </span>
        ))}
    </>
  );
}
