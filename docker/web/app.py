"""Flask application for ia-mirror Web UI."""

import os
import sys
import json
import time
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from .storage import JobStorage
from .queue import QueueWorker
from .watcher import WatcherService
from .parsing import parse_batch_input, validate_destination


def create_app(config=None):
    """Create Flask application."""
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    CORS(app)
    
    # Configuration: read from environment or passed dict
    if config is None:
        config = {}
    
    app.config['SECRET_KEY'] = config.get('SECRET_KEY', os.getenv('WEB_SECRET_KEY', 'dev-secret-key'))
    db_path = config.get('DB_PATH', os.getenv('WEB_DB_PATH', '/downloads/.ia-mirror/ui.db'))
    runner_type = config.get('RUNNER_TYPE', os.getenv('WEB_RUNNER', 'real'))
    
    # Initialize storage
    storage = JobStorage(db_path)
    
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
