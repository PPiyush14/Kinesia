export interface User {
  id: number | string;
  username: string;
  display_name: string;
  email?: string;
  avatar_color?: string;
  default_input_lang?: string;
  default_output_lang?: string;
}

export interface AuthResponse {
  token: string;
  user: User;
}

export interface Room {
  id?: number | string;
  room_code: string;
  title: string;
  status?: string;
  is_active?: boolean;
  passcode_required?: boolean;
  has_passcode?: boolean;
  record_session?: boolean;
  host_id?: string;
  created_at?: string | number;
  ended_at?: string | number;
  updated_at?: string;
  participant_count?: number;
  owner_name?: string;
}

export interface Participant {
  user_id: number | string;
  user_name: string;
  avatar_color?: string;
  speaking_lang?: string;
  listening_lang?: string;
}

export interface TranscriptTurn {
  id?: string | number;
  speaker_id?: number | string;
  speaker_name: string;
  source_lang: string;
  listening_lang?: string;
  original_text: string;
  localized_text: string;
  audio_data_url?: string;
  timestamp?: number;
  entities?: Record<string, string[]>;
}

export interface SessionInsights {
  executive_summary: string;
  action_items: string[];
  key_points: string[];
}

export type ViewState = 'auth' | 'welcome' | 'workspace' | 'history' | 'profile' | 'lobby' | 'dialogue' | 'insights';

export interface LanguageOption {
  code: string;
  name: string;
  short: string;
}

export const SUPPORTED_LANGUAGES: LanguageOption[] = [
  { code: 'mr', name: 'Marathi', short: 'MR' },
  { code: 'en', name: 'English', short: 'EN' },
  { code: 'hi', name: 'Hindi', short: 'HI' },
  { code: 'te', name: 'Telugu', short: 'TE' },
  { code: 'bn', name: 'Bengali', short: 'BN' },
  { code: 'ta', name: 'Tamil', short: 'TA' },
  { code: 'es', name: 'Spanish', short: 'ES' },
  { code: 'fr', name: 'French', short: 'FR' },
  { code: 'de', name: 'German', short: 'DE' },
];

export function langLabel(code: string): string {
  return SUPPORTED_LANGUAGES.find((l) => l.code === code)?.name ?? code.toUpperCase();
}

export function langShort(code: string): string {
  return SUPPORTED_LANGUAGES.find((l) => l.code === code)?.short ?? code.toUpperCase();
}
