"use client"

import { useEffect, useState } from "react"
import {
  AudioLines,
  SlidersHorizontal,
  Volume2,
  VolumeX,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
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
      className={`sticky top-0 z-30 border-b bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/60 lg:px-6 ${className ?? ""}`}
    >
      <div className="flex min-h-14 flex-wrap items-center gap-x-6 gap-y-2 py-2">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-md border bg-muted">
            <AudioLines className="h-4 w-4" />
          </div>
          <div className="leading-tight">
            <h1 className="text-sm font-semibold">Sonar Imaging</h1>
            <p className="text-xs text-muted-foreground">Field console · v0.1.0</p>
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
          <StatusBadge label="UDP 5005" value={udpActive ? "TX" : "Idle"} active={udpActive} />
          {telemetry?.mock_mode && <Badge variant="secondary">Mock mode</Badge>}
        </div>

        <div className="ml-auto flex flex-wrap items-center justify-end gap-4">
          <div className="flex items-center gap-2">
            <Volume2 className="h-4 w-4 shrink-0 text-muted-foreground" />
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
            <span className="w-9 text-right text-xs tabular-nums text-muted-foreground">
              {Math.round(config.speaker_volume * 100)}%
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Switch
              id="silent-mode"
              aria-label="Ultrasonic silent mode"
              checked={config.silent_mode}
              onCheckedChange={(silent_mode) => onConfigChange({ silent_mode })}
            />
            <Label htmlFor="silent-mode" className="hidden items-center gap-1.5 text-sm md:flex">
              {config.silent_mode && <VolumeX className="h-4 w-4" />}
              Silent mode
            </Label>
          </div>
          <Button onClick={onCalibrate} size="sm">
            <SlidersHorizontal className="mr-2 h-4 w-4" />
            Calibrate
          </Button>
        </div>
      </div>
      {pingMs !== null && (
        <span className="sr-only">Frame age {pingMs} milliseconds</span>
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
  const dot = warning ? "bg-amber-500" : active ? "bg-emerald-500" : "bg-muted-foreground/50"
  return (
    <Badge variant="outline" className="gap-2 font-normal">
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </Badge>
  )
}
