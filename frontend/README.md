# Filing Room frontend

Next.js 16 client for the SEC Filing RAG backend. See the root [README](../README.md) for the full setup, API, and architecture.

## Run locally

1. Create `.env` with the FastAPI origin:

   ```text
   BACKEND_URL=http://127.0.0.1:8000
   ```

   `next.config.ts` throws if `BACKEND_URL` is missing, so `npm run dev` and `npm run build` fail fast without it.

2. Install and start:

   ```powershell
   npm install
   npm run dev
   ```

3. Open `http://localhost:3000`.

The app rewrites `/api/backend/*` to `BACKEND_URL`, keeps the JWT in `localStorage`, and streams answers from `/query/stream` over SSE. The ingestion dashboard lives at `/dashboard`.

## Checks

```powershell
npm run lint
npx tsc --noEmit
npm run build
```
