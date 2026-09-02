#!/usr/bin/env python3
"""
Insert today's (2026-08-21) update-log entries.

Run on infra-ansible-01:
    python3 add_changelog_v20260821.py
"""
import sqlite3
from datetime import datetime

DB_PATH    = '/etc/mgt-api/api/infrapilot.db'
VERSION    = 'v2026.08.21'
CREATED_BY = 'system'

H2 = 'style="font-size:15px;font-weight:700;color:var(--text);margin:14px 0 6px"'
P  = 'style="margin:0 0 8px"'
UL = 'style="margin:0 0 8px;padding-left:18px"'

ENTRIES = [
    (
        "VM 프로비저닝 — Route53 DNS 체크 제거",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>Ansible 플레이북 완료 후 Route53 DNS 체크가 자주 실패하여 Puppet 실행이 불가능한 문제. 앱 서버에서 8.8.8.8(Google DNS)을 사용해 Private Hosted Zone을 조회할 수 없었음.</p>
        <h2 {H2}>개선 내용</h2>
        <p {P}>Route53 DNS 체크를 Ansible 플레이 목록에서 완전히 제거. 플레이북 체크 항목:</p>
        <ul {UL}>
            <li>install kc_monitor_agent</li>
            <li>Update Hostname</li>
            <li>Install Puppet agent</li>
            <li>Deploy Facter NTP facts</li>
            <li>Set Time Zone</li>
        </ul>''',
        "⚙️"
    ),
    (
        "IAM — 비밀번호 초기화 기능 추가",
        "NEW",
        f'''<p {P}>관리자가 IAM 사용자 목록에서 직접 비밀번호를 초기화할 수 있습니다.</p>
        <ul {UL}>
            <li>사용자 목록의 <b>PW 초기화</b> 버튼 클릭</li>
            <li>임시 비밀번호(6자 이상) 입력 후 초기화</li>
            <li>초기화된 사용자는 다음 로그인 시 비밀번호 변경 요청</li>
            <li>IAM 히스토리에 초기화 기록 저장</li>
        </ul>''',
        "🔑"
    ),
    (
        "S-Code → 인프라 코드 명칭 변경",
        "IMPROVED",
        f'''<h2 {H2}>변경 내용</h2>
        <p {P}>앱 전체에서 사용자에게 표시되는 <b>S-Code</b> 명칭이 <b>인프라 코드</b>로 변경되었습니다.</p>
        <ul {UL}>
            <li>Services 메뉴: S-Code 트리맵 → 인프라 코드 트리맵</li>
            <li>IAM: S-Code 관리 → 인프라 코드 관리</li>
            <li>VM 프로비저닝: S-Code 컬럼 → 인프라 코드</li>
            <li>트리맵 페이지 설명 텍스트 전체 업데이트</li>
        </ul>
        <p {P}>내부 코드(변수명, API 경로, DB 컬럼)는 변경 없음.</p>''',
        "🏷️"
    ),
    (
        "앱 URL 변경 — tools → dipo",
        "IMPROVED",
        f'''<p {P}>InfraPilot 접속 URL이 변경되었습니다.</p>
        <ul {UL}>
            <li>변경 전: <code>tools.example.in</code></li>
            <li>변경 후: <code>dipo.example.in</code></li>
        </ul>''',
        "🌐"
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