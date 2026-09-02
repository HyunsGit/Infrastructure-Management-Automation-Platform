#!/usr/bin/env python3
"""
Insert today's (2026-07-08) update-log entries.

Run on infra-ansible-01:
    python3 add_changelog_v20260708.py
"""
import sqlite3
from datetime import datetime

DB_PATH    = '/etc/mgt-api/api/infrapilot.db'
VERSION    = 'v2026.07.08'
CREATED_BY = 'system'

H2 = 'style="font-size:15px;font-weight:700;color:var(--text);margin:14px 0 6px"'
P  = 'style="margin:0 0 8px"'
UL = 'style="margin:0 0 8px;padding-left:18px"'

ENTRIES = [
    (
        "새로운 홈 화면 추가",
        "NEW",
        f'''<p {P}>로그인 후 처음 보이는 화면이 홈 화면으로 변경되었습니다.</p>
        <ul {UL}>
            <li><b>최근 방문</b> — 최근 방문한 페이지 최대 6개 표시 (DB 기반, 브라우저 초기화와 무관)</li>
            <li><b>알림</b> — 사용자 알림(PW 만료)과 KC 공지를 탭으로 구분하여 표시, KC 공지 탭 클릭 시 읽음 처리</li>
            <li><b>CloudTrail</b> — 최근 감사 로그 10건 즉시 확인 가능</li>
            <li>InfraPilot 로고 클릭 시 홈 화면으로 이동</li>
        </ul>''',
        "🏠"
    ),
    (
        "Services 메뉴 계층 구조 개편",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>Services 패널의 메뉴 항목이 체계적인 분류 없이 나열되어 있어 원하는 메뉴를 찾기 불편했음.</p>
        <h2 {H2}>개선 내용</h2>
        <p {P}>좌측 카테고리가 아래 계층 구조로 재편성되었습니다:</p>
        <ul {UL}>
            <li><b>Compute</b> — Instances (Create VM, Browse VM) + Volumes (List/Create/Mount)</li>
            <li><b>Network</b> — VPC (VPC 상태)</li>
            <li><b>Management Tools</b> — Overview (S-Code 트리맵, 인프라 현황, 업데이트 로그) + Provisioning + Metrics + Analytics</li>
            <li><b>IAM</b> — User Managed (Users, S-Code) + Cloud Managed (Projects) + CloudTrail (Logs)</li>
        </ul>''',
        "🗂️"
    ),
    (
        "VM 생성 — 프로젝트 드롭다운 별칭 표시",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>VM 생성 페이지의 프로젝트 드롭다운에 프로젝트 키 이름만 표시되어 어떤 프로젝트인지 구분하기 어려웠음.</p>
        <h2 {H2}>개선 내용</h2>
        <p {P}>IAM 프로젝트 관리에서 설정한 별칭이 있을 경우 <b>프로젝트키 (별칭)</b> 형태로 함께 표시됩니다.</p>''',
        "🏷️"
    ),
    (
        "VM 생성 — API 요청 빈도 초과(429) 오류 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>VM 생성 시 볼륨 생성 및 인스턴스 생성 API에서 429 Too Many Requests 오류 발생.</p>
        <h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li>볼륨 생성, 볼륨 상태 조회, 인스턴스 생성, 이미지 목록 조회 모든 API에 <b>자동 재시도</b> 로직 추가</li>
            <li>429 응답 시 지수 백오프 (1초 → 2초 → 4초 → 8초) 후 최대 4회 재시도</li>
        </ul>''',
        "🔧"
    ),
    (
        "S-Code 트리맵 — 오탐 삭제/추가 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>실제로 존재하는 VM이 삭제된 것으로 잘못 표시되거나, 증감 수치가 실제와 다르게 나타나는 오탐 발생.</p>
        <h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li>어제 스냅샷이 없을 때 <b>가장 최근 스냅샷</b>을 자동 기준으로 사용</li>
            <li>CSV의 vm_id 앞뒤 공백으로 인한 잘못된 diff 계산 수정</li>
        </ul>''',
        "📊"
    ),
    (
        "S-Code 트리맵 — 툴팁 위치 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>툴팁이 마우스 위치와 무관한 임의 위치에 표시되던 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li>툴팁이 <b>마우스 커서 근처</b>에 정확히 표시되도록 수정</li>
            <li>커서 이동 시 툴팁이 따라다니며, 화면 경계에서 자동으로 위치 조정</li>
        </ul>''',
        "🎯"
    ),
    (
        "S-Code 트리맵 — 소형 셀 표시 개선",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>VM이 적어 셀이 작은 S-Code(예: CXAUTO)에서 이름은 보이지 않고 숫자만 표시되는 문제.</p>
        <h2 {H2}>개선 내용</h2>
        <ul {UL}>
            <li>셀 너비가 충분할 때만 S-Code 이름 표시 — 너무 좁은 셀에서는 숫자만 표시해 가독성 향상</li>
            <li>마우스 오버 시 툴팁으로 전체 S-Code 이름과 VM 수 항상 확인 가능</li>
        </ul>''',
        "✨"
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