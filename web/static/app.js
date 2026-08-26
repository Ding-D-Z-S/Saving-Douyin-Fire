async function refreshStatus() {
  const statusNode = document.getElementById('job-status');
  if (!statusNode) return;
  try {
    const response = await fetch('/api/status', { cache: 'no-store' });
    if (!response.ok) return;
    const payload = await response.json();
    const setText = (id, value) => {
      const node = document.getElementById(id);
      if (node) node.textContent = value || '-';
    };
    setText('job-status', payload.status);
    setText('job-mode', payload.mode || '-');
    setText('job-pid', payload.pid ? String(payload.pid) : '-');
    setText('job-started', payload.started_at || '-');
    setText('job-finished', payload.finished_at || '-');
    const logNode = document.getElementById('log-tail');
    if (logNode) logNode.textContent = payload.log_tail || '';
  } catch (_) {
  }
}

function showClientFlash(message, level = 'info') {
  let node = document.getElementById('client-flash');
  if (!node) {
    node = document.createElement('div');
    node.id = 'client-flash';
    const container = document.querySelector('main.container');
    if (container) container.prepend(node);
  }
  node.className = `flash ${level}`;
  node.textContent = message;
  node.hidden = false;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function createEntryRow(entry = { group: '当前勾选', hour: 9, minute: 15, content: '续火花 ✨', interval_seconds: '' }, groups = []) {
  const row = document.createElement('div');
  row.className = 'schedule-entry';
  const savedGroup = entry.group || '当前勾选';
  const savedInterval = entry.interval_seconds ?? '';
  const options = ['当前勾选', ...groups.map((item) => item.name), savedGroup]
    .filter((value, index, array) => value && array.indexOf(value) === index);
  row.innerHTML = `
    <select name="schedule_group" class="group-input" aria-label="发送分组">
      ${options.map((name) => `<option value="${escapeHtml(name)}" ${name === savedGroup ? 'selected' : ''}>${escapeHtml(name)}</option>`).join('')}
    </select>
    <span class="entry-label">每日</span>
    <select name="schedule_hour" class="small-select" aria-label="发送小时">
      ${Array.from({ length: 24 }, (_, i) => `<option value="${i}" ${i === Number(entry.hour) ? 'selected' : ''}>${String(i).padStart(2, '0')}</option>`).join('')}
    </select>
    <span class="entry-label">时</span>
    <select name="schedule_minute" class="small-select" aria-label="发送分钟">
      ${Array.from({ length: 60 }, (_, i) => `<option value="${i}" ${i === Number(entry.minute) ? 'selected' : ''}>${String(i).padStart(2, '0')}</option>`).join('')}
    </select>
    <span class="entry-label">分</span>
    <input type="text" name="schedule_content" class="content-input" value="${escapeHtml(entry.content || '')}" placeholder="发送内容">
    <span class="interval-field" title="对多人分组逐人发送时的间隔（秒），单人分组自动忽略，可留空">
      <span class="entry-label">间隔</span>
      <input type="number" name="schedule_interval" min="0" max="3600" step="1" value="${escapeHtml(savedInterval)}" placeholder="秒">
    </span>
    <button type="button" class="secondary test-entry">测试发送</button>
    <button type="button" class="secondary delete-entry">删除</button>
  `;
  row.querySelector('.delete-entry').addEventListener('click', () => row.remove());
  row.querySelector('.test-entry').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const content = row.querySelector('input[name="schedule_content"]').value.trim();
    const group = row.querySelector('select[name="schedule_group"]').value;
    const interval = row.querySelector('input[name="schedule_interval"]').value.trim();
    if (!content) {
      window.alert('请先填写测试发送内容');
      return;
    }
    const memberCountHint = row.getAttribute('data-member-count') || '';
    const suffix = memberCountHint ? `（约 ${memberCountHint} 人${interval ? `，间隔 ${interval} 秒` : ''}）` : '';
    if (!window.confirm(`将立即向“${group}”真实发送这条测试消息，并打开可见浏览器。${suffix}确定继续吗？`)) return;
    button.disabled = true;
    button.textContent = '启动中…';
    const payload = new URLSearchParams({
      single_content: content,
      single_group: group,
      current_selection_submitted: '1'
    });
    if (interval) payload.append('single_interval', interval);
    document.querySelectorAll('.row-checkbox:checked').forEach((checkbox) => {
      payload.append('selected_conversation_ids', checkbox.value);
    });
    try {
      const response = await fetch('/config/test-send', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
          'X-Requested-With': 'XMLHttpRequest'
        },
        body: payload.toString()
      });
      const result = await response.json();
      showClientFlash(result.message, result.ok ? 'success' : 'error');
      await refreshStatus();
    } catch (_) {
      showClientFlash('测试发送启动失败，请查看日志信息', 'error');
    } finally {
      button.disabled = false;
      button.textContent = '测试发送';
    }
  });
  return row;
}

