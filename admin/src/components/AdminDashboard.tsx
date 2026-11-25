"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  ExternalLink,
  Flame,
  Image as ImageIcon,
  KeyRound,
  Loader2,
  Lock,
  LogOut,
  PauseCircle,
  Pencil,
  PlayCircle,
  RefreshCw,
  Save,
  Sliders,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import type { BotBehaviorConfig, ClubAnalyticsRow, PostHistoryRow } from "@/lib/db";
import type { LiveMatchSnapshot } from "@/lib/espn";
import type { CatalogImage } from "@/lib/github";

type TabId = "activity" | "analytics" | "controls" | "images";

interface AuthStatus {
  authenticated: boolean;
  authRequired: boolean;
  databaseConfigured: boolean;
  githubConfigured: boolean;
  geminiPreviewReady: boolean;
}

interface ActivityStats {
  postsTodayLive: number;
  postsLast7Days: number;
  dailyAvg7d: number;
  totalLivePosts: number;
  totalDryRuns: number;
  primaryKeyCount: number;
  fallbackKeyCount: number;
  halftimeCount: number;
  fulltimeCount: number;
  estimatedXSpendUsd: number;
}

interface PreviewResult {
  text: string;
  selected_image_ids: string[];
  angle_summary: string;
  archetype: string;
  target_club?: string;
  gemini_key_used: "primary" | "fallback";
  gemini_fallback_reason: string;
  gemini_model: string;
}

const ARCHETYPES = [
  { id: "pre_match_delusion", label: "Pre-Match & Lineup Brag", desc: "8h pre-match & lineup hype" },
  { id: "post_win_hype", label: "HT / FT Win Euphoria", desc: "Unhinged celebration & treble talk" },
  { id: "post_drop_meltdown", label: "HT / FT Meltdown", desc: "Instant rage, 'send Gordon back', 'Flick out'" },
  { id: "rival_comparison_bait", label: "Rival Comparison Bait", desc: "Side-by-side Barça vs rival player wars" },
  { id: "rival_match_slander", label: "Real Madrid & Rival Slander", desc: "Madrid lineup/FT discrediting & rival roasts" },
  { id: "epl_intensity_slander", label: "EPL 'Intensity League' Slander", desc: "Mock Premier League clubs & La Liga > EPL" },
  { id: "would_you_rather", label: "Would You Rather", desc: "2-option Barça transfer & squad dilemmas" },
  { id: "unpopular_one_percent", label: "Unpopular 1% Takes", desc: "Deadpan contrarian Culer hot takes" },
];

const TARGET_CLUBS = [
  "FC Barcelona",
  "Real Madrid",
  "Arsenal",
  "Manchester City",
  "Liverpool",
  "Chelsea",
  "Manchester United",
  "Atletico Madrid",
  "PSG",
  "Bayern Munich",
  "EPL (Intensity League)",
];

const DEFAULT_CONFIG: BotBehaviorConfig = {
  bot_enabled: true,
  dry_run: false,
  max_posts_per_day: 6,
  weekly_post_target: 26,
  non_matchday_min_posts: 1,
  non_matchday_max_posts: 3,
  min_minutes_between_posts: 150,
  matchday_cooldown_minutes: 20,
  post_hours_utc: [6, 11, 16],
  image_post_ratio: 0.6,
  gemini_model: "gemini-3.5-flash-lite",
  enable_google_search: false,
  auto_post_halftime: true,
  auto_post_fulltime: true,
  auto_post_prematch: true,
  auto_post_rival_drama: true,
  chaos_level: "spicy",
  enabled_archetypes: ARCHETYPES.map((a) => a.id),
  custom_directive: "",
};

const MOOD_PRESETS = [
  "celebrating",
  "confident",
  "cold",
  "smiling",
  "laughing",
  "serious",
  "focused",
  "angry",
  "frustrated",
  "complaining",
  "shocked",
  "sad",
  "crying",
  "arms_up",
];

function formatRelativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default function AdminDashboard() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);

  const [tab, setTab] = useState<TabId>("activity");
  const [loading, setLoading] = useState(false);
  const [banner, setBanner] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  // Settings state
  const [config, setConfig] = useState<BotBehaviorConfig>(DEFAULT_CONFIG);
  const [savingConfig, setSavingConfig] = useState(false);
  const [settingsUpdatedAt, setSettingsUpdatedAt] = useState<string | null>(null);

  // Activity & Club Analytics state
  const [posts, setPosts] = useState<PostHistoryRow[]>([]);
  const [clubAnalytics, setClubAnalytics] = useState<ClubAnalyticsRow[]>([]);
  const [stats, setStats] = useState<ActivityStats>({
    postsTodayLive: 0,
    postsLast7Days: 0,
    dailyAvg7d: 0,
    totalLivePosts: 0,
    totalDryRuns: 0,
    primaryKeyCount: 0,
    fallbackKeyCount: 0,
    halftimeCount: 0,
    fulltimeCount: 0,
    estimatedXSpendUsd: 0,
  });
  const [barcaMatches, setBarcaMatches] = useState<LiveMatchSnapshot[]>([]);
  const [rivalMatches, setRivalMatches] = useState<LiveMatchSnapshot[]>([]);
  const [activityFilter, setActivityFilter] = useState<"all" | "live" | "dry" | "fallback" | "match">("all");
  const [clubFilter, setClubFilter] = useState<string>("all");

  // Inline free metrics editor state
  const [editingMetricsId, setEditingMetricsId] = useState<number | null>(null);
  const [metricsDraft, setMetricsDraft] = useState({ impressions: 0, likes: 0, replies: 0, retweets: 0, quotes: 0 });
  const [savingMetrics, setSavingMetrics] = useState(false);

  // Preview generator state
  const [previewArchetype, setPreviewArchetype] = useState("epl_intensity_slander");
  const [previewClub, setPreviewClub] = useState("Arsenal");
  const [previewTrigger, setPreviewTrigger] = useState("manual");
  const [generatingPreview, setGeneratingPreview] = useState(false);
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null);

  // GitHub Images state
  const [images, setImages] = useState<CatalogImage[]>([]);
  const [githubMeta, setGithubMeta] = useState<{ repo: string; branch: string; configured: boolean }>({
    repo: "",
    branch: "main",
    configured: false,
  });
  const [imageFilterCat, setImageFilterCat] = useState<string>("all");
  const [uploading, setUploading] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadCategory, setUploadCategory] = useState<"barca" | "rivals" | "managers" | "memes">("barca");
  const [uploadSubject, setUploadSubject] = useState("");
  const [uploadClub, setUploadClub] = useState("FC Barcelona");
  const [uploadMood, setUploadMood] = useState("celebrating");
  const [uploadDescription, setUploadDescription] = useState("");
  const [deletingPath, setDeletingPath] = useState<string | null>(null);

  const flash = useCallback((type: "ok" | "err", text: string) => {
    setBanner({ type, text });
    setTimeout(() => setBanner((curr) => (curr?.text === text ? null : curr)), 5000);
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [settingsRes, activityRes, imagesRes] = await Promise.all([
        fetch("/api/settings", { cache: "no-store" }),
        fetch("/api/activity?limit=120", { cache: "no-store" }),
        fetch("/api/images", { cache: "no-store" }),
      ]);

      if (settingsRes.ok) {
        const sData = await settingsRes.json();
        setConfig(sData.config || DEFAULT_CONFIG);
        setSettingsUpdatedAt(sData.updatedAt || null);
      }
      if (activityRes.ok) {
        const aData = await activityRes.json();
        setPosts(aData.posts || []);
        setClubAnalytics(aData.clubAnalytics || []);
        if (aData.stats) setStats(aData.stats);
        setBarcaMatches(aData.liveMatches?.barcaMatches || []);
        setRivalMatches(aData.liveMatches?.rivalMatches || []);
      }
      if (imagesRes.ok) {
        const iData = await imagesRes.json();
        setImages(iData.images || []);
        setGithubMeta({
          repo: iData.repo || "",
          branch: iData.branch || "main",
          configured: Boolean(iData.githubConfigured),
        });
      }
    } catch (err) {
      flash("err", err instanceof Error ? err.message : "Failed to load dashboard data");
    } finally {
      setLoading(false);
    }
  }, [flash]);

  useEffect(() => {
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => r.json())
      .then((data: AuthStatus) => {
        setAuth(data);
        if (data.authenticated) {
          void loadAll();
        }
      })
      .catch(() => {
        setAuth({
          authenticated: false,
          authRequired: true,
          databaseConfigured: false,
          githubConfigured: false,
          geminiPreviewReady: false,
        });
      });
  }, [loadAll]);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoggingIn(true);
    setAuthError("");
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setAuthError(d.error || "Login failed");
        return;
      }
      setPassword("");
      const me = await fetch("/api/auth/me").then((r) => r.json());
      setAuth(me);
      await loadAll();
    } finally {
      setLoggingIn(false);
    }
  }

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    setAuth((prev) => (prev ? { ...prev, authenticated: false } : null));
  }

  async function handleSaveConfig(nextConfig: BotBehaviorConfig = config) {
    setSavingConfig(true);
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(nextConfig),
      });
      const data = await res.json();
      if (!res.ok) {
        flash("err", data.error || "Failed to save settings");
        return;
      }
      setConfig(data.config);
      setSettingsUpdatedAt(data.updatedAt);
      flash("ok", "Behavior settings saved live — Heroku will use them on the very next 10-min tick.");
    } catch (err) {
      flash("err", err instanceof Error ? err.message : "Save failed");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleQuickToggleBot() {
    const updated = { ...config, bot_enabled: !config.bot_enabled };
    setConfig(updated);
    await handleSaveConfig(updated);
  }

  async function handleGeneratePreview() {
    setGeneratingPreview(true);
    setPreviewResult(null);
    try {
      const res = await fetch("/api/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          archetype: previewArchetype,
          trigger_type: previewTrigger,
          target_club: previewClub,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        flash("err", data.error || "Preview generation failed");
        return;
      }
      setPreviewResult(data.preview);
      flash("ok", `Preview generated for ${previewClub} via Gemini ${data.preview.gemini_key_used.toUpperCase()} key!`);
      await loadAll();
    } catch (err) {
      flash("err", err instanceof Error ? err.message : "Preview error");
    } finally {
      setGeneratingPreview(false);
    }
  }

  function startEditMetrics(post: PostHistoryRow) {
    setEditingMetricsId(post.id);
    setMetricsDraft({
      impressions: post.impressions,
      likes: post.likes,
      replies: post.replies,
      retweets: post.retweets,
      quotes: post.quotes,
    });
  }

  async function handleSaveMetrics(postId: number) {
    setSavingMetrics(true);
    try {
      const res = await fetch("/api/metrics", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: postId, ...metricsDraft }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        flash("err", d.error || "Could not save metrics");
        return;
      }
      setEditingMetricsId(null);
      flash("ok", "Post reach stats saved — Club Reach leaderboard updated!");
      await loadAll();
    } finally {
      setSavingMetrics(false);
    }
  }

  async function handleUploadImage(e: React.FormEvent) {
    e.preventDefault();
    if (!uploadFile || !uploadSubject.trim()) {
      flash("err", "Choose an image file and enter the player/subject name.");
      return;
    }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", uploadFile);
      fd.append("category", uploadCategory);
      fd.append("subject", uploadSubject.trim());
      fd.append("club", uploadClub.trim());
      fd.append("mood", uploadMood.trim());
      fd.append("description", uploadDescription.trim());

      const res = await fetch("/api/images/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) {
        flash("err", data.error || "Upload failed");
        return;
      }
      flash("ok", `Uploaded ${data.image.relative_path} to GitHub & synced catalog.json!`);
      setUploadFile(null);
      setUploadSubject("");
      setUploadDescription("");
      await loadAll();
    } catch (err) {
      flash("err", err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function handleDeleteImage(relativePath: string) {
    setDeletingPath(relativePath);
    try {
      const res = await fetch("/api/images", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ relative_path: relativePath }),
      });
      const data = await res.json();
      if (!res.ok) {
        flash("err", data.error || "Delete failed");
        return;
      }
      flash("ok", `Deleted ${relativePath} from GitHub.`);
      await loadAll();
    } finally {
      setDeletingPath(null);
    }
  }

  const availableClubNames = useMemo(() => {
    const s = new Set<string>(TARGET_CLUBS);
    for (const p of posts) {
      if (p.target_club) s.add(p.target_club);
    }
    return Array.from(s);
  }, [posts]);

  const filteredPosts = useMemo(() => {
    return posts.filter((p) => {
      if (clubFilter !== "all" && p.target_club !== clubFilter) return false;
      if (activityFilter === "live") return !p.dry_run;
      if (activityFilter === "dry") return p.dry_run;
      if (activityFilter === "fallback") return p.gemini_key_used === "fallback";
      if (activityFilter === "match") {
        return ["halftime", "fulltime", "lineup", "matchday", "thursday_ucl"].includes(p.trigger_type);
      }
      return true;
    });
  }, [posts, activityFilter, clubFilter]);

  const filteredImages = useMemo(() => {
    if (imageFilterCat === "all") return images;
    return images.filter((img) => img.category === imageFilterCat);
  }, [images, imageFilterCat]);

  const maxClubAvgImpressions = useMemo(
    () => Math.max(1, ...clubAnalytics.map((c) => c.avgImpressions || c.livePosts)),
    [clubAnalytics]
  );

  const totalKeyCalls = stats.primaryKeyCount + stats.fallbackKeyCount;
  const primaryPct = totalKeyCalls > 0 ? Math.round((stats.primaryKeyCount / totalKeyCalls) * 100) : 100;

  if (!auth) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-[#edbb00]" />
      </div>
    );
  }

  if (!auth.authenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <form
          onSubmit={handleLogin}
          className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900/90 p-8 shadow-2xl backdrop-blur"
        >
          <div className="mb-6 flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-[#004d98] to-[#a50044] shadow-lg">
              <Lock className="h-5 w-5 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Barca X Command Center</h1>
              <p className="text-xs text-slate-400">Admin authentication required</p>
            </div>
          </div>

          <label className="mb-2 block text-sm font-medium text-slate-300">Admin Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Enter ADMIN_PASSWORD"
            className="mb-4 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-white outline-none focus:border-[#004d98]"
            required
          />
          {authError && <p className="mb-4 text-sm text-rose-400">{authError}</p>}

          <button
            type="submit"
            disabled={loggingIn}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-[#004d98] to-[#a50044] px-4 py-3 text-sm font-semibold text-white transition hover:opacity-95 disabled:opacity-50"
          >
            {loggingIn ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
            Sign In
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      {/* Header */}
      <header className="mb-6 flex flex-col gap-4 rounded-2xl border border-slate-800/80 bg-slate-900/75 p-5 shadow-xl backdrop-blur sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3.5">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-[#004d98] via-[#4a266a] to-[#a50044] text-lg font-black text-[#edbb00] shadow-lg">
            FCB
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-bold tracking-tight text-white">Barca X Auto-Poster</h1>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                  config.bot_enabled
                    ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30"
                    : "bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/30"
                }`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${config.bot_enabled ? "bg-emerald-400" : "bg-amber-400"}`} />
                {config.bot_enabled ? (config.dry_run ? "DRY RUN" : "LIVE AUTO") : "PAUSED"}
              </span>
            </div>
            <p className="mt-0.5 text-xs text-slate-400">
              {barcaMatches[0]
                ? `Next/Live Barça: [${barcaMatches[0].competition}] ${barcaMatches[0].homeTeam} ${
                    barcaMatches[0].homeScore ?? "vs"
                  } ${barcaMatches[0].awayScore ?? ""} ${barcaMatches[0].awayTeam} (${barcaMatches[0].detail})`
                : "No immediate Barça match — Big-8 rotation & Real Madrid / Thursday UCL radar active"}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={handleQuickToggleBot}
            disabled={savingConfig || !auth.databaseConfigured}
            className={`inline-flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-xs font-semibold transition ${
              config.bot_enabled
                ? "border border-amber-500/40 bg-amber-500/10 text-amber-200 hover:bg-amber-500/20"
                : "border border-emerald-500/40 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25"
            } disabled:opacity-40`}
          >
            {config.bot_enabled ? <PauseCircle className="h-4 w-4" /> : <PlayCircle className="h-4 w-4" />}
            {config.bot_enabled ? "Pause Bot" : "Resume Bot"}
          </button>

          <button
            onClick={() => void loadAll()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800/80 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-700"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>

          {auth.authRequired && (
            <button
              onClick={handleLogout}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800/60 px-3 py-2 text-xs font-medium text-slate-300 hover:bg-slate-700"
            >
              <LogOut className="h-3.5 w-3.5" />
              Sign Out
            </button>
          )}
        </div>
      </header>

      {/* Toast Banner */}
      {banner && (
        <div
          className={`mb-5 flex items-center gap-2 rounded-xl border px-4 py-3 text-sm ${
            banner.type === "ok"
              ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-200"
              : "border-rose-500/40 bg-rose-500/15 text-rose-200"
          }`}
        >
          {banner.type === "ok" ? <CheckCircle2 className="h-4 w-4 shrink-0" /> : <AlertTriangle className="h-4 w-4 shrink-0" />}
          <span>{banner.text}</span>
        </div>
      )}

      {/* Navigation Tabs */}
      <nav className="mb-6 flex flex-wrap gap-2">
        {[
          { id: "activity" as const, label: "Activity & API Keys", icon: Activity },
          { id: "analytics" as const, label: "Club Reach & Analytics", icon: BarChart3 },
          { id: "controls" as const, label: "Workflow & Behavior", icon: Sliders },
          { id: "images" as const, label: `GitHub Images (${images.length})`, icon: ImageIcon },
        ].map((item) => {
          const Icon = item.icon;
          const active = tab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setTab(item.id)}
              className={`inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
                active
                  ? "bg-gradient-to-r from-[#004d98] to-[#a50044] text-white shadow-lg"
                  : "border border-slate-800 bg-slate-900/70 text-slate-300 hover:bg-slate-800"
              }`}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </button>
          );
        })}
      </nav>

      {/* ==================== TAB 1: ACTIVITY & GEMINI KEY TELEMETRY ==================== */}
      {tab === "activity" && (
        <div className="space-y-6">
          {/* KPI Cards */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-4">
              <p className="text-xs text-slate-400">7-Day Rolling Pace</p>
              <p className="mt-1 text-2xl font-bold text-white">
                {stats.postsLast7Days}{" "}
                <span className="text-sm font-normal text-slate-400">/ {config.weekly_post_target}</span>
              </p>
              <p className="text-[11px] text-sky-300">
                Avg {stats.dailyAvg7d.toFixed(2)}/day (target ≤ 3.71)
              </p>
            </div>

            <div className="rounded-2xl border border-emerald-500/25 bg-slate-900/75 p-4">
              <p className="flex items-center gap-1.5 text-xs text-emerald-300">
                <KeyRound className="h-3.5 w-3.5" /> Gemini Key #1 (Primary)
              </p>
              <p className="mt-1 text-2xl font-bold text-white">
                {stats.primaryKeyCount} <span className="text-xs font-medium text-emerald-400">({primaryPct}%)</span>
              </p>
            </div>

            <div className="rounded-2xl border border-amber-500/25 bg-slate-900/75 p-4">
              <p className="flex items-center gap-1.5 text-xs text-amber-300">
                <KeyRound className="h-3.5 w-3.5" /> Gemini Key #2 (Fallback)
              </p>
              <p className="mt-1 text-2xl font-bold text-white">
                {stats.fallbackKeyCount}{" "}
                <span className="text-xs font-medium text-amber-400">
                  ({totalKeyCalls > 0 ? 100 - primaryPct : 0}%)
                </span>
              </p>
            </div>

            <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-4">
              <p className="flex items-center gap-1.5 text-xs text-sky-300">
                <Flame className="h-3.5 w-3.5" /> Live Posts Today
              </p>
              <p className="mt-1 text-2xl font-bold text-white">{stats.postsTodayLive}</p>
              <p className="text-[11px] text-slate-400">
                {stats.halftimeCount} HT · {stats.fulltimeCount} FT total
              </p>
            </div>

            <div className="col-span-2 rounded-2xl border border-slate-800 bg-slate-900/75 p-4 sm:col-span-1">
              <p className="text-xs text-slate-400">Est. X API Spend</p>
              <p className="mt-1 text-2xl font-bold text-[#edbb00]">${stats.estimatedXSpendUsd.toFixed(2)}</p>
              <p className="text-[11px] text-slate-500">{stats.totalLivePosts} live posts × $0.015</p>
            </div>
          </div>

          {/* 1-Click Live Preview Generator */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="flex items-center gap-2 text-base font-bold text-white">
                  <Sparkles className="h-4 w-4 text-[#edbb00]" />
                  Instant Dry-Run Preview (Test Any Rival Club)
                </h2>
                <p className="text-xs text-slate-400">
                  Tests Primary → Fallback Gemini keys, live ESPN context, and GitHub images without spending X credits.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={previewClub}
                  onChange={(e) => setPreviewClub(e.target.value)}
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
                >
                  {TARGET_CLUBS.map((c) => (
                    <option key={c} value={c}>
                      Target: {c}
                    </option>
                  ))}
                </select>
                <select
                  value={previewArchetype}
                  onChange={(e) => setPreviewArchetype(e.target.value)}
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
                >
                  {ARCHETYPES.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.label}
                    </option>
                  ))}
                </select>
                <select
                  value={previewTrigger}
                  onChange={(e) => setPreviewTrigger(e.target.value)}
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
                >
                  <option value="manual">Manual</option>
                  <option value="lineup">Simulate Lineup</option>
                  <option value="halftime">Simulate Half-Time</option>
                  <option value="fulltime">Simulate Full-Time</option>
                </select>
                <button
                  onClick={handleGeneratePreview}
                  disabled={generatingPreview || !auth.geminiPreviewReady}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-[#004d98] to-[#a50044] px-4 py-2 text-xs font-semibold text-white transition hover:opacity-95 disabled:opacity-40"
                >
                  {generatingPreview ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                  Generate Preview
                </button>
              </div>
            </div>

            {previewResult && (
              <div className="mt-4 rounded-xl border border-slate-700 bg-slate-950/80 p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                  <span className="rounded-full bg-[#edbb00]/20 px-2.5 py-0.5 font-semibold text-[#edbb00]">
                    Target: {previewResult.target_club || previewClub}
                  </span>
                  <span
                    className={`rounded-full px-2.5 py-0.5 font-semibold ${
                      previewResult.gemini_key_used === "primary"
                        ? "bg-emerald-500/20 text-emerald-300"
                        : "bg-amber-500/20 text-amber-300"
                    }`}
                  >
                    {previewResult.gemini_key_used === "primary" ? "Gemini Key #1 (Primary)" : "Gemini Key #2 (Fallback)"}
                  </span>
                  <span className="rounded-full bg-slate-800 px-2.5 py-0.5 text-slate-300">{previewResult.gemini_model}</span>
                  <span className="rounded-full bg-slate-800 px-2.5 py-0.5 text-slate-300">
                    {previewResult.text.length}/280 chars
                  </span>
                  {previewResult.selected_image_ids.length > 0 && (
                    <span className="rounded-full bg-sky-500/20 px-2.5 py-0.5 text-sky-300">
                      Images: {previewResult.selected_image_ids.join(", ")}
                    </span>
                  )}
                </div>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-white">{previewResult.text}</p>
              </div>
            )}
          </div>

          {/* Post & API Key Activity Feed */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-bold text-white">Posting Activity by Club & Gemini Key</h2>
                <p className="text-xs text-slate-400">
                  Every post tagged with Target Club, Workflow Slot, Gemini Key, and free inline reach logging
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={clubFilter}
                  onChange={(e) => setClubFilter(e.target.value)}
                  className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200"
                >
                  <option value="all">All Clubs</option>
                  {availableClubNames.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
                {[
                  { id: "all" as const, label: `All (${posts.length})` },
                  { id: "live" as const, label: "Live X" },
                  { id: "match" as const, label: "Matchday / Thu" },
                  { id: "fallback" as const, label: "Fallback Key" },
                  { id: "dry" as const, label: "Dry Runs" },
                ].map((f) => (
                  <button
                    key={f.id}
                    onClick={() => setActivityFilter(f.id)}
                    className={`rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
                      activityFilter === f.id
                        ? "bg-[#004d98] text-white"
                        : "bg-slate-800 text-slate-300 hover:bg-slate-700"
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </div>

            {filteredPosts.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-800 py-12 text-center text-sm text-slate-400">
                No activity entries match this filter yet.
              </div>
            ) : (
              <div className="space-y-3">
                {filteredPosts.map((post) => (
                  <div
                    key={post.id}
                    className="rounded-xl border border-slate-800 bg-slate-950/75 p-4 transition hover:border-slate-700"
                  >
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-semibold">
                        <span className="rounded-full bg-[#edbb00]/15 px-2.5 py-0.5 text-[#edbb00] ring-1 ring-[#edbb00]/30">
                          {post.target_club}
                        </span>

                        <span className="rounded-full bg-[#a50044]/25 px-2.5 py-0.5 text-rose-200">
                          {post.workflow_slot}
                        </span>

                        <span
                          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 ${
                            post.gemini_key_used === "fallback"
                              ? "bg-amber-500/20 text-amber-300 ring-1 ring-amber-500/40"
                              : "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30"
                          }`}
                        >
                          <KeyRound className="h-3 w-3" />
                          {post.gemini_key_used === "fallback" ? "Key #2 FALLBACK" : "Key #1 PRIMARY"}
                        </span>

                        {post.dry_run && (
                          <span className="rounded-full bg-purple-500/20 px-2 py-0.5 text-purple-300">DRY RUN</span>
                        )}
                      </div>

                      <div className="flex items-center gap-3 text-xs text-slate-400">
                        <span title={post.created_at_utc}>{formatRelativeTime(post.created_at_utc)}</span>
                        {!post.dry_run && (
                          <button
                            onClick={() => startEditMetrics(post)}
                            className="inline-flex items-center gap-1 rounded-lg bg-slate-800 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-700"
                          >
                            <Pencil className="h-3 w-3" />
                            Log Reach
                          </button>
                        )}
                        {!post.dry_run && post.tweet_id && post.tweet_id !== "dry-run" && (
                          <a
                            href={`https://x.com/i/web/status/${post.tweet_id}`}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-sky-400 hover:underline"
                          >
                            View on X <ExternalLink className="h-3 w-3" />
                          </a>
                        )}
                      </div>
                    </div>

                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">{post.text}</p>

                    {/* Metrics Display Bar */}
                    {!post.dry_run && (post.impressions > 0 || post.likes > 0 || post.replies > 0) && (
                      <div className="mt-2.5 flex flex-wrap gap-4 text-xs text-slate-300">
                        <span>👁️ <strong>{post.impressions.toLocaleString()}</strong> views</span>
                        <span>❤️ <strong>{post.likes}</strong> likes</span>
                        <span>💬 <strong>{post.replies}</strong> replies</span>
                        <span>🔁 <strong>{post.retweets + post.quotes}</strong> RTs/quotes</span>
                      </div>
                    )}

                    {/* Free Inline Reach Entry Form */}
                    {editingMetricsId === post.id && (
                      <div className="mt-3 flex flex-wrap items-end gap-2 rounded-xl border border-slate-700 bg-slate-900 p-3">
                        {[
                          { k: "impressions" as const, label: "Views" },
                          { k: "likes" as const, label: "Likes" },
                          { k: "replies" as const, label: "Replies" },
                          { k: "retweets" as const, label: "Reposts" },
                          { k: "quotes" as const, label: "Quotes" },
                        ].map((field) => (
                          <div key={field.k}>
                            <label className="mb-0.5 block text-[10px] text-slate-400">{field.label}</label>
                            <input
                              type="number"
                              min={0}
                              value={metricsDraft[field.k]}
                              onChange={(e) => setMetricsDraft({ ...metricsDraft, [field.k]: Number(e.target.value) })}
                              className="w-20 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-white"
                            />
                          </div>
                        ))}
                        <button
                          onClick={() => void handleSaveMetrics(post.id)}
                          disabled={savingMetrics}
                          className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500"
                        >
                          {savingMetrics ? "Saving..." : "Save"}
                        </button>
                        <button
                          onClick={() => setEditingMetricsId(null)}
                          className="rounded-lg bg-slate-800 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-700"
                        >
                          Cancel
                        </button>
                      </div>
                    )}

                    <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-slate-400">
                      <span>Archetype: {post.archetype}</span>
                      {post.match_label && <span>· {post.match_label}</span>}
                      {post.image_ids.length > 0 && (
                        <span className="text-sky-300">· Images: {post.image_ids.join(", ")}</span>
                      )}
                      {post.gemini_fallback_reason && (
                        <span className="text-amber-300">· Primary fail: {post.gemini_fallback_reason}</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ==================== TAB 2: CLUB REACH & ANALYTICS ==================== */}
      {tab === "analytics" && (
        <div className="space-y-6">
          {/* Free API Transparency Notice */}
          <div className="rounded-2xl border border-sky-500/30 bg-sky-500/10 p-4 text-xs text-sky-100">
            <p className="font-bold text-sky-300">Zero-Cost Analytics Mode Active ($0.00 X Read Spend)</p>
            <p className="mt-1 text-sky-200/90">
              X API Pay-Per-Use charges <strong>$0.001 per tweet read</strong> (there is no free metrics-read endpoint),
              so automatic polling is intentionally <strong>OFF</strong> to protect your $10 posting balance. Click{" "}
              <strong>Log Reach</strong> on any live post in the Activity tab to punch in Views / Likes / Replies from
              your X app for free — this leaderboard updates instantly to show which rival club drives the highest reach!
            </p>
          </div>

          {/* Workflow Calendar & Weekly Pace Summary */}
          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
              <h2 className="text-sm font-bold text-white">Weekly Posting Budget (~26 ± 4 / week)</h2>
              <div className="mt-3 flex items-baseline gap-2">
                <span className="text-3xl font-black text-white">{stats.postsLast7Days}</span>
                <span className="text-sm text-slate-400">/ {config.weekly_post_target} posts in last 7 days</span>
              </div>
              <div className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-slate-800">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-[#004d98] to-[#a50044]"
                  style={{ width: `${Math.min(100, Math.round((stats.postsLast7Days / Math.max(1, config.weekly_post_target)) * 100))}%` }}
                />
              </div>
              <p className="mt-2 text-xs text-slate-400">
                Current 7-day daily average: <strong className="text-white">{stats.dailyAvg7d.toFixed(2)} posts/day</strong>{" "}
                (target cap ≤ 3.71/day). Non-matchdays auto-throttle between {config.non_matchday_min_posts} and{" "}
                {config.non_matchday_max_posts} posts/day to stay on pace.
              </p>
            </div>

            <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5 text-xs text-slate-300">
              <h2 className="mb-2 text-sm font-bold text-white">Active Automated Workflow Rules</h2>
              <ul className="space-y-1.5">
                <li>
                  <strong className="text-sky-300">Barça Matchday:</strong> 8h pre-match (1) → Lineup (1) → HT (1) → FT (1 win / 3 draw-loss) = <strong>4 or 6 posts</strong>
                </li>
                <li>
                  <strong className="text-rose-300">Real Madrid Matchday:</strong> Lineup (1) → FT (2 discrediting win OR 2 mocking loss) = <strong>3 posts</strong>
                </li>
                <li>
                  <strong className="text-[#edbb00]">Every Thursday:</strong> Checks Tue/Wed UCL → if EPL lost, fires <strong>1 Intensity League roast</strong>
                </li>
                <li>
                  <strong className="text-emerald-300">Non-Matchdays:</strong> 1–3 posts rotating 1–2 Big-8 clubs/day (Atleti, PSG, Liverpool, Man City, Bayern, Arsenal, Man Utd, Chelsea)
                </li>
              </ul>
            </div>
          </div>

          {/* Club Reach Leaderboard */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
            <h2 className="mb-1 text-base font-bold text-white">Posts & Reach Performance by Target Club</h2>
            <p className="mb-4 text-xs text-slate-400">
              Compare how many posts targeted each club and which rival triggers the highest average impressions & replies
            </p>

            {clubAnalytics.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-800 py-10 text-center text-sm text-slate-400">
                No club data logged yet. Posts published by Heroku (and dry-runs) appear here automatically.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-slate-800 text-slate-400">
                      <th className="pb-2.5 pr-4 font-semibold">Target Club</th>
                      <th className="pb-2.5 pr-4 font-semibold">Live Posts</th>
                      <th className="pb-2.5 pr-4 font-semibold">Dry Runs</th>
                      <th className="pb-2.5 pr-4 font-semibold">Total Views</th>
                      <th className="pb-2.5 pr-4 font-semibold">Avg Views / Post</th>
                      <th className="pb-2.5 pr-4 font-semibold">Likes · Replies · RTs</th>
                      <th className="pb-2.5 font-semibold">Relative Reach</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/70">
                    {clubAnalytics.map((row) => {
                      const barVal = row.avgImpressions > 0 ? row.avgImpressions : row.livePosts;
                      const pct = Math.min(100, Math.round((barVal / maxClubAvgImpressions) * 100));
                      return (
                        <tr key={row.club} className="hover:bg-slate-950/60">
                          <td className="py-3 pr-4 font-bold text-white">
                            <button
                              onClick={() => {
                                setClubFilter(row.club);
                                setTab("activity");
                              }}
                              className="hover:text-[#edbb00] hover:underline"
                            >
                              {row.club}
                            </button>
                          </td>
                          <td className="py-3 pr-4 font-semibold text-emerald-300">{row.livePosts}</td>
                          <td className="py-3 pr-4 text-slate-400">{row.dryPosts}</td>
                          <td className="py-3 pr-4 text-white">{row.totalImpressions.toLocaleString()}</td>
                          <td className="py-3 pr-4 font-bold text-[#edbb00]">
                            {row.measuredPosts > 0 ? row.avgImpressions.toLocaleString() : "—"}
                          </td>
                          <td className="py-3 pr-4 text-slate-300">
                            {row.totalLikes} · {row.totalReplies} · {row.totalRetweets}
                          </td>
                          <td className="w-40 py-3">
                            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-800">
                              <div
                                className="h-full rounded-full bg-gradient-to-r from-[#004d98] to-[#edbb00]"
                                style={{ width: `${Math.max(4, pct)}%` }}
                              />
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Live Match Radar */}
          {(barcaMatches.length > 0 || rivalMatches.length > 0) && (
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-4">
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-sky-400">Barça Match Radar</h3>
                {barcaMatches.length === 0 ? (
                  <p className="text-xs text-slate-400">No Barça match in the immediate 48h window.</p>
                ) : (
                  <div className="space-y-2">
                    {barcaMatches.map((m, idx) => (
                      <div key={idx} className="flex items-center justify-between rounded-xl bg-slate-950/70 px-3 py-2 text-xs">
                        <span className="font-medium text-white">
                          [{m.competition}] {m.homeTeam} {m.homeScore ?? "vs"} {m.awayScore ?? ""} {m.awayTeam}
                        </span>
                        <span className="rounded-md bg-[#004d98]/40 px-2 py-0.5 text-[11px] text-sky-200">{m.detail}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-4">
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-rose-400">Madrid & Big-8 Radar</h3>
                {rivalMatches.length === 0 ? (
                  <p className="text-xs text-slate-400">No tracked rival matches in window.</p>
                ) : (
                  <div className="space-y-2">
                    {rivalMatches.slice(0, 5).map((m, idx) => (
                      <div key={idx} className="flex items-center justify-between rounded-xl bg-slate-950/70 px-3 py-2 text-xs">
                        <span className="text-slate-200">
                          [{m.competition}] {m.homeTeam} {m.homeScore ?? "vs"} {m.awayScore ?? ""} {m.awayTeam}
                        </span>
                        <span className="rounded-md bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">{m.detail}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ==================== TAB 3: POSTING WORKFLOW & BEHAVIOR CONTROLS ==================== */}
      {tab === "controls" && (
        <div className="space-y-6">
          <div className="grid gap-6 lg:grid-cols-2">
            {/* Core Switches & Match Triggers */}
            <div className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
              <h2 className="text-base font-bold text-white">Master Controls & Matchday Triggers</h2>

              {[
                {
                  key: "bot_enabled" as const,
                  title: "Enable Automatic Posting",
                  desc: "Master kill switch. Pause anytime without touching Heroku Scheduler.",
                },
                {
                  key: "dry_run" as const,
                  title: "Dry-Run Mode (No X Credits Used)",
                  desc: "Generates posts & logs them to Activity without actually publishing to X.",
                },
                {
                  key: "auto_post_prematch" as const,
                  title: "Barça Pre-Match (8h) + Lineup (~1h)",
                  desc: "Bragging trash talk 8h before KO & again when official Starting XI drops.",
                },
                {
                  key: "auto_post_halftime" as const,
                  title: "Barça Instant Half-Time Trigger (HT)",
                  desc: "Brags if winning; instant meltdown ('send Gordon back', 'Flick out') if trailing/tied.",
                },
                {
                  key: "auto_post_fulltime" as const,
                  title: "Barça Full-Time Trigger (1 Win / 3 Draw-Loss)",
                  desc: "Pulls live post-match news: 1 win brag or 3 spaced frustration meltdowns.",
                },
                {
                  key: "auto_post_rival_drama" as const,
                  title: "Real Madrid Matchday (Lineup + 2 FT) & Thu UCL",
                  desc: "Mocks Madrid XI + 2 FT posts (discrediting wins or gloating losses).",
                },
              ].map((item) => (
                <label
                  key={item.key}
                  className="flex cursor-pointer items-center justify-between gap-4 rounded-xl border border-slate-800 bg-slate-950/60 p-3.5 hover:border-slate-700"
                >
                  <div>
                    <p className="text-sm font-semibold text-white">{item.title}</p>
                    <p className="text-xs text-slate-400">{item.desc}</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={Boolean(config[item.key])}
                    onChange={(e) => setConfig({ ...config, [item.key]: e.target.checked })}
                    className="h-5 w-5 accent-[#a50044]"
                  />
                </label>
              ))}
            </div>

            {/* Weekly Budget, Chaos & Gemini Tuning */}
            <div className="space-y-5 rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
              <h2 className="text-base font-bold text-white">Weekly Budget, Chaos & Gemini Tuning</h2>

              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Weekly Target</label>
                  <input
                    type="number"
                    min={7}
                    max={70}
                    value={config.weekly_post_target}
                    onChange={(e) => setConfig({ ...config, weekly_post_target: Number(e.target.value) })}
                    className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                  />
                  <p className="mt-0.5 text-[10px] text-slate-500">Default: 26 (≤3.71/d)</p>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Off-Day Min</label>
                  <input
                    type="number"
                    min={0}
                    max={5}
                    value={config.non_matchday_min_posts}
                    onChange={(e) => setConfig({ ...config, non_matchday_min_posts: Number(e.target.value) })}
                    className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Off-Day Max</label>
                  <input
                    type="number"
                    min={1}
                    max={6}
                    value={config.non_matchday_max_posts}
                    onChange={(e) => setConfig({ ...config, non_matchday_max_posts: Number(e.target.value) })}
                    className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Off-Day Cooldown (min)</label>
                  <input
                    type="number"
                    min={30}
                    max={720}
                    value={config.min_minutes_between_posts}
                    onChange={(e) => setConfig({ ...config, min_minutes_between_posts: Number(e.target.value) })}
                    className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Image Attachment Ratio</label>
                  <div className="flex items-center gap-2 pt-2">
                    <input
                      type="range"
                      min={0}
                      max={100}
                      value={Math.round(config.image_post_ratio * 100)}
                      onChange={(e) => setConfig({ ...config, image_post_ratio: Number(e.target.value) / 100 })}
                      className="w-full accent-[#004d98]"
                    />
                    <span className="text-xs font-bold text-[#edbb00]">{Math.round(config.image_post_ratio * 100)}%</span>
                  </div>
                </div>
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">Banter Chaos Level</label>
                <div className="grid grid-cols-3 gap-2">
                  {(["balanced", "spicy", "unhinged"] as const).map((lvl) => (
                    <button
                      key={lvl}
                      type="button"
                      onClick={() => setConfig({ ...config, chaos_level: lvl })}
                      className={`rounded-xl py-2 text-xs font-bold uppercase tracking-wide transition ${
                        config.chaos_level === lvl
                          ? "bg-gradient-to-r from-[#004d98] to-[#a50044] text-white"
                          : "border border-slate-700 bg-slate-950 text-slate-400 hover:text-white"
                      }`}
                    >
                      {lvl}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Primary Gemini Model</label>
                  <select
                    value={config.gemini_model}
                    onChange={(e) => setConfig({ ...config, gemini_model: e.target.value })}
                    className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                  >
                    <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite (Fastest / Best Free Tier)</option>
                    <option value="gemini-4-hydrogen">gemini-4-hydrogen (Auto-falls back on 503)</option>
                  </select>
                </div>

                <label className="flex cursor-pointer items-center justify-between rounded-xl border border-slate-700 bg-slate-950 px-3 py-2">
                  <span className="text-xs font-medium text-slate-200">Google Search Grounding</span>
                  <input
                    type="checkbox"
                    checked={config.enable_google_search}
                    onChange={(e) => setConfig({ ...config, enable_google_search: e.target.checked })}
                    className="h-4 w-4 accent-[#004d98]"
                  />
                </label>
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">
                  Custom Banter Directive (Optional Weekly Agenda)
                </label>
                <textarea
                  rows={3}
                  value={config.custom_directive}
                  onChange={(e) => setConfig({ ...config, custom_directive: e.target.value })}
                  placeholder="e.g. Push Pedri Ballon d'Or agenda and cook Arsenal & Man City extra hard..."
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 p-3 text-sm text-white outline-none focus:border-[#004d98]"
                />
              </div>
            </div>
          </div>

          {/* 24-Hour UTC Posting Slots for Off-Days */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
            <h3 className="text-sm font-bold text-white">Non-Matchday Posting Hours (UTC)</h3>
            <p className="mb-3 text-xs text-slate-400">
              Default <code>06, 11, 16</code> UTC = 11:30 AM, 4:30 PM, 9:30 PM IST. Matchday triggers (8h pre-match,
              lineup, HT, FT) and Thursday UCL fire automatically outside these hours.
            </p>
            <div className="grid grid-cols-6 gap-2 sm:grid-cols-12">
              {Array.from({ length: 24 }, (_, hour) => {
                const selected = config.post_hours_utc.includes(hour);
                return (
                  <button
                    key={hour}
                    type="button"
                    onClick={() => {
                      const next = selected
                        ? config.post_hours_utc.filter((h) => h !== hour)
                        : [...config.post_hours_utc, hour].sort((a, b) => a - b);
                      setConfig({ ...config, post_hours_utc: next });
                    }}
                    className={`rounded-lg py-2 text-xs font-semibold transition ${
                      selected
                        ? "bg-[#004d98] text-white ring-1 ring-sky-400/50"
                        : "bg-slate-950 text-slate-400 hover:bg-slate-800"
                    }`}
                  >
                    {String(hour).padStart(2, "0")}:00
                  </button>
                );
              })}
            </div>
          </div>

          {/* Save Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-800 bg-slate-900/90 p-4">
            <span className="text-xs text-slate-400">
              {settingsUpdatedAt ? `Last saved: ${new Date(settingsUpdatedAt).toLocaleString()}` : "Not yet saved to DB"}
            </span>
            <button
              onClick={() => void handleSaveConfig()}
              disabled={savingConfig || !auth.databaseConfigured}
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-[#004d98] to-[#a50044] px-6 py-2.5 text-sm font-bold text-white shadow-lg transition hover:opacity-95 disabled:opacity-40"
            >
              {savingConfig ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save Behavior Settings
            </button>
          </div>
        </div>
      )}

      {/* ==================== TAB 4: GITHUB IMAGE LIBRARY & UPLOADER ==================== */}
      {tab === "images" && (
        <div className="space-y-6">
          <form
            onSubmit={handleUploadImage}
            className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5 shadow-xl"
          >
            <h2 className="flex items-center gap-2 text-base font-bold text-white">
              <Upload className="h-4 w-4 text-[#edbb00]" />
              Upload Player / Rival Image to GitHub
            </h2>
            <p className="mb-4 text-xs text-slate-400">
              Commits directly to{" "}
              <code className="text-sky-300">
                {githubMeta.repo || "GITHUB_REPO"}/{githubMeta.branch}/assets/images/
              </code>{" "}
              and updates <code>assets/catalog.json</code> — Heroku picks it up automatically with zero redeploy!
            </p>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Category</label>
                <select
                  value={uploadCategory}
                  onChange={(e) => {
                    const cat = e.target.value as "barca" | "rivals" | "managers" | "memes";
                    setUploadCategory(cat);
                    if (cat === "barca") setUploadClub("FC Barcelona");
                    else if (cat === "rivals") setUploadClub("Real Madrid");
                    else if (cat === "managers") setUploadClub("FC Barcelona");
                    else setUploadClub("Football Reaction");
                  }}
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                >
                  <option value="barca">Barça Players (barca/)</option>
                  <option value="rivals">Rival Players (rivals/)</option>
                  <option value="managers">Managers (managers/)</option>
                  <option value="memes">Memes / Ref / Stadium (memes/)</option>
                </select>
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Player / Subject Name *</label>
                <input
                  type="text"
                  value={uploadSubject}
                  onChange={(e) => setUploadSubject(e.target.value)}
                  placeholder="e.g. Lamine Yamal, Vinicius Jr, Haaland"
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                  required
                />
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Club</label>
                <input
                  type="text"
                  value={uploadClub}
                  onChange={(e) => setUploadClub(e.target.value)}
                  placeholder="e.g. FC Barcelona, Arsenal, Real Madrid"
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                />
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Mood / Emotion</label>
                <select
                  value={uploadMood}
                  onChange={(e) => setUploadMood(e.target.value)}
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                >
                  {MOOD_PRESETS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">
                  Custom Description for Gemini (Optional)
                </label>
                <input
                  type="text"
                  value={uploadDescription}
                  onChange={(e) => setUploadDescription(e.target.value)}
                  placeholder="e.g. Haaland frustrated after City dropped points in Europe"
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                />
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Image File (JPG / PNG / WebP, max 4.8 MB)</label>
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-300 file:mr-3 file:rounded-lg file:border-0 file:bg-[#004d98] file:px-3 file:py-1 file:text-xs file:font-semibold file:text-white"
                  required
                />
              </div>
            </div>

            <div className="mt-4 flex justify-end">
              <button
                type="submit"
                disabled={uploading || !githubMeta.configured}
                className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-[#004d98] to-[#a50044] px-5 py-2.5 text-sm font-bold text-white shadow-lg transition hover:opacity-95 disabled:opacity-40"
              >
                {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                {uploading ? "Committing to GitHub..." : "Upload to GitHub"}
              </button>
            </div>
          </form>

          <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-base font-bold text-white">Stored GitHub Images ({filteredImages.length})</h3>
              <div className="flex flex-wrap gap-1.5">
                {["all", "barca", "rivals", "managers", "memes"].map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setImageFilterCat(cat)}
                    className={`rounded-lg px-2.5 py-1 text-xs font-semibold capitalize ${
                      imageFilterCat === cat ? "bg-[#a50044] text-white" : "bg-slate-800 text-slate-300 hover:bg-slate-700"
                    }`}
                  >
                    {cat}
                  </button>
                ))}
              </div>
            </div>

            {filteredImages.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-800 py-12 text-center text-sm text-slate-400">
                No images in this category yet.
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {filteredImages.map((img) => (
                  <div
                    key={img.id}
                    className="group overflow-hidden rounded-xl border border-slate-800 bg-slate-950/80 transition hover:border-slate-600"
                  >
                    <div className="relative aspect-[4/3] w-full overflow-hidden bg-slate-900">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={`/api/images/raw?path=${encodeURIComponent(img.repo_path)}`}
                        alt={img.description}
                        className="h-full w-full object-cover transition duration-300 group-hover:scale-105"
                      />
                      <span className="absolute left-2 top-2 rounded-md bg-slate-950/80 px-2 py-0.5 text-[10px] font-bold uppercase text-sky-300">
                        {img.category}
                      </span>
                    </div>
                    <div className="p-3">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <p className="text-sm font-bold text-white">{img.subject}</p>
                          <p className="text-[11px] text-slate-400">
                            {img.club} · <span className="text-[#edbb00]">{img.mood}</span>
                          </p>
                        </div>
                        <button
                          onClick={() => void handleDeleteImage(img.relative_path)}
                          disabled={deletingPath === img.relative_path}
                          title="Delete from GitHub"
                          className="rounded-lg p-1.5 text-slate-400 transition hover:bg-rose-500/20 hover:text-rose-300"
                        >
                          {deletingPath === img.relative_path ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Trash2 className="h-3.5 w-3.5" />
                          )}
                        </button>
                      </div>
                      <p className="mt-1.5 truncate font-mono text-[10px] text-slate-500" title={img.id}>
                        id: {img.id}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
