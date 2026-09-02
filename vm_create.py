# vm_create.py — "VM 생성" feature: list project resources (subnet/image/
# flavor/keypair/security-group), create volumes, and create instances on
# KakaoCloud. Mirrors the pagination/auth pattern already used elsewhere
# in this app (see helpers.py's _list_all_vms_by_project, etc.)

import time
import requests

BCS_BASE     = 'https://compute.your-cloud.example.com/api/v1'
VPC_BASE     = 'https://vpc.your-cloud.example.com/api/v1'
NETWORK_BASE = 'https://network.your-cloud.example.com/api/v1'
VOLUME_BASE  = 'https://volume.your-cloud.example.com/api/v1'
IMAGE_BASE   = 'https://image.your-cloud.example.com/api/v1'


def _headers(token: str) -> dict:
    return {
        'Content-Type': 'application/json',
        'Accept':       'application/json',
        'X-Auth-Token': token,
    }


def _paginate(token: str, url: str, key: str, limit: int = 20) -> list:
    """Generic offset/limit paginator with retry on 429 rate limit."""
    results = []
    offset  = 0
    while True:
        for attempt in range(999):  # effectively unlimited
            resp = requests.get(url, headers=_headers(token),
                                params={'offset': offset, 'limit': limit}, timeout=15)
            if resp.status_code != 429:
                resp.raise_for_status()
                break
            wait = min(2 ** attempt, 30)
            time.sleep(wait)

        body       = resp.json()
        data       = body.get(key, [])
        pagination = body.get('pagination', {})
        total      = pagination.get('total', len(data))
        if not data:
            break
        results.extend(data)
        if len(results) >= total:
            break
        offset += limit
        time.sleep(0.2)  # small delay between pages to avoid triggering rate limit
    return results


def _request_with_retry(method: str, url: str, token: str, **kwargs) -> 'requests.Response':
    """Wrap a single requests call with unlimited retries on 429.
    Backoff: 1s → 2s → 4s → 8s → 16s → 30s (capped), retries until success."""
    attempt = 0
    while True:
        resp = getattr(requests, method)(url, headers=_headers(token), **kwargs)
        if resp.status_code != 429:
            return resp
        wait = min(2 ** attempt, 30)  # cap at 30s
        time.sleep(wait)
        attempt += 1


# ── Resource listing (for populating dropdowns) ───────────────────────────

def list_subnets(token: str) -> list:
    """All subnets visible to this project's token."""
    return _paginate(token, f'{VPC_BASE}/subnets', 'subnets')


def list_vpcs(token: str) -> list:
    """Best-effort — used only to resolve vpc_name for subnets that don't
    already include it inline."""
    try:
        resp = requests.get(f'{VPC_BASE}/vpcs', headers=_headers(token), timeout=15)
        resp.raise_for_status()
        return resp.json().get('vpcs', [])
    except Exception:
        return []


def list_security_groups(token: str) -> list:
    return _paginate(token, f'{NETWORK_BASE}/security-groups', 'security_groups')


def list_keypairs(token: str) -> list:
    return _paginate(token, f'{BCS_BASE}/keypairs', 'keypairs')


def list_flavors(token: str) -> list:
    return _paginate(token, f'{BCS_BASE}/flavors', 'flavors')


def list_images(token: str) -> list:
    """Images list — fetches all pages using KakaoCloud offset pagination."""
    images = []
    limit  = 100
    offset = 0
    while True:
        resp = _request_with_retry(
            'get', f'{IMAGE_BASE}/images', token,
            params={'limit': limit, 'offset': offset}, timeout=15
        )
        resp.raise_for_status()
        data        = resp.json()
        page_images = data.get('images', [])
        images.extend(page_images)
        pagination  = data.get('pagination', {})
        total       = pagination.get('total', 0)
        offset     += len(page_images)
        if offset >= total or not page_images:
            break
    return images