function initScheduleEditor() {
  const list = document.getElementById('schedule-list');
  if (!list) return;
  let existing = [];
  let groups = [];
  try {
    existing = JSON.parse(list.dataset.existing || '[]');
  } catch (_) {
    existing = [];
  }
  try {
    groups = JSON.parse(list.dataset.groups || '[]');
  } catch (_) {
    groups = [];
  }
  const computeMembers = (row) => {
    const group = row.querySelector('select[name="schedule_group"]').value;
    if (group === '当前勾选') {
      return document.querySelectorAll('.row-checkbox:checked').length;
    }
    const found = groups.find((item) => item.name === group);
    return found ? (found.members || []).length : 0;
  };
  const refreshHint = () => {
    list.querySelectorAll('.schedule-entry').forEach((row) => {
      row.setAttribute('data-member-count', String(computeMembers(row)));
    });
  };
  const addRow = (entry) => {
    const row = createEntryRow(entry, groups);
    list.appendChild(row);
    refreshHint();
  };
  if (!Array.isArray(existing) || existing.length === 0) {
    existing = [{ group: '当前勾选', hour: 9, minute: 15, content: '续火花 ✨', interval_seconds: '' }];
  }
  list.innerHTML = '';
  existing.forEach((entry) => addRow(entry));
  const addButton = document.getElementById('add-schedule-entry');
  if (addButton) {
    addButton.addEventListener('click', () => addRow({ group: '当前勾选', hour: 9, minute: 15, content: '续火花 ✨', interval_seconds: '' }));
  }
  list.addEventListener('change', refreshHint);
  document.addEventListener('change', (event) => {
    if (event.target && event.target.classList && event.target.classList.contains('row-checkbox')) refreshHint();
  });
  refreshHint();
}

function initConversationSummary() {
  const summary = document.getElementById('selected-summary-names');
  if (!summary) return;
  const refresh = () => {
    const checked = Array.from(document.querySelectorAll('.row-checkbox:checked'));
    const names = checked.map((input) => {
      const row = input.closest('.douyin-row');
      const name = row ? row.querySelector('.name') : null;
      return name ? name.textContent.trim() : '';
    }).filter(Boolean);
    summary.textContent = names.length ? names.join('、') : '暂未勾选';
  };
  document.querySelectorAll('.row-checkbox').forEach((input) => {
    input.addEventListener('change', refresh);
  });
  refresh();
}

function appendLiveSelection(form) {
  if (!form) return;
  form.querySelectorAll('input[name="selected_conversation_ids"], input[name="selected_conversation_names"], input[name="live_selection_submitted"]').forEach((input) => input.remove());
  const marker = document.createElement('input');
  marker.type = 'hidden';
  marker.name = 'live_selection_submitted';
  marker.value = '1';
  form.appendChild(marker);
  document.querySelectorAll('.row-checkbox:checked').forEach((checkbox) => {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'selected_conversation_ids';
    input.value = checkbox.value;
    form.appendChild(input);
    const nameInput = document.createElement('input');
    nameInput.type = 'hidden';
    nameInput.name = 'selected_conversation_names';
    nameInput.value = checkbox.closest('.douyin-row')?.querySelector('.name')?.textContent?.trim() || '';
    form.appendChild(nameInput);
  });
}

function initConfigFormValidation() {
  const form = document.getElementById('config-form');
  if (!form) return;
  form.addEventListener('submit', (event) => {
    const action = event.submitter?.value || 'save_plan';
    if (action === 'create_group') return;
    const entries = form.querySelectorAll('.schedule-entry');
    if (!entries.length) {
      event.preventDefault();
      showClientFlash('至少添加一个发送计划', 'error');
    }
  });
}

function initConversationActions() {
  const refreshForm = document.getElementById('refresh-conversations-form');
  if (refreshForm) {
    refreshForm.addEventListener('submit', () => {
      appendLiveSelection(refreshForm);
      const button = refreshForm.querySelector('button[type="submit"]');
      if (button) {
        button.disabled = true;
        button.textContent = '温和读取中…';
      }
    });
  }
}

function initClearConfirmations() {
  const logForm = document.getElementById('clear-log-form');
  if (logForm) {
    logForm.addEventListener('submit', (event) => {
      if (!window.confirm('确定清空所有本地日志吗？清空后无法恢复。')) event.preventDefault();
    });
  }
  const feishuForm = document.getElementById('clear-feishu-form');
  if (feishuForm) {
    feishuForm.addEventListener('submit', (event) => {
      if (!window.confirm('确定清除本地保存的飞书 Webhook 吗？')) event.preventDefault();
    });
  }
  const feishuAppForm = document.getElementById('clear-feishu-app-form');
  if (feishuAppForm) {
    feishuAppForm.addEventListener('submit', (event) => {
      if (!window.confirm('确定清除飞书应用 App ID / App Secret 并断开长连接吗？')) event.preventDefault();
    });
  }
}

function initDeleteGroupConfirmation() {
  document.querySelectorAll('.delete-group-form').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const name = form.querySelector('input[name="group_name"]')?.value || '';
      if (!window.confirm(`确定删除分组“${name}”吗？`)) event.preventDefault();
    });
  });
}

initScheduleEditor();
initConversationSummary();
initConfigFormValidation();
initConversationActions();
initClearConfirmations();
initDeleteGroupConfirmation();
const feishuChat = document.getElementById('feishu-chat');
if (feishuChat) feishuChat.scrollTop = feishuChat.scrollHeight;
setInterval(refreshStatus, 3000);
refreshStatus();
