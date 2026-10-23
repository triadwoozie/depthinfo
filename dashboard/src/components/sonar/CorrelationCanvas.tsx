/**
 * CorrelationCanvas.tsx — Real-time 60 FPS HTML5 Canvas renderer.
 *
 * Renders the acoustic cross-correlation curve from the `curveRef` populated
 * by useSonarSocket — intentionally bypassing React state to prevent re-render
 * lag at 15–30 Hz data rates.
 *
 * Zones (from AGENTS.md):
 *   Zone A (red overlay)  : 0 → blanking_distance_cm (direct acoustic bleed)
 *   Zone B (curve)        : blanking_distance_cm → 400 cm (echo search region)
 *   Zone C (noise floor)  : peaks below detection_threshold
 *
 * All drawing happens inside a requestAnimationFrame loop started on mount
 * and cancelled on unmount. React only owns the container div's size.
 */

"use client"

import { useEffect, useRef } from "react"

import { SONAR_COLORS } from "@/types/sonar"

interface CorrelationCanvasProps {
  /** Ref to the live 256-float correlation curve from useSonarSocket. */
  curveRef: React.MutableRefObject<number[]>
  /** Blanking distance in cm — controls the Zone A red overlay width. */
  blankingDistanceCm: number
  /** Detection threshold [0–1] — draws the horizontal threshold line. */
  detectionThreshold: number
  /** Optional: current sonar depth in metres for the depth-marker line. */
  sonarDepthM?: number
  /** Update the direct-path blanking range while dragging its handle. */
  onBlankingChange?: (distanceCm: number) => void
  className?: string
}

// Total range represented by the 256 bins (metres)
const CURVE_RANGE_M = 4.0
const BINS = 256

