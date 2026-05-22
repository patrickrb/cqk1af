# CQK1AF dashboard

React + Vite + TS + Tailwind operator dashboard. Pages: Operate, Log, Settings.
The kill switch and TX approval modal are always mounted outside the router.

## Dev

```powershell
cd web
npm install
npm run dev   # http://127.0.0.1:5173 (proxies /api and /ws to backend on :8000)
```

Run the backend separately:

```powershell
cd ..
.\.venv\Scripts\python.exe -m cqk1af serve --no-mcp
# or with the MCP server too:
.\.venv\Scripts\python.exe -m cqk1af serve
```

## Build

```powershell
npm run build      # outputs dist/
npm run typecheck
```
