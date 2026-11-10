/**
 * 话语霸凌模拟系统 - 网页版 JavaScript v2
 *
 * 新特性:
 * 1. 自动开始,自动运行
 * 2. 5秒等待干预
 * 3. 干预驱动继续
 * 4. 教师主动结束
 */

// 全局状态
const state = {
    sessionId: null,
    level: 2,
    isRunning: false,
    isPaused: false,
    isStopping: false,
    maxRounds: 6,
    currentRound: 0,
    messages: [],
    interventions: [],
    scenarios: [],  // 情境列表
    currentScenario: null,  // 当前情境
    scenarioModified: false,  // 情境是否被修改
    lastReport: null,  // 上次的报告数据
    sessionHistory: [],  // 会话历史记录
    participants: [],  // 参与者列表
    currentEditingParticipant: null,  // 当前编辑的参与者
    interventionLibrary: [],  // 干预库
    recommendedInterventions: [],  // 推荐干预
    allInterventions: [],  // 所有干预
    currentEditingIntervention: null,  // 当前编辑的干预
    showAllInterventions: false,  // 是否显示所有干预
    reportGenerating: false,  // 报告是否正在生成中
};

// 认证状态
const auth = {
    token: null,
    user: null,
};

// API 基础 URL
const API_BASE = '';

// ============================================================
// 认证模块
// ============================================================

async function checkAuth() {
    // 尝试从 localStorage 恢复 token
    const saved = localStorage.getItem('auth_token');
    if (saved) {
        auth.token = saved;
        try {
            const resp = await fetch(`${API_BASE}/api/auth/me`, {
                headers: { 'Authorization': `Bearer ${auth.token}` }
            });
            if (resp.ok) {
                const data = await resp.json();
                auth.user = data.user;
                hideAuthOverlay();
                updateUserDisplay();
                return;
            }
        } catch (e) {
            console.log('Token 验证失败:', e);
        }
        // Token 无效，清除
        auth.token = null;
        localStorage.removeItem('auth_token');
    }
    showAuthOverlay();
}

function showAuthOverlay() {
    document.getElementById('auth-overlay').classList.remove('hidden');
    document.getElementById('app-container').classList.add('hidden');
}

function hideAuthOverlay() {
    document.getElementById('auth-overlay').classList.add('hidden');
    document.getElementById('app-container').classList.remove('hidden');
}

function updateUserDisplay() {
    if (auth.user) {
        const label = auth.user.display_name || auth.user.username;
        document.getElementById('username-label').textContent = label;
        // 管理员显示管理后台入口
        if (auth.user.is_admin) {
            document.getElementById('admin-link').classList.remove('hidden');
        }
    }
}

async function doLogin(username, password) {
    const resp = await fetch(`${API_BASE}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
    });
    const data = await resp.json();
    if (!resp.ok) {
        throw new Error(data.detail || '登录失败');
    }
    auth.token = data.token;
    auth.user = data.user;
    localStorage.setItem('auth_token', data.token);
    hideAuthOverlay();
    updateUserDisplay();
}

async function doRegister(username, password, display_name) {
    const resp = await fetch(`${API_BASE}/api/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, display_name })
    });
    const data = await resp.json();
    if (!resp.ok) {
        throw new Error(data.detail || '注册失败');
    }
    auth.token = data.token;
    auth.user = data.user;
    localStorage.setItem('auth_token', data.token);
    hideAuthOverlay();
    updateUserDisplay();
}

function doLogout() {
    auth.token = null;
    auth.user = null;
    localStorage.removeItem('auth_token');
    showAuthOverlay();
    // 清空表单
    document.getElementById('login-username').value = '';
    document.getElementById('login-password').value = '';
    document.getElementById('login-error').classList.add('hidden');
    document.getElementById('register-error').classList.add('hidden');
}

// ============================================================
// 初始化
// ============================================================

document.addEventListener('DOMContentLoaded', async () => {
    // 先检查认证状态
    await checkAuth();
    
    initEventListeners();
    updateLevelInfo(2);
    loadScenarios();
    loadParticipants().then(() => renderParticipantCheckboxes());
    loadInterventions();
});

// ============================================================
// 事件监听
// ============================================================

// 统一的按钮状态管理
// 状态类型: 'initial', 'ready', 'running', 'stopped', 'report'
function updateButtonStates(state) {
    const simControl = document.getElementById('sim-control-group');
    const simRunning = document.getElementById('sim-running-group');
    const btnNewSess = document.getElementById('btn-new-session-alt');
    const btnStart = document.getElementById('btn-start');
    const btnViewReport = document.getElementById('btn-view-report');
    const btnStop = document.getElementById('btn-stop');
    const btnPause = document.getElementById('btn-pause');
    const pcCheckboxes = document.querySelectorAll('#participant-checkboxes input[type="checkbox"]');

    // 隐藏所有按钮组
    simControl.classList.add('hidden');
    simRunning.classList.add('hidden');
    btnNewSess.classList.add('hidden');
    btnStart.classList.add('hidden');
    btnViewReport.classList.add('hidden');

    switch(state) {
        case 'initial':
            // 初始状态:只有新会话按钮
            simControl.classList.remove('hidden');
            btnNewSess.classList.remove('hidden');
            break;

        case 'ready':
            // 准备状态:显示开始按钮(新会话已点击,情景已选择)
            simControl.classList.remove('hidden');
            btnStart.classList.remove('hidden');
            btnStart.disabled = false;
            break;

        case 'running':
            // 运行状态:显示停止和暂停按钮，禁用角色选择
            simRunning.classList.remove('hidden');
            btnPause.innerHTML = '⏸️ 暂停';
            btnPause.classList.remove('btn-warning');
            btnPause.classList.add('btn-secondary');
            state.isPaused = false;
            pcCheckboxes.forEach(cb => cb.disabled = true);
            break;

        case 'stopped':
            // 停止状态:显示查看报告和新会话按钮,隐藏开始按钮
            simControl.classList.remove('hidden');
            btnStart.classList.add('hidden');
            btnViewReport.classList.remove('hidden');
            btnViewReport.disabled = false;
            btnNewSess.classList.remove('hidden');
            pcCheckboxes.forEach(cb => cb.disabled = false);
            break;

        case 'report':
            // 报告状态:显示查看报告(已打开报告)和新会话
            simControl.classList.remove('hidden');
            btnViewReport.classList.remove('hidden');
            btnNewSess.classList.remove('hidden');
            break;
    }
}

// 更新下载按钮状态（报告生成中 / 就绪）
function updateDownloadButtons(generating) {
    const btnDownloadReport = document.getElementById('btn-download-report');
    const btnDownloadLog = document.getElementById('btn-download-log');
    if (generating) {
        if (btnDownloadReport) {
            btnDownloadReport.textContent = '⏳ 报告生成中...';
            btnDownloadReport.disabled = true;
        }
        if (btnDownloadLog) {
            btnDownloadLog.textContent = '⏳ 报告生成中...';
            btnDownloadLog.disabled = true;
        }
    } else {
        if (btnDownloadReport) {
            btnDownloadReport.textContent = '📥 下载报告';
            btnDownloadReport.disabled = false;
        }
        if (btnDownloadLog) {
            btnDownloadLog.textContent = '📋 下载互动日志';
            btnDownloadLog.disabled = false;
        }
    }
}

function initEventListeners() {
    // 级别选择
    document.querySelectorAll('.level-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.level-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            state.level = parseInt(btn.dataset.level);
            updateLevelInfo(state.level);
        });
    });

    // 情境选择
    document.getElementById('scenario-select').addEventListener('change', onScenarioSelect);

    // 新建情境
    document.getElementById('btn-new-scenario').addEventListener('click', newScenario);

    // 保存情境
    document.getElementById('btn-save-scenario').addEventListener('click', saveScenario);

    // 情境标题/描述修改检测
    document.getElementById('scenario-title').addEventListener('input', onScenarioModified);
    document.getElementById('scenario-desc').addEventListener('input', onScenarioModified);

    // 情境面板折叠/展开
    const header = document.getElementById('scenario-header');
    header.addEventListener('click', function(e) {
        // 如果点击的是按钮或select,不触发展开/折叠
        if (e.target.tagName === 'BUTTON' || e.target.tagName === 'SELECT') {
            return;
        }
        toggleScenarioPanel();
    });

    // 开始按钮
    document.getElementById('btn-start').addEventListener('click', startSimulation);

    // 停止模拟按钮
    document.getElementById('btn-stop').addEventListener('click', () => {
        stopSimulation();
    });

    // 暂停/继续模拟按钮
    document.getElementById('btn-pause').addEventListener('click', togglePause);

    // 结束会话按钮

    // 发送干预
    document.getElementById('btn-intervene').addEventListener('click', sendIntervention);

    // 干预输入框 - 输入内容时提示
    document.getElementById('intervention-input').addEventListener('input', () => {
        const input = document.getElementById('intervention-input');
        if (input.value.trim()) {
            document.querySelector('.waiting-text').textContent = '⏳ 请点击发送按钮';
        }
    });

    // 回车发送(Ctrl+Enter)
    document.getElementById('intervention-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            sendIntervention();
        }
    });

    // 帮助
    document.getElementById('btn-help').addEventListener('click', () => {
        document.getElementById('help-modal').classList.add('active');
    });

    document.getElementById('close-help').addEventListener('click', () => {
        document.getElementById('help-modal').classList.remove('active');
    });

    // 报告模态框
    document.getElementById('close-report').addEventListener('click', () => {
        document.getElementById('report-modal').classList.remove('active');
    });

    // 关闭报告模态框
    document.getElementById('btn-new-session').addEventListener('click', () => {
        document.getElementById('report-modal').classList.remove('active');
        resetToStart();
    });

    // 查看报告按钮
    document.getElementById('btn-view-report').addEventListener('click', viewReport);

    // 详细记录按钮
    document.getElementById('btn-open-detailed-history')?.addEventListener('click', openDetailedHistory);

    // 关闭详细记录模态框
    document.getElementById('close-detail-history')?.addEventListener('click', closeDetailedHistory);
    document.getElementById('btn-close-detail-history')?.addEventListener('click', closeDetailedHistory);

    // 点击模态框外部关闭
    document.getElementById('detail-history-modal')?.addEventListener('click', (e) => {
        if (e.target.id === 'detail-history-modal') closeDetailedHistory();
    });

    // 初始化历史显示
    updateHistoryDisplay();

    // 开始新会话按钮(顶部)
    document.getElementById('btn-new-session-alt')?.addEventListener('click', () => {
        document.getElementById('report-modal').classList.remove('active');
        resetToStart();
        // 启用情境选择并显示开始按钮
        enableScenarioSelection();
    });

    // 下载文本报告
    document.getElementById('btn-download-report').addEventListener('click', downloadTextReport);

    // 导出 PDF
    document.getElementById('btn-export-pdf').addEventListener('click', exportPDF);

    // 导出 Word
    document.getElementById('btn-export-word').addEventListener('click', exportWord);

    // 自动终止弹窗
    document.getElementById('close-autoend').addEventListener('click', () => {
        document.getElementById('autoend-modal').classList.remove('active');
    });
    document.getElementById('btn-autoend-confirm').addEventListener('click', () => {
        document.getElementById('autoend-modal').classList.remove('active');
        showReport();
    });

    // 点击模态框外部关闭
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.classList.remove('active');
            }
        });
    });

    // 参与者编辑相关
    document.getElementById('btn-edit-participants')?.addEventListener('click', openParticipantModal);
    document.getElementById('close-participant')?.addEventListener('click', closeParticipantModal);
    document.getElementById('btn-cancel-participant')?.addEventListener('click', closeParticipantModal);
    document.getElementById('btn-save-participant')?.addEventListener('click', saveParticipant);
    document.getElementById('btn-reset-participant')?.addEventListener('click', resetCurrentParticipant);
    document.getElementById('btn-new-participant')?.addEventListener('click', prepareNewParticipant);
    document.getElementById('btn-delete-participant')?.addEventListener('click', deleteParticipant);

    // 干预库编辑相关
    document.getElementById('btn-edit-interventions')?.addEventListener('click', openInterventionModal);
    document.getElementById('close-intervention')?.addEventListener('click', closeInterventionModal);
    document.getElementById('btn-cancel-intervention')?.addEventListener('click', closeInterventionModal);
    document.getElementById('btn-save-intervention')?.addEventListener('click', saveIntervention);
    document.getElementById('btn-delete-intervention')?.addEventListener('click', deleteIntervention);
    document.getElementById('btn-add-intervention')?.addEventListener('click', addNewIntervention);
    document.getElementById('btn-more-interventions')?.addEventListener('click', toggleMoreInterventions);

    // 快速添加预设干预
    document.getElementById('btn-add-quick-intervention')?.addEventListener('click', toggleQuickAddForm);
    document.getElementById('btn-quick-save')?.addEventListener('click', saveQuickIntervention);
    document.getElementById('btn-quick-cancel')?.addEventListener('click', closeQuickAddForm);

    // 认证相关
    initAuthListeners();
}

// ============================================================
// 认证 UI 事件
// ============================================================

function initAuthListeners() {
    // Tab 切换
    document.querySelectorAll('.auth-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            const target = tab.dataset.tab;
            document.getElementById('auth-form-login').classList.toggle('hidden', target !== 'login');
            document.getElementById('auth-form-register').classList.toggle('hidden', target !== 'register');
            document.getElementById('login-error').classList.add('hidden');
            document.getElementById('register-error').classList.add('hidden');
        });
    });

    // 登录按钮
    document.getElementById('btn-login').addEventListener('click', async () => {
        const username = document.getElementById('login-username').value.trim();
        const password = document.getElementById('login-password').value;
        const errorEl = document.getElementById('login-error');
        errorEl.classList.add('hidden');

        if (!username || !password) {
            errorEl.textContent = '请输入用户名和密码';
            errorEl.classList.remove('hidden');
            return;
        }

        try {
            await doLogin(username, password);
            location.reload();  // 刷新以初始化应用
        } catch (e) {
            errorEl.textContent = e.message;
            errorEl.classList.remove('hidden');
        }
    });

    // 注册按钮
    document.getElementById('btn-register').addEventListener('click', async () => {
        const username = document.getElementById('register-username').value.trim();
        const password = document.getElementById('register-password').value;
        const password2 = document.getElementById('register-password2').value;
        const displayName = document.getElementById('register-displayname').value.trim();
        const errorEl = document.getElementById('register-error');
        errorEl.classList.add('hidden');

        if (!username || username.length < 2) {
            errorEl.textContent = '用户名至少2个字符';
            errorEl.classList.remove('hidden');
            return;
        }
        if (!password || password.length < 6) {
            errorEl.textContent = '密码至少6位';
            errorEl.classList.remove('hidden');
            return;
        }
        if (password !== password2) {
            errorEl.textContent = '两次密码不一致';
            errorEl.classList.remove('hidden');
            return;
        }

        try {
            await doRegister(username, password, displayName || undefined);
            location.reload();
        } catch (e) {
            errorEl.textContent = e.message;
            errorEl.classList.remove('hidden');
        }
    });

    // 退出按钮
    document.getElementById('btn-logout').addEventListener('click', () => {
        if (confirm('确定要退出登录吗？')) {
            doLogout();
        }
    });

    // Enter 键快捷登录
    document.getElementById('login-password').addEventListener('keydown', e => {
        if (e.key === 'Enter') document.getElementById('btn-login').click();
    });
    document.getElementById('register-password2').addEventListener('keydown', e => {
        if (e.key === 'Enter') document.getElementById('btn-register').click();
    });
}

// ============================================================
// API 调用
// ============================================================

async function apiCall(endpoint, method = 'GET', data = null, retries = 3, signal = null) {
    const options = {
        method,
        headers: { 'Content-Type': 'application/json' },
    };

    // 添加认证Token
    if (auth.token) {
        options.headers['Authorization'] = `Bearer ${auth.token}`;
    }

    if (data) {
        options.body = JSON.stringify(data);
    }

    let lastError = null;
    for (let attempt = 0; attempt <= retries; attempt++) {
        try {
            // 每次 attempt 都检查是否已 abort
            if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
            const response = await fetch(`${API_BASE}${endpoint}`, { ...options, signal });

            // 获取响应文本
            const responseText = await response.text();

            // 401 未授权：清除token，弹出登录
            if (response.status === 401) {
                auth.token = null;
                auth.user = null;
                localStorage.removeItem('auth_token');
                showAuthOverlay();
                throw new Error('登录已过期，请重新登录');
            }

            if (!response.ok) {
                // 尝试解析错误信息
                try {
                    const errorData = JSON.parse(responseText);
                    throw new Error(errorData.detail || '请求失败');
                } catch (e) {
                    throw new Error(responseText || '请求失败');
                }
            }

            // 解析成功响应
            return JSON.parse(responseText);
        } catch (error) {
            // AbortError 直接向上抛出，不重试
            if (error.name === 'AbortError') throw error;
            
            lastError = error;
            const isNetworkError = error.message === 'Failed to fetch'
                || error.name === 'TypeError'
                || error.message.includes('NetworkError')
                || error.message.includes('network');

            if (isNetworkError && attempt < retries) {
                const delay = 1000 * Math.pow(2, attempt); // 1s, 2s, 4s
                console.warn(`网络波动，${delay/1000}秒后第${attempt + 2}次重试...`, endpoint);
                addSystemMessage(`⏳ 网络波动，${delay/1000}秒后自动重试...`);
                await new Promise(r => setTimeout(r, delay));
                continue;
            }
            console.error('API 调用失败:', error);
            throw error;
        }
    }
    throw lastError;
}

// ============================================================
// 模拟控制
// ============================================================

async function startSimulation() {
    try {
        // 获取选中的情境ID
        const scenarioSelect = document.getElementById('scenario-select');
        const scenarioId = scenarioSelect.value;

        // 获取选中的参与角色
        const selectedIds = getSelectedParticipantIds();
        if (selectedIds.length < 4) {
            alert('请至少选择4个参与角色');
            return;
        }

        // 构建参与角色数据
        const selectedParticipants = selectedIds.map(id => {
            const p = state.participants.find(pp => pp.id === id);
            return p ? { id: p.id, name: p.name, role: p.role, roleName: p.roleName || p.role, personality: p.personality || {} } : null;
        }).filter(Boolean);

        // 创建新会话,传递情境ID、LLM开关和参与角色
        const useLlmCheckbox = document.getElementById('use-llm');
        const useLlm = useLlmCheckbox ? useLlmCheckbox.checked : false;

        const result = await apiCall('/api/session', 'POST', {
            level: state.level,
            scenario_id: scenarioId || null,
            use_llm: useLlm,
            participant_ids: selectedIds,
            participants_data: selectedParticipants
        });

        state.sessionId = result.session_id;
        state.isRunning = true;
        state.isPaused = false;
        state.maxRounds = getMaxRounds(state.level);
        state.messages = [];
        state.interventions = [];
        // R15：新会话重置指导门槛
        state.guidanceEnabled = false;
        state.lastCoaching = null;
        // 清空上一会话残留的「干预已发送」状态条
        resetInterventionSendState();

        // 如果有情境,保存到当前会话
        if (result.scenario) {
            state.currentScenario = result.scenario;
        }

        // 更新按钮状态 - 运行中
        updateButtonStates('running');

        document.getElementById('session-badge').textContent = '运行中';
        document.getElementById('session-badge').className = 'badge running';
        document.getElementById('empty-state').classList.add('hidden');
        document.getElementById('messages-list').innerHTML = '';
        document.getElementById('intervention-history').querySelector('.history-list').innerHTML = '';

        // 禁用情境编辑(模拟进行中)
        scenarioSelect.disabled = true;
        document.getElementById('scenario-title').disabled = true;
        document.getElementById('scenario-desc').disabled = true;

        // 启用输入
        document.getElementById('intervention-input').disabled = false;
        document.getElementById('btn-intervene').disabled = false;

        // 更新状态
        updateStatus(result.status);

        // 添加系统消息
        if (state.currentScenario) {
            addSystemMessage(`📋 情境:${state.currentScenario.title}`);
        } else {
            addSystemMessage(`模拟开始 - ${state.level}级霸凌场景`);
        }

        // 自动开始第一轮
        setTimeout(() => runNextRound(), 1000);

    } catch (error) {
        console.error('启动失败:', error);
        alert('启动失败: ' + error.message);
    }
}

async function stopSimulation() {
    // 手动停止模拟
    if (!state.sessionId) return;
    // 防重复点击：停止流程中或已结束后再次触发，避免重复 finalizeStop 造成重复系统消息/双弹窗
    if (state.isStopping || !state.isRunning) return;

    // 首先设置标志,防止新的 runNextRound 开始
    state.isRunning = false;
    state.isStopping = true;
    state.isPaused = false;
    state.reportGenerating = true;

    // 立即更新按钮状态，不等 finalizeStop
    updateButtonStates('stopped');
    document.getElementById('session-badge').textContent = '正在停止...';
    document.getElementById('session-badge').className = 'badge warning';

    // 中止正在进行中的 API 调用
    if (state.abortController) {
        state.abortController.abort();
        state.abortController = null;
    }

    // 停止计时器

    addSystemMessage('⏹️ 正在停止...');

    // 更新 badge
    document.getElementById('session-badge').textContent = '正在停止...';
    document.getElementById('session-badge').className = 'badge warning';

    // 如果当前没有正在执行的 runNextRound（例如正在等待计时），直接调用 finalizeStop
    // 否则，runNextRound 会在 abort 后自动调用 finalizeStop
    if (!state.roundInProgress) {
        // 等待报告拉取与弹窗展示完成，便于收敛「查看报告」按顺序处理，避免双弹窗
        await finalizeStop(false, '', '', true);
    }
}

