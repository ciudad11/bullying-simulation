/**
 * 管理后台 JavaScript
 * ============================================================
 */

const API_BASE = '';
let adminToken = null;
let adminUser = null;
let currentReportSessionId = null;  // 当前查看报告的会话ID

// R11：评分统一渲染（null = 待评估，禁止按数值猜单位）
function fmtQualityScore(v) {
    if (v === null || v === undefined) return '待评估';
    const num = Number(v);
    if (!Number.isFinite(num)) return '待评估';
    return Math.round(num) + '/100';
}

// ============================================================
// 初始化
// ============================================================

async function init() {
    // 验证管理员身份
    const saved = localStorage.getItem('auth_token');
    if (!saved) {
        window.location.href = '/';
        return;
    }

    adminToken = saved;
    try {
        const resp = await fetch(`${API_BASE}/api/auth/me`, {
            headers: { 'Authorization': `Bearer ${adminToken}` }
        });
        if (!resp.ok) {
            localStorage.removeItem('auth_token');
            window.location.href = '/';
            return;
        }
        const data = await resp.json();
        adminUser = data.user;

        if (!adminUser.is_admin) {
            showToast('无管理员权限，已跳转回主页', 'error');
            setTimeout(() => { window.location.href = '/'; }, 1500);
            return;
        }

        document.getElementById('admin-user').textContent =
            adminUser.display_name || adminUser.username;
    } catch (e) {
        window.location.href = '/';
        return;
    }

    // 加载数据
    await loadStats();
    await loadSessions();
    await loadUsers();

    // 事件监听
    initEvents();
}

// ============================================================
// API 调用
// ============================================================

async function api(path) {
    const resp = await fetch(`${API_BASE}${path}`, {
        headers: { 'Authorization': `Bearer ${adminToken}` }
    });
    if (resp.status === 401) {
        localStorage.removeItem('auth_token');
        window.location.href = '/';
        return null;
    }
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        showToast(err.detail || '请求失败', 'error');
        return null;
    }
    return resp.json();
}

// ============================================================
// 统计概览
// ============================================================

async function loadStats() {
    const data = await api('/api/admin/stats');
    if (!data) return;

    document.getElementById('stat-users').textContent = data.total_users;
    document.getElementById('stat-sessions').textContent = data.total_sessions;
    document.getElementById('stat-reports').textContent = data.total_reports;
    document.getElementById('stat-today-users').textContent = data.active_users_today;
    document.getElementById('stat-today-sessions').textContent = data.sessions_today;
}

// ============================================================
// 会话列表
// ============================================================

let sessionsPage = 0;
const sessionsPerPage = 20;
let allSessions = [];

async function loadSessions(page = 0) {
    sessionsPage = page;
    const offset = page * sessionsPerPage;
    const data = await api(`/api/admin/sessions?limit=${sessionsPerPage}&offset=${offset}`);
    if (!data) return;

    allSessions = data.sessions;
    renderSessions(data.sessions);
    renderSessionsPager(data.total);
}

function renderSessions(sessions) {
    const tbody = document.getElementById('sessions-tbody');
    if (sessions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-hint">暂无会话记录</td></tr>';
        return;
    }

    tbody.innerHTML = sessions.map(s => {
        const statusBadge = {
            'running': '<span class="badge badge-running">运行中</span>',
            'completed': '<span class="badge badge-completed">已完成</span>',
            'stopped': '<span class="badge badge-stopped">已停止</span>',
            'auto_ended': '<span class="badge badge-auto">自动结束</span>',
            'abandoned': '<span class="badge badge-abandoned">已丢弃</span>',
        }[s.status] || `<span class="badge">${s.status}</span>`;

        const reportBtn = s.has_report
            ? '<button class="btn-sm btn-sm-success" onclick="viewSessionReport(\'' + s.id + '\')" title="点击查看报告">📊 报告</button>'
            : (s.status === 'running' || s.status === 'abandoned')
                ? '<button class="btn-sm btn-sm-outline" onclick="viewSessionReport(\'' + s.id + '\')" title="查看实时快照">📊 查看</button>'
                : '<span class="badge badge-no">无</span>';

        const lvl = `<span class="level-dot level-${s.level}"></span>L${s.level}`;

        const time = s.created_at ? s.created_at.slice(0, 16).replace('T', ' ') : '-';

        const cleanupBtn = (s.status === 'running' || s.status === 'abandoned')
            ? '<button class="btn-sm btn-sm-danger" onclick="cleanupSession(\'' + s.id + '\')" title="标记为已丢弃" style="margin-left:4px">🧹</button>'
            : '';

        return `<tr>
            <td><strong>${esc(s.display_name || s.username)}</strong><br><small style="color:#999">${esc(s.username)}</small></td>
            <td>${esc(s.scenario_title || s.scenario_id || '-')}</td>
            <td>${lvl}</td>
            <td>${statusBadge}</td>
            <td>${reportBtn}</td>
            <td>${time}</td>
            <td>
                <button class="btn-sm btn-sm-primary" onclick="viewSessionDetail('${s.id}')">详情</button>
                <button class="btn-sm btn-sm-outline" onclick="viewSessionLog('${s.id}')" style="margin-left:4px">日志</button>${cleanupBtn}
            </td>
        </tr>`;
    }).join('');
}

