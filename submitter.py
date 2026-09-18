# bot/submitter.py
from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional
from urllib.parse import urlparse

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core.browser import create_driver, destroy_driver
from core.logger import setup_logger
from core.proxy import ProxyPool

logger = setup_logger("formpilot.submitter")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def human_delay(a: float = 0.4, b: float = 1.2) -> None:
    time.sleep(random.uniform(a, b))


def is_allowed_form_url(url: str) -> bool:
    """Basic safety: only http(s), reject empty/javascript."""
    try:
        p = urlparse(url)
        return p.scheme in {"http", "https"} and bool(p.netloc)
    except Exception:
        return False


@dataclass
class AttemptResult:
    index: int
    success: bool
    proxy_ip: Optional[str] = None
    error: Optional[str] = None
    retries_used: int = 0


@dataclass
class JobState:
    job_id: str
    chat_id: int
    url: str
    total: int
    status: str = "pending"
    completed: int = 0
    failed: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    attempts: List[AttemptResult] = field(default_factory=list)
    last_message: str = ""
    stop_requested: bool = False

    def summary(self) -> str:
        return (
            f"Job `{self.job_id}`\n"
            f"Status: *{self.status}*\n"
            f"URL: `{self.url[:64]}{'…' if len(self.url) > 64 else ''}`\n"
            f"Progress: {self.completed + self.failed}/{self.total} "
            f"(ok={self.completed}, fail={self.failed})\n"
            f"Started: {self.started_at or '-'}\n"
            f"Finished: {self.finished_at or '-'}\n"
            f"Note: {self.last_message or '-'}"
        )


