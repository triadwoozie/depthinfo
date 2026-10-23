"use client"

import { memo, useState } from "react"
import {
  Box,
  Download,
  LoaderCircle,
  Palette,
  Sparkles,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { ColorMode, OperatorConfig } from "@/types/sonar"

interface ExportDrawerProps {
  config: OperatorConfig
  onConfigChange: (patch: Partial<OperatorConfig>) => void
}

type ExportFiles = {
  blend_url?: string
  glb_url?: string
  ply_url?: string
}

const colorModes: {
  value: ColorMode
  title: string
  description: string
}[] = [
  { value: "optical_rgb", title: "Optical RGB", description: "Camera color" },
  { value: "fog_gradient", title: "Fog mist gradient", description: "Depth falloff" },
  { value: "acoustic_heatmap", title: "Acoustic heatmap", description: "Echo strength" },
  { value: "custom_palette", title: "Custom dual hex", description: "Near / far palette" },
]

function ExportDrawerView({ config, onConfigChange }: ExportDrawerProps) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [files, setFiles] = useState<ExportFiles | null>(null)
  const [error, setError] = useState<string | null>(null)
  const selectedMode = colorModes.find((mode) => mode.value === config.color_mode)

  const exportScene = async () => {
    setDialogOpen(true)
    setExporting(true)
    setFiles(null)
    setError(null)
    try {
      const response = await fetch("http://localhost:8000/api/export/blend", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          color_mode: config.color_mode,
          point_radius_mm: config.point_radius_mm,
          custom_near_hex: config.custom_near_hex,
          custom_far_hex: config.custom_far_hex,
        }),
      })
      if (!response.ok) {
        const message = await response.text()
        throw new Error(message || `Export request failed (${response.status})`)
      }
      setFiles((await response.json()) as ExportFiles)
    } catch (exportError: unknown) {
      setError(
        exportError instanceof Error
          ? exportError.message
          : "The export request could not be completed."
      )
    } finally {
      setExporting(false)
    }
  }

  return (
    <>
      <Card className="instrument-card">
        <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
          <div className="flex items-center gap-2.5">
            <div className="icon-tile text-violet-300">
              <Palette className="h-4 w-4" />
            </div>
            <div>
              <CardTitle className="instrument-title">Blender finish</CardTitle>
              <p className="instrument-kicker">Point cloud appearance</p>
            </div>
          </div>
          <Badge variant="outline" className="instrument-badge">
            <Box className="mr-1.5 h-3 w-3" />
            EXPORT
          </Badge>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <p className="instrument-label">Vertex color mode</p>
            <Select
              value={config.color_mode}
              onValueChange={(color_mode) =>
                onConfigChange({ color_mode: color_mode as ColorMode })
              }
            >
              <SelectTrigger className="h-10 w-full rounded-lg border-zinc-700/80 bg-zinc-950/70 text-xs text-zinc-200">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {colorModes.map((mode) => (
                  <SelectItem key={mode.value} value={mode.value}>
                    <span className="flex flex-col py-0.5">
                      <span>{mode.title}</span>
                      <span className="text-[10px] text-zinc-500">{mode.description}</span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center justify-between">
            <span className="instrument-label">Near / far palette</span>
            <span className="text-[9px] text-zinc-600">HEX</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <ColorSwatch
              label="NEAR"
              value={config.custom_near_hex}
              onChange={(custom_near_hex) => onConfigChange({ custom_near_hex })}
            />
            <ColorSwatch
              label="FAR"
              value={config.custom_far_hex}
              onChange={(custom_far_hex) => onConfigChange({ custom_far_hex })}
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="instrument-label">Point radius</span>
              <span className="font-mono text-[10px] tabular-nums text-zinc-300">
                {config.point_radius_mm.toFixed(1)} mm
              </span>
            </div>
            <Slider
              aria-label="Point radius"
              value={[config.point_radius_mm]}
              min={0.5}
              max={20}
              step={0.5}
              onValueChange={([point_radius_mm]) => onConfigChange({ point_radius_mm })}
            />
          </div>

          <div className="h-px bg-zinc-800/80" />
          <div className="flex items-center justify-between text-[10px] text-zinc-500">
            <span>Scene package</span>
            <span className="font-mono text-zinc-400">.blend · .glb · .ply</span>
          </div>
          <Button
            size="lg"
            onClick={exportScene}
            disabled={exporting}
            className="h-11 w-full rounded-xl bg-emerald-600 text-xs font-semibold tracking-wide text-white shadow-[0_0_22px_rgba(16,185,129,0.14)] hover:bg-emerald-500"
          >
            {exporting ? (
              <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="mr-2 h-4 w-4" />
            )}
            Export static .blend scene
          </Button>
          <p className="text-center text-[9px] leading-relaxed text-zinc-600">
            {selectedMode?.description ?? "Color and geometry settings"} · uses latest scan
          </p>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md border-zinc-800 bg-zinc-950 text-zinc-100">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-sm">
              <Download className="h-4 w-4 text-emerald-300" />
              Static scene export
            </DialogTitle>
            <DialogDescription className="text-xs text-zinc-500">
              Blender project and portable geometry files.
            </DialogDescription>
          </DialogHeader>
          {exporting && (
            <div className="flex items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
              <LoaderCircle className="h-4 w-4 animate-spin text-emerald-300" />
              <span className="text-xs text-zinc-300">Preparing scene package…</span>
            </div>
          )}
          {error && (
            <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.06] p-4">
              <p className="text-xs font-medium text-amber-200">Export unavailable</p>
              <p className="mt-1 break-all text-[10px] leading-relaxed text-amber-100/60">
                {error}
              </p>
            </div>
          )}
          {files && (
            <div className="grid gap-2">
              <DownloadLink label="Blender scene" href={files.blend_url} />
              <DownloadLink label="glTF binary" href={files.glb_url} />
              <DownloadLink label="PLY point cloud" href={files.ply_url} />
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

function ColorSwatch({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950/60 p-2">
      <span
        className="h-7 w-7 shrink-0 rounded-md border border-white/10"
        style={{ backgroundColor: value }}
      />
      <span className="min-w-0 flex-1">
        <span className="block text-[9px] font-semibold tracking-wider text-zinc-500">{label}</span>
        <span className="block truncate font-mono text-[10px] text-zinc-300">{value}</span>
      </span>
      <input
        type="color"
        aria-label={`${label.toLowerCase()} palette color`}
        value={value}
        onChange={(event) => onChange(event.target.value.toUpperCase())}
        className="h-6 w-6 cursor-pointer rounded border-0 bg-transparent p-0"
      />
    </label>
  )
}

function DownloadLink({ label, href }: { label: string; href?: string }) {
  if (!href) {
    return (
      <div className="flex items-center justify-between rounded-lg border border-zinc-800 px-3 py-2 text-xs text-zinc-600">
        <span>{label}</span>
        <span>Not provided</span>
      </div>
    )
  }

  const target = href.startsWith("http") ? href : `http://localhost:8000${href}`
  return (
    <a
      href={target}
      download
      className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2 text-xs text-zinc-300 transition hover:border-emerald-500/40 hover:text-emerald-200"
    >
      <span>{label}</span>
      <Download className="h-3.5 w-3.5" />
    </a>
  )
}

export const ExportDrawer = memo(ExportDrawerView)
