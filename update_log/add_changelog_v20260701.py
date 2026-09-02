#!/usr/bin/env python3
"""
Insert yesterday's (2026-06-25) update-log entries into InfraPilot's app_changelog table.

Run on infra-ansible-01:
    python3 add_changelog_v20260625.py
"""
import sqlite3
from datetime import datetime

DB_PATH    = '/etc/mgt-api/api/infrapilot.db'
VERSION    = 'v2026.07.01'
CREATED_BY = 'system'

H2 = 'style="font-size:15px;font-weight:700;color:var(--text);margin:14px 0 6px"'
P  = 'style="margin:0 0 8px"'
UL = 'style="margin:0 0 8px;padding-left:18px"'

ENTRIES = [
    (
        "VM 생성 — 신규 페이지 추가",
        "NEW",
        f'''<p {P}>KakaoCloud VM을 UI에서 직접 생성할 수 있는 신규 페이지를 추가했습니다.</p>
        <ul {UL}>
            <li>프로젝트 선택 시 서브넷 / 이미지 / 플레이버 / 키페어 / 보안 그룹을 <b>라이브 API</b>로 자동 조회</li>
            <li>보안 그룹은 <b>검색형 다중 선택</b> 드롭다운 지원</li>
            <li>추가 볼륨 행을 자유롭게 추가/삭제 가능, 삭제 방지 옵션 자동 적용</li>
            <li>여러 VM을 동시에 구성 후 <b>한 번에 생성</b> 가능 (최대 3개 병렬 처리)</li>
            <li>생성 진행 상황을 <b>실시간 스트리밍</b>으로 확인 가능</li>
        </ul>''',
        "🖥️"
    ),
    (
        "VM 생성 — 스프레드시트 붙여넣기 지원",
        "NEW",
        f'''<p {P}>구글 스프레드시트에서 VM 요청 데이터를 복사해 붙여넣으면 VM 구성 카드가 자동으로 생성됩니다.</p>
        <ul {UL}>
            <li>헤더 이름 기반으로 컬럼 자동 인식 — 컬럼 순서에 무관</li>
            <li><b>리소스 타입 = VM</b> 인 행만 자동 필터링</li>
            <li>OS + Version 조합으로 이미지 자동 매칭, Spec → 플레이버 자동 매칭</li>
            <li>DISK(추가) → <code>{{hostname}}-data</code>, DISK(추가2) → <code>{{hostname}}-logs</code>, DISK(추가3) → <code>{{hostname}}-backup</code> 자동 명명</li>
        </ul>''',
        "📋"
    ),
    (
        "S-Code 트리맵 — 삭제/추가 오탐 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>존재하는 VM이 삭제된 것으로 잘못 표시되거나, 증감 수치가 실제와 다르게 나타나는 오탐 발생.</p>
        <h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li>어제 스냅샷이 없을 때 가장 <b>최근 스냅샷</b>을 자동으로 기준으로 사용</li>
            <li>CSV의 vm_id 앞뒤 공백으로 인한 <b>잘못된 diff 계산</b> 수정</li>
            <li>스냅샷 저장 시에도 vm_id / vm_name / servicecode 공백 제거 적용</li>
        </ul>''',
        "📊"
    ),
    (
        "S-Code 트리맵 — 소형 셀 S-Code 표시 개선",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>셀이 작은 S-Code(예: CXAUTO)에서 S-Code 이름은 보이지 않고 숫자만 표시되는 문제.</p>
        <h2 {H2}>개선 내용</h2>
        <ul {UL}>
            <li>셀 너비가 충분할 때만 S-Code 이름 표시, 좁은 셀에서는 숫자만 표시</li>
            <li>툴팁에서 항상 전체 S-Code 이름과 VM 수 확인 가능</li>
        </ul>''',
        "✨"
    ),
    (
        "S-Code 트리맵 — 툴팁 위치 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>툴팁이 마우스 위치와 무관하게 무작위 위치에 표시되던 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li>툴팁이 <b>마우스 커서 근처</b>에 정확히 표시되도록 수정</li>
            <li>커서를 따라 부드럽게 이동하며, 화면 경계에서 자동으로 위치 조정</li>
        </ul>''',
        "🎯"
    ),
    (
        "백그라운드 스레드 안정성 개선",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>토큰 자동 갱신 스레드가 예외 발생 시 조용히 종료되어 이후 모든 갱신이 중단되는 문제. 또한 서버 재시작 후 이전 프로세스의 락 파일이 남아 백그라운드 스레드가 시작되지 않는 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li>토큰 갱신 루프에 <b>예외 처리</b> 추가 — 오류 발생 시 다음 주기(30분 후)에 재시도</li>
            <li>PID 파일 기반 검증으로 <b>죽은 프로세스의 락 파일</b>을 자동 해제</li>
        </ul>''',
        "🔧"
    ),
    (
        "업데이트 로그 색상 — 라이트 모드 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>업데이트 로그 좌측/우측 패널의 텍스트 색상이 라이트 모드에서 배경과 구분되지 않던 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li>하드코딩된 색상을 <b>CSS 변수</b>로 대체하여 라이트/다크 모드 모두 정상 표시</li>
        </ul>''',
        "🎨"
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