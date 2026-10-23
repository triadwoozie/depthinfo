"use client"

import { useEffect, useState } from "react"
import {
  Activity,
  AudioLines,
  SlidersHorizontal,
  Volume2,
  VolumeX,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { apiUrl } from "@/lib/api"
import { OperatorConfig, SonarTelemetry } from "@/types/sonar"

interface TelemetryHeaderProps {
  connected: boolean
  telemetry: Omit<SonarTelemetry, "correlation_curve"> | null
  pingMs: number | null
  config: OperatorConfig
  onConfigChange: (patch: Partial<OperatorConfig>) => void
  onCalibrate: () => void
  className?: string
}

export function TelemetryHeader({
  connected,
  telemetry,
  pingMs,
  config,
  onConfigChange,
  onCalibrate,
  className,
}: TelemetryHeaderProps) {
  const [udpActive, setUdpActive] = useState(false)
  const visionFps = telemetry?.fps_vision ?? 0

  useEffect(() => {
    let active = true
    const checkBridge = async () => {
      try {
        const response = await fetch(apiUrl("/healthz"))
        if (!response.ok) return
        const health = (await response.json()) as { udp_sent?: number }
        if (active) setUdpActive((health.udp_sent ?? 0) > 0)
      } catch {
        if (active) setUdpActive(false)
      }
    }
    void checkBridge()
    const timer = setInterval(checkBridge, 5000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [])

  return (
    <header
      className={`sticky top-0 z-30 border-b border-zinc-800/80 bg-zinc-950/95 px-4 py-3 backdrop-blur-xl lg:px-6 ${className ?? ""}`}
    >
      <div className="mx-auto flex max-w-[1800px] flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex min-w-[225px] items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-xl border border-emerald-500/20 bg-emerald-500/10 text-emerald-300 shadow-[0_0_24px_rgba(16,185,129,0.08)]">
            <AudioLines className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold tracking-[0.18em] text-zinc-100">
                SONAR IMAGING
              </span>
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]" />
            </div>
            <span className="mt-1 block font-mono text-[10px] tracking-wider text-zinc-500">
              FIELD CONSOLE <span className="text-zinc-700">/</span> v0.1.0
            </span>
          </div>
        </div>

        <div className="flex flex-1 flex-wrap items-center gap-2">
          <StatusBadge label="Mic" value={connected ? "Active" : "Offline"} active={connected} />
          <StatusBadge
            label="Speaker"
            value={config.silent_mode ? "Muted" : "Armed"}
            active={connected && !config.silent_mode}
            warning={config.silent_mode}
          />
          <StatusBadge
            label="Vision"
            value={visionFps > 0 ? `${visionFps.toFixed(0)} FPS` : "Standby"}
            active={visionFps > 0}
          />
          <StatusBadge
            label="UDP 5005"
            value={udpActive ? "TX" : "Idle"}
            active={udpActive}
          />
          {telemetry?.mock_mode && (
            <Badge className="h-7 rounded-lg border border-amber-500/20 bg-amber-500/10 px-2.5 font-mono text-[10px] uppercase tracking-wider text-amber-300 hover:bg-amber-500/10">
              Mock mode
            </Badge>
          )}
        </div>

        <div className="ml-auto flex flex-wrap items-center justify-end gap-4">
          <div className="flex min-w-[140px] items-center gap-2 sm:min-w-[150px] sm:gap-3">
            <Volume2 className="h-4 w-4 shrink-0 text-zinc-500" />
            <Slider
              aria-label="Master volume"
              value={[config.speaker_volume]}
              min={0}
              max={1}
              step={0.01}
              disabled={config.silent_mode}
              onValueChange={([value]) => onConfigChange({ speaker_volume: value })}
              className="w-24"
            />
            <span className="w-8 text-right font-mono text-[10px] tabular-nums text-zinc-400">
              {Math.round(config.speaker_volume * 100)}%
            </span>
          </div>
          <div className="flex items-center gap-2">
            {config.silent_mode ? (
              <VolumeX className="h-4 w-4 text-amber-300" />
            ) : (
              <Activity className="h-4 w-4 text-zinc-500" />
            )}
            <span className="hidden text-[10px] uppercase tracking-wider text-zinc-400 xl:block">
              Silent mode
            </span>
            <Switch
              aria-label="Ultrasonic silent mode"
              checked={config.silent_mode}
              onCheckedChange={(silent_mode) => onConfigChange({ silent_mode })}
            />
          </div>
          <Button
            onClick={onCalibrate}
            size="sm"
            className="h-9 rounded-lg bg-emerald-600 px-3 text-xs font-semibold tracking-wide text-white shadow-[0_0_18px_rgba(16,185,129,0.16)] hover:bg-emerald-500"
          >
            <SlidersHorizontal className="mr-2 h-3.5 w-3.5" />
            Calibrate
          </Button>
        </div>
      </div>
      {pingMs !== null && (
        <div className="absolute right-6 top-[3px] hidden items-center gap-1 font-mono text-[9px] text-zinc-600 2xl:flex">
          FRAME AGE {pingMs} ms
        </div>
      )}
    </header>
  )
}

function StatusBadge({
  label,
  value,
  active,
  warning = false,
}: {
  label: string
  value: string
  active: boolean
  warning?: boolean
}) {
  const color = warning
    ? "border-amber-500/20 bg-amber-500/[0.06] text-amber-300"
    : active
      ? "border-emerald-500/20 bg-emerald-500/[0.06] text-emerald-300"
      : "border-zinc-800 bg-zinc-900/60 text-zinc-500"
  return (
    <Badge
      variant="outline"
      className={`h-7 gap-2 rounded-lg px-2.5 font-mono text-[10px] tracking-wide ${color}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${active ? "bg-emerald-400" : "bg-zinc-600"}`} />
      <span className="text-zinc-500">{label}</span>
      <span>{value}</span>
    </Badge>
  )
}
