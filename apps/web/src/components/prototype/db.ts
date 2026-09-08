// 原型数据层：单一数据源 + 本地持久化（localStorage key: secai-pt-db-v1）
// 与 prototype.html DB 模型一致；本模块只导出纯函数 + 默认种子数据。

export type TaskStatus = 'run' | 'appr' | 'done' | 'fail' | 'stop'

export interface Task {
  id: string
  name: string
  target: string
  status: TaskStatus
  group: '计划中 · 定时' | '进行中' | '今天' | '过去七天'
  mode: 'pt' | 'scan' | 'code' | 'ctf' | 're' | 'assess'
  sched: 'now' | 'once' | 'cron'
  next: string
  demo?: boolean
  atts?: string[]
}

export interface Settings {
  endpoint: string
  model: string
  concurrency: number
  apprMode: '严格' | '标准' | '自主'
  timeout: string
  retries: number
  desens: boolean
  runTimeout: string
}

export interface Member {
  n: string
  m: string
  r: string
  rc: 'r-admin' | 'r-eng' | 'r-appr' | 'r-audit'
  s: string
  on: number
  t: string
}

export interface ApiKey {
  n: string
  s: string
  d: string
  u: string
  key?: string
}

export interface Rule {
  a: string
  c: string
  d: '自动放行' | '人工审批' | '禁止'
  s: string
}

export interface DBShape {
  tasks: Task[]
  settings: Settings
  members: Member[]
  apis: ApiKey[]
  rules: Rule[]
  meta: { curTaskId: string; curMode: Task['mode'] }
}

const DB_KEY = 'saeci-pt-db-v1'

export const DEFAULT_DB: DBShape = {
  tasks: [
    { id: 't2', name: '10.0.8.0/24 — 月度合规复测', target: '10.0.8.0/24', status: 'stop', group: '计划中 · 定时', mode: 'assess', sched: 'cron', next: '10-05 02:00' },
    { id: 't1', name: 'shop.example.com — 变更后复测', target: 'shop.example.com', status: 'stop', group: '计划中 · 定时', mode: 'scan', sched: 'once', next: '09-10 22:00' },
    { id: 't3', name: 'demo.ine.local — Web 渗透', target: 'demo.ine.local', status: 'run', group: '进行中', mode: 'pt', sched: 'now', next: '', demo: true, atts: ['授权书-2026Q3.pdf'] },
    { id: 't4', name: '10.0.8.0/24 — 内网侦察', target: '10.0.8.0/24', status: 'appr', group: '进行中', mode: 'pt', sched: 'now', next: '' },
    { id: 't5', name: 'vpn.corp.example — 凭据验证', target: 'vpn.corp.example', status: 'done', group: '今天', mode: 'scan', sched: 'now', next: '' },
    { id: 't6', name: 'mail.corp.example — 钓鱼演练复盘', target: 'mail.corp.example', status: 'done', group: '今天', mode: 'assess', sched: 'now', next: '' },
    { id: 't7', name: '172.16.4.11 — 横向移动测试', target: '172.16.4.11', status: 'fail', group: '过去七天', mode: 'pt', sched: 'now', next: '' },
    { id: 't8', name: 'legacy-web-01 — CVE-2024-3400 复现', target: 'legacy-web-01', status: 'stop', group: '过去七天', mode: 'pt', sched: 'now', next: '' },
  ],
  settings: {
    endpoint: 'http://127.0.0.1:8080/v1',
    model: 'Qwen3-30B-A3B-Instruct-2507-Q4_K_M',
    concurrency: 4,
    apprMode: '标准',
    timeout: '超时 10 分钟自动拒绝',
    retries: 2,
    desens: true,
    runTimeout: '120s',
  },
  members: [
    { n: '张伟', m: 'zhangwei@corp.com', r: '管理员', rc: 'r-admin', s: '活跃', on: 1, t: '2 分钟前' },
    { n: '李娜', m: 'lina@corp.com', r: '安全工程师', rc: 'r-eng', s: '活跃', on: 1, t: '1 小时前' },
    { n: '王强', m: 'wangqiang@corp.com', r: '审批人', rc: 'r-appr', s: '活跃', on: 1, t: '昨天' },
    { n: '审计账号', m: 'audit@corp.com', r: '只读审计', rc: 'r-audit', s: '已禁用', on: 0, t: '30 天前' },
  ],
  apis: [
    { n: 'secai-cli', s: '全部作用域', d: '2026-08-12', u: '3 分钟前' },
    { n: 'ci-pipeline', s: '只读 · 报告', d: '2026-09-01', u: '2 天前' },
  ],
  rules: [
    { a: '信息收集 · nmap / whatweb / whois', c: '目标在授权范围内', d: '自动放行', s: '全部任务' },
    { a: '目录枚举 · dirsearch / ffuf', c: '目标在授权范围内', d: '自动放行', s: '全部任务' },
    { a: '漏洞扫描 · nuclei', c: '排除 DoS 类模板', d: '自动放行', s: '全部任务' },
    { a: '凭据验证 · hydra / 弱口令', c: '并发 ≤ 8 线程', d: '自动放行', s: '全部任务' },
    { a: '漏洞利用 · exploit/*', c: 'CVSS ≥ 7.0', d: '人工审批', s: '全部任务' },
    { a: '横向移动 · psexec / wmiexec', c: '任何条件', d: '人工审批', s: '全部任务' },
    { a: '破坏性动作 · 删库 / 加密 / DoS', c: '任何条件', d: '禁止', s: '全局锁定' },
  ],
  meta: { curTaskId: 't3', curMode: 'pt' },
}

export function loadDB(): DBShape {
  if (typeof localStorage === 'undefined') return DEFAULT_DB
  try {
    const raw = localStorage.getItem(DB_KEY)
    if (!raw) return DEFAULT_DB
    const parsed = JSON.parse(raw) as Partial<DBShape>
    return {
      tasks: parsed.tasks ?? DEFAULT_DB.tasks,
      settings: parsed.settings ?? DEFAULT_DB.settings,
      members: parsed.members ?? DEFAULT_DB.members,
      apis: parsed.apis ?? DEFAULT_DB.apis,
      rules: parsed.rules ?? DEFAULT_DB.rules,
      meta: parsed.meta ?? DEFAULT_DB.meta,
    }
  } catch {
    return DEFAULT_DB
  }
}

export function saveDB(db: DBShape): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(DB_KEY, JSON.stringify(db))
  } catch {
    // quota exceeded or unavailable; swallow
  }
}

export function resetDB(): void {
  if (typeof localStorage !== 'undefined') {
    localStorage.removeItem(DB_KEY)
  }
}