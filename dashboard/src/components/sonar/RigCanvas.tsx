"use client"

import { memo, useEffect, useState } from "react"
import { Camera, ChevronDown, Mic2, Radio, ScanLine } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { apiUrl } from "@/lib/api"
import {
  DeviceList,
  OperatorConfig,
  RawDeviceList,
  RigPose,
  normalizeDevices,
} from "@/types/sonar"

interface RigCanvasProps {
  config: OperatorConfig
  onConfigChange: (patch: Partial<OperatorConfig>) => void
}

const deviceAxes = ["x_cm", "y_cm", "z_cm"] as const

function RigCanvasView({ config, onConfigChange }: RigCanvasProps) {
  const [devices, setDevices] = useState<DeviceList>({
    audio_inputs: [],
    audio_outputs: [],
    cameras: [],
  })
  const [deviceError, setDeviceError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    fetch(apiUrl("/api/devices"))
      .then(async (response) => {
        if (!response.ok) throw new Error(`Device scan unavailable (${response.status})`)
        return normalizeDevices((await response.json()) as RawDeviceList)
      })
      .then((result) => {
        if (active) {
          setDevices(result)
          setDeviceError(null)
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setDeviceError(error instanceof Error ? error.message : "Device scan unavailable")
        }
      })
    return () => {
      active = false
    }
  }, [])

  const updatePose = (device: keyof OperatorConfig["rig"], axis: keyof RigPose, value: number) => {
    onConfigChange({
      rig: {
        ...config.rig,
        [device]: { ...config.rig[device], [axis]: value },
      },
    })
  }

  return (
    <Card className="instrument-card">
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <div className="flex items-center gap-2.5">
          <div className="icon-tile text-emerald-300">
            <ScanLine className="h-4 w-4" />
          </div>
          <div>
            <CardTitle className="instrument-title">Hardware &amp; rig</CardTitle>
            <p className="instrument-kicker">Device routing · top view</p>
          </div>
        </div>
        <Badge variant="outline" className="instrument-badge">
          <span className="mr-1.5 h-1.5 w-1.5 rounded-full bg-emerald-400" />
          RIG CONFIG
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="relative mx-auto flex aspect-square max-h-[260px] w-full items-center justify-center overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950/60">
          <svg
            width="100%"
            height="100%"
            viewBox="-100 -100 200 200"
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label="Top-down sensor rig showing camera field of view, speaker and microphone positions"
          >
            <defs>
              <pattern id="rig-grid" x="-100" y="-100" width="20" height="20" patternUnits="userSpaceOnUse">
                <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#27272a" strokeWidth="0.4" />
              </pattern>
              <radialGradient id="rig-glow">
                <stop offset="0%" stopColor="#10b981" stopOpacity="0.16" />
                <stop offset="100%" stopColor="#10b981" stopOpacity="0" />
              </radialGradient>
              <linearGradient id="camera-cone" x1="0" y1="1" x2="0" y2="0">
                <stop offset="0%" stopColor="#06b6d4" stopOpacity="0.03" />
                <stop offset="100%" stopColor="#06b6d4" stopOpacity="0.18" />
              </linearGradient>
            </defs>
            <rect x="-100" y="-100" width="200" height="200" fill="url(#rig-grid)" />
            <circle cx="0" cy="0" r="80" fill="url(#rig-glow)" />
            <line x1="0" y1="-96" x2="0" y2="96" stroke="#3f3f46" strokeWidth="0.5" strokeDasharray="2 3" />
            <line x1="-96" y1="0" x2="96" y2="0" stroke="#3f3f46" strokeWidth="0.5" strokeDasharray="2 3" />
            <g transform={`translate(${toX(config.rig.camera.x_cm)} ${toY(config.rig.camera.y_cm)})`}>
              <path d="M0 0 L-40 -80 Q0 -96 40 -80 Z" fill="url(#camera-cone)" stroke="#0891b2" strokeOpacity=".65" strokeWidth="0.6" />
              <circle r="22" fill="none" stroke="#10b981" strokeOpacity=".28" strokeWidth="0.5" />
              <circle r="42" fill="none" stroke="#10b981" strokeOpacity=".18" strokeWidth="0.5" strokeDasharray="2 3" />
            </g>
            <SensorMarker
              x={toX(config.rig.speaker.x_cm)}
              y={toY(config.rig.speaker.y_cm)}
              color="#f59e0b"
              label="SPK"
              icon="speaker"
            />
            <SensorMarker
              x={toX(config.rig.mic.x_cm)}
              y={toY(config.rig.mic.y_cm)}
              color="#34d399"
              label="MIC"
              icon="mic"
            />
            <SensorMarker
              x={toX(config.rig.camera.x_cm)}
              y={toY(config.rig.camera.y_cm)}
              color="#22d3ee"
              label="CAM"
              icon="camera"
            />
            <text x="-94" y="95" fill="#71717a" fontSize="5" fontFamily="monospace">
              TOP VIEW · CM
            </text>
            <text x="82" y="-90" fill="#71717a" fontSize="5" fontFamily="monospace">
              +Y
            </text>
          </svg>
        </div>

        <div className="grid min-w-0 gap-2.5">
          <DeviceSelect
            icon={<Mic2 className="h-3.5 w-3.5 text-emerald-300" />}
            label="Studio microphone"
            selectedId={config.audio_input_device_id}
            devices={devices.audio_inputs}
            onChange={(audio_input_device_id) => onConfigChange({ audio_input_device_id })}
          />
          <DeviceSelect
            icon={<Radio className="h-3.5 w-3.5 text-amber-300" />}
            label="Sonar speaker"
            selectedId={config.audio_output_device_id}
            devices={devices.audio_outputs}
            onChange={(audio_output_device_id) => onConfigChange({ audio_output_device_id })}
          />
          <DeviceSelect
            icon={<Camera className="h-3.5 w-3.5 text-cyan-300" />}
            label="Optical camera"
            selectedId={config.camera_device_index}
            devices={devices.cameras}
            onChange={(camera_device_index) => onConfigChange({ camera_device_index })}
          />
        </div>

        {deviceError && (
          <p className="rounded-lg border border-amber-500/15 bg-amber-500/[0.05] px-3 py-2 text-[10px] text-amber-300/80">
            {deviceError}
          </p>
        )}

        <details className="group rounded-xl border border-zinc-800/70 bg-zinc-950/40">
          <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-400 marker:hidden">
            Fine tune sensor positions
            <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
          </summary>
          <div className="space-y-4 border-t border-zinc-800/70 px-3 py-3">
            {(["camera", "speaker", "mic"] as const).map((device) => (
              <div key={device} className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                    {device}
                  </span>
                  <span className="font-mono text-[9px] text-zinc-600">
                    {deviceAxes
                      .map((axis) => config.rig[device][axis].toFixed(0))
                      .join(" / ")}{" "}
                    cm
                  </span>
                </div>
                {deviceAxes.map((axis) => (
                  <div key={axis} className="grid grid-cols-[16px_1fr_36px] items-center gap-2">
                    <span className="font-mono text-[9px] uppercase text-zinc-600">
                      {axis[0]}
                    </span>
                    <Slider
                      aria-label={`${device} ${axis}`}
                      value={[config.rig[device][axis]]}
                      min={-100}
                      max={100}
                      step={1}
                      onValueChange={([value]) => updatePose(device, axis, value)}
                    />
                    <span className="text-right font-mono text-[9px] tabular-nums text-zinc-400">
                      {config.rig[device][axis]}
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </details>
      </CardContent>
    </Card>
  )
}

function DeviceSelect({
  icon,
  label,
  selectedId,
  devices,
  onChange,
}: {
  icon: React.ReactNode
  label: string
  selectedId: number | null
  devices: DeviceList[keyof DeviceList]
  onChange: (value: number | null) => void
}) {
  return (
    <div className="flex min-w-0 items-center gap-2.5 overflow-hidden rounded-lg border border-zinc-800/70 bg-zinc-950/50 px-2.5">
      {icon}
      <span className="w-[116px] shrink-0 text-[10px] text-zinc-500">{label}</span>
      <Select
        value={selectedId === null ? "default" : String(selectedId)}
        onValueChange={(value) =>
          onChange(value === "default" ? null : Number(value))
        }
      >
        <SelectTrigger className="h-8 min-w-0 flex-1 overflow-hidden border-0 bg-transparent px-1 text-[10px] text-zinc-300 shadow-none focus:ring-0">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="default">System default</SelectItem>
          {devices.map((device) => (
            <SelectItem key={device.id} value={String(device.id)}>
              {device.name}
            </SelectItem>
          ))}
          {devices.length === 0 && (
            <SelectItem value="unavailable" disabled>
              No devices detected
            </SelectItem>
          )}
        </SelectContent>
      </Select>
    </div>
  )
}

function SensorMarker({
  x,
  y,
  color,
  label,
  icon,
}: {
  x: number
  y: number
  color: string
  label: string
  icon: "camera" | "speaker" | "mic"
}) {
  const shape =
    icon === "camera"
      ? "M-6 -4 H2 L5 0 L2 4 H-6 Z"
      : icon === "speaker"
        ? "M-5 -5 L5 0 L-5 5 Z"
        : "M0 -6 A4 4 0 0 0 -4 -2 V1 A4 4 0 0 0 4 1 V-2 A4 4 0 0 0 0 -6 M-6 1 A6 6 0 0 0 6 1 M0 7 V10"
  return (
    <g transform={`translate(${x} ${y})`}>
      <circle r="7" fill={color} fillOpacity=".12" stroke={color} strokeOpacity=".75" strokeWidth="0.6" />
      <path d={shape} transform="scale(.55)" fill="none" stroke={color} strokeWidth="2.2" strokeLinejoin="round" />
      <text x="9" y="2" fill={color} fontSize="5" fontFamily="monospace">
        {label}
      </text>
    </g>
  )
}

/** Rig centimetres (±100) → SVG units in the centred 200×200 viewBox; +Y points up. */
function toX(cm: number) {
  return Math.max(-86, Math.min(86, cm * 0.9))
}

function toY(cm: number) {
  return Math.max(-86, Math.min(86, -cm * 0.9))
}

export const RigCanvas = memo(RigCanvasView)
