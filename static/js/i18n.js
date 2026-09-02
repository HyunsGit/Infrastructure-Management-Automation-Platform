/* InfraPilot i18n — safe to load multiple times */
if (!window.I18N) {

window.I18N = {
    'nav.services':       { ko: 'Services',            en: 'Services' },
    'nav.console':        { ko: 'InfraPilot Console',  en: 'InfraPilot Console' },
    'nav.search':         { ko: '서비스 검색',          en: 'Search services' },
    'bell.title':         { ko: 'Notifications',       en: 'Notifications' },
    'bell.mark_all':      { ko: '모두 읽음',            en: 'Mark all read' },
    'bell.tab.recent':    { ko: '최근',                 en: 'Recent' },
    'bell.tab.user':      { ko: '사용자 알림',           en: 'User Alerts' },
    'bell.tab.kc':        { ko: 'KC 공지',              en: 'KC Notices' },
    'bell.view_all':      { ko: '전체 기록 보기 →',      en: 'View all →' },
    'bell.empty':         { ko: '알림이 없습니다 ✓',     en: 'No notifications ✓' },
    'bell.pw_empty':      { ko: '만료 임박 알림 없음',   en: 'No expiry alerts' },
    'bell.load_fail':     { ko: '로드 실패',             en: 'Failed to load' },
    'settings.title':     { ko: 'Settings',             en: 'Settings' },
    'settings.visual_mode': { ko: 'Visual mode',        en: 'Visual mode' },
    'settings.visual_desc': { ko: '원하는 인터페이스 테마를 선택하세요.', en: 'Choose your preferred interface theme.' },
    'settings.browser':   { ko: 'Browser default',      en: 'Browser default' },
    'settings.light':     { ko: 'Light',                en: 'Light' },
    'settings.dark':      { ko: 'Dark',                 en: 'Dark' },
    'settings.language':  { ko: 'Language',             en: 'Language' },
    'settings.lang_desc': { ko: '원하는 표시 언어를 선택하세요.', en: 'Choose your preferred display language.' },
    'svc.all':            { ko: 'All',                  en: 'All' },
    'svc.compute':        { ko: 'Compute',              en: 'Compute' },
    'svc.network':        { ko: 'Network',              en: 'Network' },
    'svc.mgmt':           { ko: 'Management Tools',     en: 'Management Tools' },
    'svc.iam':            { ko: 'IAM',                  en: 'IAM' },
    'svc.sec.instances':  { ko: 'Instances',            en: 'Instances' },
    'svc.sec.volumes':    { ko: 'Volumes',              en: 'Volumes' },
    'svc.sec.vpc':        { ko: 'VPC',                  en: 'VPC' },
    'svc.sec.overview':   { ko: 'Overview',             en: 'Overview' },
    'svc.sec.prov':       { ko: 'Provisioning',         en: 'Provisioning' },
    'svc.sec.metrics':    { ko: 'Metrics',              en: 'Metrics' },
    'svc.sec.analytics':  { ko: 'Analytics',            en: 'Analytics' },
    'svc.sec.user_mgd':   { ko: 'User Managed',         en: 'User Managed' },
    'svc.sec.cloud_mgd':  { ko: 'Cloud Managed',        en: 'Cloud Managed' },
    'svc.sec.cloudtrail': { ko: 'CloudTrail',           en: 'CloudTrail' },
    'svc.create_vm':      { ko: 'Create VM',            en: 'Create a VM' },
    'svc.create_vm.desc': { ko: 'VM 인스턴스 생성',      en: 'Spin up a new instance' },
    'svc.browse_vm':      { ko: 'Browse VM',            en: 'Browse VMs' },
    'svc.browse_vm.desc': { ko: 'VM 목록 조회',          en: 'View your VM inventory' },
    'svc.list_vol':       { ko: 'List Volume',          en: 'List Volumes' },
    'svc.list_vol.desc':  { ko: '볼륨 목록 조회',        en: 'View your volumes' },
    'svc.create_vol':     { ko: 'Create Volume',        en: 'Create a Volume' },
    'svc.create_vol.desc':{ ko: '볼륨 생성',             en: 'Add a new volume' },
    'svc.mount_vol':      { ko: 'Mount Volume',         en: 'Mount a Volume' },
    'svc.mount_vol.desc': { ko: '볼륨 마운트',           en: 'Attach a volume to a VM' },
    'svc.vpc':            { ko: 'VPC 상태',              en: 'VPC Status' },
    'svc.vpc.desc':       { ko: '네트워크 연결 확인',     en: 'Check network connectivity' },
    'svc.treemap':        { ko: 'S-Code 트리맵',         en: 'S-Code Treemap' },
    'svc.treemap.desc':   { ko: 'VM S-Code 현황',        en: 'VM S-Code overview' },
    'svc.infra':          { ko: '인프라 현황',            en: 'Infrastructure Status' },
    'svc.infra.desc':     { ko: '인프라 상태 대시보드',   en: 'Infrastructure dashboard' },
    'svc.changelog':      { ko: '업데이트 로그',          en: 'Update Log' },
    'svc.changelog.desc': { ko: '릴리스 노트',            en: 'Release notes' },
    'svc.prov':           { ko: 'VM Provisioning',      en: 'VM Provisioning' },
    'svc.prov.desc':      { ko: 'Ansible / Puppet',     en: 'Ansible / Puppet' },
    'svc.users':          { ko: 'Users',                en: 'Users' },
    'svc.users.desc':     { ko: '사용자 관리',            en: 'Manage users' },
    'svc.scode_mgmt':     { ko: 'S-Code',               en: 'S-Code' },
    'svc.scode_mgmt.desc':{ ko: 'VM S-Code 규칙 설정',   en: 'Configure S-Code rules' },
    'svc.projects':       { ko: 'Projects',             en: 'Projects' },
    'svc.projects.desc':  { ko: '프로젝트 토큰 관리',     en: 'Manage project tokens' },
    'svc.logs':           { ko: 'Logs',                 en: 'Logs' },
    'svc.logs.desc':      { ko: 'CloudTrail 감사 로그',   en: 'CloudTrail audit logs' },
    'home.welcome':       { ko: 'InfraPilot 콘솔에 오신 것을 환영합니다.', en: 'Welcome to InfraPilot Console.' },
    'home.recent':        { ko: '최근 방문',              en: 'Recent Visits' },
    'home.notif':         { ko: '알림',                  en: 'Notifications' },
    'home.cloudtrail':    { ko: 'CloudTrail',            en: 'CloudTrail' },
    'home.changelog':     { ko: '업데이트 로그',           en: 'Update Log' },
    'home.favs':          { ko: '즐겨찾기',               en: 'Favorites' },
    'home.status':        { ko: '시스템 상태',             en: 'System Status' },
    'home.view_all':      { ko: '전체 보기 →',            en: 'View all →' },
    'home.refresh':       { ko: '새로고침',               en: 'Refresh' },
    'home.no_recent':     { ko: '아직 방문한 페이지가 없습니다.', en: 'No pages visited yet.' },
    'home.no_recent_sub': { ko: 'Services 메뉴에서 탐색하면 여기에 표시됩니다.', en: 'Browse the Services menu to get started.' },
    'home.tab.user_alert':{ ko: '사용자 알림',             en: 'User Alerts' },
    'home.tab.kc':        { ko: 'KC 공지',                en: 'KC Notices' },
    'home.no_pw_alert':   { ko: '✅ 만료 임박 알림 없음',  en: '✅ No expiry alerts' },
    'home.no_kc':         { ko: 'KC 공지 없음',            en: 'No KC notices' },
    'home.no_history':    { ko: '기록이 없습니다.',         en: 'No records found.' },
    'home.no_changelog':  { ko: '업데이트 로그가 없습니다.',en: 'No update log entries.' },
    'home.no_favs':       { ko: '즐겨찾기가 없습니다.',    en: 'No favorites yet.' },
    'home.no_favs_sub':   { ko: 'Services 메뉴 항목 옆 ⭐를 눌러 추가하세요.', en: 'Click ⭐ next to any item in the Services menu.' },
    'home.status_ok':     { ko: '정상',                   en: 'OK' },
    'home.status_slow':   { ko: '지연',                   en: 'Slow' },
    'home.status_err':    { ko: '오류',                   en: 'Error' },
    'home.status_check':  { ko: '확인 중',                en: 'Checking' },
    'home.status_fail':   { ko: '상태 확인 실패',          en: 'Status check failed' },
    'home.fav_del_fail':  { ko: '로드 실패',              en: 'Failed to load' },
    'home.pw_expiry':     { ko: 'scv 비밀번호 만료 %d일 전', en: 'scv password expires in %d days' },
};

window._lang = localStorage.getItem('infrapilot_lang') || 'ko';

window.t = function(key) {
    var args = Array.prototype.slice.call(arguments, 1);
    var entry = window.I18N[key];
    if (!entry) return key;
    var str = entry[window._lang] || entry['ko'] || key;
    args.forEach(function(a) { str = str.replace(/%[sd]/, a); });
    return str;
};

window.applyLang = function(lang) {
    window._lang = lang;
    localStorage.setItem('infrapilot_lang', lang);
    document.cookie = 'infrapilot_lang=' + lang + ';path=/;max-age=31536000';
    document.documentElement.setAttribute('lang', lang);
    document.querySelectorAll('[data-i18n]').forEach(function(el) {
        var key = el.dataset.i18n;
        if (window.I18N[key]) el.textContent = window.t(key);
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(function(el) {
        var key = el.dataset.i18nPlaceholder;
        if (window.I18N[key]) el.placeholder = window.t(key);
    });
    document.querySelectorAll('[data-i18n-title]').forEach(function(el) {
        var key = el.dataset.i18nTitle;
        if (window.I18N[key]) el.title = window.t(key);
    });
};

} // end if (!window.I18N)

// Always apply on load (safe even if called multiple times)
window.applyLang(localStorage.getItem('infrapilot_lang') || 'ko');