import { useEffect, useState } from "react";
import { api, photoUrl } from "../api";
import type { PhotoCredit } from "../types";

type Props = { name: string | null; fallback: string; withCredit?: boolean };

/** An openly licensed photo, or the placeholder when there is none (or it was saved from Google and is gone). */
export default function Photo({ name, fallback, withCredit = false }: Props) {
  const [failed, setFailed] = useState(false);
  const src = failed ? null : photoUrl(name);
  return (
    <>
      {src ? <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} /> : <span>{fallback}</span>}
      {src && withCredit && <Credit name={name} />}
    </>
  );
}

/** "Photo: Ana · CC BY-SA 4.0", linking to the file's Commons page. Licences ask for this credit. */
export function Credit({ name }: { name: string | null }) {
  const [credit, setCredit] = useState<PhotoCredit | null>(null);
  useEffect(() => {
    if (name) api.photoCredit(name).then(setCredit).catch(() => setCredit(null));
  }, [name]);
  if (!credit) return null;
  return (
    <a className="photo-credit" href={credit.source} target="_blank" rel="noreferrer">
      Photo: {credit.by} · {credit.licence}
    </a>
  );
}
