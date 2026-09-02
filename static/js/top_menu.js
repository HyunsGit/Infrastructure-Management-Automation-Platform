// top_menu.js

// 도움말 아이콘 클릭 시 링크 열기
document.getElementById('helpIcon')?.addEventListener('click', () => {
    window.open('https://dkt.agit.in/g/300075015/wall/');
});


const burger = document.getElementById('burgerBtn');
const sidebar = document.getElementById('sidebarMenu');
const logoBtn = document.getElementById('logoBtn');
const overlay = document.getElementById('overlay');

if (logoBtn) {
    logoBtn.addEventListener('click', () => location.reload());
    logoBtn.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            location.reload();
        }
    });
}

function openSidebar() {
    sidebar.classList.add('open');
    sidebar.setAttribute('aria-hidden', 'false');
    burger.setAttribute('aria-expanded', 'true');
    overlay.classList.add('active');
    const firstLink = sidebar.querySelector('a');
    if (firstLink) firstLink.focus();

    // 포커스 트랩 간단 구현 (탭키로 사이드바 내부 순환)
    sidebar.addEventListener('keydown', trapFocus);
}

function closeSidebar() {
    sidebar.classList.remove('open');
    sidebar.setAttribute('aria-hidden', 'true');
    burger.setAttribute('aria-expanded', 'false');
    overlay.classList.remove('active');
    burger.focus();

    sidebar.removeEventListener('keydown', trapFocus);
}

function trapFocus(e) {
    const focusableElements = sidebar.querySelectorAll('a, button, input, [tabindex]:not([tabindex="-1"])');
    const firstEl = focusableElements[0];
    const lastEl = focusableElements[focusableElements.length - 1];

    if (e.key === 'Tab') {
        if (e.shiftKey) {
            // Shift + Tab
            if (document.activeElement === firstEl) {
                e.preventDefault();
                lastEl.focus();
            }
        } else {
            // Tab
            if (document.activeElement === lastEl) {
                e.preventDefault();
                firstEl.focus();
            }
        }
    }
}

burger.addEventListener('click', () => {
    if (sidebar.classList.contains('open')) closeSidebar();
    else openSidebar();
});

overlay.addEventListener('click', closeSidebar);

document.addEventListener('keydown', (e) => {
    if (e.key === "Escape" && sidebar.classList.contains('open')) closeSidebar();

    // 드롭다운 ESC 처리도 여기서 가능 (선택사항)
    if (e.key === "Escape") {
        document.querySelectorAll('.menu .dropdown').forEach(dropdown => {
            const dropdownContent = dropdown.querySelector('.dropdown-content');
            dropdown.setAttribute('aria-expanded', 'false');
            dropdownContent.style.display = 'none';
        });
    }
});

// Replace your existing dropdown toggle JS with this:

document.querySelectorAll('.menu .dropdown').forEach(dropdown => {
    const dropdownContent = dropdown.querySelector('.dropdown-content');

    // Keyboard open/close toggle
    dropdown.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            const expanded = dropdown.getAttribute('aria-expanded') === 'true';
            dropdown.setAttribute('aria-expanded', !expanded);
            dropdown.classList.toggle('open', !expanded);
        } else if (e.key === 'Escape') {
            dropdown.setAttribute('aria-expanded', 'false');
            dropdown.classList.remove('open');
            dropdown.focus();
        }
    });

    // Click toggle using class
    dropdown.addEventListener('click', e => {
        e.preventDefault();
        const expanded = dropdown.getAttribute('aria-expanded') === 'true';
        dropdown.setAttribute('aria-expanded', !expanded);
        dropdown.classList.toggle('open', !expanded);
    });

    // Prevent dropdown content from closing when clicking inside it
    dropdownContent.addEventListener('click', e => {
        e.stopPropagation();
    });
});

// Close all dropdowns if clicking outside
document.addEventListener('click', e => {
    document.querySelectorAll('.menu .dropdown').forEach(dropdown => {
        if (!dropdown.contains(e.target)) {
            dropdown.setAttribute('aria-expanded', 'false');
            dropdown.classList.remove('open');
        }
    });
});
