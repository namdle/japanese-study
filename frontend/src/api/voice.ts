import { ApiError, getStoredProfileId } from './client';
import type { ChatMessage } from './chat';

export interface VoiceTurnReply {
  transcript: string;
  reply: string;
  voice: string;
  provider: string;
  audio_url: string;
}

export async function sendVoiceTurn(
  audio: Blob,
  history: ChatMessage[],
): Promise<VoiceTurnReply> {
  const form = new FormData();
  // Filename hints are advisory; backend ignores them but they keep some
  // proxies happy.
  const filename = audio.type.includes('webm') ? 'recording.webm' : 'recording.audio';
  form.append('audio', audio, filename);
  form.append('history', JSON.stringify(history));

  const headers: Record<string, string> = {};
  const profileId = getStoredProfileId();
  if (profileId !== null) headers['X-User-Id'] = String(profileId);

  const res = await fetch('/api/voice/turn', {
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
  return (await res.json()) as VoiceTurnReply;
}
