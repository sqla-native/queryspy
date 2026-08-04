'use strict';

const fs = require('node:fs');

const MARKER = '<!-- queryspy-quality-report -->';

function readJson(path) {
  try {
    return JSON.parse(fs.readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
}

function readNumber(path) {
  try {
    return Number(fs.readFileSync(path, 'utf8').trim());
  } catch {
    return null;
  }
}

function delta(current, base, suffix = '') {
  if (base === null || base === undefined || Number.isNaN(base)) return '';
  const diff = current - base;
  if (Math.abs(diff) < 1e-9) return ' (no change)';
  const sign = diff > 0 ? '+' : '';
  return ` (${sign}${Number(diff.toFixed(2))}${suffix})`;
}

/** complexipy JSON is a flat list of functions; we only care about the worst. */
function worstComplexity(report) {
  if (!Array.isArray(report)) return null;
  return report.reduce((max, entry) => {
    const score = Number(entry.complexity ?? entry.cognitive_complexity ?? 0);
    return Number.isFinite(score) && score > max ? score : max;
  }, 0);
}

module.exports = async ({ github, context }) => {
  const coverage = readJson('coverage.json');
  const complexity = readJson('complexity.json');
  const durationMs = readNumber('test-duration-ms.txt');

  const baseCoverage = readJson('ci-data/coverage.json');
  const baseComplexity = readJson('ci-data/complexity.json');
  const baseDuration = readNumber('ci-data/test-duration-ms.txt');

  const rows = [];

  if (coverage) {
    const pct = coverage.totals.percent_covered;
    const basePct = baseCoverage ? baseCoverage.totals.percent_covered : null;
    rows.push([
      'Coverage',
      `${pct.toFixed(2)}%${delta(pct, basePct, '%')}`,
      pct === 100 ? '✅' : '❌',
    ]);
    rows.push([
      'Branches',
      `${coverage.totals.covered_branches}/${coverage.totals.num_branches}`,
      coverage.totals.num_partial_branches === 0 ? '✅' : '❌',
    ]);
  }

  const worst = worstComplexity(complexity);
  if (worst !== null) {
    const baseWorst = worstComplexity(baseComplexity);
    rows.push([
      'Worst cognitive complexity',
      `${worst} / 15${delta(worst, baseWorst)}`,
      worst <= 15 ? '✅' : '❌',
    ]);
  }

  if (durationMs !== null) {
    rows.push([
      'Test suite duration',
      `${(durationMs / 1000).toFixed(2)}s${delta(durationMs / 1000, baseDuration === null ? null : baseDuration / 1000, 's')}`,
      '',
    ]);
  }

  const hasBaseline = Boolean(baseCoverage || baseComplexity);
  const body = [
    MARKER,
    '### Package quality',
    '',
    '| Metric | Value | |',
    '| :--- | ---: | :-: |',
    ...rows.map(([name, value, status]) => `| ${name} | ${value} | ${status} |`),
    '',
    hasBaseline
      ? '_Deltas are against the base branch._'
      : '_No base-branch metrics cached yet; reporting without a baseline._',
  ].join('\n');

  const { owner, repo } = context.repo;
  const issue_number = context.payload.pull_request.number;

  const existing = await github.rest.issues.listComments({ owner, repo, issue_number });
  const previous = existing.data.find((comment) => comment.body.includes(MARKER));

  if (previous) {
    await github.rest.issues.updateComment({ owner, repo, comment_id: previous.id, body });
  } else {
    await github.rest.issues.createComment({ owner, repo, issue_number, body });
  }
};
