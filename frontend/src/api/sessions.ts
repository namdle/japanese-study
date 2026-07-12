import { apiRequest, ApiError, getStoredProfileId } from './client';

export type SessionMode = 'freeform' | 'three_phase';
export type TurnRole = 'user' | 'assistant';

export interface Turn {
  id: number;
  role: TurnRole;
  text: string;
  audio_url: string | null;
  hiragana?: string | null;
  english?: string | null;
  created_at: string;
}

export interface SessionMeta {
  id: number;
  user_id: number;
  lesson_id: number | null;
  lesson_plan_id: number | null;
  mode: SessionMode;
  tutor_voice: string;
  llm_provider: string;
  speech_provider: string;
  started_at: string;
  ended_at: string | null;
  summary: string | null;
  seed_image_url?: string | null;
}

export interface LessonInfo {
  id: number;
  title_en: string;
  title_ja: string;
  level: string;
  can_dos: string[];
  topic_title_en: string;
  topic_title_ja: string;
}

export interface LessonOption extends LessonInfo {
  practiced_count: number;
  last_practiced_at: string | null;
}

export interface SessionDetail {
  session: SessionMeta;
  lesson: LessonInfo | null;
  turns: Turn[];
}

export interface ActiveSession {
  active: SessionDetail | null;
  next_lesson: LessonInfo | null;
}

export function getActiveSession(): Promise<ActiveSession> {
  return apiRequest<ActiveSession>('/api/sessions/active');
}

export function listLessonOptions(): Promise<LessonOption[]> {
  return apiRequest<LessonOption[]>('/api/sessions/lessons');
}

export interface LessonStudy {
  lesson_id: number;
  study_markdown: string;
}

export function getLessonStudy(lessonId: number): Promise<LessonStudy> {
  return apiRequest<LessonStudy>(`/api/sessions/lessons/${lessonId}/study`);
}

export function startSession(opts?: {
  lesson_id?: number;
  mode?: SessionMode;
}): Promise<SessionDetail> {
  return apiRequest<SessionDetail>('/api/sessions/start', {
    method: 'POST',
    body: opts ?? {},
  });
}

export async function startSessionFromImage(
  image: File,
  mode: SessionMode = 'freeform',
): Promise<SessionDetail> {
  const form = new FormData();
  form.append('image', image, image.name);
  form.append('mode', mode);

  const headers: Record<string, string> = {};
  const profileId = getStoredProfileId();
  if (profileId !== null) headers['X-User-Id'] = String(profileId);

  const res = await fetch('/api/sessions/start-from-image', {
    method: 'POST',
    body: form,
    headers,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errBody = (await res.json()) as { detail?: unknown };
      if (typeof errBody.detail === 'string') detail = errBody.detail;
    } catch {
      // ignore
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as SessionDetail;
}

export function postTextTurn(sessionId: number, content: string): Promise<SessionDetail> {
  return apiRequest<SessionDetail>(`/api/sessions/${sessionId}/turn`, {
    method: 'POST',
    body: { content },
  });
}

export async function postVoiceTurn(sessionId: number, audio: Blob): Promise<SessionDetail> {
  const form = new FormData();
  const filename = audio.type.includes('webm') ? 'recording.webm' : 'recording.audio';
  form.append('audio', audio, filename);

  const headers: Record<string, string> = {};
  const profileId = getStoredProfileId();
  if (profileId !== null) headers['X-User-Id'] = String(profileId);

  const res = await fetch(`/api/sessions/${sessionId}/turn-audio`, {
    method: 'POST',
    body: form,
    headers,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errBody = (await res.json()) as { detail?: unknown };
      if (typeof errBody.detail === 'string') detail = errBody.detail;
    } catch {
      // ignore
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as SessionDetail;
}

export function endSession(sessionId: number): Promise<SessionMeta> {
  return apiRequest<SessionMeta>(`/api/sessions/${sessionId}/end`, { method: 'POST' });
}
