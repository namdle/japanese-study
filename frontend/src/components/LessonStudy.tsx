import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { ApiError } from '../api/client';
import { getLessonStudy } from '../api/sessions';

interface LessonStudyProps {
  lessonId: number;
}

/**
 * Collapsible lesson-info panel. Shows the learner-facing Scenario, Target
 * vocabulary, and Key sentence patterns for a lesson. Content is fetched
 * lazily the first time the panel is opened, so it adds no cost when unused.
 * Remount (via a `key` on lessonId) resets it for a different lesson.
 */
export function LessonStudy({ lessonId }: LessonStudyProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && markdown === null && !loading) {
      setLoading(true);
      setError(null);
      try {
        const res = await getLessonStudy(lessonId);
        setMarkdown(res.study_markdown ?? '');
      } catch (err) {
        setError(err instanceof ApiError ? err.detail : 'Could not load lesson notes.');
      } finally {
        setLoading(false);
      }
    }
  };

  const empty = markdown !== null && markdown.trim() === '';

  return (
    <div className="lesson-study">
      <button
        type="button"
        className="lesson-study__toggle"
        onClick={toggle}
        aria-expanded={open}
      >
        {open ? '▾' : '▸'} Lesson info — scenario, vocabulary & key phrases
      </button>
      {open && (
        <div className="lesson-study__panel">
          {loading && <p className="lesson-study__hint">Loading…</p>}
          {error && (
            <p className="error-banner" role="alert">
              {error}
            </p>
          )}
          {empty && <p className="lesson-study__hint">No lesson info available yet.</p>}
          {markdown && markdown.trim() !== '' && (
            <div className="markdown-preview">
              <ReactMarkdown>{markdown}</ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
