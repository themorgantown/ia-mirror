# ia-mirror Web UI Documentation

## Overview

The Web UI is a Flask-based web interface for managing Internet Archive downloads. It provides a modern, responsive interface for batch-queuing IA identifiers, tracking progress in real-time, and viewing job history.

### Key Features

1. **Batch Input**: Paste multiple IA URLs or identifiers (one per line) and enqueue them
2. **Queue Management**: View, reorder, and remove pending jobs
3. **Real-time Status**: Live progress bars, logs, and metrics via WebSocket
4. **Always-on UI**: Queue and history persist across refreshes and container restarts
5. **Responsive Design**: Works on desktop, tablet, and mobile browsers
6. **Mock Runner**: Built-in mock downloader for testing without network

## Running the Web UI

### Default Mode (Web UI Only)

```bash
docker run -d \
  -v /local/mirror:/downloads \
  -p 17865:17865 \
  ia-mirror:latest
```

Access at: `http://localhost:17865`

### With CLI Fallback Disabled

```bash
docker run -d \
  -v /local/mirror:/downloads \
  -p 17865:17865 \
  -e WEB_ENABLED=true \
  ia-mirror:latest
```

### CLI Mode (Web UI Disabled)

```bash
docker run --rm \
  -v /local/mirror:/downloads \
  -e WEB_ENABLED=false \
  ia-mirror:latest \
  --identifier jillem-archive
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WEB_ENABLED` | `true` | Enable web UI (if false, runs CLI fetcher) |
| `WEB_HOST` | `0.0.0.0` | Bind address |
| `WEB_PORT` | `17865` | Web UI port |
| `WEB_DB_PATH` | `/downloads/.ia-mirror/ui.db` | SQLite database path |
| `WEB_RUNNER` | `real` | `real` (run fetcher) or `mock` (test mode) |
| `IA_ACCESS_KEY` | - | Internet Archive access key (optional) |
| `IA_SECRET_KEY` | - | Internet Archive secret key (optional) |

## Usage Guide

### 1. Paste Identifiers

In the "Paste IA URLs or Identifiers" textarea, enter one identifier per line. You can use:

