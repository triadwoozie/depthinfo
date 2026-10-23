"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Check, LoaderCircle, Mic, RotateCcw } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { apiUrl } from "@/lib/api"

/**
 * Microphone / sonar calibration wizard. Every step calls the real backend:
 *   0  GET  /api/calibration/input-level      live microphone level meter
 *   1  POST /api/calibration/round/1          10 chirps in an empty room → blanking distance
 *   2  POST /api/calibration/round/2          5 s of silence → noise floor and detection threshold
 *   3  POST /api/calibration/round/3          30 empty-scene camera frames → background mask
 *   4  POST /api/calibration/round/4          flat target at 0.5 / 1.5 / 2.5 m → metric scale/offset
 * Verdicts come from the `quality` field the backend returns; nothing here is simulated.
 */

type Verdict = "pass" | "warn" | "fail"
type StepId = 0 | 1 | 2 | 3 | 4

interface InputLevel {
  rms: number
  peak: number
  dbfs: number
  quality: "ok" | "silent" | "too_loud" | "clipping"
  mock: boolean
}

interface RoundResult {
  verdict: Verdict
  summary: string[]
}

const TARGETS_M = [0.5, 1.5, 2.5] as const
const STEPS: { id: StepId; label: string }[] = [
  { id: 0, label: "Input" },
  { id: 1, label: "Blanking" },
  { id: 2, label: "Noise" },
  { id: 3, label: "Background" },
  { id: 4, label: "Anchor" },
]

const ROUND_INFO: Record<1 | 2 | 3 | 4, { title: string; seconds: number; instructions: string }> = {
  1: {
    title: "Direct bleed and blanking",
    seconds: 3.5,
    instructions:
      "Clear the space in front of the rig and keep the room quiet. Ten chirps play and the speaker-to-microphone crosstalk sets the blanking distance.",
  },
  2: {
    title: "Noise floor",
    seconds: 5.5,
    instructions:
      "Stay silent. The microphone listens for 5 seconds with no emission to set the noise floor and the detection threshold.",
  },
  3: {
    title: "Empty-room background",
    seconds: 4,
    instructions:
      "Step out of the camera view. Thirty frames of the empty scene are captured so later frames can subtract the background.",
  },
  4: {
    title: "Metric anchor",
    seconds: 3,
    instructions:
      "Hold a flat target at each distance in turn and capture it. The three points are fitted to correct the sonar scale and offset.",
  },
}

async function readError(response: Response): Promise<string> {
  const text = await response.text()
  try {
    const body = JSON.parse(text) as { detail?: unknown }
    if (typeof body.detail === "string") return body.detail
  } catch {
    // not JSON
  }
  return text || `Request failed (${response.status})`
}

function verdictBadge(verdict: Verdict) {
  const label = verdict === "pass" ? "Pass" : verdict === "warn" ? "Check" : "Fail"
  const variant = verdict === "pass" ? "secondary" : verdict === "warn" ? "outline" : "destructive"
  return (
    <Badge variant={variant} className="gap-1 font-normal">
      {verdict === "pass" && <Check className="h-3 w-3" />}
      {label}
    </Badge>
  )
}

function interpret(round: 1 | 2 | 3 | 4, body: Record<string, unknown>): RoundResult {
  const quality = String(body.quality ?? "ok")
  if (round === 1) {
    const verdict: Verdict = quality === "good" ? "pass" : "warn"
    return {
      verdict,
      summary: [
        `Blanking distance ${Number(body.blanking_distance_cm).toFixed(1)} cm`,
        `Speaker to mic baseline ${Number(body.baseline_cm).toFixed(1)} cm`,
        `Spread between chirps ${Number(body.spread_cm).toFixed(1)} cm (${quality})`,
        ...(verdict === "warn"
          ? ["Readings are scattered: the chirps may not be reaching the microphone."]
          : []),
      ],
    }
  }
  if (round === 2) {
    const verdict: Verdict = quality === "ok" ? "pass" : "fail"
    return {
      verdict,
      summary: [
        `Noise floor ${Number(body.ambient_noise_floor_rms).toFixed(4)} RMS`,
        `Detection threshold ${(Number(body.detection_threshold) * 100).toFixed(0)}%`,
        ...(quality === "input_too_loud"
          ? ["The input is far too loud for a quiet room: wrong device or input gain."]
          : quality === "input_silent"
            ? ["The input is silent: muted, or the wrong input channel."]
            : []),
      ],
    }
  }
  if (round === 3) {
    return { verdict: "pass", summary: [`${String(body.frames)} frames captured, background stored`] }
  }
  const rms = Number(body.fit_rms_m)
  return {
    verdict: quality === "ok" ? "pass" : "warn",
    summary: [
      `Scale ${Number(body.metric_scale_factor).toFixed(3)}, offset ${(Number(body.metric_offset_m) * 100).toFixed(1)} cm`,
      `Fit error ${(rms * 100).toFixed(1)} cm RMS`,
    ],
  }
}

