// KbScreen：知识库三栏视图（分类切换联动中栏 + 中栏条目切换右栏文章 + wikilink 回调）。
import { useState } from 'react'
import type { DBShape, Task } from '../db.ts'

interface Article {
  cat: string
  t: string
  m: string
  body: { tag: 'h1' | 'h2' | 'p'; text: string }[]
}

const ARTICLES: Article[] = [
  {
    cat: '方法论',
    t: 'Kill Chain 与 PTES 的融合实践',
    m: '方法论 · 09-06 · 来自任务复盘',
    body: [
      { tag: 'h1', text: 'Kill Chain 与 PTES 的融合实践' },
      { tag: 'p', text: '方法论 · 更新于 2026-09-06 · 来源：ENG-20260901-0031 复盘 · 4 个反向链接' },
      { tag: 'h2', text: '为什么需要融合' },
      { tag: 'p', text: '单纯按 Kill Chain 推进容易陷入「为扫而扫」；单纯按 PTES 七阶段又缺乏对攻击者视角的敬畏。破阵的实践是以 Kill Chain 作为横向推进轴、PTES 作为纵向质量轴：每个 Kill Chain 阶段结束时强制对照 PTES 的对应交付物检查表。' },
      { tag: 'h2', text: '阶段门检查表' },
      { tag: 'p', text: '例如「④ 利用」阶段的完成条件包括：证据链固化、影响面评估、回滚确认。相关设计见【审批分级 T1-T4 设计原则】。' },
      { tag: 'p', text: '在实践中我们发现，【证据留存】的合规性往往比漏洞数量更能决定报告的专业度。' },
    ],
  },
  {
    cat: '方法论',
    t: '审批分级 T1-T4 设计原则',
    m: '方法论 · 09-04 · 手动创建',
    body: [
      { tag: 'h1', text: '审批分级 T1-T4 设计原则' },
      { tag: 'p', text: '方法论 · 更新于 2026-09-04 · 来源：手动创建 · 2 个反向链接' },
      { tag: 'h2', text: '分级标准' },
      { tag: 'p', text: 'T1 只读无副作用 → T2 可逆审批 → T3 高危每调用人工 → T4 禁止。' },
    ],
  },
  {
    cat: '方法论',
    t: '证据留存的法律边界',
    m: '方法论 · 09-01 · 来自任务复盘',
    body: [
      { tag: 'h1', text: '证据留存的法律边界' },
      { tag: 'p', text: '方法论 · 09-01 · 来自任务复盘' },
      { tag: 'h2', text: '授权与留存' },
      { tag: 'p', text: '每个证据条目必须可追溯到具体的授权窗口与目标；超范围留存即违规。' },
    ],
  },
  {
    cat: '方法论',
    t: '指纹去重的增量策略',
    m: '方法论 · 08-28 · 智能体沉淀',
    body: [
      { tag: 'h1', text: '指纹去重的增量策略' },
      { tag: 'p', text: '方法论 · 08-28 · 智能体沉淀' },
      { tag: 'p', text: '资产指纹按 (host, port, banner_hash) 三元组去重；时间窗保留最新 90 天。' },
    ],
  },
  {
    cat: '漏洞情报',
    t: 'CVE-2024-50379：Tomcat PUT RCE 分析',
    m: '漏洞情报 · 09-05 · 来自任务复盘',
    body: [
      { tag: 'h1', text: 'CVE-2024-50379：Tomcat PUT RCE 分析' },
      { tag: 'p', text: '漏洞情报 · 更新于 2026-09-05 · 来源：ENG-20260901-0031 复盘 · 6 个反向链接' },
      { tag: 'h2', text: '漏洞描述' },
      { tag: 'p', text: 'Tomcat 9.0.83 及以下版本在默认配置下，若开启 PUT 方法且未限制 readonly，攻击者可上传 JSP 文件实现远程代码执行。相关处置见【Tomcat PUT 方法远程代码执行（CVE-2024-50379）】。' },
      { tag: 'h2', text: '利用条件' },
      { tag: 'p', text: '需要：1) HTTP PUT 方法开启；2) web.xml 中 readonly=false；3) 可写入路径（如 /upload）。建议参考【Kill Chain 与 PTES 的融合实践】进行阶段门检查。' },
    ],
  },
  {
    cat: '漏洞情报',
    t: 'CVE-2017-12615：Tomcat 任意文件写入',
    m: '漏洞情报 · 09-02 · 手动创建',
    body: [
      { tag: 'h1', text: 'CVE-2017-12615：Tomcat 任意文件写入' },
      { tag: 'p', text: '漏洞情报 · 更新于 2026-09-02 · 来源：手动创建 · 3 个反向链接' },
      { tag: 'h2', text: '影响版本' },
      { tag: 'p', text: 'Tomcat 7.0.0 - 7.0.79。Windows 环境下可通过 `PUT /xxx.jsp%20` 绕过扩展名过滤。' },
      { tag: 'p', text: '修复建议：升级至 7.0.81+ 或禁用 PUT/DELETE 方法。证据链留存要求见【证据留存的法律边界】。' },
    ],
  },
  {
    cat: '漏洞情报',
    t: '弱口令字典生成方法论',
    m: '漏洞情报 · 08-25 · 智能体沉淀',
    body: [
      { tag: 'h1', text: '弱口令字典生成方法论' },
      { tag: 'p', text: '漏洞情报 · 08-25 · 智能体沉淀' },
      { tag: 'p', text: '基于目标组织信息的字典变体：年份、域名、品牌、城市拼音、员工姓名拼音。线程限制见【审批分级 T1-T4 设计原则】。' },
    ],
  },
  {
    cat: '工具手册',
    t: 'nmap 高级扫描参数速查',
    m: '工具手册 · 09-03 · 手动创建',
    body: [
      { tag: 'h1', text: 'nmap 高级扫描参数速查' },
      { tag: 'p', text: '工具手册 · 更新于 2026-09-03 · 来源：手动创建 · 8 个反向链接' },
      { tag: 'h2', text: '常用组合' },
      { tag: 'p', text: '服务版本探测：`nmap -sV -sC -O target`；全端口扫描：`nmap -p- --min-rate 1000 target`。' },
      { tag: 'p', text: 'NSE 脚本示例：`nmap --script=http-put -p8080 target`。相关审批级别见【审批分级 T1-T4 设计原则】。' },
    ],
  },
  {
    cat: '工具手册',
    t: 'hydra 协议爆破参数详解',
    m: '工具手册 · 08-30 · 手动创建',
    body: [
      { tag: 'h1', text: 'hydra 协议爆破参数详解' },
      { tag: 'p', text: '工具手册 · 08-30 · 手动创建' },
      { tag: 'p', text: 'HTTP 表单爆破：`hydra -L users.txt -P pass.txt target http-post-form "/login:username=^USER^&password=^PASS^:F=Invalid"`。线程建议 ≤8（T2 审批约束）。' },
    ],
  },
  {
    cat: '工具手册',
    t: 'sqlmap 注入探测与利用',
    m: '工具手册 · 08-22 · 智能体沉淀',
    body: [
      { tag: 'h1', text: 'sqlmap 注入探测与利用' },
      { tag: 'p', text: '工具手册 · 08-22 · 智能体沉淀' },
      { tag: 'p', text: 'GET 注入探测：`sqlmap -u "target/page?id=1" --batch --level=3`。POST 注入：`sqlmap -r request.txt --batch`。' },
    ],
  },
  {
    cat: '复盘记录',
    t: '2026-09-01 电商渗透复盘',
    m: '复盘记录 · 09-02 · 来自任务复盘',
    body: [
      { tag: 'h1', text: '2026-09-01 电商渗透复盘' },
      { tag: 'p', text: '复盘记录 · 更新于 2026-09-02 · 来源：ENG-20260901-0031 复盘 · 5 个反向链接' },
      { tag: 'h2', text: '关键发现' },
      { tag: 'p', text: 'Tomcat PUT RCE 为核心突破口，横向移动因 SMB 凭据未命中而中止。方法论参考【Kill Chain 与 PTES 的融合实践】。' },
      { tag: 'p', text: '证据链完整度 92%，主要缺口是 Tomcat Manager 登录后的操作录屏。合规要求见【证据留存的法律边界】。' },
    ],
  },
  {
    cat: '复盘记录',
    t: '2026-08-15 内网侦察复盘',
    m: '复盘记录 · 08-16 · 来自任务复盘',
    body: [
      { tag: 'h1', text: '2026-08-15 内网侦察复盘' },
      { tag: 'p', text: '复盘记录 · 08-16 · 来自任务复盘' },
      { tag: 'p', text: '10.0.8.0/24 网段存活 23 台主机，其中 3 台开放 445 端口。指纹去重策略见【指纹去重的增量策略】。' },
    ],
  },
  {
    cat: '环境笔记',
    t: 'demo.ine.local 测试环境说明',
    m: '环境笔记 · 09-01 · 手动创建',
    body: [
      { tag: 'h1', text: 'demo.ine.local 测试环境说明' },
      { tag: 'p', text: '环境笔记 · 09-01 · 手动创建' },
      { tag: 'p', text: '靶场环境，含 Tomcat 9.0.83（PUT 开启）、MySQL 5.7、后台管理系统。用于内部验证与培训。' },
    ],
  },
  {
    cat: '环境笔记',
    t: 'SECAI·PT 破阵部署手册',
    m: '环境笔记 · 08-20 · 手动创建',
    body: [
      { tag: 'h1', text: 'SECAI·PT 破阵部署手册' },
      { tag: 'p', text: '环境笔记 · 08-20 · 手动创建' },
      { tag: 'p', text: '后端 8700 端口直接 serve apps/web/dist；LLM Key 在 .env。SECAI_FIXTURE=1 可脱离 LLM 跑 demo。' },
    ],
  },
]