- Raw identifiers: `baberuthstory0000ruth`
- Full URLs: `https://archive.org/details/baberuthstory0000ruth`
- Mixed formats (they'll be normalized)

### 2. Configure Options

**Basic Options** (always visible):
- **Destination**: Where to download files (within `/downloads` container mount)
- **Operation**: download, mirror, or sync
- **Verify checksums**: Verify downloaded files against IA hashes
- **Dry run**: Validate without downloading

**Advanced Options** (click to expand):
- **Concurrency**: Number of parallel threads (1-16)
- **Max Speed**: Limit download speed (Mbps)
- **File Pattern**: Glob pattern to filter files (e.g., `*.mp3`)
- **Verify Only**: Only verify, don't download
- **Collection Mode**: Download entire collections
- **Log Level**: Debug, Info, Warning, or Error

### 3. Start or Clear

- **Start**: Add items to the queue and begin processing immediately
- **Clear**: Clear the input textarea

### 4. Monitor Progress

**Live Status Panel** shows:
- Progress bar with percentage
- Files downloaded / Total files
- Data downloaded / Total data
- Download speed
- Estimated time remaining

**Live Logs** streams output in real-time. Click "Download Logs" to export the current job's log.

### 5. View History

**Job History** table shows all completed and failed jobs with:
- Identifier
- Status (completed/failed/cancelled)
- Start time
- Duration
- Log download button

## API Reference

### Configuration

- `GET /api/config` - Get current configuration defaults
- `POST /api/config` - Save UI configuration
- `GET /api/destinations` - List available subdirectories under `/data`
- `POST /api/destinations/validate` - Validate a destination path

### Status & History

- `GET /api/status` - Get current active job + queue status
- `GET /api/jobs` - List all jobs
- `GET /api/jobs/<id>` - Get job details
- `GET /api/jobs/<id>/log` - Download job log file

### Queue Management

- `POST /api/queue/add` - Add identifiers to queue
  - Body: `{"text": "item1\nitem2", "operation": "download", "config": {...}}`
- `POST /api/queue/reorder` - Reorder queue
  - Body: `{"job_ids": [3, 1, 2]}`
- `DELETE /api/queue/<id>` - Remove job from queue

### Job Control

- `POST /api/job/start` - Start processing queue
- `POST /api/job/stop` - Stop active job

### WebSocket Events

Connect to `/` namespace:

**Client → Server:**
- `request_status` - Request current status update

**Server → Client:**
- `status_update` - Complete status (active job, queue length)
- `job_update` - Job status changed (start/complete)
- `job_progress` - Progress update (files, bytes, speed, eta)
- `log_line` - New log line from active job
- `queue_update` - Queue changed (item added/removed)

## Data Persistence

### Database
- Location: `/downloads/.ia-mirror/ui.db` (SQLite)
- Contains: Job history, queue state, UI config
- Survives container restarts

### File Outputs
Per-item outputs remain in existing locations:
- `/downloads/<identifier>/`
  - `ia_download.log` - Download log
  - `.ia_status/<identifier>.json` - Status snapshot
  - `report.json` - Job report
  - Downloaded files

## Testing

### Run Unit Tests

```bash
cd /Users/daniel/Documents/GitHub/ia-mirror
pytest tests/ui/test_backend.py -v
```

Tests cover:
- URL/identifier parsing and validation
- Destination path validation
- Database operations
- Job queue management
- Mock job runner

### Run Docker Integration Tests

```bash
bash tests/test_ui_docker.sh
```

Tests cover:
- Container startup and health
- API endpoints
- Queue management
- Job processing (with mock runner)
- Input validation
- Job history

## Architecture

### Backend (Python)

- **web/app.py** - Flask application setup
- **web/routes.py** - API endpoints
- **web/queue.py** - Queue worker loop
- **web/jobs.py** - Job execution (real + mock runners)
- **web/storage.py** - SQLite persistence
- **web/parsing.py** - URL/identifier normalization

### Frontend (Vanilla JS)

- **templates/index.html** - HTML structure
- **static/js/app.js** - SocketIO client + UI logic
- **static/css/styles.css** - Responsive styling (Bootstrap 5 + custom)

### Data Model

**Jobs Table:**
```sql
id, identifier, input_original, operation, status, queue_position,
config (JSON), progress (JSON), created_at, started_at, completed_at,
exit_code, error_message, pid
```

**Worker State Table:**
```sql
id, active_job_id, active_pid, is_processing_queue, last_event_at, can_stop
```

**UI Config Table:**
```sql
key (TEXT PRIMARY KEY), value (TEXT)
```

## Security Considerations

1. **No Credential Storage**: IA credentials are only read from env vars or mounted config files
2. **Path Validation**: Destination must be under `/downloads` (no directory traversal)
3. **Input Sanitization**: All user input is validated before use
4. **No Default Secrets**: Default password is unset; set `WEB_PASSWORD` env var if needed
5. **Behind Reverse Proxy**: For public access, use a reverse proxy with HTTPS

## Performance

- **Single Worker**: One active job at a time by default (avoids bandwidth saturation)
- **In-Process Queue**: No external queue service needed
- **Efficient Logging**: Streams logs via WebSocket, limits buffer to 500 lines
- **Low Resource**: Minimal image size with only Flask + eventlet

## Troubleshooting

### Port Already in Use
```bash
docker run -p 9090:8080 ia-mirror:latest
# Access at http://localhost:9090
```

### Database Locked
If you see "database is locked" errors, ensure only one container is accessing the DB at a time.

### Jobs Not Processing
1. Check `WEB_RUNNER` is set to `real` (default)
2. Check IA credentials are set via env vars
3. Check logs: `docker logs <container>`

### WebSocket Connection Issues
If live updates don't work:
1. Check browser console for errors
2. Ensure no firewall is blocking port 8080
3. Try a hard refresh (Ctrl+Shift+R)

## Development

### Local Testing with Mock Runner

```bash
cd docker
docker build -t ia-mirror:dev .
docker run -p 8080:8080 -e WEB_RUNNER=mock ia-mirror:dev
```

The mock runner simulates downloads without network access (useful for UI testing).

### Extending the UI

- Add new API endpoints in `web/routes.py`
- Handle new WebSocket events in `static/js/app.js`
- Update HTML structure in `templates/index.html`
- Style with CSS in `static/css/styles.css`

## Future Enhancements

- Parallel job processing (configurable)
- Notifications (email/webhook on completion)
- Dark mode toggle
- Advanced filtering/search in history
- Statistics and graphs
- API key generation for programmatic access
- Export/import queue as CSV
