export interface CatalogImage {
  id: string;
  relative_path: string;
  repo_path: string;
  category: "barca" | "rivals" | "managers" | "memes";
  subject: string;
  club: string;
  mood: string;
  description: string;
  tags: string[];
  sha?: string;
}

const SUPPORTED_EXT = /\.(jpe?g|png|webp)$/i;
const VALID_CATEGORIES = new Set(["barca", "rivals", "managers", "memes"]);

export function isGitHubConfigured(): boolean {
  return Boolean(process.env.GITHUB_REPO?.trim() && process.env.GITHUB_TOKEN?.trim());
}

function getRepoConfig() {
  const repo = process.env.GITHUB_REPO?.trim() || "";
  const branch = process.env.GITHUB_BRANCH?.trim() || "main";
  const token = process.env.GITHUB_TOKEN?.trim() || "";
  const prefix = (process.env.GITHUB_IMAGES_PREFIX?.trim() || "assets/images").replace(/^\/+|\/+$/g, "");
  return { repo, branch, token, prefix };
}

function ghHeaders(token: string, extra?: Record<string, string>): HeadersInit {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "User-Agent": "BarcaXAdmin/1.0",
    "X-GitHub-Api-Version": "2022-11-28",
    ...extra,
  };
}

function slugify(val: string): string {
  return val
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

async function fetchCatalogJson(): Promise<{
  sha: string | null;
  images: Record<string, Partial<CatalogImage>>;
}> {
  const { repo, branch, token } = getRepoConfig();
  const res = await fetch(
    `https://api.github.com/repos/${repo}/contents/assets/catalog.json?ref=${encodeURIComponent(branch)}`,
    { headers: ghHeaders(token), cache: "no-store" }
  );
  if (res.status === 404) return { sha: null, images: {} };
  if (!res.ok) {
    throw new Error(`GitHub catalog.json read failed (${res.status})`);
  }
  const json = (await res.json()) as { sha?: string; content?: string };
  const decoded = Buffer.from(json.content || "", "base64").toString("utf-8");
  const parsed = JSON.parse(decoded) as { images?: Partial<CatalogImage>[] };
  const map: Record<string, Partial<CatalogImage>> = {};
  for (const item of parsed.images || []) {
    if (item.relative_path) map[item.relative_path] = item;
  }
  return { sha: json.sha || null, images: map };
}

async function writeCatalogJson(
  images: CatalogImage[],
  previousSha: string | null,
  commitMessage: string
): Promise<void> {
  const { repo, branch, token } = getRepoConfig();
  const cleanPayload = {
    note: "Managed by Barca X Admin UI & barca-x-bot. Synced live with GitHub.",
    images: images.map((img) => ({
      id: img.id,
      relative_path: img.relative_path,
      category: img.category,
      subject: img.subject,
      club: img.club,
      mood: img.mood,
      description: img.description,
      tags: img.tags,
    })),
  };

  const body: Record<string, unknown> = {
    message: commitMessage,
    content: Buffer.from(JSON.stringify(cleanPayload, null, 2), "utf-8").toString("base64"),
    branch,
  };
  if (previousSha) body.sha = previousSha;

  const res = await fetch(`https://api.github.com/repos/${repo}/contents/assets/catalog.json`, {
    method: "PUT",
    headers: ghHeaders(token, { "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`GitHub catalog.json update failed (${res.status}): ${errText.slice(0, 200)}`);
  }
}

export async function listGitHubImages(): Promise<CatalogImage[]> {
  if (!isGitHubConfigured()) return [];
  const { repo, branch, token, prefix } = getRepoConfig();

  const [treeRes, catalogData] = await Promise.all([
    fetch(`https://api.github.com/repos/${repo}/git/trees/${encodeURIComponent(branch)}?recursive=1`, {
      headers: ghHeaders(token),
      cache: "no-store",
    }),
    fetchCatalogJson().catch(() => ({ sha: null, images: {} as Record<string, Partial<CatalogImage>> })),
  ]);

  if (!treeRes.ok) {
    throw new Error(`GitHub tree list failed (${treeRes.status}). Check GITHUB_REPO, GITHUB_BRANCH, and GITHUB_TOKEN.`);
  }
  const treeJson = (await treeRes.json()) as {
    tree?: Array<{ path: string; type: string; sha: string }>;
  };

  const results: CatalogImage[] = [];
  const prefixSlash = `${prefix}/`;

  for (const node of treeJson.tree || []) {
    if (node.type !== "blob") continue;
    if (!node.path.startsWith(prefixSlash)) continue;
    const relativePath = node.path.slice(prefixSlash.length);
    if (!SUPPORTED_EXT.test(relativePath)) continue;

    const parts = relativePath.split("/");
    const rawCat = parts.length > 1 ? parts[0].toLowerCase() : "memes";
    const category = (VALID_CATEGORIES.has(rawCat) ? rawCat : "memes") as CatalogImage["category"];
    const filename = parts[parts.length - 1];
    const stem = filename.replace(SUPPORTED_EXT, "");

    const override = catalogData.images[relativePath];
    const defaultSubject = stem
      .split(/[_\-\s]+/)
      .filter(Boolean)
      .map((w) => w[0].toUpperCase() + w.slice(1))
      .join(" ");

    const id = override?.id || slugify(`${category}_${stem}`);
    const subject = override?.subject || defaultSubject;
    const club = override?.club || (category === "barca" ? "FC Barcelona" : category === "rivals" ? "Rival Club" : "Football");
    const mood = override?.mood || "neutral";
    const description = override?.description || `${subject} (${club}) — mood/pose: ${mood}`;
    const tags = Array.isArray(override?.tags) ? override.tags : [category, slugify(subject), slugify(mood)];

    results.push({
      id,
      relative_path: relativePath,
      repo_path: node.path,
      category,
      subject,
      club,
      mood,
      description,
      tags,
      sha: node.sha,
    });
  }

  return results.sort((a, b) => a.relative_path.localeCompare(b.relative_path));
}

export async function uploadImageToGitHub(params: {
  buffer: Buffer;
  originalName: string;
  category: CatalogImage["category"];
  subject: string;
  club: string;
  mood: string;
  description?: string;
}): Promise<CatalogImage> {
  if (!isGitHubConfigured()) {
    throw new Error("GITHUB_REPO and GITHUB_TOKEN must be configured to upload images.");
  }
  const { repo, branch, token, prefix } = getRepoConfig();

  const extMatch = params.originalName.toLowerCase().match(SUPPORTED_EXT);
  const ext = extMatch ? extMatch[0].replace(".jpeg", ".jpg") : ".jpg";
  const subjectSlug = slugify(params.subject) || "player";
  const moodSlug = slugify(params.mood) || "reaction";
  const filename = `${subjectSlug}_${moodSlug}${ext}`;
  const relativePath = `${params.category}/${filename}`;
  const repoPath = `${prefix}/${relativePath}`;

  // Check if file already exists to include its sha for overwrite
  let existingSha: string | undefined;
  const checkRes = await fetch(
    `https://api.github.com/repos/${repo}/contents/${repoPath}?ref=${encodeURIComponent(branch)}`,
    { headers: ghHeaders(token), cache: "no-store" }
  );
  if (checkRes.ok) {
    const existing = (await checkRes.json()) as { sha?: string };
    existingSha = existing.sha;
  }

  const putBody: Record<string, unknown> = {
    message: `Add image ${relativePath} via Barca X Admin`,
    content: params.buffer.toString("base64"),
    branch,
  };
  if (existingSha) putBody.sha = existingSha;

  const putRes = await fetch(`https://api.github.com/repos/${repo}/contents/${repoPath}`, {
    method: "PUT",
    headers: ghHeaders(token, { "Content-Type": "application/json" }),
    body: JSON.stringify(putBody),
  });
  if (!putRes.ok) {
    const errText = await putRes.text();
    throw new Error(`GitHub image upload failed (${putRes.status}): ${errText.slice(0, 220)}`);
  }
  const putData = (await putRes.json()) as { content?: { sha?: string } };

  const newAsset: CatalogImage = {
    id: slugify(`${params.category}_${subjectSlug}_${moodSlug}`),
    relative_path: relativePath,
    repo_path: repoPath,
    category: params.category,
    subject: params.subject.trim(),
    club: params.club.trim() || (params.category === "barca" ? "FC Barcelona" : "Rival Club"),
    mood: params.mood.trim() || "neutral",
    description:
      params.description?.trim() ||
      `${params.subject.trim()} (${params.club.trim() || "Football"}) — mood/pose: ${params.mood.trim() || "neutral"}`,
    tags: Array.from(
      new Set([params.category, slugify(params.club), slugify(params.subject), slugify(params.mood)].filter(Boolean))
    ),
    sha: putData.content?.sha,
  };

  // Sync assets/catalog.json in GitHub so Heroku immediately sees exact metadata
  const currentImages = await listGitHubImages();
  const merged = currentImages.filter((i) => i.relative_path !== relativePath);
  merged.push(newAsset);
  merged.sort((a, b) => a.relative_path.localeCompare(b.relative_path));
  const catalogMeta = await fetchCatalogJson().catch(() => ({ sha: null, images: {} }));
  await writeCatalogJson(merged, catalogMeta.sha, `Update catalog.json for ${relativePath}`);

  return newAsset;
}

export async function deleteImageFromGitHub(relativePath: string): Promise<void> {
  if (!isGitHubConfigured()) {
    throw new Error("GITHUB_REPO and GITHUB_TOKEN must be configured.");
  }
  const { repo, branch, token, prefix } = getRepoConfig();
  const cleanRel = relativePath.replace(/^\/+/, "");
  if (cleanRel.includes("..")) throw new Error("Invalid path");
  const repoPath = `${prefix}/${cleanRel}`;

  const getRes = await fetch(
    `https://api.github.com/repos/${repo}/contents/${repoPath}?ref=${encodeURIComponent(branch)}`,
    { headers: ghHeaders(token), cache: "no-store" }
  );
  if (!getRes.ok) {
    throw new Error(`Could not locate ${repoPath} on GitHub (${getRes.status}).`);
  }
  const fileInfo = (await getRes.json()) as { sha: string };

  const delRes = await fetch(`https://api.github.com/repos/${repo}/contents/${repoPath}`, {
    method: "DELETE",
    headers: ghHeaders(token, { "Content-Type": "application/json" }),
    body: JSON.stringify({
      message: `Delete image ${cleanRel} via Barca X Admin`,
      sha: fileInfo.sha,
      branch,
    }),
  });
  if (!delRes.ok) {
    const errText = await delRes.text();
    throw new Error(`GitHub delete failed (${delRes.status}): ${errText.slice(0, 200)}`);
  }

  // Remove from assets/catalog.json too
  const currentImages = await listGitHubImages();
  const remaining = currentImages.filter((i) => i.relative_path !== cleanRel);
  const catalogMeta = await fetchCatalogJson().catch(() => ({ sha: null, images: {} }));
  await writeCatalogJson(remaining, catalogMeta.sha, `Remove ${cleanRel} from catalog.json`);
}

export async function fetchRawGitHubImage(repoPath: string): Promise<{ bytes: ArrayBuffer; contentType: string }> {
  const { repo, branch, token, prefix } = getRepoConfig();
  if (!repoPath.startsWith(`${prefix}/`) || repoPath.includes("..")) {
    throw new Error("Forbidden image path");
  }
  const res = await fetch(
    `https://api.github.com/repos/${repo}/contents/${repoPath}?ref=${encodeURIComponent(branch)}`,
    {
      headers: ghHeaders(token, { Accept: "application/vnd.github.raw+json" }),
      cache: "force-cache",
    }
  );
  if (!res.ok) throw new Error(`GitHub raw fetch failed (${res.status})`);
  const lower = repoPath.toLowerCase();
  const contentType = lower.endsWith(".png")
    ? "image/png"
    : lower.endsWith(".webp")
    ? "image/webp"
    : "image/jpeg";
  return { bytes: await res.arrayBuffer(), contentType };
}
