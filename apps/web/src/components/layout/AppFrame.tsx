/**
 * SECAI-PT 产品壳（F2）：三栏 grid + 分隔条拖拽 + 让步链。
 *
 * - 轨道宽 = computeColumns(视口, 拖拽偏好) 的决议，内联写
 *   grid-template-columns；两个分隔条做指针捕获 + rAF 节流上报 dx，
 *   拖拽基线取"渲染宽"（抓让步中被夹紧的面板不得跳回存储偏好）。
 * - 视口用 ResizeObserver 跟踪帧自身盒子（rAF 节流），收窄时让步链
 *   自动收拢 details、回宽自动还原（columns.ts）。
 * - 三列内容由调用方以 slot 注入（sidebar / conversation / details），
 *   本组件只负责壳 + 拖拽几何，不感知业务数据。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { computeColumns, DETAILS_DEFAULT, SIDEBAR_DEFAULT } from './columns.ts'
import css from './AppFrame.module.css'

export interface AppFrameProps {
  sidebar: ReactNode
  conversation: ReactNode
  details: ReactNode
}

/**
 * 一个分隔条：pointer capture + rAF 节流 dx；`side` 决定 hover 高亮形态
 * （sidebar = 细线、details = 中部悬浮 pill，见 AppFrame.module.css）。
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

export function AppFrame(props: AppFrameProps) {
  const frameRef = useRef<HTMLDivElement | null>(null)
  const [viewport, setViewport] = useState(() => window.innerWidth)
  const [sidebarPref, setSidebarPref] = useState(SIDEBAR_DEFAULT)
  const [detailsPref, setDetailsPref] = useState(DETAILS_DEFAULT)

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

  const cols = computeColumns(viewport, sidebarPref, detailsPref)
  const colsRef = useRef(cols)
  colsRef.current = cols

  // 拖拽基线 = 抓取时的渲染宽（被让步夹紧也不跳回偏好）；手势内冻结，
  // dx 不叠加。
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
      data-dragging={dragging || undefined}
      data-details-collapsed={cols.details === 0 || undefined}
      style={{ gridTemplateColumns: `${cols.sidebar}px minmax(0, 1fr) ${cols.details}px` }}
    >
      <aside className={css.sidebarCol}>{props.sidebar}</aside>
      <main className={css.centerCol}>{props.conversation}</main>
      <aside className={css.detailsCol}>{props.details}</aside>
      <DragHandle side="sidebar" left={cols.sidebar} onStart={onSidebarStart} onDrag={onSidebarDrag} onEnd={onDragEnd} />
      {cols.details > 0 && (
        <DragHandle side="details" left={viewport - cols.details} onStart={onDetailsStart} onDrag={onDetailsDrag} onEnd={onDragEnd} />
      )}
    </div>
  )
}
