import { defineConfig } from "@playwright/test";
export default defineConfig({ testDir: ".", use: { baseURL: process.env.WEB_URL || "http://localhost:3000" }, reporter: [["list"], ["html", {open:"never"}]], retries: 0 });
