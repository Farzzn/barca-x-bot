import { NextResponse } from "next/server";
import { setSessionCookie, verifyPassword } from "@/lib/auth";

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as { password?: string };
    if (!verifyPassword(String(body.password || ""))) {
      return NextResponse.json({ error: "Invalid admin password." }, { status: 401 });
    }
    await setSessionCookie();
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ error: "Invalid request." }, { status: 400 });
  }
}
