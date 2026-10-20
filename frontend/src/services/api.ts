import type { AuthResponse, Room, SessionInsights, TranscriptTurn, User } from '@/types';

const TOKEN_KEY = 'kinesia_auth_token';

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

export const getStoredToken = (): string | null => localStorage.getItem(TOKEN_KEY);
export const setStoredToken = (token: string): void => localStorage.setItem(TOKEN_KEY, token);
export const removeStoredToken = (): void => localStorage.removeItem(TOKEN_KEY);

export async function apiCall<T>(
  endpoint: string,
  method: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE' = 'GET',
  data: unknown = null,
  requireAuth = true
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };

  const token = getStoredToken();
  if (requireAuth && token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const options: RequestInit = {
    method,
    headers,
  };

  if (data !== null && (method === 'POST' || method === 'PATCH' || method === 'PUT')) {
    options.body = JSON.stringify(data);
  }

  const url = endpoint.startsWith('http') ? endpoint : `${API_BASE}${endpoint}`;
  const response = await fetch(url, options);
  const result = await response.json().catch(() => ({}));

  if (!response.ok) {
    const errorMsg =
      (result as { error?: string; message?: string }).error ||
      (result as { message?: string }).message ||
      `Request failed with status ${response.status}`;
    throw new Error(errorMsg);
  }

  return ((result as { data?: T }).data !== undefined
    ? (result as { data: T }).data
    : result) as T;
}

export const authService = {
  async register(params: {
    username: string;
    display_name: string;
    email: string;
    password: string;
    default_input_lang: string;
    default_output_lang: string;
  }): Promise<AuthResponse> {
    return apiCall<AuthResponse>('/api/v1/auth/register', 'POST', params, false);
  },

  async login(login: string, password: string): Promise<AuthResponse> {
    return apiCall<AuthResponse>('/api/v1/auth/login', 'POST', { login, password }, false);
  },

  async getMe(): Promise<User> {
    return apiCall<User>('/api/v1/auth/me', 'GET', null, true);
  },

  async updateProfile(params: {
    display_name?: string;
    avatar_color?: string;
    default_input_lang?: string;
    default_output_lang?: string;
  }): Promise<User> {
    return apiCall<User>('/api/v1/auth/profile', 'PATCH', params, true);
  },
};

export const roomService = {
  async listRooms(): Promise<Room[]> {
    return apiCall<Room[]>('/api/v1/rooms', 'GET', null, true);
  },

  async getRoom(code: string): Promise<Room> {
    return apiCall<Room>(`/api/v1/rooms/${code}`, 'GET', null, false);
  },

  async createRoom(params: {
    title: string;
    passcode?: string | null;
    record_session: boolean;
  }): Promise<Room> {
    return apiCall<Room>('/api/v1/rooms', 'POST', params, true);
  },

  async verifyPasscode(code: string, passcode?: string | null): Promise<boolean> {
    const res = await apiCall<{ valid: boolean }>(
      `/api/v1/rooms/${code}/verify`,
      'POST',
      { passcode },
      false
    );
    return res.valid;
  },

  async speak(
    code: string,
    params: {
      text: string;
      source_lang: string;
      confidence?: number;
    }
  ): Promise<unknown> {
    return apiCall(`/api/v1/rooms/${code}/speak`, 'POST', params, true);
  },

  async endSession(code: string): Promise<SessionInsights> {
    return apiCall<SessionInsights>(`/api/v1/rooms/${code}/end`, 'POST', {}, true);
  },

  async getTranscripts(code: string): Promise<TranscriptTurn[]> {
    return apiCall<TranscriptTurn[]>(`/api/v1/rooms/${code}/transcripts`, 'GET', null, true);
  },

  async clearTranscripts(code: string): Promise<{ ok: boolean }> {
    return apiCall<{ ok: boolean }>(`/api/v1/rooms/${code}/transcripts`, 'DELETE', null, true);
  },

  async getInsights(code: string): Promise<SessionInsights> {
    return apiCall<SessionInsights>(`/api/v1/rooms/${code}/insights`, 'GET', null, false);
  },

  async deleteRoom(code: string): Promise<{ ok: boolean }> {
    return apiCall<{ ok: boolean }>(`/api/v1/rooms/${code}`, 'DELETE', null, true);
  },

  async clearAllRooms(): Promise<{ ok: boolean }> {
    return apiCall<{ ok: boolean }>('/api/v1/rooms/clear-all', 'DELETE', null, true);
  },
};
