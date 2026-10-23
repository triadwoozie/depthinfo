"use client"

import { memo, useState } from "react"
import { Download, LoaderCircle } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
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
import { apiUrl } from "@/lib/api"
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
      // The backend exports with its live config; colour/radius edits are already synced.
      const response = await fetch(apiUrl("/api/export/blend"), { method: "POST" })
      if (!response.ok) throw new Error(await readError(response))
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
      <Card>
        <CardHeader className="flex-row items-start justify-between space-y-0">
          <div className="space-y-1.5">
            <CardTitle>Blender finish</CardTitle>
            <CardDescription>Point cloud appearance and scene export</CardDescription>
          </div>
          <Badge variant="outline" className="font-normal">
            Export
          </Badge>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-2">
            <Label className="text-sm">Vertex color mode</Label>
            <Select
              value={config.color_mode}
              onValueChange={(color_mode) =>
                onConfigChange({ color_mode: color_mode as ColorMode })
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {colorModes.map((mode) => (
                  <SelectItem key={mode.value} value={mode.value}>
                    {mode.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">{selectedMode?.description}</p>
          </div>

          <div className="grid gap-2">
            <Label className="text-sm">Near / far palette</Label>
            <div className="grid grid-cols-2 gap-2">
              <ColorSwatch
                label="Near"
                value={config.custom_near_hex}
                onChange={(custom_near_hex) => onConfigChange({ custom_near_hex })}
              />
              <ColorSwatch
                label="Far"
                value={config.custom_far_hex}
                onChange={(custom_far_hex) => onConfigChange({ custom_far_hex })}
              />
            </div>
          </div>

          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm">Point radius</Label>
              <span className="text-sm tabular-nums text-muted-foreground">
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

          <Separator />
          <Button onClick={exportScene} disabled={exporting} className="w-full">
            {exporting ? (
              <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Download className="mr-2 h-4 w-4" />
            )}
            Export static .blend scene
          </Button>
          <p className="text-center text-xs text-muted-foreground">
            Writes .blend, .glb and .ply from the latest scan
          </p>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              Static scene export
            </DialogTitle>
            <DialogDescription>
              Blender project and portable geometry files.
            </DialogDescription>
          </DialogHeader>
          {exporting && (
            <div className="flex items-center gap-3 rounded-md border p-4">
              <LoaderCircle className="h-4 w-4 animate-spin" />
              <span className="text-sm">Preparing scene package…</span>
            </div>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertTitle>Export failed</AlertTitle>
              <AlertDescription className="break-all">{error}</AlertDescription>
            </Alert>
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

/** Pull the backend's `detail` message out of an error response (JSON or plain text). */
async function readError(response: Response): Promise<string> {
  const text = await response.text()
  try {
    const body = JSON.parse(text) as { detail?: unknown }
    if (typeof body.detail === "string") return body.detail
  } catch {
    // not JSON — fall through to the raw text
  }
  return text || `Export request failed (${response.status})`
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
    <label className="flex cursor-pointer items-center gap-2 rounded-md border p-2">
      <input
        type="color"
        aria-label={`${label.toLowerCase()} palette color`}
        value={value}
        onChange={(event) => onChange(event.target.value.toUpperCase())}
        className="h-7 w-7 shrink-0 cursor-pointer rounded border-0 bg-transparent p-0"
      />
      <span className="min-w-0 flex-1 leading-tight">
        <span className="block text-xs text-muted-foreground">{label}</span>
        <span className="block truncate text-sm tabular-nums">{value}</span>
      </span>
    </label>
  )
}

function DownloadLink({ label, href }: { label: string; href?: string }) {
  if (!href) {
    return (
      <div className="flex items-center justify-between rounded-md border px-3 py-2 text-sm text-muted-foreground">
        <span>{label}</span>
        <span>Not provided</span>
      </div>
    )
  }

  return (
    <Button variant="outline" asChild className="justify-between">
      <a href={apiUrl(href)} download>
        <span>{label}</span>
        <Download className="h-4 w-4" />
      </a>
    </Button>
  )
}

export const ExportDrawer = memo(ExportDrawerView)
