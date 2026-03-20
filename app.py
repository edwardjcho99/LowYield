#!/usr/bin/env python3
"""
Canvas Scraper Web Interface - Flask backend
"""

from flask import Flask, render_template, request, jsonify, send_file
from canvas_scraper import CanvasScraper
from pathlib import Path
import os
import io
import zipfile
import threading
import uuid
import shutil
import time
import tempfile

app = Flask(__name__)

# Runtime configuration (override with environment variables in deployment)
MAX_CONTENT_LENGTH_MB = int(os.getenv('MAX_CONTENT_LENGTH_MB', '20'))
MAX_ACTIVE_DOWNLOAD_JOBS = int(os.getenv('MAX_ACTIVE_DOWNLOAD_JOBS', '4'))
DOWNLOAD_JOB_TTL_SECONDS = int(os.getenv('DOWNLOAD_JOB_TTL_SECONDS', '3600'))
DEFAULT_MAX_WORKERS = int(os.getenv('DEFAULT_MAX_WORKERS', '6'))
MAX_ALLOWED_WORKERS = int(os.getenv('MAX_ALLOWED_WORKERS', '10'))

app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH_MB * 1024 * 1024

# Store scraper instance per session
scrapers = {}
download_jobs = {}
download_jobs_lock = threading.Lock()
DOWNLOAD_TEMP_ROOT = Path(tempfile.gettempdir()) / 'lowyield_canvas_downloads'

def get_scraper(session_id):
    """Get or create scraper for this session"""
    return scrapers.get(session_id)

def set_scraper(session_id, scraper):
    """Store scraper for this session"""
    scrapers[session_id] = scraper


def normalize_module_ids(module_id, module_ids):
    """Normalize incoming module selection payload to a plain list of IDs."""
    if not module_ids and module_id:
        module_ids = [module_id]
    if isinstance(module_ids, dict):
        module_ids = module_ids.get('ids', [])
    if module_ids is None:
        module_ids = []
    return [str(mid) for mid in module_ids if mid]


def cleanup_stale_download_jobs():
    """Remove stale/abandoned jobs and temp files to limit memory/disk growth."""
    now = time.time()
    stale_job_ids = []
    with download_jobs_lock:
        for job_id, job in download_jobs.items():
            created_at = job.get('created_at', now)
            if now - created_at > DOWNLOAD_JOB_TTL_SECONDS:
                stale_job_ids.append(job_id)

        for job_id in stale_job_ids:
            job = download_jobs.pop(job_id, None)
            if not job:
                continue
            job_root = job.get('job_root')
            if job_root:
                try:
                    shutil.rmtree(job_root, ignore_errors=True)
                except Exception:
                    pass


def get_active_download_job_count():
    with download_jobs_lock:
        return sum(1 for job in download_jobs.values() if job.get('status') == 'running')


def run_download_job(job_id, session_id, course_id, module_ids, filters, max_workers):
    """Background worker to download selected modules and build zip file."""
    with download_jobs_lock:
        job = download_jobs[job_id]
    try:
        scraper = get_scraper(session_id)
        if not scraper:
            raise RuntimeError('Not connected to Canvas')

        job_root = DOWNLOAD_TEMP_ROOT / session_id / job_id
        files_dir = job_root / 'files'
        files_dir.mkdir(parents=True, exist_ok=True)

        for idx, mid in enumerate(module_ids, start=1):
            with download_jobs_lock:
                job['current_module'] = str(mid)
                job['current_index'] = idx
                job['module_started_at'] = time.monotonic()
            scraper.download_pages_and_pdfs_from_module(
                course_id,
                mid,
                str(files_dir),
                filters=filters,
                max_workers=max_workers,
            )
            with download_jobs_lock:
                job['completed_modules'] = idx

        files_found = any(files for _, _, files in os.walk(files_dir))
        if not files_found:
            raise RuntimeError('No PDFs found for selected module(s)')

        zip_path = job_root / 'canvas_pdfs.zip'
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for root, dirs, files in os.walk(files_dir):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(files_dir)
                    zip_file.write(file_path, arcname)

        with download_jobs_lock:
            job['zip_path'] = str(zip_path)
            job['status'] = 'completed'
    except Exception as e:
        with download_jobs_lock:
            job['status'] = 'failed'
            job['error'] = str(e)


