/*
 * getDisplayPersona 优先级回归（任务#60 · Major-2）
 *
 * 为什么不用浏览器：本轮约束禁止重启服务、禁止动 5000/5001 端口上的进程，所以
 * 这里只测「纯函数」。做法是从 web/js/app.js 源码里按名字抠出 getDisplayPersona
 * 及其依赖（_asTextList / getScenarioCastEntry / resolveRoleKey /
 * _participantForRoleKey / _mergePersonaFields / 三张常量表），在一个只提供 stub
 * `state` 的沙箱里求值——不加载整份 app.js（它顶层就摸 document）。
 * 输入是本仓库自己的源文件，不是外部数据。
 *
 * 断言口径 = 「界面显示优先级」必须等于「后端生效优先级」：
 *   graph_builder.py:400-409 先铺情景 cast，:412-428 再用自定义 participant 的
 *   personality **逐字段覆盖**（`_merge_personality`：非空字段覆盖、空字段保留）。
 *   旧 getDisplayPersona 是 cast 命中即 return，把自定义降为第二步 ⇒ 正好相反。
 *   另：姓名只有非系统种子名才算用户改过（对齐后端 _DEFAULT_ROLE_NAMES）。
 *
 * 用法：node display_persona_probe.js [app.js 路径]
 * 退出码：0 全通过 / 1 有失败 / 2 环境或取符号失败
 */
'use strict';
const fs = require('fs');
const path = require('path');

const APP_JS = process.argv[2] || path.resolve(__dirname, '..', '..', 'web', 'js', 'app.js');
let src;
try {
  src = fs.readFileSync(APP_JS, 'utf8');
} catch (e) {
  console.error('读不到 app.js：' + APP_JS + '（' + e.message + '）');
  process.exit(2);
}

// ---- 从源码里按名字取声明（顶层 const 支持 {} / [] 字面量；function 取配对大括号） ----
function sliceFn(text, name) {
  const key = 'function ' + name + '(';
  const i = text.indexOf(key);
  if (i < 0) throw new Error('找不到函数 ' + name);
  const open = text.indexOf('{', i);
  let depth = 0, j = open;
  for (; j < text.length; j += 1) {
    if (text[j] === '{') depth += 1;
    else if (text[j] === '}') { depth -= 1; if (depth === 0) break; }
  }
  return text.slice(i, j + 1);
}
function sliceConst(text, name) {
  const re = new RegExp('const ' + name + ' = ');
  const m = re.exec(text);
  if (!m) throw new Error('找不到常量 ' + name);
  const i = m.index;
  let open = -1, closeCh = null;
  for (const ch of ['{', '[']) {
    const p = text.indexOf(ch, i);
    if (p >= 0 && (open < 0 || p < open)) { open = p; closeCh = ch === '{' ? '}' : ']'; }
  }
  if (open < 0) {
    const nl = text.indexOf('\n', i);
    return text.slice(i, nl) + '\n';
  }
  let depth = 0, j = open;
  for (; j < text.length; j += 1) {
    if (text[j] === text[open]) depth += 1;
    else if (text[j] === closeCh) { depth -= 1; if (depth === 0) break; }
  }
  return text.slice(i, j + 1) + ';';
}

const NEED_FNS = ['_asTextList', 'getScenarioCastEntry', 'resolveRoleKey',
                  '_participantForRoleKey', '_mergePersonaFields', 'getDisplayPersona'];
const NEED_CONSTS = ['SYSTEM_ROLE_KEYS', 'ROLE_NAME_BY_KEY', 'DEFAULT_PERSONA_TEXTS',
                     'SYSTEM_DEFAULT_PERSONA_NAMES'];

const found = { consts: [], fns: [], missing: [] };
let code = "'use strict';\nvar state = { participants: [], currentScenario: null };\n";
NEED_CONSTS.forEach(n => {
  try { code += sliceConst(src, n) + '\n'; found.consts.push(n); } catch (e) { found.missing.push(n); }
});
NEED_FNS.forEach(n => {
  try { code += sliceFn(src, n) + '\n'; found.fns.push(n); } catch (e) { found.missing.push(n); }
});
if (found.fns.indexOf('getDisplayPersona') < 0) {
  console.error('app.js 里没有 getDisplayPersona，无法测试');
  process.exit(2);
}
const exported = ['getDisplayPersona', '_mergePersonaFields']
  .filter(n => found.fns.indexOf(n) >= 0);
code += 'return { ' + exported.join(', ') + ', setState };\nfunction setState(s){ state = s; }\n';

let box;
try {
  box = new Function(code)();   // 输入是本仓库源文件，不是外部数据
} catch (e) {
  console.error('沙箱求值失败：' + e.message);
  process.exit(2);
}

