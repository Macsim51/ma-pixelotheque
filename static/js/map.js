/* Only bounded aggregates for the current viewport travel to the browser.
 * The server remains authoritative for all counts, bounds and media access. */
"use strict";

(() => {
  const element = document.getElementById("photo-map");
  const configElement = document.getElementById("map-config");
  if (!element || !configElement || typeof L === "undefined") return;
  const config = JSON.parse(configElement.textContent);
  const status = document.getElementById("map-status");
  const map = L.map(element, {worldCopyJump: true, maxZoom: config.max_zoom || 19}).setView([28, 6], 2);
  if (config.tile_url) {
    L.tileLayer(config.tile_url, {
      attribution: config.tile_attribution,
      maxZoom: config.max_zoom || 19,
      minZoom: 1,
    }).addTo(map);
  }
  const markers = L.layerGroup().addTo(map);
  let controller = null;
  let timer = null;
  let sequence = 0;

  const wrap = longitude => ((longitude + 180) % 360 + 360) % 360 - 180;
  function viewport() {
    const bounds = map.getBounds();
    const coversWorld = bounds.getEast() - bounds.getWest() >= 360;
    return [
      coversWorld ? -180 : wrap(bounds.getWest()),
      Math.max(-90, bounds.getSouth()),
      coversWorld ? 180 : wrap(bounds.getEast()),
      Math.min(90, bounds.getNorth()),
    ];
  }

  function popup(cell) {
    const node = document.createElement("div");
    const count = document.createElement("strong");
    count.textContent = `${cell.count} photo${cell.count > 1 ? "s" : ""}`;
    node.append(count, document.createElement("br"));
    const link = document.createElement("a");
    const destination = new URL(element.dataset.photosUrl, window.location.href);
    destination.searchParams.set("bbox", cell.bbox.join(","));
    link.href = destination.href;
    link.textContent = "Voir les photos";
    node.append(link);
    if (cell.count > 1 && map.getZoom() < (config.max_zoom || 19)) {
      const zoom = document.createElement("button");
      zoom.type = "button";
      zoom.className = "map-zoom-button";
      zoom.textContent = "Rapprocher la carte";
      zoom.addEventListener("click", () => {
        const [west, south, east, north] = cell.bbox;
        map.fitBounds([[south, west], [north, east < west ? east + 360 : east]], {
          maxZoom: Math.min(map.getZoom() + 3, config.max_zoom || 19),
          padding: [24, 24],
        });
      });
      node.append(document.createElement("br"), zoom);
    }
    return node;
  }

  async function refresh() {
    if (controller) controller.abort();
    controller = new AbortController();
    const current = ++sequence;
    if (status) status.textContent = "Recherche des photos dans cette zone…";
    const endpoint = new URL(element.dataset.mapUrl, window.location.href);
    endpoint.searchParams.set("bbox", viewport().join(","));
    endpoint.searchParams.set("zoom", map.getZoom());
    try {
      const response = await fetch(endpoint, {credentials: "same-origin", signal: controller.signal});
      if (!response.ok) throw new Error("map unavailable");
      const data = await response.json();
      if (current !== sequence) return;
      markers.clearLayers();
      for (const cell of data.cells) {
        if (!Number.isFinite(cell.lat) || !Number.isFinite(cell.lng) || !Number.isFinite(cell.count)) continue;
        const badge = document.createElement("span");
        badge.className = "map-cluster-number";
        badge.textContent = cell.count;
        const icon = L.divIcon({className: "map-cluster", html: badge, iconSize: [44, 44], iconAnchor: [22, 22]});
        L.marker([cell.lat, cell.lng], {icon, title: `${cell.count} photos`, keyboard: true})
          .bindPopup(popup(cell))
          .addTo(markers);
      }
      if (status) status.textContent = data.total
        ? `${data.total} photo${data.total > 1 ? "s" : ""} dans cette zone.`
        : "Aucune photo géolocalisée dans cette zone.";
    } catch (error) {
      if (error.name !== "AbortError" && current === sequence && status) {
        status.textContent = "La carte des photos est indisponible. Déplacez la carte pour réessayer.";
      }
    }
  }

  map.on("movestart", () => { if (controller) controller.abort(); });
  map.on("moveend", () => { clearTimeout(timer); timer = setTimeout(refresh, 250); });
  refresh();
})();
