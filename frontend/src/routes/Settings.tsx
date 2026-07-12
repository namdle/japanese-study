import { useEffect, useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import {
  createUser,
  deleteUser,
  listUsers,
  updateUser,
  type User,
  type UserUpdate,
} from '../api/users';

interface SettingsProps {
  currentUser: User;
  onChanged: () => void;
}

export function Settings({ currentUser, onChanged }: SettingsProps): JSX.Element {
  const [users, setUsers] = useState<User[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<{ id: number; name: string } | null>(null);
  // Local draft for the free-text "name in Japanese" field; committed on blur
  // so we don't PATCH on every keystroke.
  const [nameJa, setNameJa] = useState(currentUser.name_ja);
  useEffect(() => {
    setNameJa(currentUser.name_ja);
  }, [currentUser.name_ja]);

  const reload = () => {
    listUsers()
      .then(setUsers)
      .catch((err: Error) => setError(err.message));
  };

  useEffect(() => {
    reload();
  }, []);

  const handleError = (err: unknown) => {
    setError(err instanceof ApiError ? err.detail : (err as Error).message);
  };

  const handlePrefChange = async (patch: UserUpdate) => {
    setError(null);
    try {
      await updateUser(currentUser.id, patch);
      onChanged();
    } catch (err) {
      handleError(err);
    }
  };

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    setError(null);
    try {
      await createUser(name);
      setNewName('');
      reload();
      onChanged();
    } catch (err) {
      handleError(err);
    } finally {
      setCreating(false);
    }
  };

  const onRename = async (e: FormEvent) => {
    e.preventDefault();
    if (!editing) return;
    const name = editing.name.trim();
    if (!name) return;
    setError(null);
    try {
      await updateUser(editing.id, { name });
      setEditing(null);
      reload();
      onChanged();
    } catch (err) {
      handleError(err);
    }
  };

  const onDelete = async (user: User) => {
    if (user.id === currentUser.id) {
      setError("You can't delete the profile you're currently using.");
      return;
    }
    if (!window.confirm(`Delete profile "${user.name}"? This cannot be undone.`)) return;
    setError(null);
    try {
      await deleteUser(user.id);
      reload();
      onChanged();
    } catch (err) {
      handleError(err);
    }
  };

  return (
    <main className="page">
      <header className="page__header">
        <h1>Profiles</h1>
        <p className="page__subtitle">Add, rename, or remove family profiles.</p>
      </header>

      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}

      {users === null && <p>Loading…</p>}

      {users !== null && (
        <ul className="profile-table" aria-label="Profiles">
          {users.map((u) => (
            <li key={u.id} className="profile-row">
              {editing?.id === u.id ? (
                <form className="profile-row__edit" onSubmit={onRename}>
                  <input
                    type="text"
                    value={editing.name}
                    onChange={(e) => setEditing({ id: u.id, name: e.target.value })}
                    autoFocus
                    maxLength={60}
                  />
                  <button type="submit">Save</button>
                  <button type="button" onClick={() => setEditing(null)}>
                    Cancel
                  </button>
                </form>
              ) : (
                <>
                  <span className="profile-row__name">
                    {u.name}
                    {u.is_admin && <span className="badge">admin</span>}
                    {u.id === currentUser.id && <span className="badge badge--accent">you</span>}
                  </span>
                  <span className="profile-row__meta">
                    Level {u.level} · {u.voice}
                  </span>
                  <span className="profile-row__actions">
                    <button
                      type="button"
                      onClick={() => setEditing({ id: u.id, name: u.name })}
                    >
                      Rename
                    </button>
                    <button
                      type="button"
                      onClick={() => onDelete(u)}
                      disabled={u.id === currentUser.id}
                      aria-label={`Delete ${u.name}`}
                    >
                      Delete
                    </button>
                  </span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <form className="add-profile" onSubmit={onCreate}>
        <label htmlFor="settings-new-profile">Add a profile</label>
        <div className="add-profile__row">
          <input
            id="settings-new-profile"
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Name"
            maxLength={60}
            autoComplete="off"
          />
          <button type="submit" disabled={creating || newName.trim() === ''}>
            {creating ? 'Adding…' : 'Add'}
          </button>
        </div>
      </form>

      <section className="preferences-section">
        <h2>Your preferences</h2>
        <div className="preferences-grid">
          <label htmlFor="pref-name-ja">Name in Japanese (biases speech recognition)</label>
          <input
            id="pref-name-ja"
            type="text"
            lang="ja"
            value={nameJa}
            placeholder="e.g. ナム"
            maxLength={40}
            onChange={(e) => setNameJa(e.target.value)}
            onBlur={() => {
              const trimmed = nameJa.trim();
              if (trimmed !== currentUser.name_ja) handlePrefChange({ name_ja: trimmed });
            }}
          />

          <label htmlFor="pref-voice">Tutor voice</label>
          <select
            id="pref-voice"
            value={currentUser.voice}
            onChange={(e) => handlePrefChange({ voice: e.target.value as 'Misa' | 'Hiro' })}
          >
            <option value="Misa">Misa (female)</option>
            <option value="Hiro">Hiro (male)</option>
          </select>

          <label htmlFor="pref-llm">LLM provider</label>
          <select
            id="pref-llm"
            value={currentUser.llm_provider}
            onChange={(e) =>
              handlePrefChange({ llm_provider: e.target.value as UserUpdate['llm_provider'] })
            }
          >
            <option value="claude">Claude (Anthropic)</option>
            <option value="gemini">Gemini (Google)</option>
            <option value="openai">ChatGPT (OpenAI)</option>
            <option value="bedrock">Bedrock (AWS)</option>
          </select>

          <label htmlFor="pref-speech">Speech provider</label>
          <select
            id="pref-speech"
            value={currentUser.speech_provider}
            onChange={(e) =>
              handlePrefChange({
                speech_provider: e.target.value as UserUpdate['speech_provider'],
              })
            }
          >
            <option value="gcloud">Google Cloud (Neural2)</option>
            <option value="openai">OpenAI (Whisper + TTS)</option>
          </select>

          <label htmlFor="pref-correction">Corrections</label>
          <select
            id="pref-correction"
            value={currentUser.correction_style}
            onChange={(e) =>
              handlePrefChange({
                correction_style: e.target.value as UserUpdate['correction_style'],
              })
            }
          >
            <option value="end_of_turn">After each turn (gentle, in-line)</option>
            <option value="end_of_session">At end of session (summary)</option>
          </select>

          <label htmlFor="pref-explanation">Explanation language</label>
          <select
            id="pref-explanation"
            value={currentUser.explanation_language}
            onChange={(e) =>
              handlePrefChange({
                explanation_language: e.target.value as UserUpdate['explanation_language'],
              })
            }
          >
            <option value="en">English (mixed-language replies allowed)</option>
            <option value="ja">Japanese only (immersion)</option>
          </select>

          <label htmlFor="pref-hiragana">Show hiragana under each reply</label>
          <label className="checkbox-cell">
            <input
              id="pref-hiragana"
              type="checkbox"
              checked={currentUser.show_hiragana}
              onChange={(e) => handlePrefChange({ show_hiragana: e.target.checked })}
            />
            <span>Helpful for beginners learning kana.</span>
          </label>

          <label htmlFor="pref-english">Show English translation under each reply</label>
          <label className="checkbox-cell">
            <input
              id="pref-english"
              type="checkbox"
              checked={currentUser.show_english}
              onChange={(e) => handlePrefChange({ show_english: e.target.checked })}
            />
            <span>Useful when first encountering a topic.</span>
          </label>

          <label htmlFor="pref-level">Proficiency level</label>
          <select
            id="pref-level"
            value={currentUser.level}
            onChange={(e) =>
              handlePrefChange({ level: e.target.value as UserUpdate['level'] })
            }
          >
            <option value="A1">A1 — Beginner</option>
            <option value="A2">A2 — Elementary</option>
            <option value="B1">B1 — Intermediate</option>
            <option value="B2">B2 — Upper Intermediate</option>
            <option value="C1">C1 — Advanced</option>
          </select>

          <label htmlFor="pref-auto-stop">Auto-stop after silence of</label>
          <select
            id="pref-auto-stop"
            value={currentUser.auto_stop_seconds}
            onChange={(e) =>
              handlePrefChange({ auto_stop_seconds: Number(e.target.value) })
            }
          >
            <option value={1}>1 second</option>
            <option value={2}>2 seconds</option>
            <option value={3}>3 seconds</option>
            <option value={5}>5 seconds</option>
            <option value={7}>7 seconds</option>
            <option value={10}>10 seconds</option>
          </select>
        </div>
      </section>
    </main>
  );
}
