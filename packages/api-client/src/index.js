// @danwa/api-client - API Client for Danwa
// Generated from FastAPI OpenAPI Spec

const BASE_URL = '/api/v1';

async function request(path, options = {}) {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  if (response.status === 204) return null;
  return response.json();
}

export const api = {
  auth: {
    login: (email, password) => request('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
    me: () => request('/auth/me'),
    changePassword: (current, newPassword) => request('/auth/password', {
      method: 'PUT',
      body: JSON.stringify({ current_password: current, new_password: newPassword }),
    }),
  },

  debates: {
    list: (projectId, params = {}) => {
      const query = new URLSearchParams(params);
      if (projectId) query.set('project_id', projectId);
      return request(`/debates?${query}`);
    },
    get: (id) => request(`/debates/${id}`),
    create: (data) => request('/debates', { method: 'POST', body: JSON.stringify(data) }),
    delete: (id) => request(`/debates/${id}`, { method: 'DELETE' }),
    stream: (id) => fetch(`${BASE_URL}/debates/${id}/stream`).then(r => r.body),
  },

  projects: {
    list: () => request('/projects'),
    get: (id) => request(`/projects/${id}`),
    create: (data) => request('/projects', { method: 'POST', body: JSON.stringify(data) }),
    update: (id, data) => request(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id) => request(`/projects/${id}`, { method: 'DELETE' }),
  },

  documents: {
    list: (projectId, params = {}) => {
      const query = new URLSearchParams(params);
      if (projectId) query.set('project_id', projectId);
      return request(`/documents?${query}`);
    },
    get: (id) => request(`/documents/${id}`),
    upload: (projectId, file, onProgress) => {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('project_id', projectId);
      return fetch(`${BASE_URL}/documents`, {
        method: 'POST',
        body: formData,
      }).then(r => r.json());
    },
    delete: (id) => request(`/documents/${id}`, { method: 'DELETE' }),
    search: (projectId, query, params = {}) => {
      const searchParams = new URLSearchParams({ q: query, ...params });
      if (projectId) searchParams.set('project_id', projectId);
      return request(`/documents/search?${searchParams}`);
    },
  },

  modules: {
    manifest: () => request('/modules/manifest'),
    get: (type, id) => request(`/modules/${type}/${id}`),
    create: (type, data) => request(`/modules/${type}`, { method: 'POST', body: JSON.stringify(data) }),
    update: (type, id, data) => request(`/modules/${type}/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (type, id) => request(`/modules/${type}/${id}`, { method: 'DELETE' }),
    schemas: () => request('/modules/schemas'),
  },

  profiles: {
    llm: {
      list: () => request('/profiles/llm'),
      get: (id) => request(`/profiles/llm/${id}`),
      create: (data) => request('/profiles/llm', { method: 'POST', body: JSON.stringify(data) }),
      update: (id, data) => request(`/profiles/llm/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
      delete: (id) => request(`/profiles/llm/${id}`, { method: 'DELETE' }),
    },
    agents: {
      list: (role) => request(`/profiles/agents${role ? `?role=${role}` : ''}`),
      get: (id) => request(`/profiles/agents/${id}`),
      create: (data) => request('/profiles/agents', { method: 'POST', body: JSON.stringify(data) }),
      update: (id, data) => request(`/profiles/agents/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
      delete: (id) => request(`/profiles/agents/${id}`, { method: 'DELETE' }),
    },
    prompts: {
      list: () => request('/profiles/prompts'),
      get: (id) => request(`/profiles/prompts/${id}`),
      preview: (id, role) => request(`/profiles/prompts/${id}/preview?role=${role}`),
      create: (data) => request('/profiles/prompts', { method: 'POST', body: JSON.stringify(data) }),
      delete: (id) => request(`/profiles/prompts/${id}`, { method: 'DELETE' }),
    },
  },

  blueprints: {
    list: () => request('/blueprints'),
    get: (id) => request(`/blueprints/${id}`),
    create: (data) => request('/blueprints', { method: 'POST', body: JSON.stringify(data) }),
    update: (id, data) => request(`/blueprints/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id) => request(`/blueprints/${id}`, { method: 'DELETE' }),
    compile: (id) => request(`/blueprints/${id}/compile`, { method: 'POST' }),
    canvas: {
      get: (id) => request(`/blueprints/${id}/canvas`),
      save: (id, data) => request(`/blueprints/${id}/canvas`, { method: 'PUT', body: JSON.stringify(data) }),
    },
  },

  workflows: {
    list: () => request('/workflows'),
    get: (id) => request(`/workflows/${id}`),
    execute: (id, data) => request(`/workflows/${id}/execute`, { method: 'POST', body: JSON.stringify(data) }),
    stream: (id) => fetch(`${BASE_URL}/workflows/${id}/stream`).then(r => r.body),
  },

  config: {
    getSettings: () => request('/config/settings'),
    updateSettings: (data) => request('/config/settings', { method: 'PUT', body: JSON.stringify(data) }),
    getProjectSettings: (projectId) => request(`/config/settings/project/${projectId}`),
  },

  system: {
    health: () => fetch('/health').then(r => r.json()),
    reloadModules: () => request('/system/reload-modules', { method: 'POST' }),
    logs: (params = {}) => request(`/system/logs?${new URLSearchParams(params)}`),
  },
};