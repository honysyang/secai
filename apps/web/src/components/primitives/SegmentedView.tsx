/** SegmentedView：分段切换器（滑块动画）。 */

import clsx from 'clsx'
import css from './SegmentedView.module.css'

export interface SegmentedViewProps {
  options: { value: string; label: string }[]
  value: string
  onChange: (v: string) => void
  className?: string
}

export function SegmentedView({ options, value, onChange, className }: SegmentedViewProps) {
  const idx = options.findIndex((o) => o.value === value)

  return (
    <div className={clsx(css.root, className)} role="tablist">
      <div
        className={css.slider}
        style={{
          width: `${100 / options.length}%`,
          transform: `translateX(${idx * 100}%)`,
        }}
        aria-hidden="true"
      />
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          role="tab"
          aria-selected={opt.value === value}
          className={clsx(css.option, opt.value === value && css.active)}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
