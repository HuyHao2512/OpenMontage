const API = '';
let currentProjectId = null;
let currentPipeline = 'animated-explainer';
let chatHistory = [];
let progressInterval = null;
let generationInProgress = false;

function showToast(msg, type = 'success') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = `toast ${type} show`;
  setTimeout(() => t.classList.remove('show'), 4000);
}

async function api(path, method = 'GET', body = null) {
  const opts = { method, headers: {} };
  if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`${API}${path}`, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.error || `Lỗi ${res.status}`);
  }
  return data;
}

function setLoading(id, loading) {
  const btn = document.getElementById(id);
  if (!btn) return;
  btn.disabled = loading;
  const old = btn.querySelector('.spinner');
  if (loading) {
    if (!old) btn.insertAdjacentHTML('afterbegin', '<div class="spinner"></div>');
  } else if (old) {
    old.remove();
  }
}

async function loadHealth() {
  try {
    const data = await api('/health');
    const el = document.getElementById('serverStatus');
    el.textContent = `Sẵn sàng · v${data.version}`;
    document.getElementById('versionInfo').textContent = `backend v${data.version}`;
  } catch (e) {
    document.getElementById('serverStatus').textContent = 'Mất kết nối';
  }
}

async function loadPreflight() {
  setLoading('btnPreflight', true);
  try {
    const data = await api('/preflight');
    const out = document.getElementById('preflightResult');
    const caps = data.capabilities?.capabilities || [];
    const runtimes = data.capabilities?.composition_runtimes || {};
    let html = '';
    Object.entries(runtimes).forEach(([k, v]) => {
      html += `<span class="chip ${v ? 'ok' : 'error'}">${k}: ${v ? 'sẵn sàng' : 'chưa sẵn sàng'}</span>`;
    });
    caps.forEach(c => {
      const ok = c.configured > 0;
      html += `<span class="chip ${ok ? 'ok' : 'warn'}">${c.capability}: ${c.configured}/${c.total}</span>`;
    });
    out.innerHTML = html;
    showToast('Preflight hoàn tất');
  } catch (e) {
    showToast(`Preflight lỗi: ${e.message}`, 'error');
  } finally {
    setLoading('btnPreflight', false);
  }
}

async function loadPipelines() {
  try {
    const data = await api('/pipelines');
    const sel = document.getElementById('pipelineSelect');
    sel.innerHTML = (data.pipelines || []).map(p =>
      `<option value="${p.id}">${p.name} (${p.stability})</option>`
    ).join('');
    if (sel.value) currentPipeline = sel.value;
    sel.onchange = () => currentPipeline = sel.value;
  } catch (e) {
    showToast(`Không tải được pipeline: ${e.message}`, 'error');
  }
}

async function createProject() {
  const title = document.getElementById('projectTitle').value.trim();
  const brief = document.getElementById('projectBrief').value.trim();
  if (!title) return showToast('Vui lòng nhập tên dự án', 'error');

  setLoading('btnCreate', true);
  try {
    const project = await api('/projects', 'POST', { title, pipeline: currentPipeline, brief });
    currentProjectId = project.project_id;
    showToast(`Đã tạo dự án ${project.project_id}`);
    document.getElementById('projectTitle').value = '';
    document.getElementById('projectBrief').value = '';
    await loadProjects();
    await refreshChatProjects();
    selectChatProject(project.project_id);
    if (brief) addChatMessage('user', brief);
  } catch (e) {
    showToast(`Tạo dự án lỗi: ${e.message}`, 'error');
  } finally {
    setLoading('btnCreate', false);
  }
}

async function loadProjects() {
  try {
    const data = await api('/projects');
    const list = document.getElementById('projectList');
    const projects = data.projects || [];
    if (!projects.length) {
      list.innerHTML = '<div class="project-item"><small>Chưa có dự án nào.</small></div>';
      return;
    }
    list.innerHTML = projects.map(p => `
      <div class="project-item" onclick="selectChatProject('${p.project_id}')">
        <div>
          <strong>${p.title}</strong>
          <small>${p.project_id} · ${p.pipeline} · ${new Date(p.created_at).toLocaleString('vi-VN')}</small>
        </div>
        <span class="chip">${p.status || 'new'}</span>
      </div>
    `).join('');
  } catch (e) {
    showToast(`Tải danh sách lỗi: ${e.message}`, 'error');
  }
}

