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
import { DeviceList, OperatorConfig, RigPose } from "@/types/sonar"

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
    fetch("http://localhost:8000/api/devices")
      .then(async (response) => {
        if (!response.ok) throw new Error(`Device scan unavailable (${response.status})`)
        return (await response.json()) as DeviceList
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
        <div className="rounded-xl border border-zinc-800/70 bg-zinc-950/75 p-2">
          <svg
            viewBox="0 0 420 230"
            role="img"
            aria-label="Top-down sensor rig showing camera field of view, speaker and microphone positions"
            className="h-44 w-full"
          >
            <defs>
              <pattern id="rig-grid" width="24" height="24" patternUnits="userSpaceOnUse">
                <path d="M 24 0 L 0 0 0 24" fill="none" stroke="#27272a" strokeWidth="0.7" />
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
            <rect width="420" height="230" rx="12" fill="url(#rig-grid)" />
            <ellipse cx="210" cy="116" rx="155" ry="110" fill="url(#rig-glow)" />
            <line x1="210" y1="12" x2="210" y2="218" stroke="#3f3f46" strokeDasharray="3 5" />
            <line x1="14" y1="116" x2="406" y2="116" stroke="#3f3f46" strokeDasharray="3 5" />
            <path d="M210 154 L139 30 Q210 2 281 30 Z" fill="url(#camera-cone)" stroke="#0891b2" strokeOpacity=".65" />
            <circle cx="210" cy="154" r="42" fill="none" stroke="#10b981" strokeOpacity=".28" />
            <circle cx="210" cy="154" r="76" fill="none" stroke="#10b981" strokeOpacity=".18" strokeDasharray="3 6" />
            <SensorMarker
              x={toCanvasX(config.rig.speaker.x_cm)}
              y={toCanvasY(config.rig.speaker.y_cm)}
              color="#f59e0b"
              label="SPK"
              icon="speaker"
            />
            <SensorMarker
              x={toCanvasX(config.rig.mic.x_cm)}
              y={toCanvasY(config.rig.mic.y_cm)}
              color="#34d399"
              label="MIC"
              icon="mic"
            />
            <SensorMarker
              x={toCanvasX(config.rig.camera.x_cm)}
              y={toCanvasY(config.rig.camera.y_cm)}
              color="#22d3ee"
              label="CAM"
              icon="camera"
            />
            <text x="16" y="216" fill="#71717a" fontSize="9" fontFamily="monospace">
              TOP VIEW · CM
            </text>
            <text x="380" y="14" fill="#71717a" fontSize="9" fontFamily="monospace">
              +Y
            </text>
          </svg>
        </div>

        <div className="grid gap-2.5">
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
    <div className="flex items-center gap-2.5 rounded-lg border border-zinc-800/70 bg-zinc-950/50 px-2.5">
      {icon}
      <span className="w-[116px] shrink-0 text-[10px] text-zinc-500">{label}</span>
      <Select
        value={selectedId === null ? "default" : String(selectedId)}
        onValueChange={(value) =>
          onChange(value === "default" ? null : Number(value))
        }
      >
        <SelectTrigger className="h-8 min-w-0 flex-1 border-0 bg-transparent px-1 text-[10px] text-zinc-300 shadow-none focus:ring-0">
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
      <circle r="12" fill={color} fillOpacity=".12" stroke={color} strokeOpacity=".75" />
      <path d={shape} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
      <text x="14" y="3" fill={color} fontSize="8" fontFamily="monospace">
        {label}
      </text>
    </g>
  )
}

function toCanvasX(cm: number) {
  return Math.max(28, Math.min(392, 210 + cm * 2))
}

function toCanvasY(cm: number) {
  return Math.max(26, Math.min(210, 154 - cm * 2))
}

export const RigCanvas = memo(RigCanvasView)
