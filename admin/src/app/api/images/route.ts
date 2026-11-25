import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { deleteImageFromGitHub, isGitHubConfigured, listGitHubImages } from "@/lib/github";

export async function GET() {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const images = await listGitHubImages();
    return NextResponse.json({
      images,
      githubConfigured: isGitHubConfigured(),
      repo: process.env.GITHUB_REPO || "",
      branch: process.env.GITHUB_BRANCH || "main",
    });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Failed to list GitHub images" },
      { status: 500 }
    );
  }
}

export async function DELETE(req: Request) {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const body = (await req.json()) as { relative_path?: string };
    if (!body.relative_path) {
      return NextResponse.json({ error: "relative_path is required" }, { status: 400 });
    }
    await deleteImageFromGitHub(body.relative_path);
    return NextResponse.json({ ok: true });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Failed to delete image" },
      { status: 500 }
    );
  }
}
