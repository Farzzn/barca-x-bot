import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { uploadImageToGitHub, type CatalogImage } from "@/lib/github";

const MAX_UPLOAD_BYTES = 4_800_000;
const VALID_CATEGORIES = new Set<CatalogImage["category"]>(["barca", "rivals", "managers", "memes"]);

export async function POST(req: Request) {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const formData = await req.formData();
    const file = formData.get("file");
    if (!(file instanceof File)) {
      return NextResponse.json({ error: "No image file provided." }, { status: 400 });
    }
    if (file.size === 0 || file.size > MAX_UPLOAD_BYTES) {
      return NextResponse.json({ error: "Image must be between 1 byte and 4.8 MB." }, { status: 400 });
    }

    const rawCategory = String(formData.get("category") || "barca").toLowerCase() as CatalogImage["category"];
    const category: CatalogImage["category"] = VALID_CATEGORIES.has(rawCategory) ? rawCategory : "barca";
    const subject = String(formData.get("subject") || "").trim();
    const club = String(formData.get("club") || "").trim();
    const mood = String(formData.get("mood") || "").trim();
    const description = String(formData.get("description") || "").trim();

    if (!subject) {
      return NextResponse.json({ error: "Player / subject name is required." }, { status: 400 });
    }

    const buffer = Buffer.from(await file.arrayBuffer());
    const uploaded = await uploadImageToGitHub({
      buffer,
      originalName: file.name || "photo.jpg",
      category,
      subject,
      club,
      mood: mood || "reaction",
      description,
    });

    return NextResponse.json({ image: uploaded }, { status: 201 });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upload failed" },
      { status: 500 }
    );
  }
}
