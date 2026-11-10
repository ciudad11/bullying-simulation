# 修复报告功能一致性 - 2026-06-19

## 目标
使网页展示的报告、前端导出的报告（txt/HTML）、服务端下载的报告三者内容一致，均包含：
- 霸凌情况（总消息数、攻击事件、霸凌强度、受害者压力）
- 教师干预（次数、平均效果）
- OBVQ 霸凌风险指数分析（总分、六维度分析、风险等级、综合建议）
- 干预效果分析与改进建议（总结、分项建议）
- 能力评估（发现能力、干预能力）
- 干预详情

## 修复内容

### 1. `src/multi_agent_runner.py` - 修复 `get_metrics()` 方法缺失
- 添加 `get_metrics()` 方法：基于对话历史分析霸凌关键词，计算 OBVQ 六维分数、霸凌强度、受害者压力
- 在 `__init__` 中添加 `self.current_round = 0` 属性

### 2. `web/js/app.js` - 增强四个函数
- **`displayReport()`** - 添加 OBVQ 分析区块、干预效果分析区块、改进建议列表；增强能力评估显示
- **`showReport()`** - 同上，新增 OBVQ 和干预效果分析
- **`formatReportAsText()`** - 添加 OBVQ 六维分析、干预效果总结、改进建议、能力评估详情
- **`formatReportAsHTML()`** - 同样添加 OBVQ、干预效果分析、改进建议
- **修复 `a.download` 语法错误** - `评估报告_.txt` → `` `评估报告_${state.sessionId}.txt` ``

### 3. `web/templates/index.html` - 更新缓存版本号
- `v=2026061903` → `v=2026061905`

## 验证结果
- `get_metrics()` 方法独立测试通过，返回完整的 OBVQ 六维数据
- 所有 JS 函数语法引用了报告数据结构中的 `obvq_analysis` 和 `intervention_analysis`

## 启动
```bash
cd D:\bully_simulation_6.19ver\bully_simulation_4.9ver\web
python -m uvicorn app:app --reload --port 5000
```
然后 Ctrl+F5 强制刷新浏览器。
