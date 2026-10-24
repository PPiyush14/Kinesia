import type { AuthResponse, ConversationRecap, Participant, Room, SavedMoment, SessionInsights, TranscriptTurn, User } from '@/types';

const TOKEN_KEY = 'kinesia_auth_token';

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

export const getStoredToken = (): string | null => localStorage.getItem(TOKEN_KEY);
export const setStoredToken = (token: string): void => localStorage.setItem(TOKEN_KEY, token);
export const removeStoredToken = (): void => localStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

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
  const result = await response.json().catch(() => null);

  if (!result || typeof result !== 'object') {
    throw new ApiError('The backend is waking up or temporarily unavailable. Please retry.', response.ok ? 502 : response.status);
  }

  if (!response.ok) {
    const errorMsg =
      (result as { error?: string; message?: string }).error ||
      (result as { message?: string }).message ||
      (typeof (result as { detail?: unknown }).detail === 'string' ? (result as { detail: string }).detail : '') ||
      `Request failed with status ${response.status}`;
    throw new ApiError(errorMsg, response.status);
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
    gender?: 'male' | 'female';
  }): Promise<User> {
    return apiCall<User>('/api/v1/auth/profile', 'PATCH', params, true);
  },

  async deleteAccount(): Promise<{ ok: boolean; message?: string }> {
    return apiCall<{ ok: boolean; message?: string }>('/api/v1/auth/profile', 'DELETE', null, true);
  },
};

export const roomService = {
  async getRecap(code: string, language: string): Promise<ConversationRecap> {
    return apiCall(`/api/v1/rooms/${code}/recap?output_language=${encodeURIComponent(language)}`);
  },
  async generateRecap(code: string, language: string): Promise<ConversationRecap> {
    return apiCall(`/api/v1/rooms/${code}/recap`, 'POST', { language });
  },
  async getMoments(code?: string): Promise<SavedMoment[]> {
    return apiCall(code ? `/api/v1/rooms/${code}/moments` : '/api/v1/moments');
  },
  async saveMoment(code: string, turnId: string): Promise<unknown> {
    return apiCall(`/api/v1/rooms/${code}/moments`, 'POST', { turn_id: turnId });
  },
  async removeMoment(code: string, turnId: string): Promise<unknown> {
    return apiCall(`/api/v1/rooms/${code}/moments/${encodeURIComponent(turnId)}`, 'DELETE');
  },
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
      true
    );
    return res.valid;
  },

  async speak(
    code: string,
    params: {
      text: string;
      source_lang: string;
      confidence?: number;
      utterance_id?: string;
    }
  ): Promise<unknown> {
    return apiCall(`/api/v1/rooms/${code}/speak`, 'POST', params, true);
  },

  async speakAudio(
    code: string,
    params: {
      audio_base64: string;
      source_lang: string;
      mime_type?: string;
      utterance_id?: string;
    }
  ): Promise<unknown> {
    return apiCall(`/api/v1/rooms/${code}/speak-audio`, 'POST', params, true);
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
    return apiCall<SessionInsights>(`/api/v1/rooms/${code}/insights`, 'GET', null, true);
  },

  async getParticipants(code: string): Promise<Participant[]> {
    return apiCall<Participant[]>(`/api/v1/rooms/${code}/participants`, 'GET', null, true);
  },

  async updateLanguages(
    code: string,
    data: { speaking_lang?: string; listening_lang?: string; is_muted?: boolean }
  ): Promise<{ ok: boolean; participants?: Participant[] }> {
    return apiCall<{ ok: boolean; participants?: Participant[] }>(
      `/api/v1/rooms/${code}/languages`,
      'POST',
      data,
      true
    );
  },

  async leaveRoom(code: string): Promise<{ ok: boolean }> {
    return apiCall<{ ok: boolean }>(`/api/v1/rooms/${code}/leave`, 'POST', {}, true);
  },

  async deleteRoom(code: string): Promise<{ ok: boolean }> {
    return apiCall<{ ok: boolean }>(`/api/v1/rooms/${code}`, 'DELETE', null, true);
  },

  async clearAllRooms(): Promise<{ ok: boolean }> {
    return apiCall<{ ok: boolean }>('/api/v1/rooms/clear-all', 'DELETE', null, true);
  },
};
