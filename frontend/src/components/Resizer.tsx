import { useCallback, useRef } from 'react'

interface Props {
  direction: 'horizontal' | 'vertical'
  onResize: (delta: number) => void
}

export default function Resizer({ direction, onResize }: Props) {
  const startPos = useRef(0)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    startPos.current = direction === 'horizontal' ? e.clientX : e.clientY
    document.body.style.cursor = direction === 'horizontal' ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'

    const onMouseMove = (ev: MouseEvent) => {
      const current = direction === 'horizontal' ? ev.clientX : ev.clientY
      const delta = current - startPos.current
      if (delta !== 0) {
        startPos.current = current
        onResize(delta)
      }
    }

    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [direction, onResize])

  return (
    <div
      className={`resizer resizer-${direction}`}
      onMouseDown={onMouseDown}
    />
  )
}
