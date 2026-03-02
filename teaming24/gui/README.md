# Teaming24 GUI

React + Vite frontend for Teaming24 dashboard, chat, task tracking, network management, and marketplace operations.

## Tech Stack

- React 18 + TypeScript
- Vite 5
- TailwindCSS
- Zustand
- React Markdown + remark-gfm

## Development

```bash
npm install
npm run dev
npm run build
npm run preview
```

## Architecture

```
src/
├── components/
│   ├── ChatView.tsx
│   ├── Sidebar.tsx
│   ├── dashboard/
│   │   ├── Dashboard.tsx
│   │   ├── TaskCard.tsx
│   │   ├── TaskDetail.tsx
│   │   ├── TaskPhaseRail.tsx
│   │   ├── TaskTopology.tsx
│   │   ├── Marketplace.tsx
│   │   └── ...
│   └── ...
├── store/
│   ├── agentStore.ts
│   ├── networkStore.ts
│   ├── marketplaceStore.ts
│   └── ...
├── utils/
├── App.tsx
└── main.tsx
```

## Task Flow UI Design

`TaskDetail` uses a three-layer model:

1. Phase Rail (primary): six execution phases as the default view.
2. Timeline (operational): step-level stream with replay controls and speed selection.
3. Topology (advanced): node graph for deep routing diagnostics.

This keeps common monitoring simple while preserving deep debugging capability.

## Vite-Oriented Optimization

- `TaskTopology` is lazy-loaded with `React.lazy` and `Suspense` to reduce initial JS cost.
- Rollup manual chunks split vendor bundles (`vendor-react`, `vendor-ui`, `vendor-state`, `vendor-markdown`, `vendor-syntax`).
- Production builds enable gzip output via `vite-plugin-compression`.

## API Integration

- All `/api/*` requests are proxied by Vite dev server.
- Backend target is resolved from `TEAMING24_PORT` / `TEAMING24_HOST` (or `VITE_API_TARGET` override).
- Config lives in [`vite.config.ts`](./vite.config.ts).
