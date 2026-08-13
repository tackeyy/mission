const FAST_PATH_TARGETS = [
  "skills/mission/tests/test_artifact_hygiene.py",
  "skills/mission/tests/test_vendor_fingerprint.py",
  "skills/mission/tests/test_plugins_in_sync.py",
  "skills/mission/tests/test_actions_cost_guard.py",
  "skills/mission/tests/test_doc_consistency.py",
].join(" ");

const FULL_PATH_TARGETS = "skills/mission";

function isDocsOnlyFile(file) {
  return (
    file.startsWith("docs/") ||
    file.startsWith("benchmarks/mission-vs-goal/results/") ||
    file.startsWith("benchmarks/mission-vs-goal/artifacts/") ||
    file.endsWith(".md")
  );
}

function classifyChangedFiles({ eventName, files }) {
  const safeFiles = Array.isArray(files) ? files : [];
  const failSafeFull = eventName !== "pull_request" || safeFiles.length === 0;
  const docsOnly = !failSafeFull && safeFiles.every(isDocsOnlyFile);
  const shell = failSafeFull || safeFiles.some((file) => file.startsWith(".github/workflows/") || file === ".github/dependabot.yml" || file.endsWith(".sh"));

  return {
    python: true,
    pythonTargets: docsOnly ? FAST_PATH_TARGETS : FULL_PATH_TARGETS,
    shell,
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
