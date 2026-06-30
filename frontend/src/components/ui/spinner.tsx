import { Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

interface SpinnerProps {
  className?: string
  size?: number
}

export function Spinner({ className, size = 24 }: SpinnerProps) {
  return (
    <Loader2
      className={cn("animate-spin", className)}
      size={size}
    />
  )
}

export function LoadingScreen() {
  return (
    <div className="flex items-center justify-center h-screen">
      <Spinner size={48} />
    </div>
  )
}
