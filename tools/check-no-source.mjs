// Copyright firewall: fail the build if any copyrighted source artifact (epub/audio)
// is committed, or if the published incipits exceed the short-locator word cap.
import { execSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";

const BANNED_EXT = [".epub", ".m4b", ".m4a", ".mp3", ".aac", ".wav", ".flac"];
const INCIPIT_WORD_CAP = 8;

function trackedFiles() {
  try {
    return execSync("git ls-files", { encoding: "utf8" })
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  } catch {
    return null; // git unavailable; skip the tracked-file check
  }
}

let failures = [];

const files = trackedFiles();
if (files) {
  const banned = files.filter((f) =>
    BANNED_EXT.some((ext) => f.toLowerCase().endsWith(ext))
  );
  if (banned.length) {
    failures.push(
      `Copyrighted source files are committed (must be gitignored):\n  ` +
        banned.join("\n  ")
    );
  }
}

const dataPath = "src/_data/landmarks.json";
if (existsSync(dataPath)) {
  const data = JSON.parse(readFileSync(dataPath, "utf8"));
  const tooLong = [];
  for (const lm of data.landmarks ?? []) {
    for (const key of ["ml_incipit", "dv_incipit"]) {
      const n = String(lm[key] ?? "").trim().split(/\s+/).filter(Boolean).length;
      if (n > INCIPIT_WORD_CAP) tooLong.push(`#${lm.n} ${key} = ${n} words`);
    }
  }
  if (tooLong.length) {
    failures.push(
      `Incipits exceed the ${INCIPIT_WORD_CAP}-word locator cap:\n  ` +
        tooLong.join("\n  ")
    );
  }
} else {
  failures.push(`Missing ${dataPath} \u2014 run the build pipeline first.`);
}

if (failures.length) {
  console.error("\u274c Copyright firewall failed:\n\n" + failures.join("\n\n"));
  process.exit(1);
}
console.log("\u2705 Copyright firewall passed.");
