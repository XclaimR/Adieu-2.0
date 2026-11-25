// Brand lockup. The wordmark is inline so it follows currentColor (dark mode, hover); masters live in docs/brand.
export function Logo() {
  return (
    <span className="lockup">
      <img src="/adieu-symbol.svg" alt="" />
      <svg viewBox="0 0 205 76" role="img" aria-label="Adieu" fill="none" stroke="currentColor" strokeWidth="9" strokeLinecap="round">
        <circle cx="20" cy="50" r="15.5" />
        <path d="M35.5 36v30" />
        <circle cx="66" cy="50" r="15.5" />
        <path d="M81.5 8v58M100 38v28M113 50h31a15.5 15.5 0 1 0-4.5 11M160 36v14a15.5 15.5 0 0 0 31 0M191 36v30" />
        <circle cx="100" cy="15" r="7" fill="var(--accent)" stroke="none" />
      </svg>
    </span>
  );
}
