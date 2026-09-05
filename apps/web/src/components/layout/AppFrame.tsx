/** SECAI-PT 产品壳：三栏骨架（sidebar | conversation | details）。
 *
 * F0/F1 交付版为占位骨架：轨道宽度用内联 grid-template-columns（F2 将换成
 * dsh 同款拖拽 + 让步链），亮暗主题经 body[data-secai-dark] 由 token 双套驱动，
 * 跟随系统偏好由 main.tsx 的 matchMedia 维护。数据与通信层（connection/
 * runtime/）本阶段不在此实例化——后端 server/ 属 R3/R4，联调时接入。
 */

import css from './AppFrame.module.css'

/** 侧栏占位宽度（px）；F2 改为可拖拽调节。 */
export const SIDEBAR_WIDTH = 260
/** 详情栏占位宽度（px）；F2 改为可拖拽调节。 */
export const DETAILS_WIDTH = 320

function PaneTitle({ children }: { children: string }) {
  return <div className={css.paneTitle}>{children}</div>
}

function PlaceholderCard({ text }: { text: string }) {
  return <div className={css.placeholder}>{text}</div>
}

export function AppFrame() {
  return (
    <div
      className={css.frame}
      style={{
        gridTemplateColumns: `${SIDEBAR_WIDTH}px minmax(0, 1fr) ${DETAILS_WIDTH}px`,
      }}
    >
      <aside className={css.sidebarCol}>
        <PaneTitle>目标列表</PaneTitle>
        <div className={css.paneBody}>
          <PlaceholderCard text="F3 TargetList：host/session-* 帧驱动，运行 / 待审 / 完成圆点语言（dsh 同款）" />
        </div>
      </aside>
      <main className={css.centerCol}>
        <PaneTitle>会话 · 破阵</PaneTitle>
        <div className={css.paneBody}>
          <PlaceholderCard text="F3 会话视图：ChatView / ApprovalCard（respond 回响 rpcId）/ HypothesisQueue / DeadEndList" />
        </div>
      </main>
      <aside className={css.detailsCol}>
        <PaneTitle>详情</PaneTitle>
        <div className={css.paneBody}>
          <PlaceholderCard text="F3 详情：ToolOutputPanel / EvidenceChain / PortScanResult / ReportPreview / LearningPanel" />
        </div>
      </aside>
    </div>
  )
}
