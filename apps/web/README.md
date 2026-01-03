# Long-Form Content Intelligence Engine UI

## Setup

```bash
cd apps/web
npm install
cp .env.local.example .env.local
```

Edit `.env.local` to set default API values for local development:

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_API_KEY=optional
```

Use the Settings button in the UI to update the API base URL and key at runtime. These
values live in browser storage, so treat any key as public.

## Run

```bash
cd apps/web
npm run dev
```

Open http://localhost:3000 in your browser.

## Environment variables

Environment variables are used as defaults only. The UI settings drawer stores values in
browser `localStorage`, and anything in the browser is public. Use short-lived, limited
keys and rotate them as needed.

- `NEXT_PUBLIC_API_BASE_URL` (optional): Default base URL for the API. Defaults to `http://localhost:8000`.
- `NEXT_PUBLIC_API_KEY` (optional): Default API key sent as `X-API-Key` on every request.
