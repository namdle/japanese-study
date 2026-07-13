import { useCallback, useEffect, useRef, useState } from 'react';

export type MicState = 'idle' | 'requesting' | 'recording' | 'unsupported' | 'denied';

interface UseMicOptions {
  onStop?: (audio: Blob) => void;
  // "Auto-stop" silence window: when > 0, recording ends automatically after
  // this many milliseconds of *silence* once the user has started speaking
  // (voice-activity detection). The user can always stop manually. 0/undefined
  // disables auto-stop.
  autoStopMs?: number;
}

interface UseMic {
  state: MicState;
  start: () => Promise<void>;
  stop: () => void;
  error: string | null;
}

// --- Voice-activity-detection tuning -------------------------------------- //
const VAD_POLL_MS = 50; // how often we sample the mic level (finer = snappier stop)
const VAD_CALIBRATION_MS = 400; // measure ambient noise before judging speech
const VAD_SPEECH_FACTOR = 3; // speech = this many× above the noise floor
const VAD_SPEECH_ABS_MIN = 0.01; // absolute RMS floor so dead silence never counts
const VAD_NOISE_FLOOR_MIN = 0.003;
const VAD_NOISE_FLOOR_MAX = 0.02; // clamp so speaking during calibration can't blind us
// Hysteresis: once speaking, quieter trailing audio (soft syllables, breathy
// endings) still counts as speech for a short hangover window. This prevents
// premature cuts, which makes short auto-stop settings (1-2s) safe to use.
const VAD_KEEP_FACTOR = 2; // continue-speech threshold, relative to noise floor
const VAD_KEEP_ABS_MIN = 0.006;
const VAD_HANGOVER_MS = 400; // how long after speech the lower threshold applies
// Hard safety cap: a single turn never records longer than this (also the sole
// stop mechanism if the user never speaks, or if Web Audio is unavailable).
const MAX_RECORDING_MS = 60_000;

function pickMimeType(): string | undefined {
  // Prefer compact opus where supported.
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];
  for (const c of candidates) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(c)) return c;
  }
  return undefined;
}

