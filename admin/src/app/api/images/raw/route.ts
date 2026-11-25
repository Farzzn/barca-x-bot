import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { fetchRawGitHubImage } from "@/lib/github";

export async function GET(req: Request) {
  if (!(await isAuthenticated())) {
    return new NextResponse("Unauthorized", { status: 401 });
  }
  try {
    const url = new URL(req.url);
    const repoPath = url.searchParams.get("path") || "";
    if (!repoPath) return new NextResponse("Missing path", { status: 400 });
    const { bytes, contentType } = await fetchRawGitHubImage(repoPath);
    return new NextResponse(bytes, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Cache-Control": "private, max-age=3600",
      },
    });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Image load failed" },
      { status: 404 }
    );
  }
}
