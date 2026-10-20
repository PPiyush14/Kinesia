import React from 'react';

interface TabItem {
  id: string;
  label: string;
}

interface SwitcherProps {
  tabs: TabItem[];
  activeId: string;
  onChange: (id: string) => void;
  className?: string;
}

export const Switcher: React.FC<SwitcherProps> = ({
  tabs,
  activeId,
  onChange,
  className = '',
}) => {
  return (
    <div
      className={`flex bg-sidebar border border-white/10 rounded-xl p-1 gap-1 select-none ${className}`}
    >
      {tabs.map((tab) => {
        const isActive = tab.id === activeId;
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onChange(tab.id)}
            className={`flex-1 py-2 px-3 text-xs font-semibold rounded-lg transition-all duration-150 ${
              isActive
                ? 'bg-surface-elevated text-slate-100 shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
};
