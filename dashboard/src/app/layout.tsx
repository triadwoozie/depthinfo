import type { Metadata } from "next"
import "./globals.css"

export const metadata: Metadata = {
  title: "Sonar Imaging — Control Dashboard",
  description:
    "Real-time calibration and telemetry dashboard for the Sonar Imaging exhibition system.",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased bg-zinc-950 text-zinc-100">{children}</body>
    </html>
  )
}