function renderSessionsPager(total) {
    const totalPages = Math.ceil(total / sessionsPerPage);
    const pager = document.getElementById('sessions-pager');
    pager.innerHTML = `
        <button ${sessionsPage === 0 ? 'disabled' : ''} onclick="loadSessions(${sessionsPage - 1})">上一页</button>
        <span>${sessionsPage + 1} / ${totalPages || 1}</span>
        <button ${sessionsPage >= totalPages - 1 ? 'disabled' : ''} onclick="loadSessions(${sessionsPage + 1})">下一页</button>
        <span style="color:#999">共 ${total} 条</span>
    `;
}

async function viewSessionDetail(sessionId) {
    // 加载用户详情
    const session = allSessions.find(s => s.id === sessionId);
    if (!session) return;
    const data = await api(`/api/admin/users/${session.user_id}`);
    if (!data) return;
    showUserDetail(data);
}

async function viewSessionReport(sessionId) {
    currentReportSessionId = sessionId;
    const data = await api(`/api/admin/sessions/${sessionId}/report`);
    if (!data) {
        showToast('报告不存在，可能尚未生成', 'warning');
        return;
    }
    // 复用主页面的报告渲染
    const summary = data.summary || {};
    const assessment = data.ability_assessment || {};
    const obvq = data.obvq_analysis || {};
    const interventionAnalysis = data.intervention_analysis || {};
    const trends = data.trends || {};

    const modal = document.getElementById('report-modal');
    if (!modal) {
        // 动态创建报告弹窗
        createReportModal();
    }

    const content = document.getElementById('report-content');
    content.innerHTML = renderAdminReport(data);
    document.getElementById('report-modal').style.display = 'flex';
}

function createReportModal() {
    const html = `
        <div class="modal" id="report-modal" style="display:none;">
            <div class="modal-content" style="max-width:800px; max-height:85vh; display:flex; flex-direction:column;">
                <div class="modal-header">
                    <span id="report-title">📊 实训评估报告</span>
                    <button class="close-btn" onclick="closeReportModal()">✕</button>
                </div>
                <div id="report-content" class="modal-body" style="flex:1; overflow-y:auto;"></div>
                <div class="modal-footer" style="display:flex; gap:8px; padding:12px 20px; border-top:1px solid #eee;">
                    <button class="btn btn-secondary" id="btn-admin-download-report" onclick="downloadAdminReport()">📥 下载报告</button>
                    <button class="btn btn-secondary" id="btn-admin-download-log" onclick="downloadAdminLog()">📋 下载互动日志</button>
                </div>
            </div>
        </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
}

function closeReportModal() {
    const modal = document.getElementById('report-modal');
    if (modal) modal.style.display = 'none';
}

// 下载报告（管理后台）
async function downloadAdminReport() {
    if (!currentReportSessionId) return;
    const data = await api(`/api/admin/sessions/${currentReportSessionId}/report`);
    if (!data) {
        showToast('无法获取报告数据', 'error');
        return;
    }
    const text = formatAdminReportAsText(data);
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `实训报告_${currentReportSessionId}.txt`;
    a.click();
    URL.revokeObjectURL(url);
}

// 清理单个会话
async function cleanupSession(sessionId) {
    if (!confirm('确定将此会话标记为已丢弃？')) return;
    const resp = await fetch(`/api/admin/sessions/${sessionId}/cleanup`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('auth_token')}` },
    });
    if (!resp.ok) {
        showToast('操作失败: ' + resp.status, 'error');
        return;
    }
    showToast('已标记为已丢弃');
    loadSessions(sessionsPage);
}

