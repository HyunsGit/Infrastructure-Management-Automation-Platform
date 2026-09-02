#!/usr/bin/env python3
import sqlite3
from datetime import datetime

DB_PATH    = '/etc/mgt-api/api/infrapilot.db'
VERSION    = 'v2026.07.22'
CREATED_BY = 'system'

H2 = 'style="font-size:15px;font-weight:700;color:var(--text);margin:14px 0 6px"'
P  = 'style="margin:0 0 8px"'
UL = 'style="margin:0 0 8px;padding-left:18px"'

ENTRIES = [
    (
        "VM 생성 — UI 개편 (2단 레이아웃 + 사이드바)",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>VM 구성 항목이 단순 그리드로 나열되어 가독성이 낮고, 여러 VM 구성 현황 파악이 어려웠음.</p>
        <h2 {H2}>개선 내용</h2>
        <ul {UL}>
            <li><b>2단 레이아웃</b> — 인스턴스(프로젝트/이미지/플레이버/키페어) / 네트워크 & 스토리지(서브넷/보안그룹/루트볼륨/추가볼륨) 분리</li>
            <li><b>상태 보더</b> — 미완료(회색) / 완료(파랑) / 생성 완료(초록) 색상 구분</li>
            <li><b>헤더 요약</b> — 현재 선택된 플레이버/이미지/서브넷 한 줄 요약 표시</li>
            <li><b>VM 목록 사이드바</b> — 우측 고정 패널, 상태 점과 함께 전체 VM 현황 표시, 클릭 시 해당 블록으로 이동</li>
            <li><b>복제 버튼</b> — 동일 구성 VM 블록 즉시 추가</li>
        </ul>''',
        "🖥️"
    ),
    (
        "VM 생성 — 드롭다운 정렬 개선",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>서브넷/이미지/플레이버 등이 임의 순서로 표시되어 원하는 항목을 찾기 불편했음.</p>
        <h2 {H2}>개선 내용</h2>
        <ul {UL}>
            <li><b>서브넷</b> — CIDR IP 숫자 순 정렬</li>
            <li><b>이미지</b> — 자연어 정렬 (Ubuntu 22.04 → 24.04)</li>
            <li><b>플레이버</b> — 패밀리 알파벳순 + 사이즈 순 (large → xlarge → 2xlarge)</li>
            <li><b>키페어 / 보안 그룹</b> — 알파벳 순</li>
        </ul>''',
        "🔤"
    ),
    (
        "VM 생성 — 추가 볼륨 이름 자동 제안",
        "NEW",
        f'''<p {P}>추가 볼륨 추가 시 VM 이름 기반 이름 자동 제안:</p>
        <ul {UL}>
            <li>1번째: <b>{{{{hostname}}}}-data</b></li>
            <li>2번째: <b>{{{{hostname}}}}-logs</b></li>
            <li>3번째: <b>{{{{hostname}}}}-backup</b></li>
        </ul>
        <p {P}>VM 이름 변경 시 수동 수정하지 않은 볼륨 이름도 자동 업데이트.</p>''',
        "💡"
    ),
    (
        "VM 생성 — 드롭다운 병렬 로딩",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>프로젝트 선택 후 드롭다운 로딩이 순차 처리로 느렸음.</p>
        <h2 {H2}>개선 내용</h2>
        <p {P}>서브넷, 보안 그룹, 키페어, 플레이버, 이미지를 <b>동시 병렬 조회</b>하여 로딩 시간 대폭 단축.</p>''',
        "⚡"
    ),
    (
        "VM 생성 — 진행 로그 플로팅 팝업",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>VM 생성 진행 로그가 페이지 하단에 위치해 스크롤하지 않으면 보이지 않았음.</p>
        <h2 {H2}>개선 내용</h2>
        <p {P}>화면 우측 하단 <b>고정 플로팅 팝업</b>으로 전환, 스크롤 위치와 무관하게 항상 확인 가능.</p>''',
        "📋"
    ),
    (
        "VM 생성 — 이미 생성된 VM 재생성 방지",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>추가 VM 생성 시 이미 생성 완료된 VM까지 다시 생성하는 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <p {P}>"생성 완료" 상태인 VM 블록은 생성 시작 시 자동 제외.</p>''',
        "✅"
    ),
    (
        "VM 생성 — 볼륨 중복 오류 메시지 개선",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>동일 볼륨 이름 존재 시 409 HTTP 오류 코드가 그대로 표시되어 원인 파악이 어려웠음.</p>
        <h2 {H2}>개선 내용</h2>
        <p {P}>명확한 한국어 안내 메시지로 대체:<br>
        <i>"볼륨 이름 'test-data'이 이미 존재합니다. 다른 이름을 사용하거나 기존 볼륨을 먼저 삭제해주세요."</i></p>''',
        "⚠️"
    ),
    (
        "VM 생성 — 복제 버튼 프로젝트 오류 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>복제 버튼 클릭 시 프로젝트가 문자열이 아닌 객체로 전달되어 리소스 조회 실패.</p>
        <h2 {H2}>수정 내용</h2>
        <p {P}>올바른 프로젝트 키 문자열을 전달하도록 수정.</p>''',
        "🔧"
    ),
    (
        "VM 생성 — 상태 표시 버그 수정 3건",
        "FIXED",
        f'''<h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li><b>파란 보더 미전환</b> — 드롭다운 콜백 내 잘못된 변수명(<code>id</code> → <code>blockId</code>) 수정</li>
            <li><b>블록 번호 오류</b> — 삭제/복제 후 번호가 연속되지 않던 문제, 항상 현재 순서 기준으로 재번호 처리</li>
            <li><b>사이드바 점 미갱신</b> — 생성 완료 후 사이드바 상태 점이 초록으로 업데이트되지 않던 문제 수정</li>
        </ul>''',
        "🔧"
    ),
    (
        "VM 생성 — 드롭다운 키보드 탐색 지원",
        "NEW",
        f'''<p {P}>드롭다운 검색 입력창에서 키보드로 항목을 탐색하고 선택할 수 있습니다.</p>
        <ul {UL}>
            <li><b>↓ / ↑</b> — 항목 위아래 이동</li>
            <li><b>Enter</b> — 현재 선택된 항목 확정 (보안 그룹 다중 선택 시 체크/해제)</li>
            <li><b>Escape</b> — 드롭다운 닫기</li>
        </ul>
        <p {P}>단일 선택(서브넷/이미지/플레이버/키페어)과 다중 선택(보안 그룹) 모두 지원.''',
        "⌨️"
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