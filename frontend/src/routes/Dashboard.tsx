import { useEffect, useState } from 'react';
import { apiRequest } from '../api/client';
import type { User } from '../api/users';

interface VocabItem {
  id: number;
  jp: string;
  reading: string | null;
  en: string | null;
  mastery: number;
  last_seen_at: string;
}
interface GrammarItem {
  id: number;
  code: string;
  example_jp: string | null;
  notes: string | null;
  mastery: number;
}
interface MistakeItem {
  id: number;
  original: string;
  corrected: string;
  note: string | null;
  created_at: string;
}
interface TopicItem {
  id: number;
  keyword: string;
  weight: number;
}
interface Profile {
  vocab: VocabItem[];
  grammar: GrammarItem[];
  mistakes: MistakeItem[];
  topics: TopicItem[];
}

interface DashboardProps {
  user: User;
}

export function Dashboard({ user }: DashboardProps): JSX.Element {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiRequest<Profile>('/api/profile')
      .then(setProfile)
      .catch(() => setProfile(null))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="page dashboard-page">
      <header className="page__header">
        <h1>こんにちは、{user.name}さん</h1>
        <p className="page__subtitle">
          Level {user.level} · Tutor: {user.voice}
        </p>
      </header>

      {loading && <p>Loading profile…</p>}

      {!loading && profile && (
        <div className="dashboard-grid">
          {/* Topics */}
          <section className="dash-card">
            <h2 className="dash-card__title">Topics of interest</h2>
            {profile.topics.length === 0 ? (
              <p className="dash-card__empty">Complete a session to see topics here.</p>
            ) : (
              <ul className="topic-list">
                {profile.topics.map((t) => (
                  <li key={t.id} className="topic-chip">
                    {t.keyword}
                    <span className="topic-chip__weight">×{t.weight}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Vocab */}
          <section className="dash-card dash-card--wide">
            <h2 className="dash-card__title">
              Vocabulary <span className="dash-card__count">{profile.vocab.length}</span>
            </h2>
            {profile.vocab.length === 0 ? (
              <p className="dash-card__empty">No vocab captured yet.</p>
            ) : (
              <ul className="vocab-list">
                {profile.vocab.slice(0, 40).map((v) => (
                  <li key={v.id} className="vocab-item">
                    <span className="vocab-item__jp">{v.jp}</span>
                    {v.reading && (
                      <span className="vocab-item__reading">{v.reading}</span>
                    )}
                    {v.en && <span className="vocab-item__en">{v.en}</span>}
                    <MasteryBar value={v.mastery} />
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Grammar */}
          <section className="dash-card">
            <h2 className="dash-card__title">
              Grammar <span className="dash-card__count">{profile.grammar.length}</span>
            </h2>
            {profile.grammar.length === 0 ? (
              <p className="dash-card__empty">No grammar points yet.</p>
            ) : (
              <ul className="grammar-list">
                {profile.grammar.map((g) => (
                  <li key={g.id} className="grammar-item">
                    <span className="grammar-item__code">{g.code}</span>
                    {g.example_jp && (
                      <span className="grammar-item__example">{g.example_jp}</span>
                    )}
                    <MasteryBar value={g.mastery} />
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Mistakes */}
          <section className="dash-card">
            <h2 className="dash-card__title">
              Recent mistakes{' '}
              <span className="dash-card__count">{profile.mistakes.length}</span>
            </h2>
            {profile.mistakes.length === 0 ? (
              <p className="dash-card__empty">No mistakes recorded — keep it up!</p>
            ) : (
              <ul className="mistake-list">
                {profile.mistakes.slice(0, 10).map((m) => (
                  <li key={m.id} className="mistake-item">
                    <span className="mistake-item__original">{m.original}</span>
                    <span className="mistake-item__arrow">→</span>
                    <span className="mistake-item__corrected">{m.corrected}</span>
                    {m.note && <span className="mistake-item__note">{m.note}</span>}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}

      {!loading && !profile && (
        <p className="dash-card__empty">
          Could not load your profile. Complete a practice session first.
        </p>
      )}
    </main>
  );
}

function MasteryBar({ value }: { value: number }): JSX.Element {
  const pct = Math.round((value / 5) * 100);
  return (
    <span className="mastery-bar" title={`Mastery: ${value}/5`}>
      <span className="mastery-bar__fill" style={{ width: `${pct}%` }} />
    </span>
  );
}
