"""Citation URL extraction and rot probing helpers for drift detection."""
from __future__ import annotations

import re


URL_PATTERN = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+')


def extract_urls(text: str) -> list[str]:
    """Extract HTTP(S) and scheme-less www. URLs from text."""
    return URL_PATTERN.findall(text)


def probe_urls_for_rot(urls: list[str], *, timeout: float) -> list[tuple[str, str]]:
    """HTTP HEAD each URL and return (url, reason) pairs for broken links."""
    import urllib.error
    import urllib.request

    broken: list[tuple[str, str]] = []
    for url in urls:
        # URL_PATTERN also matches scheme-less www. citations. urlopen()
        # raises ValueError("unknown url type") on those, which would
        # otherwise abort the entire scan. Normalize bare www. to https://
        # before probing, and keep the original url string for reporting.
        probe_url = url if "://" in url else f"https://{url}"
        req = urllib.request.Request(probe_url, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status >= 400:
                    broken.append((url, f"HTTP {resp.status}"))
        except urllib.error.HTTPError as exc:
            if exc.code >= 400:
                broken.append((url, f"HTTP {exc.code}"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            broken.append((url, f"{type(exc).__name__}"))
    return broken


__all__ = ["URL_PATTERN", "extract_urls", "probe_urls_for_rot"]