export function CorrelationCanvas({
  curveRef,
  blankingDistanceCm,
  detectionThreshold,
  sonarDepthM,
  onBlankingChange,
  className,
}: CorrelationCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef = useRef<number>(0)
  const draggingBlankingRef = useRef(false)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext("2d")
    if (!ctx) return

    // -----------------------------------------------------------------------
    // Draw one frame
    // -----------------------------------------------------------------------
    const draw = () => {
      const W = canvas.width
      const H = canvas.height
      const curve = curveRef.current
      const binW = W / BINS

      // 1. Clear
      ctx.fillStyle = "rgba(9,9,11,0.72)"
      ctx.fillRect(0, 0, W, H)

      // 2. Subtle instrument grid
      ctx.strokeStyle = "rgba(82,82,91,0.28)"
      ctx.lineWidth = 1
      ;[0.25, 0.5, 0.75].forEach((frac) => {
        const y = H * frac
        ctx.beginPath()
        ctx.moveTo(0, y)
        ctx.lineTo(W, y)
        ctx.stroke()
      })
      for (let i = 1; i < 8; i++) {
        const x = (W / 8) * i
        ctx.beginPath()
        ctx.moveTo(x, 0)
        ctx.lineTo(x, H)
        ctx.stroke()
      }

      // 3. Zone A — blanking overlay (direct acoustic bleed)
      const blankingFraction = blankingDistanceCm / 100 / CURVE_RANGE_M
      const blankingX = Math.min(blankingFraction * W, W)
      ctx.fillStyle = SONAR_COLORS.blankingZone
      ctx.fillRect(0, 0, blankingX, H)
      ctx.strokeStyle = "rgba(248,113,113,0.9)"
      ctx.lineWidth = 1
      ctx.setLineDash([3, 4])
      ctx.beginPath()
      ctx.moveTo(blankingX, 0)
      ctx.lineTo(blankingX, H)
      ctx.stroke()
      ctx.setLineDash([])
      ctx.fillStyle = "#fca5a5"
      ctx.beginPath()
      ctx.arc(blankingX, 18, 4, 0, Math.PI * 2)
      ctx.fill()

      // Zone A label
      ctx.fillStyle = "rgba(239,68,68,0.6)"
      ctx.font = "10px monospace"
      ctx.fillText("BLANK", 4, H - 6)

      // 4. Detection threshold line
      const thresholdY = H * (1 - detectionThreshold)
      ctx.strokeStyle = SONAR_COLORS.threshold
      ctx.lineWidth = 1.5
      ctx.setLineDash([4, 4])
      ctx.beginPath()
      ctx.moveTo(0, thresholdY)
      ctx.lineTo(W, thresholdY)
      ctx.stroke()
      ctx.setLineDash([])

      // Threshold label
      ctx.fillStyle = SONAR_COLORS.threshold
      ctx.font = "9px monospace"
      ctx.fillText(`THR ${(detectionThreshold * 100).toFixed(0)}%`, W - 56, thresholdY - 4)

      // 5. Correlation curve — filled area + stroke
      if (curve.length >= 2) {
        // Filled gradient underneath the curve
        const grad = ctx.createLinearGradient(0, 0, 0, H)
        grad.addColorStop(0, "rgba(34,211,238,0.24)")
        grad.addColorStop(1, "rgba(16,185,129,0.015)")

        ctx.beginPath()
        ctx.moveTo(0, H)
        for (let i = 0; i < BINS; i++) {
          const x = i * binW
          const val = curve[i] ?? 0
          const y = H * (1 - val)
          if (i === 0) ctx.lineTo(x, y)
          else ctx.lineTo(x, y)
        }
        ctx.lineTo(W, H)
        ctx.closePath()
        ctx.fillStyle = grad
        ctx.fill()

        // Curve stroke
        ctx.strokeStyle = "#67e8f9"
        ctx.lineWidth = 1.8
        ctx.shadowColor = "rgba(34,211,238,0.42)"
        ctx.shadowBlur = 10
        ctx.beginPath()
        for (let i = 0; i < BINS; i++) {
          const x = i * binW + binW / 2
          const val = curve[i] ?? 0
          const y = H * (1 - val)
          if (i === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }
        ctx.stroke()
        ctx.shadowBlur = 0

        ctx.strokeStyle = "rgba(52,211,153,0.65)"
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(0, H - 17)
        ctx.lineTo(W, H - 17)
        ctx.stroke()

        // Peak marker: find highest point above threshold in Zone B
        let peakBin = -1
        let peakVal = -Infinity
        const blankingBin = Math.floor(blankingFraction * BINS)
        for (let i = blankingBin; i < BINS; i++) {
          const v = curve[i] ?? 0
          if (v > peakVal) {
            peakVal = v
            peakBin = i
          }
        }
        if (peakBin >= 0 && peakVal >= detectionThreshold) {
          const px = peakBin * binW + binW / 2
          const py = H * (1 - peakVal)

          // Vertical marker line
          ctx.strokeStyle = SONAR_COLORS.peakMarker
          ctx.lineWidth = 1.5
          ctx.setLineDash([3, 3])
          ctx.beginPath()
          ctx.moveTo(px, py)
          ctx.lineTo(px, H)
          ctx.stroke()
          ctx.setLineDash([])

          // Peak distance label
          const distM = (peakBin / BINS) * CURVE_RANGE_M
          ctx.fillStyle = SONAR_COLORS.peakMarker
          ctx.font = "bold 10px monospace"
          ctx.fillText(`${distM.toFixed(2)}m`, px + 4, Math.max(14, py - 4))

          // Peak dot
          ctx.beginPath()
          ctx.arc(px, py, 4, 0, Math.PI * 2)
          ctx.fillStyle = SONAR_COLORS.peakMarker
          ctx.fill()
        }
      }

      // 6. Current sonar depth marker (EMA smoothed value from state)
      if (sonarDepthM !== undefined && sonarDepthM > 0) {
        const depthFrac = sonarDepthM / CURVE_RANGE_M
        const depthX = Math.min(depthFrac * W, W - 1)

        ctx.strokeStyle = SONAR_COLORS.cyan
        ctx.lineWidth = 2
        ctx.beginPath()
        ctx.moveTo(depthX, 0)
        ctx.lineTo(depthX, H)
        ctx.stroke()

        ctx.fillStyle = SONAR_COLORS.cyan
        ctx.font = "bold 11px monospace"
        const label = `EMA ${sonarDepthM.toFixed(2)}m`
        const labelX = depthX + 4 > W - 70 ? depthX - 72 : depthX + 4
        ctx.fillText(label, labelX, 16)
      }

      // 7. X-axis distance labels (0, 1, 2, 3, 4 m)
      ctx.fillStyle = SONAR_COLORS.muted
      ctx.font = "9px monospace"
      for (let m = 0; m <= 4; m++) {
        const x = (m / CURVE_RANGE_M) * W
        ctx.fillText(`${m}m`, Math.max(0, Math.min(W - 18, x - 6)), H - 2)
      }

      rafRef.current = requestAnimationFrame(draw)
    }

    // -----------------------------------------------------------------------
    // Resize observer — keeps canvas pixel size in sync with CSS size
    // -----------------------------------------------------------------------
    const observer = new ResizeObserver(() => {
      if (!canvas) return
      canvas.width = canvas.clientWidth
      canvas.height = canvas.clientHeight
    })
    observer.observe(canvas)
    canvas.width = canvas.clientWidth
    canvas.height = canvas.clientHeight

    rafRef.current = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(rafRef.current)
      observer.disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blankingDistanceCm, detectionThreshold, sonarDepthM])
  // Note: curveRef is intentionally excluded — it's a mutable ref, not a reactive value.

  return (
    <canvas
      ref={canvasRef}
      className={className}
      onPointerDown={(event) => {
        const canvas = canvasRef.current
        if (!canvas || !onBlankingChange) return
        const rect = canvas.getBoundingClientRect()
        const pointerX = event.clientX - rect.left
        const handleX =
          (blankingDistanceCm / 100 / CURVE_RANGE_M) * rect.width
        if (Math.abs(pointerX - handleX) <= 14) {
          draggingBlankingRef.current = true
          canvas.setPointerCapture(event.pointerId)
        }
      }}
      onPointerMove={(event) => {
        const canvas = canvasRef.current
        if (!canvas || !draggingBlankingRef.current || !onBlankingChange) return
        const rect = canvas.getBoundingClientRect()
        const distanceCm = ((event.clientX - rect.left) / rect.width) * CURVE_RANGE_M * 100
        onBlankingChange(Math.max(0, Math.min(200, distanceCm)))
      }}
      onPointerUp={() => {
        draggingBlankingRef.current = false
      }}
      onPointerCancel={() => {
        draggingBlankingRef.current = false
      }}
      style={{
        width: "100%",
        height: "100%",
        display: "block",
        imageRendering: "pixelated",
        touchAction: "none",
        cursor: onBlankingChange ? "ew-resize" : "default",
      }}
    />
  )
}