@app.route('/api/download/start', methods=['POST'])
def start_download_job():
    """Start asynchronous download job for one or more modules."""
    try:
        data = request.json
        course_id = data.get('course_id')
        module_id = data.get('module_id')
        module_ids = normalize_module_ids(module_id, data.get('module_ids') or [])
        session_id = data.get('session_id', 'default')
        filters = data.get('filters', [])
        max_workers = data.get('max_workers', DEFAULT_MAX_WORKERS)

        cleanup_stale_download_jobs()

        if not course_id:
            return jsonify({'error': 'course_id required'}), 400
        if not module_ids:
            return jsonify({'error': 'module_id or module_ids required'}), 400
        if not get_scraper(session_id):
            return jsonify({'error': 'Not connected to Canvas'}), 400
        if get_active_download_job_count() >= MAX_ACTIVE_DOWNLOAD_JOBS:
            return jsonify({'error': 'Server is busy. Please wait for current downloads to finish and try again.'}), 429

        try:
            max_workers = int(max_workers)
        except (TypeError, ValueError):
            max_workers = DEFAULT_MAX_WORKERS
        max_workers = max(1, min(max_workers, MAX_ALLOWED_WORKERS))

        job_id = uuid.uuid4().hex
        job_root = DOWNLOAD_TEMP_ROOT / session_id / job_id
        with download_jobs_lock:
            download_jobs[job_id] = {
                'status': 'running',
                'error': None,
                'zip_path': None,
                'session_id': session_id,
                'course_id': str(course_id),
                'total_modules': len(module_ids),
                'completed_modules': 0,
                'current_module': None,
                'current_index': 0,
                'module_started_at': None,
                'created_at': time.time(),
                'job_root': str(job_root),
            }

        worker = threading.Thread(
            target=run_download_job,
            args=(job_id, session_id, course_id, module_ids, filters, max_workers),
            daemon=True,
        )
        worker.start()

        return jsonify({'status': 'started', 'job_id': job_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/download/progress/<job_id>', methods=['GET'])
def get_download_progress(job_id):
    """Get current progress for an asynchronous download job."""
    cleanup_stale_download_jobs()

    with download_jobs_lock:
        job = download_jobs.get(job_id)
        if job:
            job = dict(job)
    if not job:
        return jsonify({'error': 'Download job not found'}), 404

    total = job.get('total_modules', 0)
    completed = job.get('completed_modules', 0)
    in_progress_bonus = 0.0
    if job.get('status') == 'running' and job.get('current_index', 0) > completed:
        module_started_at = job.get('module_started_at')
        if module_started_at:
            elapsed = max(0.0, time.monotonic() - module_started_at)
            expected_seconds_per_module = 35.0
            in_progress_bonus = min(0.95, elapsed / expected_seconds_per_module)
        else:
            in_progress_bonus = 0.1
    percent = (((completed + in_progress_bonus) / total) * 100) if total else 0
    percent = round(max(0.0, min(99.9, percent)), 1)
    if job.get('status') == 'completed':
        percent = 100

    return jsonify({
        'status': job.get('status'),
        'error': job.get('error'),
        'total_modules': total,
        'completed_modules': completed,
        'current_module': job.get('current_module'),
        'current_index': job.get('current_index', 0),
        'percent': percent,
    })


@app.route('/api/download/result/<job_id>', methods=['GET'])
def get_download_result(job_id):
    """Fetch finished zip file for an asynchronous download job."""
    cleanup_stale_download_jobs()

    with download_jobs_lock:
        job = download_jobs.get(job_id)
        if job:
            job = dict(job)
    if not job:
        return jsonify({'error': 'Download job not found'}), 404
    if job.get('status') != 'completed':
        return jsonify({'error': 'Download not completed yet'}), 400

    zip_path = job.get('zip_path')
    if not zip_path or not Path(zip_path).exists():
        return jsonify({'error': 'Download file not found'}), 404

    with open(zip_path, 'rb') as f:
        zip_bytes = f.read()

    try:
        shutil.rmtree(Path(zip_path).parent, ignore_errors=True)
    except Exception:
        pass
    with download_jobs_lock:
        download_jobs.pop(job_id, None)

    return send_file(
        io.BytesIO(zip_bytes),
        mimetype='application/zip',
        as_attachment=True,
        download_name='canvas_pdfs.zip'
    )

@app.route('/')
def index():
    """Serve main page"""
    return render_template('index.html')

@app.route('/api/init', methods=['POST'])
def init_scraper():
    """Initialize scraper with Canvas credentials"""
    try:
        data = request.json
        canvas_url = data.get('canvas_url', 'canvas.instructure.com')
        api_token = data.get('api_token')
        
        if not api_token:
            return jsonify({'error': 'API token required'}), 400
        
        # Ensure URL is properly formatted
        if not canvas_url.startswith('http'):
            canvas_url = f'https://{canvas_url}'
        
        session_id = data.get('session_id', 'default')
        
        try:
            scraper = CanvasScraper(canvas_url, api_token)
            scraper.validate_credentials()
            set_scraper(session_id, scraper)
            return jsonify({'status': 'success', 'message': 'Connected to Canvas'})
        except Exception as e:
            return jsonify({'error': 'Invalid API token or Canvas URL'}), 401
            
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/courses', methods=['POST'])
def get_courses():
    """Get list of courses"""
    try:
        session_id = request.json.get('session_id', 'default')
        scraper = get_scraper(session_id)
        
        if not scraper:
            return jsonify({'error': 'Not connected to Canvas'}), 400
        
        courses = scraper.get_courses()
        return jsonify({
            'status': 'success',
            'courses': courses,
            'count': len(courses)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/modules', methods=['POST'])
def get_modules():
    """Get modules for a course"""
    try:
        data = request.json
        course_id = data.get('course_id')
        session_id = data.get('session_id', 'default')
        
        if not course_id:
            return jsonify({'error': 'course_id required'}), 400
        
        scraper = get_scraper(session_id)
        if not scraper:
            return jsonify({'error': 'Not connected to Canvas'}), 400
        
        modules = scraper.get_course_modules(course_id)
        return jsonify({
            'status': 'success',
            'modules': modules,
            'count': len(modules)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', '5001')),
        debug=os.getenv('FLASK_DEBUG', '0') == '1',
    )
