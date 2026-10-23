/**
 * page.tsx — Sonar Imaging Main Dashboard Page.
 *
 * Layout:
 *   ┌─────────────────────────────────────────────────────┐
 *   │  TelemetryHeader (status badges, depth, fps, ping)  │
 *   ├─────────────────────────────────────┬───────────────┤
 *   │  CorrelationCanvas (left, 60fps)    │ CalibPanel    │
 *   │                                     │ (right, slim) │
 *   ├─────────────────────────────────────┤               │
 *   │  VisionPreview (MJPEG, below curve) │               │
 *   └─────────────────────────────────────┴───────────────┘
 */

"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { CalibrationPanel } from "@/components/sonar/CalibrationPanel"
import { CorrelationCanvas } from "@/components/sonar/CorrelationCanvas"
import { TelemetryHeader } from "@/components/sonar/TelemetryHeader"
import { VisionPreview } from "@/components/sonar/VisionPreview"
import { useSonarSocket } from "@/hooks/useSonarSocket"

export default function SonarDashboard() {
  const { connected, telemetry, curveRef, calibration, updateCalibration, pingMs } =
    useSonarSocket()

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 flex flex-col">
      {/* ── Header ────────────────────────────────────────────── */}
      <TelemetryHeader
        connected={connected}
        telemetry={telemetry}
        pingMs={pingMs}
      />

      {/* ── Main grid ─────────────────────────────────────────── */}
      <div className="flex flex-1 gap-4 p-4 overflow-hidden">
        {/* Left column: canvas + vision preview */}
        <div className="flex flex-col flex-1 gap-4 min-w-0">
          {/* Correlation Canvas */}
          <Card className="bg-zinc-900 border-zinc-800 flex-1 min-h-[260px]">
            <CardHeader className="pb-2 flex flex-row items-center justify-between">
              <CardTitle className="text-sm font-mono text-zinc-300 tracking-widest uppercase">
                Cross-Correlation
              </CardTitle>
              <span className="font-mono text-xs text-zinc-500">0 m → 4 m</span>
            </CardHeader>
            <CardContent className="p-2 h-[calc(100%-3.5rem)]">
              <CorrelationCanvas
                curveRef={curveRef}
                blankingDistanceCm={calibration.blanking_distance_cm}
                detectionThreshold={calibration.detection_threshold}
                sonarDepthM={telemetry?.sonar_depth_m}
                className="rounded"
              />
            </CardContent>
          </Card>

          {/* Vision feed */}
          <VisionPreview fps={telemetry?.fps_vision} />
        </div>

        {/* Right column: calibration panel */}
        <div className="w-72 shrink-0 overflow-y-auto">
          <CalibrationPanel
            calibration={calibration}
            onChange={updateCalibration}
          />
        </div>
      </div>
    </div>
  )
}
