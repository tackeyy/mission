const FAST_PATH_TARGETS = [
  "skills/mission/tests/test_artifact_hygiene.py",
  "skills/mission/tests/test_vendor_fingerprint.py",
  "skills/mission/tests/test_private_project_names.py",
  "skills/mission/tests/test_plugins_in_sync.py",
  "skills/mission/tests/test_codex_wrapper_sync.py",
  "skills/mission/tests/test_actions_cost_guard.py",
  "skills/mission/tests/test_doc_consistency.py",
].join(" ");

const FULL_PATH_TARGETS = "skills/mission";

// fail-safe: docs 配下や results/artifacts 配下でも、コードになり得る拡張子は docs-only 扱いしない
const DOCS_SAFE_EXTENSIONS = [".md", ".txt", ".rst", ".json", ".jsonl", ".patch", ".svg", ".png", ".jpg", ".jpeg", ".gif"];

function hasDocsSafeExtension(file) {
  return DOCS_SAFE_EXTENSIONS.some((ext) => file.endsWith(ext));
}

function isDocsOnlyFile(file) {
  if (file.endsWith(".md")) {
    return true;
  }
  if (!hasDocsSafeExtension(file)) {
    return false;
  }
  return (
    file.startsWith("docs/") ||
    file.startsWith("benchmarks/mission-vs-goal/results/") ||
    file.startsWith("benchmarks/mission-vs-goal/artifacts/")
  );
}

function classifyChangedFiles({ eventName, files }) {
  const safeFiles = Array.isArray(files) ? files : [];
  const failSafeFull = eventName !== "pull_request" || safeFiles.length === 0;
  const docsOnly = !failSafeFull && safeFiles.every(isDocsOnlyFile);

  return {
    python: true,
    pythonTargets: docsOnly ? FAST_PATH_TARGETS : FULL_PATH_TARGETS,
    docsOnly,
    runAll: failSafeFull,
  };
}

module.exports = {
  FAST_PATH_TARGETS,
  FULL_PATH_TARGETS,
  classifyChangedFiles,
  isDocsOnlyFile,
};
