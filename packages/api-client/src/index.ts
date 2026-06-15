// @danwa/api-client - API Client for Danwa
// Combines generated types from OpenAPI spec with manual wrapper functions

// Re-export generated types
export * from './generated/api';

// Manual API wrapper with typed functions
const BASE_URL = '/api/v1';

async function request(path: string, options: RequestInit = {}) {
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
    login: (email: string, password: string) => request('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
    me: () => request('/auth/me'),
    changePassword: (current: string, newPassword: string) => request('/auth/password', {
      method: 'PUT',
      body: JSON.stringify({ current_password: current, new_password: newPassword }),
    }),
  },

  debates: {
    list: (projectId?: string, params: Record<string, string> = {}) => {
      const query = new URLSearchParams(params);
      if (projectId) query.set('project_id', projectId);
      return request(`/debates?${query}`);
    },
    get: (id: string) => request(`/debates/${id}`),
    create: (data: any) => request('/debates', { method: 'POST', body: JSON.stringify(data) }),
    delete: (id: string) => request(`/debates/${id}`, { method: 'DELETE' }),
  },

  projects: {
    list: () => request('/projects'),
    get: (id: string) => request(`/projects/${id}`),
    create: (data: any) => request('/projects', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: string, data: any) => request(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: string) => request(`/projects/${id}`, { method: 'DELETE' }),
  },

  documents: {
    list: (projectId?: string, params: Record<string, string> = {}) => {
      const query = new URLSearchParams(params);
      if (projectId) query.set('project_id', projectId);
      return request(`/documents?${query}`);
    },
    get: (id: string) => request(`/documents/${id}`),
    upload: (projectId: string, file: File, onProgress?: (progress: number) => void) => {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('project_id', projectId);
      return fetch(`${BASE_URL}/documents`, {
        method: 'POST',
        body: formData,
      }).then(r => r.json());
    },
    delete: (id: string) => request(`/documents/${id}`, { method: 'DELETE' }),
    search: (projectId: string, query: string, params: Record<string, string> = {}) => {
      const searchParams = new URLSearchParams({ q: query, ...params });
      if (projectId) searchParams.set('project_id', projectId);
      return request(`/documents/search?${searchParams}`);
    },
  },

  modules: {
    manifest: () => request('/modules/manifest'),
    get: (type: string, id: string) => request(`/modules/${type}/${id}`),
    create: (type: string, data: any) => request(`/modules/${type}`, { method: 'POST', body: JSON.stringify(data) }),
    update: (type: string, id: string, data: any) => request(`/modules/${type}/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (type: string, id: string) => request(`/modules/${type}/${id}`, { method: 'DELETE' }),
    schemas: () => request('/modules/schemas'),
  },

  profiles: {
    llm: {
      list: () => request('/profiles/llm'),
      get: (id: string) => request(`/profiles/llm/${id}`),
      create: (data: any) => request('/profiles/llm', { method: 'POST', body: JSON.stringify(data) }),
      update: (id: string, data: any) => request(`/profiles/llm/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
      delete: (id: string) => request(`/profiles/llm/${id}`, { method: 'DELETE' }),
    },
    agents: {
      list: (role?: string) => request(`/profiles/agents${role ? `?role=${role}` : ''}`),
      get: (id: string) => request(`/profiles/agents/${id}`),
      create: (data: any) => request('/profiles/agents', { method: 'POST', body: JSON.stringify(data) }),
      update: (id: string, data: any) => request(`/profiles/agents/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
      delete: (id: string) => request(`/profiles/agents/${id}`, { method: 'DELETE' }),
    },
    prompts: {
      list: () => request('/profiles/prompts'),
      get: (id: string) => request(`/profiles/prompts/${id}`),
      preview: (id: string, role: string) => request(`/profiles/prompts/${id}/preview?role=${role}`),
      create: (data: any) => request('/profiles/prompts', { method: 'POST', body: JSON.stringify(data) }),
      delete: (id: string) => request(`/profiles/prompts/${id}`, { method: 'DELETE' }),
    },
  },

  blueprints: {
    list: () => request('/blueprints'),
    get: (id: string) => request(`/blueprints/${id}`),
    create: (data: any) => request('/blueprints', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: string, data: any) => request(`/blueprints/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: string) => request(`/blueprints/${id}`, { method: 'DELETE' }),
    compile: (id: string) => request(`/blueprints/${id}/compile`, { method: 'POST' }),
    canvas: {
      get: (id: string) => request(`/blueprints/${id}/canvas`),
      save: (id: string, data: any) => request(`/blueprints/${id}/canvas`, { method: 'PUT', body: JSON.stringify(data) }),
    },
  },

  workflows: {
    list: () => request('/workflows'),
    get: (id: string) => request(`/workflows/${id}`),
    execute: (id: string, data: any) => request(`/workflows/${id}/execute`, { method: 'POST', body: JSON.stringify(data) }),
  },

  config: {
    getSettings: () => request('/config/settings'),
    updateSettings: (data: any) => request('/config/settings', { method: 'PUT', body: JSON.stringify(data) }),
    getProjectSettings: (projectId: string) => request(`/config/settings/project/${projectId}`),
  },

  system: {
    health: () => fetch('/health').then(r => r.json()),
    reloadModules: () => request('/system/reload-modules', { method: 'POST' }),
    logs: (params: Record<string, string> = {}) => request(`/system/logs?${new URLSearchParams(params)}`),
  },
};

export default api;