#!/usr/bin/env python3
"""
Insert today's (2026-08-27) update-log entries.

Run on infra-ansible-01:
    python3 add_changelog_v20260827.py
"""
import sqlite3
from datetime import datetime

DB_PATH    = '/etc/mgt-api/api/infrapilot.db'
VERSION    = 'v2026.08.27'
CREATED_BY = 'system'

H2 = 'style="font-size:15px;font-weight:700;color:var(--text);margin:14px 0 6px"'
P  = 'style="margin:0 0 8px"'
UL = 'style="margin:0 0 8px;padding-left:18px"'

ENTRIES = [
    (
        "CloudTrail — 로그인/로그아웃 이벤트 기록",
        "NEW",
        f'''<p {P}>사용자 로그인 및 로그아웃 시 CloudTrail에 자동 기록.</p>
        <ul {UL}>
            <li>이벤트 유형: IAM</li>
            <li>로그인 시 클라이언트 IP 기록 (X-Forwarded-For 기준)</li>
            <li>2FA 인증 완료 시점에 기록</li>
        </ul>''',
        "📜"
    ),
    (
        "CloudTrail — 페이지네이션 추가 (100건/페이지)",
        "NEW",
        f'''<h2 {H2}>변경 전</h2>
        <p {P}>최신 200건만 조회 가능.</p>
        <h2 {H2}>변경 후</h2>
        <ul {UL}>
            <li>페이지당 100건, 전체 기록 탐색 가능</li>
            <li>◀ ▶ 버튼으로 페이지 이동</li>
            <li>현재 페이지 / 전체 페이지 및 총 건수 표시</li>
            <li>유형 필터 변경 시 자동으로 첫 페이지로 이동</li>
        </ul>''',
        "📜"
    ),
    (
        "Services 메뉴 — 메뉴 클릭 시 패널 미닫힘 버그 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>Management Tools — Overview 하위 메뉴(인프라 코드 트리맵, 인프라 현황, 업데이트 로그) 클릭 시 Services 패널이 닫히지 않는 문제.</p>
        <h2 {H2}>원인</h2>
        <p {P}><code>/summary#dashboard</code>, <code>/summary#changelog</code> 등 해시 앵커 링크는 페이지 리로드 없이 동일 페이지 내에서 이동하므로 패널이 자동으로 닫히지 않았음.</p>
        <h2 {H2}>수정</h2>
        <p {P}>모든 서비스 링크 클릭 시 <code>closeServicesPanel()</code> 명시적 호출.</p>''',
        "🔧"
    ),
    (
        "홈 화면 — 스크롤바 디자인 개선",
        "IMPROVED",
        f'''<p {P}>카드 영역 스크롤바 시각 디자인 개선.</p>
        <ul {UL}>
            <li>너비 4px 슬림 스크롤바</li>
            <li>기본 상태: 12% 불투명도로 항상 표시 (위치 인지 가능)</li>
            <li>호버 시: 30% 불투명도로 강조</li>
            <li>라이트 모드 / 다크 모드 모두 적용</li>
            <li>최근 방문, CloudTrail, OS PW 만료 패널 모두 동일하게 적용</li>
        </ul>''',
        "🎨"
    ),
    (
        "알림 — 최근 탭 로드 실패 버그 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>알림 벨의 최근 탭에서 "로드 실패" 오류 표시.</p>
        <h2 {H2}>원인</h2>
        <p {P}>CloudTrail API 응답 형식이 배열에서 페이지네이션 객체(<code>{{total, events, ...}}</code>)로 변경되었으나 최근 탭에서 배열로 처리하여 오류 발생.</p>
        <h2 {H2}>수정</h2>
        <p {P}><code>response.events</code>를 우선 읽도록 수정.</p>''',
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