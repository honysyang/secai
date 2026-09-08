// ModeModal：作战模式切换（mode-mask）。6 种模式（pt/scan/code/ctf/re/assess）
// 按 prototype.html 的 MODES / MODE_MAP / renderMode / openMode / saveMode 实现。
//
// DOM 复用：使用同 .proto-modal-mask/.proto-modal 样式；模式选择走 .proto-sched-grid 三列；
// 模式卡片带颜色徽标（m.c 决定 .proto-mb-ico 背景）。

import { useEffect, useState } from 'react'

export type CombatMode = 'pt' | 'scan' | 'code' | 'ctf' | 're' | 'assess'

export interface CombatModeMeta {
  id: CombatMode
  n: string
  c: string
  ic: string
  in: string
  tools: string
  out: string
  risk: string
  ph: string
  acc: string
  act: string
  model: string
  stats: Array<[string, string]>
}

export const COMBAT_MODES: CombatModeMeta[] = [
  {
    id: 'pt', n: '渗透测试', c: '#378ADD',
    ic: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.2"/><path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3"/>',
    in: '授权目标（IP / CIDR / 域名）',
    tools: 'nmap · nuclei · Metasploit · 手工验证',
    out: '利用链 · 权限证明 · 整改报告',
    risk: '严格 · 利用需 T2 / T3 审批',
    ph: '描述渗透目标，如「从外网入口推进到域控，验证可利用性」…',
    acc: '.txt,.csv,.json,.pdf,.png',
    act: '生成报告',
    model: 'Qwen3-80B-A3B · 温度 0.3 · 长推理',
    stats: [['6 / 9', 'Kill Chain'], ['3', '已利用'], ['2', '待审批']],
  },
  {
    id: 'scan', n: '漏洞扫描', c: '#5F5E5A',
    ic: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.5-4.5"/>',
    in: '目标清单（IP / CIDR / 域名）',
    tools: 'nmap · nuclei · ffuf · hydra',
    out: '资产清单 · 漏洞清单 · 复测报告',
    risk: '需授权范围 · 不做利用',
    ph: '向破阵下达指令，如「全端口扫描并输出漏洞清单，不做利用」…',
    acc: '.txt,.csv,.json,.pdf,.png',
    act: '生成报告',
    model: 'Qwen3-30B-A3B · 温度 0.1 · 高吞吐',
    stats: [['14', '主机'], ['36', '开放服务'], ['9', '漏洞']],
  },
  {
    id: 'code', n: '代码审计', c: '#7F77DD',
    ic: '<path d="m9 18-6-6 6-6M15 6l6 6-6 6"/>',
    in: '代码仓库 / 目录 / PR',
    tools: 'Semgrep · CodeQL · Joern',
    out: '漏洞清单 · 污点链 · 修复补丁',
    risk: '只读分析 · 无需审批',
    ph: '描述审计目标，如「审计 payment 模块的注入与越权」…',
    acc: '.zip,.tar.gz,.patch,.diff,.json',
    act: '导出补丁',
    model: 'Qwen3-30B-A3B · 上下文 128k · 温度 0.2',
    stats: [['7', '待验证漏洞'], ['3', '污点链'], ['12', '已修复']],
  },
  {
    id: 'ctf', n: 'CTF 竞赛', c: '#1D9E75',
    ic: '<path d="M4 15s 1-1 4-1 5 2 8 2 4-1 4-1V4s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>',
    in: '题目附件 + 描述',
    tools: 'pwntools · z3 · binwalk · stegsolve',
    out: 'flag · 解题路径 · Writeup',
    risk: '靶场环境 · 全部 放行',
    ph: '描述你的思路，如「先用 binwalk 分离，再看 ELF 的 main」…',
    acc: '.zip,.bin,.elf,.pcap,.png,.txt',
    act: '提交 flag',
    model: 'Qwen3-30B-A3B · 温度 0.1 · 低延迟',
    stats: [['3 / 8', '已解出'], ['1h 24m', '用时'], ['2 / 5', '提示']],
  },
  {
    id: 're', n: '二进制逆向', c: '#D85A30',
    ic: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 9h6v6H9z"/>',
    in: '样本文件（加壳 / 无符号）',
    tools: 'Ghidra · radare2 · Frida · 沙箱',
    out: '行为报告 · IoC · 漏洞定位',
    risk: '样本运行需 T2 审批',
    ph: '描述分析目标，如「定位样本的网络协议与 C2 地址」…',
    acc: '.bin,.exe,.so,.elf,.apk,.pcap',
    act: '导出 IoC',
    model: 'Qwen3-30B-A3B · 汇编增强 · 温度 0.2',
    stats: [['1,284', '函数'], ['3.7k', '字符串'], ['6', 'IoC']],
  },
  {
    id: 'assess', n: '安全评估', c: '#BA7517',
    ic: '<path d="M12 2.5 4 5.5v6c0 5 3.4 8.4 8 10 4.6-1.6 8-5 8-10v-6l-8-3z"/>',
    in: '评估范围 + 合规框架',
    tools: '全量工具链 · Kill Chain · 合规映射',
    out: '风险报告 · 整改建议 · 合规对照',
    risk: '最严格 · T3 双人复核',
    ph: '描述评估目标，如「按等保三级评估该业务系统」…',
    acc: '.pdf,.docx,.txt,.csv',
    act: '导出评估',
    model: 'Qwen3-80B-A3B · 温度 0.4 · 长文本',
    stats: [['6 / 9', 'Kill Chain'], ['4', '高危'], ['38 / 52', '合规项']],
  },
]

