import { useCallback, useEffect, useRef, useState } from 'react';

export type MicState = 'idle' | 'requesting' | 'recording' | 'unsupported' | 'denied';

interface UseMicOptions {
  onStop?: (audio: Blob) => void;
}

interface UseMic {
  state: MicState;
  start: () => Promise<void>;
  stop: () => void;
  error: string | null;
}

function pickMimeType(): string | undefined {
  // Prefer compact opus where supported.
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];
  for (const c of candidates) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(c)) return c;
  }
  return undefined;
}

export function useMic(options: UseMicOptions = {}): UseMic {
  const [state, setState] = useState<MicState>(() =>
    typeof MediaRecorder === 'undefined' ? 'unsupported' : 'idle',
  );
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const mimeRef = useRef<string | undefined>(undefined);
  const onStopRef = useRef(options.onStop);

  // Keep the latest callback without re-creating start/stop on every render.
  useEffect(() => {
    onStopRef.current = options.onStop;
  }, [options.onStop]);

  const cleanupStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const start = useCallback(async () => {
    if (state === 'unsupported') return;
    setError(null);
    setState('requesting');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = pickMimeType();
      mimeRef.current = mime;
      const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const type = mime ?? recorder.mimeType ?? 'audio/webm';
        const blob = new Blob(chunksRef.current, { type });
        chunksRef.current = [];
        cleanupStream();
        setState('idle');
        onStopRef.current?.(blob);
      };
      recorder.onerror = (e) => {
        setError(`Recorder error: ${(e as ErrorEvent).message ?? 'unknown'}`);
        cleanupStream();
        setState('idle');
      };
      recorderRef.current = recorder;
      recorder.start();
      setState('recording');
    } catch (err) {
      const e = err as DOMException;
      const denied = e?.name === 'NotAllowedError' || e?.name === 'SecurityError';
      setError(denied ? 'Microphone permission denied.' : `Could not start mic: ${e?.message ?? err}`);
      setState(denied ? 'denied' : 'idle');
      cleanupStream();
    }
  }, [state, cleanupStream]);

  const stop = useCallback(() => {
    const r = recorderRef.current;
    if (r && r.state !== 'inactive') {
      r.stop();
    } else {
      cleanupStream();
      setState('idle');
    }
  }, [cleanupStream]);

  // Make sure we release the mic if the component unmounts mid-recording.
  useEffect(() => {
    return () => {
      const r = recorderRef.current;
      if (r && r.state !== 'inactive') {
        try {
          r.stop();
        } catch {
          // ignore
        }
      }
      cleanupStream();
    };
  }, [cleanupStream]);

  return { state, start, stop, error };
}
