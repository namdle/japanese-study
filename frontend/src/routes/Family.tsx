import { useEffect, useState } from 'react';
import { apiRequest } from '../api/client';

interface FamilyMember {
  id: number;
  name: string;
  level: string;
  voice: string;
  vocab_count: number;
  grammar_count: number;
  mistake_count: number;
  topic_count: number;
  session_count: number;
}

export function Family(): JSX.Element {
  const [members, setMembers] = useState<FamilyMember[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiRequest<FamilyMember[]>('/api/admin/family')
      .then(setMembers)
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <main className="page">
      <header className="page__header">
        <h1>Family overview</h1>
        <p className="page__subtitle">
          Read-only view of each family member's learning progress.
        </p>
      </header>

      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}

      {members === null && !error && <p>Loading…</p>}

      {members && (
        <div className="family-grid">
          {members.map((m) => (
            <article key={m.id} className="family-card">
              <h2 className="family-card__name">{m.name}</h2>
              <p className="family-card__meta">
                Level {m.level} · {m.voice}
              </p>
              <dl className="family-card__stats">
                <div>
                  <dt>Sessions</dt>
                  <dd>{m.session_count}</dd>
                </div>
                <div>
                  <dt>Vocab</dt>
                  <dd>{m.vocab_count}</dd>
                </div>
                <div>
                  <dt>Grammar</dt>
                  <dd>{m.grammar_count}</dd>
                </div>
                <div>
                  <dt>Mistakes</dt>
                  <dd>{m.mistake_count}</dd>
                </div>
                <div>
                  <dt>Topics</dt>
                  <dd>{m.topic_count}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      )}
    </main>
  );
}
