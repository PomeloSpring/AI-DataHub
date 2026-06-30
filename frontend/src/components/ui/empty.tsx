import { Inbox } from "lucide-react"
import { cn } from "@/lib/utils"

interface EmptyProps {
  icon?: React.ReactNode
  title?: string
  description?: string
  action?: React.ReactNode
  className?: string
}

export function Empty({
  icon,
  title = "暂无数据",
  description,
  action,
  className,
}: EmptyProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center py-12 px-4",
        className
      )}
    >
      {icon || <Inbox className="h-12 w-12 text-muted-foreground/50 mb-4" />}
      <h3 className="text-lg font-medium text-foreground">{title}</h3>
      {description && (
        <p className="text-sm text-muted-foreground mt-1">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
