import { apiRequest } from './client';

export type TutorVoice = 'Misa' | 'Hiro';
export type ProficiencyLevel = 'A1' | 'A2' | 'B1' | 'B2' | 'C1';
export type LLMProvider = 'claude' | 'gemini' | 'openai' | 'bedrock';
export type SpeechProvider = 'gcloud' | 'openai';
export type CorrectionStyle = 'end_of_turn' | 'end_of_session';
export type ExplanationLanguage = 'en' | 'ja';

export interface User {
  id: number;
  name: string;
  is_admin: boolean;
  level: ProficiencyLevel;
  voice: TutorVoice;
  llm_provider: LLMProvider;
  speech_provider: SpeechProvider;
  correction_style: CorrectionStyle;
  explanation_language: ExplanationLanguage;
  show_hiragana: boolean;
  show_english: boolean;
  created_at: string;
}

export interface UserUpdate {
  name?: string;
  is_admin?: boolean;
  level?: ProficiencyLevel;
  voice?: TutorVoice;
  llm_provider?: LLMProvider;
  speech_provider?: SpeechProvider;
  correction_style?: CorrectionStyle;
  explanation_language?: ExplanationLanguage;
  show_hiragana?: boolean;
  show_english?: boolean;
}

export function listUsers(): Promise<User[]> {
  return apiRequest<User[]>('/api/users', { withProfile: false });
}

export function createUser(name: string): Promise<User> {
  return apiRequest<User>('/api/users', {
    method: 'POST',
    body: { name },
    withProfile: false,
  });
}

export function getUser(id: number): Promise<User> {
  return apiRequest<User>(`/api/users/${id}`, { withProfile: false });
}

export function updateUser(id: number, patch: UserUpdate): Promise<User> {
  return apiRequest<User>(`/api/users/${id}`, {
    method: 'PATCH',
    body: patch,
    withProfile: false,
  });
}

export function deleteUser(id: number): Promise<{ ok: true }> {
  return apiRequest<{ ok: true }>(`/api/users/${id}`, {
    method: 'DELETE',
    withProfile: false,
  });
}
