import { useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { ApiError } from '../api/client';
import {
  approvePlan,
  getLessonDetail,
  listTopics,
  revertPlan,
  savePlan,
  type Lesson,
  type LessonDetail,
  type LessonPlan,
  type Topic,
} from '../api/curriculum';
import type { User } from '../api/users';

interface CurriculumProps {
  currentUser: User;
}

export function Curriculum({ currentUser }: CurriculumProps): JSX.Element {
  const [topics, setTopics] = useState<Topic[] | null>(null);
  const [selectedLessonId, setSelectedLessonId] = useState<number | null>(null);
  const [detail, setDetail] = useState<LessonDetail | null>(null);
  const [draft, setDraft] = useState<string>('');
  const [savingState, setSavingState] = useState<'idle' | 'saving' | 'approving' | 'reverting'>(
    'idle',
  );
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const isAdmin = currentUser.is_admin;

  // Load topics once.
  useEffect(() => {
    listTopics()
      .then((rows) => {
        setTopics(rows);
        if (rows.length > 0 && rows[0].lessons.length > 0) {
          setSelectedLessonId(rows[0].lessons[0].id);
        }
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  // Load the lesson detail whenever the selection changes.
  useEffect(() => {
    if (selectedLessonId === null) return;
    let cancelled = false;
    setError(null);
    setStatusMessage(null);
    getLessonDetail(selectedLessonId)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        setDraft(d.plan?.body_markdown ?? '');
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedLessonId]);

  const lessonsById = useMemo(() => {
    const map = new Map<number, Lesson>();
    if (!topics) return map;
    for (const t of topics) for (const l of t.lessons) map.set(l.id, l);
    return map;
  }, [topics]);

  const selectedLesson = selectedLessonId !== null ? lessonsById.get(selectedLessonId) : undefined;

  const refreshAfter = (updated: LessonPlan) => {
    setDetail((d) => (d ? { ...d, plan: updated } : d));
    setDraft(updated.body_markdown);
  };

  const handleError = (err: unknown) => {
    setError(err instanceof ApiError ? err.detail : (err as Error).message);
  };

  const onSave = async () => {
    if (!selectedLessonId) return;
    setSavingState('saving');
    setError(null);
    setStatusMessage(null);
    try {
      const plan = await savePlan(selectedLessonId, draft);
      refreshAfter(plan);
      setStatusMessage('Saved as draft.');
    } catch (err) {
      handleError(err);
    } finally {
      setSavingState('idle');
    }
  };

  const onApprove = async () => {
    if (!selectedLessonId) return;
    setSavingState('approving');
    setError(null);
    setStatusMessage(null);
    try {
      // Save first if there are unsaved edits.
      if (detail?.plan?.body_markdown !== draft) {
        await savePlan(selectedLessonId, draft);
      }
      const plan = await approvePlan(selectedLessonId);
      refreshAfter(plan);
      setStatusMessage('Approved. Sessions will use this plan.');
    } catch (err) {
      handleError(err);
    } finally {
      setSavingState('idle');
    }
  };

  const onRevert = async () => {
    if (!selectedLessonId) return;
    setSavingState('reverting');
    setError(null);
    setStatusMessage(null);
    try {
      const plan = await revertPlan(selectedLessonId);
      refreshAfter(plan);
      setStatusMessage('Reverted to draft.');
    } catch (err) {
      handleError(err);
    } finally {
      setSavingState('idle');
    }
  };

  const dirty = detail?.plan ? detail.plan.body_markdown !== draft : draft.length > 0;
  const planStatus = detail?.plan?.status;

  return (
    <main className="page curriculum-page">
      <header className="page__header">
        <h1>Curriculum</h1>
        <p className="page__subtitle">
          Kid-friendly conversation topics. {isAdmin
            ? 'Edit and approve lesson plans here.'
            : 'Approved plans are used by your tutor during practice.'}
        </p>
      </header>

      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}

      <div className="curriculum">
        {/* Sidebar: topic tree */}
        <aside className="curriculum__tree" aria-label="Topics and lessons">
          {topics === null && <p>Loading…</p>}
          {topics?.map((topic) => (
            <details key={topic.id} open>
              <summary>
                <span className="topic-title">{topic.title_en}</span>
                <span className="topic-title__ja">{topic.title_ja}</span>
              </summary>
              <ul>
                {topic.lessons.map((lesson) => (
                  <li key={lesson.id}>
                    <button
                      type="button"
                      className={`lesson-link ${
                        lesson.id === selectedLessonId ? 'lesson-link--selected' : ''
                      }`}
                      onClick={() => setSelectedLessonId(lesson.id)}
                    >
                      <span className="lesson-link__level">{lesson.level}</span>
                      <span className="lesson-link__title">{lesson.title_en}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </aside>

        {/* Main: lesson detail + editor/preview */}
        <section className="curriculum__main">
          {selectedLesson === undefined && <p className="empty-state">Pick a lesson to view it.</p>}
          {selectedLesson && (
            <>
              <div className="lesson-header">
                <h2>
                  {selectedLesson.title_en}{' '}
                  <span className="lesson-header__ja">{selectedLesson.title_ja}</span>
                </h2>
                <p className="lesson-header__meta">
                  Level {selectedLesson.level}
                  {planStatus && (
                    <>
                      {' · '}
                      <span
                        className={`badge ${
                          planStatus === 'approved' ? 'badge--accent' : ''
                        }`}
                      >
                        {planStatus}
                      </span>
                    </>
                  )}
                </p>
                {selectedLesson.can_dos.length > 0 && (
                  <ul className="can-dos">
                    {selectedLesson.can_dos.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                )}
              </div>

              {statusMessage && <p className="status-message">{statusMessage}</p>}

              {isAdmin ? (
                <div className="plan-editor">
                  <div className="plan-editor__panes">
                    <div className="plan-editor__pane">
                      <label htmlFor="plan-markdown" className="plan-editor__label">
                        Markdown
                      </label>
                      <textarea
                        id="plan-markdown"
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        rows={16}
                        placeholder="Write the lesson plan in Markdown. The LLM uses this directly as input."
                      />
                    </div>
                    <div className="plan-editor__pane">
                      <span className="plan-editor__label">Preview</span>
                      <div className="markdown-preview">
                        {draft.trim() ? (
                          <ReactMarkdown>{draft}</ReactMarkdown>
                        ) : (
                          <p className="empty-state">Preview appears here.</p>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="plan-editor__actions">
                    <button
                      type="button"
                      onClick={onSave}
                      disabled={savingState !== 'idle' || !dirty}
                    >
                      {savingState === 'saving' ? 'Saving…' : 'Save draft'}
                    </button>
                    <button
                      type="button"
                      onClick={onApprove}
                      disabled={savingState !== 'idle' || draft.trim().length === 0}
                    >
                      {savingState === 'approving' ? 'Approving…' : 'Approve'}
                    </button>
                    {planStatus === 'approved' && (
                      <button
                        type="button"
                        onClick={onRevert}
                        disabled={savingState !== 'idle'}
                      >
                        {savingState === 'reverting' ? 'Reverting…' : 'Revert to draft'}
                      </button>
                    )}
                  </div>
                </div>
              ) : (
                <div className="markdown-preview">
                  {detail?.plan?.body_markdown ? (
                    <ReactMarkdown>{detail.plan.body_markdown}</ReactMarkdown>
                  ) : (
                    <p className="empty-state">No plan has been approved for this lesson yet.</p>
                  )}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </main>
  );
}
