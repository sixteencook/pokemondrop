"""Service de captures d'écran (Playwright), consommateur de l'Event Bus."""

from .capture import CaptureRequest, CaptureResult, PageCapturer
from .policy import is_screenshot_worthy
from .service import PENDING_FLAG, ScreenshotService

__all__ = [
    "PENDING_FLAG",
    "CaptureRequest",
    "CaptureResult",
    "PageCapturer",
    "ScreenshotService",
    "is_screenshot_worthy",
]