class JobManager:
    """In-memory single-worker job manager."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Optional[JobState] = None
        self._thread: Optional[threading.Thread] = None
        self._history: List[JobState] = []
        self.proxy_pool = ProxyPool()

    def get_status(self) -> str:
        with self._lock:
            if self._current:
                return self._current.summary()
            if self._history:
                return "No active job.\n\nLast job:\n" + self._history[-1].summary()
            return "No jobs yet."

    def request_stop(self) -> str:
        with self._lock:
            if not self._current or self._current.status != "running":
                return "No running job to stop."
            self._current.stop_requested = True
            self._current.last_message = "Stop requested…"
            return f"Stopping job `{self._current.job_id}` after current attempt…"

    def start_job(
        self,
        chat_id: int,
        url: str,
        count: int,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> str:
        if not is_allowed_form_url(url):
            return "Invalid URL. Provide a full https:// link to **your own** test form."

        count = max(1, min(int(count), 100))
        max_jobs = int(os.getenv("MAX_CONCURRENT_JOBS", "1"))

        with self._lock:
            if self._current and self._current.status == "running":
                if max_jobs <= 1:
                    return "A job is already running. Use /stop or wait for it to finish."
            job_id = f"job_{int(time.time())}"
            job = JobState(job_id=job_id, chat_id=chat_id, url=url, total=count, status="running")
            job.started_at = _utc_now()
            self._current = job

        def _run() -> None:
            try:
                self._execute(job, progress_cb)
            finally:
                with self._lock:
                    job.finished_at = _utc_now()
                    if job.status == "running":
                        job.status = "completed"
                    self._history.append(job)
                    self._history = self._history[-10:]
                    self._current = None

        t = threading.Thread(target=_run, name=f"formpilot-{job_id}", daemon=True)
        with self._lock:
            self._thread = t
        t.start()
        return (
            f"Started `{job_id}`\n"
            f"Submissions: {count}\n"
            f"Form: `{url}`\n"
            f"I'll message progress here."
        )

    def _execute(self, job: JobState, progress_cb: Optional[Callable[[str], None]]) -> None:
        logger.info("Job %s start url=%s count=%s", job.job_id, job.url, job.total)
        max_retries = int(os.getenv("MAX_RETRIES_PER_SUBMISSION", "3"))

        for i in range(1, job.total + 1):
            if job.stop_requested:
                job.status = "stopped"
                job.last_message = "Stopped by user"
                if progress_cb:
                    progress_cb(job.summary())
                break

            ok = False
            last_err = None
            proxy_ip = None
            retries_used = 0

            for attempt in range(1, max_retries + 1):
                if job.stop_requested:
                    break
                retries_used = attempt - 1
                try:
                    proxy_ip, err = self._one_submission(job.url)
                    if err is None:
                        ok = True
                        last_err = None
                        break
                    last_err = err
                    logger.warning("Job %s item %s attempt %s failed: %s", job.job_id, i, attempt, err)
                except Exception as exc:
                    last_err = str(exc)
                    logger.exception("Unexpected error")
                time.sleep(1.0)

            result = AttemptResult(
                index=i,
                success=ok,
                proxy_ip=proxy_ip,
                error=None if ok else (last_err or "unknown"),
                retries_used=retries_used,
            )
            job.attempts.append(result)
            if ok:
                job.completed += 1
            else:
                job.failed += 1

            msg = (
                f"{'OK' if ok else 'FAIL'} {i}/{job.total}"
                + (f" | IP {proxy_ip}" if proxy_ip else "")
                + (f" | {last_err}" if last_err and not ok else "")
            )
            job.last_message = msg
            logger.info("Job %s %s", job.job_id, msg)
            if progress_cb and (i == 1 or i == job.total or i % 3 == 0 or not ok):
                try:
                    progress_cb(f"`{job.job_id}` {msg}")
                except Exception:
                    pass

        if job.status == "running":
            job.status = "completed"
        job.last_message = f"Done. ok={job.completed} fail={job.failed}"
        if progress_cb:
            progress_cb(job.summary())
        logger.info("Job %s finished: %s", job.job_id, job.last_message)

    def _one_submission(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """Return (proxy_ip, error_message)."""
        form_selector = os.getenv("FORM_SELECTOR", "div[role='radio']").strip()
        submit_selector = os.getenv("SUBMIT_SELECTOR", "div[role='button']").strip()
        success_selector = os.getenv("SUCCESS_SELECTOR", "").strip()
        success_text = os.getenv("SUCCESS_TEXT", "Your response has been recorded").strip()

        proxy = self.proxy_pool.get() if self.proxy_pool.enabled() else None
        proxy_ip = proxy.external_ip if proxy else None

        driver = None
        ud = None
        ext = None
        try:
            driver, ud, ext = create_driver(proxy)
            driver.get(url)
            human_delay(0.8, 1.5)

            wait = WebDriverWait(driver, 25)
            clicked = False
            try:
                el = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, form_selector)))
                human_delay()
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                human_delay()
                el.click()
                clicked = True
            except Exception:
                try:
                    radios = driver.find_elements(By.CSS_SELECTOR, 'div[role="radio"]')
                    if radios:
                        human_delay()
                        radios[0].click()
                        clicked = True
                except Exception:
                    pass
                if not clicked:
                    try:
                        radios = driver.find_elements(By.CSS_SELECTOR, 'input[type="radio"]')
                        if radios:
                            radios[0].click()
                            clicked = True
                    except Exception:
                        pass

            if not clicked:
                return proxy_ip, f"Could not find form field ({form_selector})"

            human_delay()
            submitted = False
            try:
                candidates = driver.find_elements(By.CSS_SELECTOR, submit_selector)
                for btn in candidates:
                    label = (btn.text or btn.get_attribute("aria-label") or "").strip().lower()
                    if label in {"submit", "send", "next", ""} or "submit" in label or btn.is_displayed():
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        human_delay()
                        try:
                            btn.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", btn)
                        submitted = True
                        break
            except Exception as exc:
                return proxy_ip, f"Submit click failed: {exc}"

            if not submitted:
                try:
                    btn = driver.find_element(
                        By.XPATH,
                        "//*[self::div or self::button or self::span][contains(translate(.,'SUBMIT','submit'),'submit')]",
                    )
                    btn.click()
                    submitted = True
                except Exception:
                    return proxy_ip, f"Could not find submit control ({submit_selector})"

            human_delay(0.8, 1.6)
            page = driver.page_source or ""
            ok = False
            if success_selector:
                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, success_selector))
                    )
                    ok = True
                except TimeoutException:
                    ok = False
            if success_text and success_text.lower() in page.lower():
                ok = True
            if "your response has been recorded" in page.lower():
                ok = True
            if "thank you" in page.lower():
                ok = True

            if not ok:
                cur = (driver.current_url or "").lower()
                if "formresponse" in cur or "submitted" in cur:
                    ok = True

            if not ok:
                return proxy_ip, "No confirmation detected after submit"
            return proxy_ip, None
        except WebDriverException as exc:
            return proxy_ip, f"WebDriver error: {exc.__class__.__name__}"
        except Exception as exc:
            return proxy_ip, str(exc)
        finally:
            destroy_driver(driver, ud, ext)


job_manager = JobManager()
