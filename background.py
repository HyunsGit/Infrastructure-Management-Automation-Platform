# background.py — background threads (token refresh, snapshots, nightly check)
import fcntl
import time
import threading
import pandas as pd
from datetime import datetime, timedelta
from extensions import db

_bg_lock_fd = None

def _try_acquire_bg_lock() -> bool:
    global _bg_lock_fd
    lock_path = '/tmp/infrapilot_bg.lock'
    pid_path  = '/tmp/infrapilot_bg.pid'

    # If a PID file exists, check whether that process is still alive.
    # If it's dead, clear both files so we can take over.
    try:
        with open(pid_path) as f:
            old_pid = int(f.read().strip())
        import os, errno
        try:
            os.kill(old_pid, 0)  # signal 0 = existence check only
        except OSError as e:
            if e.errno == errno.ESRCH:  # No such process — stale lock
                import logging
                logging.getLogger(__name__).warning(
                    f'Stale bg lock from dead PID {old_pid} — clearing and taking over.')
                for p in (lock_path, pid_path):
                    try: os.remove(p)
                    except: pass
    except (FileNotFoundError, ValueError):
        pass  # no PID file yet, normal on first boot

    try:
        _bg_lock_fd = open(lock_path, 'w')
        fcntl.flock(_bg_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Write our PID so future workers can validate us
        import os
        with open(pid_path, 'w') as f:
            f.write(str(os.getpid()))
        return True
    except (IOError, OSError):
        return False


def _prewarm_vm_cache(app):
    """Pre-warm the provisioning VM list cache for all projects.
    Runs immediately on startup then every 55s, keeping Redis warm
    so provisioning page loads are instant (reads from cache, not API)."""
    from helpers import get_projects_from_dotenv, _list_all_vms_by_project, \
                        _get_all_scode_rules, _resolve_scode, _get_os_type, \
                        _is_ubuntu_24_or_higher, EXCLUDED_KEYS
    import redis as _redis
    import json as _json
    import time as _time

    try:
        redis_client = _redis.Redis(host='localhost', port=6379, db=1,
                                     decode_responses=True)
    except Exception as e:
        app.logger.error(f'VM cache prewarm: Redis connect failed: {e}')
        return

    def _fetch_and_cache(project_key, token_ok_projects):
        """Fetch VMs for one project and store in Redis. Returns True on success."""
        from helpers import get_env_var
        token = get_env_var(project_key, default='')
        if not token or token.strip().lower() == 'none':
            return False
        try:
            vms = _list_all_vms_by_project(project_key)
            scode_rules = _get_all_scode_rules()
            result = []
            for vm in vms:
                image_raw  = vm.get('image')
                image_name = image_raw.get('name', '') if isinstance(image_raw, dict) else 'K8s node'
                addresses  = vm.get('addresses', [])
                private_ip = ''
                if isinstance(addresses, list):
                    private_ip = next((a.get('private_ip','') for a in addresses if a.get('private_ip')), '')
                elif isinstance(addresses, dict):
                    for nets in addresses.values():
                        for addr in nets:
                            if addr.get('OS-EXT-IPS:type') == 'fixed':
                                private_ip = addr.get('ip_address', '')
                                break
                scode = _resolve_scode(vm.get('name',''), scode_rules) if scode_rules else 'invalid'
                result.append({
                    'vm_name':     vm.get('name', ''),
                    'vm_id':       vm.get('id', ''),
                    'status':      vm.get('status', ''),
                    'flavor':      vm.get('flavor', {}).get('name', '') if isinstance(vm.get('flavor'), dict) else '',
                    'az':          vm.get('availability_zone', ''),
                    'image':       image_name,
                    'os_ok':       _is_ubuntu_24_or_higher(image_name),
                    'os_type':     _get_os_type(image_name),
                    'project_key': project_key,
                    'description': scode,
                    'key_name':    vm.get('key_name', ''),
                    'private_ip':  private_ip,
                    'project_id':  vm.get('project_id', ''),
                    'created_at':  vm.get('created_at', ''),
                })
            result.sort(key=lambda x: x['vm_name'])
            redis_client.setex(f'provvms:{project_key}', 120, _json.dumps(result))
            return True
        except Exception as e:
            # On failure (e.g. 401 expired token), delete stale cache
            redis_client.delete(f'provvms:{project_key}')
            app.logger.debug(f'VM cache prewarm failed for {project_key}: {e}')
            return False

    while True:
        try:
            with app.app_context():
                all_projects = [k for k in get_projects_from_dotenv()
                                if k not in EXCLUDED_KEYS]
                ok = fail = 0
                for project_key in all_projects:
                    if _fetch_and_cache(project_key, all_projects):
                        ok += 1
                    else:
                        fail += 1
                    _time.sleep(0.5)  # small delay to avoid 429 rate limits

                # Also cache the "all" key as a merged result
                try:
                    merged = []
                    for k in all_projects:
                        raw = redis_client.get(f'provvms:{k}')
                        if raw:
                            merged.extend(_json.loads(raw))
                    if merged:
                        merged.sort(key=lambda x: x['vm_name'])
                        redis_client.setex('provvms:all', 120, _json.dumps(merged))
                except Exception as e:
                    app.logger.debug(f'VM cache prewarm all-merge failed: {e}')

                app.logger.info(f'VM cache prewarm complete: {ok} ok, {fail} failed')
        except Exception as e:
            app.logger.error(f'VM cache prewarm cycle error: {e}')
        _time.sleep(55)


def _refresh_all_tokens(app):
    from models import Project
    from helpers import _fetch_kc_token, _write_tokens_file, _log_history, TOKEN_CHECK_INTERVAL, TOKEN_REFRESH_INTERVAL
    while True:
        time.sleep(TOKEN_CHECK_INTERVAL)
        try:
            with app.app_context():
                now = datetime.utcnow()
                refreshed = []; failed = []
                for proj in Project.query.all():
                    if not proj.access_id or not proj.secret_key:
                        continue
                    age = (now - proj.token_refreshed_at).total_seconds() if proj.token_refreshed_at else float('inf')
                    if age >= TOKEN_REFRESH_INTERVAL:
                        token = _fetch_kc_token(proj.access_id, proj.secret_key)
                        if token:
                            proj.token              = token
                            proj.token_refreshed_at = now
                            refreshed.append(proj.zone)
                            app.logger.info(f'Auto-refreshed token for {proj.zone}')
                        else:
                            failed.append(proj.zone)
                            app.logger.warning(f'Token refresh failed for {proj.zone}')
                if refreshed or failed:
                    db.session.commit()
                    _write_tokens_file()
                if refreshed:
                    _log_history(
                        'TOKEN_REFRESH',
                        f'토큰 자동 갱신 ({len(refreshed)}개 프로젝트)',
                        '갱신 완료:\n' + '\n'.join(f'* {z}' for z in refreshed) +
                        ('\n\n갱신 실패:\n' + '\n'.join(f'* {z}' for z in failed) if failed else ''),
                        'system'
                    )
        except Exception as e:
            app.logger.error(f'Token refresh cycle error (will retry in {TOKEN_CHECK_INTERVAL//60}min): {e}')


def _take_scode_snapshot(app):
    from models import SCodeSnapshot, SCodeVmSnapshot
    from helpers import _get_all_scode_rules, _resolve_scode
    try:
        kst_date = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y-%m-%d')
        existing = SCodeSnapshot.query.filter_by(date=kst_date).first()
        if existing:
            app.logger.info(f'S-Code snapshot already exists for {kst_date}')
            return
        csv_path = '/home/ubuntu/merged_vm_result.csv'
        df = pd.read_csv(csv_path, sep='\t', header=None, names=[
            'status', 'project', 'tenant_id', 'servicecode', 'vm_name', 'vm_id',
            'ip_address', 'flavor', 'availability_zone', 'vpc', 'subnet', 'os', 'date', 'user_id'
        ])
        rules = _get_all_scode_rules()
        if rules:
            df['servicecode'] = df['vm_name'].apply(lambda name: _resolve_scode(name, rules) or 'invalid')
        df = df.fillna('')
        # Strip whitespace from key fields to prevent false diffs
        df['vm_id']       = df['vm_id'].astype(str).str.strip()
        df['vm_name']     = df['vm_name'].astype(str).str.strip()
        df['servicecode'] = df['servicecode'].astype(str).str.strip()
        counts = df.groupby('servicecode').size()
        for scode, count in counts.items():
            db.session.add(SCodeSnapshot(date=kst_date, scode=scode, count=int(count)))
        SCodeVmSnapshot.query.filter_by(date=kst_date).delete()
        for _, row in df.iterrows():
            db.session.add(SCodeVmSnapshot(
                date=kst_date, scode=row['servicecode'], vm_id=row['vm_id'],
                vm_name=row['vm_name'], project=row['project'], ip_address=row['ip_address'],
            ))
        db.session.commit()
        app.logger.info(f'S-Code snapshot taken for {kst_date}: {len(counts)} s-codes, {len(df)} VMs')
    except Exception as e:
        app.logger.error(f'S-Code snapshot failed: {e}')


def _schedule_scode_snapshot(app):
    while True:
        now_kst  = datetime.utcnow() + timedelta(hours=9)
        next_7am = now_kst.replace(hour=7, minute=0, second=0, microsecond=0)
        if now_kst >= next_7am:
            next_7am += timedelta(days=1)
        sleep_secs = (next_7am - now_kst).total_seconds()
        app.logger.info(f'Next S-Code snapshot in {sleep_secs/3600:.1f} hours')
        time.sleep(sleep_secs)
        with app.app_context():
            _take_scode_snapshot(app)


def _run_pw_expiry_check(app):
    import pytz, json, redis as _redis
    from datetime import datetime as dt
    from models import Notification, AppHistory
    from helpers import (_get_ssh_client, _get_os_type, _log_history, EXCLUDED_KEYS)
    app.logger.info('Starting password expiry check...')

    # ── Prune app_history older than 2 years ──────────────────
    with app.app_context():
        try:
            cutoff = datetime.utcnow() - timedelta(days=730)
            deleted = AppHistory.query.filter(AppHistory.created_at < cutoff).delete()
            db.session.commit()
            if deleted:
                app.logger.info(f'app_history retention: deleted {deleted} records older than 2 years')
        except Exception as e:
            app.logger.error(f'app_history retention error: {e}')

    # ── Load VMs from Redis cache (avoids startup token issues) ─
    try:
        rc = _redis.Redis(host='localhost', port=6379, db=1, decode_responses=True)
        raw = rc.get('provvms:all')
        vms = json.loads(raw) if raw else []
    except Exception as e:
        app.logger.error(f'Redis VM load failed: {e}')
        vms = []

    if not vms:
        app.logger.warning('PW check: Redis cache empty, waiting 60s more...')
        time.sleep(60)
        try:
            raw = rc.get('provvms:all')
            vms = json.loads(raw) if raw else []
        except Exception:
            pass
        if not vms:
            app.logger.warning('PW check: no VMs in Redis cache after retry — skipping.')
            return

    app.logger.info(f'Checking {len(vms)} VMs for password expiry...')
    kst   = pytz.timezone('Asia/Seoul')
    found = skipped = failed = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def check_vm(vm):
        image_name  = vm.get('image', '')
        os_type     = _get_os_type(image_name)
        if os_type == 'skip' or vm.get('status', '').lower() != 'active':
            return 'skipped', None, None, None
        vm_name     = vm.get('vm_name', '')
        project_key = vm.get('project_key', '')
        private_ip  = vm.get('private_ip', '')
        key_name    = vm.get('key_name', '')
        try:
            ssh, _ = _get_ssh_client(vm_name, key_name, image_name, private_ip)
            if not ssh:
                return 'failed', vm_name, None, None
            _, out, _ = ssh.exec_command(
                "cat /etc/timezone 2>/dev/null || timedatectl 2>/dev/null | grep 'Time zone' | awk '{print $3}'",
                timeout=10)
            tz_lines  = out.read().decode().strip().splitlines()
            vm_tz_str = tz_lines[0].strip() if tz_lines else 'UTC'
            try:    vm_tz = pytz.timezone(vm_tz_str)
            except: vm_tz = pytz.utc
            _, out, _ = ssh.exec_command('sudo chage -l scv 2>/dev/null', timeout=10)
            chage_out = out.read().decode().strip()
            ssh.close()
            expiry_str = None
            for line in chage_out.splitlines():
                if 'Password expires' in line:
                    expiry_str = line.split(':', 1)[1].strip(); break
            if not expiry_str or expiry_str.lower() == 'never':
                return 'no_expiry', vm_name, project_key, None
            try:    expiry_naive = dt.strptime(expiry_str, '%b %d, %Y')
            except: return 'failed', vm_name, None, None
            expiry_kst = vm_tz.localize(expiry_naive).astimezone(kst)
            now_kst    = dt.now(kst)
            days_left  = (expiry_kst.date() - now_kst.date()).days
            return 'ok', vm_name, project_key, days_left
        except Exception as e:
            app.logger.debug(f'Expiry check failed for {vm_name}: {e}')
            return 'failed', vm_name, None, None

    results = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(check_vm, vm): vm for vm in vms}
        for future in as_completed(futures):
            results.append(future.result())

    with app.app_context():
        for status, vm_name, project_key, days_left in results:
            if status == 'skipped':
                skipped += 1
                continue
            if status == 'failed':
                failed += 1
                continue
            if status == 'no_expiry':
                notif = Notification.query.filter_by(vm_name=vm_name, resolved=False).first()
                if notif:
                    notif.resolved = True; notif.resolved_at = datetime.utcnow()
                    db.session.commit()
                continue
            # status == 'ok'
            existing = Notification.query.filter_by(vm_name=vm_name, resolved=False).first()
            if days_left <= 30:
                found += 1
                if existing:
                    existing.days_left = days_left; existing.checked_at = datetime.utcnow()
                else:
                    db.session.add(Notification(
                        vm_name=vm_name, project_key=project_key,
                        days_left=days_left, checked_at=datetime.utcnow(),
                    ))
                db.session.commit()
            else:
                if existing:
                    existing.resolved = True; existing.resolved_at = datetime.utcnow()
                    db.session.commit()
    app.logger.info(f'Password expiry check complete. found={found} skipped={skipped} failed={failed}')


