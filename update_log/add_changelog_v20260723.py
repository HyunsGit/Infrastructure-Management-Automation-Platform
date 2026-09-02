#!/usr/bin/env python3
"""
Insert today's (2026-07-23) update-log entries.

Run on infra-ansible-01:
    python3 add_changelog_v20260723.py
"""
import sqlite3
from datetime import datetime

DB_PATH    = '/etc/mgt-api/api/infrapilot.db'
VERSION    = 'v2026.07.23'
CREATED_BY = 'system'

H2 = 'style="font-size:15px;font-weight:700;color:var(--text);margin:14px 0 6px"'
P  = 'style="margin:0 0 8px"'
UL = 'style="margin:0 0 8px;padding-left:18px"'

ENTRIES = [
    (
        "VM 생성 — VM 목록 사이드바 상세 정보 표시",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>VM 목록 사이드바에 VM 이름과 플레이버만 표시되어 전체 구성을 한눈에 파악하기 어려웠음.</p>
        <h2 {H2}>개선 내용</h2>
        <p {P}>사이드바 너비를 확장하고, 각 VM의 전체 구성을 표시:</p>
        <ul {UL}>
            <li>프로젝트, 이미지, 플레이버, 서브넷, 키페어, 보안 그룹, 루트 볼륨, 추가 볼륨</li>
            <li>미선택 항목은 <i>미선택</i>으로 표시하여 누락 항목 즉시 파악 가능</li>
        </ul>''',
        "📋"
    ),
    (
        "VM 생성 — VM 목록 실시간 갱신 버그 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>보안 그룹 선택/해제, 추가 볼륨 변경 시 VM 목록 사이드바와 완료 상태가 즉시 반영되지 않는 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li>보안 그룹 선택/해제 시 상태 즉시 갱신</li>
            <li>추가 볼륨 이름/크기 입력 및 삭제 시 사이드바 즉시 갱신</li>
            <li>보안 그룹 미선택 시 완료 상태로 잘못 표시되던 문제 수정</li>
        </ul>''',
        "🔧"
    ),
    (
        "VM 생성 — 추가 볼륨 미완성 입력 방지",
        "NEW",
        f'''<p {P}>추가 볼륨 행에 이름 또는 크기 중 하나만 입력한 채 VM 생성을 시도하면 생성이 차단됩니다.</p>
        <ul {UL}>
            <li>이름만 입력 시: 크기 입력 필드가 빨간색으로 강조되고 안내 메시지 표시</li>
            <li>크기만 입력 시: 이름 입력 필드가 빨간색으로 강조되고 안내 메시지 표시</li>
            <li>둘 다 비어있는 행은 자동으로 제외 (빈 행 추가 후 사용하지 않은 경우)</li>
        </ul>''',
        "🛡️"
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