import asyncio
import logging
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from zoneinfo import ZoneInfo, available_timezones

logger = logging.getLogger("ReconScheduler")

# Regular expression to strictly validate 24-hour HH:MM time
TIME_REGEX = re.compile(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")

def validate_schedule_time(time_str: str) -> bool:
    """Validate 24-hour time string in HH:MM format."""
    if not time_str or not isinstance(time_str, str):
        return False
    return bool(TIME_REGEX.match(time_str.strip()))

def validate_timezone(tz_name: str) -> bool:
    """Validate whether tz_name is a valid IANA timezone."""
    if not tz_name or not isinstance(tz_name, str):
        return False
    try:
        ZoneInfo(tz_name.strip())
        return True
    except Exception:
        return False

def get_safe_zoneinfo(tz_name: Optional[str]) -> ZoneInfo:
    """Safely return a ZoneInfo instance, falling back to UTC if invalid."""
    if tz_name and isinstance(tz_name, str):
        try:
            return ZoneInfo(tz_name.strip())
        except Exception:
            pass
    return ZoneInfo("UTC")

def compute_next_run_utc(
    schedule_time: str,
    tz_name: str,
    from_dt_utc: Optional[datetime] = None
) -> datetime:
    """
    Compute the next occurrence of schedule_time (HH:MM) in tz_name, converted to UTC.
    Accurately handles day rollover, local timezone offsets, and Daylight Saving Time (DST).
    """
    tz = get_safe_zoneinfo(tz_name)
    if from_dt_utc is None:
        from_dt_utc = datetime.now(timezone.utc)
    elif from_dt_utc.tzinfo is None:
        from_dt_utc = from_dt_utc.replace(tzinfo=timezone.utc)

    local_now = from_dt_utc.astimezone(tz)
    try:
        hour, minute = map(int, schedule_time.strip().split(":"))
    except Exception:
        hour, minute = 4, 0

    # Form target local candidate for today
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        # Move forward one day in local timezone to properly account for DST transitions
        candidate = (local_now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Convert local candidate to UTC datetime
    return candidate.astimezone(timezone.utc)

def generate_slot_key(group_id: int, target_dt_utc: datetime, schedule_time: str, tz_name: str) -> str:
    """
    Unique slot key per schedule day in target timezone to prevent duplicate executions.
    Example: '1614098432:2026-11-16_04:00_Asia/Kolkata'
    """
    tz = get_safe_zoneinfo(tz_name)
    local_dt = target_dt_utc.astimezone(tz)
    return f"{group_id}:{local_dt.strftime('%Y-%m-%d')}_{schedule_time}_{tz_name}"


class ReconScheduler:
    """
    Server-side, database-backed automated reconnaissance scheduler.
    Survives backend restarts, page refreshes, and runs autonomously without an open browser.
    """

    validate_schedule_time = staticmethod(validate_schedule_time)
    validate_timezone = staticmethod(validate_timezone)
    compute_next_run_utc = staticmethod(compute_next_run_utc)
    generate_slot_key = staticmethod(generate_slot_key)
    get_safe_zoneinfo = staticmethod(get_safe_zoneinfo)

    def __init__(self, engine, db, check_interval_sec: float = 15.0):
        self.engine = engine
        self.db = db
        self.check_interval_sec = check_interval_sec
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._executing_schedules = set()

    def start(self):
        """Start the background scheduler monitoring loop."""
        if self._running and self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("⚡ ReconScheduler background daemon initialized.")

    def stop(self):
        """Gracefully halt the scheduler loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("ReconScheduler background daemon stopped.")

    async def _run_loop(self):
        """Continuous non-blocking scheduler evaluation loop."""
        logger.info(f"ReconScheduler monitoring loop active (tick rate: {self.check_interval_sec}s).")
        while self._running:
            try:
                await self.check_and_execute_due_schedules()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in ReconScheduler loop: {e}", exc_info=True)
            
            try:
                await asyncio.sleep(self.check_interval_sec)
            except asyncio.CancelledError:
                break

    async def check_and_execute_due_schedules(self):
        """Check persistent SQLite recon_schedules for any jobs that have reached their next_run_at timestamp."""
        if not self.db or not hasattr(self.db, "get_all_due_schedules"):
            return

        now_utc = datetime.now(timezone.utc)
        now_utc_iso = now_utc.isoformat()

        due_schedules = await self.db.get_all_due_schedules(now_utc_iso)
        if not due_schedules:
            return

        for sched in due_schedules:
            schedule_id = sched["id"]
            group_id = sched["group_id"]
            sched_time = sched.get("schedule_time", "04:00")
            tz_str = sched.get("timezone", "UTC")

            if schedule_id in self._executing_schedules:
                continue

            # Generate authoritative slot identifier for today's run
            slot = generate_slot_key(group_id, now_utc, sched_time, tz_str)

            # Atomic slot lock in database (prevents duplicate execution across workers/instances)
            locked = await self.db.acquire_schedule_slot_lock(schedule_id, slot, now_utc_iso)
            if not locked:
                # Slot was already executed or acquired by another process; advance next_run_at
                next_dt = compute_next_run_utc(sched_time, tz_str, from_dt_utc=now_utc)
                await self.db.update_recon_schedule_run(
                    schedule_id=schedule_id,
                    last_run_status=sched.get("last_run_status") or "IDLE",
                    next_run_at=next_dt.isoformat()
                )
                continue

            # Execute scheduled scan in background task
            self._executing_schedules.add(schedule_id)
            asyncio.create_task(self._guarded_execute_scheduled_scan(sched, slot, "scheduled"))

    async def _guarded_execute_scheduled_scan(self, sched: Dict[str, Any], slot: str, execution_type: str = "scheduled"):
        schedule_id = sched["id"]
        try:
            await self.execute_scheduled_scan(sched, execution_type=execution_type, slot=slot)
        finally:
            self._executing_schedules.discard(schedule_id)

    async def execute_scheduled_scan(
        self,
        sched: Dict[str, Any],
        execution_type: str = "scheduled",
        slot: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute an automated deep reputation scan reusing the existing 100K engine.
        Applies overlapping protection, Telegram availability verification, and execution history logging.
        """
        group_id = sched["group_id"]
        group_title = sched.get("group_title") or f"Group {group_id}"
        target_limit = max(100, min(int(sched.get("target_limit") or 100000), 100000))
        sched_time = sched.get("schedule_time", "04:00")
        tz_str = sched.get("timezone", "UTC")

        execution_id = f"exec_{uuid.uuid4().hex[:12]}"
        start_time_dt = datetime.now(timezone.utc)
        start_time_iso = start_time_dt.isoformat()
        scheduled_time = sched.get("next_run_at") or start_time_iso

        # 1. Create execution record with status STARTING
        if self.db and hasattr(self.db, "create_recon_schedule_execution"):
            await self.db.create_recon_schedule_execution(
                execution_id=execution_id,
                schedule_id=sched.get("id"),
                group_id=group_id,
                group_title=group_title,
                scheduled_time=scheduled_time,
                actual_start_time=start_time_iso,
                execution_type=execution_type,
                final_status="STARTING"
            )

        # 2. Overlapping Scan Protection (Section 5)
        # Check whether another scan is actively running for this target
        norm_gid = self.engine.normalize_gid(group_id) if hasattr(self.engine, "normalize_gid") else group_id
        abs_gid = abs(int(group_id)) if str(group_id).lstrip("-").isdigit() else norm_gid

        is_scan_active = False
        if hasattr(self.engine, "_active_deep_scans_in_flight") and norm_gid in self.engine._active_deep_scans_in_flight:
            is_scan_active = True
        elif hasattr(self.engine, "active_deep_scans"):
            scan_obj = self.engine.active_deep_scans.get(norm_gid) or self.engine.active_deep_scans.get(abs_gid)
            if scan_obj and scan_obj.get("status") in ("VALIDATING", "CONNECTING", "PROCESSING", "RUNNING"):
                is_scan_active = True

        if not is_scan_active and self.db and hasattr(self.db, "get_active_scan_job_for_target"):
            db_active = await self.db.get_active_scan_job_for_target(group_id)
            if db_active and db_active.get("status") in ("VALIDATING", "CONNECTING", "PROCESSING", "RUNNING"):
                is_scan_active = True

        if is_scan_active:
            skip_reason = "Previous scan still active for target (Overlapping protection applied)"
            logger.warning(f"ReconScheduler: Skipping execution for group {group_id} — {skip_reason}")
            
            completion_time_iso = datetime.now(timezone.utc).isoformat()
            if self.db and hasattr(self.db, "update_recon_schedule_execution"):
                await self.db.update_recon_schedule_execution(
                    execution_id=execution_id,
                    final_status="SKIPPED",
                    error_details=skip_reason,
                    completion_time=completion_time_iso
                )

            next_run_dt = compute_next_run_utc(sched_time, tz_str, from_dt_utc=datetime.now(timezone.utc))
            if self.db and hasattr(self.db, "update_recon_schedule_run") and sched.get("id"):
                await self.db.update_recon_schedule_run(
                    schedule_id=sched["id"],
                    last_run_status="SKIPPED",
                    next_run_at=next_run_dt.isoformat()
                )

            await self._emit_schedule_event("skipped", {
                "execution_id": execution_id,
                "group_id": group_id,
                "group_title": group_title,
                "status": "SKIPPED",
                "reason": skip_reason,
                "next_run_at": next_run_dt.isoformat()
            })
            return {
                "execution_id": execution_id,
                "status": "SKIPPED",
                "reason": skip_reason
            }

        # 3. Telegram Client Availability Check
        client = getattr(self.engine, "client", None)
        tg_available = False
        if client and hasattr(client, "is_connected") and client.is_connected():
            try:
                tg_available = await client.is_user_authorized()
            except Exception:
                tg_available = False

        if not tg_available:
            fail_reason = "Target Telegram integration offline or unauthorized"
            logger.error(f"ReconScheduler: Cannot execute scan for group {group_id} — {fail_reason}")
            
            completion_time_iso = datetime.now(timezone.utc).isoformat()
            if self.db and hasattr(self.db, "update_recon_schedule_execution"):
                await self.db.update_recon_schedule_execution(
                    execution_id=execution_id,
                    final_status="FAILED",
                    error_details=fail_reason,
                    completion_time=completion_time_iso
                )

            next_run_dt = compute_next_run_utc(sched_time, tz_str, from_dt_utc=datetime.now(timezone.utc))
            if self.db and hasattr(self.db, "update_recon_schedule_run") and sched.get("id"):
                await self.db.update_recon_schedule_run(
                    schedule_id=sched["id"],
                    last_run_status="FAILED",
                    next_run_at=next_run_dt.isoformat()
                )

            await self._emit_schedule_event("failed", {
                "execution_id": execution_id,
                "group_id": group_id,
                "group_title": group_title,
                "status": "FAILED",
                "error": fail_reason,
                "next_run_at": next_run_dt.isoformat()
            })
            return {
                "execution_id": execution_id,
                "status": "FAILED",
                "error": fail_reason
            }

        # 4. Resolve Target Group Entity
        entity = None
        try:
            entity = await self.engine.get_group_entity(group_id)
        except Exception as e:
            logger.error(f"ReconScheduler entity resolution error: {e}")

        if not entity:
            fail_reason = f"Target Telegram entity {group_id} not accessible or not found"
            completion_time_iso = datetime.now(timezone.utc).isoformat()
            if self.db and hasattr(self.db, "update_recon_schedule_execution"):
                await self.db.update_recon_schedule_execution(
                    execution_id=execution_id,
                    final_status="FAILED",
                    error_details=fail_reason,
                    completion_time=completion_time_iso
                )

            next_run_dt = compute_next_run_utc(sched_time, tz_str, from_dt_utc=datetime.now(timezone.utc))
            if self.db and hasattr(self.db, "update_recon_schedule_run") and sched.get("id"):
                await self.db.update_recon_schedule_run(
                    schedule_id=sched["id"],
                    last_run_status="FAILED",
                    next_run_at=next_run_dt.isoformat()
                )
            return {
                "execution_id": execution_id,
                "status": "FAILED",
                "error": fail_reason
            }

        # 5. Launch Authentic 100K Scan Engine
        job_id = f"sched_scan_{uuid.uuid4().hex[:12]}"
        logger.info(f"ReconScheduler: Launching scheduled 100k scan for '{group_title}' ({group_id}), Job: {job_id}")

        if self.db and hasattr(self.db, "update_recon_schedule_execution"):
            await self.db.update_recon_schedule_execution(
                execution_id=execution_id,
                final_status="RUNNING",
                job_id=job_id
            )

        await self._emit_schedule_event("started", {
            "execution_id": execution_id,
            "job_id": job_id,
            "group_id": group_id,
            "group_title": group_title,
            "target_limit": target_limit,
            "execution_type": execution_type,
            "status": "RUNNING"
        })

        summary = {}
        error_msg = None
        try:
            summary = await self.engine.deep_scan_group_messages(
                entity,
                limit=target_limit,
                job_id=job_id,
                job_type="scheduled_100k"
            )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"ReconScheduler execution error during scan: {e}", exc_info=True)

        end_time_dt = datetime.now(timezone.utc)
        duration_sec = round((end_time_dt - start_time_dt).total_seconds(), 1)
        completion_time_iso = end_time_dt.isoformat()

        scanned = (
            summary.get("scanned")
            or summary.get("messages_scanned")
            or summary.get("total_scanned")
            or summary.get("processed_count")
            or 0
        )
        unique_members = summary.get("unique_members") or 0

        # Determine final status
        if error_msg and scanned == 0:
            final_status = "FAILED"
        elif error_msg and scanned > 0:
            final_status = "PARTIALLY_COMPLETED"
        elif scanned > 0 and scanned < target_limit and summary.get("error"):
            final_status = "PARTIALLY_COMPLETED"
        elif summary.get("status") == "CANCELLED":
            final_status = "CANCELLED"
        else:
            final_status = "COMPLETED"

        # 6. Update execution history
        if self.db and hasattr(self.db, "update_recon_schedule_execution"):
            await self.db.update_recon_schedule_execution(
                execution_id=execution_id,
                final_status=final_status,
                messages_fetched=scanned,
                messages_processed=scanned,
                unique_members_profiled=unique_members,
                failed_messages=1 if error_msg else 0,
                execution_duration_sec=duration_sec,
                error_details=error_msg or summary.get("error"),
                job_id=job_id,
                completion_time=completion_time_iso
            )

        # 7. Advance next_run_at for schedule
        next_run_dt = compute_next_run_utc(sched_time, tz_str, from_dt_utc=end_time_dt)
        if self.db and hasattr(self.db, "update_recon_schedule_run") and sched.get("id"):
            await self.db.update_recon_schedule_run(
                schedule_id=sched["id"],
                last_run_status=final_status,
                last_run_job_id=job_id,
                next_run_at=next_run_dt.isoformat()
            )

        # 8. Emit notification event
        event_name = "completed" if final_status == "COMPLETED" else ("failed" if final_status == "FAILED" else "partially_completed")
        await self._emit_schedule_event(event_name, {
            "execution_id": execution_id,
            "job_id": job_id,
            "group_id": group_id,
            "group_title": group_title,
            "scanned": scanned,
            "unique_members": unique_members,
            "duration_sec": duration_sec,
            "final_status": final_status,
            "next_run_at": next_run_dt.isoformat(),
            "error": error_msg
        })

        return {
            "execution_id": execution_id,
            "job_id": job_id,
            "status": final_status,
            "scanned": scanned,
            "unique_members": unique_members,
            "duration_sec": duration_sec,
            "next_run_at": next_run_dt.isoformat(),
            "error": error_msg
        }

    async def trigger_run_now(self, group_id: int) -> Dict[str, Any]:
        """
        Manually trigger the scheduled reconnaissance scan immediately.
        Respects overlapping protection and records the execution in history.
        """
        norm_gid = self.engine.normalize_gid(group_id) if hasattr(self.engine, "normalize_gid") else group_id
        sched = await self.db.get_recon_schedule(norm_gid) if self.db else None
        
        if not sched:
            # Create a default schedule record on the fly for this group
            sched = await self.db.upsert_recon_schedule(
                group_id=norm_gid,
                is_enabled=False,
                schedule_time="04:00",
                timezone="UTC",
                frequency="daily",
                target_limit=100000,
                next_run_at=compute_next_run_utc("04:00", "UTC").isoformat()
            )

        # Launch the execution asynchronously
        now_utc = datetime.now(timezone.utc)
        tz_str = sched.get("timezone", "UTC")
        sched_time = sched.get("schedule_time", "04:00")
        slot = f"manual_{norm_gid}_{now_utc.strftime('%Y%m%d_%H%M%S')}"

        # Schedule execution in background
        task = asyncio.create_task(self.execute_scheduled_scan(sched, execution_type="manual_run_now", slot=slot))
        
        # Give initial task 0.05s to check overlapping protection and return immediate status
        await asyncio.sleep(0.05)

        return {
            "status": "started",
            "group_id": norm_gid,
            "message": "Automated reconnaissance execution dispatched immediately."
        }

    # Alias for single schedule execution
    execute_single_schedule = execute_scheduled_scan

    async def _emit_schedule_event(self, event_type: str, data: Dict[str, Any]):
        """Emit notification event to live dashboard and record in audit log."""
        payload = {
            "type": "scheduled_scan_event",
            "event": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        if hasattr(self.engine, "emit_telemetry"):
            try:
                await self.engine.emit_telemetry("scheduled_scan_event", payload)
            except Exception as e:
                logger.debug(f"Telemetry emission note: {e}")

        # Record in security / threat audit log for permanent visibility
        if self.db and hasattr(self.db, "log_audit"):
            try:
                msg = f"Scheduled Recon {event_type.upper()}: Group {data.get('group_id')} ({data.get('final_status', event_type)})"
                await self.db.log_audit(
                    group_id=data.get("group_id", 0),
                    level="INFO" if event_type in ("completed", "started") else "WARNING",
                    message=msg
                )
            except Exception:
                pass
