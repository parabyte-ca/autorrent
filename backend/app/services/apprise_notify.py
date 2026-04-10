import logging

logger = logging.getLogger(__name__)


def notify(apprise_url: str, title: str, body: str) -> None:
    if not apprise_url:
        return
    try:
        import apprise  # type: ignore

        ap = apprise.Apprise()
        ap.add(apprise_url)
        ap.notify(title=title, body=body)
    except ImportError:
        logger.warning("apprise package not installed — notifications disabled.")
    except Exception as e:
        logger.error("Notification failed: %s", e)
