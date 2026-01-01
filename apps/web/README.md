# Long-Form Content Intelligence Engine UI

## Setup

```bash
cd apps/web
npm install
cp .env.local.example .env.local
```

Edit `.env.local` to set the API URL and key:

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_API_KEY=secret
```

## Run

```bash
cd apps/web
npm run dev
```

Open http://localhost:3000 in your browser.

## Environment variables

- `NEXT_PUBLIC_API_BASE_URL` (required): Base URL for the API. Defaults to `http://localhost:8000`.
- `NEXT_PUBLIC_API_KEY` (optional): API key sent as `X-API-Key` on every request.
