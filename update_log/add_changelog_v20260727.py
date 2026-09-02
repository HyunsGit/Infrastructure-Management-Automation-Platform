#!/usr/bin/env python3
"""
Insert today's (2026-07-27) update-log entries.

Run on infra-ansible-01:
    python3 add_changelog_v20260727.py
"""
import sqlite3
from datetime import datetime

DB_PATH    = '/etc/mgt-api/api/infrapilot.db'
VERSION    = 'v2026.07.27'
CREATED_BY = 'system'

H2 = 'style="font-size:15px;font-weight:700;color:var(--text);margin:14px 0 6px"'
P  = 'style="margin:0 0 8px"'
UL = 'style="margin:0 0 8px;padding-left:18px"'

ENTRIES = [
    (
        "VM 프로비저닝 — VM 목록 캐시 도입 (빠른 로딩)",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>프로비저닝 페이지에서 프로젝트를 변경할 때마다 KakaoCloud API를 직접 호출하여 로딩이 느렸음.</p>
        <h2 {H2}>개선 내용</h2>
        <ul {UL}>
            <li>백그라운드 스레드가 <b>55초마다 모든 프로젝트의 VM 목록을 미리 Redis에 캐시</b></li>
            <li>프로젝트 전환 시 캐시에서 즉시 응답 (5ms 이하)</li>
            <li>캐시 만료(60초) 시 자동으로 API에서 재조회</li>
            <li><b>새로고침 버튼</b> 클릭 시 캐시를 무시하고 최신 데이터를 강제 조회</li>
            <li>VM 생성 완료 즉시 해당 프로젝트 캐시 자동 삭제 — 새 VM이 바로 목록에 반영</li>
        </ul>''',
        "⚡"
    ),
    (
        "서비스 메뉴 검색 — 검색 결과 미표시 버그 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>서비스 검색창에 입력해도 검색 결과가 표시되지 않는 문제. 서비스 패널 재구성 시 검색 결과 영역(<code>#cat-search</code>)이 누락되었음.</p>
        <h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li>검색 결과 섹션 복원</li>
            <li>검색 매칭 방식을 단어 시작 일치에서 <b>부분 문자열 일치</b>로 개선 — 메뉴 이름, 설명, 키워드 모두 검색 가능</li>
        </ul>''',
        "🔍"
    ),
    (
        "서비스 메뉴 검색 — 카테고리 헤더 표시",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>검색 결과에 메뉴 항목만 나열되어 어떤 카테고리에 속하는지 알 수 없었음.</p>
        <h2 {H2}>개선 내용</h2>
        <p {P}>검색 결과에 <b>카테고리 헤더</b>(예: Provisioning, Instances)를 함께 표시하여 맥락 파악이 용이해짐.</p>''',
        "🗂️"
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