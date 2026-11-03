import {state} from './core.js';

export function cancelRequests({includeGlobal = true} = {}) {
  for (const [key, request] of state.requests.entries()) {
    if (!includeGlobal && String(key).startsWith('global:')) continue;
    request.controller.abort('cancelled');
    state.requests.delete(key);
  }
}

export async function api(url, options = {}) {
  const {timeoutMs = 12000, requestKey = url, ...fetchOptions} = options;
  state.requests.get(requestKey)?.controller.abort('superseded');
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort('timeout'), timeoutMs);
  state.requests.set(requestKey, {controller, url});
  const method = String(fetchOptions.method || 'GET').toUpperCase();
  const headers = {'Content-Type': 'application/json', ...(fetchOptions.headers || {})};
  if (state.operatorToken) headers.Authorization = `Bearer ${state.operatorToken}`;
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers['X-Dashboard-Request'] = 'DisasterScope-Operator';
  try {
    const response = await fetch(url, {...fetchOptions, headers, signal: controller.signal});
    if (!response.ok) throw new Error(await response.text());
    return await response.json();
  } catch (error) {
    if (controller.signal.aborted && controller.signal.reason === 'timeout') throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)} seconds: ${url}`);
    throw error;
  } finally {
    window.clearTimeout(timeout);
    if (state.requests.get(requestKey)?.controller === controller) state.requests.delete(requestKey);
  }
}
