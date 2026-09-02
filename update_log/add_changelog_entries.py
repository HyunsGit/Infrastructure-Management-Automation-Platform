#!/usr/bin/env python3
"""
Insert today's update-log entries into InfraPilot's app_changelog table.

Run this directly on infra-ansible-01:
    python3 add_changelog_entries.py

Adjust DB_PATH, VERSION, and CREATED_BY below if needed before running.
"""
import sqlite3
from datetime import datetime

DB_PATH    = '/etc/mgt-api/api/infrapilot.db'
VERSION    = 'v2026.06.24'
CREATED_BY = 'system'

H2 = 'style="font-size:15px;font-weight:700;color:var(--text);margin:14px 0 6px"'
P  = 'style="margin:0 0 8px"'
UL = 'style="margin:0 0 8px;padding-left:18px"'

# (title, change_type, description, icon)
# change_type must be one of: NEW, IMPROVED, FIXED
ENTRIES = [
    (
        "Puppet/Ansible 상태 확인 안정성 개선",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>SSH 연결이 불안정할 때 Ansible/Puppet 상태가 잘못 표시되던 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li>SSH 연결 실패 시 최대 <b>2회 자동 재시도</b></li>
            <li>연결 확인 불가 시 빨간색 "SSH 오류" 대신 회색 "<b>미완료</b>"로 표시</li>
            <li>Bulk Puppet 실행 시 인증서 요청 직후 캐시 오류로 발생하던 "인증서 없음" 오류 해결</li>
        </ul>''',
        "🔧"
    ),
    (
        "VM 프로비저닝 Bulk 작업 버튼 동작 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>여러 VM 선택 시 일부가 실행 대상이 아니거나 이미 완료된 경우에도 Bulk 버튼이 활성화되던 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <ul {UL}>
            <li>선택한 VM 중 <b>실행 불가/완료된 VM이 포함되면 버튼 비활성화</b></li>
            <li>의도하지 않은 일괄 실행 방지</li>
        </ul>''',
        "✅"
    ),
    (
        "PEM 키 자동 인식",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>VM 키 이름을 하드코딩된 매핑으로만 처리하여, 새 키 페어 추가 시마다 코드 수정이 필요했음.</p>
        <h2 {H2}>개선 내용</h2>
        <ul {UL}>
            <li>VM 키 이름을 <b>자동으로 인식</b>하여 올바른 PEM 키 파일을 찾도록 개선</li>
            <li>새 키 페어 추가 시 별도 코드 수정 불필요</li>
        </ul>''',
        "🔑"
    ),
    (
        "볼륨 생성/마운트 작업 오류 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>특정 프로젝트에서 볼륨 생성 및 마운트 작업이 환경 변수 오류로 실패하던 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <p {P}>사용하지 않는 환경 변수 조회 로직을 제거하여 오류를 해결.</p>''',
        "💾"
    ),
    (
        "작업 상태 페이지 — 안내 메시지 표시 위치 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>볼륨 작업 시작 시 표시되는 안내 메시지가 IAM이나 로그인 페이지에 잘못 나타나던 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <p {P}>안내 메시지가 <b>작업 상태 페이지에서 바로 표시</b>되도록 수정.</p>''',
        "📋"
    ),
    (
        "작업 상태 페이지 디자인 통일",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>작업 상태 페이지에 상단 메뉴가 없고, 다른 페이지와 디자인이 통일되어 있지 않았음.</p>
        <h2 {H2}>개선 내용</h2>
        <ul {UL}>
            <li>상단 메뉴 추가</li>
            <li>다른 페이지와 동일한 <b>라이트/다크 모드</b> 디자인 적용</li>
        </ul>''',
        "🎨"
    ),
    (
        "S-Code 트리맵 — 증감 표시 및 상세 보기 개선",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>어제 대비 증감을 단순 합산(+1)으로만 표시하여, 실제 추가/삭제된 VM 개수를 알기 어려웠음. VM ID도 일부만 표시되어 확인이 불편했음.</p>
        <h2 {H2}>개선 내용</h2>
        <ul {UL}>
            <li>실제 추가/삭제된 VM 개수를 <b>+4 / -3</b> 형태로 정확하게 표시</li>
            <li>추가/삭제된 VM을 <b>하나의 목록</b>에서 상태 태그로 구분</li>
            <li>VM ID <b>전체 텍스트</b> 표시</li>
            <li>셀이 매우 작은 S-Code(예: CXAUTO)도 VM 개수 항상 표시</li>
        </ul>''',
        "📊"
    ),
    (
        "S-Code 트리맵 — 애니메이션 및 로딩 개선",
        "IMPROVED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>트리맵이 정적으로 표시되고, 로그인 후 첫 화면에서 여러 번 깜빡이는 현상이 있었음.</p>
        <h2 {H2}>개선 내용</h2>
        <ul {UL}>
            <li>트리맵 진입 시 각 셀이 부드럽게 나타나는 <b>애니메이션</b> 추가</li>
            <li>VM 개수 <b>0부터 카운트업</b> 효과 추가</li>
            <li>첫 화면 진입 시 트리맵이 여러 번 깜빡이던 현상 수정</li>
        </ul>''',
        "✨"
    ),
    (
        "S-Code 트리맵 — CMDB 데이터 자동 갱신 감지",
        "NEW",
        f'''<p {P}>CMDB에서 데이터 갱신 실행 시 S-Code 트리맵이 <b>자동으로 최신 데이터 감지 후 새로고침</b>.</p>
        <p {P}>트리맵 새로고침 버튼을 별도로 누를 필요 없음.</p>''',
        "🔄"
    ),
    (
        "알림 벨 — KC 공지 읽음 처리 오류 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>알림 벨 클릭만으로 KC 공지 탭의 안 읽음 표시가 사라지던 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <p {P}><b>실제로 KC 공지 탭을 열어야만</b> 안 읽음 표시가 사라지도록 수정.</p>''',
        "🔔"
    ),
    (
        "VPC 상태 페이지 라이트 모드 색상 수정",
        "FIXED",
        f'''<h2 {H2}>문제</h2>
        <p {P}>라이트 모드에서 테이블 헤더가 배경색과 구분되지 않던 문제.</p>
        <h2 {H2}>수정 내용</h2>
        <p {P}>헤더 배경/텍스트 색상의 CSS 우선순위를 조정하여 라이트 모드에서도 명확하게 구분되도록 수정.</p>''',
        "🎨"
    ),
    (
        "업데이트 로그 — 항목 수정 기능 추가",
        "NEW",
        f'''<p {P}>기존 삭제 기능만 있던 업데이트 로그에 <b>수정 기능</b> 추가.</p>
        <p {P}>"수정" 버튼으로 버전, 제목, 유형, 아이콘, 이미지, 설명 모두 변경 가능. 관리자 전용.</p>''',
        "📝"
    ),
]


def main():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    # Safe to re-run: clear out any existing entries for this version first,
    # so re-running this script never creates duplicates.
    cur.execute("DELETE FROM app_changelog WHERE version = ?", (VERSION,))
    deleted = cur.rowcount
    if deleted:
        print(f"Removed {deleted} existing entries for version {VERSION} before re-inserting.")

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
    print(f"Inserted {cur.rowcount if cur.rowcount != -1 else len(rows)} changelog entries "
          f"for version {VERSION}.")
    conn.close()


if __name__ == '__main__':
    main()