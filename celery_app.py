# celery_app.py
from celery import Celery
import os
import re
import json
import time
import requests
import subprocess
from env_manager import get_env_var
import logging

celery_app = Celery(
    'tasks',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/1'
)

celery_app.conf.update(
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='Asia/Seoul',
    enable_utc=True,
)

def get_vm_info_by_name(vm_name, token):
    url = f"https://compute.your-cloud.example.com/api/v1/instances?name={vm_name}"
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Auth-Token': token
    }
    #headers = {'X-Auth-Token': token, 'Content-type': 'application/json'}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        for vm in data.get('instances', []):
            if vm.get('name') == vm_name:
                return vm
    except Exception as e:
        return None
    return None

@celery_app.task(bind=True)
def create_volumes_task(self, task_args):
    token = get_env_var(task_args['token_key'], required=True)
    vm_list = task_args['vm_list']
    volume_type_a = task_args['volume_type_a']
    disk_size_a = task_args['disk_size_a']
    volume_type_b = task_args['volume_type_b']
    disk_size_b = task_args['disk_size_b']
    
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Auth-Token': token
    }
    #headers = {'X-Auth-Token': token, 'Content-type': 'application/json'}

    # First count total volume create steps needed
    total_steps = 0
    vm_infos = []
    for vm_name in vm_list:
        vm_info = get_vm_info_by_name(vm_name, token)
        vm_infos.append((vm_name, vm_info))
        if not vm_info or not vm_info.get('availability_zone'):
            continue
        az = vm_info['availability_zone']
        if az == "kr-central-2-a":
            total_steps += len(volume_type_a)
        elif az == "kr-central-2-b":
            total_steps += len(volume_type_b)

    current_step = 0
    results = []

    for vm_name, vm_info in vm_infos:
        if not vm_info or not vm_info.get('availability_zone'):
            results.append({'vm': vm_name, 'error': 'VM 정보 없음 또는 AZ 없음'})
            continue

        az = vm_info['availability_zone']
        if az == "kr-central-2-a":
            volume_types = volume_type_a
            disk_sizes = disk_size_a
        elif az == "kr-central-2-b":
            volume_types = volume_type_b
            disk_sizes = disk_size_b
        else:
            results.append({'vm': vm_name, 'error': f'알 수 없는 AZ {az}'})
            continue

        if not volume_types or not disk_sizes:
            results.append({'vm': vm_name, 'error': '볼륨 타입 또는 디스크 크기 미입력'})
            continue

        for vtype, dsize in zip(volume_types, disk_sizes):
            url = 'https://volume.your-cloud.example.com/api/v1/volumes'
            json_data = {
                "volume": {
                    "name": f"{vm_name}-{vtype}",
                    "size": dsize,
                    "availability_zone": az
                }
            }
            try:
                response = requests.post(url, json=json_data, headers=headers, timeout=10)
                res = response.json()
                results.append({
                    'vm': vm_name,
                    'volume': f"{vm_name}-{vtype}",
                    'response': res,
                    'success': response.ok
                })
            except Exception as e:
                results.append({
                    'vm': vm_name,
                    'volume': f"{vm_name}-{vtype}",
                    'response': {'error': str(e)},
                    'success': False
                })
            current_step += 1
            progress = (current_step / total_steps) * 100 if total_steps > 0 else 100
            status = f"{vm_name} / {vtype}: {'Success' if response.ok else 'Error'}"
            step_text = f"Step {current_step} of {total_steps}"
            self.update_state(
                state='PROGRESS',
                meta={
                    'current': current_step,
                    'total': total_steps,
                    'percent': progress,
                    'status': status,
                    'step_text': step_text
                }
            )
    return results