// 批量清理 abandoned 会话
async function batchCleanupSessions() {
    if (!confirm('确定要删除所有已丢弃(abandoned)的会话及其关联数据？此操作不可撤销。')) return;
    const resp = await fetch('/api/admin/sessions/batch-cleanup', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${adminToken}` },
    });
    if (!resp.ok) {
        showToast('操作失败: ' + resp.status, 'error');
        return;
    }
    const data = await resp.json();
    showToast(`已清理 ${data.cleaned} 条 abandoned 会话`);
    loadSessions(sessionsPage);
}

// 下载互动日志（管理后台）
async function downloadAdminLog() {
    if (!currentReportSessionId) return;
    const data = await api(`/api/admin/sessions/${currentReportSessionId}/log`);
    if (!data) {
        showToast('无法获取日志数据', 'error');
        return;
    }
    const markdown = data.log_markdown || data.log || '（无日志内容）';
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `互动日志_${currentReportSessionId}.md`;
    a.click();
    URL.revokeObjectURL(url);
}

// 格式化报告为纯文本
function formatAdminReportAsText(report) {
    const summary = report.summary || {};
    const assessment = report.ability_assessment || {};
    const obvq = report.obvq_analysis || {};
    const interventionAnalysis = report.intervention_analysis || {};
    const trends = report.trends || {};
    const interventions = report.interventions || [];
    const pct = v => Math.round((v || 0) * 100) + '%';
    const trendSign = v => v < -0.02 ? '↓' : v > 0.02 ? '↑' : '→';

    let lines = [];
    lines.push('========================================');
    lines.push('       教师实训评估报告');
    lines.push('========================================');
    lines.push('');
    lines.push('【霸凌情况】');
    lines.push(`  总消息数: ${summary.total_messages || 0}`);
    lines.push(`  攻击性事件: ${summary.hostile_messages || 0} 次`);
    lines.push(`  霸凌强度: ${pct(summary.bullying_intensity)}`);
    lines.push(`  受害者压力: ${pct(summary.victim_distress)}`);

    const totalDecisions = interventionAnalysis.total_decisions || 0;
    const skipCount = interventionAnalysis.skip_count || 0;
    lines.push('');
    lines.push('【干预决策统计】');
    lines.push(`  总决策次数: ${totalDecisions}`);
    lines.push(`  已干预: ${summary.intervention_count || 0}次`);
    lines.push(`  已跳过: ${skipCount}次`);
    lines.push('');
    lines.push('【教师干预】');
    lines.push(`  干预次数: ${summary.intervention_count || 0}`);
    lines.push(`  教师干预质量评分（完整互动量规、等权平均）: ${Math.round(summary.intervention_effectiveness || 0)}/100`);

    if (trends.summary) {
        lines.push('');
        lines.push('【指标变化趋势（模拟结果，非能力评价）】');
        lines.push(`  ${trends.summary}`);
        lines.push(`  霸凌强度: ${pct(trends.early_bullying)} → ${pct(trends.late_bullying)} ${trendSign(trends.bullying_change)} ${Math.abs((trends.bullying_change || 0) * 100).toFixed(1)} 个百分点`);
        lines.push(`  受害者压力: ${pct(trends.early_distress)} → ${pct(trends.late_distress)} ${trendSign(trends.distress_change)} ${Math.abs((trends.distress_change || 0) * 100).toFixed(1)} 个百分点`);
    }

    if (obvq.dimension_analysis && obvq.dimension_analysis.length) {
        lines.push('');
        lines.push('【本次模拟欺凌行为类型统计（项目分类，非 OBVQ-R 标准量表）】');
        lines.push(`  类型总分: ${obvq.total_score || 0}分`);
        lines.push('  计分方式: 事件按强度映射为 0–4 分并累加到所有命中类别；总分 = min(80, 六类累计分之和)；同一事件可重复计入多个类别。');
        lines.push('  说明: 该统计用于刻画欺凌行为类型，不用于教师 D1–D7 能力评分。');
        obvq.dimension_analysis.forEach(d => {
            lines.push(`  ${d.name}: ${d.total || 0}分 (${d.level || '-'}风险) - ${d.description || ''}`);
        });
        if (obvq.recommendation) {
            lines.push(`  综合建议: ${obvq.recommendation}`);
        }
    }

    lines.push('');
    lines.push('【干预效果分析与建议】');
    lines.push(`  ${interventionAnalysis.summary || '本次模拟未进行任何干预。'}`);

    if (interventionAnalysis.suggestions && interventionAnalysis.suggestions.length) {
        lines.push('  改进建议:');
        interventionAnalysis.suggestions.forEach((s, i) => {
            lines.push(`    ${i + 1}. ${s}`);
        });
    }

    if (interventionAnalysis.action_plan && interventionAnalysis.action_plan.length) {
        lines.push('  改进行动计划:');
        interventionAnalysis.action_plan.forEach((a, i) => {
            lines.push(`    ☐ ${a}`);
        });
    }

    if (interventions.length > 0) {
        lines.push('');
        lines.push('【干预详情】');
        interventions.forEach((iv, i) => {
            lines.push(`  第${i + 1}次: "${(iv.content || '').substring(0, 60)}"`);
            lines.push(`    效果: ${fmtQualityScore(iv.quality_score)} 受害者关怀: ${pct(iv.victim_care_score)}`);
        });
    }

    lines.push('');
    lines.push('【能力评估】');
    lines.push(`  发现能力: ${assessment.discovery?.description || '未评估'}`);
    lines.push(`  干预能力: ${assessment.intervention?.description || '未评估'}`);
    // R21：受害者保护（D2）——新报告用 victim_protection，历史报告回退读 empathy
    lines.push(`  受害者保护（D2）: ${(assessment.victim_protection || assessment.empathy)?.description || '未评估'}`);

    // R21：数据说明（与用户端同源 report_data）
    const rd = report.report_data || {};
    if (rd && rd.data_notes) {
        const sn = rd.summary || {};
        lines.push('');
        lines.push('【数据说明】');
        lines.push(`  评分状态: ${rd.scoring_status || ''}`);
        lines.push(`  量规版本: ${rd.rubric_version || ''} | 提示词版本: ${rd.scoring_prompt_version || ''}`);
        lines.push(`  有效评分维度: ${sn.rated_count || 0} | 无可观察（N/O）: ${sn.no_opportunity_count || 0} | `
                   + `证据不足: ${sn.insufficient_evidence_count || 0} | 未评估: ${sn.not_assessed_count || 0}`);
        lines.push(`  总分口径: ${(rd.data_notes || {}).score_formula || ''}`);
        ((rd.data_notes || {}).limitations || []).forEach(l => lines.push(`  · ${l}`));
    }

    lines.push('');
    lines.push('========================================');

    return lines.join('\n');
}

function renderAdminReport(report) {
    const summary = report.summary || {};
    const assessment = report.ability_assessment || {};
    const obvq = report.obvq_analysis || {};
    const interventionAnalysis = report.intervention_analysis || {};
    const trends = report.trends || {};
    const interventions = report.interventions || [];
    // R21：规范化报告合同（与用户端同一份 report_data）
    const rd = report.report_data || {};
    // R21：历史报告兼容——旧报告用 early/mid/late，新报告用 baseline/final
    const obvqStart = t => (t.baseline !== undefined ? t.baseline : (t.early || 0));
    const obvqEnd = t => (t.final !== undefined ? t.final : (t.late || 0));
    // R21：历史报告兼容——旧报告用 empathy，新报告用 victim_protection
    const victimProtection = assessment.victim_protection || assessment.empathy;

    const pct = v => Math.round((v || 0) * 100);
    const getColor = (v, threshold) => v > threshold ? 'danger' : v > threshold / 2 ? 'warning' : 'good';
    const trendArrow = v => v < -0.02 ? '&darr;' : v > 0.02 ? '&uarr;' : '&rarr;';

    const totalDecisions = interventionAnalysis.total_decisions || 0;
    const skipCount = interventionAnalysis.skip_count || 0;
    const interventionCount = summary.intervention_count || 0;

    return `
        <h3>📌 霸凌情况</h3>
        <div class="metric"><span>总消息数</span><span class="value">${summary.total_messages || 0}</span></div>
        <div class="metric"><span>攻击性事件</span><span class="value danger">${summary.hostile_messages || 0}次</span></div>
        <div class="metric"><span>霸凌强度</span><span class="value ${getColor(summary.bullying_intensity, 0.5)}">${pct(summary.bullying_intensity)}%</span></div>
        <div class="metric"><span>受害者压力</span><span class="value ${getColor(summary.victim_distress, 0.4)}">${pct(summary.victim_distress)}%</span></div>

        <h3>👨‍🏫 教师干预决策</h3>
        <div class="metric"><span>总决策次数</span><span class="value">${totalDecisions}</span></div>
        <div class="metric"><span>已干预</span><span class="value good">${interventionCount}次</span></div>
        <div class="metric"><span>已跳过</span><span class="value ${skipCount > 0 ? 'warning' : ''}">${skipCount}次</span></div>
        <div class="metric"><span>教师干预质量评分</span><span class="value ${(summary.intervention_effectiveness || 0) >= 50 ? 'good' : 'warning'}">${Math.round(summary.intervention_effectiveness || 0)}/100</span></div>

        ${trends.summary ? `
        <h3>📈 指标变化趋势</h3>
        <p style="font-size:0.8125rem;color:#555;margin:4px 0;">${trends.summary}</p>
        <div style="display:flex;gap:16px;margin:8px 0;">
            <div style="flex:1;background:#f8f8f8;border-radius:6px;padding:8px;text-align:center;">
                <div style="font-size:0.75rem;color:#888;">起点霸凌强度</div>
                <div style="font-size:1.25rem;font-weight:600;">${pct(trends.early_bullying)}%</div>
                <div style="font-size:0.75rem;color:#999;">→ 最终 ${pct(trends.late_bullying)}%</div>
                <div style="color:${trends.bullying_change > 0 ? '#f44336' : '#4CAF50'};">${trendArrow(trends.bullying_change)} ${Math.abs((trends.bullying_change || 0) * 100).toFixed(1)} 个百分点</div>
            </div>
            <div style="flex:1;background:#f8f8f8;border-radius:6px;padding:8px;text-align:center;">
                <div style="font-size:0.75rem;color:#888;">起点受害者压力</div>
                <div style="font-size:1.25rem;font-weight:600;">${pct(trends.early_distress)}%</div>
                <div style="font-size:0.75rem;color:#999;">→ 最终 ${pct(trends.late_distress)}%</div>
                <div style="color:${trends.distress_change > 0 ? '#f44336' : '#4CAF50'};">${trendArrow(trends.distress_change)} ${Math.abs((trends.distress_change || 0) * 100).toFixed(1)} 个百分点</div>
            </div>
        </div>
        ${trends.obvq_trends && trends.obvq_trends.filter(t => obvqStart(t) > 0 || obvqEnd(t) > 0).length > 0 ? `
        <div style="margin-top:12px;">
            <div style="font-size:0.8125rem;font-weight:600;margin-bottom:6px;">📊 六类欺凌类型变化（起点→终点）</div>
            <table style="width:100%;border-collapse:collapse;font-size:0.75rem;">
                <tr style="background:#f0f0f0;">
                    <th style="padding:4px 6px;text-align:left;">维度</th>
                    <th style="padding:4px 6px;text-align:center;">起点</th>
                    <th style="padding:4px 6px;text-align:center;">终点</th>
                    <th style="padding:4px 6px;text-align:center;">趋势</th>
                </tr>
                ${trends.obvq_trends.filter(t => obvqStart(t) > 0 || obvqEnd(t) > 0).map(t => `
                <tr style="border-bottom:1px solid #eee;">
                    <td style="padding:3px 6px;">${t.name}</td>
                    <td style="padding:3px 6px;text-align:center;">${obvqStart(t)}分</td>
                    <td style="padding:3px 6px;text-align:center;">${obvqEnd(t)}分</td>
                    <td style="padding:3px 6px;text-align:center;color:${t.direction === 'up' ? '#f44336' : t.direction === 'down' ? '#4CAF50' : '#999'};">
                        ${t.direction === 'up' ? '↑' : t.direction === 'down' ? '↓' : '→'}
                    </td>
                </tr>
                `).join('')}
            </table>
        </div>
        ` : ''}
        </div>
        ` : ''}

        ${obvq.dimension_analysis && obvq.dimension_analysis.length > 0 ? `
        <h3>📊 本次模拟欺凌行为类型统计（项目分类）</h3>
        <div class="metric"><span>类型总分（项目分类）</span><span class="value">${obvq.total_score || 0}分</span></div>
        <p style="font-size:0.75rem;color:#888;margin:4px 0;">计分方式：每次识别到的欺凌事件按强度映射为 0–4 分并累加到所有命中类别；总分 = min(80, 六类累计分之和)。同一事件可重复计入多个类别，六类分项之和可能大于总分。该统计不用于教师 D1–D7 能力评分。</p>
        ${obvq.dimension_analysis.map(dim => `
        <div class="metric"><span>${dim.name}</span><span class="value">${dim.total}分 (${dim.level}风险)</span></div>
        `).join('')}
        <p style="font-size:0.875rem;color:#555;margin:8px 0;padding:8px;background:#f5f5f5;border-radius:6px;"><strong>💡 综合建议: </strong>${obvq.recommendation || '无'}</p>
        ` : ''}

        <h3>🔍 干预效果分析与建议</h3>
        <p style="font-size:0.875rem;color:#555;margin:8px 0;">${interventionAnalysis.summary || '本次模拟未进行任何干预。'}</p>
        ${interventionAnalysis.suggestions && interventionAnalysis.suggestions.length > 0 ? `
        <div style="margin:8px 0;">
            <strong>改进建议:</strong>
            <ol style="margin:4px 0 0 16px;font-size:0.8125rem;color:#555;">
            ${interventionAnalysis.suggestions.map(s => `<li style="margin:2px 0;">${s}</li>`).join('')}
            </ol>
        </div>
        ` : ''}
        ${interventionAnalysis.action_plan && interventionAnalysis.action_plan.length > 0 ? `
        <div style="margin:8px 0;padding:8px;background:#e8f5e9;border-radius:6px;">
            <strong>📋 改进行动计划:</strong>
            <ul style="margin:4px 0 0 16px;font-size:0.8125rem;color:#2e7d32;">
            ${interventionAnalysis.action_plan.map(a => `<li style="margin:2px 0;">☐ ${a}</li>`).join('')}
            </ul>
        </div>
        ` : ''}

        <h3>🎯 能力评估</h3>
        <div class="metric"><span>发现能力</span><span class="value ${assessment.discovery?.level === 'excellent' ? 'good' : assessment.discovery?.level === 'good' ? 'good' : 'warning'}">${assessment.discovery?.description || '未评估'}</span></div>
        <div class="metric"><span>干预能力</span><span class="value ${assessment.intervention?.level === 'excellent' ? 'good' : assessment.intervention?.level === 'good' ? 'good' : 'warning'}">
            ${assessment.intervention?.description || ''}
        </span></div>
        <div class="metric"><span>受害者保护（D2）</span><span class="value ${victimProtection?.level === 'excellent' ? 'good' : victimProtection?.level === 'good' ? 'good' : 'warning'}">${victimProtection?.description || '未评估'}</span></div>

        ${rd && rd.risks && rd.risks.length ? `
        <h3>⚠ 关键风险检查</h3>
        ${rd.risks.map(r => `
        <div class="metric"><span>${escapeHtml(r.code || '')}（${escapeHtml(r.level || '')}）</span><span class="value">${escapeHtml((r.evidence_refs || []).join(' / '))}</span></div>
        ${r.recommended_correction ? `<p style="font-size:0.8125rem;color:#555;margin:2px 0 10px 0;">必须修正：${escapeHtml(r.recommended_correction)}</p>` : ''}
        `).join('')}
        ` : ''}

        ${rd && rd.data_notes ? `
        <h3>📋 数据说明</h3>
        <div class="metric"><span>评分状态</span><span class="value">${escapeHtml(rd.scoring_status || '')}</span></div>
        <div class="metric"><span>量规版本</span><span class="value">${escapeHtml(rd.rubric_version || '')}</span></div>
        <div class="metric"><span>报告ID / 合同版本 / 模型</span><span class="value">${escapeHtml(rd.report_id || '')} ｜ ${escapeHtml(rd.report_schema_version || '')} ｜ ${escapeHtml(rd.model_id || '') || '未记录'}</span></div>
        <div class="metric"><span>有效评分 / N-O / 证据不足 / 未评估</span><span class="value">${(rd.summary || {}).rated_count || 0} / ${(rd.summary || {}).no_opportunity_count || 0} / ${(rd.summary || {}).insufficient_evidence_count || 0} / ${(rd.summary || {}).not_assessed_count || 0}</span></div>
        <p style="font-size:0.8125rem;color:#555;margin:6px 0;">总分口径：${escapeHtml((rd.data_notes || {}).score_formula || '')}</p>
        ${((rd.data_notes || {}).limitations || []).map(l => `<p style="font-size:0.8125rem;color:#555;margin:2px 0;">· ${escapeHtml(l)}</p>`).join('')}
        ` : ''}


        ${rd && (rd.dimensions || []).length ? `
        <h3>🧾 七维评分明细（分数·证据·下一步）</h3>
        ${rd.dimensions.map(d => `
        <div style="border-left:3px solid #42a5f5;padding:6px 10px;margin:6px 0;background:#f7fbff;border-radius:0 6px 6px 0;">
            <div style="font-size:0.8125rem;font-weight:700;color:#0d47a1;">${escapeHtml(d.code || '')} ${escapeHtml(d.name || '')}｜${d.score_100 === null || d.score_100 === undefined ? '未计分' : escapeHtml(String(d.score_100)) + ' 分（' + escapeHtml(d.band || '-') + '）'}｜状态 ${escapeHtml(d.status || '')}</div>
            ${d.rationale ? `<div style="font-size:0.8125rem;color:#444;margin:2px 0;">专家判断：${escapeHtml(d.rationale)}</div>` : ''}
            ${(d.evidence_refs || []).length ? `<div style="font-size:0.8125rem;color:#444;margin:2px 0;">原文证据：${escapeHtml(d.evidence_refs.join(' / '))}</div>` : ''}
            ${(d.missing_behaviors || []).length ? `<div style="font-size:0.8125rem;color:#c62828;margin:2px 0;">缺失行为：${escapeHtml(d.missing_behaviors.join('；'))}</div>` : ''}
            ${d.next_target ? `<div style="font-size:0.8125rem;color:#1b5e20;margin:2px 0;">下一步目标：${escapeHtml(d.next_target)}</div>` : ''}
            ${d.sample_utterance ? `<div style="font-size:0.8125rem;color:#444;margin:2px 0;">示范表达：${escapeHtml(d.sample_utterance)}</div>` : ''}
            ${d.retest_criteria ? `<div style="font-size:0.8125rem;color:#0d47a1;margin:2px 0;">复测标准：${escapeHtml(d.retest_criteria)}</div>` : ''}
        </div>`).join('')}
        ` : ''}

        ${rd && (rd.training_plan || []).length ? `
        <h3>🎯 训练计划与复测</h3>
        ${rd.training_plan.map(p => `
        <div style="border-left:3px solid #8e24aa;padding:6px 10px;margin:6px 0;background:#fbf7ff;border-radius:0 6px 6px 0;">
            <div style="font-size:0.8125rem;font-weight:700;color:#4527a0;">优先级 ${p.priority} · ${escapeHtml(p.dimension || '')}｜${escapeHtml(p.objective || '')}</div>
            ${(p.actions || []).map(a => `<div style="font-size:0.8125rem;color:#444;margin:2px 0;">步骤：${escapeHtml(a)}</div>`).join('')}
            ${p.sample_utterance ? `<div style="font-size:0.8125rem;color:#444;margin:2px 0;">示范表达：${escapeHtml(p.sample_utterance)}</div>` : ''}
            ${(p.safety_constraints || []).map(c => `<div style="font-size:0.8125rem;color:#c62828;margin:2px 0;">安全约束：${escapeHtml(c)}</div>`).join('')}
            ${p.success_criteria ? `<div style="font-size:0.8125rem;color:#1b5e20;margin:2px 0;">达标标准：${escapeHtml(p.success_criteria)}</div>` : ''}
            ${p.retest_task ? `<div style="font-size:0.8125rem;color:#0d47a1;margin:2px 0;">复测任务：${escapeHtml(p.retest_task)}</div>` : ''}
        </div>`).join('')}
        ` : ''}

        ${rd && (rd.interaction_timeline || []).length ? `
        <h3>🕰 互动时间轴</h3>
        ${rd.interaction_timeline.map(t => `
        <div style="border-left:3px solid #00897b;padding:6px 10px;margin:6px 0;background:#f6fbfb;border-radius:0 6px 6px 0;">
            <div style="font-size:0.8125rem;font-weight:700;color:#00695c;">${escapeHtml(t.turn_id || '')}｜第 ${escapeHtml(String(t.round))} 轮｜${escapeHtml(({teacher:'教师',bully:'欺凌者',victim:'受害者',bystander_a:'旁观者A',bystander_b:'旁观者B'})[t.speaker_role] || t.speaker_role || '-')}</div>
            ${t.text_excerpt ? `<div style="font-size:0.8125rem;color:#444;margin:2px 0;">${escapeHtml(t.text_excerpt)}</div>` : ''}
            ${t.teacher_action ? `<div style="font-size:0.8125rem;color:#444;margin:2px 0;">教师行动：${escapeHtml(t.teacher_action)}</div>` : ''}
            ${t.student_response ? `<div style="font-size:0.8125rem;color:#444;margin:2px 0;">学生反馈：${escapeHtml(t.student_response)}</div>` : ''}
            ${t.expert_interpretation ? `<div style="font-size:0.8125rem;color:#4527a0;margin:2px 0;">专家解读：${escapeHtml(t.expert_interpretation)}</div>` : ''}
        </div>`).join('')}
        ` : ''}

        ${rd && (rd.reflection_questions || []).length ? `
        <h3>💭 反思问题</h3>
        ${rd.reflection_questions.map(q => `<p style="font-size:0.8125rem;color:#555;margin:2px 0;">· ${escapeHtml(q)}</p>`).join('')}
        ` : ''}

        ${interventions.length > 0 ? `
        <h3>📋 干预详情</h3>
        ${interventions.map((iv, i) => `
            <div class="metric">
                <span>干预 ${i + 1}</span>
                <span class="value ${(iv.quality_score || 0) >= 50 ? 'good' : 'warning'}">${fmtQualityScore(iv.quality_score)}</span>
            </div>
            <p style="font-size:0.8125rem;color:#666;margin:4px 0 12px 0;">"${escapeHtml(iv.content || '')}"</p>
        `).join('')}
        ` : '<h3>📋 干预详情</h3><p style="color:#999;font-size:0.875rem;">无干预记录</p>'}
    `;
}

async function viewSessionLog(sessionId) {
    const data = await api(`/api/admin/sessions/${sessionId}/log`);
    if (!data) {
        showToast('日志不存在，可能尚未保存', 'warning');
        return;
    }

    const logBody = document.getElementById('log-body');
    const markdown = data.log_markdown || data.log || '（无日志内容）';
    logBody.innerHTML = escapeHtml(markdown);

    document.getElementById('log-modal').classList.remove('hidden');
}

// ============================================================
// 用户列表
// ============================================================

let allUsers = [];

async function loadUsers() {
    const data = await api('/api/admin/users');
    if (!data) return;

    allUsers = data.users;
    renderUsers(data.users);
}

function renderUsers(users) {
    const tbody = document.getElementById('users-tbody');
    if (users.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-hint">暂无用户</td></tr>';
        return;
    }

    tbody.innerHTML = users.map(u => {
        const roleBadge = u.is_admin
            ? '<span class="badge badge-admin">管理员</span>'
            : '<span class="badge badge-user">普通用户</span>';

        return `<tr>
            <td><strong>${esc(u.username)}</strong></td>
            <td>${esc(u.display_name || '-')}</td>
            <td>${esc(u.email || '-')}</td>
            <td>${roleBadge}</td>
            <td>${u.session_count || 0}</td>
            <td>${(u.created_at || '').slice(0, 16).replace('T', ' ')}</td>
            <td>
                <button class="btn-sm btn-sm-primary" onclick="openUserDetail('${u.id}')">详情</button>
            </td>
        </tr>`;
    }).join('');
}

async function openUserDetail(userId) {
    const data = await api(`/api/admin/users/${userId}`);
    if (!data) return;
    showUserDetail(data);
}

function showUserDetail(data) {
    const body = document.getElementById('user-detail-body');
    document.getElementById('modal-title').textContent =
        `用户详情: ${data.display_name || data.username}`;

    const sessions = data.sessions || [];

    let sessionsHtml = '';
    if (sessions.length === 0) {
        sessionsHtml = '<p class="empty-hint">暂无会话记录</p>';
    } else {
        sessionsHtml = `
            <table class="detail-mini-table">
                <thead><tr>
                    <th>场景</th><th>级别</th><th>状态</th><th>报告</th><th>时间</th>
                </tr></thead>
                <tbody>
                    ${sessions.map(s => {
                        const statusBadge = {
                            'running': '<span class="badge badge-running">运行中</span>',
                            'completed': '<span class="badge badge-completed">已完成</span>',
                            'stopped': '<span class="badge badge-stopped">已停止</span>',
                            'auto_ended': '<span class="badge badge-auto">自动结束</span>',
                        }[s.status] || `<span class="badge">${s.status}</span>`;

                        const hasReport = s.report ? '<span class="badge badge-yes">有</span>' : '<span class="badge badge-no">无</span>';

                        return `<tr>
                            <td>${esc(s.scenario_title || s.scenario_id || '-')}</td>
                            <td><span class="level-dot level-${s.level}"></span>L${s.level}</td>
                            <td>${statusBadge}</td>
                            <td>${hasReport}</td>
                            <td>${(s.created_at || '').slice(0, 16).replace('T', ' ')}</td>
                        </tr>`;
                    }).join('')}
                </tbody>
            </table>`;
    }

    body.innerHTML = `
        <div class="detail-section">
            <h3>基本信息</h3>
            <dl class="detail-grid">
                <dt>用户 ID</dt><dd>${esc(data.id)}</dd>
                <dt>用户名</dt><dd>${esc(data.username)}</dd>
                <dt>显示名称</dt><dd>${esc(data.display_name || '-')}</dd>
                <dt>邮箱</dt><dd>${esc(data.email || '-')}</dd>
                <dt>角色</dt><dd>${data.is_admin ? '<span class="badge badge-admin">管理员</span>' : '<span class="badge badge-user">普通用户</span>'}</dd>
                <dt>注册时间</dt><dd>${(data.created_at || '').slice(0, 16).replace('T', ' ')}</dd>
                <dt>会话总数</dt><dd>${sessions.length}</dd>
            </dl>
        </div>
        <div class="detail-section">
            <h3>会话记录 (${sessions.length})</h3>
            ${sessionsHtml}
        </div>
    `;

    document.getElementById('user-detail-modal').classList.remove('hidden');
}

// ============================================================
// 搜索
// ============================================================

function searchSessions() {
    const query = document.getElementById('search-sessions').value.toLowerCase();
    if (!query) {
        renderSessions(allSessions);
        return;
    }
    const filtered = allSessions.filter(s =>
        (s.username || '').toLowerCase().includes(query) ||
        (s.display_name || '').toLowerCase().includes(query) ||
        (s.scenario_title || '').toLowerCase().includes(query) ||
        (s.scenario_id || '').toLowerCase().includes(query)
    );
    renderSessions(filtered);
}

// ============================================================
// 事件
// ============================================================

function initEvents() {
    // Tab 切换
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        });
    });

    // 搜索
    document.getElementById('search-sessions').addEventListener('input', searchSessions);

    // 退出
    document.getElementById('btn-logout').addEventListener('click', () => {
        fetch(`${API_BASE}/api/auth/logout`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${adminToken}` }
        }).finally(() => {
            localStorage.removeItem('auth_token');
            window.location.href = '/';
        });
    });

    // 关闭弹窗
    document.getElementById('modal-close').addEventListener('click', () => {
        document.getElementById('user-detail-modal').classList.add('hidden');
    });
    document.getElementById('log-modal-close').addEventListener('click', () => {
        document.getElementById('log-modal').classList.add('hidden');
    });

    // 点击遮罩关闭
    document.getElementById('user-detail-modal').addEventListener('click', function(e) {
        if (e.target === this) this.classList.add('hidden');
    });
    document.getElementById('log-modal').addEventListener('click', function(e) {
        if (e.target === this) this.classList.add('hidden');
    });
}

// ============================================================
// 工具函数
// ============================================================

function esc(s) {
    if (!s) return '';
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

function escapeHtml(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function showToast(msg, type = 'success') {
    let toast = document.getElementById('admin-toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'admin-toast';
        toast.className = 'toast hidden';
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.className = `toast toast-${type}`;
    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(() => { toast.classList.add('hidden'); }, 3000);
}

// ============================================================
// 启动
// ============================================================

document.addEventListener('DOMContentLoaded', init);
