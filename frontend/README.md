# Kinesia Frontend

React + TypeScript SPA for the Kinesia real-time multilingual dialogue platform.

## Stack

- React 18 + TypeScript (strict)
- Vite 5
- Tailwind CSS 3
- Framer Motion
- Lucide React

## Structure

```
src/
  components/
    brand/           # Logo
    navigation/      # App header
    ui/              # Button, Input, Card, Badge, Select, Toast
  features/
    auth/
    workspace/
    lobby/
    dialogue/
    insights/
  hooks/             # SSE, speech dictation, audio meter
  services/          # REST API client
  lib/               # utilities
  types/             # shared contracts
```

## Commands

```bash
npm install
npm run dev        # http://localhost:5173 (proxies API to :8765)
npm run build      # writes to ../src/pi_translator/web
npm run typecheck
```

Requires the Python backend on port 8765 for API and SSE.
