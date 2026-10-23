/**
 * TelemetryHeader.tsx — Status badges for FPS, Ping RTT, Blender UDP, and sonar state.
 *
 * This component subscribes to React state (scalars only) at the low rate that
 * shadcn/ui Badges update — never tied to the high-frequency curve ref.
 */

"use client"

import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { SonarTelemetry, statusToVariant } from "@/types/sonar"

interface TelemetryHeaderProps {
  connected: boolean
  telemetry: Omit<SonarTelemetry, "correlation_curve"> | null
  pingMs: number | null
  className?: string
}

export function TelemetryHeader({
  connected,
  telemetry,
  pingMs,
  className,
}: TelemetryHeaderProps) {
  const status = telemetry?.status ?? "initialising"
  const isMock = telemetry?.mock_mode ?? false

  return (
    <header
      className={`flex flex-wrap items-center gap-3 px-4 py-3 bg-zinc-900 border-b border-zinc-800 ${className ?? ""}`}
    >
      {/* System name */}
      <span className="font-mono text-sm font-bold tracking-widest text-emerald-400 uppercase mr-2">
        Sonar Imaging
      </span>

      <Separator orientation="vertical" className="h-4 bg-zinc-700" />

      {/* WS connection */}
      <Badge
        variant={connected ? "default" : "destructive"}
        className="font-mono text-xs"
      >
        {connected ? "● WS LIVE" : "○ WS OFFLINE"}
      </Badge>

      {/* Engine status */}
      <Badge variant={statusToVariant(status)} className="font-mono text-xs uppercase">
        {status}
      </Badge>

      {/* Mock mode indicator */}
      {isMock && (
        <Badge variant="secondary" className="font-mono text-xs bg-amber-900/50 text-amber-300 border-amber-700">
          MOCK MODE
        </Badge>
      )}

      <Separator orientation="vertical" className="h-4 bg-zinc-700" />

      {/* Depth */}
      {telemetry && (
        <span className="font-mono text-sm text-cyan-400">
          <span className="text-zinc-500 text-xs mr-1">Z</span>
          {telemetry.sonar_depth_m.toFixed(3)}
          <span className="text-zinc-500 text-xs ml-1">m</span>
        </span>
      )}

      {/* Peak amplitude */}
      {telemetry && (
        <span className="font-mono text-xs text-zinc-400">
          Amp{" "}
          <span className="text-emerald-400">
            {(telemetry.peak_amplitude * 100).toFixed(1)}%
          </span>
        </span>
      )}

      <Separator orientation="vertical" className="h-4 bg-zinc-700" />

      {/* Vision FPS */}
      <Badge variant="outline" className="font-mono text-xs border-zinc-700 text-zinc-400">
        {telemetry ? `${telemetry.fps_vision.toFixed(0)} FPS` : "— FPS"}
      </Badge>

      {/* Ping RTT */}
      <Badge
        variant="outline"
        className={`font-mono text-xs border-zinc-700 ${
          pingMs !== null && pingMs > 100 ? "text-amber-400" : "text-zinc-400"
        }`}
      >
        {pingMs !== null ? `${pingMs} ms RTT` : "— ms RTT"}
      </Badge>

      {/* WS rate */}
      {telemetry && (
        <Badge variant="outline" className="font-mono text-xs border-zinc-700 text-zinc-400">
          {telemetry.ping_rate_hz.toFixed(0)} Hz WS
        </Badge>
      )}
    </header>
  )
}
