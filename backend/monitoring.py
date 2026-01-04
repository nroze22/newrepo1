"""
Monitoring and Metrics

Comprehensive monitoring with Prometheus metrics, health checks, and performance tracking.
"""

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from typing import Dict, Any, Callable
import time
import asyncio
from datetime import datetime
import psutil
import structlog

logger = structlog.get_logger(__name__)

# Prometheus Metrics

# Request metrics
request_count = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

request_duration = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration',
    ['method', 'endpoint']
)

# Analysis metrics
analysis_count = Counter(
    'analysis_total',
    'Total analyses performed',
    ['consultant_mode', 'status']
)

analysis_duration = Histogram(
    'analysis_duration_seconds',
    'Analysis duration',
    ['consultant_mode']
)

# Session metrics
active_sessions = Gauge(
    'active_sessions',
    'Number of active sessions'
)

session_duration = Histogram(
    'session_duration_seconds',
    'Session duration'
)

# WebSocket metrics
ws_connections = Gauge(
    'websocket_connections',
    'Active WebSocket connections'
)

ws_messages = Counter(
    'websocket_messages_total',
    'Total WebSocket messages',
    ['type', 'direction']
)

# API metrics
gemini_api_calls = Counter(
    'gemini_api_calls_total',
    'Total Gemini API calls',
    ['status']
)

gemini_api_duration = Histogram(
    'gemini_api_duration_seconds',
    'Gemini API call duration'
)

cache_hits = Counter(
    'cache_hits_total',
    'Total cache hits'
)

cache_misses = Counter(
    'cache_misses_total',
    'Total cache misses'
)

# System metrics
cpu_usage = Gauge(
    'system_cpu_usage_percent',
    'CPU usage percentage'
)

memory_usage = Gauge(
    'system_memory_usage_percent',
    'Memory usage percentage'
)

# Error metrics
errors = Counter(
    'errors_total',
    'Total errors',
    ['type', 'component']
)


class PerformanceMonitor:
    """Monitor and track application performance."""

    def __init__(self):
        self.start_time = time.time()
        self.health_checks: Dict[str, Callable] = {}

    def track_request(self, method: str, endpoint: str, status: int, duration: float):
        """Track HTTP request metrics."""
        request_count.labels(method=method, endpoint=endpoint, status=status).inc()
        request_duration.labels(method=method, endpoint=endpoint).observe(duration)

    def track_analysis(self, consultant_mode: str, status: str, duration: float):
        """Track analysis metrics."""
        analysis_count.labels(consultant_mode=consultant_mode, status=status).inc()
        analysis_duration.labels(consultant_mode=consultant_mode).observe(duration)

    def track_gemini_api(self, status: str, duration: float):
        """Track Gemini API call metrics."""
        gemini_api_calls.labels(status=status).inc()
        gemini_api_duration.observe(duration)

    def track_cache(self, hit: bool):
        """Track cache hit/miss."""
        if hit:
            cache_hits.inc()
        else:
            cache_misses.inc()

    def track_error(self, error_type: str, component: str):
        """Track error occurrence."""
        errors.labels(type=error_type, component=component).inc()

    def update_system_metrics(self):
        """Update system resource metrics."""
        try:
            cpu_usage.set(psutil.cpu_percent())
            memory_usage.set(psutil.virtual_memory().percent)
        except Exception as e:
            logger.error("system_metrics_update_failed", error=str(e))

    def register_health_check(self, name: str, check_func: Callable):
        """Register a health check function."""
        self.health_checks[name] = check_func

    async def run_health_checks(self) -> Dict[str, Any]:
        """Run all health checks."""
        results = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': time.time() - self.start_time,
            'checks': {}
        }

        for name, check_func in self.health_checks.items():
            try:
                if asyncio.iscoroutinefunction(check_func):
                    result = await check_func()
                else:
                    result = check_func()

                results['checks'][name] = {
                    'status': 'pass',
                    'details': result
                }
            except Exception as e:
                logger.error("health_check_failed", check=name, error=str(e))
                results['checks'][name] = {
                    'status': 'fail',
                    'error': str(e)
                }
                results['status'] = 'degraded'

        # Add system metrics
        self.update_system_metrics()
        results['system'] = {
            'cpu_percent': psutil.cpu_percent(),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_percent': psutil.disk_usage('/').percent
        }

        return results

    def get_metrics(self) -> str:
        """Get Prometheus metrics in text format."""
        return generate_latest().decode('utf-8')

    def get_stats(self) -> Dict[str, Any]:
        """Get application statistics."""
        return {
            'uptime_seconds': time.time() - self.start_time,
            'timestamp': datetime.now().isoformat(),
            'system': {
                'cpu_percent': psutil.cpu_percent(),
                'memory_percent': psutil.virtual_memory().percent,
                'disk_percent': psutil.disk_usage('/').percent
            }
        }


# Global monitor instance
monitor = PerformanceMonitor()


class MetricsMiddleware:
    """Middleware to track request metrics."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)

        start_time = time.time()
        method = scope['method']
        path = scope['path']

        # Track system metrics periodically
        monitor.update_system_metrics()

        status = 500  # Default to error

        async def send_wrapper(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.time() - start_time
            monitor.track_request(method, path, status, duration)


def setup_monitoring(app):
    """Setup monitoring for the application."""

    # Register basic health checks
    def check_startup():
        """Check if application started successfully."""
        return {'started': True}

    monitor.register_health_check('startup', check_startup)

    logger.info("monitoring_setup_complete")

    return monitor
