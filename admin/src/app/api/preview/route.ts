import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { getBehaviorConfig, insertPostRecord } from "@/lib/db";
import { fetchBarcaSnapshot } from "@/lib/espn";
import { generatePreviewPost } from "@/lib/gemini";
import { isGitHubConfigured, listGitHubImages } from "@/lib/github";

export async function POST(req: Request) {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const body = (await req.json().catch(() => ({}))) as {
      archetype?: string;
      trigger_type?: string;
      target_club?: string;
    };

    const [{ config }, liveMatches, images] = await Promise.all([
      getBehaviorConfig(),
      fetchBarcaSnapshot().catch(() => ({ barcaMatches: [], rivalMatches: [] })),
      isGitHubConfigured() ? listGitHubImages().catch(() => []) : Promise.resolve([]),
    ]);

    const archetype = body.archetype || config.enabled_archetypes[0] || "epl_intensity_slander";
    const triggerType = body.trigger_type || "manual";
    const targetClub = body.target_club || "Arsenal";

    const result = await generatePreviewPost({
      behavior: config,
      archetype,
      triggerType,
      targetClub,
      barcaMatches: liveMatches.barcaMatches,
      rivalMatches: liveMatches.rivalMatches,
      images,
    });

    const focusMatch = liveMatches.barcaMatches[0];
    const matchLabel = focusMatch
      ? `[${focusMatch.competition}] ${focusMatch.homeTeam} vs ${focusMatch.awayTeam} (${focusMatch.detail})`
      : "";

    await insertPostRecord({
      tweet_id: "dry-run",
      archetype: result.archetype,
      match_state: "admin_preview",
      angle_summary: result.angle_summary,
      text: result.text,
      image_ids: result.selected_image_ids,
      dry_run: true,
      trigger_type: triggerType,
      gemini_key_used: result.gemini_key_used,
      gemini_fallback_reason: result.gemini_fallback_reason,
      gemini_model: result.gemini_model,
      search_grounded: false,
      match_label: matchLabel,
      target_club: targetClub,
      workflow_slot: "admin_preview",
    });

    return NextResponse.json({ preview: { ...result, target_club: targetClub } });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Preview generation failed" },
      { status: 500 }
    );
  }
}
