/**
 * VisionPreview.tsx — MJPEG / WebSocket frame viewer.
 *
 * Displays the annotated camera feed from the FastAPI /video_feed MJPEG endpoint.
 * Using a native <img> tag with a multipart MJPEG src keeps video frames
 * completely out of the WebSocket JSON pipe.
 */

"use client"

import { useState } from "react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

const MJPEG_URL = "http://localhost:8000/video_feed"

interface VisionPreviewProps {
  fps?: number
  className?: string
}

export function VisionPreview({ fps, className }: VisionPreviewProps) {
  const [error, setError] = useState(false)
  const [loaded, setLoaded] = useState(false)

  return (
    <Card className={`bg-zinc-900 border-zinc-800 ${className ?? ""}`}>
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <CardTitle className="text-sm font-mono text-zinc-300 tracking-widest uppercase">
          Vision Feed
        </CardTitle>
        <div className="flex items-center gap-2">
          {fps !== undefined && (
            <Badge variant="outline" className="text-xs font-mono border-zinc-700 text-zinc-400">
              {fps.toFixed(1)} FPS
            </Badge>
          )}
          <Badge
            variant={error ? "destructive" : loaded ? "default" : "outline"}
            className="text-xs font-mono"
          >
            {error ? "NO SIGNAL" : loaded ? "LIVE" : "CONNECTING"}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="p-0 overflow-hidden rounded-b-lg">
        <div className="relative bg-zinc-950 w-full aspect-video flex items-center justify-center">
          {/* MJPEG stream — native browser MJPEG support via img src */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={error ? undefined : MJPEG_URL}
            alt="Vision feed"
            className="w-full h-full object-contain"
            onLoad={() => {
              setLoaded(true)
              setError(false)
            }}
            onError={() => {
              setError(true)
              setLoaded(false)
            }}
          />

          {/* Overlay shown when feed unavailable */}
          {(error || !loaded) && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
              {/* Animated sonar ring */}
              <div className="relative w-16 h-16">
                <div className="absolute inset-0 rounded-full border-2 border-emerald-500/30 animate-ping" />
                <div className="absolute inset-2 rounded-full border-2 border-emerald-500/50 animate-ping [animation-delay:0.2s]" />
                <div className="absolute inset-4 rounded-full border-2 border-emerald-500 animate-pulse" />
              </div>
              <p className="text-zinc-500 text-xs font-mono mt-2">
                {error
                  ? "Backend unreachable — start the FastAPI server"
                  : "Waiting for video feed…"}
              </p>
            </div>
          )}

          {/* Scan-line overlay for CRT aesthetic */}
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              backgroundImage:
                "repeating-linear-gradient(0deg, rgba(0,0,0,0.08) 0px, rgba(0,0,0,0.08) 1px, transparent 1px, transparent 2px)",
            }}
          />
        </div>
      </CardContent>
    </Card>
  )
}
