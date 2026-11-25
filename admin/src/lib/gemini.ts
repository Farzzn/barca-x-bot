import type { BotBehaviorConfig } from "./db";
import type { LiveMatchSnapshot } from "./espn";
import type { CatalogImage } from "./github";

export interface PreviewGenerationResult {
  text: string;
  selected_image_ids: string[];
  angle_summary: string;
  archetype: string;
  gemini_key_used: "primary" | "fallback";
  gemini_fallback_reason: string;
  gemini_model: string;
}

const SYSTEM_INSTRUCTION = `You run an unapologetic, chaotic, sharp-tongued FC Barcelona fan account on X (Football Twitter).
Short punchy paragraphs separated by blank lines. Sometimes a blunt 1-liner, ALL CAPS burst, or ending with "End."
CORE AGENDA: You push La Liga > English Premier League. Mock the EPL as the "Intensity League" (overhyped billion-pound cardio merchants). When La Liga teams beat EPL clubs or EPL teams flop in Europe, cook them hard ("Intensity League teams are trash").
STRICT RULES:
- Max 270 characters total.
- ZERO hashtags (#).
- ZERO URLs or links.
- ZERO emojis.
- Never sound like a corporate sports journalist or AI ("Hot take:", "What do you think?", "Visca el Barca!").
- Football banter only (tactics, form, refs, transfers, rivalries) — zero hate speech, slurs, or personal harassment.`;

const RESPONSE_SCHEMA = {
  type: "object",
  properties: {
    text: { type: "string", description: "X post text <= 270 chars, no hashtags, no URLs, no emojis." },
    selected_image_ids: {
      type: "array",
      items: { type: "string" },
      description: "0, 1, or 2 exact image IDs from the provided image catalog.",
    },
    angle_summary: { type: "string", description: "5-10 word summary of the banter angle." },
  },
  required: ["text", "selected_image_ids", "angle_summary"],
};

async function callGeminiInteractions(apiKey: string, model: string, prompt: string): Promise<{
  text: string;
  selected_image_ids: string[];
  angle_summary: string;
}> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 22000);
  try {
    const res = await fetch("https://generativelanguage.googleapis.com/v1/interactions", {
      method: "POST",
      headers: {
        "x-goog-api-key": apiKey,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model,
        input: prompt,
        system_instruction: SYSTEM_INSTRUCTION,
        response_format: {
          type: "text",
          mime_type: "application/json",
          schema: RESPONSE_SCHEMA,
        },
        generation_config: { temperature: 1.0 },
        store: false,
      }),
      signal: controller.signal,
    });

    if (!res.ok) {
      const body = await res.text();
      throw new Error(`HTTP ${res.status}: ${body.slice(0, 200)}`);
    }

    const data = (await res.json()) as {
      output_text?: string;
      steps?: Array<{ type?: string; content?: Array<{ type?: string; text?: string }> }>;
    };

    let outputText = data.output_text || "";
    if (!outputText && Array.isArray(data.steps)) {
      const modelSteps = data.steps.filter((s) => s.type === "model_output");
      const last = modelSteps[modelSteps.length - 1];
      if (last?.content) {
        outputText = last.content
          .filter((c) => c.type === "text" && c.text)
          .map((c) => c.text)
          .join("");
      }
    }
    if (!outputText) {
      throw new Error("Gemini returned empty output.");
    }
    return JSON.parse(outputText) as {
      text: string;
      selected_image_ids: string[];
      angle_summary: string;
    };
  } finally {
    clearTimeout(timeout);
  }
}

export async function generatePreviewPost(params: {
  behavior: BotBehaviorConfig;
  archetype: string;
  triggerType: string;
  barcaMatches: LiveMatchSnapshot[];
  rivalMatches: LiveMatchSnapshot[];
  images: CatalogImage[];
}): Promise<PreviewGenerationResult> {
  const primaryKey = process.env.BARCA_GEMINI_API_KEY?.trim() || "";
  const fallbackKey = (process.env.BARCA_GEMINI_API_KEY_2 || process.env.BARCA_GEMINI_API_KEY_FALLBACK || "").trim();

  const keys: Array<{ label: "primary" | "fallback"; key: string }> = [];
  if (primaryKey) keys.push({ label: "primary", key: primaryKey });
  if (fallbackKey) keys.push({ label: "fallback", key: fallbackKey });

  if (keys.length === 0) {
    throw new Error(
      "Set BARCA_GEMINI_API_KEY (and optional BARCA_GEMINI_API_KEY_2) in Vercel env vars to run previews here."
    );
  }

  const imageMenu =
    params.images.length > 0
      ? params.images.map((i) => `- id="${i.id}" | ${i.description} | category=${i.category}`).join("\n")
      : "No images available; return [].";

  const barcaSummary =
    params.barcaMatches
      .map((m) => `- [${m.competition}] ${m.homeTeam} ${m.homeScore ?? "vs"} ${m.awayScore ?? ""} ${m.awayTeam} (${m.detail})`)
      .join("\n") || "No Barca match in immediate 48h window.";

  const rivalSummary =
    params.rivalMatches
      .slice(0, 5)
      .map((m) => `- [${m.competition}] ${m.homeTeam} ${m.homeScore ?? "vs"} ${m.awayScore ?? ""} ${m.awayTeam} (${m.detail})`)
      .join("\n") || "None.";

  const prompt = `=== LIVE FOOTBALL CONTEXT ===
UTC Now: ${new Date().toISOString()}
Trigger: ${params.triggerType}
FC Barcelona Matches:
${barcaSummary}
Rival Matches:
${rivalSummary}

=== TARGET ARCHETYPE & TUNING ===
Archetype: ${params.archetype}
Chaos Level: ${params.behavior.chaos_level}
${params.behavior.custom_directive ? `Admin Directive: ${params.behavior.custom_directive}` : ""}

=== IMAGE CATALOG ===
${imageMenu}

Generate ONE punchy Football Twitter X post in JSON (<=270 chars, 0 hashtags, 0 URLs, 0 emojis).`;

  const preferred = params.behavior.gemini_model || "gemini-3.5-flash-lite";
  const models = Array.from(new Set([preferred, "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]));

  let primaryFailureReason = "";
  let lastError: unknown = null;

  for (const { label, key } of keys) {
    for (const model of models) {
      try {
        const parsed = await callGeminiInteractions(key, model, prompt);
        const validIds = new Set(params.images.map((i) => i.id));
        const selected = (parsed.selected_image_ids || []).filter((id) => validIds.has(id)).slice(0, 2);
        return {
          text: String(parsed.text || "").trim(),
          selected_image_ids: selected,
          angle_summary: String(parsed.angle_summary || "").trim(),
          archetype: params.archetype,
          gemini_key_used: label,
          gemini_fallback_reason: primaryFailureReason,
          gemini_model: model,
        };
      } catch (err) {
        lastError = err;
        const msg = err instanceof Error ? err.message : String(err);
        if (label === "primary" && !primaryFailureReason) {
          primaryFailureReason = msg.slice(0, 200);
        }
        if (/429|quota|401|403|api_key/i.test(msg)) {
          break; // Immediately fail over to fallback key
        }
      }
    }
  }

  throw new Error(`All Gemini keys failed: ${lastError instanceof Error ? lastError.message : String(lastError)}`);
}
