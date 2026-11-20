/**
 * 시선 분석 프로그램(wasm)을 web/public/mediapipe/wasm으로 복사합니다 (#181).
 * 외부 CDN에서 받지 않고 우리 웹이 같은 출처에서 내려 주기 위해서입니다.
 * 설치된 @mediapipe/tasks-vision 패키지에서 복사하므로 버전이 늘 맞습니다. 복사한 파일은 커밋하지 않습니다(.gitignore).
 * dev와 build 전에 실행합니다 (package.json).
 */
import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const pkgDir = dirname(require.resolve("@mediapipe/tasks-vision"));
const from = join(pkgDir, "wasm");
const to = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "mediapipe", "wasm");

if (!existsSync(from)) {
  console.error(`시선 분석 wasm을 찾지 못했습니다: ${from}`);
  process.exit(1);
}
rmSync(to, { recursive: true, force: true });
mkdirSync(to, { recursive: true });
cpSync(from, to, { recursive: true });
console.log(`시선 분석 wasm 복사: ${to}`);
