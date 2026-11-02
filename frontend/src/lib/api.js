function cookie(name) {
  const value = document.cookie.split('; ').find((item) => item.startsWith(`${name}=`));
  return value ? decodeURIComponent(value.split('=').slice(1).join('=')) : '';
}

export async function request(path, options = {}, retried = false) {
  const method = options.method || 'GET';
  const headers = { Accept: 'application/json', ...(options.headers || {}) };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers['X-CSRFToken'] = cookie('csrftoken');
  const response = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    ...options,
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body)
  });
  const payload = await response.json().catch(() => ({}));
  // A rejected CSRF token means the view never ran, so it is safe to fetch a
  // fresh csrftoken cookie from /api/session/ and send the request once more.
  if (response.status === 403 && payload.csrf && !retried) {
    await fetch('/api/session/', { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    return request(path, options, true);
  }
  if (!response.ok) {
    const error = new Error(payload.error || `Request failed (${response.status}).`);
    error.status = response.status;
    error.fields = payload.fields || {};
    throw error;
  }
  return payload;
}

export const api = {
  get: (path) => request(path),
  post: (path, body = {}) => request(path, { method: 'POST', body }),
  patch: (path, body = {}) => request(path, { method: 'PATCH', body }),
  remove: (path) => request(path, { method: 'DELETE' })
};
