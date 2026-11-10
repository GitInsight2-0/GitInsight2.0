// 계약 스키마와 TS 타입을 생성하거나, 커밋된 파일과 같은지 검사합니다.
// 사용: node scripts/contracts.mjs gen | check
// Windows와 CI에서 똑같이 동작하도록 셸 문법 대신 Node로 작성했습니다.
import { spawnSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { compileFromFile } from "json-schema-to-typescript";

const root = resolve(import.meta.dirname, "..");
const schemaPath = resolve(root, "contracts/schema/contracts.json");
const typesPath = resolve(root, "contracts/ts/index.d.ts");

function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, stdio: "inherit", shell: false });
  if (result.error) {
    throw result.error;
  }
  return result.status ?? 1;
}

async function gen() {
  const status = run("uv", ["run", "python", "-m", "gitinsight_contracts.schema_export", schemaPath]);
  if (status !== 0) {
    process.exit(status);
  }
  const ts = await compileFromFile(schemaPath, {
    cwd: resolve(root, "contracts/schema"),
    unreachableDefinitions: true,
    // 길이 제한은 Pydantic이 검사합니다. TS에서는 튜플 대신 일반 배열로 둡니다.
    ignoreMinAndMaxItems: true,
    additionalProperties: false,
    bannerComment:
      "/* 자동 생성 파일입니다. 직접 고치지 말고 contracts/src를 고친 뒤 `pnpm contracts:gen`을 실행하세요. */",
    style: { singleQuote: false, semi: true, printWidth: 100, endOfLine: "lf" },
  });
  writeFileSync(typesPath, ts, { encoding: "utf8" });
  console.log(`생성: ${schemaPath}`);
  console.log(`생성: ${typesPath}`);
}

const command = process.argv[2];
if (command === "gen") {
  await gen();
} else if (command === "check") {
  await gen();
  const status = run("git", ["diff", "--exit-code", "--", schemaPath, typesPath]);
  if (status !== 0) {
    console.error("계약 생성 파일이 커밋된 내용과 다릅니다. `pnpm contracts:gen` 후 결과를 커밋하세요.");
    process.exit(status);
  }
  console.log("계약 생성 파일이 최신입니다.");
} else {
  console.error("사용법: node scripts/contracts.mjs gen | check");
  process.exit(2);
}
