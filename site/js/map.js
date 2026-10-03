// The map: neighborhood outlines, one circle per hundred-block (sized by reports, colored by the
// most serious one), an optional density view, and unverified chatter pins and police-call dots.
// MapLibre GL comes from a <script> tag in index.html; the basemap is CARTO's free Dark Matter style.

const BASEMAP = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
const EMPTY = { type: 'FeatureCollection', features: [] };

export function createMap(container, { beats, sevColors, chatterColor, onBeat, onPlace, onChatter, onHoverBeat }) {
  const { maplibregl } = window;
  if (!maplibregl) {
    container.textContent = 'The map could not load (it needs an internet connection). Everything else on this page still works.';
    container.classList.add('map-failed');
    return null;
  }
  const map = new maplibregl.Map({
    container,
    style: BASEMAP,
    center: [-117.1289, 32.745],
    zoom: 12.6,
    attributionControl: false,
    cooperativeGestures: true,   // the page scrolls past the map; ctrl/two fingers to zoom it
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');

  let selected = null;
  let hovered = null;
  let popup = null;

  const ready = new Promise((resolve) => {
    map.on('load', () => {
      // Draw under the basemap's labels so street names stay readable on top of the circles.
      const layers = map.getStyle().layers;
      let i = layers.length;
      while (i > 0 && layers[i - 1].type === 'symbol') i--;
      const under = layers[i] ? layers[i].id : undefined;

      map.addSource('beats', { type: 'geojson', data: beats, promoteId: 'beat' });
      map.addLayer({
        id: 'beats-fill', type: 'fill', source: 'beats',
        paint: {
          'fill-color': '#ffffff',
          'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.06, 0],
        },
      }, under);
      map.addLayer({
        id: 'beats-line', type: 'line', source: 'beats',
        paint: {
          'line-color': ['case', ['boolean', ['feature-state', 'selected'], false], '#ffffff', 'rgba(255,255,255,0.28)'],
          'line-width': ['case', ['boolean', ['feature-state', 'selected'], false], 2, 0.75],
        },
      }, under);

      map.addSource('places', { type: 'geojson', data: EMPTY });
      map.addLayer({
        id: 'heat', type: 'heatmap', source: 'places', layout: { visibility: 'none' },
        paint: {
          'heatmap-weight': ['get', 'w'],      // share of the busiest block in view, set by app.js
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 11, 10, 15, 34],
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 11, 1, 15, 2.2],
          'heatmap-opacity': 0.85,
          // one hue, transparent to bright
          'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
            0, 'rgba(28,92,171,0)', 0.15, '#1c5cab', 0.45, '#3987e5', 0.75, '#86b6ef', 1, '#cde2fb'],
        },
      }, under);

      const radius = ['interpolate', ['linear'], ['sqrt', ['get', 'n']], 1, 4.5, 3, 8.5, 8, 17, 14, 24];
      map.addLayer({
        id: 'places', type: 'circle', source: 'places',
        // serious on top; among equals, small on top so nothing hides under a large circle
        layout: { 'circle-sort-key': ['-', ['*', ['-', 2, ['get', 'sev']], 10000], ['get', 'n']] },
        paint: {
          'circle-radius': ['interpolate', ['exponential', 1.6], ['zoom'],
            11, ['*', 0.5, radius], 13.5, radius, 17, ['*', 2, radius]],
          'circle-color': ['match', ['get', 'sev'], 0, sevColors[0], 1, sevColors[1], sevColors[2]],
          'circle-opacity': 0.9,
          'circle-stroke-color': '#0d0d0d',
          'circle-stroke-width': 1,
        },
      }, under);

      // Unverified items: a wide ring for a news or Reddit post, a small solid dot for a police call
      // (there are many more of those, and each sits on the same point as its block's circle).
      const call = ['boolean', ['get', 'dispatch'], false];
      map.addSource('chatter', { type: 'geojson', data: EMPTY });
      map.addLayer({
        id: 'chatter', type: 'circle', source: 'chatter',
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'],
            11, ['case', call, 1.5, 6], 14, ['case', call, 3.5, 13], 17, ['case', call, 7, 26]],
          'circle-color': chatterColor,
          'circle-opacity': ['case', call, 1, 0.2],
          'circle-stroke-color': ['case', call, '#0d0d0d', chatterColor],
          'circle-stroke-width': ['case', call, 1, 1.5],
        },
      }, under);

      const overData = (point) => map.queryRenderedFeatures(point, { layers: ['places', 'chatter'] });
      map.on('click', 'places', (e) => onPlace(e.features[0].properties.p, e.lngLat));
      map.on('click', 'chatter', (e) => {
        if (!map.queryRenderedFeatures(e.point, { layers: ['places'] }).length) onChatter(e.features[0].properties.id, e.lngLat);
      });
      map.on('click', 'beats-fill', (e) => {
        const beat = e.features[0].properties.beat;
        if (beat !== selected && !overData(e.point).length) onBeat(beat);
      });
      const setHover = (beat) => {
        if (hovered !== null) map.setFeatureState({ source: 'beats', id: hovered }, { hover: false });
        hovered = beat;
        if (beat !== null) map.setFeatureState({ source: 'beats', id: beat }, { hover: true });
      };
      map.on('mousemove', 'beats-fill', (e) => {
        const { beat, name } = e.features[0].properties;
        const other = beat !== selected && !overData(e.point).length;
        setHover(other ? beat : null);
        onHoverBeat(other ? name : null);
        map.getCanvas().style.cursor = other || overData(e.point).length ? 'pointer' : '';
      });
      map.on('mouseleave', 'beats-fill', () => { setHover(null); onHoverBeat(null); map.getCanvas().style.cursor = ''; });
      resolve();
    });
  });

  return {
    ready,
    setBeat(beat, bbox, animate = true) {
      ready.then(() => {
        if (selected !== null) map.setFeatureState({ source: 'beats', id: selected }, { selected: false });
        selected = beat;
        map.setFeatureState({ source: 'beats', id: beat }, { selected: true });
        popup?.remove();
        map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 28, duration: animate ? 700 : 0 });
      });
    },
    setPlaces(collection) { ready.then(() => map.getSource('places').setData(collection)); },
    setChatter(collection) { ready.then(() => map.getSource('chatter').setData(collection)); },
    setMode(mode) {
      ready.then(() => {
        map.setLayoutProperty('heat', 'visibility', mode === 'heat' ? 'visible' : 'none');
        map.setLayoutProperty('places', 'visibility', mode === 'heat' ? 'none' : 'visible');
      });
    },
    popup(lngLat, node) {
      popup?.remove();
      popup = new maplibregl.Popup({ maxWidth: '320px', offset: 10 }).setLngLat(lngLat).setDOMContent(node).addTo(map);
    },
    closePopup() { popup?.remove(); },
    flyTo(lngLat) { ready.then(() => map.easeTo({ center: lngLat, zoom: Math.max(map.getZoom(), 15.5), duration: 600 })); },
  };
}
