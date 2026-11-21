import { useRef, type ReactNode } from "react";

type Props = {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
};

/** Slide-in panel on desktop, bottom sheet on phones (CSS decides). Drag the handle down to close on touch. */
export default function OptionsPanel({ open, title, onClose, children }: Props) {
  const sheetRef = useRef<HTMLElement>(null);
  const startY = useRef<number | null>(null);

  const onTouchStart = (e: React.TouchEvent) => {
    startY.current = e.touches[0].clientY;
  };
  const onTouchMove = (e: React.TouchEvent) => {
    if (startY.current === null || !sheetRef.current) return;
    const dy = Math.max(0, e.touches[0].clientY - startY.current);
    sheetRef.current.style.transform = `translateY(${dy}px)`;
  };
  const onTouchEnd = (e: React.TouchEvent) => {
    if (startY.current === null || !sheetRef.current) return;
    const dy = e.changedTouches[0].clientY - startY.current;
    sheetRef.current.style.transform = "";
    startY.current = null;
    if (dy > 90) onClose();
  };

  return (
    <>
      <div className={`panel-backdrop ${open ? "open" : ""}`} onClick={onClose} aria-hidden />
      <aside ref={sheetRef} className={`options-panel ${open ? "open" : ""}`} aria-hidden={!open} aria-label={title}>
        <div className="panel-grip" onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd}>
          <span />
        </div>
        <header className="panel-head">
          <h2>{title}</h2>
          <button className="ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <div className="panel-body">{children}</div>
      </aside>
    </>
  );
}
