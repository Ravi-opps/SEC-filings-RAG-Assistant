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

## Deploy on Vercel

This is a Next.js app, so `VITE_*` variables are not read. `BACKEND_URL` is used server-side by `next.config.ts` and must be the public backend origin:

```text
BACKEND_URL=http://16.113.19.229
```

Set it in **Project → Settings → Environment Variables** for both **Production** and **Preview**, then redeploy with a fresh build (environment changes are baked in at build time). No `NEXT_PUBLIC_` prefix is needed because the browser only calls the relative `/api/backend/*` rewrite. Do not add a `vercel.json` rewrite; the rewrite already lives in `next.config.ts`.

After deploying, check `https://<deployment>/api/backend` — it should return `{"service":"rag-backend","docs":"/docs"}`. A localhost value here will fail the Vercel build with a clear error.

## Checks

```powershell
npm run lint
npx tsc --noEmit
npm run build
```
