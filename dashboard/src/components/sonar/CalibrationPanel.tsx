/**
 * CalibrationPanel.tsx — shadcn/ui Slider controls for runtime calibration.
 *
 * All slider changes are immediately reflected in the local calibration state
 * (for snappy UI) and debounced before being sent to the backend via WebSocket
 * (handled inside useSonarSocket.updateCalibration).
 *
 * Groups:
 *   1. Chirp — frequency sweep and duration
 *   2. Gating — blanking distance and detection threshold
 *   3. Depth — scale multiplier, zero offset, EMA smoothing
 *   4. Autopilot — idle timeout
 */

"use client"

import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Slider } from "@/components/ui/slider"
import { CalibrationState } from "@/types/sonar"

interface CalibrationPanelProps {
  calibration: CalibrationState
  onChange: (patch: Partial<CalibrationState>) => void
  className?: string
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function SliderRow({
  label,
  value,
  min,
  max,
  step,
  unit,
  formatValue,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  unit?: string
  formatValue?: (v: number) => string
  onChange: (v: number) => void
}) {
  const display = formatValue ? formatValue(value) : value.toFixed(step < 1 ? 2 : 0)

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <Label className="text-sm">{label}</Label>
        <span className="text-sm tabular-nums text-muted-foreground">
          {display}
          {unit && <span className="ml-0.5">{unit}</span>}
        </span>
      </div>
      <Slider
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={([v]) => onChange(v)}
      />
    </div>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="pt-1 text-sm font-semibold">{children}</h4>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function CalibrationPanel({ calibration: cal, onChange, className }: CalibrationPanelProps) {
  return (
    <div className={className}>
      <div className="flex flex-col gap-4">
        {/* ── 1. Chirp ──────────────────────────────── */}
        <SectionTitle>Chirp Synthesis</SectionTitle>

        <SliderRow
          label="F Start"
          value={cal.chirp_f_start}
          min={8000}
          max={22000}
          step={100}
          unit="Hz"
          formatValue={(v) => `${(v / 1000).toFixed(1)}k`}
          onChange={(v) => onChange({ chirp_f_start: v })}
        />
        <SliderRow
          label="F End"
          value={cal.chirp_f_end}
          min={8000}
          max={22050}
          step={100}
          unit="Hz"
          formatValue={(v) => `${(v / 1000).toFixed(1)}k`}
          onChange={(v) => onChange({ chirp_f_end: v })}
        />
        <SliderRow
          label="Duration"
          value={cal.chirp_duration_ms}
          min={5}
          max={100}
          step={1}
          unit="ms"
          onChange={(v) => onChange({ chirp_duration_ms: v })}
        />

        <Separator />

        {/* ── 2. Gating ─────────────────────────────── */}
        <SectionTitle>Acoustic Gating</SectionTitle>

        <SliderRow
          label="Blanking"
          value={cal.blanking_distance_cm}
          min={0}
          max={200}
          step={1}
          unit="cm"
          onChange={(v) => onChange({ blanking_distance_cm: v })}
        />
        <SliderRow
          label="Threshold"
          value={cal.detection_threshold}
          min={0.01}
          max={1.0}
          step={0.01}
          formatValue={(v) => `${(v * 100).toFixed(0)}%`}
          onChange={(v) => onChange({ detection_threshold: v })}
        />

        <Separator />

        {/* ── 3. Depth post-processing ──────────────── */}
        <SectionTitle>Depth Post-Processing</SectionTitle>

        <SliderRow
          label="Scale ×"
          value={cal.depth_scale_multiplier}
          min={0.1}
          max={5.0}
          step={0.05}
          onChange={(v) => onChange({ depth_scale_multiplier: v })}
        />
        <SliderRow
          label="Offset"
          value={cal.depth_zero_offset_cm}
          min={-200}
          max={200}
          step={1}
          unit="cm"
          onChange={(v) => onChange({ depth_zero_offset_cm: v })}
        />
        <SliderRow
          label="EMA α"
          value={cal.smoothing_alpha}
          min={0.01}
          max={1.0}
          step={0.01}
          formatValue={(v) => v.toFixed(2)}
          onChange={(v) => onChange({ smoothing_alpha: v })}
        />

        <Separator />

        {/* ── 4. Autopilot ──────────────────────────── */}
        <SectionTitle>Autopilot</SectionTitle>

        <SliderRow
          label="Idle Timeout"
          value={cal.autopilot_timeout_sec}
          min={1}
          max={120}
          step={1}
          unit="s"
          onChange={(v) => onChange({ autopilot_timeout_sec: v })}
        />
      </div>
    </div>
  )
}
