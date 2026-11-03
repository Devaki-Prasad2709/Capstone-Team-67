import {$, $$, setTextIfChanged, state, titles, toast, cancelled} from './modules/core.js';
import {api, cancelRequests} from './modules/api-client.js';
import {activateView, initRouter} from './modules/router.js';
import {initOperationsMap, loadOperational, setMapData} from './modules/operations-map.js?v=20261103-2';
import {initScenarioControls, loadScenarioRegistry} from './modules/scenario-controls.js';
import {initSocialAlerts, loadSocialAlerts} from './modules/social-alerts.js';
import {initSatelliteEvidence, loadSatelliteChange} from './modules/satellite-evidence.js';
import {initBuildingClassifications, loadBuildings} from './modules/building-classifications.js';
import {initDiagnostics, loadAI, loadGlobalHealth, loadLogs, loadObjects, loadSpark, renderStatus} from './modules/diagnostics.js';

function applyOperatorControls() {
  const operator = Boolean(state.access?.operator), selectors = ['#infraStart', '#infraStop', '#activateScenario', '#saveBuildingComponent', '[data-scenario-action]', '[data-system-scenario-action]', '.process-actions button', '.alert-actions button'];
  $$(selectors.join(',')).forEach(control => { if (control.dataset.baseDisabled === undefined) control.dataset.baseDisabled = control.disabled ? 'true' : 'false'; control.disabled = !operator || control.dataset.baseDisabled === 'true'; if (!operator) control.title = 'Authenticated operator access is required.'; });
}
function renderAccess(access) {
  state.access = access || {mode: 'read_only_observer', operator: false, local_development: false};
  const operator = Boolean(state.access.operator), badge = $('#accessMode'); badge.className = `access-mode ${operator ? 'operator' : 'observer'}`;
  badge.textContent = operator ? (state.access.local_development && !state.access.authentication_configured ? 'Local operator' : 'Authenticated operator') : 'Read-only observer';
  $('#operatorLogin').classList.toggle('hidden', operator); $('#operatorLogoutButton').classList.toggle('hidden', !operator || Boolean(state.access.local_development && !state.access.authentication_configured));
  $$('[data-local-only]').forEach(link => link.classList.toggle('hidden', !state.access.minio_console_available)); applyOperatorControls();
}
async function loadHealth() { const [status, access] = await Promise.all([loadGlobalHealth(), api('/api/access', {requestKey: 'global:access', timeoutMs: 5000})]); renderStatus(status); renderAccess(access); }

const viewLoaders = {operations: loadOperational, spark: loadSpark, ai: loadAI, 'satellite-change': loadSatelliteChange, buildings: loadBuildings, 'social-alerts': loadSocialAlerts, media: loadObjects, logs: loadLogs};
function showRefreshState(kind, message) { const badge = $('#refreshState'); badge.className = `refresh-state ${kind}`; badge.textContent = message; }
function scheduleRefresh() { window.clearTimeout(state.refreshTimer); state.refreshTimer = null; if (!state.autoRefresh || document.hidden) return; state.refreshTimer = window.setTimeout(() => requestRefresh({includeHealth: true, reason: 'auto'}), state.refreshIntervalMs); }
export async function requestRefresh({includeHealth = true, reason = 'manual'} = {}) {
  if (document.hidden) { scheduleRefresh(); return; }
  window.clearTimeout(state.refreshTimer); state.refreshTimer = null; const generation = (state.refreshGeneration || 0) + 1; state.refreshGeneration = generation; const view = state.activeView;
  showRefreshState('refreshing', `Refreshing ${titles[view]}`); const work = []; if (includeHealth) { work.push(loadHealth()); if (!$('#scenarioSelector')?.options.length) work.push(loadScenarioRegistry()); } if (viewLoaders[view]) work.push(viewLoaders[view]());
  const results = await Promise.allSettled(work); if (generation !== state.refreshGeneration) return; const failures = results.filter(result => result.status === 'rejected' && !cancelled(result.reason));
  if (failures.length) { const previous = state.lastSuccessfulRefresh[view]; showRefreshState('stale', previous ? 'Stale · refresh failed' : 'Refresh error'); setTextIfChanged('#lastUpdated', previous ? `Last UI refresh ${new Date(previous).toLocaleTimeString()}` : 'No successful UI refresh'); if (reason === 'manual') toast(failures[0].reason.message, false); }
  else if (results.some(result => result.status === 'fulfilled')) { const now = Date.now(); state.lastSuccessfulRefresh[view] = now; showRefreshState('live', 'Current'); setTextIfChanged('#lastUpdated', `UI refreshed ${new Date(now).toLocaleTimeString()}`); }
  scheduleRefresh();
}

