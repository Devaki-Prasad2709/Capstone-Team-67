import {$, $$, state, titles} from './core.js';
import {cancelRequests} from './api-client.js';

let navigate = () => {};

export function activateView(view, {refresh = true} = {}) {
  cancelRequests();
  state.activeView = view;
  $$('.nav[data-view]').forEach(element => {
    const active = element.dataset.view === view;
    element.classList.toggle('active', active);
    element.setAttribute('aria-current', active ? 'page' : 'false');
    element.tabIndex = active ? 0 : -1;
  });
  $$('.view').forEach(element => element.classList.remove('active'));
  if (view === 'system-control') {
    $('#overview')?.classList.add('active');
    $('#pipeline')?.classList.add('active');
  } else $('#'+CSS.escape(view))?.classList.add('active');
  $('#pageTitle').textContent = titles[view] || view;
  document.title = `DisasterScope — ${titles[view] || view}`;
  if (refresh) navigate(view);
}

export function initRouter(onNavigate) {
  navigate = onNavigate;
  const navigation = document.querySelector('.sidebar nav');
  navigation?.setAttribute('aria-label', 'Primary dashboard navigation');
  navigation?.addEventListener('click', event => {
    const button = event.target.closest('button.nav[data-view]');
    if (button) activateView(button.dataset.view);
  });
  navigation?.addEventListener('keydown', event => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const buttons = $$('.nav[data-view]');
    const current = Math.max(0, buttons.indexOf(document.activeElement));
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
    event.preventDefault();
    buttons[index]?.focus();
  });
}
