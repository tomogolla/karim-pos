function cookie(name) {
  const value = document.cookie.split('; ').find((item) => item.startsWith(`${name}=`));
  return value ? decodeURIComponent(value.split('=').slice(1).join('=')) : '';
}

export async function request(path, options = {}) {
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
