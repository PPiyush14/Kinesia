# Kinesia Frontend

React + TypeScript SPA for the Kinesia real-time multilingual dialogue platform.

## Stack

- React 18 + TypeScript (strict)
- Vite 7 (Node 20.19+)
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
  hooks/             # WebSocket rooms, microphone and playback lifecycle
  services/          # REST API client
  lib/               # utilities
  types/             # shared contracts
```

## Commands

```bash
npm install
npm run dev        # http://localhost:5173 (proxies API to :8765)
npm run build      # writes to ../backend/web
npm run typecheck
```

Requires the Python backend on port 8765 for REST and WebSocket audio.
Set `VITE_API_BASE_URL` before building for a separate Render frontend service.
See [Render deployment](../docs/RENDER_DEPLOYMENT.md) for configuration and checks.
