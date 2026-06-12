"""Flask application for ia-mirror Web UI."""

import json
import os
import secrets
import sys
import time
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from .storage import JobStorage
from .queue import QueueWorker
from .watcher import WatcherService
from .parsing import parse_batch_input, validate_destination


def _resolve_secret_key(config):
    """Return an explicit or generated secret key for the Flask app."""
    configured_secret = config.get('SECRET_KEY') or os.getenv('WEB_SECRET_KEY')
    if configured_secret:
        return configured_secret

    generated_secret = secrets.token_hex(32)
    print(
        "WARNING: WEB_SECRET_KEY is not set. Generated an ephemeral secret key for this process. "
        "Set WEB_SECRET_KEY for stable sessions across restarts.",
        file=sys.stderr,
    )
    return generated_secret


def create_app(config=None):
    """Create Flask application."""
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    
    # Configuration: read from environment or passed dict
    if config is None:
        config = {}

    cors_origins = config.get('CORS_ORIGINS') or os.getenv('WEB_CORS_ORIGINS')
    if cors_origins:
        origins = [origin.strip() for origin in str(cors_origins).split(',') if origin.strip()]
        if origins:
            CORS(app, origins=origins)

    secret_key = _resolve_secret_key(config)
    app.config['SECRET_KEY'] = secret_key
    db_path = config.get('DB_PATH', os.getenv('WEB_DB_PATH', '/data/ui.db'))
    runner_type = config.get('RUNNER_TYPE', os.getenv('WEB_RUNNER', 'real'))
    
    # Initialize storage
    storage = JobStorage(db_path)
    
    # Reset stuck jobs from previous run
    storage.reset_stuck_jobs()
    
    # Load stored credentials into environment if they exist
    stored_config = storage.get_all_config()
    if stored_config.get('ia_access_key'):
        os.environ['IA_ACCESS_KEY'] = stored_config['ia_access_key']
    if stored_config.get('ia_secret_key'):
        os.environ['IA_SECRET_KEY'] = stored_config['ia_secret_key']
    
    # Initialize queue worker
    worker = QueueWorker(storage, runner_type=runner_type)
    
    # Initialize watcher
    watcher = WatcherService(storage)
    
    # Initialize SocketIO
    socketio = SocketIO(app, cors_allowed_origins="*")
    
    # Wire up callbacks
    def on_job_start(job_id, identifier):
        socketio.emit('job_update', {
            'job_id': job_id,
            'identifier': identifier,
            'status': 'running'
        }, namespace='/')
        socketio.emit('queue_update', {
            'queue_length': len(storage.get_queued_jobs())
        }, namespace='/')
    
    def on_job_log(job_id, line):
        socketio.emit('log_line', {
            'job_id': job_id,
            'line': line,
            'timestamp': time.time()
        }, namespace='/')
    
    def on_job_progress(job_id, progress):
        socketio.emit('job_progress', {
            'job_id': job_id,
            'progress': progress
        }, namespace='/')
    
    def on_job_complete(job_id, status, exit_code):
        socketio.emit('job_update', {
            'job_id': job_id,
            'status': status,
            'exit_code': exit_code
        }, namespace='/')
        socketio.emit('queue_update', {
            'queue_length': len(storage.get_queued_jobs())
        }, namespace='/')
    
    worker.add_callback('on_job_start', on_job_start)
    worker.add_callback('on_job_log', on_job_log)
    worker.add_callback('on_job_progress', on_job_progress)
    worker.add_callback('on_job_complete', on_job_complete)
    
    # Start worker
    worker.start()
    
    # Start watcher
    watcher.start()
    
    # Store references in app context
    app.storage = storage
    app.worker = worker
    app.watcher = watcher
    
    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://archive.org https://*.us.archive.org; "
            "connect-src 'self' ws: wss:"
        )
        return response

    # Register blueprints and routes
    from . import routes
    routes.register_routes(app, storage, worker, socketio, watcher)

    return app, socketio


# Create app and socketio for gunicorn
# Only if not being imported for tests
if os.getenv('FLASK_ENV') != 'testing' and 'pytest' not in sys.modules:
    app, socketio = create_app()


if __name__ == '__main__':
    if 'app' not in globals():
        app, socketio = create_app()
    socketio.run(app, host='0.0.0.0', port=8080, debug=False)
