"""Flask routes for ia-mirror Web UI API."""

import os
import json
from flask import request, jsonify, send_file, render_template
from flask_socketio import emit
from flask_socketio import emit
from .parsing import parse_batch_input, validate_destination
from .metadata import fetch_metadata


def register_routes(app, storage, worker, socketio):
    """Register all API routes."""
    
    def get_system_info():
        """Get system-level status info."""
        has_creds = (
            (os.getenv('IA_ACCESS_KEY') and os.getenv('IA_SECRET_KEY')) or
            os.path.exists(os.path.expanduser('~/.config/ia/ia.ini'))
        )
        return {
            'has_credentials': bool(has_creds)
        }

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
        }
        defaults.update(config)
        return jsonify(defaults)
    
    @app.route('/api/config', methods=['POST'])
    def set_config():
        """Save configuration."""
        data = request.json or {}
        for key, value in data.items():
            storage.set_config(key, json.dumps(value) if not isinstance(value, str) else value)
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
        data = request.json or {}
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
            'last_event_at': state.get('last_event_at'),
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
            config = json.loads(job['config'])
            
        destdir = config.get('destdir', '/downloads')
        log_path = os.path.join(destdir, job['identifier'], 'ia_download.log')
        
        if os.path.exists(log_path):
            return send_file(log_path, as_attachment=True, download_name=f"{job['identifier']}.log")
        
        return jsonify({'error': 'Log not found'}), 404
    
    # ============ Queue Management ============
    
    @app.route('/api/queue/add', methods=['POST'])
    def queue_add():
        """Add one or more items to queue."""
        data = request.json or {}
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
        data = request.json or {}
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
        """Start processing queue."""
        state = storage.get_worker_state()
        if state.get('is_processing_queue'):
            return jsonify({'status': 'already_running'}), 400
        
        storage.update_worker_state(is_processing_queue=True)
        # Worker thread will pick up next job
        return jsonify({'status': 'started'})
    
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
