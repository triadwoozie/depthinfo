"use client"

import { memo, useEffect, useRef, useState } from "react"
import { Box, Camera, CloudFog, Scan, Signal } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Slider } from "@/components/ui/slider"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { FogFalloff, OperatorConfig } from "@/types/sonar"

const MJPEG_URL = "http://localhost:8000/video_feed"

interface VisionPreviewProps {
  config: OperatorConfig
  onConfigChange: (patch: Partial<OperatorConfig>) => void
}

function VisionPreviewView({ config, onConfigChange }: VisionPreviewProps) {
  const [streamError, setStreamError] = useState(false)
  const [streamLoaded, setStreamLoaded] = useState(false)
  const range = [config.scan_min_m, config.scan_max_m]

  return (
    <Card className="instrument-card overflow-hidden">
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <div className="flex items-center gap-2.5">
          <div className="icon-tile text-cyan-300">
            <Scan className="h-4 w-4" />
          </div>
          <div>
            <CardTitle className="instrument-title">Spatial monitor</CardTitle>
            <p className="instrument-kicker">Optical · acoustic · metric depth</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="instrument-badge">
            <Signal className="mr-1.5 h-3 w-3 text-emerald-300" />
            SPATIAL VIEW
          </Badge>
          <Badge variant="outline" className="instrument-badge hidden sm:flex">
            Z-DEPTH / 4 M
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <Tabs defaultValue="fog" className="w-full">
          <TabsList className="h-10 w-full justify-start gap-1 rounded-xl border border-zinc-800/80 bg-zinc-950/70 p-1">
            <TabsTrigger value="fog" className="h-8 flex-1 rounded-lg text-[10px] uppercase tracking-wider sm:flex-none">
              <CloudFog className="mr-1.5 h-3.5 w-3.5" />
              Fog map
            </TabsTrigger>
            <TabsTrigger value="optical" className="h-8 flex-1 rounded-lg text-[10px] uppercase tracking-wider sm:flex-none">
              <Camera className="mr-1.5 h-3.5 w-3.5" />
              Optical
            </TabsTrigger>
            <TabsTrigger value="cloud" className="h-8 flex-1 rounded-lg text-[10px] uppercase tracking-wider sm:flex-none">
              <Box className="mr-1.5 h-3.5 w-3.5" />
              3D cloud
            </TabsTrigger>
          </TabsList>

          <div className="relative mt-3 h-[280px] overflow-hidden rounded-xl border border-zinc-800/80 bg-zinc-950">
            <TabsContent value="fog" className="m-0 h-full">
              <FogView config={config} />
            </TabsContent>
            <TabsContent value="optical" className="m-0 h-full">
              <div className="relative flex h-full items-center justify-center">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={streamError ? undefined : MJPEG_URL}
                  alt="Live annotated optical camera stream"
                  className={`h-full w-full object-contain ${streamLoaded ? "opacity-100" : "opacity-0"}`}
                  onLoad={() => {
                    setStreamLoaded(true)
                    setStreamError(false)
                  }}
                  onError={() => {
                    setStreamError(true)
                    setStreamLoaded(false)
                  }}
                />
                {!streamLoaded && (
                  <StreamPlaceholder
                    title={streamError ? "Optical stream unavailable" : "Waiting for optical stream"}
                    detail={
                      streamError
                        ? "Check camera access and the backend video endpoint."
                        : "Connecting to the annotated camera feed."
                    }
                  />
                )}
                {streamLoaded && (
                  <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(transparent_50%,rgba(0,0,0,0.12)_50%)] bg-[length:100%_4px]" />
                )}
              </div>
            </TabsContent>
            <TabsContent value="cloud" className="m-0 h-full">
              <CloudView />
            </TabsContent>
          </div>
        </Tabs>

        <div className="space-y-3 rounded-xl border border-zinc-800/80 bg-zinc-950/55 px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="instrument-label">Active scan frustum</p>
              <p className="mt-1 font-mono text-xs tabular-nums text-zinc-200">
                {config.scan_min_m.toFixed(2)} m <span className="text-zinc-600">—</span>{" "}
                {config.scan_max_m.toFixed(2)} m
              </p>
            </div>
            <Badge variant="outline" className="instrument-badge text-cyan-300">
              {Math.round((config.scan_max_m - config.scan_min_m) * 100)} cm depth
            </Badge>
          </div>
          <Slider
            aria-label="Active scan distance range"
            value={range}
            min={0.2}
            max={4}
            step={0.05}
            minStepsBetweenThumbs={1}
            onValueChange={([scan_min_m, scan_max_m]) =>
              onConfigChange({ scan_min_m, scan_max_m })
            }
            className="[&_[role=slider]]:h-4 [&_[role=slider]]:w-4 [&>span>span]:bg-cyan-500"
          />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="instrument-label">Fog falloff</span>
            <ToggleGroup
              type="single"
              value={config.fog_falloff_curve}
              onValueChange={(fog_falloff_curve) => {
                if (fog_falloff_curve) {
                  onConfigChange({ fog_falloff_curve: fog_falloff_curve as FogFalloff })
                }
              }}
              className="rounded-lg border border-zinc-800 bg-zinc-950 p-0.5"
            >
              <ToggleGroupItem value="linear" aria-label="Linear falloff" className="h-7 rounded-md px-2 text-[9px] uppercase tracking-wider">
                Linear
              </ToggleGroupItem>
              <ToggleGroupItem value="exponential" aria-label="Exponential falloff" className="h-7 rounded-md px-2 text-[9px] uppercase tracking-wider">
                Exponential
              </ToggleGroupItem>
              <ToggleGroupItem value="squared" aria-label="Squared falloff" className="h-7 rounded-md px-2 text-[9px] uppercase tracking-wider">
                Squared
              </ToggleGroupItem>
            </ToggleGroup>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function FogView({ config }: { config: OperatorConfig }) {
  return (
    <div className="relative flex h-full items-center justify-center overflow-hidden bg-[#080c12]">
      <div className="absolute inset-0 opacity-70 [background-image:linear-gradient(rgba(63,63,70,.16)_1px,transparent_1px),linear-gradient(90deg,rgba(63,63,70,.16)_1px,transparent_1px)] [background-size:32px_32px]" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_54%,rgba(6,182,212,.19),transparent_30%),radial-gradient(ellipse_at_49%_53%,rgba(16,185,129,.12),transparent_55%)]" />
      <div className="relative flex h-48 w-48 items-center justify-center rounded-full border border-cyan-500/15">
        <div className="absolute inset-4 rounded-full border border-cyan-500/20" />
        <div className="absolute inset-10 rounded-full border border-emerald-400/20" />
        <div className="absolute h-px w-full bg-cyan-500/20" />
        <div className="absolute h-full w-px bg-cyan-500/20" />
        <div className="h-20 w-20 animate-pulse rounded-full bg-[radial-gradient(circle,rgba(103,232,249,.75)_0%,rgba(34,211,238,.24)_38%,rgba(6,182,212,0)_72%)] shadow-[0_0_46px_rgba(34,211,238,.18)]" />
        <span className="absolute left-2 top-1/2 -translate-y-1/2 font-mono text-[9px] text-cyan-200/70">
          NEAR
        </span>
        <span className="absolute right-1 top-1/2 -translate-y-1/2 font-mono text-[9px] text-zinc-500">
          FAR
        </span>
        <span className="absolute bottom-4 font-mono text-[9px] uppercase tracking-[.18em] text-cyan-100/50">
          {config.fog_falloff_curve} mist
        </span>
      </div>
      <div className="absolute left-4 top-4 flex items-center gap-2 rounded-md border border-amber-400/15 bg-zinc-950/70 px-2.5 py-1.5 font-mono text-[9px] uppercase tracking-wider text-amber-200/80">
        <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
        Fog stream pending
      </div>
      <div className="absolute bottom-4 right-4 font-mono text-[9px] text-zinc-600">
        {config.scan_min_m.toFixed(2)}m → {config.scan_max_m.toFixed(2)}m
      </div>
    </div>
  )
}

