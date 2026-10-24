import {
  Home,
  LayoutGrid,
  History,
  User,
  LogOut,
  Plus,
} from 'lucide-react';
import { SUPPORTED_LANGUAGES, type User as UserType, type ViewState } from '@/types';
import { KinesiaLogo } from '@/components/brand/KinesiaLogo';
import { Button } from '@/components/ui/Button';
import { initials } from '@/lib/utils';

interface SidebarProps {
  user: UserType | null;
  currentView: ViewState;
  onNavigate: (view: ViewState) => void;
  onNewSession: () => void;
  onLogout: () => void;
  isOpenMobile?: boolean;
  onCloseMobile?: () => void;
}

export function Sidebar({
  user,
  currentView,
  onNavigate,
  onNewSession,
  onLogout,
  isOpenMobile,
  onCloseMobile,
}: SidebarProps) {
  const navItems = [
    { id: 'welcome' as ViewState, label: 'Home', icon: Home },
    { id: 'workspace' as ViewState, label: 'Conversations', icon: LayoutGrid },
    { id: 'history' as ViewState, label: 'History & Moments', icon: History },
    { id: 'profile' as ViewState, label: 'Profile & Audio', icon: User },
  ];

  const handleNav = (v: ViewState) => {
    onNavigate(v);
    onCloseMobile?.();
  };

  return (
    <>
      {/* Mobile backdrop */}
      {isOpenMobile && (
        <div
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
          onClick={onCloseMobile}
        />
      )}

      <aside
        className={`fixed top-0 bottom-0 left-0 z-40 w-64 bg-surface-1/95 backdrop-blur-xl border-r border-border flex flex-col justify-between transition-transform duration-300 lg:translate-x-0 ${
          isOpenMobile ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
        }`}
      >
        {/* Top Section */}
        <div className="p-5 space-y-6">
          {/* Brand */}
          <div className="px-1">
            <KinesiaLogo
              size="md"
              showTagline
              onClick={() => handleNav('welcome')}
              className="cursor-pointer"
            />
          </div>

          {/* New Session Quick Action */}
          <Button
            size="md"
            onClick={onNewSession}
            leftIcon={<Plus size={16} />}
            className="w-full shadow-md shadow-accent/15 font-medium"
          >
            New conversation
          </Button>

          {/* Navigation Links */}
          <nav className="space-y-1">
            {navItems.map((item) => {
              const active = currentView === item.id;
              const Icon = item.icon;

              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => handleNav(item.id)}
                  className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all ${
                    active
                      ? 'bg-accent/10 text-accent font-semibold border border-accent/25 shadow-sm'
                      : 'text-ink-secondary hover:text-ink-primary hover:bg-surface-2'
                  }`}
                >
                  <Icon size={16} className={active ? 'text-accent' : 'text-ink-muted'} />
                  <span>{item.label}</span>
                </button>
              );
            })}
          </nav>
        </div>

        {/* Bottom Section: Engine status & User profile widget */}
        <div className="p-4 border-t border-border space-y-3">
          {/* Live Engine Indicator */}
          <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-surface-2/60 border border-border-subtle text-2xs text-ink-muted">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="relative inline-flex rounded-full h-2 w-2 bg-accent" />
              </span>
              <span className="font-medium text-ink-secondary">Speech translation</span>
            </div>
            <span className="font-mono text-accent">{SUPPORTED_LANGUAGES.length} languages</span>
          </div>

          {/* User profile capsule */}
          {user && (
            <div className="flex items-center justify-between gap-2 p-2 rounded-xl bg-surface-2 border border-border">
              <button
                type="button"
                onClick={() => handleNav('profile')}
                className="flex items-center gap-2.5 min-w-0 flex-1 text-left"
                title="Manage Profile"
              >
                <div
                  className="h-8 w-8 rounded-lg flex items-center justify-center text-xs font-bold text-white shadow-sm shrink-0"
                  style={{ backgroundColor: user.avatar_color || '#10B981' }}
                >
                  {initials(user.display_name || user.username)}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold text-ink-primary truncate">
                    {user.display_name || user.username}
                  </p>
                  <p className="text-2xs text-ink-muted truncate">@{user.username}</p>
                </div>
              </button>

              <button
                type="button"
                onClick={onLogout}
                className="p-1.5 rounded-lg text-ink-muted hover:text-danger hover:bg-surface-3 transition-colors"
                title="Sign Out"
              >
                <LogOut size={15} />
              </button>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
