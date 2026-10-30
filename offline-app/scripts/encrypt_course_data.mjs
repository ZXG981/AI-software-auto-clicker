import { webcrypto } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

if (!globalThis.crypto) globalThis.crypto = webcrypto;

const appRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const library = JSON.parse(
  await readFile(join(appRoot, "..", "data", "library.json"), "utf8"),
);
const courseData = {
  agents: {
    cet4: "四级阅读与写作",
    cet6: "六级阅读与写作",
    postgrad: "考研阅读与写作",
  },
  profiles: library.profiles || {},
  sources: (library.sources || []).map(source => ({
    title: source.title || "",
    agent: source.agent || "",
    chunks: source.chunks || [],
  })),
};
const passphrase = process.env.COURSE_DATA_PASSPHRASE || "";
if (passphrase.length < 12) {
  throw new Error("Set COURSE_DATA_PASSPHRASE to at least 12 characters.");
}

const salt = crypto.getRandomValues(new Uint8Array(16));
const nonce = crypto.getRandomValues(new Uint8Array(12));
const iterations = 250_000;
const keyMaterial = await crypto.subtle.importKey(
  "raw",
  new TextEncoder().encode(passphrase),
  "PBKDF2",
  false,
  ["deriveKey"],
);
const key = await crypto.subtle.deriveKey(
  { name: "PBKDF2", salt, iterations, hash: "SHA-256" },
  keyMaterial,
  { name: "AES-GCM", length: 256 },
  false,
  ["encrypt"],
);
const ciphertext = await crypto.subtle.encrypt(
  { name: "AES-GCM", iv: nonce },
  key,
  new TextEncoder().encode(JSON.stringify(courseData)),
);
const base64 = bytes => Buffer.from(bytes).toString("base64");
const encrypted = {
  version: 1,
  iterations,
  salt: base64(salt),
  nonce: base64(nonce),
  ciphertext: base64(new Uint8Array(ciphertext)),
};
const target = join(appRoot, "public", "course-data.enc.json");
await writeFile(target, JSON.stringify(encrypted), "utf8");
console.log(`Encrypted ${courseData.sources.length} sources to ${target}`);
