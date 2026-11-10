# tg-viewer

Browse, search and analyse your Telegram chat history from a Telegram Desktop JSON export.

**Live version:** https://spyobird.github.io/tg-viewer/ — everything runs in your browser (via [Pyodide](https://pyodide.org)); your export is never uploaded anywhere. The first visit downloads a ~12 MB Python runtime (cached afterwards); it starts loading as soon as the page opens.

## Exporting your data from Telegram Desktop

You need **Telegram Desktop** (from [desktop.telegram.org](https://desktop.telegram.org)). The phone apps can't export, and neither can the macOS App Store app called just "Telegram"; on a Mac, install Telegram Desktop.

Whichever export you choose, set **Format** to **Machine-readable JSON**. The default (HTML) won't work with tg-viewer.

### One chat

1. Open the chat (private chat, group or channel).
2. Click the **⋮** menu in the top-right corner, then **Export chat history**.
3. Untick photos, videos, files and so on unless you want them. tg-viewer only reads the text, and leaving media out makes the export much faster.
4. Set **Format** to **Machine-readable JSON**. You can also limit the date range here.
5. Click **Export**. When it finishes, click **Show my data**.

The output is a folder like `ChatExport_2026-11-10/` that contains `result.json`.

### Everything (all chats)

1. Open **Settings → Advanced → Export Telegram data**.
2. Tick the chat types you want: **Personal chats**, **Bot chats**, **Private groups**, **Public groups**, **Private channels**, **Public channels**. Untick media types unless you need them.
3. Set **Format** to **Machine-readable JSON**.
4. Click **Export**. Telegram may ask you to confirm the export from another logged-in device. If this session was opened recently, you may have to wait up to 24 hours before the export is allowed. Telegram says so in a service message, and you can start the export again from that message once the wait is over.

The output is a folder like `DataExport_2026-11-10/` that contains a single `result.json` with every chat in it.

### Loading it

Click **Import result.json** (or drag the file onto the page). A single-chat export shows one chat; a full export lists all of them in the sidebar.

## Running locally

```sh
uv run tg-viewer   # serves on http://127.0.0.1:8000
uv run pytest
```

## Deployment

Every push to `main` runs the tests and deploys to GitHub Pages (`.github/workflows/pages.yml`). The build copies `index.html`, sets `STATIC_SITE = true` and puts the Python sources next to it, so the page runs the same Flask app in the browser.

One-time setup: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
