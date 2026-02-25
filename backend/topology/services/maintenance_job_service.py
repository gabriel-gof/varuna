import logging
import threading
import time
from io import StringIO
from typing import Dict, Optional, Tuple

from django.core.management import call_command
from django.db import close_old_connections, transaction
from django.utils import timezone

from topology.models import MaintenanceJob, OLT
from topology.services.cache_service import cache_service
from topology.services.maintenance_runtime import collect_power_for_olt


logger = logging.getLogger(__name__)


class MaintenanceJobService:
    def __init__(self):
        self.poll_interval_seconds = 0.5
        self.idle_shutdown_seconds = 10.0
        self._runner_lock = threading.Lock()
        self._runner_thread: Optional[threading.Thread] = None

    def enqueue_job(
        self,
        *,
        olt_id: int,
        kind: str,
        requested_by=None,
    ) -> Tuple[MaintenanceJob, bool]:
        with transaction.atomic():
            olt = OLT.objects.select_for_update().get(id=olt_id, is_active=True)
            existing = (
                MaintenanceJob.objects.select_for_update()
                .filter(olt=olt, status__in=MaintenanceJob.ACTIVE_STATUSES)
                .order_by('-created_at')
                .first()
            )
            if existing:
                return existing, False

            job = MaintenanceJob.objects.create(
                olt=olt,
                kind=kind,
                status=MaintenanceJob.STATUS_QUEUED,
                progress=0,
                detail='Queued.',
                requested_by=requested_by if getattr(requested_by, 'is_authenticated', False) else None,
            )

        self.ensure_runner()
        return job, True

    def ensure_runner(self) -> None:
        with self._runner_lock:
            if self._runner_thread and self._runner_thread.is_alive():
                return
            self._runner_thread = threading.Thread(
                target=self._runner_loop,
                name='varuna-maintenance-runner',
                daemon=True,
            )
            self._runner_thread.start()

    def _ensure_runner_if_queued(self) -> None:
        if MaintenanceJob.objects.filter(status=MaintenanceJob.STATUS_QUEUED).exists():
            self.ensure_runner()

    def has_active_job(self, olt_id: int) -> bool:
        self._ensure_runner_if_queued()
        return MaintenanceJob.objects.filter(
            olt_id=olt_id,
            status__in=MaintenanceJob.ACTIVE_STATUSES,
        ).exists()

    def get_active_job(self, olt_id: int) -> Optional[MaintenanceJob]:
        self._ensure_runner_if_queued()
        return (
            MaintenanceJob.objects.filter(
                olt_id=olt_id,
                status__in=MaintenanceJob.ACTIVE_STATUSES,
            )
            .select_related('requested_by')
            .order_by('-created_at')
            .first()
        )

    def get_latest_job(self, olt_id: int) -> Optional[MaintenanceJob]:
        self._ensure_runner_if_queued()
        return (
            MaintenanceJob.objects.filter(olt_id=olt_id)
            .select_related('requested_by')
            .order_by('-created_at')
            .first()
        )

    def serialize_job(self, job: Optional[MaintenanceJob]) -> Optional[Dict]:
        if not job:
            return None
        return {
            'id': job.id,
            'kind': job.kind,
            'status': job.status,
            'progress': int(job.progress or 0),
            'detail': job.detail or '',
            'output': job.output or '',
            'error': job.error or '',
            'olt_id': job.olt_id,
            'requested_by': job.requested_by.username if job.requested_by else None,
            'created_at': job.created_at.isoformat() if job.created_at else None,
            'started_at': job.started_at.isoformat() if job.started_at else None,
            'finished_at': job.finished_at.isoformat() if job.finished_at else None,
            'updated_at': job.updated_at.isoformat() if job.updated_at else None,
        }

    def _runner_loop(self) -> None:
        close_old_connections()
        idle_started_at = None
        try:
            while True:
                processed = self._process_one_job()
                if processed:
                    idle_started_at = None
                    continue

                has_queued = MaintenanceJob.objects.filter(
                    status=MaintenanceJob.STATUS_QUEUED
                ).exists()
                if has_queued:
                    time.sleep(self.poll_interval_seconds)
                    continue

                if idle_started_at is None:
                    idle_started_at = time.monotonic()
                elif (time.monotonic() - idle_started_at) >= self.idle_shutdown_seconds:
                    with self._runner_lock:
                        has_more = MaintenanceJob.objects.filter(
                            status=MaintenanceJob.STATUS_QUEUED
                        ).exists()
                        if not has_more:
                            self._runner_thread = None
                            break
                        idle_started_at = None

                time.sleep(self.poll_interval_seconds)
        finally:
            close_old_connections()

    def _process_one_job(self) -> bool:
        job_id = self._claim_next_job_id()
        if not job_id:
            return False

        close_old_connections()
        try:
            self._execute_job(job_id)
        finally:
            close_old_connections()
        return True

    def _claim_next_job_id(self) -> Optional[int]:
        with transaction.atomic():
            running_olt_ids = list(
                MaintenanceJob.objects.select_for_update()
                .filter(status=MaintenanceJob.STATUS_RUNNING)
                .values_list('olt_id', flat=True)
            )
            queue_qs = (
                MaintenanceJob.objects.select_for_update(skip_locked=True)
                .filter(status=MaintenanceJob.STATUS_QUEUED)
            )
            if running_olt_ids:
                queue_qs = queue_qs.exclude(olt_id__in=running_olt_ids)

            job = queue_qs.order_by('created_at').first()
            if not job:
                return None

            now = timezone.now()
            job.status = MaintenanceJob.STATUS_RUNNING
            job.progress = 5
            job.detail = 'Starting maintenance task.'
            job.started_at = now
            job.finished_at = None
            job.error = ''
            job.save(
                update_fields=[
                    'status',
                    'progress',
                    'detail',
                    'started_at',
                    'finished_at',
                    'error',
                    'updated_at',
                ]
            )
            return job.id

    def _update_job(self, job_id: int, **fields) -> None:
        fields['updated_at'] = timezone.now()
        MaintenanceJob.objects.filter(id=job_id).update(**fields)

    def _progress_update(self, job_id: int, percent: int, detail: str = '') -> None:
        self._update_job(
            job_id,
            progress=max(0, min(int(percent), 99)),
            detail=(detail or '')[:255],
        )

    def _complete_job(
        self,
        job_id: int,
        *,
        detail: str,
        output: str = '',
    ) -> None:
        self._update_job(
            job_id,
            status=MaintenanceJob.STATUS_COMPLETED,
            progress=100,
            detail=(detail or 'Completed.')[:255],
            output=(output or '')[:20000],
            error='',
            finished_at=timezone.now(),
        )

    def _fail_job(self, job_id: int, exc: Exception) -> None:
        logger.exception("Maintenance job %s failed: %s", job_id, exc)
        self._update_job(
            job_id,
            status=MaintenanceJob.STATUS_FAILED,
            progress=100,
            detail='Maintenance task failed.',
            error=str(exc)[:20000],
            finished_at=timezone.now(),
        )

    def _execute_job(self, job_id: int) -> None:
        job = MaintenanceJob.objects.select_related('olt', 'olt__vendor_profile').get(id=job_id)
        if not job.olt.is_active:
            self._update_job(
                job_id,
                status=MaintenanceJob.STATUS_CANCELED,
                progress=100,
                detail='OLT is inactive. Job canceled.',
                finished_at=timezone.now(),
            )
            return

        try:
            if job.kind == MaintenanceJob.KIND_DISCOVERY:
                detail, output = self._run_discovery(job)
            elif job.kind == MaintenanceJob.KIND_POLLING:
                detail, output = self._run_polling(job)
            elif job.kind == MaintenanceJob.KIND_POWER:
                detail, output = self._run_power(job)
            else:
                raise ValueError(f"Unsupported maintenance job kind: {job.kind}")

            self._complete_job(job_id, detail=detail, output=output)
        except Exception as exc:
            self._fail_job(job_id, exc)

    def _run_discovery(self, job: MaintenanceJob) -> Tuple[str, str]:
        self._progress_update(job.id, 12, 'Running ONU discovery.')
        output = StringIO()
        call_command('discover_onus', olt_id=job.olt_id, force=True, stdout=output)
        self._progress_update(job.id, 92, 'Finalizing discovery.')
        cache_service.invalidate_topology_api_cache(job.olt_id)
        return 'Discovery completed.', output.getvalue().strip()

    def _run_polling(self, job: MaintenanceJob) -> Tuple[str, str]:
        self._progress_update(job.id, 12, 'Running ONU status polling.')
        output = StringIO()
        call_command('poll_onu_status', olt_id=job.olt_id, force=True, stdout=output)
        self._progress_update(job.id, 92, 'Finalizing polling.')
        cache_service.invalidate_topology_api_cache(job.olt_id)
        return 'Polling completed.', output.getvalue().strip()

    def _run_power(self, job: MaintenanceJob) -> Tuple[str, str]:
        self._progress_update(job.id, 12, 'Running power collection.')
        olt = OLT.objects.select_related('vendor_profile').get(id=job.olt_id, is_active=True)
        payload = collect_power_for_olt(
            olt,
            force_refresh=True,
            include_results=False,
            progress_callback=lambda percent, detail: self._progress_update(job.id, percent, detail),
        )
        self._progress_update(job.id, 92, 'Finalizing power collection.')
        cache_service.invalidate_topology_api_cache(job.olt_id)
        summary = (
            f"count={payload.get('count', 0)} "
            f"attempted={payload.get('attempted_count', 0)} "
            f"collected={payload.get('collected_count', 0)}"
        )
        return 'Power collection completed.', summary


maintenance_job_service = MaintenanceJobService()
