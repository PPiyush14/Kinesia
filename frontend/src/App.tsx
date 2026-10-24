import { useCallback, useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Sidebar } from '@/components/navigation/Sidebar';
import { AppHeader } from '@/components/navigation/AppHeader';
import { Toast, type ToastTone } from '@/components/ui/Toast';
import { AuthView } from '@/features/auth/AuthView';
import { WelcomeView } from '@/features/welcome/WelcomeView';
import { ProfileView } from '@/features/profile/ProfileView';
import { WorkspaceView } from '@/features/workspace/WorkspaceView';
import { HistoryView } from '@/features/history/HistoryView';
import { LobbyView } from '@/features/lobby/LobbyView';
import { DialogueCanvas } from '@/features/dialogue/DialogueCanvas';
import { InsightsView } from '@/features/insights/InsightsView';
import {
  authService,
  roomService,
  getStoredToken,
  removeStoredToken,
  setStoredToken,
  ApiError,
} from '@/services/api';
import type { Room, SessionInsights, User, ViewState } from '@/types';

export default function App() {
  const [view, setViewState] = useState<ViewState>('auth');
  const [bootstrapping, setBootstrapping] = useState(true);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const [speakingLang, setSpeakingLang] = useState('en');
  const [listeningLang, setListeningLang] = useState('hi');
  const [insights, setInsights] = useState<SessionInsights | null>(null);
  const [toast, setToast] = useState<{ message: string; tone: ToastTone } | null>(null);

  const setView = useCallback((nextView: ViewState) => {
    setViewState(nextView);
    if (nextView !== 'auth') {
      localStorage.setItem('kinesia_view', nextView);
    }
  }, []);

  const [pendingRoomCode, setPendingRoomCode] = useState<string | null>(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const code = params.get('join') || params.get('room');
      return code ? code.trim().toLowerCase() : null;
    } catch {
      return null;
    }
  });

  const showToast = useCallback((message: string, tone: ToastTone = 'success') => {
    setToast({ message, tone });
    window.setTimeout(() => setToast(null), 3200);
  }, []);

  const joinPendingRoom = useCallback(async (code: string) => {
    try {
      const target = await roomService.getRoom(code);
      if (target) {
        if (target.passcode_required) {
          showToast(`Passcode required for room ${code}`, 'info');
          setView('workspace');
          return;
        }
        setRoom(target);
        setInsights(null);
        setView('lobby');
        showToast(`Joined dialogue "${target.title}"`);
        setPendingRoomCode(null);
        return;
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Room not found', 'error');
    }
    setView('welcome');
  }, [showToast, setView]);

  // Bootstrap session from stored token on refresh
  useEffect(() => {
    let cancelled = false;
    const existing = getStoredToken();
    if (!existing || existing === 'undefined' || existing === 'null' || existing.trim() === '') {
      removeStoredToken();
      setToken(null);
      setBootstrapping(false);
      return;
    }
    setToken(existing);
    authService
      .getMe()
      .then((u) => {
        if (cancelled) return;
        setUser(u);
        if (u.default_input_lang) setSpeakingLang(u.default_input_lang);
        if (u.default_output_lang) setListeningLang(u.default_output_lang);
        if (pendingRoomCode) {
          void joinPendingRoom(pendingRoomCode);
        } else {
          const savedView = localStorage.getItem('kinesia_view') as ViewState | null;
          const savedRoomCode = localStorage.getItem('kinesia_room_code');
          if (savedRoomCode && (savedView === 'lobby' || savedView === 'dialogue')) {
            roomService
              .getRoom(savedRoomCode)
              .then((r) => {
                if (cancelled) return;
                if (r && r.is_active) {
                  setRoom(r);
                  setView(savedView);
                } else {
                  localStorage.removeItem('kinesia_room_code');
                  setView('welcome');
                }
              })
              .catch(() => {
                if (cancelled) return;
                localStorage.removeItem('kinesia_room_code');
                setView('welcome');
              });
          } else if (savedView && ['welcome', 'workspace', 'history', 'profile'].includes(savedView)) {
            setView(savedView);
          } else {
            setView('welcome');
          }
        }
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && [401, 403].includes(err.status)) {
          removeStoredToken();
          setToken(null);
          setView('auth');
        } else {
          setRestoreError('The backend is waking up or temporarily unavailable. Your sign-in is saved.');
        }
      })
      .finally(() => { if (!cancelled) setBootstrapping(false); });
    return () => { cancelled = true; };
  }, [joinPendingRoom, pendingRoomCode, setView, restoreAttempt]);

  const handleAuthenticated = (u: User, t: string) => {
    setStoredToken(t);
    setToken(t);
    setUser(u);
    if (u.default_input_lang) setSpeakingLang(u.default_input_lang);
    if (u.default_output_lang) setListeningLang(u.default_output_lang);
    if (pendingRoomCode) {
      void joinPendingRoom(pendingRoomCode);
    } else {
      setView('welcome');
    }
  };

  const handleLogout = () => {
    removeStoredToken();
    setToken(null);
    setUser(null);
    setRoom(null);
    setInsights(null);
    localStorage.removeItem('kinesia_view');
    localStorage.removeItem('kinesia_room_code');
    setView('auth');
  };

  const handleSelectRoom = (r: Room, _isExistingLive: boolean) => {
    setRoom(r);
    localStorage.setItem('kinesia_room_code', r.room_code);
    setInsights(null);
    setView(r.is_active === false || r.status === 'ended' ? 'history' : 'lobby');
  };

  const handleUserUpdated = (updatedUser: User) => {
    setUser(updatedUser);
    if (updatedUser.default_input_lang) setSpeakingLang(updatedUser.default_input_lang);
    if (updatedUser.default_output_lang) setListeningLang(updatedUser.default_output_lang);
  };

  const copyInviteLink = () => {
    if (!room) return;
    const url = `${window.location.origin}/?join=${encodeURIComponent(room.room_code)}`;
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(url)
        .then(() => showToast(`Invite link copied (${room.room_code})`))
        .catch(() => fallbackCopy(url));
    } else {
      fallbackCopy(url);
    }
  };

  const fallbackCopy = (text: string) => {
    try {
      const textArea = document.createElement('textarea');
      textArea.value = text;
      textArea.style.position = 'fixed';
      textArea.style.left = '-9999px';
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();
      document.execCommand('copy');
      document.body.removeChild(textArea);
      showToast(`Invite link copied (${room?.room_code})`);
    } catch {
      window.prompt('Copy invite link:', text);
    }
  };

  if (bootstrapping) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-canvas">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 rounded-full border-2 border-accent border-t-transparent animate-spin" />
          <p className="text-xs text-ink-muted tracking-wide">Loading Kinesia...</p>
        </div>
      </div>
    );
  }

  if (restoreError) {
    return (
      <main className="min-h-screen flex items-center justify-center bg-canvas text-ink-primary p-6">
        <div className="max-w-md text-center space-y-5">
          <p role="alert">{restoreError}</p>
          <button className="rounded-xl bg-accent px-5 py-3 text-canvas font-medium" onClick={() => {
            setRestoreError(null);
            setBootstrapping(true);
            setRestoreAttempt((attempt) => attempt + 1);
          }}>Retry connection</button>
        </div>
      </main>
    );
  }

  // Views that take full viewport without shell

  if (view === 'auth') {
    return (
      <div className="min-h-screen bg-canvas text-ink-primary">
        <AuthView
          pendingRoomCode={pendingRoomCode}
          onAuthenticated={handleAuthenticated}
          onToast={showToast}
        />
        <Toast message={toast?.message ?? null} tone={toast?.tone} />
      </div>
    );
  }

  if (view === 'dialogue' && room && user && token) {
    return (
      <div className="min-h-screen bg-canvas text-ink-primary">
        <DialogueCanvas
          room={room}
          currentUser={user}
          token={token}
          speakingLang={speakingLang}
          listeningLang={listeningLang}
          onUpdateSpeakingLang={setSpeakingLang}
          onUpdateListeningLang={setListeningLang}
          onConcludeSession={(res) => {
            setInsights(res);
            setView('insights');
          }}
          onLeaveRoom={() => {
            setRoom(null);
            setView('workspace');
          }}
          onToast={showToast}
          onCopyInvite={copyInviteLink}
        />
        <Toast message={toast?.message ?? null} tone={toast?.tone} />
      </div>
    );
  }

  // Modern Spotify / Linear Layout Shell
  return (
    <div className="min-h-screen bg-canvas text-ink-primary flex">
      {/* Persistent Left Sidebar */}
      <Sidebar
        user={user}
        currentView={view}
        onNavigate={(nextView) => {
          setRoom(null);
          setInsights(null);
          setView(nextView);
        }}
        onNewSession={() => {
          setRoom(null);
          setInsights(null);
          setView('workspace');
        }}
        onLogout={handleLogout}
        isOpenMobile={mobileMenuOpen}
        onCloseMobile={() => setMobileMenuOpen(false)}
      />

      {/* Main Content Area (offset by sidebar on desktop) */}
      <div className="flex-1 flex flex-col min-w-0 lg:pl-64">
        {/* Top Header */}
        <AppHeader
          user={user}
          currentView={view}
          onNavigate={(nextView) => {
            setRoom(null);
            setInsights(null);
            setView(nextView);
          }}
          onOpenMobileMenu={() => setMobileMenuOpen(true)}
          onLogout={handleLogout}
          onNewSession={() => {
            setRoom(null);
            setInsights(null);
            setView('workspace');
          }}
        />

        {/* Viewport Content */}
        <main className="flex-1 overflow-y-auto">
          <AnimatePresence mode="wait">
            {view === 'welcome' && user && (
              <motion.div
                key="welcome"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              >
                <WelcomeView
                  user={user}
                  onStartConversation={() => setView('workspace')}
                  onJoinWithCode={() => setView('workspace')}
                  onOpenWorkspace={() => setView('workspace')}
                  onOpenProfile={() => setView('profile')}
                  onResumeConversation={handleSelectRoom}
                />
              </motion.div>
            )}

            {view === 'workspace' && (
              <motion.div
                key="workspace"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              >
                <WorkspaceView
                  defaultJoinCode={pendingRoomCode || undefined}
                  onSelectRoom={handleSelectRoom}
                  onBackToHome={() => setView('welcome')}
                  onToast={showToast}
                />
              </motion.div>
            )}

            {view === 'history' && (
              <motion.div
                key="history"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              >
                <HistoryView
                  initialRoomCode={room?.room_code}
                  language={listeningLang}
                  currentUserId={user?.id}
                  onSelectRoom={handleSelectRoom}
                  onBackToHome={() => setView('welcome')}
                  onToast={showToast}
                />
              </motion.div>
            )}

            {view === 'profile' && user && (
              <motion.div
                key="profile"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              >
                <ProfileView
                  user={user}
                  onUserUpdated={handleUserUpdated}
                  onAccountDeleted={handleLogout}
                  onBack={() => setView('welcome')}
                  onToast={showToast}
                />
              </motion.div>
            )}

            {view === 'lobby' && room && (
              <motion.div
                key="lobby"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              >
                <LobbyView
                  room={room}
                  speakingLang={speakingLang}
                  listeningLang={listeningLang}
                  onUpdateSpeakingLang={setSpeakingLang}
                  onUpdateListeningLang={setListeningLang}
                  onEnterRoom={() => setView('dialogue')}
                  onBack={() => {
                    setRoom(null);
                    setView('workspace');
                  }}
                />
              </motion.div>
            )}

            {view === 'insights' && room && (
              <motion.div
                key="insights"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              >
                <InsightsView
                  language={listeningLang}
                  room={room}
                  insights={insights}
                  onReturnToWorkspace={() => {
                    setRoom(null);
                    setInsights(null);
                    setView('workspace');
                  }}
                  onReturnToHome={() => {
                    setRoom(null);
                    setInsights(null);
                    setView('welcome');
                  }}
                  onToast={showToast}
                />
              </motion.div>
            )}
          </AnimatePresence>
        </main>
      </div>

      <Toast message={toast?.message ?? null} tone={toast?.tone} />
    </div>
  );
}