function CloudView() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rotationRef = useRef({ x: 0.12, y: -0.35, dragging: false, lastX: 0, lastY: 0 })
  const [webglError, setWebglError] = useState<string | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const gl = canvas.getContext("webgl2", { alpha: true, antialias: true })
    if (!gl) {
      setWebglError("WebGL2 is unavailable in this browser.")
      return
    }

    const vertexShader = compileShader(
      gl,
      gl.VERTEX_SHADER,
      `#version 300 es
      in vec3 a_position;
      in vec3 a_color;
      uniform vec2 u_rotation;
      out vec3 v_color;
      void main() {
        float cy = cos(u_rotation.y);
        float sy = sin(u_rotation.y);
        vec3 p = vec3(a_position.x * cy + a_position.z * sy, a_position.y, -a_position.x * sy + a_position.z * cy);
        float cx = cos(u_rotation.x);
        float sx = sin(u_rotation.x);
        p = vec3(p.x, p.y * cx - p.z * sx, p.y * sx + p.z * cx);
        float perspective = 2.6 / (3.8 + p.z);
        gl_Position = vec4(p.x * perspective, p.y * perspective, p.z * 0.12, 1.0);
        gl_PointSize = 2.0 + clamp((p.z + 1.0) * 0.8, 0.0, 1.5);
        v_color = a_color;
      }`
    )
    const fragmentShader = compileShader(
      gl,
      gl.FRAGMENT_SHADER,
      `#version 300 es
      precision highp float;
      in vec3 v_color;
      out vec4 out_color;
      void main() {
        vec2 p = gl_PointCoord - vec2(0.5);
        if (dot(p, p) > 0.25) discard;
        out_color = vec4(v_color, 0.78);
      }`
    )
    if (!vertexShader || !fragmentShader) {
      setWebglError("WebGL shader initialization failed.")
      return
    }

    const program = gl.createProgram()
    if (!program) {
      setWebglError("WebGL program initialization failed.")
      return
    }
    gl.attachShader(program, vertexShader)
    gl.attachShader(program, fragmentShader)
    gl.linkProgram(program)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      setWebglError(gl.getProgramInfoLog(program) || "WebGL program link failed.")
      gl.deleteProgram(program)
      gl.deleteShader(vertexShader)
      gl.deleteShader(fragmentShader)
      return
    }

    const { positions, colors } = createCloudGeometry()
    const positionBuffer = gl.createBuffer()
    const colorBuffer = gl.createBuffer()
    if (!positionBuffer || !colorBuffer) {
      setWebglError("WebGL buffer allocation failed.")
      return
    }
    gl.useProgram(program)
    gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer)
    gl.bufferData(gl.ARRAY_BUFFER, positions, gl.STATIC_DRAW)
    const positionLocation = gl.getAttribLocation(program, "a_position")
    gl.enableVertexAttribArray(positionLocation)
    gl.vertexAttribPointer(positionLocation, 3, gl.FLOAT, false, 0, 0)
    gl.bindBuffer(gl.ARRAY_BUFFER, colorBuffer)
    gl.bufferData(gl.ARRAY_BUFFER, colors, gl.STATIC_DRAW)
    const colorLocation = gl.getAttribLocation(program, "a_color")
    gl.enableVertexAttribArray(colorLocation)
    gl.vertexAttribPointer(colorLocation, 3, gl.FLOAT, false, 0, 0)

    const rotationLocation = gl.getUniformLocation(program, "u_rotation")
    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.max(1, Math.floor(canvas.clientWidth * ratio))
      canvas.height = Math.max(1, Math.floor(canvas.clientHeight * ratio))
      gl.viewport(0, 0, canvas.width, canvas.height)
    }
    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    resize()
    gl.clearColor(0, 0, 0, 0)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA)

    let frameId = 0
    const draw = () => {
      const rotation = rotationRef.current
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT)
      gl.uniform2f(rotationLocation, rotation.x, rotation.y)
      gl.drawArrays(gl.POINTS, 0, positions.length / 3)
      frameId = requestAnimationFrame(draw)
    }
    frameId = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(frameId)
      observer.disconnect()
      gl.deleteBuffer(positionBuffer)
      gl.deleteBuffer(colorBuffer)
      gl.deleteProgram(program)
      gl.deleteShader(vertexShader)
      gl.deleteShader(fragmentShader)
    }
  }, [])

  return (
    <div
      className="relative flex h-full items-center justify-center overflow-hidden bg-[#080c12]"
      onPointerDown={(event) => {
        rotationRef.current.dragging = true
        rotationRef.current.lastX = event.clientX
        rotationRef.current.lastY = event.clientY
        event.currentTarget.setPointerCapture(event.pointerId)
      }}
      onPointerMove={(event) => {
        if (!rotationRef.current.dragging) return
        const deltaX = event.clientX - rotationRef.current.lastX
        const deltaY = event.clientY - rotationRef.current.lastY
        rotationRef.current.y += deltaX * 0.008
        rotationRef.current.x = Math.max(
          -1.1,
          Math.min(1.1, rotationRef.current.x + deltaY * 0.008)
        )
        rotationRef.current.lastX = event.clientX
        rotationRef.current.lastY = event.clientY
      }}
      onPointerUp={() => {
        rotationRef.current.dragging = false
      }}
      onPointerCancel={() => {
        rotationRef.current.dragging = false
      }}
      style={{ touchAction: "none", cursor: "grab" }}
    >
      <div className="absolute inset-0 opacity-60 [background-image:linear-gradient(rgba(63,63,70,.15)_1px,transparent_1px),linear-gradient(90deg,rgba(63,63,70,.15)_1px,transparent_1px)] [background-size:32px_32px]" />
      <canvas ref={canvasRef} aria-label="Interactive 3D synthetic point cloud" className="relative h-full w-full" />
      <div className="absolute left-4 top-4 rounded-md border border-amber-400/15 bg-zinc-950/70 px-2.5 py-1.5 font-mono text-[9px] uppercase tracking-wider text-amber-200/80">
        Point cloud stream pending
      </div>
      <div className="absolute bottom-4 right-4 font-mono text-[9px] text-zinc-600">
        {webglError ?? "DRAG TO ORBIT · WEBGL2"}
      </div>
    </div>
  )
}

