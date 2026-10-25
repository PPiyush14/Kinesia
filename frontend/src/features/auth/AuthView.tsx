import { useState } from 'react';
import { motion } from 'framer-motion';
import { Eye, EyeOff, ArrowRight } from 'lucide-react';
import { KinesiaLogo } from '@/components/brand/KinesiaLogo';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { authService, setStoredToken } from '@/services/api';
import { SUPPORTED_LANGUAGES, type User } from '@/types';

interface AuthViewProps {
  pendingRoomCode?: string | null;
  onAuthenticated: (user: User, token: string) => void;
  onToast: (msg: string, tone?: 'success' | 'error' | 'info') => void;
}

export function AuthView({ pendingRoomCode, onAuthenticated, onToast }: AuthViewProps) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  // Login
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');

  // Register
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [regPassword, setRegPassword] = useState('');
  const [inputLang, setInputLang] = useState('en');
  const [outputLang, setOutputLang] = useState('hi');

  const langOptions = SUPPORTED_LANGUAGES.map((l) => ({ value: l.code, label: l.name }));

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!login.trim() || !password) {
      onToast('Enter your credentials', 'error');
      return;
    }
    setLoading(true);
    try {
      const res = await authService.login(login.trim(), password);
      const token = res.token || (res as any).data?.token;
      const user = res.user || (res as any).data?.user || res;
      if (token && user) {
        setStoredToken(token);
        onAuthenticated(user, token);
        onToast(`Welcome back, ${user.display_name || user.username || 'there'}`);
      } else {
        throw new Error('Sign in succeeded but session token was missing.');
      }
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Sign in failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !displayName.trim() || !email.trim() || !regPassword) {
      onToast('Fill in all required fields', 'error');
      return;
    }
    setLoading(true);
    try {
      const res = await authService.register({
        username: username.trim(),
        display_name: displayName.trim(),
        email: email.trim(),
        password: regPassword,
        default_input_lang: inputLang,
        default_output_lang: outputLang,
      });
      const token = res.token || (res as any).data?.token;
      const user = res.user || (res as any).data?.user || res;
      if (token) {
        setStoredToken(token);
        onAuthenticated(user, token);
      } else {
        // If no token in register, automatically login
        const loginRes = await authService.login(username.trim(), regPassword);
        setStoredToken(loginRes.token);
        onAuthenticated(loginRes.user, loginRes.token);
      }
      onToast('Account created — welcome to Kinesia');
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Registration failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col lg:flex-row">
      {/* Brand panel */}
      <div className="relative lg:w-[45%] xl:w-[42%] flex flex-col justify-between p-8 sm:p-12 lg:p-14 overflow-hidden border-b lg:border-b-0 lg:border-r border-border">
        <div className="absolute inset-0 bg-gradient-to-br from-accent/8 via-transparent to-accent-cyan/6 pointer-events-none" />
        <div className="absolute -top-24 -left-24 w-72 h-72 rounded-full bg-accent/10 blur-3xl pointer-events-none" />
        <div className="absolute bottom-0 right-0 w-80 h-80 rounded-full bg-accent-cyan/8 blur-3xl pointer-events-none" />

        <div className="relative z-10">
          <KinesiaLogo size="lg" showTagline animated />
        </div>

        <div className="relative z-10 mt-12 lg:mt-0 space-y-6 max-w-md">
          <h1 className="font-display text-display text-ink-primary tracking-tight">
            Speak in your language.
            <br />
            <span className="text-gradient">They hear in theirs.</span>
          </h1>
          <p className="text-ink-secondary text-base leading-relaxed">
            Live voice translation with real-time captions and conversation intelligence — built so global teams and individuals can communicate naturally and understand each other effortlessly.
          </p>
          <div className="flex flex-wrap gap-2 pt-2">
            {['Live Voice Translation', 'Real-Time Captions', 'Conversation Intelligence', '9+ Languages'].map((t) => (
              <span
                key={t}
                className="text-2xs font-medium tracking-wide px-2.5 py-1 rounded-full bg-surface-2 border border-border text-ink-muted"
              >
                {t}
              </span>
            ))}
          </div>
        </div>

        <p className="relative z-10 hidden lg:block text-2xs text-ink-faint mt-12">
          © {new Date().getFullYear()} Kinesia · Production build
        </p>
      </div>

      {/* Form panel */}
      <div className="flex-1 flex items-center justify-center p-6 sm:p-10">
        <motion.div
          key={mode}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
          className="w-full max-w-[400px]"
        >
          <div className="mb-8">
            <h2 className="font-display text-title text-ink-primary">
              {mode === 'login' ? 'Sign in' : 'Create account'}
            </h2>
            <p className="text-sm text-ink-muted mt-1.5">
              {mode === 'login'
                ? 'Continue to your workspace'
                : 'Set up your profile and preferred languages'}
            </p>
          </div>

          {pendingRoomCode && (
            <div className="mb-4 rounded-xl border border-accent/30 bg-accent-soft p-3 flex items-center justify-between text-xs text-ink-primary">
              <span>You have an invite to join room <strong className="font-mono font-semibold tracking-wider text-accent">{pendingRoomCode}</strong></span>
              <span className="rounded bg-accent/20 px-2 py-0.5 text-2xs font-medium text-accent">Pending</span>
            </div>
          )}

          {mode === 'login' ? (
            <form onSubmit={handleLogin} className="space-y-4">
              <Input
                label="Email or username"
                name="login"
                autoComplete="username"
                value={login}
                onChange={(e) => setLogin(e.target.value)}
                placeholder="you@company.com"
              />
              <Input
                label="Password"
                name="password"
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                rightSlot={
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    className="p-0.5 hover:text-ink-primary transition-colors"
                    tabIndex={-1}
                  >
                    {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                }
              />
              <Button type="submit" className="w-full mt-2" size="lg" loading={loading} rightIcon={<ArrowRight size={16} />}>
                Continue
              </Button>
            </form>
          ) : (
            <form onSubmit={handleRegister} className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <Input
                  label="Username"
                  name="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="alex"
                  autoComplete="username"
                />
                <Input
                  label="Display name"
                  name="display_name"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="Alex R."
                  autoComplete="name"
                />
              </div>
              <Input
                label="Email"
                name="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                autoComplete="email"
              />
              <Input
                label="Password"
                name="reg_password"
                type={showPassword ? 'text' : 'password'}
                value={regPassword}
                onChange={(e) => setRegPassword(e.target.value)}
                placeholder="Min. 6 characters"
                autoComplete="new-password"
                rightSlot={
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    className="p-0.5 hover:text-ink-primary transition-colors"
                    tabIndex={-1}
                  >
                    {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                }
              />
              <div className="grid grid-cols-2 gap-3">
                <Select
                  label="I speak"
                  name="input_lang"
                  value={inputLang}
                  onChange={(e) => setInputLang(e.target.value)}
                  options={langOptions}
                />
                <Select
                  label="I listen in"
                  name="output_lang"
                  value={outputLang}
                  onChange={(e) => setOutputLang(e.target.value)}
                  options={langOptions}
                />
              </div>
              <Button type="submit" className="w-full mt-2" size="lg" loading={loading} rightIcon={<ArrowRight size={16} />}>
                Create account
              </Button>
            </form>
          )}

          <p className="mt-8 text-center text-sm text-ink-muted">
            {mode === 'login' ? (
              <>
                New here?{' '}
                <button
                  type="button"
                  onClick={() => setMode('register')}
                  className="text-accent hover:text-accent-hover font-medium transition-colors"
                >
                  Create an account
                </button>
              </>
            ) : (
              <>
                Already have an account?{' '}
                <button
                  type="button"
                  onClick={() => setMode('login')}
                  className="text-accent hover:text-accent-hover font-medium transition-colors"
                >
                  Sign in
                </button>
              </>
            )}
          </p>
        </motion.div>
      </div>
    </div>
  );
}
