import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { getBehaviorConfig, isDatabaseConfigured, saveBehaviorConfig, type BotBehaviorConfig } from "@/lib/db";

export async function GET() {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const data = await getBehaviorConfig();
    return NextResponse.json({ ...data, databaseConfigured: isDatabaseConfigured() });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Failed to load settings" },
      { status: 500 }
    );
  }
}

export async function PUT(req: Request) {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const body = (await req.json()) as BotBehaviorConfig;
    const saved = await saveBehaviorConfig(body);
    return NextResponse.json({ config: saved, updatedAt: new Date().toISOString() });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Failed to save settings" },
      { status: 500 }
    );
  }
}
