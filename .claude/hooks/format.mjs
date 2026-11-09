// Claude Code가 파일을 수정한 뒤 해당 파일에만 Prettier를 적용합니다.
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import * as prettier from "prettier";

const projectDir = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();

async function readStdin() {
  const chunks = [];

  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }

  return Buffer.concat(chunks).toString("utf8");
}

async function main() {
  const input = JSON.parse(await readStdin());
  const filePath = input.tool_response?.filePath ?? input.tool_input?.file_path;

  if (!filePath) {
    return;
  }

  const absolutePath = path.resolve(projectDir, filePath);
  const relativePath = path.relative(projectDir, absolutePath);

  if (relativePath.startsWith("..") || path.isAbsolute(relativePath)) {
    return;
  }

  const fileInfo = await prettier.getFileInfo(absolutePath, {
    ignorePath: path.join(projectDir, ".prettierignore"),
    resolveConfig: true,
  });

  if (fileInfo.ignored || !fileInfo.inferredParser) {
    return;
  }

  const config = (await prettier.resolveConfig(absolutePath)) ?? {};
  const source = await readFile(absolutePath, "utf8");
  const formatted = await prettier.format(source, {
    ...config,
    filepath: absolutePath,
  });

  if (formatted !== source) {
    await writeFile(absolutePath, formatted, "utf8");
  }
}

main().catch((error) => {
  console.error(`Prettier hook 실패: ${error.message}`);
  process.exit(1);
});
