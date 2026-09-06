/**
 * SECAI-PT 三栏壳（一比一复刻 dsh ui-layout AppFrame）。
 *
 * - 轨道宽 = computeColumns(视口, 侧栏偏好, 详情偏好) 的决议，内联写
 *   grid-template-columns；两个分隔条做指针捕获 + rAF 节流上报 dx，
 *   拖拽基线取"渲染宽"（抓让步中被夹紧的面板不得跳回存储偏好）。
 * - 让步链（columns.ts）：放不下先压 details 到 min，再归零；sidebar 永不让位。
 * - 窄屏（<1024px）sidebar 自动收成 56px rail；rail 态下隐藏侧栏分隔条，
 *   点 rail 的展开钮可手动回宽（narrowExpanded）。
 * - 三列内容由调用方以 slot 注入（sidebar / conversation / details），
 *   本组件只负责壳 + 拖拽几何，不感知业务数据。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { computeColumns, DETAILS_DEFAULT, SIDEBAR_AUTO_COLLAPSE, SIDEBAR_DEFAULT } from './columns.ts'
import css from './AppFrame.module.css'

export interface AppFrameProps {
  sidebar: ReactNode
  conversation: ReactNode
  details: ReactNode
  /** 穿透渲染的全局覆盖层（Modal 等 portal 内容之外的非 portal 浮层）。 */
  children?: ReactNode
  /** 侧栏展开偏好（false = 收起成 rail）。 */
  sidebarOpen: boolean
}

/**
 * 一个分隔条：pointer capture + rAF 节流 dx；`side` 决定 hover 高亮形态
 * （sidebar = 仅命中条、details = 垂直居中 12x32 悬浮 pill，见 module.css）。
 */
function DragHandle(props: {
  side: 'sidebar' | 'details'
  left: number
  onStart: () => void
  onDrag: (dx: number) => void
  onEnd: () => void
}) {
  const [dragging, setDragging] = useState(false)
  const origin = useRef(0)
  const latest = useRef(0)
  const frame = useRef<number | null>(null)
  const callbacks = useRef({ onStart: props.onStart, onDrag: props.onDrag, onEnd: props.onEnd })
  callbacks.current = { onStart: props.onStart, onDrag: props.onDrag, onEnd: props.onEnd }

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    origin.current = e.clientX
    latest.current = e.clientX
    callbacks.current.onStart()
    setDragging(true)
  }, [])

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!e.currentTarget.hasPointerCapture(e.pointerId)) return
    latest.current = e.clientX
    if (frame.current !== null) return
    frame.current = requestAnimationFrame(() => {
      frame.current = null
      callbacks.current.onDrag(latest.current - origin.current)
    })
  }, [])

  const onPointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!e.currentTarget.hasPointerCapture(e.pointerId)) return
    e.currentTarget.releasePointerCapture(e.pointerId)
    if (frame.current !== null) {
      cancelAnimationFrame(frame.current)
      frame.current = null
    }
    callbacks.current.onDrag(latest.current - origin.current)
    setDragging(false)
    callbacks.current.onEnd()
  }, [])

  return (
    <div
      className={css.handle}
      style={{ left: props.left }}
      data-side={props.side}
      data-dragging={dragging || undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
    />
  )
}

export function AppFrame({ sidebar, conversation, details, children = null, sidebarOpen }: AppFrameProps) {
  const frameRef = useRef<HTMLDivElement | null>(null)
  const [viewport, setViewport] = useState(() => window.innerWidth)
  const [sidebarPref, setSidebarPref] = useState(SIDEBAR_DEFAULT)
  const [detailsPref, setDetailsPref] = useState(DETAILS_DEFAULT)
  /** 窄屏下用户手动回宽（dsh narrowExpanded 语义）。 */
  const [narrowExpanded, setNarrowExpanded] = useState(false)

  // 帧自身盒子宽度（非窗口）：ResizeObserver + rAF 节流
  useEffect(() => {
    const el = frameRef.current
    if (el === null) return
    let raf: number | null = null
    const observer = new ResizeObserver(() => {
      if (raf !== null) return
      raf = requestAnimationFrame(() => {
        raf = null
        const width = el.getBoundingClientRect().width
        if (width > 0) setViewport(width)
      })
    })
    observer.observe(el)
    return () => {
      observer.disconnect()
      if (raf !== null) cancelAnimationFrame(raf)
    }
  }, [])

  // 窄屏自动收起；宽屏回来清掉手动回宽（决议函数无滞回，回宽自动还原）
  const narrow = viewport < SIDEBAR_AUTO_COLLAPSE
  useEffect(() => {
    if (!narrow) setNarrowExpanded(false)
  }, [narrow])

  const sidebarCollapsed = narrow ? !narrowExpanded : !sidebarOpen
  const sidebarPreference = sidebarCollapsed
    ? 0
    : sidebarPref === 0 ? SIDEBAR_DEFAULT : sidebarPref

  const cols = computeColumns(viewport, sidebarPreference, detailsPref)
  const colsRef = useRef(cols)
  colsRef.current = cols

  // 拖拽基线 = 抓取时的渲染宽（被让步夹紧也不跳回偏好）；手势内冻结，dx 不叠加。
  const sidebarBase = useRef(0)
  const detailsBase = useRef(0)
  // 拖拽全程暂停轨道过渡（缓动会让列边脱离指针）
  const [dragging, setDragging] = useState(false)
  const onDragEnd = useCallback(() => setDragging(false), [])
  const onSidebarStart = useCallback(() => {
    sidebarBase.current = colsRef.current.sidebar
    setDragging(true)
  }, [])
  const onDetailsStart = useCallback(() => {
    detailsBase.current = colsRef.current.details
    setDragging(true)
  }, [])
  const onSidebarDrag = useCallback((dx: number) => setSidebarPref(sidebarBase.current + dx), [])
  const onDetailsDrag = useCallback((dx: number) => setDetailsPref(detailsBase.current - dx), [])

  return (
    <div
      ref={frameRef}
      className={css.frame}
      data-sidebar-collapsed={sidebarCollapsed || undefined}
      data-details-collapsed={cols.details === 0 || undefined}
      data-dragging={dragging || undefined}
      style={{ gridTemplateColumns: `${cols.sidebar}px minmax(0, 1fr) ${cols.details}px` }}
    >
      <aside className={css.sidebarCol}>{sidebar}</aside>
      <main className={css.centerCol}>{conversation}</main>
      <aside className={css.detailsCol}>{details}</aside>
      {children}
      {/* rail 态侧栏定宽 56，无分隔条（dsh：collapsed 无 handle） */}
      {!sidebarCollapsed && (
        <DragHandle side="sidebar" left={cols.sidebar} onStart={onSidebarStart} onDrag={onSidebarDrag} onEnd={onDragEnd} />
      )}
      {cols.details > 0 && (
        <DragHandle side="details" left={viewport - cols.details} onStart={onDetailsStart} onDrag={onDetailsDrag} onEnd={onDragEnd} />
      )}
    </div>
  )
}
