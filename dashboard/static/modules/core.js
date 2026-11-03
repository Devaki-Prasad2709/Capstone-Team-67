export const $ = selector => document.querySelector(selector);
export const $$ = selector => [...document.querySelectorAll(selector)];

export const state = {
  status: null, sparkSource: 'social', logSource: 'spark', objects: [], operations: null,
  operationsMap: null, mapReady: null, highestMarker: null, buildings: [],
  selectedBuildingId: null, buildingComponentContext: null, aiResults: [], activeView: 'operations',
  autoRefresh: true, refreshIntervalMs: 10000, refreshTimer: null, requests: new Map(),
  lastSuccessfulRefresh: {}, operatorToken: null,
  access: {mode: 'read_only_observer', operator: false, local_development: false},
  renderCache: new WeakMap(), mapDataSignatures: new Map(),
};

export const titles = {
  operations: 'Operational picture', 'system-control': 'System Control', diagnostics: 'Kafka counts',
  spark: 'Spark durable output', ai: 'Drone / YOLO evidence', 'satellite-change': 'Satellite evidence',
  buildings: 'Building evidence', 'social-alerts': 'Social audit', media: 'MinIO browser', logs: 'Diagnostic logs',
};

export function escapeHtml(value) {
  return String(value ?? '—').replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[character]);
}

export function safeUrl(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const parsed = new URL(value, window.location.origin);
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : null;
  } catch { return null; }
}

export function setHTMLIfChanged(target, html) {
  const element = typeof target === 'string' ? $(target) : target;
  if (!element || state.renderCache.get(element) === html) return false;
  element.innerHTML = html;
  state.renderCache.set(element, html);
  return true;
}

export function setTextIfChanged(target, value) {
  const element = typeof target === 'string' ? $(target) : target;
  const text = String(value ?? '');
  if (!element || element.textContent === text) return false;
  element.textContent = text;
  return true;
}

export function toast(message, ok = true) {
  const element = $('#toast');
  setTextIfChanged(element, message);
  element.className = ok ? 'show' : 'show error';
  element.setAttribute('aria-live', ok ? 'polite' : 'assertive');
  window.setTimeout(() => { element.className = ''; }, 4000);
}

export function announce(target, message, assertive = false) {
  const element = typeof target === 'string' ? $(target) : target;
  if (!element) return;
  element.setAttribute('role', assertive ? 'alert' : 'status');
  element.setAttribute('aria-live', assertive ? 'assertive' : 'polite');
  setTextIfChanged(element, message);
}

export function compact(value) { return new Intl.NumberFormat('en', {notation: value > 9999 ? 'compact' : 'standard', maximumFractionDigits: 1}).format(value || 0); }
export function bytes(value) { if (!value) return '0 B'; const units = ['B', 'KB', 'MB', 'GB']; const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 3); return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`; }
export function shortTime(value) { if (!value) return 'No live update'; const date = new Date(value); return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString(); }
export function latestTime(...values) { const valid = values.filter(Boolean).map(value => [new Date(value).valueOf(), value]).filter(([time]) => Number.isFinite(time)); return valid.sort((a, b) => b[0] - a[0])[0]?.[1] || null; }
export function cancelled(error) { return error?.name === 'AbortError'; }
