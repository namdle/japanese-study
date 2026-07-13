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

// ---------------------------------------------------------------------- //
// Streaming voice turn (SSE over fetch)
// ---------------------------------------------------------------------- //

export interface VoiceTurnStreamHandlers {
  /** The learner's recognized speech, available as soon as STT finishes. */
  onTranscript?: (text: string) => void;
  /** Incremental tutor reply text (sentence-sized chunks, no aid markers). */
  onTextDelta?: (delta: string) => void;
  /** A synthesized audio chunk (one sentence) ready to play, in order. */
  onAudioChunk?: (bytes: Uint8Array, mime: string) => void;
  /** Reading aids, delivered after all audio chunks. */
  onAids?: (hiragana: string | null, english: string | null) => void;
}

interface SseEvent {
  event: string;
  data: unknown;
}

function parseSseBlock(block: string): SseEvent | null {
  let event: string | null = null;
  let data: string | null = null;
  for (const line of block.split('\n')) {
    if (line.startsWith('event: ')) event = line.slice(7);
    else if (line.startsWith('data: ')) data = line.slice(6);
  }
  if (!event || data === null) return null;
  try {
    return { event, data: JSON.parse(data) };
  } catch {
    return null;
  }
}

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/**
 * Voice turn against the streaming endpoint. Resolves with the persisted
 * SessionDetail from the final `done` event. Throws ApiError on HTTP errors
 * (before the stream starts) so callers can fall back to postVoiceTurn.
 */
export async function postVoiceTurnStream(
  sessionId: number,
  audio: Blob,
  handlers: VoiceTurnStreamHandlers,
): Promise<SessionDetail> {
  const form = new FormData();
  const filename = audio.type.includes('webm') ? 'recording.webm' : 'recording.audio';
  form.append('audio', audio, filename);

  const headers: Record<string, string> = {};
  const profileId = getStoredProfileId();
  if (profileId !== null) headers['X-User-Id'] = String(profileId);

  const res = await fetch(`/api/sessions/${sessionId}/turn-audio/stream`, {
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
  if (!res.body) {
    throw new ApiError(0, 'Streaming not supported by this browser.');
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let done: SessionDetail | null = null;
  let streamError: string | null = null;

  const handle = (raw: string) => {
    const parsed = parseSseBlock(raw);
    if (!parsed) return;
    if (parsed.event === 'transcript') {
      handlers.onTranscript?.((parsed.data as { text: string }).text);
    } else if (parsed.event === 'text') {
      handlers.onTextDelta?.((parsed.data as { delta: string }).delta);
    } else if (parsed.event === 'audio') {
      const d = parsed.data as { b64: string; mime: string };
      handlers.onAudioChunk?.(base64ToBytes(d.b64), d.mime);
    } else if (parsed.event === 'aids') {
      const d = parsed.data as { hiragana: string | null; english: string | null };
      handlers.onAids?.(d.hiragana, d.english);
    } else if (parsed.event === 'error') {
      streamError = (parsed.data as { detail?: string }).detail ?? 'Stream error';
    } else if (parsed.event === 'done') {
      done = parsed.data as SessionDetail;
    }
  };

  for (;;) {
    const { value, done: eof } = await reader.read();
    if (eof) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      handle(buffer.slice(0, sep));
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) handle(buffer);

  if (streamError) throw new ApiError(502, streamError);
  if (!done) throw new ApiError(0, 'The tutor stream ended unexpectedly.');
  return done;
}

export function endSession(sessionId: number): Promise<SessionMeta> {
  return apiRequest<SessionMeta>(`/api/sessions/${sessionId}/end`, { method: 'POST' });
}
