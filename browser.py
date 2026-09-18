# core/browser.py
from __future__ import annotations

import os
import random
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import undetected_chromedriver as uc

from core.logger import setup_logger
from core.proxy import ProxyInfo, selenium_proxy_extension_bytes

logger = setup_logger("formpilot.browser")

USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def _truthy(v: str | None, default: bool = True) -> bool:
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def create_driver(proxy: Optional[ProxyInfo] = None) -> Tuple[uc.Chrome, Path, Optional[Path]]:
    """Create a fresh isolated Chrome session."""
    user_data_dir = Path(tempfile.mkdtemp(prefix="formpilot_ud_"))
    ext_dir: Optional[Path] = None

    options = uc.ChromeOptions()
    headless = _truthy(os.getenv("HEADLESS"), True)
    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--incognito")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--window-size=1366,900")
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")
    options.add_argument("--lang=en-US,en")

    chrome_bin = os.getenv("CHROME_BIN", "").strip()
    if chrome_bin:
        options.binary_location = chrome_bin

    if proxy:
        if proxy.username and proxy.password:
            ext_dir = Path(tempfile.mkdtemp(prefix="formpilot_ext_"))
            blob = selenium_proxy_extension_bytes(proxy)
            if blob:
                ext_zip = ext_dir / "proxy_auth.zip"
                ext_zip.write_bytes(blob)
                options.add_extension(str(ext_zip))
        else:
            options.add_argument(f"--proxy-server=http://{proxy.host}:{proxy.port}")

    driver_path = os.getenv("CHROMEDRIVER_PATH", "").strip() or None

    try:
        driver = uc.Chrome(
            options=options,
            driver_executable_path=driver_path,
            use_subprocess=True,
            version_main=None,
        )
    except Exception as exc:
        shutil.rmtree(user_data_dir, ignore_errors=True)
        if ext_dir:
            shutil.rmtree(ext_dir, ignore_errors=True)
        logger.exception("Failed to start Chrome: %s", exc)
        raise

    driver.set_page_load_timeout(int(os.getenv("SUBMISSION_TIMEOUT_SEC", "90")))
    logger.info("Chrome started | ud=%s | proxy=%s", user_data_dir.name, bool(proxy))
    return driver, user_data_dir, ext_dir


def destroy_driver(driver, user_data_dir: Optional[Path], ext_dir: Optional[Path]) -> None:
    try:
        if driver is not None:
            driver.quit()
    except Exception:
        pass
    for d in (user_data_dir, ext_dir):
        if d is not None:
            shutil.rmtree(d, ignore_errors=True)
