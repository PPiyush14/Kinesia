import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  User as UserIcon,
  Palette,
  Languages,
  Check,
  Save,
  ArrowLeft,
  Sparkles,
  RefreshCw,
  Mic,
  Volume2,
  Play,
} from 'lucide-react';
import type { User as UserType } from '@/types';
import { SUPPORTED_LANGUAGES } from '@/types';
import { authService } from '@/services/api';
import { useAudioDevices } from '@/hooks/useAudioDevices';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Card, CardContent, CardHeader } from '@/components/ui/Card';

interface ProfileViewProps {
  user: UserType;
  onUserUpdated: (user: UserType) => void;
  onBack: () => void;
  onToast: (msg: string, tone?: 'success' | 'error' | 'info') => void;
}

const AVATAR_COLORS = [
  { name: 'Emerald', value: '#10B981' },
  { name: 'Cyan', value: '#06B6D4' },
  { name: 'Violet', value: '#8B5CF6' },
  { name: 'Rose', value: '#F43F5E' },
  { name: 'Amber', value: '#F59E0B' },
  { name: 'Blue', value: '#3B82F6' },
  { name: 'Teal', value: '#14B8A6' },
  { name: 'Indigo', value: '#6366F1' },
];

export function ProfileView({ user, onUserUpdated, onBack, onToast }: ProfileViewProps) {
  const [displayName, setDisplayName] = useState(user.display_name || '');
  const [avatarColor, setAvatarColor] = useState(user.avatar_color || '#10B981');
  const [inputLang, setInputLang] = useState(user.default_input_lang || 'en');
  const [outputLang, setOutputLang] = useState(user.default_output_lang || 'hi');
  const [saving, setSaving] = useState(false);
  const [fetching, setFetching] = useState(false);

  const {
    inputs,
    outputs,
    selectedInput,
    selectedOutput,
    changeInput,
    changeOutput,
    refreshDevices,
    requestPermission,
    permissionGranted,
    testSpeaker,
  } = useAudioDevices();

  // Standard API call: fetch fresh user info on mount to guarantee up-to-date data
  useEffect(() => {
    let mounted = true;
    setFetching(true);
    authService
      .getMe()
      .then((freshUser) => {
        if (!mounted) return;
        setDisplayName(freshUser.display_name || freshUser.username);
        setAvatarColor(freshUser.avatar_color || '#10B981');
        if (freshUser.default_input_lang) setInputLang(freshUser.default_input_lang);
        if (freshUser.default_output_lang) setOutputLang(freshUser.default_output_lang);
      })
      .catch((err) => {
        console.error('Failed to refresh profile', err);
      })
      .finally(() => {
        if (mounted) setFetching(false);
      });

    return () => {
      mounted = false;
    };
  }, []);

  const langOptions = SUPPORTED_LANGUAGES.map((l) => ({
    value: l.code,
    label: `${l.name} (${l.short})`,
  }));

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!displayName.trim()) {
      onToast('Display name cannot be empty', 'error');
      return;
    }

    setSaving(true);
    try {
      const updated = await authService.updateProfile({
        display_name: displayName.trim(),
        avatar_color: avatarColor,
        default_input_lang: inputLang,
        default_output_lang: outputLang,
      });

      onUserUpdated(updated);
      onToast('Profile preferences saved successfully', 'success');
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to update profile', 'error');
    } finally {
      setSaving(false);
    }
  };

  const userInitials = (displayName.trim() || user.username || 'K')
    .substring(0, 2)
    .toUpperCase();

  return (
    <div className="mx-auto max-w-4xl px-4 sm:px-6 py-8 sm:py-12 space-y-8">
      {/* Top back button */}
      <div className="flex items-center justify-between">
        <Button
          variant="ghost"
          size="sm"
          onClick={onBack}
          leftIcon={<ArrowLeft size={16} />}
          className="text-ink-muted hover:text-ink-primary"
        >
          Back
        </Button>
        <span className="text-xs text-ink-muted">Account Settings</span>
      </div>

      {/* Header */}
      <div className="space-y-1">
        <h1 className="font-display text-2xl sm:text-3xl font-bold tracking-tight text-ink-primary">
          Profile & Preferences
        </h1>
        <p className="text-sm text-ink-muted">
          Manage your personal profile and customize your default speaking, listening, and audio hardware settings.
        </p>
      </div>

      <form onSubmit={handleSave} className="space-y-6">
        {/* Avatar & Identity Card */}
        <Card className="border border-border bg-surface-1/90 backdrop-blur-md">
          <CardHeader className="border-b border-border-subtle p-5 sm:p-6">
            <div className="flex items-center gap-2">
              <UserIcon size={18} className="text-accent" />
              <h2 className="font-semibold text-ink-primary text-base">Identity & Avatar</h2>
            </div>
          </CardHeader>
          <CardContent className="p-5 sm:p-6 space-y-6">
            <div className="flex flex-col sm:flex-row items-center gap-6">
              {/* Avatar Preview */}
              <div className="relative group">
                <div
                  className="h-24 w-24 rounded-3xl flex items-center justify-center text-2xl font-bold text-white shadow-xl transition-all duration-300 ring-4 ring-surface-2"
                  style={{ backgroundColor: avatarColor }}
                >
                  {userInitials}
                </div>
                <div className="absolute -bottom-1 -right-1 h-7 w-7 rounded-full bg-surface-2 border border-border flex items-center justify-center text-accent">
                  <Sparkles size={14} />
                </div>
              </div>

              {/* Color Picker */}
              <div className="flex-1 space-y-2 text-center sm:text-left">
                <label className="text-xs font-medium text-ink-secondary flex items-center gap-1.5 justify-center sm:justify-start">
                  <Palette size={14} /> Avatar Accent Color
                </label>
                <div className="flex flex-wrap items-center gap-2.5 justify-center sm:justify-start pt-1">
                  {AVATAR_COLORS.map((c) => {
                    const isSelected = avatarColor.toLowerCase() === c.value.toLowerCase();
                    return (
                      <button
                        key={c.value}
                        type="button"
                        onClick={() => setAvatarColor(c.value)}
                        className={`h-8 w-8 rounded-xl flex items-center justify-center transition-all ${
                          isSelected
                            ? 'ring-2 ring-white ring-offset-2 ring-offset-canvas scale-110'
                            : 'hover:scale-105 opacity-80 hover:opacity-100'
                        }`}
                        style={{ backgroundColor: c.value }}
                        title={c.name}
                      >
                        {isSelected && <Check size={14} className="text-white drop-shadow-sm" />}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
              <Input
                label="Display Name"
                name="display_name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Your full name"
                required
              />
              <Input
                label="Username"
                name="username"
                value={`@${user.username}`}
                disabled
                className="opacity-70 cursor-not-allowed"
              />
            </div>

            {user.email && (
              <Input
                label="Email Address"
                name="email"
                value={user.email}
                disabled
                className="opacity-70 cursor-not-allowed"
              />
            )}
          </CardContent>
        </Card>

        {/* Language Defaults Card */}
        <Card className="border border-border bg-surface-1/90 backdrop-blur-md">
          <CardHeader className="border-b border-border-subtle p-5 sm:p-6">
            <div className="flex items-center gap-2">
              <Languages size={18} className="text-accent-cyan" />
              <h2 className="font-semibold text-ink-primary text-base">Language Defaults</h2>
            </div>
          </CardHeader>
          <CardContent className="p-5 sm:p-6 space-y-4">
            <p className="text-xs text-ink-muted">
              These dialects will be pre-selected automatically whenever you create or enter a room.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Select
                  label="I Speak (My Speaking Language)"
                  name="input_lang"
                  value={inputLang}
                  onChange={(e) => setInputLang(e.target.value)}
                  options={langOptions}
                />
                <p className="text-2xs text-ink-muted">
                  The language you speak into your microphone during sessions.
                </p>
              </div>

              <div className="space-y-1.5">
                <Select
                  label="I Listen In (My Translated Hearing Language)"
                  name="output_lang"
                  value={outputLang}
                  onChange={(e) => setOutputLang(e.target.value)}
                  options={langOptions}
                />
                <p className="text-2xs text-ink-muted">
                  The translated speech dialect you wish to read and hear in real-time.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Audio Input & Speaker Output Settings Card */}
        <Card className="border border-border bg-surface-1/90 backdrop-blur-md">
          <CardHeader className="border-b border-border-subtle p-5 sm:p-6 flex flex-row items-center justify-between">
            <div className="flex items-center gap-2">
              <Mic size={18} className="text-accent" />
              <h2 className="font-semibold text-ink-primary text-base">Audio & Speaker Hardware</h2>
            </div>
            <button
              type="button"
              onClick={() => void refreshDevices()}
              className="text-2xs text-ink-muted hover:text-ink-primary flex items-center gap-1 transition-colors"
              title="Refresh connected devices"
            >
              <RefreshCw size={13} />
              <span>Rescan</span>
            </button>
          </CardHeader>
          <CardContent className="p-5 sm:p-6 space-y-4">
            <p className="text-xs text-ink-muted">
              Select your microphone and output speaker. These settings are saved automatically on this device.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Select
                  label="Microphone (Audio Input)"
                  name="audio_input"
                  value={selectedInput}
                  onChange={(e) => changeInput(e.target.value)}
                  options={[
                    { value: 'default', label: 'Default Microphone' },
                    ...inputs.map((d) => ({ value: d.deviceId, label: d.label })),
                  ]}
                />
                {!permissionGranted && (
                  <button
                    type="button"
                    onClick={() => void requestPermission()}
                    className="text-2xs text-accent hover:underline inline-block pt-0.5"
                  >
                    Grant mic permission to display device names
                  </button>
                )}
              </div>

              <div className="space-y-1.5">
                <Select
                  label="Speakers / Headphones (Audio Output)"
                  name="audio_output"
                  value={selectedOutput}
                  onChange={(e) => changeOutput(e.target.value)}
                  options={[
                    { value: 'default', label: 'Default System Speaker' },
                    ...outputs.map((d) => ({ value: d.deviceId, label: d.label })),
                  ]}
                />
                <div className="pt-1">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      testSpeaker(selectedOutput);
                      onToast('Playing test chime through speakers', 'info');
                    }}
                    leftIcon={<Volume2 size={14} />}
                    className="text-xs"
                  >
                    Test Speaker Output
                  </Button>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Submit Actions */}
        <div className="flex items-center justify-end gap-3 pt-2">
          <Button type="button" variant="secondary" onClick={onBack}>
            Cancel
          </Button>
          <Button
            type="submit"
            loading={saving}
            leftIcon={<Save size={16} />}
            className="px-6"
          >
            Save Preferences
          </Button>
        </div>
      </form>
    </div>
  );
}
