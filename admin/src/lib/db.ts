import { Pool } from "pg";

export interface BotBehaviorConfig {
  bot_enabled: boolean;
  dry_run: boolean;
  max_posts_per_day: number;
  min_minutes_between_posts: number;
  matchday_cooldown_minutes: number;
  post_hours_utc: number[];
  image_post_ratio: number;
  gemini_model: string;
  enable_google_search: boolean;
  auto_post_halftime: boolean;
  auto_post_fulltime: boolean;
  auto_post_prematch: boolean;
  auto_post_rival_drama: boolean;
  chaos_level: "balanced" | "spicy" | "unhinged";
  enabled_archetypes: string[];
  custom_directive: string;
}

export interface PostHistoryRow {
  id: number;
  tweet_id: string;
  archetype: string;
  match_state: string;
  angle_summary: string;
  text: string;
  image_ids: string[];
  dry_run: boolean;
  trigger_type: string;
  gemini_key_used: string;
  gemini_fallback_reason: string;
  gemini_model: string;
  search_grounded: boolean;
  match_label: string;
  created_at_utc: string;
}

export const ALL_ARCHETYPES = [
  { id: "pre_match_delusion", label: "Pre-Match Delusion", desc: "Overconfident lineup & scoreline hype" },
  { id: "post_win_hype", label: "Post-Win / HT Euphoria", desc: "Unhinged celebration & treble talk" },
  { id: "post_drop_meltdown", label: "Draw/Loss Meltdown", desc: "Instant rage, 'tactically empty', 'sack him'" },
  { id: "rival_comparison_bait", label: "Rival Comparison Bait", desc: "Side-by-side Barça vs Madrid player civil wars" },
  { id: "rival_match_slander", label: "Rival Match Slander", desc: "Madrid/UCL jinxes, haram ball & ref banter" },
  { id: "epl_intensity_slander", label: "EPL 'Intensity League' Slander", desc: "Mock Premier League clubs & push La Liga > EPL" },
  { id: "would_you_rather", label: "Would You Rather", desc: "2-option Barça transfer & squad dilemmas" },
  { id: "unpopular_one_percent", label: "Unpopular 1% Takes", desc: "Deadpan contrarian Culer hot takes" },
] as const;

export const DEFAULT_BEHAVIOR: BotBehaviorConfig = {
  bot_enabled: true,
  dry_run: false,
  max_posts_per_day: 6,
  min_minutes_between_posts: 110,
  matchday_cooldown_minutes: 45,
  post_hours_utc: [8, 11, 14, 17, 20, 22],
  image_post_ratio: 0.55,
  gemini_model: "gemini-3.5-flash-lite",
  enable_google_search: false,
  auto_post_halftime: true,
  auto_post_fulltime: true,
  auto_post_prematch: true,
  auto_post_rival_drama: true,
  chaos_level: "spicy",
  enabled_archetypes: ALL_ARCHETYPES.map((a) => a.id),
  custom_directive: "",
};

const globalForPg = globalThis as unknown as { barcaPool?: Pool; barcaSchemaReady?: boolean };

export function isDatabaseConfigured(): boolean {
  return Boolean(process.env.DATABASE_URL?.trim());
}

function getPool(): Pool {
  if (!globalForPg.barcaPool) {
    const rawUrl = process.env.DATABASE_URL?.trim() || "";
    const connectionString = rawUrl.startsWith("postgres://")
      ? `postgresql://${rawUrl.slice("postgres://".length)}`
      : rawUrl;
    globalForPg.barcaPool = new Pool({
      connectionString,
      ssl: connectionString.includes("localhost") ? undefined : { rejectUnauthorized: false },
      max: 5,
    });
  }
  return globalForPg.barcaPool;
}

export async function ensureSchema(): Promise<void> {
  if (!isDatabaseConfigured() || globalForPg.barcaSchemaReady) return;
  const pool = getPool();
  await pool.query(`
    CREATE TABLE IF NOT EXISTS x_post_history (
      id SERIAL PRIMARY KEY,
      tweet_id TEXT,
      archetype TEXT NOT NULL,
      match_state TEXT NOT NULL,
      angle_summary TEXT,
      text TEXT NOT NULL,
      image_ids TEXT NOT NULL,
      dry_run BOOLEAN NOT NULL DEFAULT FALSE,
      trigger_type TEXT NOT NULL DEFAULT 'scheduled',
      gemini_key_used TEXT NOT NULL DEFAULT 'primary',
      gemini_fallback_reason TEXT NOT NULL DEFAULT '',
      gemini_model TEXT NOT NULL DEFAULT '',
      search_grounded BOOLEAN NOT NULL DEFAULT FALSE,
      match_label TEXT NOT NULL DEFAULT '',
      created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);
  await pool.query(`
    CREATE TABLE IF NOT EXISTS bot_settings (
      id INTEGER PRIMARY KEY,
      config_json TEXT NOT NULL,
      updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);
  await pool.query(`
    CREATE TABLE IF NOT EXISTS match_events_sent (
      match_key TEXT NOT NULL,
      event_type TEXT NOT NULL,
      created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      PRIMARY KEY (match_key, event_type)
    );
  `);
  // Safe migrations for existing v1 tables
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS trigger_type TEXT NOT NULL DEFAULT 'scheduled'`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS gemini_key_used TEXT NOT NULL DEFAULT 'primary'`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS gemini_fallback_reason TEXT NOT NULL DEFAULT ''`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS gemini_model TEXT NOT NULL DEFAULT ''`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS search_grounded BOOLEAN NOT NULL DEFAULT FALSE`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS match_label TEXT NOT NULL DEFAULT ''`);

  globalForPg.barcaSchemaReady = true;
}

