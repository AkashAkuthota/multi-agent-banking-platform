"""Privacy-conscious HTTP request IDs and access telemetry."""

import logging
import time
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("app.http")


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    """Log request metadata without bodies, headers, query strings, or IDs."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as error:
            logger.warning(
                "http.request.failed request_id=%s method=%s route=%s "
                "error_type=%s duration_ms=%.2f",
                request_id,
                request.method,
                _route_template(request),
                type(error).__name__,
                (time.perf_counter() - started) * 1000,
            )
            raise

        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http.request.completed request_id=%s method=%s route=%s "
            "status_code=%d duration_ms=%.2f",
            request_id,
            request.method,
            _route_template(request),
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"
