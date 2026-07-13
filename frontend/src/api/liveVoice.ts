import { ApiError, getStoredProfileId } from './client';
import type { SessionDetail } from './sessions';

/**
 * Live voice turn over a WebSocket (streaming STT with server endpointing).
 *
 * Mic chunks are streamed while the learner is still speaking, so the
 * transcript is ready the moment they stop — no upload+transcribe wait and
 * no client-side silence window. The server signals `endpoint` when Google
 * detects the end of the utterance, then streams the same reply events as
 * the SSE endpoint over the socket.
 *
 * Fallbacks: `unsupported` (speech provider can't stream) rejects with
 * LiveVoiceUnsupportedError; any failure before the transcript was
 * persisted rejects with `turnPersisted === false`, so callers can safely
 * re-send the recorded blob through the classic flow.
 */

export interface LiveVoiceHandlers {
  /** Partial hypothesis while the learner is still speaking (live caption). */
  onInterim?: (text: string) => void;
  /** Server detected end of speech — stop the microphone now. */
  onEndpoint?: () => void;
  /** Final transcript (the user turn is persisted at this point). */
  onTranscript?: (text: string) => void;
  onTextDelta?: (delta: string) => void;
  onAudioChunk?: (bytes: Uint8Array, mime: string) => void;
  onAids?: (hiragana: string | null, english: string | null) => void;
}

export interface LiveVoiceTurn {
  sendChunk: (chunk: Blob) => void;
  /** Client-side stop (manual stop or the VAD backstop). */
  end: () => void;
  abort: () => void;
  /** Resolves with the persisted SessionDetail from the `done` event. */
  result: Promise<SessionDetail>;
}

export class LiveVoiceUnsupportedError extends Error {
  constructor() {
    super('Live voice is not supported for this speech provider.');
    this.name = 'LiveVoiceUnsupportedError';
  }
}

export class LiveVoiceError extends Error {
  /** True when the user turn was already persisted server-side — callers
   * must NOT re-send the recording (it would duplicate the turn). */
  turnPersisted: boolean;

  constructor(message: string, turnPersisted: boolean) {
    super(message);
    this.name = 'LiveVoiceError';
    this.turnPersisted = turnPersisted;
  }
}

export function isLiveVoiceSupported(): boolean {
  // Streaming STT decodes WEBM/Opus; Safari's mp4 recordings can't stream.
  return (
    typeof WebSocket !== 'undefined' &&
    typeof MediaRecorder !== 'undefined' &&
    MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
  );
}

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

export interface LiveVoiceOptions {
  /** When false (learner disabled Auto-stop), the server keeps transcribing
   * across pauses instead of ending the turn at the first silence; the
   * learner stops the mic manually. Defaults to true. */
  autoEndpoint?: boolean;
}

/** Open the socket; resolves once it's ready for audio (start the mic then). */
export function openLiveVoiceTurn(
  sessionId: number,
  handlers: LiveVoiceHandlers,
  options: LiveVoiceOptions = {},
): Promise<LiveVoiceTurn> {
  const profileId = getStoredProfileId();
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const autoEnd = options.autoEndpoint === false ? '0' : '1';
  const url = `${proto}://${window.location.host}/api/sessions/${sessionId}/turn-audio/live?user=${profileId ?? ''}&auto_end=${autoEnd}`;

  return new Promise<LiveVoiceTurn>((resolveOpen, rejectOpen) => {
    const ws = new WebSocket(url);
    let opened = false;
    let settled = false;
    let gotTranscript = false;

    let resolveResult: (d: SessionDetail) => void;
    let rejectResult: (e: Error) => void;
    const result = new Promise<SessionDetail>((res, rej) => {
      resolveResult = res;
      rejectResult = rej;
    });
    // The caller may only consume `result` on the fallback path; avoid
    // unhandled-rejection noise for the abort case.
    result.catch(() => {});

    const settle = (err?: Error, detail?: SessionDetail) => {
      if (settled) return;
      settled = true;
      if (detail) resolveResult(detail);
      else rejectResult(err ?? new LiveVoiceError('Live voice turn failed.', gotTranscript));
      try {
        ws.close();
      } catch {
        // ignore
      }
    };

    ws.onopen = () => {
      opened = true;
      resolveOpen({
        sendChunk: (chunk: Blob) => {
          if (ws.readyState === WebSocket.OPEN) ws.send(chunk);
        },
        end: () => {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'end' }));
        },
        abort: () => settle(new LiveVoiceError('Aborted.', gotTranscript)),
        result,
      });
    };

    ws.onmessage = (msg: MessageEvent<string>) => {
      let parsed: { event: string; data: Record<string, unknown> };
      try {
        parsed = JSON.parse(msg.data) as typeof parsed;
      } catch {
        return;
      }
      const { event, data } = parsed;
      if (event === 'interim') {
        handlers.onInterim?.(data.text as string);
      } else if (event === 'endpoint') {
        handlers.onEndpoint?.();
      } else if (event === 'transcript') {
        gotTranscript = true;
        handlers.onTranscript?.(data.text as string);
      } else if (event === 'text') {
        handlers.onTextDelta?.(data.delta as string);
      } else if (event === 'audio') {
        handlers.onAudioChunk?.(base64ToBytes(data.b64 as string), data.mime as string);
      } else if (event === 'aids') {
        handlers.onAids?.(
          (data.hiragana as string | null) ?? null,
          (data.english as string | null) ?? null,
        );
      } else if (event === 'unsupported') {
        settle(new LiveVoiceUnsupportedError());
      } else if (event === 'error') {
        settle(new LiveVoiceError((data.detail as string) ?? 'Stream error', gotTranscript));
      } else if (event === 'done') {
        settle(undefined, data as unknown as SessionDetail);
      }
    };

    ws.onerror = () => {
      if (!opened) rejectOpen(new ApiError(0, 'Could not open the live voice connection.'));
      settle(new LiveVoiceError('Live voice connection failed.', gotTranscript));
    };

    ws.onclose = () => {
      if (!opened) rejectOpen(new ApiError(0, 'Could not open the live voice connection.'));
      settle(new LiveVoiceError('The live voice connection closed early.', gotTranscript));
    };
  });
}