// ---- 用例矩阵 ----
const cast = {
  bully: { name: '龙飞', personality: { traits: ['凶', '勒索'], speakingStyle: ['威胁'], background: '辍学混社会', cognitiveStyle: '觉得拿钱是本事' } },
  victim: { name: '陈默', personality: { traits: ['强硬', '好面子'], speakingStyle: ['嘴硬'], background: '家里没人管', cognitiveStyle: '按江湖规矩解决' } },
  bystander_a: { name: '唐宁', personality: { traits: ['有正义感'], speakingStyle: ['小声'] } },
  bystander_b: { name: '郝强', personality: { traits: ['怕事'], speakingStyle: ['沉默'] } },
};
const sysScenario = { title: '放学路上的威胁', cast: cast };
const noCastScenario = { title: '自建情境', cast: null };
const customVictim = {
  id: 'custom_vv', name: '自定义受害者', role: 'victim',
  personality: { traits: ['不肯服软', '拒绝求助'], speakingStyle: ['硬气'] },
};
const sysSeedVictim = { id: 'victim', name: '思远', role: 'victim', personality: {} };

const cases = [
  { t: '①cast + 自定义参与者（Major-2 触发场景）→ 显示自定义',
    state: { participants: [sysSeedVictim, customVictim], currentScenario: sysScenario },
    role: 'victim',
    expect: { name: '自定义受害者', text: '不肯服软、拒绝求助', source: 'participant_personality' } },
  { t: '②只有 cast + 系统种子记录 → 显示 cast',
    state: { participants: [sysSeedVictim], currentScenario: sysScenario },
    role: 'victim',
    expect: { name: '陈默', text: '强硬、好面子', source: 'scenario_cast' } },
  { t: '③cast + 种子记录人设为空 → 仍算 cast，不算自定义',
    state: { participants: [{ id: 'victim', name: '思远', role: 'victim', personality: { traits: [], background: '' } }],
             currentScenario: sysScenario },
    role: 'victim',
    expect: { name: '陈默', text: '强硬、好面子', source: 'scenario_cast' } },
  { t: '④无 cast + 自定义（自定义记录靠 role 绑定，不是靠 id）→ 自定义',
    state: { participants: [customVictim], currentScenario: noCastScenario },
    role: 'victim',
    expect: { name: '自定义受害者', text: '不肯服软、拒绝求助', source: 'participant_personality' } },
  { t: '⑤无 cast + 无参与者 → 默认四句',
    state: { participants: [], currentScenario: noCastScenario },
    role: 'victim',
    expect: { name: '', text: '性格较为内向敏感', source: 'default_fallback' } },
  { t: '⑥字段级覆盖：自定义只填 background，traits 仍来自 cast',
    state: { participants: [{ id: 'custom_bb', name: '自定义霸凌者', role: 'bully', personality: { background: '单亲家庭' } }],
             currentScenario: sysScenario },
    role: 'bully',
    expect: { name: '自定义霸凌者', text: '凶、勒索', source: 'participant_personality' } },
  { t: '⑦自定义记录沿用系统种子名「思远」→ 姓名回落 cast 名，但人设仍算自定义',
    state: { participants: [{ id: 'custom_nn', name: '思远', role: 'victim', personality: { traits: ['只填性格'] } }],
             currentScenario: sysScenario },
    role: 'victim',
    expect: { name: '陈默', text: '只填性格', source: 'participant_personality' } },
];

let fail = 0;
console.log('# F（Major-2）getDisplayPersona 优先级回归');
console.log('# 源文件: ' + APP_JS);
console.log('# 取到符号: ' + found.consts.concat(found.fns).join(', '));
console.log('# 缺失符号: ' + (found.missing.join(', ') || '无'));
cases.forEach(c => {
  box.setState(c.state);
  const got = box.getDisplayPersona(c.role);
  const bad = Object.keys(c.expect).filter(k => got[k] !== c.expect[k]);
  if (bad.length) fail += 1;
  console.log((bad.length ? '[FAIL] ' : '[PASS] ') + c.t);
  console.log('        got=' + JSON.stringify({ name: got.name, text: got.text, source: got.source })
              + (bad.length ? ' expect=' + JSON.stringify(c.expect) + ' 差异=' + bad.join(',') : ''));
});
if (typeof box._mergePersonaFields === 'function') {
  const m = box._mergePersonaFields({ traits: ['a'], background: 'base' }, { traits: ['z'], background: '' });
  const ok = JSON.stringify(m) === JSON.stringify({ traits: ['z'], background: 'base' });
  if (!ok) fail += 1;
  console.log((ok ? '[PASS] ' : '[FAIL] ') + '_mergePersonaFields 非空覆盖/空保留: ' + JSON.stringify(m));
} else {
  console.log('[FAIL] 缺 _mergePersonaFields：显示层没有与后端同口径的字段级合并');
  fail += 1;
}
console.log(fail === 0 ? '=== getDisplayPersona 优先级回归：全部通过 ==='
                       : '=== getDisplayPersona 优先级回归：' + fail + ' 项失败 ===');
process.exit(fail === 0 ? 0 : 1);
