import {$, $$, escapeHtml, setHTMLIfChanged, setTextIfChanged, shortTime, toast} from './core.js';
import {api} from './api-client.js';

export function renderTimeline(status = {}) {
  const complete = new Set((status.completed_events || []).map(item => item.event_id));
  const current = status.current_event?.event_id;
  const all = [...(status.completed_events || []), ...(status.upcoming_events || [])];
  if (status.current_event && !all.some(item => item.event_id === current)) all.push(status.current_event);
  all.sort((a, b) => a.sequence - b.sequence);
  setHTMLIfChanged('#timelineTrack', all.map(item => `<span title="${escapeHtml(item.name)}" aria-label="${escapeHtml(item.name)}" class="timeline-event ${complete.has(item.event_id) ? 'complete' : ''} ${current === item.event_id && status.state === 'running' ? 'current' : ''}"></span>`).join(''));
  setTextIfChanged('#scenarioClock', `${String(status.state).toUpperCase()} · ${shortTime(status.simulation_timestamp)}`);
  setTextIfChanged('#scenarioCurrentEvent', status.current_event?.name?.replaceAll('_', ' ') || 'Not started');
  setTextIfChanged('#scenarioProgress', `${status.completed_events?.length || 0} of ${(status.completed_events?.length || 0) + (status.upcoming_events?.length || 0)} events · ${status.speed}×`);
  if ($('#scenarioSpeed')) $('#scenarioSpeed').value = String(status.speed);
  if ($('#systemScenarioSpeed')) $('#systemScenarioSpeed').value = String(status.speed);
  setTextIfChanged('#systemReplayState', `${String(status.state).toUpperCase()} · ${status.completed_events?.length || 0} completed`);
}

async function controlScenario(action, speedValue) {
  try {
    const speed = Number(speedValue ?? $('#scenarioSpeed').value);
    if (action === 'start') await api('/api/scenario/speed', {method: 'POST', body: JSON.stringify({speed})});
    const result = await api('/api/scenario/' + action, {method: 'POST', body: JSON.stringify(action === 'speed' ? {speed} : {}), requestKey: `action:scenario:${action}`});
    renderTimeline(result.status); toast(result.message, true);
  } catch (error) { toast(error.message, false); }
}

export function initScenarioControls() {
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-scenario-action],[data-system-scenario-action]');
    if (!button) return;
    const action = button.dataset.scenarioAction || button.dataset.systemScenarioAction;
    controlScenario(action, button.dataset.systemScenarioAction ? $('#systemScenarioSpeed').value : undefined);
  });
  $('#scenarioSpeed')?.addEventListener('change', () => controlScenario('speed'));
  $('#systemScenarioSpeed')?.addEventListener('change', () => controlScenario('speed', $('#systemScenarioSpeed').value));
}