// 最终化停止过程
// userStopped：仅真实用户停止（停止按钮/收敛查看报告）为 true；错误/异常路径不得触发主动终结弹窗
async function finalizeStop(autoEnded = false, autoEndReason = '', resultType = '', userStopped = false) {
    console.log('finalizeStop called', { autoEnded, autoEndReason, resultType, userStopped });

    // 确保不会运行
    state.isRunning = false;
    state.isStopping = false;

    addSystemMessage('🛑 实训结束');

    // 禁用输入
    document.getElementById('intervention-input').disabled = true;
    document.getElementById('btn-intervene').disabled = true;

    // 更新 badge
    document.getElementById('session-badge').textContent = '已结束';
    document.getElementById('session-badge').className = 'badge';

    // 获取并保存报告数据
    try {
        console.log('Getting report...');
        state.lastReport = await apiCall(`/api/session/${state.sessionId}/report`);
        console.log('Report received, updating buttons');

        // 保存到历史记录
        saveToHistory(state.lastReport);

    } catch (error) {
        console.error('获取报告失败:', error);
    } finally {
        // 报告生成完毕，恢复按钮状态
        state.reportGenerating = false;
        updateButtonStates('stopped');
        updateDownloadButtons(false);
    }

    // 结束弹窗（在报告数据获取完成后显示）
    const iconEl = document.getElementById('autoend-icon');
    const titleEl = document.getElementById('autoend-title');
    const subtitleEl = document.getElementById('autoend-subtitle');
    const reasonEl = document.getElementById('autoend-reason');

    // 结束弹窗展示前收起收敛提示，避免收敛弹窗与结束弹窗叠加
    const convergenceModalEl = document.getElementById('convergence-modal');
    if (convergenceModalEl) convergenceModalEl.style.display = 'none';

    if (autoEnded && autoEndReason) {
        // 系统自动终止：按结果类型显示不同文案
        if (resultType === 'success') {
            iconEl.textContent = '✅';
            titleEl.textContent = '🎉 调解成功';
            subtitleEl.textContent = '恭喜！您有效制止了欺凌行为，保护了受害者';
            reasonEl.style.borderLeftColor = '#4caf50';
            // 仅前端方案：后端未提供成功细分字段，只能按 reason 文案区分变体。
            // 含「调解成功」= 干净成功（纯恭喜）；否则保留事实提醒并降级为「温馨提示」。
            if (autoEndReason.includes('调解成功')) {
                reasonEl.textContent = '🎉 恭喜您有效制止了欺凌行为，保护了受害者！';
            } else {
                reasonEl.textContent =
                    '🎉 恭喜您有效制止了欺凌行为，保护了受害者！\n温馨提示：' + autoEndReason;
            }
        } else if (resultType === 'failure') {
            iconEl.textContent = '💪';
            titleEl.textContent = '💪 调解未成功';
            subtitleEl.textContent = '别灰心，每次尝试都是宝贵的经验';
            reasonEl.style.borderLeftColor = '#f44336';
            reasonEl.textContent =
                '建议复盘干预时机与方式，调整策略后再试一次！\n事实原因：' + autoEndReason;
        } else if (resultType === 'timeout') {
            iconEl.textContent = '⏰';
            titleEl.textContent = '⏰ 本轮模拟结束';
            subtitleEl.textContent = '对话轮次已达上限';
            reasonEl.style.borderLeftColor = '#ff9800';
            reasonEl.textContent = '报告将基于已有数据生成分析，欢迎调整策略再试一次！';
        } else {
            // 未知类型：保持原有中性文案
            iconEl.textContent = '🏁';
            titleEl.textContent = '📋 模拟结束';
            subtitleEl.textContent = '模拟已达成终止条件';
            reasonEl.style.borderLeftColor = 'var(--primary)';
            reasonEl.textContent = autoEndReason;
        }

        document.getElementById('autoend-modal').classList.add('active');
    } else if (userStopped) {
        // 主动终结：仅真实用户停止（停止按钮 / 收敛「查看报告」）时触发
        iconEl.textContent = '📋';
        titleEl.textContent = '📋 实训已结束';
        subtitleEl.textContent = '您已主动结束本次模拟';
        reasonEl.style.borderLeftColor = 'var(--primary)';
        reasonEl.textContent = '报告将基于已有对话与干预数据生成分析，供您复盘参考。';
        document.getElementById('autoend-modal').classList.add('active');
    }
}

// 保存到历史记录
function saveToHistory(report) {
    const summary = report.summary || {};
    const assessment = report.ability_assessment || {};

    // 获取干预详情 - API返回的是 interventions 不是 intervention_records
    const interventions = report.interventions || [];

    const historyEntry = {
        id: Date.now().toString(),
        date: new Date().toLocaleDateString('zh-CN'),
        datetime: new Date().toISOString(),
        level: state.level,
        scenario: state.currentScenario?.title || '未选择情境',

        // 霸凌情况
        bullying_intensity: summary.bullying_intensity || 0,
        victim_distress: summary.victim_distress || 0,
        hostile_messages: summary.hostile_messages || 0,
        total_messages: summary.total_messages || 0,

        // 干预情况
        intervention_count: summary.intervention_count || 0,
        intervention_effectiveness: summary.intervention_effectiveness || 0,
        scoreScale: 'percent100', // 评分口径标记（0-100 百分制）

        // 干预详情(用于详细记录展示)
        interventions: interventions.map(iv => ({
            content: iv.content || '',
            quality_score: iv.quality_score ?? null,
            victim_care_score: iv.victim_care_score || 0,
            feedback: iv.feedback || []
        })),

        // 能力评估
        discovery_level: assessment.discovery?.level || 'unknown',
        intervention_count_assessed: assessment.intervention?.count || 0,

        // 简短评价
        rating: summary.intervention_effectiveness >= 60 ? 'good' :
                summary.intervention_effectiveness >= 30 ? 'medium' : 'poor'
    };

    // 获取现有历史
    const history = getHistory();

    // 添加新记录(最新在前)
    history.unshift(historyEntry);

    // 只保留最近 50 条
    if (history.length > 50) {
        history.pop();
    }

    // 保存
    localStorage.setItem('bullying_sim_history', JSON.stringify(history));
    state.sessionHistory = history;

    // 更新历史显示(如果存在)
    updateHistoryDisplay();
}

// 历史记录评分口径归一化
//
// 背景：2026-10-26 百分制改造前，intervention_effectiveness / quality_score
// 存的是 0-1 口径；改造后存 0-100 口径。浏览器 localStorage 里两种口径混存，
// 直接按百分制渲染会让旧记录显示成 0/100 或 10000% 这类异常值。
//
// R11（方案 §14.4/§15.6）：迁移只依据【明确的版本边界】（改造完成时刻），
// 禁止再按「数值是否小于 1」猜测分数单位；缺少时间戳的旧条目不做猜测，
// 标记为 unknown 并按原值显示。
const SCORE_SCALE_CUTOVER = '2026-10-26T20:00:00'; // 百分制改造完成时刻

// R11：评分统一渲染（null = 待评估）
function fmtQualityScore(v) {
    if (v === null || v === undefined) return '待评估';
    const num = Number(v);
    if (!Number.isFinite(num)) return '待评估';
    return Math.round(num) + '/100';
}

function normalizeHistoryScores(history) {
    if (!Array.isArray(history)) return history;

    let changed = false;

    history.forEach(entry => {
        if (!entry || typeof entry !== 'object') return;

        // 已有明确标记：按标记处理
        if (entry.scoreScale === 'percent100' || entry.scoreScale === 'ratio' || entry.scoreScale === 'unknown') return;

        const dt = entry.datetime || '';

        if (dt && dt < SCORE_SCALE_CUTOVER) {
            // 旧口径（0-1）：按版本边界一次性迁移为 0-100
            if (typeof entry.intervention_effectiveness === 'number') {
                entry.intervention_effectiveness = Math.round(entry.intervention_effectiveness * 100);
            }
            if (Array.isArray(entry.interventions)) {
                entry.interventions.forEach(iv => {
                    if (iv && typeof iv.quality_score === 'number') {
                        iv.quality_score = Math.round(iv.quality_score * 100);
                    }
                });
            }
            entry.scoreScale = 'percent100';
            changed = true;
        } else if (!dt) {
            // 无版本信息：不猜测单位，标记 unknown
            entry.scoreScale = 'unknown';
            changed = true;
        } else {
            entry.scoreScale = 'percent100';
        }
    });

    // 回写，避免每次读取都重复判断
    if (changed) {
        try {
            localStorage.setItem('bullying_sim_history', JSON.stringify(history));
        } catch (e) { /* 存储失败不影响显示 */ }
    }

    return history;
}

// 获取历史记录
function getHistory() {
    try {
        const stored = localStorage.getItem('bullying_sim_history');
        const history = stored ? JSON.parse(stored) : [];
        return normalizeHistoryScores(Array.isArray(history) ? history : []);
    } catch (e) {
        return [];
    }
}

// 更新历史显示
function updateHistoryDisplay() {
    const historyList = document.getElementById('history-list');
    if (!historyList) return;

    const history = getHistory();

    if (history.length === 0) {
        historyList.innerHTML = '<p class="empty-hint">暂无历史记录</p>';
        return;
    }

    historyList.innerHTML = history.slice(0, 10).map((entry, i) => `
        <div class="history-item ${entry.rating}" data-id="${entry.id}">
            <div class="history-date">${entry.date}</div>
            <div class="history-summary">
                ${entry.scenario} | 干预${entry.intervention_count}次 |
                效果${Math.round(entry.intervention_effectiveness)}/100
            </div>
            <div class="history-rating">
                ${entry.rating === 'good' ? '✅' : entry.rating === 'medium' ? '⚠️' : '❌'}
            </div>
        </div>
    `).join('');
}

// ==================== 详细记录功能 ====================

// 打开详细记录模态框
function openDetailedHistory() {
    const content = document.getElementById('detail-history-content');
    const records = getHistory();

    if (records.length === 0) {
        content.innerHTML = '<p class="empty-hint" style="text-align: center; padding: 40px;">暂无实训记录</p>';
    } else {
        content.innerHTML = records.map((record, index) => `
            <div class="detail-record-item" id="record-${record.id}">
                <div class="detail-record-header" onclick="toggleRecordDetail('${record.id}')">
                    <div class="detail-record-info">
                        <span class="record-date">${record.date}</span>
                        <span class="record-scenario">${record.scenario}</span>
                        <span class="record-level">${getLevelName(record.level)}</span>
                    </div>
                    <div class="detail-record-stats">
                        <span class="stat-badge">干预 ${record.intervention_count} 次</span>
                        <span class="stat-badge ${record.rating === 'good' ? 'good' : record.rating === 'medium' ? 'medium' : 'poor'}">
                            效果 ${Math.round(record.intervention_effectiveness)}/100
                        </span>
                        <span class="rating-badge">${record.rating === 'good' ? '✅' : record.rating === 'medium' ? '⚠️' : '❌'}</span>
                        <button class="btn-icon report-btn" onclick="event.stopPropagation(); viewReportFromHistory('${record.id}')" title="查看报告">📊</button>
                        <button class="btn-icon delete-btn" onclick="event.stopPropagation(); deleteRecord('${record.id}')" title="删除此记录">🗑️</button>
                        <span class="toggle-icon" id="toggle-${record.id}">▼</span>
                    </div>
                </div>
                <div class="detail-record-body" id="body-${record.id}" style="display: none;">
                    ${renderRecordDetailBody(record)}
                </div>
            </div>
        `).join('');
    }

    document.getElementById('detail-history-modal').classList.add('active');
}

// 关闭详细记录模态框
function closeDetailedHistory() {
    document.getElementById('detail-history-modal').classList.remove('active');
}

// 切换记录详情展开/收起
function toggleRecordDetail(recordId) {
    const body = document.getElementById(`body-${recordId}`);
    const icon = document.getElementById(`toggle-${recordId}`);

    if (body.style.display === 'none') {
        body.style.display = 'block';
        icon.textContent = '▲';
    } else {
        body.style.display = 'none';
        icon.textContent = '▼';
    }
}

// 渲染记录详情内容
function renderRecordDetailBody(record) {
    let html = '<div class="detail-record-content">';

    // 基本信息
    html += '<div class="detail-section">';
    html += '<h4>📊 基本信息</h4>';
    html += '<table class="detail-table">';
    html += `<tr><td>总消息数</td><td>${record.total_messages || 0} 条</td></tr>`;
    html += `<tr><td>攻击性消息</td><td>${record.hostile_messages || 0} 条</td></tr>`;
    html += `<tr><td>霸凌强度</td><td>${Math.round((record.bullying_intensity || 0) * 100)}%</td></tr>`;
    html += `<tr><td>受害者压力</td><td>${Math.round((record.victim_distress || 0) * 100)}%</td></tr>`;
    html += `<tr><td>干预次数</td><td>${record.intervention_count || 0} 次</td></tr>`;
    html += `<tr><td>干预效果</td><td>${Math.round((record.intervention_effectiveness || 0))}/100</td></tr>`;
    html += '</table></div>';

    // 每次干预详情
    if (record.interventions && record.interventions.length > 0) {
        html += '<div class="detail-section">';
        html += '<h4>📝 干预详情</h4>';
        record.interventions.forEach((iv, idx) => {
            html += `<div class="intervention-detail-item">`;
            html += `<div class="iv-header">#${idx + 1} <span class="iv-quality">评分: ${fmtQualityScore(iv.quality_score)}</span></div>`;
            html += `<div class="iv-content">"${iv.content}"</div>`;
            if (iv.feedback && iv.feedback.length > 0) {
                html += '<div class="iv-feedback">';
                html += iv.feedback.filter(f => f.includes('✅') || f.includes('⚠️') || f.includes('🚨')).map(f => f).join('<br>');
                html += '</div>';
            }
            if (iv.victim_care_score > 0) {
                html += `<div class="iv-care">关怀得分: ${Math.round(iv.victim_care_score * 100)}%</div>`;
            }
            html += '</div>';
        });
        html += '</div>';
    } else {
        html += '<div class="detail-section">';
        html += '<h4>📝 干预详情</h4>';
        html += '<p class="empty-hint">无干预记录</p>';
        html += '</div>';
    }

    html += '</div>';
    return html;
}

// 从历史记录查看报告
function viewReportFromHistory(recordId) {
    const history = getHistory();
    const record = history.find(r => r.id === recordId);

    if (!record) {
        alert('未找到该记录');
        return;
    }

    // 从历史记录重建报告数据(与generate_report格式一致)
    const report = {
        success: true,
        summary: {
            total_messages: record.total_messages || 0,
            bullying_intensity: record.bullying_intensity,
            victim_distress: record.victim_distress,
            hostile_messages: record.hostile_messages || 0,
            intervention_count: record.intervention_count,
            intervention_effectiveness: record.intervention_effectiveness,
        },
        level: record.level,
        scenario: record.scenario,
        interventions: record.interventions || [],
        ability_assessment: {
            discovery: { level: record.discovery_level || 'unknown' },
            intervention: { count: record.intervention_count_assessed || 0 }
        }
    };

    // 关闭详细记录模态框
    closeDetailedHistory();

    // 显示报告
    displayReport(report);
    document.getElementById('report-modal').classList.add('active');
}

// 删除单条记录
function deleteRecord(recordId) {
    if (!confirm('确定要删除此条实训记录吗?')) return;

    const history = getHistory();
    const filtered = history.filter(r => r.id !== recordId);

    localStorage.setItem('bullying_sim_history', JSON.stringify(filtered));
    state.sessionHistory = filtered;

    // 重新渲染
    openDetailedHistory();
    updateHistoryDisplay();
}

// 获取霸凌等级名称
function getLevelName(level) {
    const levels = {1: '1级-轻微', 2: '2级-一般', 3: '3级-较重', 4: '4级-严重', 5: '5级-极重'};
    return levels[level] || `${level}级`;
}

// 查看报告
async function viewReport() {
    // 如果报告正在生成中，显示等待状态
    if (state.reportGenerating) {
        document.getElementById('report-content').innerHTML = `
            <div style="text-align:center; padding: 40px;">
                <div style="font-size: 2rem; margin-bottom: 16px;">⏳</div>
                <div style="font-size: 1.1rem; color: #666;">报告生成中，请稍候...</div>
            </div>`;
        document.getElementById('report-modal').classList.add('active');
        updateDownloadButtons(true);
        return;
    }

    // 如果 lastReport 为空但有 sessionId，尝试重新获取
    if (!state.lastReport && state.sessionId) {
        // 再次检查是否正在生成（可能在查看期间完成）
        if (state.reportGenerating) {
            document.getElementById('report-content').innerHTML = `
                <div style="text-align:center; padding: 40px;">
                    <div style="font-size: 2rem; margin-bottom: 16px;">⏳</div>
                    <div style="font-size: 1.1rem; color: #666;">报告生成中，请稍候...</div>
                </div>`;
            document.getElementById('report-modal').classList.add('active');
            updateDownloadButtons(true);
            return;
        }
        try {
            state.lastReport = await apiCall(`/api/session/${state.sessionId}/report`);
        } catch (e) {
            console.error('重新获取报告失败:', e);
            alert('无法加载报告: ' + (e.message || '请检查网络连接'));
            return;
        }
    }
    if (state.lastReport) {
        displayReport(state.lastReport);
        document.getElementById('report-modal').classList.add('active');
        updateDownloadButtons(false);
    } else {
        alert('暂无报告数据');
    }
}

// 下载文本报告
async function downloadTextReport() {
    if (!state.sessionId) return;

    try {
        const report = await apiCall(`/api/session/${state.sessionId}/report`);
        const text = formatReportAsText(report);

        const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `教师实训报告_${new Date().toLocaleDateString('zh-CN').replace(/\//g, '-')}.txt`;
        a.click();
        URL.revokeObjectURL(url);
    } catch (error) {
        console.error('下载失败:', error);
        alert('下载失败: ' + error.message);
    }
}

// 导出 PDF
async function exportPDF() {
    if (!state.sessionId) return;

    try {
        const report = await apiCall(`/api/session/${state.sessionId}/report`);
        const html = formatReportAsHTML(report);

        // 创建一个隐藏的 iframe 用于打印
        const iframe = document.createElement('iframe');
        iframe.style.position = 'fixed';
        iframe.style.right = '0';
        iframe.style.bottom = '0';
        iframe.style.width = '0';
        iframe.style.height = '0';
        iframe.style.border = '0';
        document.body.appendChild(iframe);

        const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
        iframeDoc.open();
        iframeDoc.write(html);
        iframeDoc.close();

        iframe.contentWindow.print();

        setTimeout(() => iframe.remove(), 1000);
    } catch (error) {
        console.error('导出PDF失败:', error);
        alert('导出PDF失败: ' + error.message);
    }
}

