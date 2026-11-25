import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { getActivityData, isDatabaseConfigured } from "@/lib/db";
import { fetchBarcaSnapshot } from "@/lib/espn";

export async function GET(req: Request) {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const url = new URL(req.url);
    const limit = Math.min(200, Math.max(10, Number(url.searchParams.get("limit") || 60)));
    const [activity, liveMatches] = await Promise.all([
      getActivityData(limit),
      fetchBarcaSnapshot().catch(() => ({ barcaMatches: [], rivalMatches: [] })),
    ]);

    return NextResponse.json({
      ...activity,
      liveMatches,
      databaseConfigured: isDatabaseConfigured(),
    });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Failed to load activity" },
      { status: 500 }
    );
  }
}