async function refreshChatProjects() {
  try {
    const data = await api('/projects');
    const sel = document.getElementById('chatProjectSelect');
    const projects = data.projects || [];
    sel.innerHTML = '<option value="">Chưa chọn dự án</option>' +
      projects.map(p => `<option value="${p.project_id}">${p.title}</option>`).join('');
    if (currentProjectId) sel.value = currentProjectId;
  } catch (e) {
    showToast(`Làm mới dự án lỗi: ${e.message}`, 'error');
  }
}

function selectChatProject(projectId) {
  currentProjectId = projectId;
  const sel = document.getElementById('chatProjectSelect');
  if (sel) sel.value = projectId;
  document.getElementById('btnQuickGenerate').disabled = !projectId || generationInProgress;
  refreshPreview();
}

function addChatMessage(role, text) {
  const box = document.getElementById('chatBox');
  const welcome = box.querySelector('.chat-welcome');
  if (welcome) welcome.remove();
  const div = document.createElement('div');
  div.className = `chat-message ${role}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

async function sendChatMessage() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text) return;
  
  if (!currentProjectId) {
    setLoading('btnChatSend', true);
    try {
      // Auto-create a project based on the first message
      const title = text.split(' ').slice(0, 5).join(' ') + '...';
      const project = await api('/projects', 'POST', { 
        title: title, 
        pipeline: currentPipeline, 
        brief: text 
      });
      currentProjectId = project.project_id;
      await loadProjects();
      await refreshChatProjects();
      selectChatProject(project.project_id);
    } catch (e) {
      showToast('Tạo dự án lỗi: ' + e.message, 'error');
      setLoading('btnChatSend', false);
      return;
    }
  }

  addChatMessage('user', text);
  chatHistory.push({ role: 'user', content: text });
  input.value = '';
  setLoading('btnChatSend', true);

  try {
    const data = await api(`/projects/${currentProjectId}/chat`, 'POST', {
      message: text,
      history: chatHistory.slice(-10),
    });
    chatHistory.push({ role: 'assistant', content: data.reply });
    addChatMessage('assistant', data.reply);

    if (data.intent === 'generate') {
      document.getElementById('btnQuickGenerate').disabled = false;
      showToast('Bạn có thể nhấn "Tạo video ngay" để bắt đầu sản xuất', 'success');
    }
  } catch (e) {
    showToast(`Chat lỗi: ${e.message}`, 'error');
  } finally {
    setLoading('btnChatSend', false);
  }
}

async function quickGenerate() {
  if (!currentProjectId) return showToast('Chưa chọn dự án', 'error');

  // Use the entire conversation history as the brief for the pipeline
  const brief = chatHistory
    .map(m => (m.role === 'user' ? 'Khách hàng: ' : 'Đạo diễn AI: ') + m.content)
    .join('\n');

  const btn = document.getElementById('btnQuickGenerate');
  const spinner = document.getElementById('btnQuickGenerateSpinner');
  btn.disabled = true;
  spinner.classList.remove('hidden');
  generationInProgress = true;

  // Show progress panel
  const panel = document.getElementById('generationProgress');
  panel.classList.remove('hidden');
  updateProgressSteps('proposal', 'running');
  document.getElementById('progressStatus').textContent = 'Đang khởi động pipeline…';

  try {
    await api(`/projects/${currentProjectId}/generate`, 'POST', {
      brief,
      pipeline: currentPipeline,
      duration_seconds: 60,
      language: 'vi',
    });
    showToast('Pipeline đã chạy. Theo dõi tiến trình bên dưới.');
    startProgressPolling(currentProjectId);
  } catch (e) {
    showToast(`Tạo video lỗi: ${e.message}`, 'error');
    btn.disabled = false;
    spinner.classList.add('hidden');
    generationInProgress = false;
    updateProgressSteps('proposal', 'failed');
    document.getElementById('progressStatus').textContent = 'Lỗi: ' + e.message;
  }
}

function updateProgressSteps(stage, status) {
  const order = ['proposal', 'script', 'scene_plan', 'assets', 'edit', 'compose'];
  const labels = {
    proposal: 'Kế hoạch',
    script: 'Kịch bản',
    scene_plan: 'Scene plan',
    assets: 'Tài nguyên',
    edit: 'Edit',
    compose: 'Render',
  };
  const container = document.getElementById('progressSteps');
  const idx = order.indexOf(stage);
  container.innerHTML = order.map((s, i) => {
    let cls = 'progress-step';
    if (s === stage && status === 'running') cls += ' running';
    else if (i < idx || (s === stage && status === 'completed')) cls += ' completed';
    else if (status === 'failed' && s === stage) cls += ' failed';
    const icon = cls.includes('completed') ? '✓' : cls.includes('failed') ? '✕' : cls.includes('running') ? '●' : '○';
    return `<span class="${cls}">${icon} ${labels[s]}</span>`;
  }).join('');
}

function startProgressPolling(projectId) {
  if (progressInterval) clearInterval(progressInterval);
  progressInterval = setInterval(async () => {
    try {
      const data = await api(`/projects/${projectId}`);
      const checkpoint = data.latest_checkpoint || {};
      const stage = checkpoint.stage || 'proposal';
      const status = checkpoint.status || 'running';
      updateProgressSteps(stage, status);
      document.getElementById('progressStatus').textContent =
        `${stage}: ${status === 'completed' ? 'hoàn tất' : status === 'failed' ? 'thất bại' : 'đang chạy'}`;

      if ((stage === 'compose' && status === 'completed') || status === 'failed') {
        clearInterval(progressInterval);
        progressInterval = null;
        generationInProgress = false;
        document.getElementById('btnQuickGenerate').disabled = false;
        document.getElementById('btnQuickGenerateSpinner').classList.add('hidden');
        if (status === 'completed') {
          showToast('Video đã sẵn sàng!');
          refreshPreview();
        } else {
          showToast('Tạo video thất bại. Xem log để biết chi tiết.', 'error');
        }
      }
    } catch (e) {
      // keep polling despite transient errors
    }
  }, 3000);
}

async function refreshPreview() {
  const video = document.getElementById('videoPreview');
  const placeholder = document.getElementById('videoPlaceholder');
  const link = document.getElementById('videoDownloadLink');
  if (!currentProjectId) {
    video.classList.add('hidden');
    placeholder.classList.remove('hidden');
    link.classList.add('hidden');
    return;
  }
  const src = `/projects/${currentProjectId}/assets/renders/final.mp4`;
  try {
    const res = await fetch(src, { method: 'HEAD' });
    if (res.ok) {
      video.src = src;
      video.classList.remove('hidden');
      placeholder.classList.add('hidden');
      link.href = src;
      link.classList.remove('hidden');
    } else {
      video.classList.add('hidden');
      placeholder.classList.remove('hidden');
      link.classList.add('hidden');
    }
  } catch (e) {
    video.classList.add('hidden');
    placeholder.classList.remove('hidden');
    link.classList.add('hidden');
  }
}

async function runIdea() {
  if (!currentProjectId) return showToast('Chưa chọn dự án', 'error');
  try {
    const data = await api(`/projects/${currentProjectId}/run/idea`, 'POST');
    showToast(`Stage ý tưởng: ${data.status}`);
  } catch (e) {
    showToast(`Chạy stage lỗi: ${e.message}`, 'error');
  }
}

window.addEventListener('DOMContentLoaded', () => {
  loadHealth();
  loadPipelines();
  loadProjects();
  refreshChatProjects();

  const chatInput = document.getElementById('chatInput');
  if (chatInput) {
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') sendChatMessage();
    });
  }

  const chatProjectSelect = document.getElementById('chatProjectSelect');
  if (chatProjectSelect) {
    chatProjectSelect.addEventListener('change', (e) => {
      selectChatProject(e.target.value);
    });
  }
});
