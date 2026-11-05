import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "ReliefGrid | Emergency coordination", description: "Coordinate critical resources with accountability." };
export default function RootLayout({ children }: Readonly<{children: React.ReactNode}>) {
  return <html lang="en"><body>{children}</body></html>;
}
