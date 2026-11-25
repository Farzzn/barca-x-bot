import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { updatePostMetrics } from "@/lib/db";

export async function PATCH(req: Request) {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const body = (await req.json()) as {
      id?: number;
      impressions?: number;
      likes?: number;
      replies?: number;
      retweets?: number;
      quotes?: number;
    };
    const id = Number(body.id);
    if (!Number.isInteger(id) || id <= 0) {
      return NextResponse.json({ error: "Valid post id is required." }, { status: 400 });
    }
    await updatePostMetrics(id, {
      impressions: Number(body.impressions || 0),
      likes: Number(body.likes || 0),
      replies: Number(body.replies || 0),
      retweets: Number(body.retweets || 0),
      quotes: Number(body.quotes || 0),
    });
    return NextResponse.json({ ok: true });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Failed to save metrics" },
      { status: 500 }
    );
  }
}
