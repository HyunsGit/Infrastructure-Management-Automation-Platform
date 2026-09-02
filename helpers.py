# helpers.py — shared helper functions used across routes
import os
import time
import requests
import paramiko
import pandas as pd
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from env_manager import get_env_var
from extensions import db

# ── Imported at runtime to avoid circular imports ────────────
def _get_models():
    from models import (DBUser, IAMHistory, Project, DeletedProject,
                        AppHistory, Notification, SCodeRule, SCodeVmSnapshot,
                        SCodeSnapshot, SCodeInfo, SeenNotification, SeenKcNotice)
    return (DBUser, IAMHistory, Project, DeletedProject, AppHistory,
            Notification, SCodeRule, SCodeVmSnapshot, SCodeSnapshot,
            SCodeInfo, SeenNotification, SeenKcNotice)

# ── Constants ─────────────────────────────────────────────────
IAM_URL               = 'https://iam.your-cloud.example.com/identity/v3/auth/tokens'
TOKEN_FILE            = '/etc/mgt-api/.tokens'
TOKEN_REFRESH_INTERVAL = 6 * 3600
TOKEN_CHECK_INTERVAL   = 30 * 60
PUPPET_MASTER          = 'puppet-master'
PEM_DIR                = '/etc/ansible/pem'
SSH_USERNAME           = 'scv'
SSH_PASSWORD           = os.environ.get('SSH_PASSWORD', '')
KAKAOCLOUD_COMPUTE_URL = 'https://compute.your-cloud.example.com/api/v1/instances'
EXCLUDED_KEYS = {
    'ROUTE53_HOSTED_ZONE_ID', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
    'AWS_REGION', 'ANSIBLE_PLAYBOOK_PATH', 'PUPPET_APPLY_CMD',
    'FLASK_SECRET_KEY', 'ADMIN_USERNAME', 'ADMIN_PASSWORD'
}

_puppet_cert_cache     = {'data': None, 'ts': 0}
_puppet_cert_cache_ttl = 60


# ── S-Code helpers ────────────────────────────────────────────
def _get_all_scode_rules() -> list:
    from models import SCodeRule
    try:
        return SCodeRule.query.filter_by(zone='global') \
            .order_by(SCodeRule.is_default, SCodeRule.priority).all()
    except Exception:
        return []


def _resolve_scode(hostname: str, rules: list) -> str:
    hostname_lower = hostname.casefold()
    sorted_rules = sorted([r for r in rules if not r.is_default], key=lambda r: r.priority)
    for rule in sorted_rules:
        val = rule.match_value.casefold()
        if rule.match_type == 'contains'     and val in hostname_lower:            return rule.scode
        if rule.match_type == 'contains_all' and all(p.strip() in hostname_lower for p in val.split(',') if p.strip()): return rule.scode
        if rule.match_type == 'startswith'   and hostname_lower.startswith(val):  return rule.scode
        if rule.match_type == 'endswith'     and hostname_lower.endswith(val):    return rule.scode
        if rule.match_type == 'exact'        and hostname_lower == val:           return rule.scode
    for rule in rules:
        if rule.is_default:
            return rule.scode
    return None


def _apply_scodes_for_project(zone: str, token: str) -> tuple:
    """DEPRECATED: S-Codes are now managed in-app, not written to KakaoCloud."""
    from flask import current_app
    current_app.logger.info(f"S-Code apply skipped for {zone} — managed in-app now")
    return 0, 0



def _fetch_kc_token(access_id: str, secret_key: str) -> str | None:
    from flask import current_app
    try:
        resp = requests.post(IAM_URL, json={
            'auth': {
                'identity': {
                    'methods': ['application_credential'],
                    'application_credential': {'id': access_id, 'secret': secret_key}
                }
            }
        }, headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, timeout=15)
        token = resp.headers.get('X-Subject-Token')
        if not token:
            current_app.logger.error(f'Token fetch failed for {access_id}: {resp.status_code} {resp.text[:200]}')
        return token
    except Exception as e:
        current_app.logger.error(f'Token fetch exception: {e}')
        return None


def _write_tokens_file():
    from flask import current_app
    from models import Project
    static_keys = {
        'ROUTE53_HOSTED_ZONE_ID', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
        'AWS_REGION', 'ANSIBLE_PLAYBOOK_PATH', 'PUPPET_APPLY_CMD',
        'FLASK_SECRET_KEY', 'ADMIN_USERNAME', 'ADMIN_PASSWORD'
    }
    static_lines = []
    try:
        with open(TOKEN_FILE, 'r') as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    continue
                key = stripped.split('=')[0]
                if key in static_keys:
                    static_lines.append(line)
    except FileNotFoundError:
        pass

    db.session.expire_all()
    with open(TOKEN_FILE, 'w') as f:
        for proj in Project.query.order_by(Project.zone).all():
            f.write(f'# {proj.zone}\n')
            f.write(f'{proj.zone}={proj.token or ""}\n\n')
        f.write('\n# ── Static configuration ──\n')
        for line in static_lines:
            f.write(line)
    current_app.logger.info('Regenerated .tokens file')