function compileShader(
  gl: WebGL2RenderingContext,
  type: number,
  source: string
): WebGLShader | null {
  const shader = gl.createShader(type)
  if (!shader) return null
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader)
    return null
  }
  return shader
}

function createCloudGeometry() {
  const positions: number[] = []
  const colors: number[] = []
  for (let index = 0; index < 520; index++) {
    const band = index < 90 ? "head" : "body"
    const localIndex = band === "head" ? index : index - 90
    const count = band === "head" ? 90 : 430
    const y = 1 - (localIndex / (count - 1)) * 2
    const ring = Math.sqrt(Math.max(0, 1 - y * y))
    const angle = localIndex * 2.399963
    const jitter = ((localIndex * 37) % 17) / 85
    const scale = band === "head" ? 0.27 : 1
    const centerY = band === "head" ? 0.78 : -0.05
    positions.push(
      Math.cos(angle) * ring * 0.5 * scale,
      centerY + y * (band === "head" ? 0.3 : 0.92),
      Math.sin(angle) * ring * (band === "head" ? 0.27 : 0.38) + jitter * 0.08
    )
    const depthMix = (Math.sin(angle) + 1) / 2
    colors.push(
      0.12 + depthMix * 0.08,
      0.62 + depthMix * 0.24,
      0.62 + depthMix * 0.34
    )
  }
  return {
    positions: new Float32Array(positions),
    colors: new Float32Array(colors),
  }
}

function StreamPlaceholder({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
      <div className="relative grid h-14 w-14 place-items-center rounded-2xl border border-zinc-700/70 bg-zinc-900/80 text-zinc-500">
        <Camera className="h-5 w-5" />
        <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border-2 border-zinc-950 bg-amber-400" />
      </div>
      <div>
        <p className="text-xs font-medium text-zinc-300">{title}</p>
        <p className="mt-1 max-w-xs text-[10px] leading-relaxed text-zinc-500">{detail}</p>
      </div>
    </div>
  )
}

export const VisionPreview = memo(VisionPreviewView)
