import { Navigate, Route, BrowserRouter, Routes, useLocation } from 'react-router-dom';
import { Header } from './components/Header';
import { useProfile } from './hooks/useProfile';
import { Chat } from './routes/Chat';
import { Curriculum } from './routes/Curriculum';
import { Dashboard } from './routes/Dashboard';
import { Family } from './routes/Family';
import { ProfilePicker } from './routes/ProfilePicker';
import { Settings } from './routes/Settings';

function AppShell(): JSX.Element {
  const profile = useProfile();
  const location = useLocation();

  if (profile.status === 'loading') {
    return (
      <main className="page">
        <p>Loading…</p>
      </main>
    );
  }

  if (profile.status === 'error') {
    // Stored profile no longer exists; bounce to picker.
    return <Navigate to="/profiles" replace />;
  }

  if (profile.status === 'unselected' || profile.user === null) {
    if (location.pathname !== '/profiles') {
      return <Navigate to="/profiles" replace />;
    }
    return (
      <Routes>
        <Route path="/profiles" element={<ProfilePicker onSelect={profile.selectProfile} />} />
        <Route path="*" element={<Navigate to="/profiles" replace />} />
      </Routes>
    );
  }

  // Logged-in shell with header.
  const user = profile.user;
  return (
    <>
      <Header user={user} onSwitch={profile.clearProfile} />
      <Routes>
        <Route path="/" element={<Dashboard user={user} />} />
        <Route path="/chat" element={<Chat user={user} />} />
        <Route path="/curriculum" element={<Curriculum currentUser={user} />} />
        {user.is_admin && <Route path="/family" element={<Family />} />}
        <Route
          path="/settings"
          element={<Settings currentUser={user} onChanged={profile.refresh} />}
        />
        <Route path="/profiles" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}

export function App(): JSX.Element {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}