def list_all_resources(token: str) -> dict:
    """Fetch all dropdown resources for a project in parallel.
    Each resource type hits a different endpoint/host so parallel
    calls don't share rate limit buckets."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    tasks = {
        'vpcs':            lambda: list_vpcs(token),
        'subnets':         lambda: list_subnets(token),
        'security_groups': lambda: list_security_groups(token),
        'keypairs':        lambda: list_keypairs(token),
        'flavors':         lambda: list_flavors(token),
        'images':          lambda: list_images(token),
    }

    results = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(futures):
            key = futures[future]
            results[key] = future.result()

    # Enrich subnets with vpc_name if not already present
    vpc_name_by_id = {v['id']: v.get('name', v['id']) for v in results.get('vpcs', [])}
    for s in results.get('subnets', []):
        if not s.get('vpc_name'):
            s['vpc_name'] = vpc_name_by_id.get(s.get('vpc_id', ''), s.get('vpc_id', ''))

    return {
        'subnets':         results.get('subnets', []),
        'security_groups': results.get('security_groups', []),
        'keypairs':        results.get('keypairs', []),
        'flavors':         results.get('flavors', []),
        'images':          results.get('images', []),
    }


# ── Volume helpers ──────────────────────────────────────────────────────

def create_volume(token: str, name: str, size: int, availability_zone: str,
                  on_tick=None) -> str:
    """Create a named volume and return its UUID."""
    payload = {
        'volume': {
            'name':              name,
            'size':              size,
            'availability_zone': availability_zone,
        }
    }
    resp = _request_with_retry('post', f'{VOLUME_BASE}/volumes', token,
                               json=payload, timeout=15)
    if resp.status_code == 409:
        raise Exception(f"볼륨 이름 '{name}'이 이미 존재합니다. 다른 이름을 사용하거나 기존 볼륨을 먼저 삭제해주세요.")
    resp.raise_for_status()
    return resp.json()['volume']['id']


def wait_for_volume(token: str, volume_id: str, timeout: int = 180,
                     interval: int = 8, initial_delay: int = 5,
                     on_tick=None) -> None:
    """Poll until volume status is 'available'. `on_tick` is an optional
    callback(message: str) used to stream progress lines (SSE etc.)."""
    if on_tick:
        on_tick(f'볼륨 {volume_id} 사용 가능 상태 대기 중...')
    time.sleep(initial_delay)

    elapsed = 0
    while elapsed < timeout:
        resp = _request_with_retry('get', f'{VOLUME_BASE}/volumes/{volume_id}',
                                     token, timeout=15)
        if resp.status_code == 404:
            time.sleep(interval)
            elapsed += interval
            continue
        resp.raise_for_status()
        status = resp.json()['volume']['status']
        if status == 'available':
            if on_tick:
                on_tick(f'볼륨 {volume_id} 사용 가능')
            return
        time.sleep(interval)
        elapsed += interval
    raise TimeoutError(f'볼륨 {volume_id}이 {timeout}초 내에 사용 가능 상태가 되지 않았습니다.')


# ── Instance creation ──────────────────────────────────────────────────

def create_instance(token: str, name: str, image_id: str, flavor_id: str,
                     subnet_id: str, availability_zone: str,
                     security_group_names: list,
                     keypair_name: str,
                     root_volume_size: int,
                     extra_volume_ids: list) -> dict:
    """Create one instance. extra_volume_ids is a list of already-created
    volume UUIDs (each with is_delete_on_termination=False per requirement)."""
    volumes = [
        {
            'is_delete_on_termination': True,
            'size':        root_volume_size,
            'source_type': 'image',
            'uuid':        image_id,
        }
    ]
    for vol_id in extra_volume_ids:
        volumes.append({
            'is_delete_on_termination': False,
            'source_type': 'volume',
            'uuid':        vol_id,
        })

    payload = {
        'instance': {
            'name':                       name,
            'description':                '',
            'count':                      1,
            'image_id':                   image_id,
            'flavor_id':                  flavor_id,
            'availability_zone':          availability_zone,
            'subnets':                    [{'id': subnet_id}],
            'volumes':                    volumes,
            'key_name':                   keypair_name,
            'security_groups':            [{'name': sg} for sg in security_group_names],
            'user_data':                  '',
            'is_disable_hyper_threading': False,
        }
    }
    resp = _request_with_retry('post', f'{BCS_BASE}/instances', token,
                               json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()