export function CalibrationWizard({ active }: { active: boolean }) {
  const [step, setStep] = useState<StepId>(0)
  const [results, setResults] = useState<Partial<Record<1 | 2 | 3 | 4, RoundResult>>>({})
  const [running, setRunning] = useState<1 | 2 | 3 | 4 | null>(null)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [log, setLog] = useState<string[]>([])
  const [level, setLevel] = useState<InputLevel | null>(null)
  const [captured, setCaptured] = useState<number[]>([])
  const logRef = useRef<HTMLPreElement>(null)

  const note = useCallback((line: string) => {
    const stamp = new Date().toLocaleTimeString([], { hour12: false })
    setLog((prev) => [...prev.slice(-40), `${stamp}  ${line}`])
  }, [])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [log])

  // Live microphone level while on the input-check step.
  useEffect(() => {
    if (!active || step !== 0 || running !== null) return
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      try {
        const response = await fetch(apiUrl("/api/calibration/input-level"), { cache: "no-store" })
        if (!response.ok) throw new Error(await readError(response))
        if (alive) {
          setLevel((await response.json()) as InputLevel)
          setError(null)
        }
      } catch (exc) {
        if (alive) setError(exc instanceof Error ? exc.message : "Input level unavailable")
      }
      if (alive) timer = setTimeout(poll, 500)
    }
    void poll()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [active, step, running])

  // Progress bar for the blocking round requests (their duration is fixed by the backend).
  useEffect(() => {
    if (running === null) return
    const seconds = ROUND_INFO[running].seconds
    const started = Date.now()
    setProgress(0)
    const timer = setInterval(() => {
      setProgress(Math.min(95, ((Date.now() - started) / (seconds * 1000)) * 100))
    }, 100)
    return () => clearInterval(timer)
  }, [running])

  const run = async (round: 1 | 2 | 3 | 4, distance?: number) => {
    setRunning(round)
    setError(null)
    note(
      distance !== undefined
        ? `Round 4: capturing the target at ${distance} m…`
        : `Round ${round}: ${ROUND_INFO[round].title.toLowerCase()}…`
    )
    try {
      const response = await fetch(apiUrl(`/api/calibration/round/${round}`), {
        method: "POST",
        headers: distance !== undefined ? { "Content-Type": "application/json" } : undefined,
        body: distance !== undefined ? JSON.stringify({ distance_m: distance }) : undefined,
      })
      if (!response.ok) throw new Error(await readError(response))
      const body = (await response.json()) as Record<string, unknown>
      setProgress(100)
      if (round === 4 && body.status === "pending") {
        setCaptured((body.captured_m as number[]) ?? [])
        note(`Round 4: captured ${(body.captured_m as number[]).join(", ")} m, ${(body.remaining_m as number[]).length} left`)
      } else {
        const result = interpret(round, body)
        setResults((prev) => ({ ...prev, [round]: result }))
        if (round === 4) setCaptured([...TARGETS_M])
        result.summary.forEach((line) => note(`Round ${round}: ${line}`))
      }
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "Calibration request failed"
      setError(message)
      note(`Round ${round} failed: ${message}`)
    } finally {
      setRunning(null)
    }
  }

  const done = (Object.keys(results) as unknown as (1 | 2 | 3 | 4)[]).length
  const dbfsPercent = level ? Math.max(0, Math.min(100, ((level.dbfs + 70) / 70) * 100)) : 0

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Calibration steps">
        {STEPS.map((s) => {
          const result = s.id === 0 ? undefined : results[s.id as 1 | 2 | 3 | 4]
          return (
            <Button
              key={s.id}
              variant={step === s.id ? "default" : "outline"}
              size="sm"
              role="tab"
              aria-selected={step === s.id}
              disabled={running !== null}
              onClick={() => setStep(s.id)}
              className="gap-1.5"
            >
              {result?.verdict === "pass" ? (
                <Check className="h-3.5 w-3.5" />
              ) : (
                <span className="tabular-nums">{s.id}</span>
              )}
              {s.label}
            </Button>
          )
        })}
        <span className="ml-auto text-xs text-muted-foreground">{done} of 4 rounds done</span>
      </div>

      <Separator />

      {step === 0 ? (
        <section className="space-y-3">
          <div>
            <h4 className="flex items-center gap-2 text-sm font-semibold">
              <Mic className="h-4 w-4" />
              Microphone input check
            </h4>
            <p className="mt-1 text-sm text-muted-foreground">
              Stay quiet and watch the meter. A quiet room should sit well below the top; if it is
              pinned, the wrong device or gain is selected. Choose devices under Hardware and rig.
            </p>
          </div>
          <div className="space-y-2">
            <Progress value={dbfsPercent} aria-label="Microphone level" />
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="tabular-nums text-muted-foreground">
                {level
                  ? `${level.dbfs.toFixed(1)} dBFS, RMS ${level.rms.toFixed(4)}, peak ${level.peak.toFixed(3)}`
                  : "Measuring…"}
              </span>
              {level &&
                (level.quality === "ok"
                  ? verdictBadge("pass")
                  : verdictBadge(level.quality === "silent" ? "fail" : "warn"))}
            </div>
            {level?.quality && level.quality !== "ok" && (
              <p className="text-xs text-muted-foreground">
                {level.quality === "silent" && "No signal: the microphone is muted or the wrong channel is selected."}
                {level.quality === "too_loud" && "Level is very high for a quiet room: lower the input gain or check the device."}
                {level.quality === "clipping" && "The signal is clipping: lower the input gain."}
              </p>
            )}
            {level?.mock && (
              <Badge variant="outline" className="font-normal">
                Mock mode: synthetic microphone input
              </Badge>
            )}
          </div>
          <Button onClick={() => setStep(1)}>Continue to round 1</Button>
        </section>
      ) : (
        <RoundPanel
          round={step as 1 | 2 | 3 | 4}
          result={results[step as 1 | 2 | 3 | 4]}
          running={running}
          progress={progress}
          captured={captured}
          onRun={run}
          onNext={() => setStep(Math.min(4, step + 1) as StepId)}
        />
      )}

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Calibration problem</AlertTitle>
          <AlertDescription className="break-words">{error}</AlertDescription>
        </Alert>
      )}

      <div className="space-y-1.5">
        <h4 className="text-sm font-semibold">Diagnostics</h4>
        <pre
          ref={logRef}
          className="h-28 overflow-y-auto whitespace-pre-wrap rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground"
        >
          {log.length ? log.join("\n") : "Nothing run yet."}
        </pre>
      </div>
    </div>
  )
}

