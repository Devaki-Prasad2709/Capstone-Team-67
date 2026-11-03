import {$, escapeHtml, safeUrl, setHTMLIfChanged, setTextIfChanged, shortTime} from './core.js';
import {api} from './api-client.js';

export function adaptSatelliteEvidence(payload) {
  const result = payload?.latest || null;
  if (!result) return {result: null, error: payload?.error || null};
  const summary = result.summary || {}, features = result.change?.features || [], images = result.source_images || {}, metadata = result.change?.metadata || {};
  const cellCount = summary.cell_count ?? features.length;
  const scoredCount = summary.scored_cell_count ?? features.filter(feature => feature.properties?.diff_score != null).length;
  const simulationFields = [...new Set(Object.values(images).flatMap(image => image?.simulation_fields || []))];
  const dataset = images.pre?.source_dataset || images.post?.source_dataset || 'Satellite imagery';
  return {result, error: payload?.error || null, summary, features, images, metadata,
    changedCount: summary.changed_cell_count ?? 0, scoredCount, unknownCount: Math.max(0, cellCount - scoredCount), cellCount,
    timestamp: result.scenario_timestamp || (result.timestamp ? new Date(result.timestamp * 1000).toISOString() : null),
    tile: result.tile_id || 'unknown', labels: result.reference_labels || {},
    provenance: `${dataset}${simulationFields.length ? ` · simulated ${simulationFields.join(', ')}` : ' · real acquisition metadata'}`};
}

export function renderOperationalSatelliteEvidence(payload, setMapData) {
  const evidence = payload?.result !== undefined ? payload : adaptSatelliteEvidence(payload);
  const card = $('#mapSatelliteSummary');
  if (!evidence.result) {
    setMapData?.('satellite-change', {type: 'FeatureCollection', features: []});
    card?.classList.remove('stale');
    card?.removeAttribute('aria-label');
    card?.classList.add('hidden');
    return evidence;
  }
  setMapData?.('satellite-change', evidence.result.change);
  card?.classList.remove('stale');
  card?.removeAttribute('aria-label');
  setTextIfChanged('#mapSatelliteChanged', `${evidence.changedCount} changed cells`);
  setTextIfChanged('#mapSatelliteCounts', `${evidence.scoredCount} scored · ${evidence.unknownCount} unknown · ${evidence.cellCount} total`);
  setTextIfChanged('#mapSatelliteDetail', `Tile ${evidence.tile} · ${shortTime(evidence.timestamp)}`);
  setTextIfChanged('#mapSatelliteProvenance', evidence.provenance);
  card?.classList.remove('hidden');
  return evidence;
}

const safeRelativePath = value => typeof value === 'string' && !/^(?:[A-Za-z]:[\\/]|[\\/]{1,2})/.test(value) ? value : null;
const detailRows = rows => `<dl>${rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join('')}</dl>`;
function sourceCard(phase, image) {
  if (!image) return `<div class="source-provenance-card"><strong>${phase.toUpperCase()}</strong><small>Source unavailable</small></div>`;
  return `<div class="source-provenance-card"><strong>${phase.toUpperCase()} · ${escapeHtml(image.image_id || 'image')}</strong>${detailRows([['Origin', image.input_origin || 'Unavailable'], ['Relative path', safeRelativePath(image.source_relative_path) || image.object_key || 'Path withheld'], ['SHA-256', image.source_sha256 || image.content_hash || 'Unavailable'], ['Transfer', image.transfer_mode || 'Unavailable']])}</div>`;
}
function renderSource(label, image) {
  const element = $(`#satellite${label}Image`), caption = $(`#satellite${label}Caption`);
  const preview = safeUrl(image?.preview_url);
  if (preview) { element.src = preview; element.style.display = 'block'; }
  else { element.removeAttribute('src'); element.style.display = 'none'; }
  caption.textContent = image ? `${label.toUpperCase()} · ${image.image_id} · ${image.scenario_timestamp || new Date(image.timestamp * 1000).toISOString()}` : `${label.toUpperCase()} source unavailable`;
}