export function sanitizeBehaviorConfig(input: Partial<BotBehaviorConfig>): BotBehaviorConfig {
  const hoursRaw = Array.isArray(input.post_hours_utc) ? input.post_hours_utc : DEFAULT_BEHAVIOR.post_hours_utc;
  const postHours = Array.from(
    new Set(hoursRaw.map((h) => Number(h)).filter((h) => Number.isInteger(h) && h >= 0 && h <= 23))
  ).sort((a, b) => a - b);

  const validArchSet = new Set<string>(ALL_ARCHETYPES.map((a) => a.id));
  const enabledArch = Array.isArray(input.enabled_archetypes)
    ? input.enabled_archetypes.filter((a) => validArchSet.has(String(a)))
    : DEFAULT_BEHAVIOR.enabled_archetypes;

  const chaos =
    input.chaos_level === "balanced" || input.chaos_level === "spicy" || input.chaos_level === "unhinged"
      ? input.chaos_level
      : DEFAULT_BEHAVIOR.chaos_level;

  return {
    bot_enabled: typeof input.bot_enabled === "boolean" ? input.bot_enabled : DEFAULT_BEHAVIOR.bot_enabled,
    dry_run: typeof input.dry_run === "boolean" ? input.dry_run : DEFAULT_BEHAVIOR.dry_run,
    max_posts_per_day: Math.min(15, Math.max(1, Number(input.max_posts_per_day ?? DEFAULT_BEHAVIOR.max_posts_per_day) || 6)),
    min_minutes_between_posts: Math.min(
      720,
      Math.max(15, Number(input.min_minutes_between_posts ?? DEFAULT_BEHAVIOR.min_minutes_between_posts) || 110)
    ),
    matchday_cooldown_minutes: Math.min(
      240,
      Math.max(10, Number(input.matchday_cooldown_minutes ?? DEFAULT_BEHAVIOR.matchday_cooldown_minutes) || 45)
    ),
    post_hours_utc: postHours.length > 0 ? postHours : DEFAULT_BEHAVIOR.post_hours_utc,
    image_post_ratio: Math.min(
      1,
      Math.max(0, Number(input.image_post_ratio ?? DEFAULT_BEHAVIOR.image_post_ratio))
    ),
    gemini_model: String(input.gemini_model || DEFAULT_BEHAVIOR.gemini_model).trim(),
    enable_google_search:
      typeof input.enable_google_search === "boolean"
        ? input.enable_google_search
        : DEFAULT_BEHAVIOR.enable_google_search,
    auto_post_halftime:
      typeof input.auto_post_halftime === "boolean" ? input.auto_post_halftime : DEFAULT_BEHAVIOR.auto_post_halftime,
    auto_post_fulltime:
      typeof input.auto_post_fulltime === "boolean" ? input.auto_post_fulltime : DEFAULT_BEHAVIOR.auto_post_fulltime,
    auto_post_prematch:
      typeof input.auto_post_prematch === "boolean" ? input.auto_post_prematch : DEFAULT_BEHAVIOR.auto_post_prematch,
    auto_post_rival_drama:
      typeof input.auto_post_rival_drama === "boolean"
        ? input.auto_post_rival_drama
        : DEFAULT_BEHAVIOR.auto_post_rival_drama,
    chaos_level: chaos,
    enabled_archetypes: enabledArch.length > 0 ? enabledArch : DEFAULT_BEHAVIOR.enabled_archetypes,
    custom_directive: String(input.custom_directive ?? "").slice(0, 500),
  };
}

export async function getBehaviorConfig(): Promise<{ config: BotBehaviorConfig; updatedAt: string | null }> {
  if (!isDatabaseConfigured()) {
    return { config: DEFAULT_BEHAVIOR, updatedAt: null };
  }
  await ensureSchema();
  const pool = getPool();
  const res = await pool.query("SELECT config_json, updated_at_utc FROM bot_settings WHERE id = 1");
  if (res.rows.length === 0) {
    await saveBehaviorConfig(DEFAULT_BEHAVIOR);
    return { config: DEFAULT_BEHAVIOR, updatedAt: new Date().toISOString() };
  }
  const parsed = JSON.parse(res.rows[0].config_json) as Partial<BotBehaviorConfig>;
  return {
    config: sanitizeBehaviorConfig(parsed),
    updatedAt: res.rows[0].updated_at_utc ? new Date(res.rows[0].updated_at_utc).toISOString() : null,
  };
}