@celery_app.task(bind=True)
def attach_volumes_task(self, task_args):
    logger = logging.getLogger(__name__)

    token = get_env_var(task_args['token_key'], required=True)
    vm_list = task_args['vm_list']
    vm_id_list = task_args['vm_id_list']   # Must contain *instance UUIDs*
    vol_type = task_args['vol_type']

    logger.info("--- START VOLUME ATTACH TASK ---")
    logger.info(f"VMs: {vm_list}")
    logger.info(f"VM IDs: {vm_id_list}")
    logger.info(f"Volume types: {vol_type}")

    headers = {
        'X-Auth-Token': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    # --- Fetch all volumes with paging ---
    all_volumes = []
    url = 'https://volume.your-cloud.example.com/api/v1/volumes'
    marker = None
    while True:
        params = {'limit': 100}
        if marker:
            params['marker'] = marker
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        volumes = data.get('volumes', [])
        if not volumes:
            break
        all_volumes.extend(volumes)
        if len(volumes) < 200:
            break
        marker = volumes[-1]['id']

    logger.info(f"Fetched {len(all_volumes)} volumes")

    # --- Retry function for attach ---
    MAX_RETRIES = 5
    RETRY_BACKOFF_FACTOR = 2
    INITIAL_RETRY_WAIT = 1

    def attach_volume_with_retries(url, headers, json_data):
        wait = INITIAL_RETRY_WAIT
        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"POST {url} | Payload: {json_data}")
                response = requests.post(url, headers=headers, json=json_data, timeout=10)
                if response.status_code == 429:
                    logger.warning(f"429 Too Many Requests - retrying after {wait}s (attempt {attempt+1})")
                    time.sleep(wait)
                    wait *= RETRY_BACKOFF_FACTOR
                    continue
                return response
            except requests.RequestException as e:
                logger.error(f"Request exception on attempt {attempt+1}: {str(e)}")
                time.sleep(wait)
                wait *= RETRY_BACKOFF_FACTOR
        return None

    # --- Attachment loop ---
    total_steps = sum(1 for vm in vm_list for vt in vol_type)
    current_step = 0
    results = []

    for vm, vm_id in zip(vm_list, vm_id_list):
        logger.info(f"-- PROCESSING VM: {vm} (Instance ID: {vm_id}) --")
        for vt in vol_type:
            vol_name = f"{vm}-{vt}"
            # Find volume ID by name
            volume_id = next((v['id'] for v in all_volumes if v['name'] == vol_name), None)

            logger.info(f"VM: {vm} | VM_ID: {vm_id} | Volume Name: {vol_name} | Volume ID: {volume_id}")

            if not volume_id:
                logger.warning(f"    SKIP: Volume '{vol_name}' not found in Kakao Cloud")
                current_step += 1
                continue

            # Attach API call (works in your standalone test)
            attach_url = f"https://compute.your-cloud.example.com/api/v1/instances/{vm_id}/volumes/{volume_id}"
            json_data = {
                "volume": {
                    "is_delete_on_termination": False
                }
            }

            try:
                response = attach_volume_with_retries(attach_url, headers, json_data)
                if response is None:
                    raise Exception("No response after retries")

                logger.info(f"    ATTACH RESPONSE: HTTP {response.status_code} << {response.text}")

                result = {
                    'vm': vm,
                    'volume': vol_name
                }

                if not response.ok:
                    try:
                        error_body = response.json()
                        error_msg = error_body.get('error', str(error_body))
                    except ValueError:
                        error_msg = response.text or "Unknown error"
                    raise Exception(error_msg)

                try:
                    res = response.json()
                    result['success'] = True
                    result['response'] = res
                except ValueError:
                    result['success'] = True
                    result['response'] = {
                        'status': 'attached',
                        'note': 'Response not JSON',
                        'raw': response.text
                    }

                results.append(result)

            except Exception as e:
                logger.error(f"    ATTACH EXCEPTION: {str(e)}", exc_info=True)
                results.append({
                    'vm': vm,
                    'volume': vol_name,
                    'success': False,
                    'response': {'error': str(e)}
                })

            # --- Progress tracking ---
            current_step += 1
            progress = (current_step / total_steps) * 100 if total_steps > 0 else 100
            status = f"{vm} / {vt}: {'Success' if results[-1]['success'] else 'Failed'}"
            self.update_state(
                state='PROGRESS',
                meta={
                    'current': current_step,
                    'total': total_steps,
                    'percent': progress,
                    'status': status,
                    'step_text': f"Step {current_step} of {total_steps}"
                }
            )

            time.sleep(0.2)  # small delay to avoid rate limit hits

    logger.info("--- TASK COMPLETE ---")
    logger.info(f"RESULTS: {results}")
    return results