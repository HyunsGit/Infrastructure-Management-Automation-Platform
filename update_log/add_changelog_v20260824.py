#!/usr/bin/env python3
"""
Insert today's (2026-08-24) update-log entries.

Run on infra-ansible-01:
    python3 add_changelog_v20260824.py
"""
import sqlite3
from datetime import datetime

DB_PATH    = '/etc/mgt-api/api/infrapilot.db'
VERSION    = 'v2026.08.24'
CREATED_BY = 'system'

H2 = 'style="font-size:15px;font-weight:700;color:var(--text);margin:14px 0 6px"'
P  = 'style="margin:0 0 8px"'
UL = 'style="margin:0 0 8px;padding-left:18px"'

ENTRIES = [
    (
        "홈 화면 — OS PW 만료 전용 패널 추가",
        "NEW",
        f'''<p {P}>홈 화면 우측에 OS 사용자 비밀번호 만료 현황을 보여주는 전용 패널이 추가되었습니다.</p>
        <ul {UL}>
            <li>전체 / CRITICAL(≤7일) / WARNING(≤30일) VM 수 요약 표시</li>
            <li>VM별 만료 상태 목록 (스크롤 가능)</li>
            <li>기존 3×2 그리드 우측에 2행 높이로 배치</li>
            <li>새로고침 버튼으로 즉시 갱신 가능</li>
        </ul>''',
        "🔑"
    ),
    (
        "OS PW 만료 — 자동 점검 시스템 구축",
        "NEW",
        f'''<p {P}>전체 VM의 scv OS 사용자 비밀번호 만료일을 자동으로 점검하고 DB에 저장합니다.</p>
        <ul {UL}>
            <li>매일 <b>오전 6시 KST</b> 자동 실행</li>
            <li>서비스 재시작 시 즉시 1회 실행 (Redis VM 캐시 활용)</li>
            <li>977개 VM을 <b>20개 병렬 SSH</b>로 약 45초 내 점검 완료</li>
            <li>만료 30일 이내 VM을 알림 DB에 저장</li>
            <li>만료 해소 시 자동으로 알림 해제</li>
        </ul>''',
        "🔑"
    ),
    (
        "알림 — 사용자 알림 OS PW 만료 표시",
        "IMPROVED",
        f'''<h2 {H2}>변경 내용</h2>
        <p {P}>알림 벨(🔔)의 <b>사용자 알림</b> 탭과 <b>최근</b> 탭에서 OS PW 만료 알림을 동일한 디자인으로 표시합니다.</p>
        <ul {UL}>
            <li>만료 VM 전체를 하나의 그룹 항목으로 표시</li>
            <li>배지: <b>PW 만료 — N개 VM</b></li>
            <li>상세: 전체 VM 목록 및 잔여 일수</li>
        </ul>''',
        "🔔"
    ),
    (
        "알림 기준 — 30일 이내 만료 VM 포함",
        "IMPROVED",
        f'''<h2 {H2}>변경 전</h2>
        <p {P}>만료 10일 이내 VM만 알림 생성.</p>
        <h2 {H2}>변경 후</h2>
        <p {P}>VM 프로비저닝과 동일한 기준인 <b>만료 30일 이내</b> VM을 알림 생성.</p>
        <ul {UL}>
            <li>≤7일: CRITICAL (🔴)</li>
            <li>8~30일: WARNING (🟡)</li>
        </ul>''',
        "🔔"
    ),
    (
        "VM 프로비저닝 — Acc 체크 완료 항목 추가",
        "NEW",
        f'''<p {P}>Ansible / Puppet 완료 이후 <b>scv 계정 존재 여부</b>를 추가로 확인합니다.</p>
        <ul {UL}>
            <li>툴바에 <b>Acc 체크 완료</b> 카운터 추가</li>
            <li>파이프라인에 <b>계정</b> 점검 단계 추가</li>
            <li><b>모두 완료</b> = Ansible + Puppet + Acc 체크 모두 완료</li>
            <li>Puppet 체크와 동일한 SSH 세션 재사용 (추가 연결 없음)</li>
        </ul>''',
        "⚙️"
    ),
    (
        "CloudTrail — PW 만료 임박 이벤트 제거",
        "IMPROVED",
        f'''<h2 {H2}>변경 내용</h2>
        <p {P}>CloudTrail은 실제 작업(VM 생성, Ansible 실행, 토큰 갱신 등)만 기록합니다.</p>
        <p {P}>OS 비밀번호 만료 임박은 모니터링 알림이므로 CloudTrail에서 제거하고, 전용 OS PW 만료 패널 및 알림 벨에서 확인할 수 있습니다.</p>''',
        "📜"
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