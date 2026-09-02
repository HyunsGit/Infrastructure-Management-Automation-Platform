// Render table including Private IP and added DNS Status column
function renderSkeletonTable(hostList) {
  let table = `
    <table>
      <thead>
        <tr>
          <th>Host</th>
          <th>CIDR Range</th>
          <th>Ping Status</th>
          <th>SSH Status</th>
          <th>DNS Status</th>
        </tr>
      </thead>
      <tbody>
  `;

  if (hostList.length === 0) {
    table += `
      <tr>
        <td colspan="5" style="color:#888;">모니터링 대상 호스트가 없습니다.</td>
      </tr>
    `;
  } else {
    hostList.forEach((host, idx) => {
      const ip = PRIVATE_IP_MAP[host] || 'N/A';
      table += `
        <tr data-host="${host}">
          <td>${host}</td>
          <td>${ip}</td>
          <td id="ping-${idx}">
            <div class="cell-spinner" aria-label="Ping 상태 로딩 중" role="status"></div>
          </td>
          <td id="ssh-${idx}">
            <div class="cell-spinner" aria-label="SSH 상태 로딩 중" role="status"></div>
          </td>
          <td id="dns-${idx}">
            <div class="cell-spinner" aria-label="DNS 상태 로딩 중" role="status"></div>
          </td>
        </tr>
      `;
    });
  }

  table += '</tbody></table>';
  document.getElementById('connectivity-table').innerHTML = table;
}

// Update status cells with colored dots including DNS status
function updateStatusCells(statusData) {
  statusData.forEach((row, idx) => {
    // Update Ping Status
    let pingTd = document.getElementById(`ping-${idx}`);
    if (pingTd) {
      if ('ping' in row) {
        pingTd.className = "";
        pingTd.innerHTML = row.ping
          ? '<span class="status-dot status-ok-dot" aria-label="정상"></span>'
          : '<span class="status-dot status-fail-dot" aria-label="실패"></span>';
        pingTd.setAttribute('aria-label', `Ping 상태: ${row.ping ? '정상' : '실패'}`);
      } else {
        pingTd.innerHTML = '<span class="cell-error">오류</span>';
        pingTd.setAttribute('aria-label', 'Ping 상태 오류');
      }
    }

    // Update SSH Status
    let sshTd = document.getElementById(`ssh-${idx}`);
    if (sshTd) {
      if ('ssh' in row) {
        sshTd.className = "";
        sshTd.innerHTML = row.ssh
          ? '<span class="status-dot status-ok-dot" aria-label="정상"></span>'
          : '<span class="status-dot status-fail-dot" aria-label="실패"></span>';
        sshTd.setAttribute('aria-label', `SSH 상태: ${row.ssh ? '정상' : '실패'}`);
      } else {
        sshTd.innerHTML = '<span class="cell-error">오류</span>';
        sshTd.setAttribute('aria-label', 'SSH 상태 오류');
      }
    }

    // Update DNS Status
    let dnsTd = document.getElementById(`dns-${idx}`);
    if (dnsTd) {
      if ('dns' in row) {
        dnsTd.className = "";
        dnsTd.innerHTML = row.dns
          ? '<span class="status-dot status-ok-dot" aria-label="정상"></span>'
          : '<span class="status-dot status-fail-dot" aria-label="실패"></span>';
        dnsTd.setAttribute('aria-label', `DNS 상태: ${row.dns ? '정상' : '실패'}`);
      } else {
        dnsTd.innerHTML = '<span class="cell-error">오류</span>';
        dnsTd.setAttribute('aria-label', 'DNS 상태 오류');
      }
    }
  });
}

// Fetch connectivity data from backend API
function fetchConnectivityStatus() {
  $.getJSON(CONNECTIVITY_STATUS_URL, function(data) {
    updateStatusCells(data);
  }).fail(function() {
    MONITOR_HOSTS.forEach((_, idx) => {
      let pingTd = document.getElementById(`ping-${idx}`);
      let sshTd = document.getElementById(`ssh-${idx}`);
      let dnsTd = document.getElementById(`dns-${idx}`);

      if (pingTd) {
        pingTd.innerHTML = '<span class="cell-error">오류</span>';
        pingTd.setAttribute('aria-label', 'Ping 상태 오류');
      }
      if (sshTd) {
        sshTd.innerHTML = '<span class="cell-error">오류</span>';
        sshTd.setAttribute('aria-label', 'SSH 상태 오류');
      }
      if (dnsTd) {
        dnsTd.innerHTML = '<span class="cell-error">오류</span>';
        dnsTd.setAttribute('aria-label', 'DNS 상태 오류');
      }
    });
  });
}

// Initialize on page load
$(function() {
  renderSkeletonTable(MONITOR_HOSTS);
  fetchConnectivityStatus();

  // Bind manual refresh button
  $('#refreshBtn').on('click', function() {
    renderSkeletonTable(MONITOR_HOSTS);
    fetchConnectivityStatus();
  });
});
