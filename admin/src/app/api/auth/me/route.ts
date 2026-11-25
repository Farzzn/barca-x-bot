import { NextResponse } from "next/server";
import { isAuthConfigured, isAuthenticated } from "@/lib/auth";
import { isDatabaseConfigured } from "@/lib/db";
import { isGitHubConfigured } from "@/lib/github";

export async function GET() {
  const authed = await isAuthenticated();
  return NextResponse.json({
    authenticated: authed,
    authRequired: isAuthConfigured(),
    databaseConfigured: isDatabaseConfigured(),
    githubConfigured: isGitHubConfigured(),
    geminiPreviewReady: Boolean(process.env.BARCA_GEMINI_API_KEY?.trim() || process.env.BARCA_GEMINI_API_KEY_2?.trim()),
  });
}