async function action(url, body) { try { const result = await api(url, {method: 'POST', body: JSON.stringify(body || {}), requestKey: `action:${url}`, timeoutMs: 30000}); toast(result.message, result.ok); await requestRefresh({includeHealth: true, reason: 'action'}); } catch (error) { if (!cancelled(error)) toast(error.message, false); } }
function processAction(name, verb) { if (verb === 'stop') return action(`/api/process/${name}/stop`); const delays = {social: +$('#socialDelay').value, drone: +$('#droneDelay').value, satellite: +$('#satelliteDelay').value, gis: +$('#gisDelay').value}, producers = ['social', 'drone', 'satellite', 'gis'], workerLimit = $('#workerMaxEvents').value; return producers.includes(name) ? action(`/api/process/${name}/start`, {limit: +$('#eventLimit').value, delay: delays[name]}) : action(`/api/process/${name}/start`, {max_events: workerLimit ? +workerLimit : null}); }
function installPerViewRefreshControls() {
  const hosts = {operations: '#operations', 'system-control': '#overview', diagnostics: '#diagnostics', spark: '#spark', ai: '#ai', 'satellite-change': '#satellite-change', buildings: '#buildings', 'social-alerts': '#social-alerts', media: '#media', logs: '#logs'};
  for (const [view, selector] of Object.entries(hosts)) { const host = $(selector); if (!host || host.querySelector('[data-refresh-view]')) continue; const bar = document.createElement('div'), label = document.createElement('span'), button = document.createElement('button'); bar.className = 'view-refresh-bar'; label.textContent = `Refreshes only ${titles[view]}`; button.className = 'secondary'; button.dataset.refreshView = view; button.textContent = 'Refresh this view'; button.setAttribute('aria-label', `Refresh ${titles[view]}`); bar.append(label, button); host.prepend(bar); }
}
function bindShellEvents() {
  document.addEventListener('click', event => { const refreshButton = event.target.closest('[data-refresh-view]'); if (refreshButton && refreshButton.dataset.refreshView === state.activeView) requestRefresh({includeHealth: true, reason: 'manual'}); const processButton = event.target.closest('[data-process-action][data-process-name]'); if (processButton) processAction(processButton.dataset.processName, processButton.dataset.processAction); });
  $('#infraStart').addEventListener('click', () => action('/api/infrastructure/start')); $('#infraStop').addEventListener('click', () => { if (window.confirm('Stop Kafka, MinIO and supporting infrastructure? Active pipeline processes may fail.')) action('/api/infrastructure/stop'); }); $('#refreshButton').addEventListener('click', () => requestRefresh({includeHealth: true, reason: 'manual'}));
  $('#autoRefreshToggle').addEventListener('change', event => { state.autoRefresh = event.target.checked; showRefreshState(state.autoRefresh ? 'live' : 'idle', state.autoRefresh ? 'Auto-refresh on' : 'Auto-refresh off'); scheduleRefresh(); }); $('#refreshInterval').addEventListener('change', event => { state.refreshIntervalMs = Number(event.target.value); scheduleRefresh(); });
  $('#operatorLoginButton').addEventListener('click', async () => { const token = $('#operatorToken').value.trim(); if (!token) return toast('Enter the operator token.', false); state.operatorToken = token; try { const access = await api('/api/access', {requestKey: 'operator:login'}); if (!access.operator) { state.operatorToken = null; $('#operatorToken').value = ''; renderAccess(access); return toast('Operator token was not accepted.', false); } $('#operatorToken').value = ''; renderAccess(access); toast('Operator controls unlocked for this browser tab.'); } catch { state.operatorToken = null; toast('Operator authentication failed.', false); } });
  $('#operatorToken').addEventListener('keydown', event => { if (event.key === 'Enter') $('#operatorLoginButton').click(); }); $('#operatorLogoutButton').addEventListener('click', () => { state.operatorToken = null; renderAccess({mode: 'read_only_observer', operator: false, authentication_configured: true, local_development: false, minio_console_available: false}); toast('Returned to read-only observer mode.'); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) { window.clearTimeout(state.refreshTimer); state.refreshTimer = null; cancelRequests(); showRefreshState('paused', 'Paused · tab hidden'); } else requestRefresh({includeHealth: true, reason: 'visible'}); });
}
function bootstrap() {
  initRouter(() => requestRefresh({includeHealth: true, reason: 'navigation'})); initOperationsMap(); initScenarioControls({refresh: () => requestRefresh({includeHealth: true, reason: 'scenario-switch'})});
  initSocialAlerts({refresh: () => requestRefresh({includeHealth: false, reason: 'action'}), applyOperatorControls}); initSatelliteEvidence(view => activateView(view)); initBuildingClassifications({setMapData}); initDiagnostics({refresh: () => requestRefresh({includeHealth: false, reason: 'tab'}), applyOperatorControls});
  installPerViewRefreshControls(); bindShellEvents(); if (!['localhost', '127.0.0.1', '::1'].includes(location.hostname)) $$('[data-local-only]').forEach(link => link.classList.add('hidden')); activateView('operations');
}
bootstrap();
