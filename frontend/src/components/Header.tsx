import { Link, NavLink, useNavigate } from 'react-router-dom';
import type { User } from '../api/users';

interface HeaderProps {
  user: User;
  onSwitch: () => void;
}

export function Header({ user, onSwitch }: HeaderProps): JSX.Element {
  const navigate = useNavigate();

  const handleSwitch = () => {
    onSwitch();
    navigate('/profiles');
  };

  return (
    <header className="app-header">
      <div className="app-header__inner">
        <Link to="/" className="app-header__brand">
          Japanese Study
        </Link>
        <nav className="app-header__nav" aria-label="Primary">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/chat">Practice</NavLink>
          <NavLink to="/curriculum">Curriculum</NavLink>
          {user.is_admin && <NavLink to="/family">Family</NavLink>}
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <div className="app-header__profile">
          <span className="app-header__name">{user.name}</span>
          <button type="button" className="link-button" onClick={handleSwitch}>
            Switch profile
          </button>
        </div>
      </div>
    </header>
  );
}
