"""Single scheduler (APScheduler). Runs inside the API process locally and as its own `worker`
service in Compose. Every job goes through `jobs.run`, which enforces one active run per job key
and records success or failure."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import Settings
from app.core.container import Container
from app.services import flows, jobs, pipeline

# job id -> (settings attribute holding the cron expression, job function, description)
JOBS: dict[str, tuple[str, Any, str]] = {
    "instruments": ("instrument_refresh_cron", pipeline.instruments_job, "Instrument master sync"),
    "prices": ("price_refresh_cron", pipeline.prices_job, "End-of-day prices, fundamentals reload, metric recomputation"),
    "rrg": ("rrg_refresh_cron", pipeline.rrg_job, "Sector rotation (RRG) snapshot, every 12 hours"),
    "themes": ("theme_refresh_cron", pipeline.themes_job, "Monthly theme membership and ranking snapshots"),
    "alerts": ("alerts_cron", pipeline.alerts_job, "Alert evaluation"),
    "fii_dii": ("fii_dii_cron", flows.fii_dii_job, "Record NSE's provisional FII/DII figures for the day"),
}


_DOW = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def standard_day_of_week(field: str) -> str:
    """Standard cron numbers days of the week from Sunday (0 or 7 = Sunday, 1 = Monday); APScheduler numbers
    them from Monday (0 = Monday), so `1-5` would run Tuesday to Saturday. Numeric days are rewritten as
    names, which APScheduler reads unambiguously; named days and `*` pass through."""
    if not any(ch.isdigit() for ch in field.split("/")[0]):
        return field
    days: set[int] = set()
    for part in field.split(","):
        rng, _, step = part.partition("/")
        if rng == "*":
            lo, hi = 0, 6
        elif "-" in rng:
            a, b = rng.split("-")
            lo, hi = int(a), int(b)
        else:
            lo = hi = int(rng)
        days.update(d % 7 for d in range(lo, hi + 1, int(step) if step else 1))
    if days == set(range(7)):
        return "*"
    return ",".join(_DOW[d] for d in sorted(days, key=lambda d: (d + 6) % 7))  # Monday first


def to_trigger(expr: str, tz: ZoneInfo) -> CronTrigger:
    parts = expr.split()
    if len(parts) == 5:
        parts[4] = standard_day_of_week(parts[4])
    return CronTrigger.from_crontab(" ".join(parts), timezone=tz)


def build_triggers(settings: Settings) -> dict[str, CronTrigger]:
    tz = ZoneInfo(settings.default_market_timezone)
    return {job_id: to_trigger(getattr(settings, attr), tz) for job_id, (attr, _fn, _d) in JOBS.items()}


class Scheduler:
    def __init__(self, c: Container) -> None:
        self.c = c
        self._s = AsyncIOScheduler(timezone=ZoneInfo(c.settings.default_market_timezone))
        self.triggers = build_triggers(c.settings)

    def start(self) -> None:
        for job_id, (_attr, fn, _desc) in JOBS.items():
            self._s.add_job(self._run, self.triggers[job_id], args=[job_id, fn], id=job_id, coalesce=True, max_instances=1,
                            misfire_grace_time=3600, replace_existing=True)
        self._s.start()

    async def _run(self, job_id: str, fn: Any) -> None:
        await jobs.run(self.c, job_id, job_id, fn, "scheduled")

    def shutdown(self) -> None:
        if self._s.running:
            self._s.shutdown(wait=False)

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for job_id, (attr, _fn, desc) in JOBS.items():
            job = self._s.get_job(job_id) if self._s.running else None
            nxt = job.next_run_time if job else self.triggers[job_id].get_next_fire_time(None, self.c.clock.now())
            out.append({"id": job_id, "description": desc, "cron": getattr(self.c.settings, attr),
                        "timezone": self.c.settings.default_market_timezone, "next_run": nxt.isoformat() if nxt else None})
        return out
