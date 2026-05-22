import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react';
import { ApiError } from '../api/client';
import {
  endSession,
  getActiveSession,
  postTextTurn,
  postVoiceTurn,
  startSession,
  startSessionFromImage,
  type LessonInfo,
  type SessionDetail,
} from '../api/sessions';
import type { User } from '../api/users';
import { useMic } from '../hooks/useMic';

interface ChatProps {
  user: User;
}

type LoadState = 'loading' | 'no-session' | 'active' | 'error';

export function Chat({ user }: ChatProps): JSX.Element {
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [nextLesson, setNextLesson] = useState<LessonInfo | null>(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState<'idle' | 'starting' | 'sending' | 'ending' | 'uploading'>('idle');
  const [error, setError] = useState<string | null>(null);
  // Mode chosen on the lesson preview, applied on the next Start.
  const [pendingMode, setPendingMode] = useState<'freeform' | 'three_phase'>('freeform');
  // Summary returned by End session (only set when correction_style is end_of_session).
  const [endSummary, setEndSummary] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLOListElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const lastPlayedAudioUrl = useRef<string | null>(null);
  // Keep a ref to the live session so the mic onStop callback always uses the
  // current value (it's created with the initial render's closure otherwise).
  const detailRef = useRef<SessionDetail | null>(null);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const active = await getActiveSession();
      setNextLesson(active.next_lesson);
      if (active.active) {
        setDetail(active.active);
        setLoadState('active');
      } else {
        setDetail(null);
        setLoadState('no-session');
      }
    } catch (err) {
      const detailMsg = err instanceof ApiError ? err.detail : (err as Error).message;
      setError(detailMsg);
      setLoadState('error');
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Auto-scroll the transcript when new turns appear.
  useEffect(() => {
    if (!transcriptRef.current) return;
    transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
  }, [detail, busy]);

  // Auto-play newly arrived assistant audio (only the latest one we haven't played).
  useEffect(() => {
    if (!detail || !audioRef.current) return;
    const lastWithAudio = [...detail.turns]
      .reverse()
      .find((t) => t.role === 'assistant' && t.audio_url);
    if (!lastWithAudio?.audio_url) return;
    if (lastWithAudio.audio_url === lastPlayedAudioUrl.current) return;
    lastPlayedAudioUrl.current = lastWithAudio.audio_url;
    audioRef.current.src = lastWithAudio.audio_url;
    audioRef.current.play().catch(() => {
      /* autoplay can be blocked; user can press play */
    });
  }, [detail]);

  const handleStart = async () => {
    setBusy('starting');
    setError(null);
    setEndSummary(null);
    try {
      const started = await startSession({ mode: pendingMode });
      setDetail(started);
      setLoadState('active');
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : (err as Error).message);
    } finally {
      setBusy('idle');
    }
  };

  const handleImageStart = async (file: File) => {
    if (!file.type.startsWith('image/')) {
      setError(
        'That file is not an image. Open the photo, then drag the still image (JPEG, PNG, WebP, GIF, or HEIC) here. Videos and Motion Photos aren\'t supported.',
      );
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setError('Image must be 10 MB or smaller.');
      return;
    }
    setBusy('uploading');
    setError(null);
    setEndSummary(null);
    try {
      const started = await startSessionFromImage(file, pendingMode);
      setDetail(started);
      setLoadState('active');
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : (err as Error).message);
    } finally {
      setBusy('idle');
    }
  };

  const handleEnd = async () => {
    if (!detail) return;
    setBusy('ending');
    setError(null);
    try {
      const ended = await endSession(detail.session.id);
      lastPlayedAudioUrl.current = null;
      setEndSummary(ended.summary ?? null);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : (err as Error).message);
    } finally {
      setBusy('idle');
    }
  };

  const handleVoiceTurn = useCallback(async (audioBlob: Blob) => {
    const current = detailRef.current;
    if (!current) return;
    setBusy('sending');
    setError(null);
    try {
      const updated = await postVoiceTurn(current.session.id, audioBlob);
      setDetail(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : (err as Error).message);
    } finally {
      setBusy('idle');
    }
  }, []);

  const mic = useMic({ onStop: handleVoiceTurn });

  const onTextSubmit = async (e: FormEvent | KeyboardEvent) => {
    e.preventDefault();
    if (!detail) return;
    const text = draft.trim();
    if (!text || busy !== 'idle') return;
    setBusy('sending');
    setError(null);
    setDraft('');
    try {
      const updated = await postTextTurn(detail.session.id, text);
      setDetail(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : (err as Error).message);
    } finally {
      setBusy('idle');
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      onTextSubmit(e);
    }
  };

  const toggleMic = () => {
    if (mic.state === 'recording') mic.stop();
    else if (mic.state === 'idle' || mic.state === 'denied') void mic.start();
  };

  const sending = busy === 'sending' || busy === 'starting' || busy === 'ending';
  const micLabel =
    mic.state === 'recording'
      ? 'Stop recording'
      : mic.state === 'requesting'
        ? 'Starting…'
        : mic.state === 'unsupported'
          ? 'Mic unsupported in this browser'
          : 'Start recording';
  const micDisabled = sending || mic.state === 'unsupported' || mic.state === 'requesting';

  // ---------------------------------------------------------------------- //
  // Rendering
  // ---------------------------------------------------------------------- //

  if (loadState === 'loading') {
    return (
      <main className="page chat-page">
        <header className="page__header">
          <h1>Practice with {user.voice}</h1>
        </header>
        <p>Loading session…</p>
      </main>
    );
  }

  if (loadState === 'no-session' || loadState === 'error') {
    return (
      <main className="page chat-page">
        <header className="page__header">
          <h1>Practice with {user.voice}</h1>
          <p className="page__subtitle">
            Start a curriculum-aligned conversation. Sessions are saved as you go.
          </p>
        </header>
        {error && (
          <p className="error-banner" role="alert">
            {error}
          </p>
        )}

        {endSummary && (
          <section className="end-summary" aria-label="Session summary">
            <h2>Session wrap-up</h2>
            <p className="end-summary__text">{endSummary}</p>
          </section>
        )}

        {nextLesson ? (
          <section className="lesson-preview">
            <h2>Next up: {nextLesson.title_en}</h2>
            <p className="lesson-preview__meta">
              {nextLesson.topic_title_en} · Level {nextLesson.level}
            </p>
            {nextLesson.can_dos.length > 0 && (
              <ul className="can-dos">
                {nextLesson.can_dos.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            )}

            <fieldset className="mode-toggle">
              <legend>Session style</legend>
              <label>
                <input
                  type="radio"
                  name="mode"
                  value="freeform"
                  checked={pendingMode === 'freeform'}
                  onChange={() => setPendingMode('freeform')}
                />
                <span>Free-form chat</span>
                <span className="mode-toggle__hint">
                  Open conversation around the topic.
                </span>
              </label>
              <label>
                <input
                  type="radio"
                  name="mode"
                  value="three_phase"
                  checked={pendingMode === 'three_phase'}
                  onChange={() => setPendingMode('three_phase')}
                />
                <span>3-phase lesson</span>
                <span className="mode-toggle__hint">
                  Warm-up → main practice → wrap-up.
                </span>
              </label>
            </fieldset>

            <button
              type="button"
              className="primary-button"
              onClick={handleStart}
              disabled={busy !== 'idle'}
            >
              {busy === 'starting' ? 'Starting…' : 'Start session'}
            </button>
          </section>
        ) : (
          <section className="lesson-preview">
            <h2>No approved lesson plans yet</h2>
            <p>
              An admin needs to approve a lesson plan in <strong>Curriculum</strong>{' '}
              before you can start a session.
            </p>
          </section>
        )}

        <ImageUploadPanel
          uploading={busy === 'uploading'}
          onImage={handleImageStart}
        />
      </main>
    );
  }

  if (!detail) return <main className="page" />;
  const lesson = detail.lesson;

  return (
    <main className="page chat-page">
      <header className="page__header">
        <h1>Practice with {user.voice}</h1>
        {lesson ? (
          <p className="page__subtitle">
            <strong>{lesson.title_en}</strong> ({lesson.topic_title_en} · Level {lesson.level})
            {' · '}
            <span className="badge">
              {detail.session.mode === 'three_phase' ? '3-phase' : 'free-form'}
            </span>
          </p>
        ) : (
          <p className="page__subtitle">Free conversation.</p>
        )}
      </header>

      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {mic.error && (
        <p className="error-banner" role="alert">
          {mic.error}
        </p>
      )}

      {detail.session.seed_image_url && (
        <div className="seed-image-preview">
          <img
            src={detail.session.seed_image_url}
            alt="Uploaded textbook page"
          />
          <span className="seed-image-preview__caption">
            Practicing from your uploaded image.
          </span>
        </div>
      )}

      <ol className="transcript" aria-label="Conversation" ref={transcriptRef}>
        {detail.turns.length === 0 && (
          <li className="transcript__empty">No messages yet — say something to begin.</li>
        )}
        {detail.turns.map((t) => (
          <li
            key={t.id}
            className={`transcript__turn transcript__turn--${t.role}`}
            data-testid={`turn-${t.role}`}
          >
            <span className="transcript__role">{t.role === 'user' ? user.name : user.voice}</span>
            <span className="transcript__content">{t.text}</span>
            {t.role === 'assistant' && t.hiragana && (
              <span className="transcript__aid transcript__aid--hiragana" lang="ja">
                {t.hiragana}
              </span>
            )}
            {t.role === 'assistant' && t.english && (
              <span className="transcript__aid transcript__aid--english" lang="en">
                {t.english}
              </span>
            )}
          </li>
        ))}
        {sending && busy === 'sending' && (
          <li className="transcript__turn transcript__turn--assistant">
            <span className="transcript__role">{user.voice}</span>
            <span className="transcript__content transcript__content--pending">…</span>
          </li>
        )}
      </ol>

      {/* Hidden audio element auto-plays assistant replies. */}
      <audio ref={audioRef} controls className="audio-player" aria-label="Tutor audio" />

      <div className="composer-wrap">
        <div className="composer-actions">
          <button
            type="button"
            className={`mic-button ${mic.state === 'recording' ? 'mic-button--recording' : ''}`}
            onClick={toggleMic}
            disabled={micDisabled}
            aria-label={micLabel}
            title={micLabel}
          >
            {mic.state === 'recording' ? '■ Stop' : '🎤 Speak'}
          </button>
          <button
            type="button"
            className="end-session-button"
            onClick={handleEnd}
            disabled={busy !== 'idle'}
          >
            {busy === 'ending' ? 'Ending…' : 'End session'}
          </button>
        </div>

        <form className="composer" onSubmit={onTextSubmit}>
          <label htmlFor="chat-input" className="visually-hidden">
            Your message
          </label>
          <textarea
            id="chat-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Type in Japanese — Cmd/Ctrl+Enter to send"
            rows={2}
            disabled={sending}
          />
          <button type="submit" disabled={sending || draft.trim() === ''}>
            {busy === 'sending' ? 'Sending…' : 'Send'}
          </button>
        </form>
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------- //
// Image upload sub-component
// ---------------------------------------------------------------------- //

interface ImageUploadPanelProps {
  uploading: boolean;
  onImage: (file: File) => void;
}

function ImageUploadPanel({ uploading, onImage }: ImageUploadPanelProps): JSX.Element {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) onImage(file);
  };

  const onPick = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) onImage(file);
    e.target.value = '';
  };

  return (
    <section
      className={`image-upload ${dragOver ? 'image-upload--drag' : ''}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
      aria-label="Upload textbook image"
    >
      <h2>Or upload a textbook page</h2>
      <p className="image-upload__hint">
        Drop a photo or screenshot here, and your tutor will design a short
        practice based on what's on the page.
      </p>
      <div className="image-upload__actions">
        <input
          ref={inputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif,.heic,.heif"
          onChange={onPick}
          style={{ display: 'none' }}
          data-testid="image-upload-input"
        />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={uploading}
        >
          {uploading ? 'Uploading…' : 'Choose image'}
        </button>
        <span className="image-upload__formats">
          JPEG · PNG · WebP · GIF · HEIC, up to 10 MB
        </span>
      </div>
    </section>
  );
}