const CATS: Array<{ name: string; count: number }> = [
  { name: '方法论', count: 12 },
  { name: '漏洞情报', count: 28 },
  { name: '工具手册', count: 19 },
  { name: '复盘记录', count: 7 },
  { name: '环境笔记', count: 4 },
]

export function KbScreen({ currentTask: _currentTask, onToast }: { db: DBShape; currentTask: Task | null; onToast?: (msg: string, kind?: 'ok' | 'err') => void }) {
  const [catIdx, setCatIdx] = useState(0)
  const [itemIdx, setItemIdx] = useState(0)
  const cat = CATS[catIdx]?.name ?? '方法论'
  const items = ARTICLES.filter((a) => a.cat === cat)
  const cur = items[itemIdx] ?? items[0] ?? ARTICLES[0]
  if (cur !== undefined && items.findIndex((a) => a.t === cur.t) === -1 && itemIdx > 0) {
    // 切分类后索引越界，重置
    setItemIdx(0)
  }
  return (
    <div className="proto-screen on" style={{ overflow: 'hidden' }}>
      <div className="proto-kb">
        <div className="proto-kb-col">
          <div className="proto-kb-cat head">分类</div>
          {CATS.map((c, i) => (
            <div
              key={c.name}
              className={'proto-kb-cat' + (i === catIdx ? ' on' : '')}
              onClick={() => { setCatIdx(i); setItemIdx(0) }}
            >
              {c.name} ({c.count})
            </div>
          ))}
        </div>
        <div className="proto-kb-col">
          <div style={{ padding: '16px 18px 10px', display: 'flex', gap: 8, alignItems: 'center', borderBottom: '1px solid var(--border)' }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ color: 'var(--text-3)' }}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
            <input placeholder="搜索条目…" style={{ border: 'none', outline: 'none', background: 'none', fontSize: 12.5, flex: 1, color: 'var(--text-1)' }} />
          </div>
          {items.map((it, i) => (
            <div
              key={it.t}
              className={'proto-kb-item' + (i === itemIdx ? ' on' : '')}
              onClick={() => setItemIdx(i)}
            >
              <div className="proto-kb-t">{it.t}</div>
              <div className="proto-kb-m">{it.m}</div>
            </div>
          ))}
        </div>
        <div className="proto-kb-col">
          <div className="proto-kb-read">
            {cur ? cur.body.map((b, i) => {
              if (b.tag === 'h1') return <h1 key={i}>{b.text}</h1>
              if (b.tag === 'h2') return <h2 key={i}>{b.text}</h2>
              // p 段落：内联【…】→ wikilink 按钮
              const parts = b.text.split(/【([^】]+)】/)
              return (
                <p key={i}>
                  {parts.map((seg, j) => (j % 2 === 1 ? (
                    <button
                      key={j}
                      type="button"
                      className="wikilink"
                      onClick={() => onToast?.(`跳转：${seg}`, 'ok')}
                    >
                      {seg}
                    </button>
                  ) : (
                    <span key={j}>{seg}</span>
                  )))}
                </p>
              )
            }) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