def _schedule_pw_check(app):
    """Run PW expiry check at 6am KST daily. Also runs once immediately on startup."""
    import pytz
    from datetime import datetime as dt

    # Wait for VM cache prewarm to populate Redis before first run
    app.logger.info('PW check: waiting 30s for VM cache to warm up...')
    time.sleep(30)

    # Run immediately on first start to populate data
    app.logger.info('Running initial password expiry check on startup...')
    try:
        _run_pw_expiry_check(app)
    except Exception as e:
        app.logger.error(f'Initial pw check error: {e}')

    while True:
        kst      = pytz.timezone('Asia/Seoul')
        now      = dt.now(kst)
        next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= next_6am:
            next_6am = next_6am + timedelta(days=1)
        secs = (next_6am - now).total_seconds()
        app.logger.info(f'Next pw expiry check at 06:00 KST (in {secs/3600:.1f} hours)')
        time.sleep(max(secs, 60))
        try:    _run_pw_expiry_check(app)
        except Exception as e: app.logger.error(f'PW check error: {e}')


def start_background_threads(app):
    """Call this once after app is fully initialized."""
    if not _try_acquire_bg_lock():
        app.logger.info('Background threads skipped (another worker holds bg lock)')
        return
    threading.Thread(target=_refresh_all_tokens,     args=(app,), daemon=True).start()
    threading.Thread(target=_schedule_pw_check,      args=(app,), daemon=True).start()
    threading.Thread(target=_schedule_scode_snapshot, args=(app,), daemon=True).start()
    threading.Thread(target=_prewarm_vm_cache,        args=(app,), daemon=True).start()
    with app.app_context():
        from helpers import _write_tokens_file
        _write_tokens_file()
    app.logger.info('Background threads started (this worker holds bg lock)')