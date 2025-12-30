"""WebSocket event handlers for Flask-SocketIO."""

from flask_socketio import emit


def setup_socketio_handlers(socketio, storage, worker):
    """Set up WebSocket event handlers."""
    
    @socketio.on('connect', namespace='/')
    def handle_connect():
        """Handle client connection."""
        print(f"Client connected: {request.sid}")
        emit('connection_response', {
            'data': 'Connected to ia-mirror web UI'
        })
    
    @socketio.on('disconnect', namespace='/')
    def handle_disconnect():
        """Handle client disconnection."""
        print(f"Client disconnected: {request.sid}")
    
    @socketio.on('request_status', namespace='/')
    def handle_request_status():
        """Client requested current status."""
        state = storage.get_worker_state()
        queued_jobs = storage.get_queued_jobs()
        
        active_job = None
        if state.get('active_job_id'):
            active_job = storage.get_job(state['active_job_id'])
            if active_job:
                import json
                active_job = dict(active_job)
                if active_job.get('progress'):
                    active_job['progress'] = json.loads(active_job['progress'])
        
        emit('status_update', {
            'active_job': active_job,
            'queue_length': len(queued_jobs),
            'is_processing': state.get('is_processing_queue', False)
        })
