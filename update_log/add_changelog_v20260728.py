#!/usr/bin/env python3
"""
Insert today's (2026-07-28) update-log entries.

Run on infra-ansible-01:
    python3 add_changelog_v20260728.py
"""
import sqlite3
from datetime import datetime

DB_PATH    = '/etc/mgt-api/api/infrapilot.db'
VERSION    = 'v2026.07.28'
CREATED_BY = 'system'

H2 = 'style="font-size:15px;font-weight:700;color:var(--text);margin:14px 0 6px"'
P  = 'style="margin:0 0 8px"'
UL = 'style="margin:0 0 8px;padding-left:18px"'

ENTRIES = [
    (
        "업데이트 로그 — 검색 및 유형 필터 추가",
        "NEW",
        f'''<p {P}>업데이트 로그(Release Notes) 좌측 패널에 검색과 유형 필터가 추가되었습니다.</p>
        <ul {UL}>
            <li><b>검색</b> — 제목 및 내용 기반 실시간 검색</li>
            <li><b>유형 필터</b> — 전체 / NEW / IMPROVED / FIXED 버튼으로 필터링</li>
            <li>두 필터를 동시에 적용 가능 (예: FIXED + "볼륨")</li>
            <li>검색 결과 없을 시 안내 메시지 표시</li>
        </ul>''',
        "🔍"
    ),
    (
        "IAM 히스토리 — 검색 및 컬럼 필터 추가",
        "NEW",
        f'''<p {P}>IAM → History 탭에 텍스트 검색과 검색 대상 컬럼 선택 기능이 추가되었습니다.</p>
        <ul {UL}>
            <li><b>검색</b> — 제목, 상세, 사용자 중 원하는 컬럼을 선택하여 검색</li>
            <li><b>컬럼 범위</b> — 전체 컬럼 / 제목 / 상세 / 사용자 중 선택</li>
            <li><b>유형 필터</b> — IAM / VM / Ansible / Puppet / Token 필터와 동시 적용</li>
            <li>필터/검색은 클라이언트 사이드로 동작 — 추가 API 호출 없이 즉시 반영</li>
        </ul>''',
        "🔍"
    ),
    (
        "앱 히스토리 2년 데이터 보존 정책 적용",
        "NEW",
        f'''<p {P}>IAM → History(CloudTrail 감사 로그)에 <b>2년 데이터 보존 정책</b>이 적용되었습니다.</p>
        <p {P}>매일 자정(KST) 730일 이전의 기록이 자동으로 삭제되어 DB 크기를 관리합니다.</p>''',
        "🗄️"
    ),
    (
        "홈 화면 — 최근 방문 카테고리 그룹화",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>최근 방문 페이지가 단순 목록으로 나열되어 맥락 파악이 어려웠음.</p>
        <h2 {H2}>개선 내용</h2>
        <ul {UL}>
            <li>Services 메뉴 카테고리 기준으로 그룹화 — 카테고리 헤더 표시</li>
            <li>같은 카테고리 항목은 <b>2열 그리드</b>로 나란히 표시</li>
            <li>카테고리 알파벳 순 정렬 (Compute → IAM → Management Tools → Network)</li>
            <li>Services 메뉴 클릭 시 카테고리 자동 기록, 기존 데이터는 URL 기반 자동 매핑</li>
        </ul>''',
        "🏠"
    ),
    (
        "홈 화면 — CloudTrail 유형 색상 적용",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>홈 화면의 CloudTrail 패널에서 유형 배지가 회색으로만 표시되어 IAM 히스토리와 시각적으로 달랐음.</p>
        <h2 {H2}>개선 내용</h2>
        <p {P}>IAM 히스토리와 동일한 색상 체계 적용: IAM(파랑) / VM(초록) / Ansible(노랑) / Puppet(보라) / Token(하늘).</p>''',
        "🎨"
    ),
    (
        "홈 화면 — 최근 방문 및 CloudTrail 시간 KST 변환",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>홈 화면의 최근 방문 시간과 CloudTrail 시간이 UTC로 표시되던 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <p {P}>모든 시간 표시를 <b>KST(+9시간)</b>으로 변환하여 표시.</p>''',
        "🕐"
    ),
    (
        "알림 — KC 공지 날짜 중복 표시 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>KC 공지 항목에서 날짜가 상세 내용과 시간 영역에 중복으로 표시되던 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <p {P}>날짜는 우측 시간 영역에만 표시되도록 수정.</p>''',
        "🔔"
    ),
    (
        "알림 — 텍스트 줄바꿈 개선",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>알림 패널에서 "완료" 등 한국어 단어가 "완\n료"처럼 글자 단위로 줄바꿈되는 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li>알림 배지에 <code>white-space: nowrap</code> 적용</li>
            <li>알림 상세 내용에 <code>word-break: keep-all</code> 적용 — 어절 단위 줄바꿈</li>
        </ul>''',
        "📝"
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