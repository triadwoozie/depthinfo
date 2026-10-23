"use client"

import { useState } from "react"
import { Activity, AudioLines, Crosshair, Gauge, Radio } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { AcousticSynthPanel } from "@/components/sonar/AcousticSynthPanel"
import { CalibrationPanel } from "@/components/sonar/CalibrationPanel"
import { CorrelationCanvas } from "@/components/sonar/CorrelationCanvas"
import { ExportDrawer } from "@/components/sonar/ExportDrawer"
import { RigCanvas } from "@/components/sonar/RigCanvas"
import { TelemetryHeader } from "@/components/sonar/TelemetryHeader"
import { VisionPreview } from "@/components/sonar/VisionPreview"
import { useSonarSocket } from "@/hooks/useSonarSocket"

export default function SonarDashboard() {
  const {
    connected,
    telemetry,
    curveRef,
    calibration,
    operatorConfig,
    configError,
    updateCalibration,
    updateOperatorConfig,
    pingMs,
  } = useSonarSocket()
  const [calibrationOpen, setCalibrationOpen] = useState(false)

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <TelemetryHeader
        connected={connected}
        telemetry={telemetry}
        pingMs={pingMs}
        config={operatorConfig}
        onConfigChange={updateOperatorConfig}
        onCalibrate={() => setCalibrationOpen(true)}
      />

      <main className="mx-auto max-w-[1800px] px-3 pb-8 pt-5 sm:px-5 lg:px-6">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[.2em] text-zinc-500">
              <span className="h-px w-5 bg-emerald-500/60" />
              Acoustic imaging system
            </div>
            <h1 className="text-xl font-semibold tracking-tight text-zinc-100 sm:text-2xl">
              Live acquisition <span className="text-zinc-600">/</span>{" "}
              <span className="font-mono text-cyan-200">
                {telemetry?.status === "tracking" || telemetry?.status === "mock"
                  ? "TRACKING"
                  : "STANDBY"}
              </span>
            </h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <MetricPill
              icon={<Crosshair className="h-3.5 w-3.5 text-cyan-300" />}
              label="Range"
              value={`${(telemetry?.sonar_depth_m ?? 0).toFixed(2)} m`}
            />
            <MetricPill
              icon={<Activity className="h-3.5 w-3.5 text-emerald-300" />}
              label="Echo"
              value={`${((telemetry?.peak_amplitude ?? 0) * 100).toFixed(0)}%`}
            />
            <MetricPill
              icon={<Radio className="h-3.5 w-3.5 text-violet-300" />}
              label="Pulse"
              value={`${operatorConfig.ping_rate_hz.toFixed(0)} Hz`}
            />
          </div>
        </div>

        {configError && (
          <div
            role="status"
            className="mb-4 flex items-center gap-2 rounded-xl border border-amber-500/20 bg-amber-500/[0.06] px-4 py-2.5 text-[11px] text-amber-200/80"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
            Configuration sync: {configError}
          </div>
        )}

        <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(310px,360px)_minmax(0,1.25fr)_minmax(300px,0.9fr)]">
          <aside className="grid min-w-0 gap-4">
            <RigCanvas config={operatorConfig} onConfigChange={updateOperatorConfig} />
            <AcousticSynthPanel
              calibration={calibration}
              config={operatorConfig}
              onCalibrationChange={updateCalibration}
              onConfigChange={updateOperatorConfig}
            />
            <ExportDrawer config={operatorConfig} onConfigChange={updateOperatorConfig} />
          </aside>

          <section className="grid min-w-0 gap-4">
            <VisionPreview
              config={operatorConfig}
              onConfigChange={updateOperatorConfig}
            />
          </section>

          <section className="grid min-w-0 content-start gap-4">
            <Card className="instrument-card overflow-hidden">
              <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0 pb-3">
                <div className="flex items-center gap-2.5">
                  <div className="icon-tile text-emerald-300">
                    <AudioLines className="h-4 w-4" />
                  </div>
                  <div>
                    <CardTitle className="instrument-title">Echo analyzer</CardTitle>
                    <p className="instrument-kicker">Matched-filter return · 0–4 m</p>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline" className="instrument-badge text-cyan-200">
                    <Crosshair className="mr-1.5 h-3 w-3" />
                    Peak {telemetry?.sonar_depth_m.toFixed(2) ?? "—"} m
                  </Badge>
                  <Badge variant="outline" className="instrument-badge text-emerald-200">
                    <Gauge className="mr-1.5 h-3 w-3" />
                    Confidence {((telemetry?.peak_amplitude ?? 0) * 100).toFixed(0)}%
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="pt-0">
                <div className="relative h-[300px] overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950/90 shadow-[inset_0_0_40px_rgba(6,182,212,0.025)]">
                  <div className="pointer-events-none absolute inset-0 z-0 bg-[linear-gradient(rgba(63,63,70,.18)_1px,transparent_1px),linear-gradient(90deg,rgba(63,63,70,.18)_1px,transparent_1px)] bg-[size:32px_32px]" />
                  <CorrelationCanvas
                    curveRef={curveRef}
                    blankingDistanceCm={calibration.blanking_distance_cm}
                    detectionThreshold={calibration.detection_threshold}
                    sonarDepthM={telemetry?.sonar_depth_m}
                    onBlankingChange={(blanking_distance_cm) =>
                      updateCalibration({ blanking_distance_cm })
                    }
                    className="relative z-[1] rounded-xl"
                  />
                  <div className="pointer-events-none absolute left-3 top-3 z-[2] rounded-md border border-red-500/20 bg-red-950/35 px-2 py-1 font-mono text-[9px] uppercase tracking-wider text-red-300/80">
                    Blanking {calibration.blanking_distance_cm.toFixed(0)} cm
                  </div>
                  <div className="pointer-events-none absolute right-3 top-3 z-[2] rounded-md border border-amber-500/20 bg-amber-950/30 px-2 py-1 font-mono text-[9px] uppercase tracking-wider text-amber-200/80">
                    Gate {(calibration.detection_threshold * 100).toFixed(0)}%
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[9px] uppercase tracking-wider text-zinc-600">
                  <span>Zone A · direct bleed blanking</span>
                  <span className="font-mono">256 BINS · 0.0–4.0 M</span>
                  <span>Zone B · echo returns</span>
                </div>
              </CardContent>
            </Card>
          </section>
        </div>

        <footer className="mt-5 flex flex-wrap items-center justify-between gap-2 border-t border-zinc-900 pt-3 font-mono text-[9px] uppercase tracking-wider text-zinc-700">
          <span>Sonar Imaging Instrument Console</span>
          <span>
            {connected ? "WEBSOCKET LINK ACTIVE" : "AWAITING BACKEND"} ·{" "}
            {telemetry?.mock_mode ? "SYNTHETIC INPUT" : "HARDWARE INPUT"}
          </span>
        </footer>
      </main>

      <Dialog open={calibrationOpen} onOpenChange={setCalibrationOpen}>
        <DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto border-zinc-800 bg-zinc-950 p-4 text-zinc-100 sm:p-6">
          <DialogHeader className="pr-8">
            <DialogTitle className="text-sm font-semibold uppercase tracking-wider">
              Calibration controls
            </DialogTitle>
            <DialogDescription className="text-xs text-zinc-500">
              Adjust chirp timing, direct-path blanking, echo threshold and depth smoothing.
            </DialogDescription>
          </DialogHeader>
          <CalibrationPanel calibration={calibration} onChange={updateCalibration} />
        </DialogContent>
      </Dialog>
    </div>
  )
}

function MetricPill({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode
  label: string
  value: string
}) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-zinc-800/80 bg-zinc-900/40 px-3 py-2">
      {icon}
      <span className="text-[9px] uppercase tracking-wider text-zinc-500">{label}</span>
      <span className="font-mono text-xs tabular-nums text-zinc-200">{value}</span>
    </div>
  )
}
