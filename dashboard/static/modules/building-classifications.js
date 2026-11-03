import {$, escapeHtml, setHTMLIfChanged, setTextIfChanged, state, toast, cancelled} from './core.js';
import {api} from './api-client.js';

let setMapData = () => {};

export function syncBuildingInventory(payload = {}) {
  state.buildings = payload.buildings?.features || [];
  setTextIfChanged('#buildingClassifiedTotal', `${payload.classified_count || 0} of ${payload.count || 0} classified`);
  const filter = $('#buildingTypeFilter'), selected = filter?.value || '', types = [...new Set(state.buildings.map(feature => feature.properties?.assigned_type).filter(Boolean))].sort();
  if (filter) { setHTMLIfChanged(filter, '<option value="">All types</option>' + types.map(type => `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`).join('')); if (types.includes(selected)) filter.value = selected; }
  renderBuildingInventory();
}

export function renderBuildingInventory() {
  const sourceQuery = $('#buildingSourceFilter')?.value.trim().toLowerCase() || '', type = $('#buildingTypeFilter')?.value || '', status = $('#buildingStatusFilter')?.value || 'all';
  const rows = state.buildings.filter(feature => { const properties = feature.properties || {}, classified = Boolean(properties.classification); return (!sourceQuery || String(properties.building_source_id).toLowerCase().includes(sourceQuery)) && (!type || properties.assigned_type === type) && (status === 'all' || (status === 'classified' && classified) || (status === 'unclassified' && !classified)); });
  setTextIfChanged('#buildingInventoryTotal', `${rows.length} buildings`);
  setHTMLIfChanged('#buildingInventoryRows', rows.length ? rows.map(feature => { const properties = feature.properties || {}, active = properties.building_source_id === state.selectedBuildingId; return `<button class="building-inventory-row ${active ? 'active' : ''}" data-building-id="${escapeHtml(properties.building_source_id)}"><strong>${escapeHtml(properties.name || properties.building_source_id)}</strong><small>${escapeHtml(properties.building_source_id)} · graph ${escapeHtml(properties.graph_node_id)}</small><span>${escapeHtml(properties.assigned_type || 'Unclassified')}</span></button>`; }).join('') : '<div class="empty compact-empty">No buildings match these filters.</div>');
}

function mount(context) { const component = $('#buildingClassificationComponent'), host = $(context === 'operations' ? '#opsBuildingComponentHost' : '#inventoryBuildingComponentHost'); if (component.parentElement !== host) host.replaceChildren(component); component.classList.remove('hidden'); state.buildingComponentContext = context; }
export async function selectBuildingClassification(sourceId, context = 'inventory') { if (!sourceId) return; state.selectedBuildingId = sourceId; mount(context); renderBuildingInventory(); try { const detail = await api('/api/buildings/' + encodeURIComponent(sourceId), {requestKey: `building:${sourceId}`}); renderBuildingClassification(detail); $('#buildingClassificationComponent').scrollIntoView({behavior: 'smooth', block: 'nearest'}); } catch (error) { toast('Building detail failed: ' + error.message, false); } }
export const selectOperationalBuilding = sourceId => selectBuildingClassification(sourceId, 'operations');

function renderBuildingClassification(detail) {
  const record = detail.classification, history = detail.revision_history || [];
  setTextIfChanged('#buildingComponentTitle', detail.name || detail.building_source_id); setTextIfChanged('#buildingComponentRevision', record ? `Revision ${record.revision_number}` : 'Unclassified');
  $('#buildingComponentCurrent').classList.remove('empty');
  setHTMLIfChanged('#buildingComponentCurrent', `<dl><dt>GIS source ID</dt><dd>${escapeHtml(detail.building_source_id)}</dd><dt>Graph node</dt><dd>${escapeHtml(detail.graph_node_id)}</dd><dt>Assigned type</dt><dd>${escapeHtml(record?.assigned_type || 'Not classified')}</dd><dt>Operator</dt><dd>${escapeHtml(record?.operator || '—')}</dd><dt>Updated</dt><dd>${escapeHtml(record?.timestamp || '—')}</dd><dt>Raw GIS</dt><dd>Frozen and unchanged</dd></dl>`);
  $('#buildingComponentType').value = record?.assigned_type || ''; $('#buildingComponentOperator').value = record?.operator || '';
  setTextIfChanged('#buildingHistoryCount', `${history.length} revision${history.length === 1 ? '' : 's'}`);
  setHTMLIfChanged('#buildingComponentHistory', history.length ? history.slice().reverse().map(item => `<div class="building-revision"><span>r${item.revision_number}</span><div><strong>${escapeHtml(item.assigned_type)}</strong><p>${escapeHtml(item.operator)} · ${escapeHtml(item.timestamp)}</p><small>${escapeHtml(item.notes || 'No notes')}</small></div></div>`).join('') : '<div class="empty compact-empty">No revisions yet.</div>');
}

function applyLocally(sourceId, record) {
  const inventory = state.buildings.find(feature => feature.properties?.building_source_id === sourceId); if (inventory) Object.assign(inventory.properties, {classification: record, assigned_type: record.assigned_type, classification_source: 'manual_revision_store'});
  const structural = state.operations?.layers?.structural_gis; for (const feature of structural?.features || []) if (feature.properties?.building_id === sourceId) Object.assign(feature.properties, {classification: record, assigned_type: record.assigned_type});
  const risk = state.operations?.layers?.tgnn_risk; for (const feature of risk?.features || []) if (feature.properties?.gis_source_id === sourceId) Object.assign(feature.properties, {classification: record, assigned_building_type: record.assigned_type});
  if (structural) setMapData('structural', structural); if (risk) setMapData('risk', risk); renderBuildingInventory();
}

async function save() { const sourceId = state.selectedBuildingId; if (!sourceId) { toast('Select a building first.', false); return; } try { const result = await api(`/api/buildings/${encodeURIComponent(sourceId)}/classification`, {method: 'POST', body: JSON.stringify({assigned_type: $('#buildingComponentType').value, operator: $('#buildingComponentOperator').value, notes: $('#buildingComponentNotes').value || null}), requestKey: `action:building:${sourceId}`}); applyLocally(sourceId, result.classification); renderBuildingClassification(result.building); $('#buildingComponentNotes').value = ''; toast(result.message, true); if (state.activeView === 'buildings') await loadBuildings({preserveComponent: true}); } catch (error) { if (!cancelled(error)) toast(error.message, false); } }
export async function loadBuildings({preserveComponent = false} = {}) { const data = await api('/api/buildings', {requestKey: 'view:buildings'}); syncBuildingInventory(data); if (!preserveComponent && state.selectedBuildingId && state.buildings.some(feature => feature.properties?.building_source_id === state.selectedBuildingId)) await selectBuildingClassification(state.selectedBuildingId, 'inventory'); }

export function initBuildingClassifications(options = {}) {
  setMapData = options.setMapData || setMapData;
  document.addEventListener('click', event => { const row = event.target.closest('[data-building-id]'); if (row) selectBuildingClassification(row.dataset.buildingId, 'inventory'); });
  $('#saveBuildingComponent')?.addEventListener('click', save);
  $('#closeBuildingComponent')?.addEventListener('click', () => { $('#buildingClassificationComponent').classList.add('hidden'); state.buildingComponentContext = null; });
  ['buildingSourceFilter', 'buildingTypeFilter', 'buildingStatusFilter'].forEach(id => $('#'+id)?.addEventListener(id === 'buildingSourceFilter' ? 'input' : 'change', renderBuildingInventory));
}