// 导出 Word
async function exportWord() {
    if (!state.sessionId) return;

    try {
        const report = await apiCall(`/api/session/${state.sessionId}/report`);
        const html = formatReportAsHTML(report);

        // 添加 Word 命名空间的 HTML
        const wordHtml = `
<!DOCTYPE html>
<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word">
<head>
<meta charset="utf-8">
<title>教师实训报告</title>
<style>
body { font-family: '微软雅黑', Arial, sans-serif; padding: 20px; }
h1 { color: #333; border-bottom: 2px solid #2196F3; padding-bottom: 10px; }
h3 { color: #666; margin-top: 20px; }
.metric { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #eee; }
.value { font-weight: bold; }
.value.good { color: #4CAF50; }
.value.warning { color: #FF9800; }
.value.danger { color: #F44336; }
</style>
</head>
<body>
${html}
</body>
</html>`;

        const blob = new Blob([wordHtml], { type: 'application/msword' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `教师实训报告_${new Date().toLocaleDateString('zh-CN').replace(/\//g, '-')}.doc`;
        a.click();
        URL.revokeObjectURL(url);
    } catch (error) {
        console.error('导出Word失败:', error);
        alert('导出Word失败: ' + error.message);
    }
}

// 格式化报告为纯文本
function formatReportAsText(report) {
    const summary = report.summary || {};
    const metrics = report.metrics || {};
    const assessment = report.ability_assessment || {};
    const obvq = report.obvq_analysis || {};
    const interventionAnalysis = report.intervention_analysis || {};
    const trends = report.trends || {};

    let obvqLines = '';
    if (obvq.dimension_analysis && obvq.dimension_analysis.length) {
        obvqLines = obvq.dimension_analysis.map((dim, i) =>
            `    ${dim.name}: ${dim.total}分`
        ).join('\n');
    }

    let suggestionLines = '';
    if (interventionAnalysis.suggestions && interventionAnalysis.suggestions.length) {
        suggestionLines = interventionAnalysis.suggestions.map((s, i) =>
            `    ${i + 1}. ${s}`
        ).join('\n');
    }

    let actionPlanLines = '';
    if (interventionAnalysis.action_plan && interventionAnalysis.action_plan.length) {
        actionPlanLines = interventionAnalysis.action_plan.map((a, i) =>
            `    ☐ ${i + 1}. ${a}`
        ).join('\n');
    }

    let interventionDetails = '';
    if (report.interventions && report.interventions.length > 0) {
        interventionDetails = report.interventions.map((iv, i) =>
            `  干预 ${i + 1}: ${iv.content || ''} (效果:${fmtQualityScore(iv.quality_score)})`
        ).join('\n');
    }

    // R20：关键词策略分类已移除（策略功能由量规证据说明）

    return `
================================================================================
                    教师话语霸凌实训评估报告
================================================================================

生成时间: ${new Date().toLocaleString('zh-CN')}
会话ID: ${report.session_id || 'N/A'}
模拟时长: ${report.session_duration || 'N/A'}
模拟轮数: ${report.total_rounds || 'N/A'}

【霸凌情况】
  总消息数: ${summary.total_messages || 0}
  攻击性事件: ${summary.hostile_messages || 0}次
  最终霸凌强度: ${Math.round((summary.bullying_intensity || 0) * 100)}%
  受害者压力: ${Math.round((summary.victim_distress || 0) * 100)}%

【教师干预】
  干预次数: ${summary.intervention_count || 0}
  教师干预质量评分: ${Math.round(summary.intervention_effectiveness || 0)}/100

【指标变化趋势】
  ${trends.summary || '数据不足'}
  霸凌强度: 起点 ${Math.round((trends.early_bullying || 0) * 100)}% → 终点 ${Math.round((trends.late_bullying || 0) * 100)}%（变化 ${(trends.bullying_change || 0) * 100 >= 0 ? '+' : ''}${((trends.bullying_change || 0) * 100).toFixed(1)} 个百分点）
  受害者压力: 起点 ${Math.round((trends.early_distress || 0) * 100)}% → 终点 ${Math.round((trends.late_distress || 0) * 100)}%（变化 ${(trends.distress_change || 0) * 100 >= 0 ? '+' : ''}${((trends.distress_change || 0) * 100).toFixed(1)} 个百分点）
${trends.obvq_trends && trends.obvq_trends.filter(t => obvqStart(t) > 0 || obvqEnd(t) > 0).length > 0 ? `
  六类欺凌类型变化（起点→终点）:
${trends.obvq_trends.filter(t => obvqStart(t) > 0 || obvqEnd(t) > 0).map(t => `    ${t.name}: ${obvqStart(t)}分 → ${obvqEnd(t)}分 (${t.direction === 'up' ? '↑ 上升' : t.direction === 'down' ? '↓ 下降' : '→ 稳定'})`).join(String.fromCharCode(10))}` : ''}

【本次模拟欺凌行为类型统计（项目分类，非 OBVQ-R 标准量表）】
  类型总分: ${obvq.total_score || 0}分
  计分方式: 每次识别到的欺凌事件按强度映射为 0–4 分并累加到所有命中类别；总分 = min(80, 六类累计分之和)。同一事件可重复计入多个类别。
  说明: 该统计不用于教师 D1–D7 能力评分。
${obvqLines ? `
  六维分析:
${obvqLines}
` : ''}
  综合建议: ${obvq.recommendation || '无'}

【干预效果分析与建议】
  ${interventionAnalysis.summary || '本次模拟未进行任何干预。'}
${suggestionLines ? `
  改进建议:
${suggestionLines}
` : ''}
${actionPlanLines ? `
  改进行动计划:
${actionPlanLines}
` : ''}

【能力评估】
  发现能力: ${assessment.discovery?.description || '未评估'}
  干预能力: ${assessment.intervention?.description || '未评估'}
  教师干预质量评分: ${assessment.intervention?.rubric_score != null ? assessment.intervention.rubric_score + '/100（' + (assessment.intervention.band || '') + '）' : '待评估'}
  受害者保护（D2）: ${victimProtectionOf(assessment)?.description || '未评估'}

【干预详情】
${interventionDetails || '  无干预记录'}

================================================================================
                        报告结束
================================================================================
`;
}

// 格式化报告为 HTML
function formatReportAsHTML(report) {
    const summary = report.summary || {};
    const assessment = report.ability_assessment || {};
    const obvq = report.obvq_analysis || {};
    const interventionAnalysis = report.intervention_analysis || {};
    const trends = report.trends || {};

    let obvqDimsHtml = '';
    if (obvq.dimension_analysis && obvq.dimension_analysis.length) {
        obvqDimsHtml = obvq.dimension_analysis.map(dim =>
            `<div class="metric"><span>${dim.name}</span><span class="value">${dim.total}分</span></div>`
        ).join('');
    }

    let suggestionHtml = '';
    if (interventionAnalysis.suggestions && interventionAnalysis.suggestions.length) {
        suggestionHtml = interventionAnalysis.suggestions.map(s => `<li>${s}</li>`).join('');
    }

    let actionPlanHtml = '';
    if (interventionAnalysis.action_plan && interventionAnalysis.action_plan.length) {
        actionPlanHtml = '<h3>📋 改进行动计划</h3><ul>' +
            interventionAnalysis.action_plan.map(a => `<li>☐ ${a}</li>`).join('') + '</ul>';
    }

    // R20：关键词策略分类已移除（策略功能由量规证据说明）

    return `
<h1>📊 教师话语霸凌实训评估报告</h1>
<p><strong>生成时间:</strong> ${new Date().toLocaleString('zh-CN')}</p>

<h3>📌 霸凌情况</h3>
<div class="metric"><span>总消息数</span><span class="value">${summary.total_messages || 0}</span></div>
<div class="metric"><span>攻击性事件</span><span class="value danger">${summary.hostile_messages || 0}次</span></div>
<div class="metric"><span>最终霸凌强度</span><span class="value ${(summary.bullying_intensity || 0) > 0.5 ? 'danger' : ''}">${Math.round((summary.bullying_intensity || 0) * 100)}%</span></div>
<div class="metric"><span>受害者压力</span><span class="value ${(summary.victim_distress || 0) > 0.4 ? 'warning' : ''}">${Math.round((summary.victim_distress || 0) * 100)}%</span></div>

<h3>👨‍🏫 教师干预</h3>
<div class="metric"><span>干预次数</span><span class="value">${summary.intervention_count || 0}</span></div>
<div class="metric"><span>教师干预质量评分</span><span class="value ${(summary.intervention_effectiveness || 0) >= 50 ? 'good' : 'warning'}">${Math.round(summary.intervention_effectiveness || 0)}/100</span></div>

<h3>📈 指标变化趋势</h3>
<p>${trends.summary || '数据不足'}</p>
<p><strong>霸凌强度:</strong> ${Math.round((trends.early_bullying || 0) * 100)}% → ${Math.round((trends.late_bullying || 0) * 100)}%</p>
<p><strong>受害者压力:</strong> ${Math.round((trends.early_distress || 0) * 100)}% → ${Math.round((trends.late_distress || 0) * 100)}%</p>
${trends.obvq_trends && trends.obvq_trends.filter(t => obvqStart(t) > 0 || obvqEnd(t) > 0).length > 0 ? `
<p><strong>六类欺凌类型变化（起点→终点）:</strong></p>
<table style="width:100%;border-collapse:collapse;font-size:0.8125rem;">
<tr style="background:#f0f0f0;"><th style="padding:4px 8px;text-align:left;">维度</th><th style="padding:4px 8px;text-align:center;">起点</th><th style="padding:4px 8px;text-align:center;">终点</th><th style="padding:4px 8px;text-align:center;">趋势</th></tr>
${trends.obvq_trends.filter(t => obvqStart(t) > 0 || obvqEnd(t) > 0).map(t => `<tr style="border-bottom:1px solid #eee;"><td style="padding:3px 8px;">${t.name}</td><td style="padding:3px 8px;text-align:center;">${obvqStart(t)}分</td><td style=\"padding:3px 8px;text-align:center;\">${obvqEnd(t)}分</td><td style="padding:3px 8px;text-align:center;color:${t.direction === 'up' ? '#f44336' : t.direction === 'down' ? '#4CAF50' : '#999'};">${t.direction === 'up' ? '↑' : t.direction === 'down' ? '↓' : '→'}</td></tr>`).join('')}
</table>` : ''}

<h3>📊 本次模拟欺凌行为类型统计（项目分类）</h3>
<div class="metric"><span>类型总分（项目分类）</span><span class="value">${obvq.total_score || 0}分</span></div><p style="font-size:0.75rem;color:#888;margin:4px 0;">计分方式：每次识别到的欺凌事件按强度映射为 0–4 分并累加到所有命中类别；总分 = min(80, 六类累计分之和)。同一事件可重复计入多个类别，六类分项之和可能大于总分。该统计用于刻画欺凌行为类型，不用于教师 D1–D7 能力评分。</p>
${obvqDimsHtml}
<p><strong>综合建议: </strong>${obvq.recommendation || ''}</p>

<h3>🔍 干预效果分析与建议</h3>
<p>${interventionAnalysis.summary || '本次模拟未进行任何干预。'}</p>
${suggestionHtml ? `<ol>${suggestionHtml}</ol>` : ''}
${actionPlanHtml}

<h3>🎯 能力评估</h3>
<div class="metric"><span>发现能力</span><span class="value ${assessment.discovery?.level === 'excellent' ? 'good' : assessment.discovery?.level === 'good' ? 'good' : 'warning'}">${assessment.discovery?.description || '未评估'}</span></div>
<div class="metric"><span>干预能力</span><span class="value ${assessment.intervention?.level === 'excellent' ? 'good' : assessment.intervention?.level === 'good' ? 'good' : 'warning'}">${assessment.intervention?.description || ''}</span></div>
<div class="metric"><span>受害者保护（D2）</span><span class="value ${(victimProtectionOf(assessment)?.level === 'excellent' ? 'good' : victimProtectionOf(assessment)?.level === 'good' ? 'good' : 'warning')}">${victimProtectionOf(assessment)?.description || '未评估'}</span></div>

<h3>📋 干预详情</h3>
${(report.interventions && report.interventions.length > 0) ? report.interventions.map(iv => `
<div class="metric"><span>干预内容</span><span class="value ${(iv.quality_score || 0) >= 50 ? 'good' : 'warning'}">${fmtQualityScore(iv.quality_score)}</span></div>
<p style="font-size:0.8125rem;color:#666;margin:4px 0;">"${iv.content || ''}"</p>
`).join('') : '<p>无干预记录</p>'}
`;
}function togglePause() {
    if (!state.sessionId || !state.isRunning) return;

    state.isPaused = !state.isPaused;

    const pauseBtn = document.getElementById('btn-pause');

    if (state.isPaused) {
        // 暂停
        pauseBtn.innerHTML = '▶️ 继续模拟';
        pauseBtn.classList.remove('btn-secondary');
        pauseBtn.classList.add('btn-warning');
        document.getElementById('session-badge').textContent = '已暂停';
        document.getElementById('session-badge').className = 'badge warning';
        addSystemMessage('⏸️ 模拟已暂停');
    } else {
        // 继续
        pauseBtn.innerHTML = '⏸️ 暂停模拟';
        pauseBtn.classList.remove('btn-warning');
        pauseBtn.classList.add('btn-secondary');
        document.getElementById('session-badge').textContent = '运行中';
        document.getElementById('session-badge').className = 'badge running';
        addSystemMessage('▶️ 模拟继续');

        // 继续下一轮
        setTimeout(() => runNextRound(), 1000);
    }
}

async function runNextRound() {
    if (!state.sessionId) return;

    // 如果正在停止或已停止,跳过
    if (state.isStopping || !state.isRunning) {
        if (state.isStopping) {
            addSystemMessage('⏹️ 停止中...');
        }
        return;
    }

    if (!state.isRunning) return;

    // 如果暂停状态,不继续
    if (state.isPaused) {
        addSystemMessage('⏸️ 模拟已暂停');
        return;
    }

    // 标记轮次正在执行（用于 stopSimulation 判断是否需要等 abort 完成）
    state.roundInProgress = true;
    state.abortController = new AbortController();

    state.currentRound++;

    // 添加轮次分隔消息
    addSystemMessage(`--- 第 ${state.currentRound} 轮对话 ---`);

    // 高亮参与者
    highlightParticipants('all');

    try {
        // 显示加载动画和生成中状态
        showLoading();
        showGeneratingStatus();

        const result = await apiCall(`/api/session/${state.sessionId}/run`, 'POST', null, 0, state.abortController.signal);

        // 隐藏加载动画
        hideLoading();

        // 显示新消息 - 使用正确的字段 (API返回 result.messages,而非 result.status.messages)
        const newMessages = result.messages || result.status?.messages || [];
        if (newMessages.length > 0) {
            for (const msg of newMessages) {
                // 每处理一条前检查是否已停止
                if (state.isStopping) break;
                await addMessage(msg);
                await delay(800); // 模拟打字延迟
            }
        }

        // 更新状态
        updateStatus(result.status);

        // 重新加载推荐干预(传入当前状态)
        setTimeout(() => loadRecommendedInterventions(), 500);

        // 收敛检测弹窗（仅提示，不强制结束）
        if (result.convergence) {
            showConvergencePopup(result.convergence);
        }

        // 如果模拟完成或正在停止
        console.log('API returned, checking stop condition:', { done: result.done, isStopping: state.isStopping, autoEnded: result.auto_ended, convergence: result.convergence, round: result.round, intensity: result.metrics?.bullying_intensity });
        if (result.done || state.isStopping) {
            console.log('Calling finalizeStop from runNextRound');
            state.roundInProgress = false;
            // 仅用户停止且非服务端自动结束时，才展示「实训已结束」主动终结弹窗
            const userStopped = !result.auto_ended && state.isStopping;
            await finalizeStop(result.auto_ended, result.auto_end_reason || '', result.result_type || '', userStopped);
            console.log('finalizeStop returned, returning');
            return;
        }

        state.roundInProgress = false;
        state.abortController = null;
        // 显示干预确认按钮（替换原来的5秒倒计时）
        showInterventionConfirm();

    } catch (error) {
        state.roundInProgress = false;
        state.abortController = null;
        
        // 用户主动停止导致的 abort
        if (error.name === 'AbortError' || error instanceof DOMException && error.code === DOMException.ABORT_ERR) {
            console.log('runNextRound aborted by user');
            hideLoading();
            addSystemMessage('⏹️ 已停止');
            // AbortError 只由用户停止（stopSimulation）主动 abort 触发 → 主动终结
            await finalizeStop(false, '', '', true);
            return;
        }
        
        console.error('运行失败:', error);
        addSystemMessage('运行出错,请检查');
        hideLoading();
    }
}

// ============================================================
// 干预确认流程（替代旧版5秒倒计时）
// ============================================================

function setInterventionStatus(html) {
    const el = document.getElementById('intervention-status');
    if (el) el.innerHTML = html;
}

function showGeneratingStatus() {
    setInterventionStatus('<p class="generating-text">⏳ 对话生成中，请等待...</p>');
    document.getElementById('intervention-confirm-area').classList.add('hidden');
    document.querySelector('.intervention-input-area').classList.add('hidden');
}

function showInterventionConfirm() {
    setInterventionStatus('<p class="confirm-text">🤔 请决定是否需要进行干预</p>');
    document.getElementById('intervention-confirm-area').classList.remove('hidden');
    document.querySelector('.intervention-input-area').classList.add('hidden');
    document.getElementById('intervention-input').value = '';
}

function goBackToInterventionConfirm() {
    // 用户反悔，从输入区返回确认区，不跳过
    showInterventionConfirm();
}

async function onInterventionDecision(need) {
    document.getElementById('intervention-confirm-area').classList.add('hidden');
    
    if (need) {
        // 用户需要干预 → 显示输入框，无时间限制，阻塞等待发送
        setInterventionStatus('<p class="waiting-text">📝 等待干预输入中</p>');
        document.querySelector('.intervention-input-area').classList.remove('hidden');
        // R15：仅当连续多轮低分后才展示情境化提示（最小帮助，不给答案）
        renderWritingChecklist(
            state.guidanceEnabled && state.lastCoaching ? state.lastCoaching.context_hints : null);
        document.getElementById('intervention-input').focus();
    } else {
        // 用户不需要干预 → 记录跳过，直接下一轮
        setInterventionStatus('<p class="skip-text">⏭️ 本轮跳过干预</p>');
        await recordSkipIntervention();
        runNextRound();
    }
}

async function recordSkipIntervention() {
    if (!state.sessionId) return;
    try {
        await apiCall(`/api/session/${state.sessionId}/skip-intervention`, 'POST', {
            round: state.currentRound
        });
    } catch (e) {
        console.warn('记录跳过干预失败:', e);
    }
}

// ============================================================
// 干预发送状态机（R12）
// 目的：用户能明确区分「发送中 / 已发送 / 发送失败」，
// 避免因误以为未发送而重复操作。
// ============================================================
const interventionSend = {
    status: 'idle',       // idle | sending | sent | failed
    token: null,          // 幂等令牌：内容不变时复用，重试不会产生第二次干预
    content: '',
    slowTimer: null,
    startedAt: 0,
};

function newClientToken() {
    return 'iv_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
}

function truncateText(text, max) {
    const s = String(text || '');
    return s.length > max ? s.slice(0, max) + '…' : s;
}

// 发送按钮状态（发送中禁用并改文案）
function setSendButtonState(status) {
    const btn = document.getElementById('btn-intervene');
    if (!btn) return;
    if (status === 'sending') {
        btn.disabled = true;
        btn.textContent = '⏳ 发送中…';
        btn.classList.add('is-sending');
    } else {
        btn.disabled = false;
        btn.textContent = '📤 发送干预';
        btn.classList.remove('is-sending');
    }
}

// 常驻发送状态条：不会被下一轮「对话生成中」覆盖
function renderSendStatus(info) {
    const box = document.getElementById('intervention-send-status');
    if (!box) return;
    const s = interventionSend;

    if (s.status === 'sending') {
        const elapsed = Math.round((Date.now() - s.startedAt) / 1000);
        box.className = 'intervention-send-status sending';
        box.innerHTML = `
            <div class="send-state-line"><span class="send-spinner"></span>
                <span>⏳ 正在发送干预…（${elapsed}s）内容已提交，请勿重复点击</span></div>`;
        return;
    }

    if (s.status === 'sent') {
        const info2 = info || {};
        const q = info2.quality_score;
        const qText = (q === null || q === undefined) ? '评分待评估' : `评分 ${Math.round(q)}/100`;
        const dup = !!info2.duplicate;
        box.className = 'intervention-send-status ' + (dup ? 'duplicate' : 'sent');
        box.innerHTML = `
            <div class="send-state-line title">${dup ? 'ℹ️ 该干预此前已发送，本次未重复发送' : '✅ 干预已发送'}</div>
            <div class="send-state-meta">发送时间 ${escapeHtml(info2.sent_at || '')} · ${qText}${info2.round ? ' · 第 ' + info2.round + ' 轮' : ''}</div>
            <div class="send-state-content">${escapeHtml(truncateText(info2.content || '', 60))}</div>
            ${(info2.pending_note) ? `<div class="send-state-meta" style="color:#b71c1c;">⚠️ ${escapeHtml(info2.pending_note)}</div>` : ''}
            ${(info2.progress && ((info2.progress.streak > 0) || (info2.progress.unscored_streak > 0)))
                ? `<div class="send-state-meta">📌 ${escapeHtml(info2.progress.message || '')}</div>` : ''}`;
        return;
    }

    if (s.status === 'failed') {
        const msg = (info && info.message) ? info.message : '未知错误';
        box.className = 'intervention-send-status failed';
        box.innerHTML = `
            <div class="send-state-line title">❌ 发送失败：${escapeHtml(msg)}</div>
            <div class="send-state-meta">输入内容已保留，可直接点击「发送干预」重试；若长时间无响应，请先查看对话区确认是否已发送，避免重复干预。</div>`;
        return;
    }

    box.className = 'intervention-send-status idle';
    box.innerHTML = '';
}

// 新会话 / 回到开始面板时清空发送状态条与状态机，
// 避免残留上一会话的「已发送」干预话语；同时取消发送中的慢速计时器。
// 不改动按钮 disabled（由调用方 / updateButtonStates 决定）。
function resetInterventionSendState() {
    clearTimeout(interventionSend.slowTimer);
    interventionSend.slowTimer = null;
    interventionSend.status = 'idle';
    interventionSend.token = null;
    interventionSend.content = '';
    interventionSend.startedAt = 0;
    const btn = document.getElementById('btn-intervene');
    if (btn) {
        btn.classList.remove('is-sending');
        btn.textContent = '📤 发送干预';
    }
    renderSendStatus();
}

// ============================================================
// R21：报告渲染兼容辅助（历史报告字段回退）
// 旧报告用 early/mid/late 与 empathy；新报告用 baseline/final 与 victim_protection
// ============================================================
function obvqStart(t) {
    if (!t) return 0;
    return (t.baseline !== undefined && t.baseline !== null) ? t.baseline : (t.early || 0);
}
function obvqEnd(t) {
    if (!t) return 0;
    return (t.final !== undefined && t.final !== null) ? t.final : (t.late || 0);
}
function victimProtectionOf(assessment) {
    if (!assessment) return null;
    return assessment.victim_protection || assessment.empathy || null;
}

// ============================================================
// R21：报告渲染兼容辅助（历史报告字段回退）
// 旧报告用 early/mid/late 与 empathy；新报告用 baseline/final 与 victim_protection
// ============================================================
function obvqStart(t) {
    if (!t) return 0;
    return (t.baseline !== undefined && t.baseline !== null) ? t.baseline : (t.early || 0);
}
function obvqEnd(t) {
    if (!t) return 0;
    return (t.final !== undefined && t.final !== null) ? t.final : (t.late || 0);
}
function victimProtectionOf(assessment) {
    if (!assessment) return null;
    return assessment.victim_protection || assessment.empathy || null;
}

// ============================================================
// 实时指导与连续低分预警（R14/R15：连续多轮低分后才展示，只给策略方向）
// ============================================================

// 干预后的实时指导卡片（维度分 + 最需改进项 + 风险提醒 + 情境化策略提示）
// R15：不渲染任何可照抄话术（不给答案），只展示策略方向
function renderCoachingCard(coaching) {
    const panel = document.getElementById('coaching-panel');
    if (!panel) return;
    if (!coaching) {
        panel.classList.add('hidden');
        panel.innerHTML = '';
        return;
    }
    const dims = coaching.dimension_scores || {};
    let dimRows = '';
    Object.keys(dims).forEach(k => {
        const v = dims[k];
        const cls = (v === null || v === undefined) ? '' : (v < 40 ? 'low' : (v < 60 ? 'mid' : 'ok'));
        const txt = (v === null || v === undefined) ? 'N/O' : v;
        dimRows += `<tr><td>${k}</td><td class="dim-score ${cls}">${txt}</td></tr>`;
    });

    let html = '<div class="coaching-title">🧭 实时指导</div>';
    html += `<div class="coaching-item">${escapeHtml(coaching.headline || '')}</div>`;
    (coaching.tips || []).forEach(t => {
        html += `<div class="coaching-item">${escapeHtml(t)}</div>`;
    });
    if (dimRows) {
        html += `<table class="coaching-dim-table">${dimRows}</table>`;
    }
    // R15：情境化策略提示（最小限度帮助，不给答案）
    const hints = coaching.context_hints || [];
    if (hints.length) {
        html += '<div class="coaching-title" style="margin-top:8px;">📍 当前情境可考虑的策略</div>';
        hints.forEach(h => {
            html += `<div class="coaching-item"><strong>${escapeHtml(h.title || '')}</strong>：${escapeHtml(h.text || '')}</div>`;
        });
    }
    panel.innerHTML = html;
    panel.classList.remove('hidden');
    state.lastCoaching = coaching;
}

// ============================================================
// R18：指导呈现（对话流卡片 + 顶部浮层 + 面板徽标）
// —— 之前指导只渲染在右侧窄面板内，窄屏（≤768px）或用户只看对话区时完全不可见，
//    这是「提示卡面完全没生效」的直接原因。
// ============================================================
let guidanceShownCount = 0;

function updateGuidanceBadge() {
    const badge = document.getElementById('guidance-badge');
    if (!badge) return;
    badge.textContent = `指导 ×${guidanceShownCount}`;
    badge.classList.toggle('hidden', guidanceShownCount === 0);
}

// 指导卡插入对话流（用户最常看的位置）
function renderGuidanceInChat(coaching) {
    const list = document.getElementById('messages-list');
    if (!list || !coaching) return;
    guidanceShownCount += 1;
    const focus = coaching.focus || {};
    const hints = coaching.context_hints || [];
    const div = document.createElement('div');
    div.className = 'message guidance';
    div.innerHTML = `
        <div class="guidance-head">🧭 针对性指导（第 ${guidanceShownCount} 次）</div>
        <div class="guidance-sub">${escapeHtml(coaching.headline || '')}</div>
        ${focus && focus.name ? `<div class="guidance-line"><strong>最需改进：${escapeHtml(focus.name)}（${focus.score} 分）</strong></div>
            <div class="guidance-line">${escapeHtml(focus.advice || '')}</div>` : ''}
        ${hints.length ? `<div class="guidance-line"><strong>当前情境可考虑的策略</strong></div>` + hints.map(h =>
            `<div class="guidance-hint">· ${escapeHtml(h.title || '')}：${escapeHtml(h.text || '')}</div>`).join('') : ''}
        <div class="guidance-note">仅提示策略方向，具体话术请结合现场自行组织</div>
    `;
    list.appendChild(div);
    const container = document.getElementById('messages-container');
    if (container) container.scrollTop = container.scrollHeight;
    updateGuidanceBadge();
}

// 顶部浮层（任何视口宽度都可见，需手动关闭）
function showGuidanceToast(coaching, title) {
    if (!coaching) return;
    let toast = document.getElementById('guidance-toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'guidance-toast';
        toast.className = 'guidance-toast';
        document.body.appendChild(toast);
    }
    const focus = coaching.focus || {};
    const hints = coaching.context_hints || [];
    const strategies = coaching.strategies || [];
    const first = hints[0] || (strategies[0] ? { text: strategies[0] } : null);
    toast.innerHTML = `
        <div class="guidance-toast-title">${escapeHtml(title || '🧭 已给出针对性指导')}</div>
        <div class="guidance-toast-line">${escapeHtml(coaching.headline || '')}</div>
        ${focus && focus.name ? `<div class="guidance-toast-line">重点改进：${escapeHtml(focus.name)}（${focus.score} 分）</div>` : ''}
        ${first ? `<div class="guidance-toast-line">策略提示：${escapeHtml(first.text || '')}</div>` : ''}
        <button class="guidance-toast-close" id="guidance-toast-close">知道了</button>
    `;
    toast.classList.add('show');
    const btn = document.getElementById('guidance-toast-close');
    if (btn) btn.addEventListener('click', () => toast.classList.remove('show'));
}

// 连续多轮低于阈值 → 醒目的预警（含诊断与策略方向；不给答案）
function showLowScoreAlert(alert) {
    const box = document.getElementById('coaching-alert');
    if (!box) return;
    if (!alert) {
        box.classList.add('hidden');
        box.innerHTML = '';
        return;
    }
    let html = `<div class="alert-title">⚠️ ${escapeHtml(alert.headline || '连续低分预警')}</div>`;
    html += `<div>${escapeHtml(alert.diagnosis || '')}</div>`;
    if (alert.actions && alert.actions.length) {
        html += '<ul>' + alert.actions.map(a => `<li>${escapeHtml(a)}</li>`).join('') + '</ul>';
    }
    if (alert.strategies && alert.strategies.length) {
        html += '<div style="margin-top:6px;"><strong>可考虑的策略方向：</strong></div>';
        html += '<ul>' + alert.strategies.map(s => `<li>${escapeHtml(s)}</li>`).join('') + '</ul>';
    }
    box.innerHTML = html;
    box.classList.remove('hidden');
    // 同时进入对话区，避免被面板滚动忽略
    addSystemMessage(`⚠️ ${alert.headline || '连续低分预警'}｜${alert.diagnosis || ''}`);
}

// 干预输入前的情境化提示（R15：仅在连续多轮低分后展示，且只给策略方向）
function renderWritingChecklist(checklist) {
    const box = document.getElementById('writing-checklist');
    if (!box) return;
    // 门槛控制：未连续多轮低分时不展示任何提示
    if (!state.guidanceEnabled || !checklist || !checklist.length) {
        box.classList.add('hidden');
        box.innerHTML = '';
        return;
    }
    let html = '<div class="wc-title">✍️ 情境提示（仅提示策略方向，答案需要你自己组织）</div>';
    checklist.forEach(c => {
        html += `<div class="wc-item"><strong>${escapeHtml(c.title || '')}</strong>：${escapeHtml(c.text || '')}</div>`;
    });
    box.innerHTML = html;
    box.classList.remove('hidden');
}

async function sendIntervention() {
    if (!state.sessionId) return;

    const input = document.getElementById('intervention-input');
    const content = input.value.trim();

    // R28：占位符未替换提醒（未替换会导致指代不明、评价失真，并可能被判为「暴露受害者」风险）
    if (/【[^】]+】/.test(content)) {
        const go = confirm(
            '检测到干预内容中仍有未替换的占位符（如【受害者】）。\n\n' +
            '占位符未替换会让「指代对象」不明确，评价可能失真，也可能被判为「暴露受害者」风险。\n' +
            '（系统发送时会按当前学生姓名自动替换为真实姓名。）\n\n是否继续发送？');
        if (!go) return;
    }

    // R12：发送中忽略重复点击（避免重复干预）
    if (interventionSend.status === 'sending') {
        renderSendStatus();
        return;
    }

    if (!content) {
        setInterventionStatus('<p class="waiting-text">✏️ 请先输入干预内容再发送</p>');
        input.focus();
        return;
    }

    // 幂等令牌：内容不变时复用（重试不会产生第二次干预）
    if (interventionSend.content !== content || !interventionSend.token) {
        interventionSend.content = content;
        interventionSend.token = newClientToken();
    }

    // 立即进入「发送中」状态（可见 + 阻止重复提交）
    interventionSend.status = 'sending';
    interventionSend.startedAt = Date.now();
    setSendButtonState('sending');
    setInterventionStatus('<p class="generating-text">⏳ 正在发送干预，请稍候…</p>');
    renderSendStatus();

    // 超过 8 秒未返回 → 明确提示仍在处理，避免用户误以为未发送而重复点击
    clearTimeout(interventionSend.slowTimer);
    interventionSend.slowTimer = setTimeout(() => {
        if (interventionSend.status === 'sending') {
            setInterventionStatus('<p class="generating-text">⏳ 仍在处理（专家评分中），内容已提交，请勿重复发送</p>');
            renderSendStatus();
        }
    }, 8000);

    try {
        showLoading();
        const result = await apiCall(`/api/session/${state.sessionId}/intervene`, 'POST', {
            content: content,
            client_token: interventionSend.token,
        });
        hideLoading();
        clearTimeout(interventionSend.slowTimer);

        console.log('干预响应:', result);

        const ivData = result.intervention || {};
        const fbList = ivData.feedback || [];
        const pendingNoteRaw = fbList.find(f => String(f).includes('本轮评分待评估')) || '';
        const pendingNote = pendingNoteRaw ? String(pendingNoteRaw).replace(/^⚠️\s*/, '') : '';
        const sentInfo = {
            quality_score: ivData.quality_score,
            sent_at: result.sent_at || ivData.sent_at || '',
            content: result.content || content,
            round: result.message ? result.message.round : state.currentRound,
            duplicate: result.send_status === 'duplicate',
            // R17：评分不可用时明确告知原因（不再只显示笼统的「待评估」）
            pending_note: pendingNote,
            // R16：指导触发进度（未达门槛时说明「还差几轮」，避免用户以为功能失效）
            progress: result.guidance_progress || null,
        };

        // 发送成功（或服务端判定为重复发送）→ 明确成功状态
        interventionSend.status = 'sent';
        setSendButtonState('idle');

        // 清空输入并隐藏输入区
        input.value = '';
        document.querySelector('.intervention-input-area').classList.add('hidden');

        // 使用后端返回的消息数据(避免重复添加)
        const msgData = result.message || {
            id: Date.now().toString(),
            sender: '张老师',
            role: 'teacher',
            content: content,
            hostile_score: 0,
            emotions: [],
        };

        // 如果返回了评估数据,加入
        if (result.intervention?.quality_score !== undefined) {
            msgData.quality_score = result.intervention.quality_score;
        }
        // R12：消息上标记「已发送 + 时间」，用户可在对话区直接确认
        msgData.sent_status = 'sent';
        msgData.sent_at = sentInfo.sent_at;

        // 添加干预消息到对话区
        await addMessage(msgData);

        // 干预效果可视化（R11：待评估(null)单独提示，不再回落到默认 50 分）
        const quality = (msgData.quality_score === null || msgData.quality_score === undefined)
            ? null : msgData.quality_score;
        if (quality === null) {
            showInterventionEffect('medium', '本轮干预评分待评估');
        } else if (quality >= 60) {
            showInterventionEffect('good', '干预有效!角色行为可能改变');
        } else if (quality >= 30) {
            showInterventionEffect('medium', '干预一般,可尝试其他方法');
        } else {
            showInterventionEffect('poor', '干预效果不佳');
        }

        // 添加到历史
        if (result.intervention) {
            addToHistory(result.intervention);
        }

        // 更新状态 - 使用干预返回的指标值更新UI
        if (result.status) {
            updateStatus(result.status);
        } else if (result.intervention?.bullying_intensity !== undefined) {
            // 直接使用干预返回的指标值更新UI
            updateStatus({
                round: state.currentRound,
                bullying_intensity: result.intervention.bullying_intensity,
                victim_distress: result.intervention.victim_distress,
            });
        }

        // 常驻成功反馈（不随下一轮状态被覆盖）
        renderSendStatus(sentInfo);

        // R14/R15：实时指导（连续多轮低分后才展示）+ 连续低分预警
        state.guidanceEnabled = !!result.guidance_enabled;
        renderCoachingCard(result.coaching);
        if (result.coaching) {
            // R18：指导同时进入对话流与顶部浮层——任何视口宽度、任何注意力位置都能看到
            renderGuidanceInChat(result.coaching);
            showGuidanceToast(result.coaching);
        }
        if (result.low_score_alert) {
            showLowScoreAlert(result.low_score_alert);
            showGuidanceToast(result.low_score_alert,
                '⚠ ' + (result.low_score_alert.headline || '连续低分预警'));
        }
        setInterventionStatus(
            `<p class="sent-text">✅ 干预已发送（${escapeHtml(sentInfo.sent_at || '')}）` +
            `${sentInfo.duplicate ? '（此前已发送，未重复）' : ''}</p>`);
        setTimeout(() => {
            if (state.isRunning && !state.isPaused) {
                runNextRound();
            }
        }, 1500);

    } catch (error) {
        clearTimeout(interventionSend.slowTimer);
        hideLoading();
        console.error('干预失败:', error);
        // R12：失败态明确可见，并保留内容供重试
        interventionSend.status = 'failed';
        setSendButtonState('idle');
        renderSendStatus({ message: error.message || String(error) });
        setInterventionStatus('<p class="skip-text">❌ 发送失败，内容已保留，可点击「发送干预」重试</p>');
        // 恢复输入区（内容保留）
        document.querySelector('.intervention-input-area').classList.remove('hidden');
        input.focus();
    }
}

async function endSimulation() {
    if (!state.sessionId) return;

    state.isRunning = false;
    state.isPaused = false;

    // 添加系统消息
    addSystemMessage('会话已结束,正在生成报告...');

    // 禁用输入
    document.getElementById('intervention-input').disabled = true;
    document.getElementById('btn-intervene').disabled = true;

    // 隐藏运行中按钮组
    document.getElementById('sim-running-group').classList.add('hidden');
    document.getElementById('sim-control-group').classList.remove('hidden');

    // 显示报告
    await showReport();
}

function resetToStart() {
    state.sessionId = null;
    state.isRunning = false;
    state.isPaused = false;
    state.currentRound = 0;
    state.messages = [];
    state.interventions = [];
    // R15：指导门槛状态复位
    state.guidanceEnabled = false;
    state.lastCoaching = null;
    // 清空上一会话残留的「干预已发送」状态条
    resetInterventionSendState();
    const cpanel = document.getElementById('coaching-panel');
    if (cpanel) { cpanel.classList.add('hidden'); cpanel.innerHTML = ''; }
    const calert = document.getElementById('coaching-alert');
    if (calert) { calert.classList.add('hidden'); calert.innerHTML = ''; }
    const wcl = document.getElementById('writing-checklist');
    if (wcl) { wcl.classList.add('hidden'); wcl.innerHTML = ''; }

    // 重置 UI
    document.getElementById('start-panel').classList.remove('hidden');
    document.getElementById('session-badge').textContent = '等待开始';
    document.getElementById('session-badge').className = 'badge';
    document.getElementById('empty-state').classList.remove('hidden');
    document.getElementById('messages-list').innerHTML = '';
    document.getElementById('intervention-input').disabled = true;
    document.getElementById('btn-intervene').disabled = true;
    document.getElementById('intervention-input').value = '';

    // 重置按钮状态 - 初始状态
    updateButtonStates('initial');

    // 更新历史显示
    updateHistoryDisplay();

    // 重置情境区域
    document.getElementById('scenario-select').disabled = false;
    document.getElementById('scenario-select').value = '';
    document.getElementById('scenario-title').disabled = false;
    document.getElementById('scenario-title').value = '';
    document.getElementById('scenario-desc').disabled = false;
    document.getElementById('scenario-desc').value = '';
    document.getElementById('btn-save-scenario').classList.add('hidden');
    state.currentScenario = null;
    state.scenarioModified = false;
    updateScenarioTip();

    // 重置状态
    document.getElementById('status-round').textContent = '0';
    document.getElementById('progress-intensity').style.width = '0%';
    document.getElementById('value-intensity').textContent = '0%';
    document.getElementById('progress-distress').style.width = '0%';
    document.getElementById('value-distress').textContent = '0%';
    document.getElementById('alert-box').classList.add('hidden');
    document.getElementById('timer').classList.add('hidden');

    // 重置参与者高亮
    document.querySelectorAll('.participant').forEach(p => {
        p.classList.remove('active', 'speaking');
    });
}

// ============================================================
// UI 更新
// ============================================================

function updateLevelInfo(level) {
    const levels = {
        1: { name: '1级 - 轻微', desc: '偶尔的嘲笑、轻微言语冲突' },
        2: { name: '2级 - 一般', desc: '持续的语言嘲讽、轻微社交排斥' },
        3: { name: '3级 - 中等', desc: '明显的言语攻击、社交排斥' },
        4: { name: '4级 - 严重', desc: '持续的语言暴力、人身攻击' },
        5: { name: '5级 - 极其严重', desc: '极端的语言暴力、威胁恐吓' },
    };

    const info = levels[level];
    document.getElementById('level-info').innerHTML = `
        <p><strong>${info.name}</strong></p>
        <p>${info.desc}</p>
    `;
}

function updateStatus(status) {
    status = status || {};
    document.getElementById('status-round').textContent = status.round || state.currentRound;

    const intensity = status.bullying_intensity || 0;
    const distress = status.victim_distress || 0;
    const frequency = status.attack_frequency || 0;

    document.getElementById('progress-intensity').style.width = `${Math.round(intensity * 100)}%`;
    document.getElementById('value-intensity').textContent = `${Math.round(intensity * 100)}%`;

    document.getElementById('progress-distress').style.width = `${Math.round(distress * 100)}%`;
    document.getElementById('value-distress').textContent = `${Math.round(distress * 100)}%`;

    // 攻击频率：真实终止判定的硬门槛之一，必须可见（B<阈值不蕴含 F<阈值）
    const freqBar = document.getElementById('progress-frequency');
    const freqValue = document.getElementById('value-frequency');
    if (freqBar) freqBar.style.width = `${Math.round(frequency * 100)}%`;
    if (freqValue) freqValue.textContent = `${Math.round(frequency * 100)}%`;

    updateAutoEndDiagnostics(status);

    // 干警报报
    if (status.needs_intervention) {
        document.getElementById('alert-box').classList.remove('hidden');
    } else {
        document.getElementById('alert-box').classList.add('hidden');
    }

    // Step 4: OBVQ 风险仪表盘更新
    updateOBVQDashboard(status);
}

// 自动结束判定诊断：回显真实判定门槛与进度，避免「看起来满足却不结束」
function updateAutoEndDiagnostics(status) {
    if (!document.getElementById('auto-end-check')) return;

    const intensity = Number(status.bullying_intensity || 0);
    const frequency = Number(status.attack_frequency || 0);
    const thrIntensity = Number(status.bullying_intensity_max != null ? status.bullying_intensity_max : 0.20);
    const thrFrequency = Number(status.attack_frequency_max != null ? status.attack_frequency_max : 0.20);
    const required = Number(status.bullying_stopped_required || 3);
    const stopped = Number(status.bullying_stopped_rounds || 0);
    const remaining = status.remaining_to_stop != null
        ? Number(status.remaining_to_stop)
        : Math.max(0, required - stopped);

    const intensityOk = intensity < thrIntensity;
    const frequencyOk = frequency < thrFrequency;

    setAutoEndItem('check-intensity', `强度${intensityOk ? '✓' : '✗'}`, intensityOk);
    setAutoEndItem('check-frequency', `频率${frequencyOk ? '✓' : '✗'}`, frequencyOk);
    setAutoEndItem('check-streak', `连续 ${stopped}/${required}`, stopped >= required);
    setAutoEndItem('check-remaining',
        remaining > 0 ? `距结束还需 ${remaining} 轮` : '已达结束条件',
        remaining === 0);

    const gaps = [];
    if (!intensityOk) {
        gaps.push(`霸凌强度 ${Math.round(intensity * 100)}%（需 <${Math.round(thrIntensity * 100)}%）`);
    }
    if (!frequencyOk) {
        gaps.push(`攻击频率 ${Math.round(frequency * 100)}%（需 <${Math.round(thrFrequency * 100)}%）`);
    }
    if (intensityOk && frequencyOk && remaining > 0) {
        gaps.push(`连续达标进行中，还需 ${remaining} 轮`);
    }
    const gapEl = document.getElementById('check-gap');
    if (gapEl) {
        gapEl.textContent = gaps.length
            ? `卡点：${gaps.join('；')}`
            : '条件已满足，本会话将自动结束';
    }
}

function setAutoEndItem(id, text, ok) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.classList.remove('ok', 'bad');
    el.classList.add(ok ? 'ok' : 'bad');
}

function highlightParticipants(who) {
    document.querySelectorAll('.participant').forEach(p => {
        p.classList.remove('speaking');
    });
}

function highlightSpeaker(role) {
    const roleMap = {
        'bully': 'chenhao',
        'victim': 'siyuan',
        'bystander_a': 'linyutong',
        'bystander_b': 'zhaolei',
        'teacher': null,
    };

    const participantId = roleMap[role];
    if (participantId) {
        document.querySelectorAll('.participant').forEach(p => {
            p.classList.remove('speaking');
        });
        const el = document.getElementById(`part-${participantId}`);
        if (el) {
            el.classList.add('speaking');
            setTimeout(() => el.classList.remove('speaking'), 2000);
        }
    }
}

// 显示/隐藏加载动画
function showLoading() {
    const loading = document.getElementById('llm-loading');
    if (loading) {
        loading.classList.remove('hidden');
    }
}

function hideLoading() {
    const loading = document.getElementById('llm-loading');
    if (loading) {
        loading.classList.add('hidden');
    }
}

// 干预效果可视化
function showInterventionEffect(level, message) {
    const effectTypes = {
        'good': { icon: '✅', class: 'intervention-good', color: '#4CAF50' },
        'medium': { icon: '⚠️', class: 'intervention-medium', color: '#FF9800' },
        'poor': { icon: '❌', class: 'intervention-poor', color: '#F44336' }
    };

    const effect = effectTypes[level] || effectTypes['medium'];

    // 创建效果提示
    const effectDiv = document.createElement('div');
    effectDiv.className = `intervention-effect ${effect.class}`;
    effectDiv.innerHTML = `
        <span class="effect-icon">${effect.icon}</span>
        <span class="effect-message">${message}</span>
    `;
    effectDiv.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 12px 20px;
        background: ${effect.color};
        color: white;
        border-radius: 8px;
        font-size: 0.875rem;
        z-index: 10000;
        animation: slideIn 0.3s ease-out;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    `;

    document.body.appendChild(effectDiv);

    // 3秒后移除
    setTimeout(() => {
        effectDiv.style.animation = 'slideOut 0.3s ease-out forwards';
        setTimeout(() => effectDiv.remove(), 300);
    }, 3000);
}

// 添加动画样式
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from { transform: translateX(100%); opacity: 0; }
        to { transform: translateX(0); opacity: 1; }
    }
    @keyframes slideOut {
        from { transform: translateX(0); opacity: 1; }
        to { transform: translateX(100%); opacity: 0; }
    }
`;
document.head.appendChild(style);

async function addMessage(msg) {
    state.messages.push(msg);

    // 确定角色
    // 注意：角色标签（霸凌者/受害者/旁观者）属内部信息，不在界面上暴露，
    // 仅用于消息样式区分（见 css/style.css 中 .message.<role> 的配色）。
    const role = msg.role || 'unknown';

    const messagesList = document.getElementById('messages-list');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;
    messageDiv.dataset.id = msg.id;

    // 高亮说话者
    highlightSpeaker(role);

    let metaHtml = '';

    if (msg.hostile_score && msg.hostile_score > 0) {
        metaHtml += `<span class="tag hostile">攻击性: ${Math.round(msg.hostile_score * 100)}%</span>`;
    }

    if (msg.emotions && msg.emotions.length > 0) {
        const emotionNames = {
            'sad': '悲伤',
            'angry': '愤怒',
            'fearful': '恐惧',
            'happy': '开心',
            'remorseful': '后悔',
        };
        const emotions = msg.emotions.map(e => emotionNames[e] || e).join(', ');
        metaHtml += `<span class="tag emotion">${emotions}</span>`;
    }

    if (role === 'teacher') {
        metaHtml = '<span class="tag intervention">教师干预</span>';

        // 如果有评估结果,添加反馈
        if (msg.quality_score !== undefined) {
            const q = msg.quality_score;
            const qualityLabel = (q === null) ? '待评估' : (q >= 60 ? '良好' : q >= 40 ? '一般' : '需改进');
            const qBg = (q !== null && q >= 50) ? '#c8e6c9' : '#fff9c4';
            const qFg = (q !== null && q >= 50) ? '#2e7d32' : '#f57c00';
            metaHtml += ` <span class="tag" style="background: ${qBg}; color: ${qFg};">效果: ${qualityLabel} (${fmtQualityScore(q)})</span>`;
        }

        // R12：教师干预消息显示「已发送」标记，用户可在对话区直接确认发送结果
        if (msg.sent_status === 'sent') {
            metaHtml += ` <span class="tag sent-badge">✅ 已发送${msg.sent_at ? ' ' + escapeHtml(msg.sent_at) : ''}</span>`;
        }
    }

    messageDiv.innerHTML = `
        <div class="message-header">
            <span class="message-sender">${msg.sender}</span>
        </div>
        <div class="message-content">${escapeHtml(msg.content)}</div>
        ${metaHtml ? `<div class="message-meta">${metaHtml}</div>` : ''}
    `;

    messagesList.appendChild(messageDiv);

    // 滚动到底部
    const container = document.getElementById('messages-container');
    container.scrollTop = container.scrollHeight;

    await delay(100);
}

function addSystemMessage(content) {
    const messagesList = document.getElementById('messages-list');
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message system';
    messageDiv.textContent = content;
    messagesList.appendChild(messageDiv);

    const container = document.getElementById('messages-container');
    container.scrollTop = container.scrollHeight;
}

function showInterventionResult(result) {
    // 显示评估反馈
    if (result && result.feedback && result.feedback.length > 0) {
        // 找到最后一条干预消息并添加反馈显示
        const messages = document.querySelectorAll('.message.teacher');
        if (messages.length > 0) {
            const lastMsg = messages[messages.length - 1];
            const feedbackDiv = document.createElement('div');
            feedbackDiv.className = 'intervention-feedback';
            feedbackDiv.style.cssText = 'margin-top: 8px; padding: 8px; background: rgba(0,0,0,0.05); border-radius: 6px; font-size: 0.8125rem;';

            let feedbackHtml = '<div style="margin-bottom: 4px;"><strong>📊 干预评估</strong></div>';
            result.feedback.forEach(fb => {
                const isPositive = fb.startsWith('✅') || fb.startsWith('+');
                const isWarning = fb.startsWith('!!') || fb.startsWith('⚠️');
                const isInfo = fb.startsWith('📌') || fb.startsWith('💡');
                let color = '#ff9800';
                if (isPositive) color = '#4CAF50';
                if (isWarning) color = '#f44336';
                if (isInfo) color = '#2196F3';
                feedbackHtml += `<div style="color: ${color}; margin: 2px 0;">${fb}</div>`;
            });
            if (result.recommendation) {
                feedbackHtml += `<div style="margin-top: 6px; color: #666; font-style: italic;">💬 ${result.recommendation}</div>`;
            }
            // OBVQ 得分和维度聚焦
            if (result.obvq_total_score !== undefined) {
                feedbackHtml += `<div style="margin-top: 4px; font-size: 0.75rem;">📊 欺凌类型总分（项目分类）: <span style="font-weight:600">${result.obvq_total_score}</span></div>`;
            }
            feedbackHtml += `<div style="margin-top: 4px; font-size: 0.75rem; color: #999;">效果: ${fmtQualityScore(result.quality_score)}</div>`;
            feedbackDiv.innerHTML = feedbackHtml;
            lastMsg.appendChild(feedbackDiv);
        }
    }
}

function addToHistory(intervention) {
    state.interventions.push(intervention);

    const historyList = document.querySelector('.history-list');
    const item = document.createElement('div');
    item.className = 'history-item';

    const qualityClass = intervention.quality_score >= 50 ? 'good' : 'bad';
    const qualityLabel = intervention.quality_score >= 60 ? '良好' : intervention.quality_score >= 40 ? '一般' : '需改进';

    item.innerHTML = `
        <div class="quality ${qualityClass}">${qualityLabel} (${fmtQualityScore(intervention.quality_score)})</div>
        <div class="content">"${escapeHtml((intervention.content || '').substring(0, 50))}${(intervention.content || '').length > 50 ? '...' : ''}"</div>
    `;

    historyList.insertBefore(item, historyList.firstChild);
}

// 显示报告内容(传入报告数据)
function displayReport(report) {
    const content = document.getElementById('report-content');
    const summary = report.summary || {};
    const metrics = report.metrics || {};
    const assessment = report.ability_assessment || {};
    const obvq = report.obvq_analysis || {};
    const interventionAnalysis = report.intervention_analysis || {};
    const trends = report.trends || {};

    function trendArrow(val) {
        if (val < -0.02) return '↓';
        if (val > 0.02) return '↑';
        return '→';
    }

    content.innerHTML = `
        <div class="report-section">
            <h3>📊 霸凌情况</h3>
            <div class="report-metric">
                <span>总消息数</span>
                <span class="value">${summary.total_messages || 0}</span>
            </div>
            <div class="report-metric">
                <span>攻击性事件</span>
                <span class="value danger">${summary.hostile_messages || 0}次</span>
            </div>
            <div class="report-metric">
                <span>最终霸凌强度</span>
                <span class="value ${summary.bullying_intensity > 0.5 ? 'danger' : ''}">${Math.round((summary.bullying_intensity || 0) * 100)}%</span>
            </div>
            <div class="report-metric">
                <span>受害者压力</span>
                <span class="value ${summary.victim_distress > 0.4 ? 'warning' : ''}">${Math.round((summary.victim_distress || 0) * 100)}%</span>
            </div>
        </div>

        <div class="report-section">
            <h3>👨🏫 教师干预</h3>
            <div class="report-metric">
                <span>干预次数</span>
                <span class="value">${summary.intervention_count || 0}</span>
            </div>
            <div class="report-metric">
                <span>教师干预质量评分</span>
                <span class="value ${(summary.intervention_effectiveness || 0) >= 50 ? 'good' : 'warning'}">${Math.round(summary.intervention_effectiveness || 0)}/100</span>
            </div>
        </div>
        ${trends.summary ? `
        <div class="report-section">
            <h3>📈 指标变化趋势</h3>
            <p style="font-size: 0.8125rem; color: #555; margin: 4px 0;">${trends.summary}</p>
            <div style="display: flex; gap: 16px; margin: 8px 0;">
                <div style="flex:1; background:#f8f8f8; border-radius:6px; padding:8px; text-align:center;">
                    <div style="font-size:0.75rem; color:#888;">起点欺凌强度</div>
                    <div style="font-size:1.25rem; font-weight:600;">${Math.round((trends.early_bullying || 0) * 100)}%</div>
                    <div style="font-size:0.75rem; color:#999;">→ 最终 ${Math.round((trends.late_bullying || 0) * 100)}%</div>
                    <div style="color:${trends.bullying_change > 0 ? '#f44336' : '#4CAF50'};">${trendArrow(trends.bullying_change)} ${Math.abs((trends.bullying_change || 0) * 100).toFixed(1)} 个百分点</div>
                </div>
                <div style="flex:1; background:#f8f8f8; border-radius:6px; padding:8px; text-align:center;">
                    <div style="font-size:0.75rem; color:#888;">起点受害者压力</div>
                    <div style="font-size:1.25rem; font-weight:600;">${Math.round((trends.early_distress || 0) * 100)}%</div>
                    <div style="font-size:0.75rem; color:#999;">→ 最终 ${Math.round((trends.late_distress || 0) * 100)}%</div>
                    <div style="color:${trends.distress_change > 0 ? '#f44336' : '#4CAF50'};">${trendArrow(trends.distress_change)} ${Math.abs((trends.distress_change || 0) * 100).toFixed(1)} 个百分点</div>
                </div>
            </div>
            ${trends.obvq_trends && trends.obvq_trends.length > 0 ? `
            <div style="margin-top: 12px;">
                <div style="font-size:0.8125rem; font-weight:600; margin-bottom:6px;">📊 六类欺凌类型变化（起点→终点）</div>
                <table style="width:100%; border-collapse:collapse; font-size:0.75rem;">
                    <tr style="background:#f0f0f0;">
                        <th style="padding:4px 6px; text-align:left;">维度</th>
                        <th style="padding:4px 6px; text-align:center;">起点</th>
                        <th style="padding:4px 6px; text-align:center;">终点</th>
                        <th style="padding:4px 6px; text-align:center;">趋势</th>
                    </tr>
                    ${trends.obvq_trends.filter(t => obvqStart(t) > 0 || obvqEnd(t) > 0).map(t => `
                    <tr style="border-bottom:1px solid #eee;">
                        <td style="padding:3px 6px;">${t.name}</td>
                        <td style="padding:3px 6px;text-align:center;">${obvqStart(t)}分</td><td style="padding:3px 6px;text-align:center;">${obvqEnd(t)}分</td>
                        <td style="padding:3px 6px; text-align:center; color:${t.direction === 'up' ? '#f44336' : t.direction === 'down' ? '#4CAF50' : '#999'};">
                            ${t.direction === 'up' ? '↑' : t.direction === 'down' ? '↓' : '→'}
                        </td>
                    </tr>
                    `).join('')}
                </table>
            </div>
            ` : ''}
        </div>
        ` : ''}

        <div class="report-section">
            <h3>📊 本次模拟欺凌行为类型统计（项目分类）</h3>
            <div class="report-metric">
                <span>类型总分（项目分类）</span>
                <span class="value">${obvq.total_score || 0}分</span>
            </div>
<p style="font-size:0.75rem;color:#888;margin:4px 0;">计分方式：每次识别到的欺凌事件按强度映射为 0–4 分并累加到所有命中类别；总分 = min(80, 六类累计分之和)。同一事件可重复计入多个类别。该统计不用于教师能力评分。</p>
            ${obvq.dimension_analysis && obvq.dimension_analysis.length > 0 ? obvq.dimension_analysis.map(dim => `
            <div class="report-metric">
                <span>${dim.name}</span>
                <span class="value">${dim.total}分</span>
            </div>
            `).join('') : ''}
            <p style="font-size: 0.875rem; color: #555; margin: 8px 0; padding: 8px; background: #f5f5f5; border-radius: 6px;"><strong>💡 综合建议: </strong>${obvq.recommendation || '无'}</p>
        </div>

        <div class="report-section">
            <h3>🔍 干预效果分析与建议</h3>
            <p style="font-size: 0.875rem; color: #555; margin: 8px 0;">${interventionAnalysis.summary || '本次模拟未进行任何干预。'}</p>
            ${interventionAnalysis.suggestions && interventionAnalysis.suggestions.length > 0 ? `
            <div style="margin: 8px 0;">
                <strong>改进建议:</strong>
                <ol style="margin: 4px 0 0 16px; font-size: 0.8125rem; color: #555;">
                ${interventionAnalysis.suggestions.map(s => `<li style="margin: 2px 0;">${s}</li>`).join('')}
                </ol>
            </div>
            ` : ''}
            ${interventionAnalysis.action_plan && interventionAnalysis.action_plan.length > 0 ? `
            <div style="margin: 8px 0; padding: 8px; background: #e8f5e9; border-radius: 6px;">
                <strong>📋 改进行动计划:</strong>
                <ul style="margin: 4px 0 0 16px; font-size: 0.8125rem; color: #2e7d32;">
                ${interventionAnalysis.action_plan.map(a => `<li style="margin: 2px 0;">☐ ${a}</li>`).join('')}
                </ul>
            </div>
            ` : ''}
        </div>

        <div class="report-section">
            <h3>🎯 能力评估</h3>
            <div class="report-metric">
                <span>发现能力</span>
                <span class="value ${assessment.discovery?.level === 'excellent' ? 'good' : assessment.discovery?.level === 'good' ? 'good' : 'warning'}">${assessment.discovery?.description || '未评估'}</span>
            </div>
            ${assessment.discovery?.response_delay !== null && assessment.discovery?.response_delay !== undefined ? `
            <p style="font-size: 0.75rem; color: #888; margin: -4px 0 8px 0;">${assessment.discovery.response_delay === 0 ? '欺凌信号出现后当轮即介入' : `欺凌信号出现后 ${assessment.discovery.response_delay} 轮内介入`}</p>
            ` : ''}
            <div class="report-metric">
                <span>干预能力</span>
                <span class="value ${assessment.intervention?.level === 'excellent' ? 'good' : assessment.intervention?.level === 'good' ? 'good' : 'warning'}">
                    ${assessment.intervention?.description || ''}
                </span>
            </div>
            <div class="report-metric">
                <span>受害者保护（D2）</span>
                <span class="value ${(victimProtectionOf(assessment)?.level === 'excellent' ? 'good' : victimProtectionOf(assessment)?.level === 'good' ? 'good' : 'warning')}">${victimProtectionOf(assessment)?.description || '未评估'}</span>
            </div>
        </div>

        ${report.interventions && report.interventions.length > 0 ? `
        <div class="report-section">
            <h3>📋 干预详情</h3>
            ${report.interventions.slice(-5).map((iv, i) => `
                <div class="report-metric">
                    <span>干预 ${i + 1}</span>
                    <span class="value ${iv.quality_score >= 50 ? 'good' : 'warning'}">${fmtQualityScore(iv.quality_score)}</span>
                </div>
                <p style="font-size: 0.8125rem; color: #666; margin: 4px 0 12px 0;">
                    "${escapeHtml(iv.content)}"
                </p>
            `).join('')}
        </div>
        ` : ''}
    `;

    // 更新 badge
    document.getElementById('session-badge').textContent = '已完成';
    document.getElementById('session-badge').className = 'badge';
}

// ============================================================
// 收敛弹窗（2026-08-06 新增）
// ============================================================

function showConvergencePopup(convergence) {
    if (!convergence || !convergence.convergence) return;

    const modal = document.getElementById('convergence-modal');
    if (!modal) return;

    const isResolved = convergence.convergence === 'resolved';
    document.getElementById('convergence-title').textContent =
        isResolved ? '✨ 霸凌行为已显著缓解' : '⏰ 实训已进行较长时间';
    document.getElementById('convergence-icon').textContent =
        isResolved ? '✅' : '🔄';
    document.getElementById('convergence-message').textContent = convergence.message;

    // "查看报告" 按钮
    const btnReport = document.getElementById('btn-convergence-report');
    const btnContinue = document.getElementById('btn-convergence-continue');

    // 清除旧事件绑定（用 cloneNode 替换）
    const newReport = btnReport.cloneNode(true);
    btnReport.parentNode.replaceChild(newReport, btnReport);
    const newContinue = btnContinue.cloneNode(true);
    btnContinue.parentNode.replaceChild(newContinue, btnContinue);

    newReport.addEventListener('click', async () => {
        modal.style.display = 'none';
        await stopSimulation();
        // 主动终结弹窗已按顺序展示时，先让用户确认，避免报告弹窗与结束弹窗叠加
        if (!document.getElementById('autoend-modal').classList.contains('active')) {
            await showReport();
        }
    });

    newContinue.addEventListener('click', () => {
        modal.style.display = 'none';
        // 不重置收敛标记（_convergence_triggered 已在后端控制，不会再次触发）
    });

    document.getElementById('close-convergence').onclick = () => {
        modal.style.display = 'none';
    };

    // 点击外部关闭
    modal.onclick = function(e) {
        if (e.target === modal) modal.style.display = 'none';
    };

    modal.style.display = 'flex';
}

async function showReport() {
    if (!state.sessionId) return;

    try {
        const report = await apiCall(`/api/session/${state.sessionId}/report`);

        const content = document.getElementById('report-content');

        const summary = report.summary || {};
        const metrics = report.metrics || {};
        const assessment = report.ability_assessment || {};
        const obvq = report.obvq_analysis || {};
        const interventionAnalysis = report.intervention_analysis || {};
        const trends = report.trends || {};
        // R19：结构化教师实训评估报告（八大板块）
        const tr = report.training_report || {};
        // R20：规范化报告数据合同（模拟状态变化/数据说明等均来自同一份 report_data）
        const rd = report.report_data || {};

        function trendArrow(val) {
            if (val < -0.02) return '&darr;';
            if (val > 0.02) return '&uarr;';
            return '&rarr;';
        }

        content.innerHTML = `
            <div class="report-section">
                <h3>📊 霸凌情况</h3>
                <div class="report-metric">
                    <span>总消息数</span>
                    <span class="value">${summary.total_messages || 0}</span>
                </div>
                <div class="report-metric">
                    <span>攻击性事件</span>
                    <span class="value danger">${summary.hostile_messages || 0}次</span>
                </div>
                <div class="report-metric">
                    <span>最终霸凌强度</span>
                    <span class="value ${summary.bullying_intensity > 0.5 ? 'danger' : ''}">${Math.round((summary.bullying_intensity || 0) * 100)}%</span>
                </div>
                <div class="report-metric">
                    <span>受害者压力</span>
                    <span class="value ${summary.victim_distress > 0.4 ? 'warning' : ''}">${Math.round((summary.victim_distress || 0) * 100)}%</span>
                </div>
            </div>

            <div class="report-section">
                <h3>👨‍🏫 教师干预</h3>
                <div class="report-metric">
                    <span>干预次数</span>
                    <span class="value">${summary.intervention_count || 0}</span>
                </div>
                <div class="report-metric">
                    <span>教师干预质量评分</span>
                    <span class="value ${(summary.intervention_effectiveness || 0) >= 50 ? 'good' : 'warning'}">${Math.round(summary.intervention_effectiveness || 0)}/100</span>
                </div>
            </div>
            ${trends.summary ? `
            <div class="report-section">
                <h3>📈 指标变化趋势</h3>
                <p style="font-size: 0.8125rem; color: #555; margin: 4px 0;">${trends.summary}</p>
                <div style="display: flex; gap: 16px; margin: 8px 0;">
                    <div style="flex:1; background:#f8f8f8; border-radius:6px; padding:8px; text-align:center;">
                        <div style="font-size:0.75rem; color:#888;">起点欺凌强度</div>
                        <div style="font-size:1.25rem; font-weight:600;">${Math.round((trends.early_bullying || 0) * 100)}%</div>
                        <div style="font-size:0.75rem; color:#999;">→ 最终 ${Math.round((trends.late_bullying || 0) * 100)}%</div>
                        <div style="color:${trends.bullying_change > 0 ? '#f44336' : '#4CAF50'};">${trendArrow(trends.bullying_change)} ${Math.abs((trends.bullying_change || 0) * 100).toFixed(1)} 个百分点</div>
                    </div>
                    <div style="flex:1; background:#f8f8f8; border-radius:6px; padding:8px; text-align:center;">
                        <div style="font-size:0.75rem; color:#888;">起点受害者压力</div>
                        <div style="font-size:1.25rem; font-weight:600;">${Math.round((trends.early_distress || 0) * 100)}%</div>
                        <div style="font-size:0.75rem; color:#999;">→ 最终 ${Math.round((trends.late_distress || 0) * 100)}%</div>
                        <div style="color:${trends.distress_change > 0 ? '#f44336' : '#4CAF50'};">${trendArrow(trends.distress_change)} ${Math.abs((trends.distress_change || 0) * 100).toFixed(1)} 个百分点</div>
                    </div>
                </div>
                ${trends.obvq_trends && trends.obvq_trends.length > 0 ? `
                <div style="margin-top: 12px;">
                    <div style="font-size:0.8125rem; font-weight:600; margin-bottom:6px;">📊 六类欺凌类型变化（起点→终点）</div>
                    <table style="width:100%; border-collapse:collapse; font-size:0.75rem;">
                        <tr style="background:#f0f0f0;">
                            <th style="padding:4px 6px; text-align:left;">维度</th>
                            <th style="padding:4px 6px; text-align:center;">起点</th>
                            <th style="padding:4px 6px; text-align:center;">终点</th>
                            <th style="padding:4px 6px; text-align:center;">趋势</th>
                        </tr>
                        ${trends.obvq_trends.filter(t => obvqStart(t) > 0 || obvqEnd(t) > 0).map(t => `
                        <tr style="border-bottom:1px solid #eee;">
                            <td style="padding:3px 6px;">${t.name}</td>
                            <td style="padding:3px 6px;text-align:center;">${obvqStart(t)}分</td><td style="padding:3px 6px;text-align:center;">${obvqEnd(t)}分</td>
                            <td style="padding:3px 6px; text-align:center; color:${t.direction === 'up' ? '#f44336' : t.direction === 'down' ? '#4CAF50' : '#999'};">
                                ${t.direction === 'up' ? '↑' : t.direction === 'down' ? '↓' : '→'}
                            </td>
                        </tr>
                        `).join('')}
                    </table>
                </div>
                ` : ''}
            </div>
            ` : ''}
            
            <div class="report-section">
                <h3>📊 本次模拟欺凌行为类型统计（项目分类）</h3>
                <div class="report-metric">
                    <span>类型总分（项目分类）</span>
                    <span class="value">${obvq.total_score || 0}分</span>
                </div>
<p style="font-size:0.75rem;color:#888;margin:4px 0;">计分方式：每次识别到的欺凌事件按强度映射为 0–4 分并累加到所有命中类别；总分 = min(80, 六类累计分之和)。同一事件可重复计入多个类别。该统计不用于教师能力评分。</p>
                ${obvq.dimension_analysis && obvq.dimension_analysis.length > 0 ? obvq.dimension_analysis.map(dim => `
                <div class="report-metric">
                    <span>${dim.name}</span>
                    <span class="value">${dim.total}分</span>
                </div>
                `).join('') : ''}
                <p style="font-size: 0.875rem; color: #555; margin: 8px 0; padding: 8px; background: #f5f5f5; border-radius: 6px;"><strong>💡 综合建议: </strong>${obvq.recommendation || '无'}</p>
            </div>

            <div class="report-section">
                <h3>🔍 干预效果分析与建议</h3>
                <p style="font-size: 0.875rem; color: #555; margin: 8px 0;">${interventionAnalysis.summary || '本次模拟未进行任何干预。'}</p>
                ${interventionAnalysis.suggestions && interventionAnalysis.suggestions.length > 0 ? `
                <div style="margin: 8px 0;">
                    <strong>改进建议:</strong>
                    <ol style="margin: 4px 0 0 16px; font-size: 0.8125rem; color: #555;">
                    ${interventionAnalysis.suggestions.map(s => `<li style="margin: 2px 0;">${s}</li>`).join('')}
                    </ol>
                </div>
                ` : ''}
                ${interventionAnalysis.action_plan && interventionAnalysis.action_plan.length > 0 ? `
                <div style="margin: 8px 0; padding: 8px; background: #e8f5e9; border-radius: 6px;">
                    <strong>📋 改进行动计划:</strong>
                    <ul style="margin: 4px 0 0 16px; font-size: 0.8125rem; color: #2e7d32;">
                    ${interventionAnalysis.action_plan.map(a => `<li style="margin: 2px 0;">☐ ${a}</li>`).join('')}
                    </ul>
                </div>
                ` : ''}
            </div>

            ${rd && rd.metrics ? `
            <div class="report-section">
                <h3>📈 模拟状态变化（模拟结果，非能力评价）</h3>
                ${['bullying_intensity', 'victim_distress'].map(k => {
                    const m = (rd.metrics || {})[k];
                    return m ? `<div class="tr-note">${escapeHtml(m.text || '')}（有效快照 ${m.valid_sample_count} 个，观察窗口：${escapeHtml(m.source_window || '')}）</div>` : '';
                }).join('')}
                ${rd.metrics && rd.metrics.note ? `<div class="tr-note">${escapeHtml(rd.metrics.note)}</div>` : ''}
            </div>
            ` : ''}

            ${rd && rd.risks && rd.risks.length ? `
            <div class="report-section">
                <h3>⚠ 关键风险检查</h3>
                <div class="tr-note">严重风险不可被其他维度的高分抵消；下列风险需要优先修正。</div>
                ${rd.risks.map(r => `
                <div class="tr-dimcard" style="border-left-color:#e53935;">
                    <div class="tr-dimcard-head">${escapeHtml(r.code || '')}｜等级 ${escapeHtml(r.level || '')}</div>
                    ${(r.evidence_refs || []).length ? `<div class="tr-dimcard-line"><b>原文证据：</b>${escapeHtml(r.evidence_refs.join(' / '))}</div>` : ''}
                    ${r.context ? `<div class="tr-dimcard-line"><b>语境：</b>${escapeHtml(r.context)}</div>` : ''}
                    ${r.recommended_correction ? `<div class="tr-dimcard-line" style="color:#1b5e20;"><b>必须修正：</b>${escapeHtml(r.recommended_correction)}</div>` : ''}
                </div>`).join('')}
            </div>
            ` : ''}

            ${rd && (rd.strengths || []).length ? `
            <div class="report-section">
                <h3>✅ 已具备的优势</h3>
                <div class="tr-note">以下维度已达到基本达标，可在后续演练中保持并迁移到新情境。</div>
                ${rd.strengths.map(st => `<div class="tr-note"><b>${escapeHtml(st.name || '')}</b>（${escapeHtml(String(st.score))}）：${escapeHtml(st.explanation || '')}</div>`).join('')}
            </div>
            ` : ''}



            ${rd && (rd.training_plan || []).length ? `
            <div class="report-section">
                <h3>🎯 训练计划与复测</h3>
                <div class="tr-note">最多 3 项近期训练目标：每项含目标行为、操作步骤、示范表达、安全约束、达标标准与复测任务。</div>
                ${rd.training_plan.map(p => `
                <div class="tr-dimcard" style="border-left-color:#8e24aa;">
                    <div class="tr-dimcard-head">优先级 ${p.priority} · ${escapeHtml(p.dimension || '')}｜${escapeHtml(p.objective || '')}</div>
                    ${(p.actions || []).map(a => `<div class="tr-dimcard-line">· 步骤：${escapeHtml(a)}</div>`).join('')}
                    ${p.sample_utterance ? `<div class="tr-dimcard-line"><b>示范表达：</b>${escapeHtml(p.sample_utterance)}</div>` : ''}
                    ${(p.safety_constraints || []).map(c => `<div class="tr-dimcard-line warn"><b>安全约束：</b>${escapeHtml(c)}</div>`).join('')}
                    ${p.success_criteria ? `<div class="tr-dimcard-line" style="color:#1b5e20;"><b>达标标准：</b>${escapeHtml(p.success_criteria)}</div>` : ''}
                    ${p.retest_task ? `<div class="tr-dimcard-line" style="color:#0d47a1;"><b>复测任务：</b>${escapeHtml(p.retest_task)}</div>` : ''}
                </div>`).join('')}
            </div>
            ` : ''}

            ${rd && (rd.interaction_timeline || []).length ? `
            <div class="report-section">
                <h3>🕰 互动时间轴</h3>
                <div class="tr-note">按轮次还原「谁说了什么 → 教师如何回应 → 学生如何反馈 → 专家如何解读」。</div>
                ${rd.interaction_timeline.map(t => `
                <div class="tr-dimcard" style="border-left-color:#00897b;">
                    <div class="tr-dimcard-head">${escapeHtml(t.turn_id || '')}｜第 ${escapeHtml(String(t.round))} 轮｜${escapeHtml(({teacher:'教师',bully:'欺凌者',victim:'受害者',bystander_a:'旁观者A',bystander_b:'旁观者B'})[t.speaker_role] || t.speaker_role || '-')}</div>
                    ${t.text_excerpt ? `<div class="tr-dimcard-line">${escapeHtml(t.text_excerpt)}</div>` : ''}
                    ${t.teacher_action ? `<div class="tr-dimcard-line"><b>教师行动：</b>${escapeHtml(t.teacher_action)}</div>` : ''}
                    ${t.student_response ? `<div class="tr-dimcard-line"><b>学生反馈：</b>${escapeHtml(t.student_response)}</div>` : ''}
                    ${t.expert_interpretation ? `<div class="tr-dimcard-line" style="color:#4527a0;"><b>专家解读：</b>${escapeHtml(t.expert_interpretation)}</div>` : ''}
                </div>`).join('')}
            </div>
            ` : ''}

            ${rd && (rd.reflection_questions || []).length ? `
            <div class="report-section">
                <h3>💭 反思问题</h3>
                <div class="tr-note">复盘时逐条回答，把「看过报告」变成「下次能做到」。</div>
                ${rd.reflection_questions.map(q => `<div class="tr-note">· ${escapeHtml(q)}</div>`).join('')}
            </div>
            ` : ''}

            ${rd && rd.data_notes ? `
            <div class="report-section">
                <h3>📋 数据说明（可复核性）</h3>
                <div class="tr-note">评分状态：<b>${escapeHtml(rd.scoring_status || '')}</b> ｜ 量规版本：${escapeHtml(rd.rubric_version || '')} ｜ 提示词版本：${escapeHtml(rd.scoring_prompt_version || '')} ｜ 生成时间：${escapeHtml((rd.generated_at || '').slice(0, 19))}</div>
                <div class="tr-note">有效评分维度 ${((rd.summary || {}).rated_count || 0)} ｜ 无可观察（N/O）${((rd.summary || {}).no_opportunity_count || 0)} ｜ 证据不足 ${((rd.summary || {}).insufficient_evidence_count || 0)} ｜ 未评估 ${((rd.summary || {}).not_assessed_count || 0)}</div>
                <div class="tr-note">报告ID：${escapeHtml(rd.report_id || '')} ｜ 合同版本：${escapeHtml(rd.report_schema_version || '')} ｜ 模型：${escapeHtml(rd.model_id || '') || '未记录'} ｜ 生成时间：${escapeHtml((rd.generated_at || '').slice(0, 19))}</div>
                <div class="tr-note">总分口径：${escapeHtml((rd.data_notes || {}).score_formula || '')}</div>
                ${((rd.data_notes || {}).limitations || []).map(l => `<div class="tr-note">· ${escapeHtml(l)}</div>`).join('')}
                ${(rd.dimensions || []).some(d => d.status === 'N_O') ? `<div class="tr-note">ℹ N/O＝本次互动中该维度客观上没有表现机会，不计入均分分母，也不按 0 分处理。</div>` : ''}
                ${(rd.dimensions || []).some(d => d.status === 'insufficient_evidence') ? `<div class="tr-note">⚠ 证据不足＝本应可能评价，但互动记录不足以可靠支持评分；不计分，不伪装成 N/O 或 0 分。</div>` : ''}
            </div>
            ` : ''}

            ${tr && tr.executive_summary ? `
            <div class="report-section">
                <h3>📊 总体评估结论</h3>
                <div class="tr-summary">
                    <div class="tr-level">${escapeHtml(tr.executive_summary.level || '')}</div>
                    <div class="tr-headline">${escapeHtml(tr.executive_summary.headline || '')}</div>
                    <div class="tr-desc">${escapeHtml(tr.executive_summary.level_desc || '')}</div>
                    <div class="tr-kpis">
                        <div><b>${tr.executive_summary.avg_score !== null && tr.executive_summary.avg_score !== undefined ? tr.executive_summary.avg_score : '—'}</b><span>平均分</span></div>
                        <div><b>${tr.executive_summary.rounds_passed}/${tr.executive_summary.rounds_intervened}</b><span>达标轮次</span></div>
                        <div><b>${escapeHtml((tr.executive_summary.trend || {}).direction || '—')}</b><span>趋势</span></div>
                        <div><b>${tr.executive_summary.gap_to_pass !== null && tr.executive_summary.gap_to_pass !== undefined ? tr.executive_summary.gap_to_pass : '—'}</b><span>距达标差距</span></div>
                    </div>
                    <div class="tr-note">🔎 ${escapeHtml(tr.executive_summary.rules_note || '')}</div>
                    <div class="tr-note">📐 评分口径：${escapeHtml(tr.executive_summary.score_formula || '')}</div>
                    ${tr.executive_summary.key_problem ? `<div class="tr-key">⚑ ${escapeHtml(tr.executive_summary.key_problem)}</div>` : ''}
                    ${(tr.executive_summary.top_actions || []).length ? `
                    <div class="tr-subtitle">最高性价比的三个动作</div>
                    <ul class="tr-list">
                        ${tr.executive_summary.top_actions.map((a, i) => `<li><b>${i + 1}. ${escapeHtml(a.name || '')}</b>（预计可提升约 ${a.expected_gain} 分）：${escapeHtml(a.action || '')}</li>`).join('')}
                    </ul>` : ''}
                </div>
            </div>` : ''}

            ${tr && tr.capability_profile ? `
            <div class="report-section">
                <h3>🧬 七维能力画像</h3>
                <div class="tr-note">${escapeHtml(tr.capability_profile.summary || '')}</div>
                <table class="tr-table">
                    <thead><tr><th>维度</th><th>均分</th><th>最好/最差</th><th>达标率</th><th>趋势</th><th>等级</th></tr></thead>
                    <tbody>
                        ${(tr.capability_profile.dimensions || []).map(d => `
                        <tr class="${d.avg !== null && d.avg !== undefined && d.avg < 60 ? 'low' : ''}">
                            <td><b>${escapeHtml(d.dim || '')}</b> ${escapeHtml(d.name || '')}</td>
                            <td>${d.avg !== null && d.avg !== undefined ? d.avg : 'N/O'}</td>
                            <td>${d.best !== null && d.best !== undefined ? d.best + ' / ' + d.worst : '—'}</td>
                            <td>${Math.round((d.pass_rate || 0) * 100)}%</td>
                            <td>${escapeHtml(d.trend || '—')}</td>
                            <td>${escapeHtml(d.level || '—')}</td>
                        </tr>`).join('')}
                    </tbody>
                </table>
                ${(tr.capability_profile.no_observable_dims || []).length ? `<div class="tr-note">ℹ 本次无可观察证据（判 N/O，不计入均分）：${tr.capability_profile.no_observable_dims.join('、')}</div>` : ''}
                ${(tr.capability_profile.dimensions || []).filter(d => d.avg !== null && d.avg !== undefined && d.avg < 60).map(d => `
                <div class="tr-dimcard">
                    <div class="tr-dimcard-head">${escapeHtml(d.dim || '')} ${escapeHtml(d.name || '')}（均分 ${d.avg}）</div>
                    <div class="tr-dimcard-line"><b>量规要求：</b>${escapeHtml(d.requirement || '')}</div>
                    <div class="tr-dimcard-line"><b>常见缺口：</b>${escapeHtml(d.gap || '')}</div>
                    <div class="tr-dimcard-line"><b>改进做法：</b>${escapeHtml(d.advice || '')}</div>
                    <div class="tr-dimcard-line warn"><b>避免：</b>${escapeHtml(d.avoid || '')}</div>
                </div>`).join('')}
            </div>` : ''}

            ${rd && (rd.dimensions || []).length ? `
            <div class="report-section">
                <h3>🧾 七维评分明细（分数·证据·下一步）</h3>
                <div class="tr-note">每个维度的得分依据：分数与分档、专家判断理由、引用的原文证据、缺失行为、下一步目标与复测标准。</div>
                ${rd.dimensions.map(d => `
                <div class="tr-dimcard" style="border-left-color:${d.status === 'scored' ? '#42a5f5' : '#bdbdbd'};">
                    <div class="tr-dimcard-head">${escapeHtml(d.code || '')} ${escapeHtml(d.name || '')}｜${d.score_100 === null || d.score_100 === undefined ? '未计分' : escapeHtml(String(d.score_100)) + ' 分（' + escapeHtml(d.band || '-') + '）'}｜状态 ${escapeHtml(d.status || '')}</div>
                    ${d.rationale ? `<div class="tr-dimcard-line"><b>专家判断：</b>${escapeHtml(d.rationale)}</div>` : ''}
                    ${(d.evidence_refs || []).length ? `<div class="tr-dimcard-line"><b>原文证据：</b>${escapeHtml(d.evidence_refs.join(' / '))}</div>` : ''}
                    ${(d.missing_behaviors || []).length ? `<div class="tr-dimcard-line warn"><b>缺失行为：</b>${escapeHtml(d.missing_behaviors.join('；'))}</div>` : ''}
                    ${d.next_target ? `<div class="tr-dimcard-line" style="color:#1b5e20;"><b>下一步目标：</b>${escapeHtml(d.next_target)}</div>` : ''}
                    ${d.sample_utterance ? `<div class="tr-dimcard-line"><b>示范表达：</b>${escapeHtml(d.sample_utterance)}</div>` : ''}
                    ${d.retest_criteria ? `<div class="tr-dimcard-line" style="color:#0d47a1;"><b>复测标准：</b>${escapeHtml(d.retest_criteria)}</div>` : ''}
                </div>`).join('')}
            </div>
            ` : ''}

            ${tr && tr.failure_attribution ? `
            <div class="report-section">
                <h3>🔍 失分归因</h3>
                <div class="tr-note">${escapeHtml(tr.failure_attribution.summary || '')}</div>
                ${(tr.failure_attribution.categories || []).map(c => `
                <div class="tr-catcard">
                    <div class="tr-catcard-head">
                        <span class="tr-cat-name">${escapeHtml(c.category || '')}</span>
                        <span class="tr-cat-meta">${c.count} 项 · 涉及第 ${(c.rounds || []).join('、')} 轮 · 占总缺口 ${c.share}%</span>
                    </div>
                    <div class="tr-catcard-desc">${escapeHtml(c.desc || '')}</div>
                    <ul class="tr-list">
                        ${(c.items || []).map(it => `
                        <li>
                            <b>第 ${it.round} 轮${it.name ? ' · ' + escapeHtml(it.name) : ''}</b>
                            ${it.detail ? `<div class="tr-item-detail">${escapeHtml(it.detail)}</div>` : ''}
                            ${it.fix ? `<div class="tr-item-fix">👉 改法：${escapeHtml(it.fix)}</div>` : ''}
                            ${it.avoid ? `<div class="tr-item-warn">⚠️ ${escapeHtml(it.avoid)}</div>` : ''}
                        </li>`).join('')}
                    </ul>
                </div>`).join('')}
                <div class="tr-note">${escapeHtml(tr.failure_attribution.principle || '')}</div>
            </div>` : ''}

            ${tr && tr.pitfalls && (tr.pitfalls.items || []).length ? `
            <div class="report-section">
                <h3>⚠ 典型表述误区对照（取自你的实际用语）</h3>
                <div class="tr-note">${escapeHtml(tr.pitfalls.summary || '')}</div>
                ${tr.pitfalls.items.map(it => `
                <div class="tr-pitfall">
                    <div class="tr-pf-quote">第 ${it.round} 轮你的表述：「${escapeHtml(it.excerpt || '')}」</div>
                    <div class="tr-pf-line"><b>${escapeHtml(it.trigger || '')}</b></div>
                    <div class="tr-pf-line">判定：${escapeHtml(it.verdict || '')}</div>
                    ${it.why ? `<div class="tr-pf-line">原因：${escapeHtml(it.why)}</div>` : ''}
                    ${it.fix ? `<div class="tr-pf-line ok">👉 改法：${escapeHtml(it.fix)}</div>` : ''}
                    ${it.avoid ? `<div class="tr-pf-line warn">⚠️ ${escapeHtml(it.avoid)}</div>` : ''}
                </div>`).join('')}
            </div>` : ''}

            ${tr && tr.training_roadmap ? `
            <div class="report-section">
                <h3>🪜 分阶段训练路线</h3>
                <div class="tr-note">${escapeHtml(tr.training_roadmap.summary || '')}</div>
                <div class="tr-note">${escapeHtml(tr.training_roadmap.method || '')}</div>
                ${(tr.training_roadmap.stages || []).map(s => `
                <div class="tr-stage">
                    <div class="tr-stage-head">${escapeHtml(s.title || '')}<span class="tr-stage-target">目标维度：${(s.target_names || []).join('、')}</span></div>
                    <div class="tr-stage-goal">🎯 ${escapeHtml(s.goal || '')}</div>
                    <div class="tr-stage-why">${escapeHtml(s.why_now || '')}</div>
                    <div class="tr-subtitle">练习动作</div>
                    <ul class="tr-list">${(s.drills || []).map(d => `<li>${escapeHtml(d)}</li>`).join('')}</ul>
                    <div class="tr-stage-check">✅ 达标标准：${escapeHtml(s.checkpoint || '')}</div>
                    ${s.script ? `<div class="tr-script">参考结构：${escapeHtml(s.script)}</div>` : ''}
                </div>`).join('')}
            </div>` : ''}

            ${tr && tr.scripts ? `
            <div class="report-section">
                <h3>📜 可直接套用的完整干预话术</h3>
                <div class="tr-note">${escapeHtml(tr.scripts.summary || '')}</div>
                ${(tr.scripts.scripts || []).map(s => `
                <div class="tr-script-card">
                    <div class="tr-script-scene">🎬 ${escapeHtml(s.scene || '')}<span class="tr-script-covers">覆盖 ${(s.covers || []).join('、')}</span></div>
                    <div class="tr-script-text">${escapeHtml(s.script || '')}</div>
                    <div class="tr-script-when">使用时机：${escapeHtml(s.when_to_use || '')}</div>
                </div>`).join('')}
                <div class="tr-note">${escapeHtml(tr.scripts.note || '')}</div>
            </div>` : ''}

            ${tr && tr.self_check ? `
            <div class="report-section">
                <h3>☑ 发送前自检清单</h3>
                <div class="tr-note">${escapeHtml(tr.self_check.summary || '')}</div>
                <div class="tr-note">💡 ${escapeHtml(tr.self_check.hint || '')}</div>
                <ul class="tr-checklist">
                    ${(tr.self_check.items || []).map(it => `<li><span class="tr-box"></span>${escapeHtml(it.text || '')}<span class="tr-check-dim">${escapeHtml(it.dim || '')}</span></li>`).join('')}
                </ul>
            </div>` : ''}

            ${tr && tr.rubric_explainer ? `
            <div class="report-section">
                <h3>📖 评分标准速查</h3>
                <div class="tr-subtitle">四档分位</div>
                <table class="tr-table">
                    <thead><tr><th>区间</th><th>含义</th><th>说明</th></tr></thead>
                    <tbody>
                        ${(tr.rubric_explainer.bands || []).map(b => `<tr><td><b>${escapeHtml(b.range || '')}</b></td><td>${escapeHtml(b.label || '')}</td><td>${escapeHtml(b.desc || '')}</td></tr>`).join('')}
                    </tbody>
                </table>
                <div class="tr-subtitle">七维判定要点</div>
                <ul class="tr-list">
                    ${(tr.rubric_explainer.dimensions || []).map(d => `<li><b>${escapeHtml(d.dim || '')} ${escapeHtml(d.name || '')}</b>：${escapeHtml(d.criteria || '')}</li>`).join('')}
                </ul>
                <div class="tr-note">ℹ N/O 规则：${escapeHtml(tr.rubric_explainer.no_rule || '')}</div>
                <div class="tr-subtitle">风险代码与影响</div>
                <ul class="tr-list">
                    ${(tr.rubric_explainer.risk_codes || []).map(r => `<li><b>${escapeHtml(r.label || '')}</b>（${escapeHtml(r.code || '')}）：${escapeHtml(r.avoid || '')}<div class="tr-item-detail">${escapeHtml(r.penalty || '')}</div></li>`).join('')}
                </ul>
                <div class="tr-note">🧷 ${escapeHtml(tr.rubric_explainer.cap_rule || '')}</div>
                <ul class="tr-list">${(tr.rubric_explainer.principles || []).map(p => `<li>${escapeHtml(p)}</li>`).join('')}</ul>
            </div>` : ''}

            ${(report.round_analysis || []).length ? `
            <div class="report-section">
                <h3>🧭 逐轮分析与针对性建议</h3>
                ${report.round_analysis.map(r => `
                <div class="report-round-card">
                    <div class="rc-head">
                        <span>第 ${r.round} 轮 · ${r.has_intervention ? '已干预' : '未干预'}</span>
                        <span class="rc-score ${(r.score !== null && r.score !== undefined && r.score < 60) ? 'low' : ''}">${(r.score === null || r.score === undefined) ? '未评分' : r.score + ' 分'}</span>
                    </div>
                    <div class="rc-line">情境：${escapeHtml(r.event_summary || '')}</div>
                    ${r.what_you_did ? `<div class="rc-line">你的干预：${escapeHtml(r.what_you_did)}</div>` : ''}
                    ${r.coach_comment ? `<div class="rc-line">点评：${escapeHtml(r.coach_comment)}</div>` : ''}
                    ${(r.weak_dimensions || []).length ? `<div class="rc-line">失分维度：${r.weak_dimensions.map(w => `${w.dim} ${w.name} ${w.score}分（${w.gap || ''}）`).map(escapeHtml).join('；')}</div>` : ''}
                    ${(r.risk_flags || []).length ? `<div class="rc-line">风险标记：${r.risk_flags.map(f => `${f.label || f.code}：${f.advice || ''}`).map(escapeHtml).join('；')}</div>` : ''}
                    ${r.next_step ? `<div class="rc-next">👉 下一步建议：${escapeHtml(r.next_step)}</div>` : ''}
                </div>`).join('')}
            </div>
            ` : ''}

            ${report.improvement_suggestions && report.improvement_suggestions.rounds_analyzed ? `
            <div class="report-section">
                <h3>🛠 详细改进意见</h3>
                <p style="font-size:0.875rem;color:#555;margin:6px 0;">${escapeHtml(report.improvement_suggestions.summary || '')}</p>
                ${(report.improvement_suggestions.priorities || []).length ? '<strong style="font-size:0.875rem;">优先改进项</strong>' : ''}
                ${(report.improvement_suggestions.priorities || []).map(p => `
                <div class="report-priority">
                    <div class="rp-title">${p.rank}. ${escapeHtml(p.name || '')}（${p.dim || ''}）</div>
                    <div class="rp-why">${escapeHtml(p.why || '')}</div>
                    <div class="rp-action">👉 ${escapeHtml(p.action || '')}</div>
                    ${p.avoid ? `<div class="rp-action" style="color:#c62828;">⚠️ ${escapeHtml(p.avoid)}</div>` : ''}
                    ${p.sample ? `<div class="coaching-quote">参考话术：${escapeHtml(p.sample)}</div>` : ''}
                </div>`).join('')}
                ${(report.improvement_suggestions.templates || []).length ? '<strong style="font-size:0.875rem;margin-top:8px;display:block;">可直接套用的话术</strong>' : ''}
                ${(report.improvement_suggestions.templates || []).map(t => `
                <div class="report-template">
                    <div class="rt-target">${escapeHtml(t.target || '')}</div>
                    <div class="rt-text">${escapeHtml(t.text || '')}</div>
                    ${t.why ? `<div class="rp-why">${escapeHtml(t.why)}</div>` : ''}
                </div>`).join('')}
                ${(report.improvement_suggestions.risk_reminders || []).length ? `
                <div style="margin:8px 0;">
                    <strong style="font-size:0.875rem;">⚠️ 需规避的风险</strong>
                    <ul style="margin:4px 0 0 16px;font-size:0.8125rem;color:#c62828;">
                    ${report.improvement_suggestions.risk_reminders.map(r => `<li>${escapeHtml(r.label || r.code)}（${r.times || 0} 次）：${escapeHtml(r.advice || '')}</li>`).join('')}
                    </ul>
                </div>` : ''}
                ${(report.improvement_suggestions.action_plan || []).length ? `
                <div style="margin:8px 0;padding:8px;background:#e8f5e9;border-radius:6px;">
                    <strong>📋 改进行动计划：</strong>
                    <ul style="margin:4px 0 0 16px;font-size:0.8125rem;color:#2e7d32;">
                    ${report.improvement_suggestions.action_plan.map(a => `<li>☐ ${escapeHtml(a)}</li>`).join('')}
                    </ul>
                </div>` : ''}
            </div>
            ` : ''}

            <div class="report-section">
                <h3>🎯 能力评估</h3>
                <div class="report-metric">
                    <span>发现能力</span>
                    <span class="value ${assessment.discovery?.level === 'excellent' ? 'good' : assessment.discovery?.level === 'good' ? 'good' : 'warning'}">${assessment.discovery?.description || '未评估'}</span>
                </div>
                ${assessment.discovery?.response_delay !== null && assessment.discovery?.response_delay !== undefined ? `
                <p style="font-size: 0.75rem; color: #888; margin: -4px 0 8px 0;">${assessment.discovery.response_delay === 0 ? '欺凌信号出现后当轮即介入' : `欺凌信号出现后 ${assessment.discovery.response_delay} 轮内介入`}</p>
                ` : ''}
                <div class="report-metric">
                    <span>干预能力</span>
                    <span class="value ${assessment.intervention?.level === 'excellent' ? 'good' : assessment.intervention?.level === 'good' ? 'good' : 'warning'}">
                        ${assessment.intervention?.description || ''}
                    </span>
                </div>
                <div class="report-metric">
                    <span>受害者保护（D2）</span>
                    <span class="value ${(victimProtectionOf(assessment)?.level === 'excellent' ? 'good' : victimProtectionOf(assessment)?.level === 'good' ? 'good' : 'warning')}">${victimProtectionOf(assessment)?.description || '未评估'}</span>
                </div>
            </div>

            ${report.interventions && report.interventions.length > 0 ? `
            <div class="report-section">
                <h3>📋 干预详情</h3>
                ${report.interventions.slice(-5).map((iv, i) => `
                    <div class="report-metric">
                        <span>干预 ${i + 1}</span>
                        <span class="value ${(iv.quality_score || 0) >= 50 ? 'good' : 'warning'}">${fmtQualityScore(iv.quality_score)}</span>
                    </div>
                    <p style="font-size: 0.8125rem; color: #666; margin: 4px 0 12px 0;">
                        "${escapeHtml(iv.content)}"
                    </p>
                `).join('')}
            </div>
            ` : ''}
        `;

        // 显示模态框
        document.getElementById('report-modal').classList.add('active');

        // 更新 badge
        document.getElementById('session-badge').textContent = '已完成';
        document.getElementById('session-badge').className = 'badge';

    } catch (error) {
        console.error('获取报告失败:', error);
    }
}

// ============================================================
// 会话情境管理
// ============================================================

async function loadScenarios() {
    try {
        const result = await apiCall('/api/scenarios');
        state.scenarios = result.scenarios || [];
        renderScenarioSelect();
    } catch (error) {
        console.error('加载情境失败:', error);
    }
}

function renderScenarioSelect() {
    const select = document.getElementById('scenario-select');
    select.innerHTML = '<option value="">-- 选择情境 --</option>';

    state.scenarios.forEach(s => {
        const option = document.createElement('option');
        option.value = s.id;
        option.textContent = s.title;
        option.dataset.level = s.level;
        select.appendChild(option);
    });
}

function onScenarioSelect() {
    const select = document.getElementById('scenario-select');
    const scenarioId = select.value;

    if (!scenarioId) {
        // 清空输入
        document.getElementById('scenario-title').value = '';
        document.getElementById('scenario-desc').value = '';
        state.currentScenario = null;
        state.scenarioModified = false;
        updateScenarioTip();
        return;
    }

    // 查找选中情境
    const scenario = state.scenarios.find(s => s.id === scenarioId);
    if (scenario) {
        state.currentScenario = scenario;
        document.getElementById('scenario-title').value = scenario.title;
        document.getElementById('scenario-desc').value = scenario.description;
        state.scenarioModified = false;
        updateScenarioTip();

        // 自动选择级别
        if (scenario.level) {
            document.querySelectorAll('.level-btn').forEach(b => {
                b.classList.remove('active');
                if (parseInt(b.dataset.level) === scenario.level) {
                    b.classList.add('active');
                    state.level = scenario.level;
                    updateLevelInfo(scenario.level);
                }
            });
        }

        // 启用开始按钮
        document.getElementById('btn-start').disabled = false;
    }
}

// 启用情境选择(新会话流程)
function enableScenarioSelection() {
    document.getElementById('scenario-select').disabled = false;
    document.getElementById('scenario-select').value = '';
    document.getElementById('scenario-title').disabled = false;
    document.getElementById('scenario-title').value = '';
    document.getElementById('scenario-desc').disabled = false;
    document.getElementById('scenario-desc').value = '';
    document.getElementById('btn-save-scenario').classList.remove('hidden');
    state.currentScenario = null;
    state.scenarioModified = false;
    updateScenarioTip();

    // 显示开始按钮(禁用状态)
    const btnStart = document.getElementById('btn-start');
    const btnNewSess = document.getElementById('btn-new-session-alt');
    btnStart.classList.remove('hidden');
    btnStart.disabled = true;
    btnNewSess.classList.add('hidden');
}

function onScenarioModified() {
    if (!state.scenarioModified) {
        state.scenarioModified = true;
        updateScenarioTip();
    }
}

function updateScenarioTip() {
    const tip = document.getElementById('scenario-tip');
    if (state.scenarioModified) {
        tip.querySelector('.status-text').textContent = '描述已修改,点击"保存"保存情境';
        tip.className = 'scenario-status modified';
        document.getElementById('btn-save-scenario').classList.remove('hidden');
    } else {
        tip.querySelector('.status-text').textContent = '选择情境或创建新的会话情境';
        tip.className = 'scenario-status';
        document.getElementById('btn-save-scenario').classList.add('hidden');
    }
}

function newScenario() {
    // 清空并准备新建
    document.getElementById('scenario-select').value = '';
    document.getElementById('scenario-title').value = '新情境';
    document.getElementById('scenario-desc').value = '';
    state.currentScenario = null;
    state.scenarioModified = true;
    updateScenarioTip();
    document.getElementById('scenario-title').focus();
}

async function saveScenario() {
    const title = document.getElementById('scenario-title').value.trim();
    const description = document.getElementById('scenario-desc').value.trim();

    if (!title) {
        alert('请输入情境标题');
        return;
    }

    if (!description) {
        alert('请输入情境描述');
        return;
    }

    try {
        const result = await apiCall('/api/scenarios', 'POST', {
            title: title,
            description: description,
            level: state.level
        });

        // 添加到列表
        state.scenarios.push(result.scenario);
        renderScenarioSelect();

        // 选中新创建的情境
        document.getElementById('scenario-select').value = result.scenario.id;
        state.currentScenario = result.scenario;
        state.scenarioModified = false;
        updateScenarioTip();

        // 显示保存成功
        const tip = document.getElementById('scenario-tip');
        tip.querySelector('.status-text').textContent = '情境已保存成功';
        tip.className = 'scenario-status saved';
        setTimeout(() => {
            tip.querySelector('.status-text').textContent = '选择情境或创建新的会话情境';
            tip.className = 'scenario-status';
        }, 2000);

    } catch (error) {
        console.error('保存情境失败:', error);
        alert('保存失败: ' + error.message);
    }
}

function toggleScenarioPanel() {
    const form = document.getElementById('scenario-form');
    const icon = document.getElementById('scenario-collapse-icon');
    const header = document.getElementById('scenario-header');

    if (form.classList.contains('collapsed')) {
        // 展开
        form.classList.remove('collapsed');
        icon.classList.remove('collapsed');
        header.classList.remove('collapsed');
    } else {
        // 折叠
        form.classList.add('collapsed');
        icon.classList.add('collapsed');
        header.classList.add('collapsed');
    }
}

// ============================================================
// 工具函数
// ============================================================

function getMaxRounds(level) {
    return 99;  // 不限制轮数
}

function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================
// 参与者信息编辑
// ============================================================

async function loadParticipants() {
    try {
        const result = await apiCall('/api/participants', 'GET');
        state.participants = result.participants || [];
    } catch (error) {
        console.error('加载参与者信息失败:', error);
    }
}

function renderParticipantSelectList() {
    const container = document.getElementById('participant-select-list');
    if (!container) return;

    container.innerHTML = state.participants.map(p => {
        const pers = p.personality || {};
        const traits = Array.isArray(pers.traits) ? pers.traits : [];
        const preview = traits.slice(0, 2).join('、') || pers.background?.slice(0, 20) || '';
        return `
        <div class="participant-select-item ${state.currentEditingParticipant?.id === p.id ? 'active' : ''}"
             data-id="${p.id}">
            <span class="participant-icon">👤</span>
            <div style="flex:1">
                <div class="participant-name">${p.name}</div>
                <div class="participant-role">${p.roleName || p.role}</div>
                ${preview ? `<div style="font-size:0.75rem;color:#888;margin-top:2px;">${preview}</div>` : ''}
            </div>
        </div>
    `}).join('');

    // 添加点击事件
    container.querySelectorAll('.participant-select-item').forEach(item => {
        item.addEventListener('click', () => {
            const id = item.dataset.id;
            selectParticipant(id);
        });
    });
}

function selectParticipant(id) {
    const participant = state.participants.find(p => p.id === id);
    if (!participant) return;

    state.currentEditingParticipant = participant;
    state._isNewParticipant = false;
    document.getElementById('btn-save-participant').textContent = '💾 保存';

    // 更新选中状态
    document.querySelectorAll('.participant-select-item').forEach(item => {
        item.classList.toggle('active', item.dataset.id === id);
    });

    const isSystem = !id.startsWith('custom_');

    // 填充表单
    document.getElementById('edit-participant-name').textContent = isSystem && participant.id === '' ? '新建角色' : participant.name;
    document.getElementById('edit-participant-role').textContent = participant.roleName || participant.role;

    // 自定义徽章
    const customBadge = document.getElementById('edit-participant-custom-badge');
    if (isSystem) {
        customBadge.classList.add('hidden');
        document.getElementById('group-edit-role-type').classList.add('hidden');
    } else {
        customBadge.classList.remove('hidden');
    }

    // 系统角色可编辑姓名但不能改角色类型
    document.getElementById('edit-name').value = participant.name || '';
    document.getElementById('edit-background').value = participant.personality?.background || '';
    const traits = participant.personality?.traits || [];
    document.getElementById('edit-traits').value = traits.join('、');
    document.getElementById('edit-speaking-style').value = participant.personality?.speakingStyle?.join('、') || '';

    // 按钮状态
    if (isSystem) {
        document.getElementById('btn-delete-participant').classList.add('hidden');
        document.getElementById('btn-reset-participant').classList.remove('hidden');
    } else {
        document.getElementById('btn-delete-participant').classList.remove('hidden');
        document.getElementById('btn-reset-participant').classList.add('hidden');
    }
}

async function openParticipantModal() {
    await loadParticipants();

    state._isNewParticipant = false;

    if (state.participants.length > 0 && !state.currentEditingParticipant) {
        selectParticipant(state.participants[0].id);
    }

    renderParticipantSelectList();
    document.getElementById('participant-modal').classList.add('active');
}

function closeParticipantModal(e) {
    if (e) e.stopPropagation();
    const modal = document.getElementById('participant-modal');
    if (modal) modal.classList.remove('active');
    state.currentEditingParticipant = null;
    state._isNewParticipant = false;
}

function prepareNewParticipant() {
    const newId = 'custom_' + Date.now();
    state.currentEditingParticipant = {
        id: newId,
        name: '',
        role: 'bully',
        roleName: '霸凌者',
        isNew: true,
        personality: { background: '', traits: [], speakingStyle: [] }
    };
    state._isNewParticipant = true;

    // 更新选中状态
    document.querySelectorAll('.participant-select-item').forEach(item => {
        item.classList.remove('active');
    });

    // 填充表单
    document.getElementById('edit-participant-name').textContent = '新建角色';
    document.getElementById('edit-participant-role').textContent = '霸凌者';
    document.getElementById('edit-participant-custom-badge').classList.remove('hidden');
    document.getElementById('edit-name').value = '';
    document.getElementById('edit-background').value = '';
    document.getElementById('edit-traits').value = '';
    document.getElementById('edit-speaking-style').value = '';

    // 显示角色类型选择器
    document.getElementById('group-edit-role-type').classList.remove('hidden');
    document.getElementById('edit-role-type').value = 'bully';
    document.getElementById('edit-role-type').addEventListener('change', function(e) {
        const roleNames = { bully: '霸凌者', victim: '受害者', bystander_a: '正义旁观者', bystander_b: '从众旁观者' };
        document.getElementById('edit-participant-role').textContent = roleNames[e.target.value] || '';
    });

    // 按钮状态
    document.getElementById('btn-delete-participant').classList.add('hidden');
    document.getElementById('btn-reset-participant').classList.add('hidden');
    document.getElementById('btn-save-participant').textContent = '＋ 创建';
}

async function deleteParticipant() {
    if (!state.currentEditingParticipant) return;

    const id = state.currentEditingParticipant.id;
    if (!id.startsWith('custom_')) {
        alert('系统预设角色不能删除');
        return;
    }

    if (!confirm(`确定要删除角色"${state.currentEditingParticipant.name}"吗？此操作不可恢复。`)) return;

    try {
        const result = await apiCall(`/api/participants/${id}`, 'DELETE');
        if (result.success) {
            state.participants = state.participants.filter(p => p.id !== id);
            state.currentEditingParticipant = null;
            renderParticipantSelectList();
            renderParticipantCheckboxes();
            updateParticipantPanel();

            // 如果有其他角色则选中第一个
            if (state.participants.length > 0) {
                selectParticipant(state.participants[0].id);
            } else {
                prepareNewParticipant();
            }
            alert('已删除');
        }
    } catch (error) {
        console.error('删除失败:', error);
        alert('删除失败: ' + error.message);
    }
}

async function saveParticipant() {
    if (!state.currentEditingParticipant) return;

    const isNew = state._isNewParticipant;
    const isSystem = !state.currentEditingParticipant.id.startsWith('custom_');

    // 获取表单数据
    const name = document.getElementById('edit-name').value.trim();
    const background = document.getElementById('edit-background').value.trim();
    const traitsText = document.getElementById('edit-traits').value.trim();
    const speakingStyleText = document.getElementById('edit-speaking-style').value.trim();

    if (!name) {
        alert('请输入姓名');
        return;
    }

    const traits = traitsText ? traitsText.split(/[、,,]/).map(t => t.trim()).filter(t => t) : [];
    const speakingStyle = speakingStyleText ? speakingStyleText.split(/[、,,]/).map(s => s.trim()).filter(s => s) : [];

    try {
        let result;
        if (isNew || isSystem) {
            // 新建角色 / 编辑系统角色(自动转为自定义副本)
            const roleType = document.getElementById('edit-role-type').value;
            const roleNames = { bully: '霸凌者', victim: '受害者', bystander_a: '正义旁观者', bystander_b: '从众旁观者' };
            result = await apiCall('/api/participants', 'POST', {
                name: name,
                role: roleType,
                roleName: roleNames[roleType] || '',
                personality: { background, traits, speakingStyle }
            });
            if (isSystem && result.success) {
                alert('已创建自定义角色副本，预设角色未被修改');
            }
        } else {
            // 更新自定义角色
            const id = state.currentEditingParticipant.id;
            result = await apiCall(`/api/participants/${id}`, 'PUT', {
                name: name,
                roleName: state.currentEditingParticipant.roleName || '',
                personality: { background, traits, speakingStyle }
            });
        }

        if (result.success) {
            const savedParticipant = result.participant;
            const effectivelyNew = isNew || isSystem;

            if (effectivelyNew) {
                state.participants.push(savedParticipant);
            } else {
                const index = state.participants.findIndex(p => p.id === savedParticipant.id);
                if (index !== -1) state.participants[index] = savedParticipant;
            }

            state.currentEditingParticipant = savedParticipant;
            state._isNewParticipant = false;

            renderParticipantSelectList();
            renderParticipantCheckboxes();
            updateParticipantPanel();

            // 更新表单显示
            selectParticipant(savedParticipant.id);
            document.getElementById('btn-save-participant').textContent = '💾 保存';

            alert('保存成功!');
        }
    } catch (error) {
        console.error('保存失败:', error);
        alert('保存失败: ' + error.message);
    }
}

async function resetCurrentParticipant() {
    if (!state.currentEditingParticipant) return;

    if (!confirm('确定要重置为默认配置吗?')) return;

    const id = state.currentEditingParticipant.id;

    try {
        const result = await apiCall(`/api/participants/${id}/reset`, 'POST');

        if (result.success) {
            // 更新本地状态
            const index = state.participants.findIndex(p => p.id === id);
            if (index !== -1) {
                state.participants[index] = result.participant;
            }
            state.currentEditingParticipant = result.participant;

            // 更新表单显示
            selectParticipant(id);

            // 更新左侧参与者档案显示
            updateParticipantCard(id, result.participant);

            alert('已重置为默认配置');
        }
    } catch (error) {
        console.error('重置失败:', error);
        alert('重置失败: ' + error.message);
    }
}

function updateParticipantCard(id, participant) {
    const card = document.getElementById(`part-${id}`);
    if (!card) return;

    // 更新姓名
    const nameEl = card.querySelector('.participant-info strong');
    if (nameEl) nameEl.textContent = participant.name;

    // 更新图标
    const iconEl = card.querySelector('.participant-icon');
    if (iconEl && participant.icon) iconEl.textContent = participant.icon;

    // 更新角色标签
    // 角色标签（霸凌者/受害者/旁观者）不在学员界面展示，此处仅在存在时同步文字
    const roleEl = card.querySelector('.role-tag');
    if (roleEl && participant.roleName) roleEl.textContent = participant.roleName;

    // 更新特征描述
    const traitEl = card.querySelector('.participant-detail .trait');
    if (traitEl && participant.personality?.traits?.length > 0) {
        traitEl.textContent = participant.personality.traits.slice(0, 2).join('、');
    }
}

function renderParticipantCheckboxes() {
    const container = document.getElementById('participant-checkboxes');
    if (!container) return;

    // 角色标签不在学员界面暴露：仅用于详情提示（title），不作为可见文字渲染。
    const defaultSelected = ['bully', 'victim', 'bystander_a', 'bystander_b'];

    const sorted = [...state.participants].sort((a, b) => {
        const aSys = a.id.startsWith('custom_') ? 1 : 0;
        const bSys = b.id.startsWith('custom_') ? 1 : 0;
        return aSys - bSys;
    });

    container.innerHTML = sorted.map(p => {
        const checked = defaultSelected.includes(p.id) || !p.id.startsWith('custom_');
        const pers = p.personality || {};
        const traits = Array.isArray(pers.traits) ? pers.traits.slice(0, 2).join('、') : '';
        const detail = traits || pers.background?.slice(0, 15) || '';
        return `
        <label class="participant-checkbox-item" data-id="${p.id}" title="${detail}">
            <input type="checkbox" value="${p.id}" ${checked ? 'checked' : ''}>
            <div style="flex:1;min-width:0;">
                <span class="pc-name">${p.name}</span>
                ${detail ? `<span style="display:block;font-size:0.7rem;color:#999;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${detail}</span>` : ''}
            </div>
        </label>`;
    }).join('');
}

function getSelectedParticipantIds() {
    const checkboxes = document.querySelectorAll('#participant-checkboxes input[type="checkbox"]:checked');
    return Array.from(checkboxes).map(cb => cb.value);
}

function updateParticipantPanel() {
    // 更新左侧参与者档案面板（动态渲染）
    const panel = document.querySelector('.participants-panel');
    if (!panel) return;

    const headerEl = panel.querySelector('.panel-header');
    const defaultIds = ['bully', 'victim', 'bystander_a', 'bystander_b'];

    // 移除旧的动态卡片（保留 header）
    panel.querySelectorAll('.participant.dynamic').forEach(el => el.remove());
    // 保留系统默认四个角色卡片，更新它们的显示
    const systemCards = panel.querySelectorAll('.participant:not(.dynamic)');

    // 更新系统角色卡片
    // 注意：data-role 属性已移除（避免在 DOM 中暴露角色身份），
    // 改用卡片 id（part-<角色id>）定位对应参与者
    systemCards.forEach((card, idx) => {
        const roleId = defaultIds[idx];
        const p = state.participants.find(pp => pp.id === roleId);
        if (p) {
            const nameEl = card.querySelector('.participant-info strong');
            if (nameEl) nameEl.textContent = p.name;
            // 角色标签不在学员界面展示
            const traitEl = card.querySelector('.participant-detail .trait');
            if (traitEl && p.personality?.traits?.length > 0) {
                traitEl.textContent = p.personality.traits.slice(0, 2).join('、');
            }
        }
    });

    // 添加自定义角色卡片
    const customP = state.participants.filter(p => p.id.startsWith('custom_'));
    customP.forEach(p => {
        const card = document.createElement('div');
        card.className = 'participant dynamic';
        card.dataset.role = p.role;
        card.id = `part-${p.id}`;
        card.innerHTML = `
            <div class="participant-header">
                <span class="participant-icon">👤</span>
                <div class="participant-info">
                    <strong>${p.name}</strong>
                </div>
                <span class="status-dot"></span>
            </div>
            <div class="participant-detail">
                <p class="trait">${(p.personality?.traits || []).slice(0, 2).join('、') || '自定义角色'}</p>
            </div>`;
        panel.appendChild(card);
    });
}

// ============================================================
// 干预库管理
// ============================================================

async function loadInterventions() {
    try {
        const result = await apiCall('/api/interventions', 'GET');
        state.interventionLibrary = result.interventions || [];
        // 加载推荐干预
        await loadRecommendedInterventions();
    } catch (error) {
        console.error('加载干预库失败:', error);
    }
}

async function loadRecommendedInterventions() {
    try {
        // 获取当前状态参数
        const intensity = parseFloat(document.getElementById('progress-intensity')?.style.width || '0') / 100;
        const distress = parseFloat(document.getElementById('progress-distress')?.style.width || '0') / 100;
        const result = await apiCall(`/api/interventions/recommend?level=${state.level}&distress=${distress}&bullying_intensity=${intensity}`, 'GET');
        state.recommendedInterventions = result.recommended || [];
        state.allInterventions = result.all || [];

        // 显示关怀预警
        if (result.warning) {
            showInterventionWarning(result.warning);
        } else {
            hideInterventionWarning();
        }

        renderQuickButtons();
    } catch (error) {
        console.error('加载推荐干预失败:', error);
        // 降级:使用全部干预的前5个
        state.recommendedInterventions = state.interventionLibrary.slice(0, 5);
        state.allInterventions = state.interventionLibrary;
        renderQuickButtons();
    }
}

function showInterventionWarning(warning) {
    // 查找或创建关怀预警区域
    let warnEl = document.getElementById('intervention-warning');
    if (!warnEl) {
        warnEl = document.createElement('div');
        warnEl.id = 'intervention-warning';
        warnEl.className = 'intervention-warning';
        warnEl.style.cssText = 'margin: 8px 0; padding: 10px 12px; border-radius: 6px; font-size: 0.8125rem; cursor: pointer;';
        // 插入到干预面板顶部
        const panel = document.querySelector('.intervention-panel .panel-header');
        if (panel) panel.parentNode.insertBefore(warnEl, panel.nextSibling);
    }

    const colors = {
        'danger': { bg: '#ffebee', border: '#ef5350', color: '#c62828' },
        'warning': { bg: '#fff8e1', border: '#ffb300', color: '#f57c00' },
        'info': { bg: '#e3f2fd', border: '#42a5f5', color: '#1565c0' }
    };
    const style = colors[warning.level] || colors['info'];

    warnEl.style.background = style.bg;
    warnEl.style.border = `1px solid ${style.border}`;
    warnEl.style.color = style.color;
    warnEl.innerHTML = `<div style="font-weight: bold; margin-bottom: 4px;">${warning.message}</div>`;
    warnEl.onclick = () => warnEl.classList.toggle('collapsed');
    warnEl.classList.remove('collapsed');

    // 高亮推荐的心理修复干预
    if (warning.suggestions && warning.suggestions.length > 0) {
        setTimeout(() => {
            document.querySelectorAll('.quick-btn[data-id]').forEach(btn => {
                if (warning.suggestions.includes(btn.dataset.id)) {
                    btn.style.boxShadow = `0 0 0 2px ${style.border}`;
                    btn.style.fontWeight = 'bold';
                }
            });
        }, 100);
    }
}

function hideInterventionWarning() {
    const warnEl = document.getElementById('intervention-warning');
    if (warnEl) warnEl.remove();
    // 清除高亮
    document.querySelectorAll('.quick-btn[data-id]').forEach(btn => {
        btn.style.boxShadow = '';
        btn.style.fontWeight = '';
    });
}

function renderQuickButtons() {
    const container = document.getElementById('quick-btn-list');
    if (!container) return;

    // 显示推荐的干预(最多5个)
    const toShow = state.recommendedInterventions.slice(0, 5);
    container.innerHTML = toShow.map(item => `
        <button class="quick-btn" data-id="${item.id}" title="${item.content.slice(0, 50)}...">
            ${item.title}
        </button>
    `).join('');

    // 添加点击事件
    container.querySelectorAll('.quick-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = btn.dataset.id;
            const intervention = state.interventionLibrary.find(i => i.id === id);
            if (intervention) {
                applyPresetToInput(intervention.content);
            }
        });
    });

    // 更新更多列表
    const moreList = document.getElementById('more-intervention-list');
    if (moreList) {
        const remaining = state.allInterventions.filter(i =>
            !state.recommendedInterventions.slice(0, 5).some(r => r.id === i.id)
        );
        moreList.innerHTML = remaining.map(item => `
            <button class="quick-btn" data-id="${item.id}" title="${item.content.slice(0, 50)}...">
                ${item.title}
            </button>
        `).join('');

        moreList.querySelectorAll('.quick-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const id = btn.dataset.id;
                const intervention = state.interventionLibrary.find(i => i.id === id);
                if (intervention) {
                    applyPresetToInput(intervention.content);
                    // 关闭更多列表
                    toggleMoreInterventions();
                }
            });
        });
    }
}

function toggleMoreInterventions() {
    const moreDiv = document.getElementById('more-interventions');
    const moreBtn = document.getElementById('btn-more-interventions');

    state.showAllInterventions = !state.showAllInterventions;

    if (state.showAllInterventions) {
        moreDiv.classList.remove('hidden');
        moreBtn.classList.add('expanded');
        moreBtn.textContent = '收起 ▲';
    } else {
        moreDiv.classList.add('hidden');
        moreBtn.classList.remove('expanded');
        moreBtn.textContent = '更多 ▼';
    }
}

function openInterventionModal() {
    state.currentEditingIntervention = null;
    renderInterventionList();
    clearInterventionForm();
    document.getElementById('intervention-modal').classList.add('active');
}

function closeInterventionModal() {
    document.getElementById('intervention-modal').classList.remove('active');
    state.currentEditingIntervention = null;
}

// ============================================================
// 快速添加预设干预（主页内联表单）
// ============================================================

function toggleQuickAddForm() {
    const form = document.getElementById('quick-add-form');
    const isHidden = form.classList.contains('hidden');
    if (isHidden) {
        form.classList.remove('hidden');
        document.getElementById('quick-add-title').focus();
        document.getElementById('btn-add-quick-intervention').textContent = '✕ 收起';
    } else {
        closeQuickAddForm();
    }
}

function closeQuickAddForm() {
    const form = document.getElementById('quick-add-form');
    form.classList.add('hidden');
    document.getElementById('quick-add-title').value = '';
    document.getElementById('quick-add-content').value = '';
    document.getElementById('btn-add-quick-intervention').textContent = '＋ 添加';
}

async function saveQuickIntervention() {
    const title = document.getElementById('quick-add-title').value.trim();
    const content = document.getElementById('quick-add-content').value.trim();

    if (!title || !content) {
        alert('请输入标题和内容');
        return;
    }

    try {
        const result = await apiCall('/api/interventions', 'POST', {
            title, content, category: '自定义', tags: []
        });

        if (result.success) {
            await loadInterventions();
            closeQuickAddForm();
            // 滚动到预设区域顶部
            document.getElementById('quick-btn-list')?.scrollIntoView({ behavior: 'smooth' });
        }
    } catch (error) {
        console.error('保存失败:', error);
        alert('保存失败: ' + (error.message || '网络错误'));
    }
}

// R24：把预设文案填入输入框；若含【】占位符则提示用户替换为真实姓名
function applyPresetToInput(content) {
    const input = document.getElementById('intervention-input');
    if (!input) return;
    input.value = content || '';
    const hint = document.getElementById('preset-placeholder-hint');
    if (hint) {
        const has = /【[^】]+】/.test(content || '');
        hint.textContent = has
            ? '⚠️ 本条预设含【】占位符（【欺凌者】【受害者】【旁观者】），发送前请替换为对应学生的真实姓名。'
            : '';
        hint.classList.toggle('hidden', !has);
    }
    input.focus();
}

function renderInterventionList() {
    const container = document.getElementById('intervention-list');
    if (!container) return;

    container.innerHTML = state.interventionLibrary.map(item => `
        <div class="intervention-list-item ${state.currentEditingIntervention?.id === item.id ? 'active' : ''}"
             data-id="${item.id}">
            <span class="item-title">${item.title}</span>
            ${item.id.startsWith('custom_') ? '<span class="item-delete" data-delete="' + item.id + '">✕</span>' : ''}
        </div>
    `).join('');

    // 添加点击事件
    container.querySelectorAll('.intervention-list-item').forEach(item => {
        item.addEventListener('click', (e) => {
            if (e.target.classList.contains('item-delete')) {
                return; // 删除按钮单独处理
            }
            const id = item.dataset.id;
            selectIntervention(id);
        });
    });

    // 删除按钮事件
    container.querySelectorAll('.item-delete').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const id = btn.dataset.delete;
            if (confirm('确定要删除这个干预内容吗?')) {
                deleteInterventionById(id);
            }
        });
    });
}

function selectIntervention(id) {
    const intervention = state.interventionLibrary.find(i => i.id === id);
    if (!intervention) return;

    state.currentEditingIntervention = intervention;

    // 更新选中状态
    document.querySelectorAll('.intervention-list-item').forEach(item => {
        item.classList.toggle('active', item.dataset.id === id);
    });

    // 填充表单
    document.getElementById('edit-intervention-title').value = intervention.title || '';
    document.getElementById('edit-intervention-content').value = intervention.content || '';
    document.getElementById('edit-intervention-category').value = intervention.category || '';
    document.getElementById('edit-intervention-tags').value = (intervention.tags || []).join('、');

    // 显示/隐藏删除按钮
    const deleteBtn = document.getElementById('btn-delete-intervention');
    const infoDiv = document.getElementById('edit-intervention-info');

    if (intervention.id.startsWith('custom_')) {
        deleteBtn.classList.remove('hidden');
        infoDiv.classList.add('hidden');
    } else {
        deleteBtn.classList.add('hidden');
        infoDiv.classList.remove('hidden');
    }
}

function clearInterventionForm() {
    document.getElementById('edit-intervention-title').value = '';
    document.getElementById('edit-intervention-content').value = '';
    document.getElementById('edit-intervention-category').value = '';
    document.getElementById('edit-intervention-tags').value = '';
    document.getElementById('btn-delete-intervention').classList.add('hidden');
    document.getElementById('edit-intervention-info').classList.add('hidden');
}

function addNewIntervention() {
    state.currentEditingIntervention = { id: 'new', title: '', content: '', category: '自定义', tags: [] };
    clearInterventionForm();
    document.getElementById('edit-intervention-title').focus();

    // 更新列表选中状态
    document.querySelectorAll('.intervention-list-item').forEach(item => {
        item.classList.remove('active');
    });
}

async function saveIntervention() {
    const title = document.getElementById('edit-intervention-title').value.trim();
    const content = document.getElementById('edit-intervention-content').value.trim();
    const category = document.getElementById('edit-intervention-category').value.trim() || '自定义';
    const tagsText = document.getElementById('edit-intervention-tags').value.trim();
    const tags = tagsText ? tagsText.split(/[、,,]/).map(t => t.trim()).filter(t => t) : [];

    if (!title || !content) {
        alert('请输入标题和内容');
        return;
    }

    try {
        if (state.currentEditingIntervention?.id === 'new') {
            // 新建
            const result = await apiCall('/api/interventions', 'POST', {
                title, content, category, tags
            });

            if (result.success) {
                await loadInterventions();
                closeInterventionModal();
                alert('添加成功!');
            }
        } else if (state.currentEditingIntervention?.id?.startsWith('custom_')) {
            // 更新自定义干预
            const result = await apiCall(`/api/interventions/${state.currentEditingIntervention.id}`, 'PUT', {
                title, content, category, tags
            });

            if (result.success) {
                await loadInterventions();
                closeInterventionModal();
                alert('保存成功!');
            }
        } else {
            alert('系统内置干预内容不能修改');
        }
    } catch (error) {
        console.error('保存失败:', error);
        alert('保存失败: ' + error.message);
    }
}

async function deleteIntervention() {
    if (!state.currentEditingIntervention?.id?.startsWith('custom_')) {
        alert('系统内置干预内容不能删除');
        return;
    }

    if (!confirm('确定要删除这个干预内容吗?')) return;

    try {
        const result = await apiCall(`/api/interventions/${state.currentEditingIntervention.id}`, 'DELETE');

        if (result.success) {
            state.currentEditingIntervention = null;
            await loadInterventions();
            renderInterventionList();
            clearInterventionForm();
            alert('删除成功!');
        }
    } catch (error) {
        console.error('删除失败:', error);
        alert('删除失败: ' + error.message);
    }
}

async function deleteInterventionById(id) {
    try {
        const result = await apiCall(`/api/interventions/${id}`, 'DELETE');

        if (result.success) {
            if (state.currentEditingIntervention?.id === id) {
                state.currentEditingIntervention = null;
                clearInterventionForm();
            }
            await loadInterventions();
            renderInterventionList();
        }
    } catch (error) {
        console.error('删除失败:', error);
        alert('删除失败: ' + error.message);
    }
}

// 更新推荐干预(根据当前对话状态)
async function updateRecommendedInterventions() {
    const distress = parseFloat(document.getElementById('value-distress')?.textContent || '50%') / 100;

    try {
        const result = await apiCall(`/api/interventions/recommend?level=${state.level}&distress=${distress}`, 'GET');
        state.recommendedInterventions = result.recommended || [];
        state.allInterventions = result.all || [];
        renderQuickButtons();
    } catch (error) {
        console.error('更新推荐干预失败:', error);
    }
}

// ============================================================
// Step 4: OBVQ 风险仪表盘 + 六维雷达图
// ============================================================

const OBVQ_DIMS = ['physical', 'verbal', 'relational', 'cyber', 'power', 'prejudice'];
const OBVQ_DIM_LABELS = { physical: '身体', verbal: '言语', relational: '关系', cyber: '网络', power: '权力', prejudice: '偏见' };
const OBVQ_DIM_COLORS = { physical: '#f44336', verbal: '#FF9800', relational: '#9C27B0', cyber: '#2196F3', power: '#FF5722', prejudice: '#607D8B' };
function updateOBVQDashboard(status) {
    const totalScore = status.obvq_total_score || 0;
    const dimTotals = status.obvq_dimension_totals || [0,0,0,0,0,0];
    const dimAvgs = status.obvq_dimension_averages || [0,0,0,0,0,0];

    // 总分
    document.getElementById('obvq-risk-score').textContent = totalScore + ' 分';

    // 进度条 (0-80 映射)
    const bar = document.getElementById('obvq-risk-bar');
    const pct = Math.min(100, (totalScore / 80) * 100);
    bar.style.width = pct + '%';

    // 维度得分
    OBVQ_DIMS.forEach((dim, i) => {
        const el = document.getElementById('dim-' + dim);
        if (el) el.textContent = dimTotals[i];
    });

    // 雷达图
    drawOBVQRadar(dimAvgs);
}

function drawOBVQRadar(dimAvgs) {
    const canvas = document.getElementById('obvq-radar');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    const cx = W / 2, cy = H / 2;
    const R = Math.min(W, H) / 2 - 30; // 留边距
    const n = 6;

    ctx.clearRect(0, 0, W, H);

    // 背景网格(3层)
    for (let ring = 1; ring <= 3; ring++) {
        const r = R * ring / 3;
        ctx.beginPath();
        for (let i = 0; i <= n; i++) {
            const angle = (Math.PI * 2 * i / n) - Math.PI / 2;
            const x = cx + r * Math.cos(angle);
            const y = cy + r * Math.sin(angle);
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.strokeStyle = '#ddd';
        ctx.lineWidth = 0.5;
        ctx.stroke();
    }

    // 轴线
    for (let i = 0; i < n; i++) {
        const angle = (Math.PI * 2 * i / n) - Math.PI / 2;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + R * Math.cos(angle), cy + R * Math.sin(angle));
        ctx.strokeStyle = '#ccc';
        ctx.lineWidth = 0.5;
        ctx.stroke();
    }

    // 数据区域
    ctx.beginPath();
    for (let i = 0; i <= n; i++) {
        const idx = i % n;
        const val = Math.min(1, dimAvgs[idx] || 0);
        const r = R * val;
        const angle = (Math.PI * 2 * idx / n) - Math.PI / 2;
        const x = cx + r * Math.cos(angle);
        const y = cy + r * Math.sin(angle);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fillStyle = 'rgba(33, 150, 243, 0.2)';
    ctx.fill();
    ctx.strokeStyle = '#2196F3';
    ctx.lineWidth = 2;
    ctx.stroke();

    // 数据点
    for (let i = 0; i < n; i++) {
        const val = Math.min(1, dimAvgs[i] || 0);
        const r = R * val;
        const angle = (Math.PI * 2 * i / n) - Math.PI / 2;
        const x = cx + r * Math.cos(angle);
        const y = cy + r * Math.sin(angle);
        ctx.beginPath();
        ctx.arc(x, y, 3, 0, Math.PI * 2);
        ctx.fillStyle = OBVQ_DIM_COLORS[OBVQ_DIMS[i]];
        ctx.fill();
    }

    // 标签
    ctx.font = '11px sans-serif';
    ctx.fillStyle = '#555';
    ctx.textAlign = 'center';
    for (let i = 0; i < n; i++) {
        const angle = (Math.PI * 2 * i / n) - Math.PI / 2;
        const lx = cx + (R + 20) * Math.cos(angle);
        const ly = cy + (R + 20) * Math.sin(angle);
        ctx.fillText(OBVQ_DIM_LABELS[OBVQ_DIMS[i]], lx, ly + 4);
    }
}



// ============================================================
// 下载功能
// ============================================================

// 下载互动日志(Markdown格式)
async function downloadInteractionLog() {
    if (!state.sessionId) {
        alert('没有活跃的会话');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/session/${state.sessionId}/download-log`, {
            headers: { 'Authorization': `Bearer ${auth.token}` }
        });
        if (!response.ok) {
            throw new Error('下载失败: ' + response.status);
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `互动日志_${state.sessionId}.md`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    } catch (error) {
        console.error('下载互动日志失败:', error);
        alert('下载互动日志失败: ' + error.message);
    }
}

// 下载文本报告
async function downloadTextReportFromServer() {
    if (!state.sessionId) {
        alert('没有活跃的会话');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/session/${state.sessionId}/download-report-text`, {
            headers: { 'Authorization': `Bearer ${auth.token}` }
        });
        if (!response.ok) {
            throw new Error('下载失败: ' + response.status);
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `评估报告_${state.sessionId}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    } catch (error) {
        console.error('下载报告失败:', error);
        alert('下载报告失败: ' + error.message);
    }
}

// 初始化下载按钮事件监听
function initDownloadButtons() {
    const btnDownloadReport = document.getElementById('btn-download-report');
    if (btnDownloadReport) {
        btnDownloadReport.addEventListener('click', downloadTextReportFromServer);
    }

    const btnDownloadLog = document.getElementById('btn-download-log');
    if (btnDownloadLog) {
        btnDownloadLog.addEventListener('click', downloadInteractionLog);
    }
}

// 页面加载完成后初始化下载按钮
document.addEventListener('DOMContentLoaded', function() {
    setTimeout(initDownloadButtons, 500);
});




