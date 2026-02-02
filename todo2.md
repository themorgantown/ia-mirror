# Simplified Web Interface Plan for ia-mirror

## Overview
Simplify the web interface to focus on batch downloading with minimal UI:
- Remove queue management UI, frontend logging, file browser, collection watcher, history display
- Keep batch URL input with validation and a single Start/Stop button
- Keep all advanced options selectable (mirror, glob matching, concurrency, etc.)
- Keep operation dropdown (download/mirror/sync), verify checksums, dry run checkboxes
- Remove destination directory selector (use fixed default `/downloads`)
- Backend queue system remains functional but hidden from UI

## Specific File Changes

### 1. HTML Template (`docker/templates/index.html`)
**Remove:**
- Navigation tabs shows only 'Download' and 'Watching. Remove 'files' and 'logs' tabs (lines 50-70)
- remove 'Files tab content (lines 370-418)
- Watching tab content (lines 420-444)
- Queue section card (lines 300-310)
- Logs card (lines 312-350)
- History card (lines 352-366)
- Destination directory selector (lines 111-120)
- Browse button (line 118)
- Donation alert (lines 89-101)
- Advanced dropdown menu for collection watcher (lines 240-252)
- File checklist card (lines 286-298)

**Modify:**
- Keep Input Card (lines 106-257) but simplify:
  - Remove destination directory section
  - Keep batch textarea, operation dropdown, checkboxes
  - Keep advanced options collapsible section
  - Keep Start, Stop, Clear buttons (remove dropdown button)
- Keep Compact Active Job Card (lines 260-284) for progress display
- Remove tab navigation entirely - single page interface

### 2. JavaScript (`docker/static/js/app.js`)
**Remove functions:**
- `setupTabListeners()` (lines 91-114) - no tabs needed
- File browser functions: `browseDest()`, related event listeners
- Watcher functions: `addWatcher()`, `loadWatchedCollections()`, `removeWatcher()`
- Queue UI functions: `updateQueueUI()`, `removeFromQueue()`
- Log functions: `downloadLogs()`, `addLogLine()`, log-related listeners
- Donation functions: `checkDonationMilestones()`, `showDonationAlert()`
- Credentials warning display logic

**Modify functions:**
- `setupEventListeners()`: Remove watcher, browse, download-logs, auto-scroll listeners
- `setupSocketListeners()`: Remove `queue_update` handler if not needed for UI
- `updateUI()` (lines 420-450): Simplify to only show basic status, remove queue/logs/history updates
- `getConfig()`: Remove destination field, use fixed default
- Keep batch validation, job start/stop, progress updates, basic status updates

### 3. CSS (`docker/static/css/styles.css`)
**Cleanup:**
- Remove styles for removed components (logs container, file browser tables, watcher UI)
- Simplify responsive styles for single-column layout
- Remove unused utility classes

### 4. Backend (`docker/web/routes.py`)
**Keep functional but UI won't call:**
- `/api/job/start` - Start jobs. We're just going to add any items in the input field into the queue with default destination `/downloads`. Show a simplified progress bar in the UI with a countdown estimate. 
- `/api/job/stop` - Stop jobs
- `/api/status` - Get status
- `/api/config` - Get/set config
- WebSocket events for real-time updates

**Endpoints to keep for compatibility (UI won't use):**
- `/api/queue/*` - Backend queue management
- `/api/jobs/*` - Job history storage
- `/api/files/*` - File browser (for potential future use)
- `/api/watcher/*` - Collection watcher (for potential future use)

**Note:** Backend queue system remains intact, processing jobs sequentially.

## Implementation Steps

### Phase 1: Simplify HTML
1. Create backup of current `index.html`
2. Remove all marked sections from HTML file
3. Simplify Input Card:
   - Remove destination directory section
   - Remove advanced dropdown button
   - Keep Start, Stop, Clear buttons in simple layout
4. Test rendering to ensure no broken layout

### Phase 2: Simplify JavaScript
1. Create backup of current `app.js`
2. Remove all marked functions
3. Simplify `IAMirrorUI` class:
   - Remove queue, logs, watcher, file browser properties
   - Keep only batch input, job control, progress tracking
4. Update event listeners to match simplified UI
5. Test basic functionality: input validation, job start/stop

### Phase 3: Cleanup CSS
1. Remove CSS rules for removed components
2. Ensure simplified layout is responsive
3. Test on different screen sizes

### Phase 4: Verify Backend Compatibility
1. Ensure `/api/job/start` accepts simplified config (without destination)
2. Update route to use default `/downloads` if destination not provided
3. Test job submission and processing

### Phase 5: Update Tests
1. Update `tests/test_ui.py` to test simplified interface
2. Remove tests for removed features (file browser, watcher)
3. Add tests for simplified workflow
4. Run test suite to ensure everything works

### Phase 6: Integration Testing
1. Test complete workflow: batch input → start → progress → completion
2. Test all advanced options (mirror, glob patterns, concurrency, etc.)
3. Test error handling
4. Test responsive design

## Testing Approach

### Unit Tests
1. **Batch Input Validation**: Test URL/identifier parsing
2. **Job Control**: Test start/stop functionality
3. **Config Generation**: Test advanced options serialization
4. **Progress Updates**: Test real-time progress display

### Integration Tests
1. **Full Workflow**: Submit batch job, monitor progress, verify completion
2. **Advanced Options**: Test mirror mode, glob patterns, concurrency limits
3. **Error Scenarios**: Test invalid identifiers, network errors

### Manual Testing
1. **UI Responsiveness**: Test on mobile and desktop
2. **User Experience**: Verify interface is intuitive
3. **Backward Compatibility**: Ensure existing configurations still work

## Critical Files
1. `/Users/daniel/Documents/GitHub/ia-mirror/docker/templates/index.html` - Main HTML template
2. `/Users/daniel/Documents/GitHub/ia-mirror/docker/static/js/app.js` - Frontend JavaScript
3. `/Users/daniel/Documents/GitHub/ia-mirror/docker/web/routes.py` - Backend API endpoints
4. `/Users/daniel/Documents/GitHub/ia-mirror/docker/static/css/styles.css` - CSS styles
5. `/Users/daniel/Documents/GitHub/ia-mirror/tests/test_ui.py` - UI tests

## Success Criteria
1. Interface shows only batch input, start button, and advanced options
2. No queue, logs, file browser, or watcher UI elements visible
3. Jobs can be submitted and progress monitored
4. All advanced options (mirror, glob matching, etc.) are selectable and work
5. Backend queue system continues to process jobs sequentially
6. Tests pass for simplified interface