export async function saveBehaviorConfig(config: BotBehaviorConfig): Promise<BotBehaviorConfig> {
  const clean = sanitizeBehaviorConfig(config);
  if (!isDatabaseConfigured()) {
    throw new Error("DATABASE_URL is not configured. Connect a free Neon or Supabase Postgres database.");
  }
  await ensureSchema();
  const pool = getPool();
  await pool.query(
    `
    INSERT INTO bot_settings (id, config_json, updated_at_utc)
    VALUES (1, $1, NOW())
    ON CONFLICT (id) DO UPDATE
    SET config_json = EXCLUDED.config_json, updated_at_utc = NOW()
    `,
    [JSON.stringify(clean)]
  );
  return clean;
}

export async function getActivityData(limit = 60): Promise<{
  posts: PostHistoryRow[];
  stats: {
    postsTodayLive: number;
    totalLivePosts: number;
    totalDryRuns: number;
    primaryKeyCount: number;
    fallbackKeyCount: number;
    halftimeCount: number;
    fulltimeCount: number;
    estimatedXSpendUsd: number;
  };
}> {
  if (!isDatabaseConfigured()) {
    return {
      posts: [],
      stats: {
        postsTodayLive: 0,
        totalLivePosts: 0,
        totalDryRuns: 0,
        primaryKeyCount: 0,
        fallbackKeyCount: 0,
        halftimeCount: 0,
        fulltimeCount: 0,
        estimatedXSpendUsd: 0,
      },
    };
  }
  await ensureSchema();
  const pool = getPool();

  const [rowsRes, aggRes] = await Promise.all([
    pool.query(
      `
      SELECT id, tweet_id, archetype, match_state, angle_summary, text, image_ids,
             dry_run, trigger_type, gemini_key_used, gemini_fallback_reason,
             gemini_model, search_grounded, match_label, created_at_utc
      FROM x_post_history
      ORDER BY id DESC
      LIMIT $1
      `,
      [limit]
    ),
    pool.query(`
      SELECT
        COUNT(*) FILTER (WHERE dry_run = FALSE AND created_at_utc >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')) AS posts_today_live,
        COUNT(*) FILTER (WHERE dry_run = FALSE) AS total_live,
        COUNT(*) FILTER (WHERE dry_run = TRUE) AS total_dry,
        COUNT(*) FILTER (WHERE gemini_key_used = 'primary') AS primary_key_count,
        COUNT(*) FILTER (WHERE gemini_key_used = 'fallback') AS fallback_key_count,
        COUNT(*) FILTER (WHERE trigger_type = 'halftime') AS halftime_count,
        COUNT(*) FILTER (WHERE trigger_type = 'fulltime') AS fulltime_count
      FROM x_post_history
    `),
  ]);

  const posts: PostHistoryRow[] = rowsRes.rows.map((r) => {
    let imageIds: string[] = [];
    try {
      imageIds = JSON.parse(r.image_ids || "[]");
    } catch {
      imageIds = [];
    }
    return {
      id: Number(r.id),
      tweet_id: r.tweet_id || "",
      archetype: r.archetype,
      match_state: r.match_state,
      angle_summary: r.angle_summary || "",
      text: r.text,
      image_ids: imageIds,
      dry_run: Boolean(r.dry_run),
      trigger_type: r.trigger_type || "scheduled",
      gemini_key_used: r.gemini_key_used || "primary",
      gemini_fallback_reason: r.gemini_fallback_reason || "",
      gemini_model: r.gemini_model || "",
      search_grounded: Boolean(r.search_grounded),
      match_label: r.match_label || "",
      created_at_utc: new Date(r.created_at_utc).toISOString(),
    };
  });

  const a = aggRes.rows[0] || {};
  const totalLive = Number(a.total_live || 0);
  return {
    posts,
    stats: {
      postsTodayLive: Number(a.posts_today_live || 0),
      totalLivePosts: totalLive,
      totalDryRuns: Number(a.total_dry || 0),
      primaryKeyCount: Number(a.primary_key_count || 0),
      fallbackKeyCount: Number(a.fallback_key_count || 0),
      halftimeCount: Number(a.halftime_count || 0),
      fulltimeCount: Number(a.fulltime_count || 0),
      estimatedXSpendUsd: Number((totalLive * 0.015).toFixed(3)),
    },
  };
}

export async function insertPostRecord(record: Omit<PostHistoryRow, "id" | "created_at_utc">): Promise<void> {
  if (!isDatabaseConfigured()) return;
  await ensureSchema();
  const pool = getPool();
  await pool.query(
    `
    INSERT INTO x_post_history (
      tweet_id, archetype, match_state, angle_summary, text, image_ids,
      dry_run, trigger_type, gemini_key_used, gemini_fallback_reason,
      gemini_model, search_grounded, match_label, created_at_utc
    )
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,NOW())
    `,
    [
      record.tweet_id,
      record.archetype,
      record.match_state,
      record.angle_summary,
      record.text,
      JSON.stringify(record.image_ids),
      record.dry_run,
      record.trigger_type,
      record.gemini_key_used,
      record.gemini_fallback_reason,
      record.gemini_model,
      record.search_grounded,
      record.match_label,
    ]
  );
}
