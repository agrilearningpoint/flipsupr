# core/proxy.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests

from core.logger import setup_logger

logger = setup_logger("formpilot.proxy")


@dataclass
class ProxyInfo:
    raw_url: str
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    external_ip: Optional[str] = None


def parse_proxy_url(proxy_url: str) -> Optional[ProxyInfo]:
    if not proxy_url:
        return None
    raw = proxy_url.strip()
    if "://" not in raw:
        raw = "http://" + raw
    try:
        u = urlparse(raw)
        if not u.hostname or not u.port:
            return None
        return ProxyInfo(
            raw_url=raw,
            host=u.hostname,
            port=int(u.port),
            username=u.username,
            password=u.password,
        )
    except Exception as exc:
        logger.error("Failed to parse proxy URL: %s", exc)
        return None


class ProxyPool:
    """
    Uses a single rotating gateway (typical residential providers).
    Each new browser session through the gateway usually gets a fresh exit IP.
    """

    def __init__(self, gateway_url: Optional[str] = None) -> None:
        self.gateway_url = (gateway_url or os.getenv("PROXY_GATEWAY", "")).strip()
        self._last: Optional[ProxyInfo] = None

    def enabled(self) -> bool:
        return bool(self.gateway_url)

    def get(self) -> Optional[ProxyInfo]:
        if not self.gateway_url:
            return None
        info = parse_proxy_url(self.gateway_url)
        if not info:
            logger.warning("PROXY_GATEWAY is set but could not be parsed")
            return None
        try:
            proxies = {"http": info.raw_url, "https": info.raw_url}
            r = requests.get("https://api.ipify.org", proxies=proxies, timeout=12)
            if r.ok:
                info.external_ip = r.text.strip()
                logger.info("Proxy exit IP: %s", info.external_ip)
        except Exception as exc:
            logger.warning("Proxy IP probe failed (will still try browser): %s", exc)
        self._last = info
        return info

    def last(self) -> Optional[ProxyInfo]:
        return self._last


def selenium_proxy_extension_bytes(proxy: ProxyInfo) -> Optional[bytes]:
    """Build a minimal MV2 extension in-memory for authenticated proxies."""
    if not proxy.username or not proxy.password:
        return None

    import io
    import zipfile

    manifest = """{
  "version": "1.0.0",
  "manifest_version": 2,
  "name": "FormPilot Proxy Auth",
  "permissions": ["proxy","tabs","unlimitedStorage","storage","<all_urls>","webRequest","webRequestBlocking"],
  "background": {"scripts": ["background.js"]},
  "minimum_chrome_version": "76.0.0"
}"""
    background = f"""
var config = {{
  mode: "fixed_servers",
  rules: {{
    singleProxy: {{ scheme: "http", host: "{proxy.host}", port: parseInt({proxy.port}) }},
    bypassList: ["localhost", "127.0.0.1"]
  }}
}};
chrome.proxy.settings.set({{value: config, scope: "regular"}}, function(){{}});
function callbackFn(details) {{
  return {{ authCredentials: {{ username: "{proxy.username}", password: "{proxy.password}" }} }};
}}
chrome.webRequest.onAuthRequired.addListener(callbackFn, {{urls: ["<all_urls>"]}}, ["blocking"]);
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest)
        zf.writestr("background.js", background)
    return buf.getvalue()
