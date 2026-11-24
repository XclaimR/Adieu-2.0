/** The open data's licences ask for these credits. */
export default function Credits() {
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
