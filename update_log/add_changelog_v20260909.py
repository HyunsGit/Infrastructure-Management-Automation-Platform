#!/usr/bin/env python3
"""
Insert today's (2026-09-09) update-log entries.

Run on infra-ansible-01:
    python3 add_changelog_v20260909.py
"""
import sqlite3
from datetime import datetime

DB_PATH    = '/etc/mgt-api/api/infrapilot.db'
VERSION    = 'v2026.09.09'
CREATED_BY = 'system'

H2 = 'style="font-size:15px;font-weight:700;color:var(--text);margin:14px 0 6px"'
P  = 'style="margin:0 0 8px"'
UL = 'style="margin:0 0 8px;padding-left:18px"'

ENTRIES = [
    (
        "VM 프로비저닝 — 계정 상태 점 색상 PW 만료 연동",
        "IMPROVED",
        f'''<p {P}>프로비저닝 단계 컬럼의 계정(계정) 점 색상이 PW 만료 상태와 연동.</p>
        <ul {UL}>
            <li>🟢 정상 — 만료 30일 초과</li>
            <li>🟡 WARNING — 만료 30일 이내</li>
            <li>🔴 CRITICAL — 만료 7일 이내</li>
        </ul>''',
        "⚙️"
    ),
    (
        "VM 프로비저닝 — 점 애니메이션 동기화",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>동일 색상의 파이프라인 점들이 각자 다른 타이밍에 깜빡이는 문제.</p>
        <h2 {H2}>수정</h2>
        <p {P}>단일 <code>requestAnimationFrame</code> 루프로 전체 점 애니메이션 구동. 동일 색상의 모든 점이 완전히 동기화되어 동시에 깜빡임.</p>
        <ul {UL}>
            <li>🟡 Yellow (running): 1s 주기</li>
            <li>🔴 Red (error/critical): 0.5s 주기 (2배 빠름)</li>
        </ul>''',
        "🎨"
    ),
    (
        "VM 프로비저닝 — PW 만료 전체 페이지 필터",
        "NEW",
        f'''<p {P}>🔑 PW 만료 필터 버튼 추가. 전체 VM(모든 페이지)을 대상으로 필터링.</p>
        <ul {UL}>
            <li>🔴 CRITICAL (≤7일) / 🟡 WARNING (≤30일) / ⚫ EXPIRED 선택 가능</li>
            <li>DB 스냅샷 데이터 기반으로 페이지 이동 없이 전체 결과 필터링</li>
            <li>실시간 SSH 확인 결과가 있으면 DB 데이터보다 우선 적용</li>
        </ul>''',
        "🔑"
    ),
    (
        "VM 프로비저닝 — PW 만료 실시간 갱신 시 DB 동기화",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>비밀번호 갱신 후에도 홈 화면 OS PW 만료 패널과 알림에서 갱신 전 만료일 표시.</p>
        <h2 {H2}>수정</h2>
        <p {P}>계정 상태 실시간 SSH 확인 완료 시 <code>/api/notifications/pw_update</code>를 호출해 DB와 로컬 맵을 즉시 갱신. 비밀번호 갱신된 VM은 다음 날 6시까지 기다리지 않고 즉시 알림에서 제거.</p>''',
        "🔑"
    ),
    (
        "VM 프로비저닝 — OS 비밀번호 일괄 갱신 인증 오류 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>OS 비밀번호 일괄 갱신 플레이북 실행 시 일부 VM에서 SSH 인증 실패 (<code>Permission denied</code>).</p>
        <h2 {H2}>원인</h2>
        <ul {UL}>
            <li>Ansible 인벤토리가 <code>ansible_user=ubuntu</code>로 생성 (PEM 키 방식)</li>
            <li>비밀번호 갱신 플레이북은 이미 생성된 <code>scv</code> 계정으로 접속해야 함</li>
            <li>그룹 내 첫 번째 VM의 PEM 키를 나머지 VM에도 적용하던 문제</li>
        </ul>
        <h2 {H2}>수정</h2>
        <p {P}>비밀번호 갱신 플레이북 감지 시 <code>ansible_user=scv</code> + <code>ansible_ssh_pass</code> 패스워드 인증으로 인벤토리 생성.</p>''',
        "🔧"
    ),
    (
        "KakaoCloud 이미지 API — 페이지네이션 대응",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>VM 생성 이미지 목록에 Kubernetes 관련 이미지만 표시되고 Ubuntu / Rocky 등 일반 이미지 미노출.</p>
        <h2 {H2}>원인</h2>
        <p {P}>KakaoCloud 이미지 API에 페이지네이션 적용 (기본 20건 반환). 기존 코드는 첫 번째 페이지만 읽어 알파벳 순 상위 20개(대부분 K8s 이미지)만 표시.</p>
        <h2 {H2}>수정</h2>
        <p {P}><code>offset</code> 기반 전체 페이지 순회 적용. 현재 65개 전체 이미지 정상 표시.</p>''',
        "🔧"
    ),
]


def main():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    cur.execute("DELETE FROM app_changelog WHERE version = ?", (VERSION,))
    deleted = cur.rowcount
    if deleted:
        print(f"Removed {deleted} existing entries for {VERSION}.")

    now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
    rows = [
        (VERSION, title, change_type, description, None, icon, now, CREATED_BY)
        for title, change_type, description, icon in ENTRIES
    ]
    cur.executemany(
        """INSERT INTO app_changelog
           (version, title, change_type, description, image_url, icon, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows
    )
    conn.commit()
    print(f"Inserted {len(rows)} changelog entries for {VERSION}.")
    conn.close()


if __name__ == '__main__':
    main()