function getAudioContextCtor(): typeof AudioContext | undefined {
  if (typeof window === 'undefined') return undefined;
  return (
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  );
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
  const autoStopMsRef = useRef(options.autoStopMs);
  const autoStopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const vadIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);

  // Keep the latest callback/config without re-creating start/stop on every render.
  useEffect(() => {
    onStopRef.current = options.onStop;
  }, [options.onStop]);
  useEffect(() => {
    autoStopMsRef.current = options.autoStopMs;
  }, [options.autoStopMs]);

  // Tear down every auto-stop mechanism (silence timer, VAD poller, audio graph).
  const clearAutoStop = useCallback(() => {
    if (autoStopTimerRef.current !== null) {
      clearTimeout(autoStopTimerRef.current);
      autoStopTimerRef.current = null;
    }
    if (vadIntervalRef.current !== null) {
      clearInterval(vadIntervalRef.current);
      vadIntervalRef.current = null;
    }
    if (audioCtxRef.current !== null) {
      try {
        void audioCtxRef.current.close();
      } catch {
        // ignore
      }
      audioCtxRef.current = null;
    }
  }, []);

  const cleanupStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  // Arm auto-stop: prefer silence detection (Web Audio), else a max-length cap.
  const armAutoStop = useCallback((stream: MediaStream, silenceMs: number) => {
    const stopNow = () => {
      const r = recorderRef.current;
      if (r && r.state !== 'inactive') r.stop();
    };

    const AudioCtx = getAudioContextCtor();
    if (!AudioCtx) {
      // No Web Audio — can't detect silence. Cap length so the mic can't hang.
      autoStopTimerRef.current = setTimeout(stopNow, MAX_RECORDING_MS);
      return;
    }

    try {
      const audioCtx = new AudioCtx();
      audioCtxRef.current = audioCtx;
      void audioCtx.resume(); // required on some browsers; harmless on Chrome

      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);
      const buf = new Float32Array(analyser.fftSize);

      const startedAt = Date.now();
      let lastSpeech = startedAt;
      let hasSpoken = false;
      let noiseFloor = VAD_NOISE_FLOOR_MIN;
      let noiseMin = Infinity;
      let calibrated = false;

      vadIntervalRef.current = setInterval(() => {
        analyser.getFloatTimeDomainData(buf);
        let sumSq = 0;
        for (let i = 0; i < buf.length; i++) sumSq += buf[i] * buf[i];
        const rms = Math.sqrt(sumSq / buf.length);
        const now = Date.now();
        const elapsed = now - startedAt;

        // Calibrate the ambient noise floor from the quietest early frame
        // (min is robust to the user speaking during calibration).
        if (elapsed < VAD_CALIBRATION_MS) {
          noiseMin = Math.min(noiseMin, rms);
          return;
        }
        if (!calibrated) {
          const measured = noiseMin === Infinity ? VAD_NOISE_FLOOR_MIN : noiseMin;
          noiseFloor = Math.min(Math.max(measured, VAD_NOISE_FLOOR_MIN), VAD_NOISE_FLOOR_MAX);
          calibrated = true;
        }

        const enterThreshold = Math.max(noiseFloor * VAD_SPEECH_FACTOR, VAD_SPEECH_ABS_MIN);
        const keepThreshold = Math.max(noiseFloor * VAD_KEEP_FACTOR, VAD_KEEP_ABS_MIN);
        if (rms > enterThreshold) {
          lastSpeech = now;
          hasSpoken = true;
        } else if (hasSpoken && now - lastSpeech <= VAD_HANGOVER_MS && rms > keepThreshold) {
          // Trailing soft speech: keep the silence clock from starting early.
          lastSpeech = now;
        }

        // Stop after a silence gap following speech, or at the hard cap.
        if ((hasSpoken && now - lastSpeech >= silenceMs) || elapsed >= MAX_RECORDING_MS) {
          stopNow();
        }
      }, VAD_POLL_MS);
    } catch {
      // Web Audio setup failed — degrade to a max-length cap.
      clearAutoStop();
      autoStopTimerRef.current = setTimeout(stopNow, MAX_RECORDING_MS);
    }
  }, [clearAutoStop]);

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
        clearAutoStop();
        const type = mime ?? recorder.mimeType ?? 'audio/webm';
        const blob = new Blob(chunksRef.current, { type });
        chunksRef.current = [];
        cleanupStream();
        setState('idle');
        onStopRef.current?.(blob);
      };
      recorder.onerror = (e) => {
        clearAutoStop();
        setError(`Recorder error: ${(e as ErrorEvent).message ?? 'unknown'}`);
        cleanupStream();
        setState('idle');
      };
      recorderRef.current = recorder;
      recorder.start();
      setState('recording');

      // Arm auto-stop (silence detection) if enabled. Manual stop clears it too.
      const silenceMs = autoStopMsRef.current;
      if (silenceMs && silenceMs > 0) {
        armAutoStop(stream, silenceMs);
      }
    } catch (err) {
      const e = err as DOMException;
      const denied = e?.name === 'NotAllowedError' || e?.name === 'SecurityError';
      setError(denied ? 'Microphone permission denied.' : `Could not start mic: ${e?.message ?? err}`);
      setState(denied ? 'denied' : 'idle');
      cleanupStream();
    }
  }, [state, cleanupStream, clearAutoStop, armAutoStop]);

  const stop = useCallback(() => {
    clearAutoStop();
    const r = recorderRef.current;
    if (r && r.state !== 'inactive') {
      r.stop();
    } else {
      cleanupStream();
      setState('idle');
    }
  }, [cleanupStream, clearAutoStop]);

  // Make sure we release the mic if the component unmounts mid-recording.
  useEffect(() => {
    return () => {
      clearAutoStop();
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
  }, [cleanupStream, clearAutoStop]);

  return { state, start, stop, error };
}
