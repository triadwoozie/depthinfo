"use client"

import { useState } from "react"
import { Activity, AudioLines, Box, Crosshair, Radio } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
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

  const tracking = telemetry?.status === "tracking" || telemetry?.status === "mock"

  return (
    <div className="flex min-h-screen flex-col overflow-y-auto bg-background text-foreground xl:h-screen xl:max-h-screen xl:overflow-hidden">
      <TelemetryHeader
        connected={connected}
        telemetry={telemetry}
        pingMs={pingMs}
        config={operatorConfig}
        onConfigChange={updateOperatorConfig}
        onCalibrate={() => setCalibrationOpen(true)}
      />

      <main className="flex min-h-0 flex-1 flex-col gap-4 p-4 lg:p-6">
        {configError && (
          <Alert className="shrink-0">
            <AlertDescription>Configuration sync: {configError}</AlertDescription>
          </Alert>
        )}

        <div className="grid shrink-0 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            title="Range"
            icon={<Crosshair className="h-4 w-4 text-muted-foreground" />}
            value={`${(telemetry?.sonar_depth_m ?? 0).toFixed(2)} m`}
            hint={tracking ? "Tracking a target" : "Standby"}
          />
          <StatCard
            title="Echo strength"
            icon={<Activity className="h-4 w-4 text-muted-foreground" />}
            value={`${((telemetry?.peak_amplitude ?? 0) * 100).toFixed(0)}%`}
            hint={`Gate ${(calibration.detection_threshold * 100).toFixed(0)}%`}
          />
          <StatCard
            title="Pulse rate"
            icon={<Radio className="h-4 w-4 text-muted-foreground" />}
            value={`${operatorConfig.ping_rate_hz.toFixed(0)} Hz`}
            hint={operatorConfig.silent_mode ? "Speaker muted" : "Speaker armed"}
          />
          <StatCard
            title="Point cloud"
            icon={<Box className="h-4 w-4 text-muted-foreground" />}
            value={(telemetry?.active_vertex_count ?? 0).toLocaleString()}
            hint="Stitched vertices"
          />
        </div>

        <div className="grid min-h-0 grid-cols-1 gap-4 xl:flex-1 xl:grid-cols-[minmax(300px,340px)_minmax(0,1fr)_minmax(300px,380px)]">
          <aside className="min-w-0 space-y-4 xl:h-full xl:overflow-y-auto xl:pr-1">
            <RigCanvas config={operatorConfig} onConfigChange={updateOperatorConfig} />
            <AcousticSynthPanel
              calibration={calibration}
              config={operatorConfig}
              onCalibrationChange={updateCalibration}
              onConfigChange={updateOperatorConfig}
            />
            <ExportDrawer config={operatorConfig} onConfigChange={updateOperatorConfig} />
          </aside>

          <section className="min-w-0 xl:h-full xl:overflow-y-auto">
            <VisionPreview config={operatorConfig} onConfigChange={updateOperatorConfig} />
          </section>

          <section className="flex min-w-0 flex-col gap-4 xl:h-full xl:overflow-hidden">
            <Card>
              <CardHeader className="flex-row items-start justify-between space-y-0">
                <div className="space-y-1.5">
                  <CardTitle className="flex items-center gap-2">
                    <AudioLines className="h-4 w-4" />
                    Echo analyzer
                  </CardTitle>
                  <CardDescription>Matched-filter return, 0 to 4 m</CardDescription>
                </div>
                <Badge variant="outline" className="font-normal tabular-nums">
                  Peak {telemetry?.sonar_depth_m.toFixed(2) ?? "—"} m
                </Badge>
              </CardHeader>
              <CardContent>
                <div className="relative h-[300px] overflow-hidden rounded-md border bg-muted/20">
                  <CorrelationCanvas
                    curveRef={curveRef}
                    blankingDistanceCm={calibration.blanking_distance_cm}
                    detectionThreshold={calibration.detection_threshold}
                    sonarDepthM={telemetry?.sonar_depth_m}
                    onBlankingChange={(blanking_distance_cm) =>
                      updateCalibration({ blanking_distance_cm })
                    }
                    className="relative z-[1] rounded-md"
                  />
                  <Badge
                    variant="destructive"
                    className="pointer-events-none absolute left-3 top-3 z-[2] font-normal"
                  >
                    Blanking {calibration.blanking_distance_cm.toFixed(0)} cm
                  </Badge>
                  <Badge
                    variant="secondary"
                    className="pointer-events-none absolute right-3 top-3 z-[2] font-normal"
                  >
                    Gate {(calibration.detection_threshold * 100).toFixed(0)}%
                  </Badge>
                </div>
                <p className="mt-3 text-xs text-muted-foreground">
                  Drag the red zone to set direct-bleed blanking. 256 bins, 0.0 to 4.0 m.
                </p>
              </CardContent>
            </Card>
          </section>
        </div>

        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>Sonar Imaging console</span>
          <span>
            {connected ? "WebSocket connected" : "Waiting for backend"} ·{" "}
            {telemetry?.mock_mode ? "Synthetic input" : "Hardware input"}
          </span>
        </footer>
      </main>

      <Dialog open={calibrationOpen} onOpenChange={setCalibrationOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Calibration</DialogTitle>
            <DialogDescription>
              Adjust chirp timing, direct-path blanking, echo threshold and depth smoothing.
            </DialogDescription>
          </DialogHeader>
          <CalibrationPanel calibration={calibration} onChange={updateCalibration} />
        </DialogContent>
      </Dialog>
    </div>
  )
}

function StatCard({
  title,
  icon,
  value,
  hint,
}: {
  title: string
  icon: React.ReactNode
  value: string
  hint: string
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold tabular-nums">{value}</div>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </CardContent>
    </Card>
  )
}
