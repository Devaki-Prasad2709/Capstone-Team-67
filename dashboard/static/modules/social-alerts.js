import {$, escapeHtml, setHTMLIfChanged, setTextIfChanged, toast, cancelled} from './core.js';
import {api} from './api-client.js';

let refresh = async () => {}, applyOperatorControls = () => {};
const associations = social => Object.fromEntries((social.associations || []).map(item => [item.alert_id, item]));

export function renderPendingAlerts(containerSelector, totalSelector, social, {compact = false} = {}) {
  const rows = social.pending_alerts || [], index = associations(social);
  setTextIfChanged(totalSelector, `${rows.length} pending`);
  setHTMLIfChanged(containerSelector, rows.length ? rows.map(alert => { const report = alert.report || {}, association = index[alert.alert_id] || {}; return `<div class="shared-pending-alert ${compact ? 'compact' : ''}"><div><span class="alert-status pending">Pending review</span><strong>${escapeHtml(alert.alert_id)}</strong><p>${escapeHtml(report.raw_text)}</p><small>GIS ${escapeHtml(association.gis_source_id || 'unresolved')} → graph ${escapeHtml(association.graph_node_id ?? 'unresolved')} · ${escapeHtml(report.resolved_via || 'NLP')} · confidence ${escapeHtml(report.confidence)}</small></div><div class="alert-actions"><button class="primary" data-alert-action="confirm" data-alert-id="${escapeHtml(alert.alert_id)}">Confirm</button><button class="secondary reject" data-alert-action="reject" data-alert-id="${escapeHtml(alert.alert_id)}">Reject</button></div></div>`; }).join('') : '<div class="empty compact-empty">No reports awaiting review.</div>');
  applyOperatorControls();
}

export const renderOpsPending = social => renderPendingAlerts('#opsPendingRows', '#opsPendingTotal', social, {compact: true});
function reviewedAlert(alert, status, association = {}, observation = {}) { const review = alert.responder_confirmation || {}, report = alert.report || {}; return `<div class="audit-row reviewed-alert"><span class="alert-status ${status === 'confirmed' ? 'confirmed' : 'rejected'}">${escapeHtml(status)}</span><strong>${escapeHtml(alert.alert_id)}</strong><p>${escapeHtml(report.raw_text)}</p><dl><dt>Responder</dt><dd>${escapeHtml(review.responder_id || 'Unavailable')}</dd><dt>Reviewed</dt><dd>${escapeHtml(review.reviewed_at || 'Unavailable')}</dd><dt>GIS source</dt><dd>${escapeHtml(observation.gis_source_id || association.gis_source_id || 'Unresolved')}</dd><dt>Graph node</dt><dd>${escapeHtml(observation.graph_node_id ?? association.graph_node_id ?? 'Unresolved')}</dd></dl></div>`; }

export async function loadSocialAlerts() {
  try {
    const scenarioOnly = $('#socialScope').value === 'scenario', data = await api(`/api/social/alerts?scenario_only=${scenarioOnly}`, {requestKey: 'view:social-alerts'}), counts = data.counts || {};
    [['#alertIncoming', counts.incoming || 0], ['#alertPending', counts.pending || 0], ['#alertConfirmed', counts.confirmed || 0], ['#alertRejected', counts.rejected || 0]].forEach(([selector, value]) => setTextIfChanged(selector, value));
    renderPendingAlerts('#pendingAlertRows', '#pendingAlertTotal', data);
    const index = associations(data), observations = Object.fromEntries((data.graph_observations || []).map(item => [item.alert_id, item]));
    const reviewed = [...(data.confirmed_alerts || []).map(alert => [alert, 'confirmed']), ...(data.reported_false_alerts || []).map(alert => [alert, 'rejected'])];
    setHTMLIfChanged('#reviewedAlertRows', reviewed.length ? reviewed.map(([alert, status]) => reviewedAlert(alert, status, index[alert.alert_id], observations[alert.alert_id])).join('') : '<div class="empty compact-empty">No reviewed reports in this scope.</div>');
    const duplicates = (data.ingest_outcomes || []).filter(item => item.outcome === 'duplicate'), errors = data.ingest_errors || [];
    setTextIfChanged('#duplicateAlertTotal', `${duplicates.length} duplicates`); setTextIfChanged('#contradictionAlertTotal', `${errors.length} errors`);
    setHTMLIfChanged('#duplicateAlertRows', duplicates.length ? duplicates.map(item => `<div class="audit-row"><span class="alert-status duplicate">Duplicate</span><strong>${escapeHtml(item.alert_id)}</strong><p>Canonical alert: ${escapeHtml(item.canonical_alert_id)}</p><small>Status ${escapeHtml(item.status)}</small></div>`).join('') : '<div class="empty compact-empty">No duplicate reports.</div>');
    setHTMLIfChanged('#contradictionAlertRows', errors.length ? errors.map(item => `<div class="audit-row"><span class="alert-status rejected">Contradiction / error</span><strong>${escapeHtml(item.id || 'Unknown report')}</strong><p>${escapeHtml(item.error)}</p><small>Kafka partition ${escapeHtml(item.kafka_partition)} · offset ${escapeHtml(item.kafka_offset)}</small></div>`).join('') : '<div class="empty compact-empty">No contradictory or invalid reports.</div>');
    const alert = $('#socialAlertError'); if (data.kafka_error) { alert.textContent = data.kafka_error; alert.classList.remove('hidden'); } else alert.classList.add('hidden');
  } catch (error) { if (!cancelled(error)) { const alert = $('#socialAlertError'); alert.textContent = `Social alerts could not refresh: ${error.message} · Last successful state remains visible.`; alert.classList.remove('hidden'); } throw error; }
}

async function reviewAlert(alertId, action) { const responderId = $('#responderIdentity').value.trim(); if (!responderId) { toast('Enter a responder identity before reviewing alerts.', false); return; } try { const result = await api(`/api/social/alerts/${encodeURIComponent(alertId)}/${action}`, {method: 'POST', body: JSON.stringify({responder_id: responderId}), requestKey: `action:social:${alertId}`}); toast(result.message, true); await refresh(); } catch (error) { if (!cancelled(error)) toast(error.message, false); } }
export function initSocialAlerts(options = {}) { refresh = options.refresh || refresh; applyOperatorControls = options.applyOperatorControls || applyOperatorControls; document.addEventListener('click', event => { const button = event.target.closest('[data-alert-action][data-alert-id]'); if (button) reviewAlert(button.dataset.alertId, button.dataset.alertAction); }); $('#socialScope')?.addEventListener('change', () => refresh()); }