export const COMBAT_MODE_MAP: Record<CombatMode, CombatModeMeta> = COMBAT_MODES.reduce(
  (acc, m) => ({ ...acc, [m.id]: m }),
  {} as Record<CombatMode, CombatModeMeta>
)

export interface ModeModalProps {
  open: boolean
  current: CombatMode
  onClose: () => void
  onApply: (m: CombatMode) => void
}

export function ModeModal({ open, current, onClose, onApply }: ModeModalProps) {
  const [pending, setPending] = useState<CombatMode>(current)
  useEffect(() => {
    if (open) setPending(current)
  }, [open, current])
  if (!open) return null

  const m = COMBAT_MODE_MAP[pending]

  return (
    <div className="proto-modal-mask on" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="proto-modal" role="dialog" aria-label="切换作战模式" style={{ width: 720, display: 'flex', flexDirection: 'column' }}>
        <div className="proto-modal-head">
          <span className="proto-modal-title">切换作战模式</span>
          <button className="proto-icon-btn" type="button" onClick={onClose} title="关闭">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="proto-modal-body" style={{ flex: 1 }}>
          <div className="proto-set-sec">
            <div className="proto-set-h">选择作战模式</div>
            <div className="proto-set-d">模式改变推理策略、可用工具、产出模板与审批边界；对话流、轨迹、链路、审批与附件骨架保持一致。</div>
            <div className="proto-sched-grid">
              {COMBAT_MODES.map((mm) => (
                <div
                  key={mm.id}
                  className={'proto-mode-select-card' + (pending === mm.id ? ' on' : '')}
                  onClick={() => setPending(mm.id)}
                >
                  <div className="sc-t">
                    <span style={{ color: mm.c, display: 'inline-flex' }}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: `<g>${mm.ic}</g>` }} />
                    </span>
                    {mm.n}
                  </div>
                  <div className="sc-d">{mm.in} · {mm.tools}</div>
                </div>
              ))}
            </div>
          </div>
          <div className="proto-set-sec">
            <div className="proto-set-h">该模式的配置</div>
            <div className="proto-f-row">
              <span className="proto-f-lb">推理模型</span>
              <input className="proto-f-inp mono" defaultValue={m.model} />
              <button type="button" className="proto-mb-btn" onClick={() => onApply(pending)}>应用</button>
            </div>
            <div className="proto-f-row">
              <span className="proto-f-lb">工具集</span>
              <input className="proto-f-inp" defaultValue={m.tools} />
            </div>
            <div className="proto-f-row">
              <span className="proto-f-lb">产出模板</span>
              <input className="proto-f-inp" defaultValue={m.out} />
            </div>
            <div className="proto-f-row">
              <span className="proto-f-lb">审批边界</span>
              <input className="proto-f-inp" defaultValue={m.risk} />
            </div>
            <div className="proto-conf-sched-tip">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--warn)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /><path d="M12 9v4M12 17h.01" /></svg>
              <span>切换模式会同步套用该模式的<b>推荐模型</b>与工具集；也可在「「设置 → 模型」」中单独调整，两者互不影响。</span>
            </div>
          </div>
        </div>
        <div className="proto-modal-foot">
          <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>模式决定推理策略、工具集与产出模板；已积累的证据不会丢失</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="proto-btn ghost" onClick={onClose}>取消</button>
            <button type="button" className="proto-btn primary" onClick={() => { onApply(pending); onClose() }}>应用</button>
          </div>
        </div>
      </div>
    </div>
  )
}

/** ModeBanner：会话栏正下方显示当前作战模式（与 prototype.html 的 mode-banner 一致）。 */
export function ModeBanner({ mode, onOpenSettings, onSwitchMode, onApply }: {
  mode: CombatMode
  onOpenSettings: () => void
  onSwitchMode: () => void
  onApply: () => void
}) {
  const m = COMBAT_MODE_MAP[mode]
  return (
    <div className="proto-mode-mb">
      <div className="proto-mb-main">
        <span className="proto-mb-ico" style={{ background: m.c }} dangerouslySetInnerHTML={{
          __html: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${m.ic}</svg>`
        }} />
        <div className="proto-mb-txt">
          <div className="proto-mb-n">
            {m.n}
            <span className="proto-mb-badge">作战模式</span>
            <span className="proto-mb-model" onClick={onOpenSettings} title="切换推理模型（设置 → 模型）">{m.model.split(' · ')[0]}</span>
          </div>
          <div className="proto-mb-meta">输入 {m.in} · 工具 {m.tools} · 产出 {m.out} · 风险 {m.risk}</div>
        </div>
        <button type="button" className="proto-mb-btn solid" onClick={onApply}>{m.act}</button>
        <button type="button" className="proto-mb-btn" onClick={onSwitchMode}>切换</button>
      </div>
      <div className="proto-mb-stats">
        {m.stats.map((s, i) => (
          <div key={i} className="proto-mb-stat">
            <span className="s-v">{s[0]}</span>
            <span className="s-l">{s[1]}</span>
          </div>
        ))}
      </div>
    </div>
  )
}