def _seed_projects_from_tokens():
    from flask import current_app
    from models import Project, DeletedProject
    _excluded = EXCLUDED_KEYS
    try:
        with open(TOKEN_FILE, 'r') as f:
            lines = f.readlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip(); val = val.strip()
            if key in _excluded or not val or val.lower() == 'none':
                continue
            if DeletedProject.query.get(key):
                continue
            if not Project.query.get(key):
                db.session.add(Project(
                    zone=key, access_id='', secret_key='',
                    token=val, token_refreshed_at=None,
                ))
        db.session.commit()
        current_app.logger.info('Seeded projects from .tokens file')
    except Exception as e:
        current_app.logger.error(f'Failed to seed projects: {e}')


def _log_history(event_type: str, title: str, detail: str = None, username: str = None):
    from flask import current_app
    from models import AppHistory
    try:
        db.session.add(AppHistory(
            event_type=event_type, title=title,
            detail=detail, username=username,
            created_at=datetime.utcnow(),
        ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.debug(f'History log failed: {e}')


# ── Project helpers ───────────────────────────────────────────
def get_projects_from_dotenv():
    projects = []
    try:
        with open(TOKEN_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    projects.append(line.split('=')[0])
    except Exception:
        pass
    return projects


def _list_all_vms_by_project(project_key: str) -> list:
    token = get_env_var(project_key, default='')
    if not token or token.strip().lower() == 'none':
        return []
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Auth-Token': token,
    }
    vms = []; marker = None
    try:
        while True:
            params = {'limit': 200}
            if marker: params['marker'] = marker
            resp = requests.get(KAKAOCLOUD_COMPUTE_URL, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            instances = data.get('instances', [])
            for inst in instances:
                inst['_project_key'] = project_key
            vms.extend(instances)
            marker = data.get('next_marker') or data.get('next')
            if not marker: break
    except Exception as e:
        pass
    return vms


def _find_kakaocloud_vm(hostname: str, token: str) -> dict | None:
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Auth-Token': token,
    }
    marker = None
    try:
        while True:
            params = {'limit': 200}
            if marker: params['marker'] = marker
            resp = requests.get(KAKAOCLOUD_COMPUTE_URL, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            for inst in data.get('instances', []):
                if hostname in inst.get('name', ''):
                    return inst
            marker = data.get('next_marker') or data.get('next')
            if not marker: break
    except Exception as e:
        pass
    return None


def _find_vm_across_all_projects(hostname: str) -> dict | None:
    short_name = hostname.split('.')[0]
    for project_key in get_projects_from_dotenv():
        if project_key in EXCLUDED_KEYS: continue
        token = get_env_var(project_key, default='')
        if not token or token.strip().lower() == 'none': continue
        try:
            vm = _find_kakaocloud_vm(short_name, token)
            if vm: return vm
        except Exception:
            continue
    return None


# ── OS type helpers ───────────────────────────────────────────
def _is_ubuntu_24_or_higher(image_name: str) -> bool:
    import re
    match = re.search(r'Ubuntu\s+(\d+)\.(\d+)', image_name, re.IGNORECASE)
    if not match: return False
    major, minor = int(match.group(1)), int(match.group(2))
    return major > 24 or (major == 24 and minor >= 4)


def _get_os_type(image_name: str) -> str:
    import re
    if not image_name or image_name == 'K8s node': return 'skip'
    if 'windows' in image_name.lower(): return 'skip'
    if re.search(r'Ubuntu\s+24', image_name, re.IGNORECASE): return 'ubuntu24'
    if re.search(r'Ubuntu\s+(18|20|22)', image_name, re.IGNORECASE): return 'legacy'
    if 'rocky' in image_name.lower(): return 'legacy'
    return 'skip'


# ── SSH helpers ───────────────────────────────────────────────
def _load_pem_key(pem_path: str):
    for key_class in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
        try:
            return key_class.from_private_key_file(pem_path)
        except paramiko.ssh_exception.SSHException:
            continue
        except Exception:
            continue
    raise ValueError(f'Could not load pem key: {pem_path}')


def _get_ssh_client_with_retry(hostname: str, key_name: str, image: str,
                               private_ip: str = '', retries: int = 2,
                               interval: int = 1) -> tuple:
    """Wrapper around _get_ssh_client with retry logic.
    Retries up to `retries` times with `interval` seconds between attempts."""
    import time, logging
    _log = logging.getLogger(__name__)
    connect_target = private_ip if private_ip else hostname
    for attempt in range(1, retries + 1):
        ssh, target = _get_ssh_client(hostname, key_name, image, private_ip)
        if ssh is not None:
            if attempt > 1:
                _log.debug(f'SSH connected to {connect_target} on attempt {attempt}')
            return ssh, target
        if attempt < retries:
            _log.debug(f'SSH failed for {connect_target} (attempt {attempt}/{retries}), retrying in {interval}s')
            time.sleep(interval)
    _log.debug(f'SSH failed for {connect_target} after {retries} attempts')
    return None, connect_target


def _resolve_pem_path(key_name: str) -> str:
    """Dynamically resolve PEM file from key_name — no hardcoded mapping.
    Tries: {key_name}.pem → key-{key_name}.pem → key-{key_name}.pem
    Returns the first existing path, or empty string if none found."""
    if not key_name:
        return ''
    candidates = [
        f'{key_name}.pem',
        f'key-{key_name}.pem',
        f'key-{key_name}.pem',
    ]
    for filename in candidates:
        path = os.path.join(PEM_DIR, filename)
        if os.path.exists(path):
            return path
    return ''


def _get_ssh_client(hostname: str, key_name: str, image: str, private_ip: str = '') -> tuple:
    connect_target = private_ip if private_ip else hostname
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(connect_target, username=SSH_USERNAME, password=SSH_PASSWORD, timeout=6)
        return ssh, connect_target
    except Exception:
        pass
    pem_path = _resolve_pem_path(key_name)
    if pem_path and os.path.exists(pem_path):
        try:
            pkey = _load_pem_key(pem_path)
            is_rocky = 'rocky' in image.lower()
            fallback_user = 'rocky' if is_rocky else 'ubuntu'
            ssh2 = paramiko.SSHClient()
            ssh2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh2.connect(connect_target, username=fallback_user, pkey=pkey, timeout=6)
            return ssh2, connect_target
        except Exception:
            pass
    return None, connect_target


# ── Puppet cert helpers ───────────────────────────────────────
def _get_puppet_cert_list() -> dict:
    now = time.time()
    if _puppet_cert_cache['data'] is not None and (now - _puppet_cert_cache['ts']) < _puppet_cert_cache_ttl:
        return _puppet_cert_cache['data']
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    result = {}
    try:
        ssh.connect(PUPPET_MASTER, username=SSH_USERNAME, password=SSH_PASSWORD, timeout=8)
        _, stdout, _ = ssh.exec_command('sudo puppetserver ca list --all 2>/dev/null', timeout=30)
        output = stdout.read().decode()
        ssh.close()
        in_signed = in_requested = False
        for line in output.splitlines():
            if 'Signed Certificates:' in line:     in_signed = True; in_requested = False; continue
            if 'Requested Certificates:' in line:  in_requested = True; in_signed = False; continue
            line = line.strip()
            if not line: continue
            parts = line.split()
            if parts:
                fqdn = parts[0]; short = fqdn.split('.')[0]
                if in_signed:    result[short] = 'signed';    result[fqdn] = 'signed'
                elif in_requested: result[short] = 'requested'; result[fqdn] = 'requested'
        _puppet_cert_cache['data'] = result
        _puppet_cert_cache['ts']   = now
    except Exception:
        pass
    return result


def _check_puppet_cert_status(hostname: str) -> str:
    short = hostname.split('.')[0]
    try:
        cert_list = _get_puppet_cert_list()
        if not cert_list: return 'error'
        return cert_list.get(short, cert_list.get(hostname, 'none'))
    except Exception:
        return 'error'


# ── Ansible inventory builder ─────────────────────────────────
PROJECT_CREDS = {
    # Maps project_id → (access_id, secret_key)
    # Loaded from environment variables or a secrets manager in production.
    # Example:
    # '<project_id>': (os.environ.get('PROJECT_ACCESS_ID_1', ''), os.environ.get('PROJECT_SECRET_KEY_1', '')),
}


def _build_ansible_inventory(hostname: str, private_ip: str, image: str, key_name: str, project_id: str = '') -> tuple:
    import tempfile
    inventory_target = private_ip if private_ip else hostname
    is_rocky         = 'rocky' in image.lower()
    os_group         = 'rocky' if is_rocky else 'ubuntu'
    ansible_user     = 'rocky' if is_rocky else 'ubuntu'
    pem_path         = _resolve_pem_path(key_name)
    os_type          = _get_os_type(image)
    if os_type == 'ubuntu24':
        playbook_path = '/etc/ansible/playbook/TOBE-Default-Playbook-V3.yaml'
    else:
        playbook_path = '/etc/ansible/playbook/Default-Playbook-V3.yaml'
    access_id, secret_key = PROJECT_CREDS.get(project_id, ('', ''))
    inventory_content = f"""[{os_group}]
{hostname} ansible_host={inventory_target}

[{os_group}:vars]
ansible_ssh_private_key_file={pem_path}
ansible_connection=ssh
ansible_user={ansible_user}
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False, prefix='ansible_inv_') as f:
        f.write(inventory_content)
        inv_path = f.name
    return inv_path, access_id, secret_key, playbook_path