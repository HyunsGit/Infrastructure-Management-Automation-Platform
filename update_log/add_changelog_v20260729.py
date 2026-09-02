#!/usr/bin/env python3
"""
Insert today's (2026-07-29) update-log entries.

Run on infra-ansible-01:
    python3 add_changelog_v20260729.py
"""
import sqlite3
from datetime import datetime

DB_PATH    = '/etc/mgt-api/api/infrapilot.db'
VERSION    = 'v2026.07.29'
CREATED_BY = 'system'

H2 = 'style="font-size:15px;font-weight:700;color:var(--text);margin:14px 0 6px"'
P  = 'style="margin:0 0 8px"'
UL = 'style="margin:0 0 8px;padding-left:18px"'

ENTRIES = [
    (
        "홈 화면 — 6개 카드 대시보드 개편",
        "IMPROVED",
        f'''<h2 {H2}>변경 내용</h2>
        <p {P}>홈 화면이 3×2 그리드 대시보드로 개편되었습니다. 모든 카드가 동시에 표시됩니다.</p>
        <ul {UL}>
            <li><b>1행 (사용자)</b> — 최근 방문 · 알림 · 즐겨찾기</li>
            <li><b>2행 (앱)</b> — CloudTrail · 업데이트 로그 · 시스템 상태</li>
            <li>알림 카드: 사용자 알림 / KC 공지 내부 탭 토글</li>
            <li>업데이트 로그: 최신 5건 + 전체 보기 링크</li>
            <li>시스템 상태: KakaoCloud 5개 API 엔드포인트 실시간 응답 확인</li>
        </ul>''',
        "🏠"
    ),
    (
        "홈 화면 — 즐겨찾기 기능 추가",
        "NEW",
        f'''<p {P}>Services 메뉴에서 자주 사용하는 항목을 즐겨찾기에 등록할 수 있습니다.</p>
        <ul {UL}>
            <li>Services 패널 항목 옆 <b>⭐ 버튼</b>으로 추가/제거</li>
            <li>내부 링크 및 외부 링크(Grafana, Kibana 등) 모두 지원</li>
            <li>즐겨찾기 추가 즉시 홈 화면 카드에 실시간 반영</li>
            <li>카테고리별 그룹화, 카테고리·항목명 알파벳 순 정렬</li>
            <li>DB 저장 — 브라우저 초기화와 무관하게 유지</li>
        </ul>''',
        "⭐"
    ),
    (
        "홈 화면 — 시스템 상태 카드 추가",
        "NEW",
        f'''<p {P}>KakaoCloud API 5개 엔드포인트의 실시간 응답 상태를 홈 화면에서 확인할 수 있습니다.</p>
        <ul {UL}>
            <li>BCS (Compute), VPC (Network), Network, Volume, Image</li>
            <li>응답 속도(ms) 및 정상/지연/오류 상태 표시</li>
            <li>새로고침 버튼으로 즉시 재확인 가능</li>
        </ul>''',
        "🟢"
    ),
    (
        "홈 화면 — 최근 방문 카테고리 알파벳 순 정렬",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>최근 방문 카드의 카테고리 및 항목 정렬이 방문 시간 기준으로만 정렬되어 일관성이 없었음.</p>
        <h2 {H2}>개선 내용</h2>
        <p {P}>카테고리와 항목 모두 알파벳(가나다) 순 정렬로 변경되어 일관된 위치에 표시됩니다.</p>''',
        "🔤"
    ),
    (
        "Services 메뉴 — 즐겨찾기 버튼 상시 표시",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>Services 메뉴의 ⭐ 버튼이 마우스 오버 시에만 표시되어 기능을 인지하기 어려웠음.</p>
        <h2 {H2}>개선 내용</h2>
        <ul {UL}>
            <li>⭐/☆ 버튼이 항상 표시됨 (노란색, 미등록 시 40% 투명도)</li>
            <li>즐겨찾기 등록 항목은 항상 ⭐ 완전 불투명으로 표시</li>
            <li>외부 링크(Grafana, Prometheus, Kibana 등)도 즐겨찾기 지원</li>
        </ul>''',
        "⭐"
    ),
    (
        "Services 메뉴 — 라이트 모드 배경 표시 버그 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>라이트 모드에서 Services 메뉴를 열면 배경 페이지가 완전히 가려지는 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <p {P}><code>light-mode.css</code>의 <code>[class*="panel"]</code> 와일드카드 셀렉터가 <code>.services-panel</code>에 단색 배경을 적용하던 문제를 수정. <code>:not(.services-panel)</code>으로 제외 처리.</p>''',
        "🔧"
    ),
    (
        "알림 및 설정 패널 — Services 메뉴 열린 상태에서 가림 현상 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>Services 메뉴가 열린 상태에서 알림(🔔) 또는 설정(⚙️) 패널을 클릭하면 어두운 오버레이 뒤로 숨는 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <p {P}>알림 패널과 설정 패널의 <code>z-index</code>를 Services 백드롭(1900)보다 높은 2050으로 상향 조정.</p>''',
        "🔧"
    ),
    (
        "VM 생성 — 플레이버 메모리 필드 단위 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>플레이버 드롭다운에서 메모리 값이 표시되지 않던 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <p {P}><code>memory_mb</code> 필드가 KB 단위가 아닌 MB 단위임을 확인, <code>/1024</code>로 GB 변환 적용.</p>''',
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