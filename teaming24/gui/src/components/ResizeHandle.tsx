import { Separator as PanelResizeHandle } from 'react-resizable-panels'
import clsx from 'clsx'

interface ResizeHandleProps {
  direction?: 'horizontal' | 'vertical'
  className?: string
}

/**
 * A draggable resize handle for resizable panels.
 * Shows a subtle bar that becomes more visible on hover.
 */
export default function ResizeHandle({ 
  direction = 'horizontal',
  className 
}: ResizeHandleProps) {
  const isHorizontal = direction === 'horizontal'
  
  return (
    <PanelResizeHandle
      className={clsx(
        'group relative flex items-center justify-center transition-colors',
        isHorizontal ? 'w-2 hover:bg-primary-500/10' : 'h-2 hover:bg-primary-500/10',
        className
      )}
    >
      {/* Visual handle indicator */}
      <div
        className={clsx(
          'rounded-full bg-dark-border group-hover:bg-primary-500/50 transition-all',
          isHorizontal 
            ? 'w-1 h-8 group-hover:h-12 group-active:bg-primary-500' 
            : 'h-1 w-8 group-hover:w-12 group-active:bg-primary-500'
        )}
      />
    </PanelResizeHandle>
  )
}