export function renderDetailedSatelliteEvidence(payload) {
  const evidence = payload?.result !== undefined ? payload : adaptSatelliteEvidence(payload), alert = $('#satelliteChangeAlert');
  if (evidence.error) { alert.textContent = evidence.error; alert.classList.remove('hidden'); } else alert.classList.add('hidden');
  if (!evidence.result) { $('#satelliteChangeEmpty').classList.remove('hidden'); $('#satelliteChangeContent').classList.add('hidden'); return evidence; }
  $('#satelliteChangeEmpty').classList.add('hidden'); $('#satelliteChangeContent').classList.remove('hidden');
  const {result, summary, labels, images, features, metadata} = evidence;
  const damageCounts = labels.damage_counts || null;
  const damagedLabels = damageCounts ? Number(damageCounts['major-damage'] || 0) + Number(damageCounts.destroyed || 0) : null;
  const referenceValue = damageCounts ? `${damagedLabels}/${labels.feature_count}` : (labels.flooded_feature_count == null ? '—' : `${labels.flooded_feature_count}/${labels.feature_count}`);
  setTextIfChanged('#referenceLabelTitle', damageCounts ? 'Major / destroyed labels' : 'Reference flooding');
  setTextIfChanged('#referenceLabelSource', images.post?.source_dataset || 'Official dataset labels');
  [['#changeCellCount', evidence.changedCount], ['#scoredCellCount', evidence.scoredCount], ['#unknownCellCount', evidence.unknownCount], ['#totalCellCount', `of ${evidence.cellCount} grid cells`], ['#maxDiffScore', summary.max_diff_score == null ? '—' : Number(summary.max_diff_score).toFixed(3)], ['#referenceFlooding', referenceValue], ['#satelliteTimestamp', evidence.timestamp || 'Timestamp unavailable'], ['#satelliteTileTitle', `Satellite tile ${evidence.tile}`]].forEach(([selector, value]) => setTextIfChanged(selector, value));
  renderSource('Pre', images.pre); renderSource('Post', images.post);
  const side = Math.max(1, Math.round(Math.sqrt(features.length))); $('#changeGrid').style.gridTemplateColumns = `repeat(${side},1fr)`;
  setHTMLIfChanged('#changeGrid', features.map(feature => { const p = feature.properties || {}, level = ['low', 'moderate', 'severe'].includes(p.severity) ? p.severity : 'unknown'; return `<div class="change-cell ${level}" title="row ${escapeHtml(p.row)}, col ${escapeHtml(p.col)} · score ${escapeHtml(p.diff_score)} · valid ${Math.round((p.valid_fraction || 0) * 100)}%"><span>${p.diff_score == null ? '—' : Number(p.diff_score).toFixed(2)}</span></div>`; }).join(''));
  const ring = result.footprint?.coordinates?.[0] || [];
  setTextIfChanged('#satelliteFootprint', ring.length >= 3 ? `Footprint: ${ring[0][0].toFixed(6)}, ${ring[0][1].toFixed(6)} to ${ring[2][0].toFixed(6)}, ${ring[2][1].toFixed(6)} · EPSG:4326` : 'Footprint unavailable');
  setHTMLIfChanged('#satelliteSourceProvenance', ['pre', 'post'].map(phase => sourceCard(phase, images[phase])).join(''));
  const labelRows = damageCounts ? Object.entries(damageCounts).map(([name, count]) => [`${name} features`, count]) : [['Flood-labelled features', labels.flooded_feature_count ?? 'Unavailable']];
  setHTMLIfChanged('#satelliteReferenceDetail', detailRows([['Dataset', images.post?.source_dataset || images.pre?.source_dataset || 'Unavailable'], ['Labelled features', labels.feature_count ?? 'Unavailable'], ...labelRows, ['Use', 'Reference evidence only; not a classifier target or TGNN feature']]));
  const selected = (metadata.selected_bands || []).map(band => band.semantic || band.name || band.index).join(', ') || 'Unavailable';
  setHTMLIfChanged('#satelliteNormalization', detailRows([['Adapter', metadata.source || 'Unavailable'], ['Working CRS', metadata.working_crs || 'Unavailable'], ['Output CRS', metadata.crs || 'Unavailable'], ['Normalized size', (metadata.normalized_size || []).join(' × ') || 'Unavailable'], ['Representation', metadata.representation || 'Unavailable'], ['Selected bands', selected], ['Valid coverage', metadata.valid_fraction == null ? 'Unavailable' : `${(Number(metadata.valid_fraction) * 100).toFixed(2)}%`], ['PRE path', metadata.pre_provenance?.metadata?.normalization_path || 'Unavailable'], ['POST path', metadata.post_provenance?.metadata?.normalization_path || 'Unavailable']]));
  return evidence;
}

export async function loadSatelliteChange() { return renderDetailedSatelliteEvidence(await api('/api/satellite/change', {requestKey: 'view:satellite-change'})); }
export function initSatelliteEvidence(activateView) { document.addEventListener('click', event => { if (event.target.closest('[data-satellite-drilldown]')) activateView('satellite-change'); }); }