function RoundPanel({
  round,
  result,
  running,
  progress,
  captured,
  onRun,
  onNext,
}: {
  round: 1 | 2 | 3 | 4
  result?: RoundResult
  running: 1 | 2 | 3 | 4 | null
  progress: number
  captured: number[]
  onRun: (round: 1 | 2 | 3 | 4, distance?: number) => void
  onNext: () => void
}) {
  const info = ROUND_INFO[round]
  const busy = running === round
  return (
    <section className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-semibold">
            Round {round}: {info.title}
          </h4>
          <p className="mt-1 text-sm text-muted-foreground">{info.instructions}</p>
        </div>
        {result && verdictBadge(result.verdict)}
      </div>

      {busy && <Progress value={progress} aria-label={`Round ${round} progress`} />}

      {round === 4 ? (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {TARGETS_M.map((m) => (
              <Button
                key={m}
                variant={captured.includes(m) ? "secondary" : "outline"}
                disabled={running !== null}
                onClick={() => onRun(4, m)}
                className="gap-1.5"
              >
                {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : captured.includes(m) && <Check className="h-4 w-4" />}
                Capture at {m} m
              </Button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            {captured.length} of {TARGETS_M.length} distances captured. The fit is applied after the third.
          </p>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button disabled={running !== null} onClick={() => onRun(round)}>
            {busy ? (
              <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
            ) : result ? (
              <RotateCcw className="mr-2 h-4 w-4" />
            ) : null}
            {busy ? "Running…" : result ? "Run again" : `Run round ${round}`}
          </Button>
        </div>
      )}

      {result && (
        <ul className="space-y-0.5 rounded-md border p-3 text-sm">
          {result.summary.map((line) => (
            <li key={line} className="text-muted-foreground">
              {line}
            </li>
          ))}
        </ul>
      )}

      {round < 4 && result && (
        <Button variant="outline" onClick={onNext} disabled={running !== null}>
          Next round
        </Button>
      )}
    </section>
  )
}
