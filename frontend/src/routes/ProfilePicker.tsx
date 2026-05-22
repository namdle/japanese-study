import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { createUser, listUsers, type User } from '../api/users';

interface ProfilePickerProps {
  onSelect: (id: number) => void;
}

export function ProfilePicker({ onSelect }: ProfilePickerProps): JSX.Element {
  const navigate = useNavigate();
  const [users, setUsers] = useState<User[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);

  const reload = () => {
    listUsers()
      .then(setUsers)
      .catch((err: Error) => setError(err.message));
  };

  useEffect(() => {
    reload();
  }, []);

  const select = (id: number) => {
    onSelect(id);
    navigate('/');
  };

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    setError(null);
    try {
      const user = await createUser(name);
      setNewName('');
      reload();
      select(user.id);
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : (err as Error).message;
      setError(msg);
    } finally {
      setCreating(false);
    }
  };

  return (
    <main className="page">
      <header className="page__header">
        <h1>Who's practicing today?</h1>
        <p className="page__subtitle">Pick a profile, or add a new one.</p>
      </header>

      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}

      {users === null && <p>Loading…</p>}

      {users !== null && users.length === 0 && (
        <p className="empty-state">No profiles yet — add one below to get started.</p>
      )}

      {users !== null && users.length > 0 && (
        <ul className="profile-list" aria-label="Profiles">
          {users.map((u) => (
            <li key={u.id}>
              <button
                type="button"
                className="profile-card"
                onClick={() => select(u.id)}
              >
                <span className="profile-card__name">{u.name}</span>
                {u.is_admin && <span className="profile-card__badge">admin</span>}
                <span className="profile-card__meta">
                  Level {u.level} · {u.voice}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <form className="add-profile" onSubmit={onCreate}>
        <label htmlFor="new-profile">Add a profile</label>
        <div className="add-profile__row">
          <input
            id="new-profile"
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
    </main>
  );
}
