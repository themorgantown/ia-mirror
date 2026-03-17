"""Flask routes for ia-mirror Web UI API."""

import os
import json
from flask import request, jsonify, send_file, render_template, abort
from flask_socketio import emit
from .parsing import parse_batch_input, validate_destination, safe_join
from .metadata import fetch_metadata
import shutil
import mimetypes



def register_routes(app, storage, worker, socketio, watcher=None):
    """Register all API routes."""
    
    def get_json_data():
        """Safely parse JSON payloads without raising 415 errors."""
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def get_system_info():
        """Get system-level status info."""
        has_creds = (
            (os.getenv('IA_ACCESS_KEY') and os.getenv('IA_SECRET_KEY')) or
            os.path.exists(os.path.expanduser('~/.config/ia/ia.ini'))
        )
        return {
            'has_credentials': bool(has_creds)
        }

    def parse_json_field(value, default=None):
        """Safely parse JSON-encoded DB fields."""
        if default is None:
            default = {}
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return default
        return default

    def get_identifier_download_path(destdir, identifier):
        """Resolve canonical item path under destination directory."""
        safe_dest = destdir or '/downloads'
        safe_identifier = identifier or ''
        return os.path.join(safe_dest, safe_identifier)

    def get_folder_size_bytes(path):
        """Calculate directory size recursively."""
        if not path or not os.path.exists(path):
            return 0
        total = 0
        for root, _, files in os.walk(path):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                try:
                    total += os.path.getsize(file_path)
                except OSError:
                    continue
        return total

    # ============ Root / UI ============
    
    @app.route('/')
    def index():
        """Serve the main UI page."""
        return render_template('index.html')
    
    # ============ Configuration ============
    
    @app.route('/api/config', methods=['GET'])
    def get_config():
        """Get current configuration."""
        config = storage.get_all_config()
        defaults = {
            'destination': os.getenv('IA_DESTDIR', '/downloads'),
            'operation': 'download',
            'verify_checksums': os.getenv('IA_CHECKSUM') == '1' or os.getenv('IA_VERIFY_MODE') == 'checksum',
            'dry_run': os.getenv('IA_DRY_RUN') == '1' or os.getenv('IA_DRY_RUN', '').lower() == 'true',
            'concurrency': int(os.getenv('IA_CONCURRENCY', '4')),
            'max_mbps': float(os.getenv('IA_MAX_MBPS')) if os.getenv('IA_MAX_MBPS') else None,
            'glob_pattern': os.getenv('IA_GLOB', '*'),
            'verify_only': os.getenv('IA_VERIFY_ONLY') == '1' or os.getenv('IA_VERIFY_ONLY', '').lower() == 'true',
            'collection_mode': os.getenv('IA_COLLECTION') == '1' or os.getenv('IA_COLLECTION', '').lower() == 'true',
            'log_level': os.getenv('IA_LOG_LEVEL', 'INFO'),
            'sync_mode': False,
            'ignore_existing': False,
            'verify_mode': 'size',
            'file_formats': None,
            'exclude_pattern': None,
            'retries': 5,
            'source': None,
            'assumed_mbps': 100,
            'cost_per_gb': 0,
            'no_directories': False,
            'resumefolders': False,
            'no_lock': False,
            'no_backoff': False,
        }
        defaults.update(config)
        return jsonify(defaults)
    
    @app.route('/api/config', methods=['POST'])
    def set_config():
        """Save configuration."""
        data = get_json_data()
        for key, value in data.items():
            storage.set_config(key, json.dumps(value) if not isinstance(value, str) else value)
            
            # Special case: sync credentials to environment
            if key == 'ia_access_key':
                os.environ['IA_ACCESS_KEY'] = value
            elif key == 'ia_secret_key':
                os.environ['IA_SECRET_KEY'] = value
                
        return jsonify({'status': 'ok'})

    @app.route('/api/maintenance/clear-history', methods=['POST'])
    def clear_history():
        """Delete all job history."""
        # This only deletes completed/failed jobs, not queued or running
        storage.clear_all_history()
        return jsonify({'status': 'ok'})
    
    @app.route('/api/destinations', methods=['GET'])
    def list_destinations():
        """List available subdirectories under /downloads."""
        data_dir = '/downloads'
        subdirs = []
        try:
            if os.path.exists(data_dir):
                for item in os.listdir(data_dir):
                    path = os.path.join(data_dir, item)
                    if os.path.isdir(path) and not item.startswith('.'):
                        subdirs.append(f"/downloads/{item}")
        except Exception:
            pass
        return jsonify({'destinations': ['/downloads'] + subdirs})
    
    @app.route('/api/destinations/validate', methods=['POST'])
    def validate_dest():
        """Validate a destination path."""
        data = get_json_data()
        path = data.get('path', '/downloads')
        
        if not validate_destination(path):
            return jsonify({'valid': False, 'error': 'Invalid destination path'}), 400
        
        # Check if writable
        try:
            os.makedirs(path, exist_ok=True)
            test_file = os.path.join(path, '.ia-test')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            return jsonify({'valid': True})
        except Exception as e:
            return jsonify({'valid': False, 'error': str(e)}), 400

    # ============ File Browser ============

    @app.route('/api/files/list', methods=['GET'])
    def list_files():
        """List files in a directory."""
        path = request.args.get('path', '')
        # Security check: Ensure path is within /downloads
        # Normalize and ensure it starts with /downloads, but we treat 'path' as relative to /downloads unless it is absolute /downloads
        
        base_dir = os.path.abspath('/downloads')
        
        # If path is empty, listing root
        if not path or path == '/':
             req_path = base_dir
        else:
            # Prevent directory traversal using safe_join
            try:
                req_path = safe_join(base_dir, path.lstrip('/'))
            except ValueError:
                return jsonify({'error': 'Access denied'}), 403

        if not os.path.exists(req_path):
             return jsonify({'error': 'Path not found'}), 404
        
        if not os.path.isdir(req_path):
             return jsonify({'error': 'Not a directory'}), 400

        items = []
        try:
            with os.scandir(req_path) as it:
                for entry in it:
                    if entry.name.startswith('.'):
                        continue
                    
                    stat = entry.stat()
                    items.append({
                        'name': entry.name,
                        'path': os.path.join(path, entry.name).lstrip('/'),
                        'type': 'directory' if entry.is_dir() else 'file',
                        'size': stat.st_size,
                        'mtime': stat.st_mtime
                    })
        except Exception as e:
             return jsonify({'error': str(e)}), 500
        
        # Sort: directories first, then files
        items.sort(key=lambda x: (x['type'] != 'directory', x['name'].lower()))
        
        return jsonify({
            'path': path if path else '/',
            'items': items
        })

    @app.route('/api/files/download', methods=['GET'])
    def download_file():
        """Download a file."""
        path = request.args.get('path', '')
        base_dir = os.path.abspath('/downloads')
        try:
            safe_path = safe_join(base_dir, path.lstrip('/'))
        except ValueError:
            return jsonify({'error': 'Access denied'}), 403

        if not os.path.isfile(safe_path):
             return jsonify({'error': 'File not found'}), 404

        return send_file(safe_path, as_attachment=True)

    @app.route('/api/files/delete', methods=['POST'])
    def delete_item():
        """Delete a file or directory."""
        data = get_json_data()
        path = data.get('path')
        if not path:
             return jsonify({'error': 'Path required'}), 400

        base_dir = os.path.abspath('/downloads')
        try:
            safe_path = safe_join(base_dir, path.lstrip('/'))
        except ValueError:
            return jsonify({'error': 'Access denied'}), 403

        if not os.path.exists(safe_path):
             return jsonify({'error': 'Item not found'}), 404

        # Root protection (should be covered by startswith check, but safe_path could be exactly base_dir)
        if safe_path == base_dir:
             return jsonify({'error': 'Cannot delete root downloads directory'}), 403

        try:
            if os.path.isfile(safe_path) or os.path.islink(safe_path):
                os.remove(safe_path)
            elif os.path.isdir(safe_path):
                shutil.rmtree(safe_path)
        except Exception as e:
             return jsonify({'error': str(e)}), 500

        return jsonify({'status': 'ok'})
        
    @app.route('/api/files/content', methods=['GET'])
    def get_file_content():
        """Get file content for preview (text)."""
        path = request.args.get('path', '')
        base_dir = os.path.abspath('/downloads')
        try:
            safe_path = safe_join(base_dir, path.lstrip('/'))
        except ValueError:
            return jsonify({'error': 'Access denied'}), 403

        if not os.path.isfile(safe_path):
             return jsonify({'error': 'File not found'}), 404

        # Check size limit (e.g. 1MB)
        if os.path.getsize(safe_path) > 1024 * 1024:
             return jsonify({'error': 'File too large to preview'}), 400

        try:
             with open(safe_path, 'r', encoding='utf-8', errors='replace') as f:
                 content = f.read()
             return jsonify({'content': content})
        except Exception as e:
             return jsonify({'error': str(e)}), 500

    
    # ============ Status & History ============
    
    @app.route('/api/status', methods=['GET'])
    def get_status():
        """Get current worker status."""
        state = storage.get_worker_state()
        queued_jobs = storage.get_queued_jobs()
        
        active_job = None
        if state.get('active_job_id'):
            active_job = storage.get_job(state['active_job_id'])
            if active_job:
                active_job = dict(active_job)
                if active_job.get('progress'):
                    active_job['progress'] = json.loads(active_job['progress'])
                if active_job.get('config'):
                    active_job['config'] = json.loads(active_job['config'])
        
        return jsonify({
            'active_job': active_job,
            'queue_length': len(queued_jobs),
            'is_processing': state.get('is_processing_queue', False),
            'last_event_at': state.get('last_updated') or state.get('last_event_at'),
            'system': get_system_info()
        })
    
    @app.route('/api/jobs', methods=['GET'])
    def get_jobs():
        """Get all jobs."""
        limit = request.args.get('limit', 100, type=int)
        jobs = storage.get_all_jobs(limit=limit)
        
        result = []
        for job in jobs:
            job = dict(job)
            if job.get('progress'):
                job['progress'] = json.loads(job['progress'])
            if job.get('config'):
                job['config'] = json.loads(job['config'])
            result.append(job)
        
        return jsonify({'jobs': result})

    @app.route('/api/jobs/recent', methods=['GET'])
    def get_recent_jobs():
        """Get recent completed downloads with resolved destination details."""
        days = request.args.get('days', 7, type=int)
        limit = request.args.get('limit', 20, type=int)
        rows = storage.get_recent_downloads(days=days, limit=limit)

        jobs = []
        for row in rows:
            config = parse_json_field(row.get('config'), default={})
            progress = parse_json_field(row.get('progress'), default={})
            destdir = config.get('destdir') or '/downloads'
            identifier = row.get('identifier')
            resolved_path = get_identifier_download_path(destdir, identifier)

            bytes_total = progress.get('bytes_total') or 0
            if not bytes_total:
                bytes_total = get_folder_size_bytes(resolved_path)

            jobs.append({
                'id': row.get('id'),
                'identifier': identifier,
                'status': row.get('status'),
                'completed_at': row.get('completed_at'),
                'resolved_path': resolved_path,
                'bytes_total': bytes_total,
                'destdir': destdir,
            })

        return jsonify({'jobs': jobs, 'days': days})
    
    @app.route('/api/jobs/<int:job_id>', methods=['GET'])
    def get_job(job_id):
        """Get job details."""
        job = storage.get_job(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404
        
        job = dict(job)
        if job.get('progress'):
            job['progress'] = json.loads(job['progress'])
        if job.get('config'):
            job['config'] = json.loads(job['config'])
        
        return jsonify(job)
    
    @app.route('/api/jobs/<int:job_id>/log', methods=['GET'])
    def get_job_log(job_id):
        """Get job log file."""
        job = storage.get_job(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404
        
        job = dict(job)
        config = {}
        if job.get('config'):
            if isinstance(job['config'], str):
                config = json.loads(job['config'])
            else:
                config = job['config']
            
        destdir = config.get('destdir', '/downloads')
        log_path = os.path.join(destdir, job['identifier'], 'ia_download.log')
        
        if not os.path.exists(log_path):
            return jsonify({'error': 'Log not found'}), 404

        # Return JSON content for viewing in modal
        if request.args.get('format') == 'json':
            try:
                # Read last 50KB
                file_size = os.path.getsize(log_path)
                read_size = 1024 * 50
                
                with open(log_path, 'rb') as f:
                    if file_size > read_size:
                        f.seek(-read_size, 2)
                    content = f.read().decode('utf-8', errors='replace')
                    
                return jsonify({
                    'identifier': job['identifier'],
                    'content': content
                })
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        
        return send_file(log_path, as_attachment=True, download_name=f"{job['identifier']}.log")
    
    # ============ Queue Management ============
    
    @app.route('/api/queue/add', methods=['POST'])
    def queue_add():
        """Add one or more items to queue."""
        data = get_json_data()
        # Accept either 'batch_input' (frontend) or 'text' (API)
        text = data.get('batch_input') or data.get('text', '')
        operation = data.get('operation', 'download')
        config = data.get('config', {})
        
        valid, invalid = parse_batch_input(text)
        
        job_ids = []
        for identifier in valid:
            # Fetch metadata
            meta = fetch_metadata(identifier)
            
            job_id = storage.add_job(
                identifier=identifier,
                input_original=identifier,
                operation=operation,
                config=config,
                title=meta.get('title'),
                creator=meta.get('creator'),
                thumbnail_url=meta.get('thumbnail_url')
            )
            job_ids.append(job_id)
        
        # Notify clients
        socketio.emit('queue_update', {
            'queue_length': len(storage.get_queued_jobs())
        }, namespace='/')
        
        return jsonify({
            'job_ids': job_ids,
            'valid_count': len(valid),
            'invalid': invalid
        })
    
    @app.route('/api/queue/reorder', methods=['POST'])
    def queue_reorder():
        """Reorder queue."""
        data = get_json_data()
        job_ids = data.get('job_ids', [])
        
        storage.reorder_queue(job_ids)
        
        socketio.emit('queue_update', {
            'queue_length': len(storage.get_queued_jobs())
        }, namespace='/')
        
        return jsonify({'status': 'ok'})
    
    @app.route('/api/queue/<int:job_id>', methods=['DELETE'])
    def queue_remove(job_id):
        """Remove job from queue."""
        storage.delete_job(job_id)
        
        socketio.emit('queue_update', {
            'queue_length': len(storage.get_queued_jobs())
        }, namespace='/')
        
        return jsonify({'status': 'ok'})
    
    # ============ Job Control ============
    
    @app.route('/api/job/start', methods=['POST'])
    def job_start():
        """Start a job immediately with provided input."""
        data = get_json_data()

        state = storage.get_worker_state()
        active_job = storage.get_job(state['active_job_id']) if state.get('active_job_id') else None
        
        # New immediate execution mode: accept text input
        text = data.get('text', '')
        if text:
            operation = data.get('operation', 'download')
            config = data.get('config', {})
            
            # Parse identifiers
            valid, invalid = parse_batch_input(text)
            
            if not valid:
                return jsonify({'error': 'No valid identifiers found', 'invalid': invalid}), 400
            
            # Add jobs to queue
            job_ids = []
            for identifier in valid:
                # Fetch metadata
                meta = fetch_metadata(identifier)
                
                job_id = storage.add_job(
                    identifier=identifier,
                    input_original=identifier,
                    operation=operation,
                    config=config,
                    title=meta.get('title'),
                    creator=meta.get('creator'),
                    thumbnail_url=meta.get('thumbnail_url')
                )
                job_ids.append(job_id)

            # Ensure processing is enabled when jobs exist in queue
            storage.update_worker_state(is_processing_queue=True)
            
            # Notify clients
            socketio.emit('queue_update', {
                'queue_length': len(storage.get_queued_jobs())
            }, namespace='/')

            response_status = 'queued' if active_job else 'started'
            message = 'Jobs queued behind currently running job.' if active_job else 'Jobs added and processing started.'

            return jsonify({
                'status': response_status,
                'message': message,
                'job_ids': job_ids,
                'valid_count': len(valid),
                'invalid': invalid,
                'active_job': {
                    'id': active_job.get('id'),
                    'identifier': active_job.get('identifier')
                } if active_job else None
            })

        # Legacy mode: start processing existing queue
        queued_jobs = storage.get_queued_jobs()
        if not queued_jobs and not active_job:
            return jsonify({'status': 'idle', 'message': 'No queued jobs to start.'})

        storage.update_worker_state(is_processing_queue=True)
        return jsonify({
            'status': 'queued' if active_job else 'started',
            'message': 'Queue processing is active.'
        })
    
    @app.route('/api/job/stop', methods=['POST'])
    def job_stop():
        """Stop active job."""
        worker.stop_active_job()
        storage.update_worker_state(is_processing_queue=False)
        return jsonify({'status': 'stopped'})
    
    @app.route('/api/jobs/<int:job_id>/unlock', methods=['POST'])
    def job_unlock(job_id):
        """Force unlock a job by removing its lock file."""
        job = storage.get_job(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404
        
        job = dict(job)
        config = {}
        if job.get('config'):
            config = json.loads(job['config'])
            
        destdir = config.get('destdir', '/downloads')
        # Logic to find the lockfile, mirroring process in fetcher.py
        # Check standard location: destdir/identifier/.ia_status/lock.json
        # Also check just destdir/.ia_status/lock.json if identifier is not nested?
        # fetcher.py: status_dir_for(dest) -> dest / ".ia_status"
        # dest is derived as dest_base / identifier usually.
        
        identifier = job['identifier']
        # Reconstruct the likely path used by fetcher:
        # If destdir ends with identifier, use it, else append identifier
        # But here we should trust standard layout: destdir/identifier
        
        # We need to be careful. The fetcher logic is:
        # if args.destdir: ...
        # dest = dest_base / identifier (unless dest_base == /downloads and identifier provided)
        # To match fetcher exactly might be hard without re-implementing its logic.
        # But we can try the most common path.
        
        # Candidate 1: destdir/identifier/.ia_status/lock.json
        p1 = os.path.join(destdir, identifier, '.ia_status', 'lock.json')
        # Candidate 2: destdir/.ia_status/lock.json
        p2 = os.path.join(destdir, '.ia_status', 'lock.json')
        
        removed = False
        if os.path.exists(p1):
            try:
                os.remove(p1)
                removed = True
            except Exception as e:
                return jsonify({'error': f'Failed to remove lock at {p1}: {e}'}), 500
        elif os.path.exists(p2):
             try:
                os.remove(p2)
                removed = True
             except Exception as e:
                return jsonify({'error': f'Failed to remove lock at {p2}: {e}'}), 500
        
        if removed:
            return jsonify({'status': 'unlocked'})
        else:
             return jsonify({'error': 'No lock file found'}), 404
    
    # ============ Collection Watcher ============

    @app.route('/api/watcher/collections', methods=['GET'])
    def watcher_list():
        """List watched collections."""
        cols = storage.get_watched_collections()
        return jsonify({'collections': cols})

    @app.route('/api/watcher/collections', methods=['POST'])
    def watcher_add():
        """Add a collection to watch."""
        data = get_json_data()
        identifier = data.get('identifier')
        watch_type = data.get('watch_type') # new, future, all_future
        
        if not identifier or not watch_type:
            return jsonify({'error': 'Missing identifier or watch_type'}), 400
            
        # Optional: force trigger a check immediately?
        # Or just let the loop pick it up.
        
        storage.add_watched_collection(identifier, watch_type)
        
        # Trigger check immediately in background if possible, or just wait.
        # If we have the watcher instance, we can hint it?
        # watcher.trigger_check(identifier) # If we implemented that.
        # valid types: new, future, all_future
        
        return jsonify({'status': 'ok'})

    @app.route('/api/watcher/collections/<identifier>', methods=['DELETE'])
    def watcher_remove(identifier):
        """stop watching a collection."""
        storage.remove_watched_collection(identifier)
        return jsonify({'status': 'ok'})

    # ============ WebSocket Events ============
    
    @socketio.on('connect', namespace='/')
    def handle_connect():
        """Client connected."""
        # Send current status
        state = storage.get_worker_state()
        queued_jobs = storage.get_queued_jobs()
        
        emit('status_update', {
            'queue_length': len(queued_jobs),
            'is_processing': state.get('is_processing_queue', False),
            'system': get_system_info()
        })
    
    @socketio.on('request_status', namespace='/')
    def handle_request_status():
        """Client requested current status."""
        state = storage.get_worker_state()
        queued_jobs = storage.get_queued_jobs()
        
        active_job = None
        if state.get('active_job_id'):
            active_job = storage.get_job(state['active_job_id'])
            if active_job:
                active_job = dict(active_job)
                if active_job.get('progress'):
                    active_job['progress'] = json.loads(active_job['progress'])
        
        emit('status_update', {
            'active_job': active_job,
            'queue_length': len(queued_jobs),
            'is_processing': state.get('is_processing_queue', False),
            'system': get_system_info()
        })
