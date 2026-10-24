import { Menu, Languages, Headphones, ArrowLeft } from 'lucide-react';
import type { User, ViewState } from '@/types';
import { langShort } from '@/types';
import { initials } from '@/lib/utils';

interface AppHeaderProps {
  user: User | null;
  currentView: ViewState;
  onNavigate: (view: ViewState) => void;
  onOpenMobileMenu?: () => void;
  onLogout: () => void;
  onNewSession?: () => void;
}

const titles: Record<string, string> = {
  welcome: 'Home',
  workspace: 'Workspace',
  history: 'Your conversations',
  profile: 'Profile & Settings',
  lobby: 'Before you join',
  insights: 'Conversation recap',
};

export function AppHeader({
  user,
  currentView,
  onNavigate,
  onOpenMobileMenu,
}: AppHeaderProps) {
  const title = titles[currentView] || 'Kinesia';

  return (
    <header className="sticky top-0 z-30 h-16 border-b border-border bg-canvas/80 backdrop-blur-xl flex items-center justify-between px-4 sm:px-8">
      {/* Left: Mobile hamburger, Back button & breadcrumb title */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onOpenMobileMenu}
          className="p-2 rounded-xl text-ink-muted hover:text-ink-primary hover:bg-surface-2 transition-colors lg:hidden"
          aria-label="Open navigation menu"
        >
          <Menu size={20} />
        </button>

        {currentView !== 'welcome' && (
          <button
            type="button"
            onClick={() => onNavigate('welcome')}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-muted hover:text-ink-primary px-2.5 py-1.5 rounded-lg hover:bg-surface-2 border border-border-subtle transition-all"
            title="Back to Home Dashboard"
          >
            <ArrowLeft size={14} />
            <span className="hidden sm:inline">Back</span>
          </button>
        )}

        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-ink-muted hidden sm:inline">Kinesia</span>
          <span className="text-xs text-border hidden sm:inline">/</span>
          <h1 className="text-sm sm:text-base font-semibold text-ink-primary">{title}</h1>
        </div>
      </div>

      {/* Right: Quick Language summary pill + New Session button + Avatar */}
      <div className="flex items-center gap-3">
        {user && (
          <div className="hidden md:flex items-center gap-2 px-3 py-1.5 rounded-full bg-surface-2/70 border border-border text-2xs text-ink-secondary">
            <span className="flex items-center gap-1 font-medium">
              <Languages size={12} className="text-accent" />
              {langShort(user.default_input_lang || 'en')}
            </span>
            <span className="text-ink-muted">→</span>
            <span className="flex items-center gap-1 font-medium">
              <Headphones size={12} className="text-accent-cyan" />
              {langShort(user.default_output_lang || 'hi')}
            </span>
          </div>
        )}

        {user && (
          <button
            type="button"
            onClick={() => onNavigate('profile')}
            className="h-8 w-8 rounded-lg flex items-center justify-center text-xs font-bold text-white shadow-sm ring-2 ring-border hover:ring-accent transition-all"
            style={{ backgroundColor: user.avatar_color || '#10B981' }}
            title="Manage Profile"
          >
            {initials(user.display_name || user.username)}
          </button>
        )}
      </div>
    </header>
  );
}
