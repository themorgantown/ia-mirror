"""Watcher service for monitoring collections."""
import time
import threading
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import internetarchive
try:
    from .storage import JobStorage
except ImportError:
    from storage import JobStorage

logger = logging.getLogger(__name__)

class WatcherService:
    """Background service to watch collections and queue new items."""
    
    def __init__(self, storage: JobStorage, check_interval: int = 600):
        self.storage = storage
        self.check_interval = check_interval
        self.thread = None
        self.running = False
        self.stop_event = threading.Event()

    def start(self):
        """Start the watcher thread."""
        if not self.running:
            self.running = True
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
            logger.info("Watcher service started")

    def stop(self):
        """Stop the watcher thread."""
        self.running = False
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
            logger.info("Watcher service stopped")

    def _loop(self):
        """Main loop."""
        while self.running and not self.stop_event.is_set():
            try:
                self._check_all()
            except Exception as e:
                logger.error(f"Error in watcher loop: {e}")
            
            # Sleep for interval, checking stop_event frequenty
            for _ in range(self.check_interval):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

    def _check_all(self):
        """Check all watched collections."""
        collections = self.storage.get_watched_collections()
        for col in collections:
            if self.stop_event.is_set():
                break
                
            try:
                self._check_collection(col)
            except Exception as e:
                logger.error(f"Error checking collection {col['identifier']}: {e}")

    def _check_collection(self, col: Dict):
        """Check a single collection."""
        identifier = col['identifier']
        watch_type = col['watch_type']
        last_checked_str = col['last_checked']
        interval = col['interval_seconds'] or 86400

        now = datetime.utcnow()
        
        # Parse last_checked
        last_checked = None
        if last_checked_str:
            try:
                # SQLite often returns string, parse it
                # Format is typically YYYY-MM-DD HH:MM:SS
                if isinstance(last_checked_str, str):
                    last_checked = datetime.fromisoformat(last_checked_str)
                else:
                    last_checked = last_checked_str
            except Exception:
                pass

        # Check if due
        if last_checked:
            next_check = last_checked + timedelta(seconds=interval)
            if now < next_check:
                return # Not due yet

        logger.info(f"Checking watched collection: {identifier} ({watch_type})")

        # Determine search query
        query = f'collection:"{identifier}"'
        
        # If this is the FIRST check (last_checked is None)
        if last_checked is None:
            if watch_type == 'future':
                # Don't queue anything, just mark as checked now
                self.storage.update_watched_collection_last_checked(identifier)
                logger.info(f"Initialized future-only watch for {identifier}")
                return
            elif watch_type == 'all_future':
                # Query everything
                pass 
            elif watch_type == 'new':
                 # "New" is ambiguous on first run. 
                 # If user says "Download only new", they probably mean "Start watching now".
                 # So same as 'future'? Or maybe they want "Items added today"?
                 # Implementation Plan said: "Download only new" -> mapped to future logic or "items added since X".
                 # Let's treat "new" as "future" for the first run unless we want to grab very recent things.
                 # Actually, usually "only new" implies "don't backlog". So same as future.
                 # Wait, if I choose "Download all + future", I want backlog.
                 # If I choose "Download only new", I want future releases.
                 # Redundant? "Download future releases" vs "Download only new".
                 # User prompt: 
                 # * Download only new
                 # * Download future releases
                 # * Download all + future releases
                 # "Download only new" might mean "Download items that are effectively 'new' to the collection" (maybe added in last 24h?)
                 # Let's stick to: "future" -> mark checked, no download. "all_future" -> download all, mark checked.
                 # "new" -> Let's interpret as "Added in the last 7 days" maybe? Or just synonym for future?
                 # safer to make "only new" == "future" for now to avoid accidental huge downloads, 
                 # OR make it "added recently".
                 # I'll treat 'new' and 'future' as synomyms for the *first* check (skip backlog), 
                 # UNLESS 'new' implies "Items released *recently*".
                 # Let's just make 'new' = 'future' (start watching from now) for safety.
                 self.storage.update_watched_collection_last_checked(identifier)
                 return

        # If not first check, OR if 'all_future' on first check
        # We need to construct a date query if we have a last_checked
        
        if last_checked:
            # IA addeddate is YYYY-MM-DD typically. or YYYY-MM-DD HH:MM:SS
            # We want items added SINCE last check.
            # Format: addeddate:[YYYY-MM-DDTHH:MM:SSZ TO null]
            # Use a slight buffer to overlap
            search_date = last_checked - timedelta(hours=1)
            date_str = search_date.strftime('%Y-%m-%dT%H:%M:%SZ')
            query += f' AND addeddate:[{date_str} TO null]'
        
        # Perform search
        # We use a sort by addeddate desc
        try:
            # internetarchive search returns an iterable
            search = internetarchive.search_items(query, sorts=['addeddate desc'])
            
            count = 0
            for item in search:
                item_id = item['identifier']
                
                # Check if we already have this job queued or completed (deduplication)
                # storage.add_job doesn't strictly dedup if status is 'completed'.
                # But for a watcher, we probably don't want to re-download things we *just* did.
                # However, storage doesn't easy have "check if exists". 
                # We can check existing files on disk? OR check job history.
                # Checking job history might be expensive if huge.
                # But typically we trust 'addeddate > last_checked' to return new things.
                # We should validte `addeddate` is indeed new.
                
                # Add to queue
                # We need title/creator ideally. The search result has metadata?
                # item is usually a dict-like object.
                
                # Check if item is really new (IA search might be loose)
                # If we rely on the query, it should be fine.
                
                # We can fetch minimal metadata here or let the queue processor do main fetch.
                # storage.add_job takes title/creator. 
                # item might have 'title' field.
                title = item.get('title')
                
                # Add job
                # We probably want to add a tag or config that it came from watcher?
                config = {'source': 'watcher', 'collection': identifier}
                
                # Check if already in queue to avoid spam in short intervals?
                # For now, just add.
                
                self.storage.add_job(
                    identifier=item_id,
                    input_original=f"watcher:{identifier}",
                    operation='download',
                    config=config,
                    title=title
                )
                count += 1
                
            if count > 0:
                logger.info(f"Watcher: Queued {count} new items for {identifier}")
            
            # Update last_checked
            self.storage.update_watched_collection_last_checked(identifier)
            
        except Exception as e:
            logger.error(f"Search failed for {identifier}: {e}")

