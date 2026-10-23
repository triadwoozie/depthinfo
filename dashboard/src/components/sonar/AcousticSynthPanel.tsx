"use client"

import { memo } from "react"
import { Radio } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import { CalibrationState, OperatorConfig, WaveformType } from "@/types/sonar"

interface AcousticSynthPanelProps {
  calibration: CalibrationState
  config: OperatorConfig
  onCalibrationChange: (patch: Partial<CalibrationState>) => void
  onConfigChange: (patch: Partial<OperatorConfig>) => void
}

function AcousticSynthPanelView({
  calibration,
  config,
  onCalibrationChange,
  onConfigChange,
}: AcousticSynthPanelProps) {
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle>Acoustic synth</CardTitle>
          <CardDescription>Emission and echo gate</CardDescription>
        </div>
        <Badge variant="outline" className="gap-1.5 font-normal">
          <Radio className="h-3 w-3" />
          48 kHz
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <FieldHeading label="Waveform" value={config.waveform.replaceAll("_", " ")} />
          <Select
            value={config.waveform}
            onValueChange={(waveform) =>
              onConfigChange({ waveform: waveform as WaveformType })
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="linear_fm_chirp">Linear FM chirp</SelectItem>
              <SelectItem value="log_chirp">Logarithmic chirp</SelectItem>
              <SelectItem value="audible_ping">Audible ping</SelectItem>
              <SelectItem value="ultrasonic_sweep">Ultrasonic sweep</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <FieldHeading
            label="Sweep start"
            value={`${(calibration.chirp_f_start / 1000).toFixed(1)} kHz`}
          />
          <Slider
            aria-label="Sweep start frequency"
            value={[calibration.chirp_f_start]}
            min={8000}
            max={22000}
            step={100}
            onValueChange={([chirp_f_start]) => onCalibrationChange({ chirp_f_start })}
          />
        </div>

        <div className="space-y-2">
          <FieldHeading
            label="Sweep end"
            value={`${(calibration.chirp_f_end / 1000).toFixed(1)} kHz`}
          />
          <Slider
            aria-label="Sweep end frequency"
            value={[calibration.chirp_f_end]}
            min={8000}
            max={22050}
            step={100}
            onValueChange={([chirp_f_end]) => onCalibrationChange({ chirp_f_end })}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <FieldHeading label="Pulse length" value={`${calibration.chirp_duration_ms} ms`} />
            <Slider
              aria-label="Pulse duration"
              value={[calibration.chirp_duration_ms]}
              min={5}
              max={100}
              step={1}
              onValueChange={([chirp_duration_ms]) =>
                onCalibrationChange({ chirp_duration_ms })
              }
            />
          </div>
          <div className="space-y-2">
            <FieldHeading label="Ping rate" value={`${config.ping_rate_hz.toFixed(0)} Hz`} />
            <Slider
              aria-label="Ping rate"
              value={[config.ping_rate_hz]}
              min={1}
              max={30}
              step={1}
              onValueChange={([ping_rate_hz]) => onConfigChange({ ping_rate_hz })}
            />
          </div>
        </div>

        <div className="space-y-2">
          <FieldHeading
            label="Echo threshold"
            value={`${Math.round(calibration.detection_threshold * 100)}%`}
          />
          <div>
            <Slider
              aria-label="Echo detection threshold"
              value={[calibration.detection_threshold]}
              min={0.01}
              max={1}
              step={0.01}
              onValueChange={([detection_threshold]) =>
                onCalibrationChange({ detection_threshold })
              }
            />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function FieldHeading({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <Label className="text-sm">{label}</Label>
      <span className="text-sm tabular-nums text-muted-foreground">{value}</span>
    </div>
  )
}

export const AcousticSynthPanel = memo(AcousticSynthPanelView)
