(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("history-data").textContent);
  const $ = (id) => document.getElementById(id);
  const escape = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
    );
  const title = (value) =>
    ({
      allstar: "All-Star",
      jack_and_jill: "Jack & Jill",
      pro_am: "Pro-Am",
      none: "Open / other",
      prelim: "Preliminary",
      semifinal: "Semifinal",
      final: "Final",
      quarterfinal: "Quarterfinal",
      alt1: "Alternate 1",
      alt2: "Alternate 2",
      alt3: "Alternate 3",
    })[value] ||
    String(value || "Not recorded")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  const ordinal = (number) =>
    `${number}${number % 100 >= 11 && number % 100 <= 13 ? "th" : { 1: "st", 2: "nd", 3: "rd" }[number % 10] || "th"}`;
  const monthName = (date) =>
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][
      Number(date.slice(5, 7)) - 1
    ];
  const prettyMonth = (date) => `${monthName(date)} ${date.slice(0, 4)}`;
  const sourceLink = (url, label) => {
    try {
      const parsed = new URL(url);
      return ["https:", "http:"].includes(parsed.protocol)
        ? `<a class="source-link" href="${escape(parsed.href)}" target="_blank" rel="noopener noreferrer">${escape(label)} ↗</a>`
        : "Not recorded";
    } catch {
      return "Not recorded";
    }
  };
  const groupBy = (items, key) => {
    const groups = new Map();
    for (const item of items) {
      const id = key(item);
      if (!groups.has(id)) groups.set(id, []);
      groups.get(id).push(item);
    }
    return groups;
  };
  const notes = {
    participation:
      "All recorded participation, including prelims, semifinals, Strictly, and Pro-Am. Matching registry and sheet records are grouped for reading; sheet identities remain unconfirmed. No registry match does not mean zero points.",
    registry:
      "Confirmed registry identity · WSDC #7849. “Finalist” means the registry does not give an exact place. Open any result for its points and source.",
    sheets:
      "Name matches · These printed names have not been confirmed as WSDC #7849 in this release. Open a result to see partners, rounds, and judge marks. Some results also appear in the registry.",
    judging:
      "Name matches · These judging appearances have not been confirmed as WSDC #7849 in this release. Round counts cover only rounds with recorded marks.",
  };
  const judgingMarks = groupBy(
    [
      ...data.callback_marks_given.map((row) => ({
        ...row,
        kind: "Callback",
        value: row.mark_raw || title(row.mark),
        subject: row.entry_id,
      })),
      ...data.final_marks_given.map((row) => ({
        ...row,
        kind: "Final rank",
        value: row.rank,
        subject: row.placement_id,
      })),
    ],
    (row) => row.judge_id,
  );
  const records = {
    registry: data.registry_results.map((r) => ({
      raw: r,
      date: r.event_month.slice(0, 7),
      event: r.series_name_raw,
      division: r.division,
      role: r.role,
      place: r.result === "F" ? null : Number(r.result),
      points: r.points,
      partner: "",
      result: r.result === "F" ? "Finalist" : ordinal(Number(r.result)),
    })),
    sheets: data.score_sheet_results.map((r) => ({
      raw: r,
      date: r.event_month.slice(0, 7),
      event: r.event_name,
      division: r.age_division !== "none" ? r.age_division : r.division,
      role: r.role,
      place: r.place,
      partner: r.partner_name_raw || (r.role === "couple" ? r.name_raw : ""),
      result: r.place !== null ? ordinal(r.place) : title(r.best_round),
    })),
    judging: data.judging_appearances.map((r) => {
      const marks = judgingMarks.get(r.judge_id) || [];
      return {
        raw: r,
        date: r.event_month.slice(0, 7),
        event: r.event_name,
        division: "",
        role: "",
        place: null,
        partner: "",
        marks,
        roundCount: new Set(marks.map((m) => m.round_id)).size,
      };
    }),
  };
  const groupedRegistry = new Set();
  // Use the benchmark's placement-independent correspondence. A disagreement
  // remains one appearance with both records visible instead of two entries.
  const registryByKey = new Map(
    records.registry.map((registry) => [
      JSON.stringify({
        wsdc_id: "7849",
        role: registry.role,
        series_id: registry.raw.series_id,
        event_month: registry.raw.event_month,
        division: registry.division,
        dance_style: registry.raw.dance_style,
      }),
      registry,
    ]),
  );
  const jesEntryMatches = new Map();
  for (const row of data.jes_test?.rows || []) {
    if (row.coverage_status === "covered" && row.entry_ids.length === 1) {
      jesEntryMatches.set(row.entry_ids[0], registryByKey.get(row.registry_key) || null);
    }
  }
  records.participation = records.sheets.map((sheet) => {
    const candidate = jesEntryMatches.get(sheet.raw.entry_id) || null;
    const registry = candidate && !groupedRegistry.has(candidate) ? candidate : null;
    if (registry) groupedRegistry.add(registry);
    sheet.registryMatch = registry;
    return { ...sheet, sheet: true, registry, points: registry?.points ?? null };
  });
  records.participation.push(
    ...records.registry
      .filter((r) => !groupedRegistry.has(r))
      .map((r) => ({ ...r, sheet: false, registry: r })),
  );
  let view = "participation";
  let visible = [];
  const callbacks = groupBy(data.callbacks, (r) => r.entry_id);
  const callbackMarks = groupBy(data.callback_marks_received, (r) => r.entry_id);
  const finalMarks = groupBy(data.final_marks_received, (r) => r.placement_id);
  const years = Array.from({ length: 17 }, (_, i) => String(2010 + i));
  $("total-results").textContent = records.participation.length;
  $("participation-tab-count").textContent = records.participation.length;
  $("total-events").textContent = records.sheets.length;
  $("total-wins").textContent = records.participation.filter((r) => !r.registry).length;
  $("total-podiums").textContent = records.judging.length;
  const totalAppearances = records.participation.length;
  const bothSources = records.participation.filter((r) => r.sheet && r.registry).length;
  const sheetOnly = records.participation.filter((r) => r.sheet && !r.registry).length;
  const registryOnly = totalAppearances - bothSources - sheetOnly;
  const percent = (n) => `${Number(((n / totalAppearances) * 100).toFixed(1))}%`;
  $("detailed-percent").textContent = percent(bothSources + sheetOnly);
  $("registry-only-percent").textContent = percent(registryOnly);
  $("coverage-denominator").textContent =
    `Across all ${totalAppearances} recorded competition appearances, after grouping overlapping records. Judging is counted separately.`;
  const coverageParts = [
    [sheetOnly, "Detailed results only", "sheet-only"],
    [bothSources, "Both sources", "both"],
    [registryOnly, "Registry only", "registry-only"],
  ];
  $("coverage-bar").innerHTML = coverageParts
    .map(
      ([n, label, kind]) =>
        `<span class="coverage-segment ${kind}" style="width:${percent(n)}" title="${escape(label)}: ${n} appearances (${percent(n)})"></span>`,
    )
    .join("");
  $("coverage-bar").setAttribute(
    "aria-label",
    coverageParts.map(([n, label]) => `${label}: ${n} appearances, ${percent(n)}`).join("; "),
  );
  $("coverage-legend").innerHTML = coverageParts
    .map(
      ([n, label, kind]) =>
        `<div><span class="coverage-dot ${kind}"></span><span>${escape(label)}</span><strong>${percent(n)} <small>(${n})</small></strong></div>`,
    )
    .join("");
  $("coverage-overlap").textContent =
    `Detailed result data supports ${bothSources + sheetOnly} of ${totalAppearances} appearances: ${bothSources} also occur in the registry and ${sheetOnly} have no registry match in this extract. Registry records support ${bothSources + registryOnly} of ${totalAppearances} (${percent(bothSources + registryOnly)}); adding that percentage to the result-data percentage would count the overlap twice. These totals describe the recorded dataset, not coverage of every real-world appearance.`;
  $("year").insertAdjacentHTML(
    "beforeend",
    [...years]
      .reverse()
      .map((year) => `<option value="${year}">${year}</option>`)
      .join(""),
  );
  const maxYearCount = Math.max(
    ...years.map((year) => records.participation.filter((r) => r.date.startsWith(year)).length),
  );
  $("year-chart").innerHTML = years
    .map((year) => {
      const yearRows = records.participation.filter((r) => r.date.startsWith(year));
      const leaders = yearRows.filter((r) => !r.registry).length;
      const followers = yearRows.length - leaders;
      return `<button class="year-bar" type="button" data-year="${year}" aria-pressed="false" aria-label="${year}: ${yearRows.length} participation records, ${followers} registry-backed and ${leaders} sheet only" title="${year}: ${yearRows.length} results"><span class="bar-number" aria-hidden="true">${yearRows.length || "—"}</span><span class="bar-stack" aria-hidden="true" style="--height:${(yearRows.length / maxYearCount) * 102}px;--followers:${followers};--leaders:${leaders}">${followers ? '<span class="bar-follower"></span>' : ""}${leaders ? '<span class="bar-leader"></span>' : ""}${!yearRows.length ? '<span class="bar-zero"></span>' : ""}</span><span class="bar-year" aria-hidden="true">${year}</span></button>`;
    })
    .join("");
  const points = groupBy(records.registry, (r) => r.role);
  $("points-breakdown").innerHTML = [...points]
    .map(
      ([role, rows]) =>
        `<p class="point-role">${title(role)}</p>${[...groupBy(rows, (r) => r.division)].map(([division, results]) => `<div class="point-row"><span>${escape(title(division))}</span><strong>${results.reduce((sum, r) => sum + r.points, 0)}</strong></div>`).join("")}`,
    )
    .join("");
  const releaseDate = new Date(data.release.verified_at).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
  $("release-note").textContent =
    `Published ${releaseDate}. Dataset revision ${data.release.commit.slice(0, 12)}. Evidence cutoff: ${new Date(data.release.evidence_cutoff).toISOString().slice(0, 16).replace("T", " ")} UTC.`;
  $("release-link").href =
    `https://huggingface.co/datasets/skeswa/swingset/tree/${encodeURIComponent(data.release.commit)}`;
  function updateDivisions() {
    const current = $("division").value;
    const divisions = [...new Set(records[view].map((r) => r.division).filter(Boolean))].sort();
    $("division").innerHTML =
      '<option value="">All divisions</option>' +
      divisions.map((d) => `<option value="${escape(d)}">${escape(title(d))}</option>`).join("");
    $("division").value = divisions.includes(current) ? current : "";
  }
  function resultSummary(row) {
    if (view === "judging")
      return `<span class="event-meta">${row.roundCount} rounds with marks <span aria-hidden="true">·</span> Name match</span>`;
    const isSheet = view === "sheets" || (view === "participation" && row.sheet);
    const contest = isSheet
      ? ` <span aria-hidden="true">·</span> ${escape(title(row.raw.contest_type))}`
      : "";
    return `<span class="event-meta"><span class="division-chip ${escape(row.division)}">${escape(title(row.division))}</span><span class="${row.role === "leader" ? "role-leader" : ""}">${escape(title(row.role))}</span>${contest}${isSheet ? '<span aria-hidden="true">·</span> Name match' : ""}${view === "participation" ? '<span aria-hidden="true">·</span> ' + (row.registry ? (row.sheet ? "Registry + sheet" : "Registry") : "No registry match") : ""}</span>`;
  }
  function render() {
    const query = $("search").value.trim().toLocaleLowerCase();
    const year = $("year").value;
    const division = view === "judging" ? "" : $("division").value;
    const role = view === "judging" ? "" : $("role").value;
    const finish = view === "judging" ? "" : $("finish").value;
    visible = records[view]
      .filter((row) => {
        const haystack = [
          row.event,
          row.partner,
          title(row.division),
          row.role,
          row.raw.contest_name,
          row.raw.name_raw,
        ]
          .filter(Boolean)
          .join(" ")
          .toLocaleLowerCase();
        return (
          (!query || haystack.includes(query)) &&
          (!year || row.date.startsWith(year)) &&
          (!division || row.division === division) &&
          (!role || row.role === role) &&
          (!finish ||
            (finish === "sheet-only"
              ? view === "participation"
                ? !row.registry
                : view === "sheets" && !row.registryMatch
              : finish === "early-rounds"
                ? ["prelim", "semifinal", "quarterfinal"].includes(row.raw.best_round)
                : row.place !== null && row.place <= (finish === "wins" ? 1 : 3)))
        );
      })
      .sort(
        (a, b) =>
          ($("sort").value === "newest"
            ? b.date.localeCompare(a.date)
            : a.date.localeCompare(b.date)) ||
          a.event.localeCompare(b.event) ||
          a.division.localeCompare(b.division),
      );
    $("result-count").textContent =
      `${visible.length} of ${records[view].length} ${view === "judging" || view === "participation" ? "appearances" : "results"}${year ? ` · ${year}` : ""}`;
    $("reset").hidden = !(query || year || division || role || finish);
    $("download").disabled = !visible.length;
    for (const button of document.querySelectorAll("[data-year]"))
      button.setAttribute(
        "aria-pressed",
        String(view === "participation" && year === button.dataset.year),
      );
    if (!visible.length) {
      $("result-list").innerHTML =
        '<div class="empty"><h3>No records in this view.</h3><p>Try another year or a broader search. A gap here does not establish nonparticipation.</p><button type="button" class="text-link" id="empty-reset">Show all records ↗</button></div>';
      $("empty-reset").addEventListener("click", resetFilters);
      return;
    }
    const indexed = visible.map((row, index) => ({ ...row, index }));
    $("result-list").innerHTML = [...groupBy(indexed, (r) => r.date.slice(0, 4))]
      .map(
        ([year, rows]) =>
          `<section class="year-group" aria-label="${year} results"><div class="year-heading"><h3>${year}</h3><span>${rows.length} ${view === "judging" || view === "participation" ? "appearances" : "results"}</span></div>${rows.map((row) => `<details class="result" data-index="${row.index}"><summary><span class="month">${monthName(row.date)}</span><span><span class="event-title">${escape(row.event)}</span>${resultSummary(row)}</span><span class="result-value ${row.place === 1 ? "winner" : ""}">${view === "judging" ? `<strong>${row.marks.length}</strong><span>marks</span>` : `<strong>${escape(row.result)}</strong><span>${view === "registry" || (view === "participation" && !row.sheet) ? `${row.points} ${row.points === 1 ? "point" : "points"}` : row.place === null ? "best recorded" : "place"}</span>`}</span><span class="chevron" aria-hidden="true">+</span></summary><div class="result-content"></div></details>`).join("")}</section>`,
      )
      .join("");
  }
  function detailGrid(items) {
    return `<dl class="detail-grid">${items.map(([label, value]) => `<div class="detail-item"><dt>${escape(label)}</dt><dd>${value}</dd></div>`).join("")}</dl>`;
  }
  function table(headers, rows) {
    return `<div class="table-wrap" tabindex="0" role="region" aria-label="${escape(headers.join(", "))}"><table><thead><tr>${headers.map((h) => `<th scope="col">${escape(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((v) => `<td>${escape(v ?? "Not recorded")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }
  function renderJesTest() {
    if (!data.jes_test) return;
    const summary = data.jes_test.summary;
    const rows = data.jes_test.rows;
    const coverage = summary.coverage;
    const agreement = summary.agreement;
    $("jes-test").hidden = false;
    $("jes-test-release").textContent =
      summary.release.candidate_id || summary.release.commit || "Local dataset";
    $("jes-test-coverage").textContent = `${coverage.individual_results}/${summary.denominator}`;
    $("jes-test-coverage-percent").textContent =
      coverage.individual_results_percent === null
        ? "Not assessable"
        : `${coverage.individual_results_percent}%`;
    $("jes-test-uncovered").textContent =
      `${coverage.uncovered} uncovered · ${coverage.ambiguous} ambiguous · ${coverage.identity_conflicts} possible identity conflicts`;
    const parts = [
      [coverage.individual_results, "Covered", "both"],
      [coverage.uncovered, "Uncovered or unverified", "registry-only"],
      [coverage.ambiguous, "Ambiguous", "sheet-only"],
    ];
    $("jes-test-coverage-bar").innerHTML = parts
      .map(
        ([n, label, kind]) =>
          `<span class="coverage-segment ${kind}" style="width:${summary.denominator ? (n / summary.denominator) * 100 : 0}%" title="${escape(label)}: ${n}"></span>`,
      )
      .join("");
    $("jes-test-coverage-bar").setAttribute(
      "aria-label",
      parts.map(([n, label]) => `${label}: ${n}`).join("; "),
    );
    $("jes-test-coverage-legend").innerHTML = parts
      .map(
        ([n, label, kind]) =>
          `<div><span class="coverage-dot ${kind}"></span><span>${escape(label)}</span><strong>${n} <small>entries</small></strong></div>`,
      )
      .join("");
    $("jes-test-agreement").textContent =
      agreement.percent_agree_when_assessable === null
        ? "Not assessable"
        : `${agreement.percent_agree_when_assessable}%`;
    $("jes-test-agreement-count").textContent =
      `${agreement.agrees} agree · ${agreement.disagrees} disagree · ${agreement.insufficient_evidence} insufficient · ${agreement.conflicting_evidence} conflicting`;
    $("jes-test-agreement-note").textContent =
      `Assessable for ${agreement.assessable} of ${coverage.individual_results} covered entries. F confirms finalist status without asserting an exact place.`;
    const yearly = Object.entries(summary.by_year).map(([year, value]) => [
      year,
      `${value.covered}/${value.registry}`,
      value.final,
      value.judge_marks,
      value.registry ? `${((value.covered / value.registry) * 100).toFixed(1)}%` : "—",
    ]);
    $("jes-test-table").innerHTML = table(
      ["Year", "Covered / registry", "Final evidence", "Judge marks", "Coverage"],
      yearly,
    );
    const divisions = Object.entries(summary.by_division_and_role).map(([key, value]) => {
      const [division, role] = key.split("|");
      return [
        title(division),
        title(role),
        `${value.covered}/${value.registry}`,
        value.final,
        value.judge_marks,
      ];
    });
    $("jes-test-table").insertAdjacentHTML(
      "afterend",
      `<details class="jes-test-division-breakdown"><summary>Coverage by division and role</summary>${table(["Division", "Role", "Covered / registry", "Final evidence", "Judge marks"], divisions)}</details>`,
    );
    const uncovered = rows.filter((row) => !row.has_individual_result);
    $("jes-test-uncovered-heading").textContent =
      `Uncovered registry entries (${uncovered.length})`;
    $("jes-test-uncovered-table").innerHTML = table(
      ["Month", "Event", "Division", "Role", "Registry result", "Finding"],
      uncovered.map((row) => [
        row.event_month,
        row.series_name,
        title(row.division),
        title(row.role),
        row.registry_result === "F" ? "Finalist" : `Place ${row.registry_result}`,
        row.coverage_status === "ambiguous"
          ? "Multiple possible sheets"
          : row.coverage_status === "unverified"
            ? "Sheet evidence not verified"
            : "No corresponding individual result",
      ]),
    );
    const findings = rows.filter(
      (row) =>
        row.coverage_status === "ambiguous" ||
        row.coverage_status === "unverified" ||
        (row.has_individual_result && row.agreement !== "agrees"),
    );
    $("jes-test-review-table").innerHTML = findings.length
      ? table(
          ["Month", "Event", "Registry", "Individual result", "Assessment", "Entry"],
          findings.map((row) => [
            row.event_month,
            row.series_name,
            row.registry_result,
            row.evidence
              .map((e) => e.final_places.join(", ") || e.rounds_danced.join(", ") || "—")
              .join("; "),
            row.agreement,
            row.entry_ids.join(", "),
          ]),
        )
      : '<p class="detail-note">No disagreements or ambiguous matches in this report.</p>';
    const baseline = summary.baseline_comparison;
    if (baseline) {
      $("jes-test-baseline").hidden = false;
      $("jes-test-baseline").textContent =
        `Compared with ${baseline.baseline_candidate_id || "the earlier report"}: ${baseline.shared_registry_entries} shared entries; ${baseline.coverage_gained.length} gained and ${baseline.coverage_lost.length} lost coverage; ${baseline.final_evidence_gained.length} final evidence upgrades and ${baseline.final_evidence_lost.length} losses; ${baseline.new_disagreements.length} new disagreements; ${baseline.registry_claim_changes.length} registry claim changes; ${baseline.added_registry_entries.length} entries added and ${baseline.removed_registry_entries.length} removed.`;
    }
  }
  function sheetDetails(row) {
    const r = row.raw;
    const cb = callbacks.get(r.entry_id) || [];
    const marks = callbackMarks.get(r.entry_id) || [];
    const finals = finalMarks.get(r.placement_id) || [];
    let html = detailGrid([
      ["As printed", escape(r.name_raw)],
      ["Contest", escape(r.contest_name)],
      [r.role === "couple" ? "Printed couple" : "Partner", escape(row.partner || "Not recorded")],
      ["Identity link", `${escape(title(r.link_status))} · unconfirmed`],
      ["Recorded rounds", escape((r.rounds_danced || []).map(title).join(" → ") || "Not recorded")],
      ["Source", sourceLink(r.score_sheet_url || r.source_url, "Open source sheet")],
    ]);
    if (cb.length) {
      html +=
        '<h4 class="details-subheading">Round outcomes</h4>' +
        table(
          ["Round", "Outcome", "Score", "Yes / Alt / No"],
          cb.map((m) => [
            title(m.round_id.split("/").at(-1)),
            title(m.outcome),
            m.score_sum,
            `${m.yes_count ?? "—"} / ${m.alt_count ?? "—"} / ${m.no_count ?? "—"}`,
          ]),
        );
    }
    if (finals.length)
      html +=
        '<h4 class="details-subheading">Final judge rankings</h4>' +
        table(
          ["Judge", "Rank"],
          finals.map((m) => [m.judge_name || m.judge_id, m.rank]),
        );
    if (marks.length) {
      html +=
        '<details class="round-details"><summary>Individual callback marks (' +
        marks.length +
        ")</summary>";
      for (const [round, list] of groupBy(marks, (m) => m.round_id))
        html +=
          `<p class="round-label">${escape(title(round.split("/").at(-1)))}</p>` +
          table(
            ["Judge", "Mark", "Value"],
            list.map((m) => [
              m.judge_name || m.judge_id,
              m.mark_raw || title(m.mark),
              m.mark_value,
            ]),
          );
      html += "</details>";
    }
    if (!cb.length && !finals.length && !marks.length)
      html +=
        '<p class="detail-note">No individual scoring records are available for this entry.</p>';
    if (r.place === null)
      html +=
        '<p class="detail-note">The furthest recorded round does not establish elimination.</p>';
    return html;
  }
  function renderDetails(row) {
    if (view === "registry" || (view === "participation" && !row.sheet))
      return (
        detailGrid([
          ["Reported month", escape(prettyMonth(row.date))],
          ["Result", escape(row.result)],
          ["Division & role", escape(`${title(row.division)} · ${title(row.role)}`)],
          ["Registry points", escape(row.points)],
          ["Identity", "Confirmed · WSDC #7849"],
          ["Source", sourceLink(row.raw.source_url, "Open registry record")],
        ]) +
        '<p class="detail-note">The registry gives a month, not an exact competition date.' +
        (row.place === null ? " This finalist record does not specify an exact place." : "") +
        "</p>"
      );
    if (view === "sheets" || view === "participation") {
      const registry = view === "sheets" ? row.registryMatch : row.registry;
      const context = registry
        ? `<p class="detail-note">Corresponding registry record: ${escape(registry.result)}, ${registry.points} points, confirmed WSDC #7849. The sheet below is an unconfirmed name match; grouping does not change that identity status.</p>`
        : '<p class="detail-note">No corresponding registry result in this extract. Points are unknown; this participation is recorded by the score sheet.</p>';
      return context + sheetDetails(row);
    }
    let html = detailGrid([
      ["Printed name", escape(row.raw.name_raw)],
      ["Identity", "Unconfirmed name match"],
      ["Rounds with marks", escape(row.roundCount)],
      ["Recorded marks", escape(row.marks.length)],
    ]);
    html +=
      '<p class="detail-note">Each section shows the recorded scores from this judging appearance. Entry identifiers are preserved where an entrant name is unavailable in this extract.</p>';
    for (const [round, marks] of groupBy(row.marks, (m) => m.round_id)) {
      const parts = round.split("/");
      html += `<details class="round-details"><summary>${escape(title(parts.at(-2).replaceAll("-", " ")))} · ${escape(title(parts.at(-1)))} (${marks.length} marks)</summary>${table(
        ["Entry / placement", "Score type", "Mark"],
        marks.map((m) => [m.subject.split("/").at(-1), m.kind, m.value]),
      )}</details>`;
    }
    return html;
  }
  $("result-list").addEventListener(
    "toggle",
    (event) => {
      const target = event.target;
      if (!target.matches("details.result") || !target.open || target.dataset.loaded) return;
      target.querySelector(".result-content").innerHTML = renderDetails(
        visible[Number(target.dataset.index)],
      );
      target.dataset.loaded = "true";
    },
    true,
  );
  function resetFilters() {
    for (const id of ["search", "year", "division", "role", "finish"]) $(id).value = "";
    render();
  }
  function setView(next, clear = true) {
    view = next;
    for (const button of document.querySelectorAll("[data-view]"))
      button.setAttribute("aria-pressed", String(button.dataset.view === view));
    for (const id of ["division-label", "role-label", "finish-label"])
      $(id).hidden = view === "judging";
    $("view-note").textContent = notes[view];
    $("view-note").classList.toggle("name-match", view !== "registry");
    $("search").placeholder =
      view === "judging" ? "Search judging appearances…" : "Search events, divisions, partners…";
    updateDivisions();
    if (clear) resetFilters();
    else render();
  }
  for (const button of document.querySelectorAll("[data-view]"))
    button.addEventListener("click", () => setView(button.dataset.view));
  for (const id of ["year", "division", "role", "sort", "finish"])
    $(id).addEventListener("change", render);
  $("search").addEventListener("input", render);
  $("reset").addEventListener("click", resetFilters);
  $("show-sheets").addEventListener("click", () => {
    setView("sheets");
    $("explore").scrollIntoView({ block: "start" });
    document.querySelector('[data-view="sheets"]').focus({ preventScroll: true });
  });
  for (const button of document.querySelectorAll("[data-year]"))
    button.addEventListener("click", () => {
      setView("participation");
      $("year").value = button.dataset.year;
      render();
      $("explore").scrollIntoView({ block: "start" });
      $("year").focus({ preventScroll: true });
    });
  renderJesTest();
  const csvCell = (value) => {
    let text =
      value === null || value === undefined
        ? ""
        : typeof value === "object"
          ? JSON.stringify(value)
          : String(value);
    if (/^[=+@\-\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  $("download").addEventListener("click", () => {
    if (!visible.length) return;
    const exported = visible.map((row) => ({
      event_month: row.date,
      event: row.event,
      division: row.division,
      role: row.role,
      result: row.result ?? "",
      partner: row.partner,
      entry_id: row.raw.entry_id ?? "",
      best_round: row.raw.best_round ?? "",
      registry_points:
        view === "registry" || (view === "participation" && row.registry)
          ? row.points
          : view === "sheets"
            ? (row.registryMatch?.points ?? null)
            : null,
      identity_basis:
        view === "registry" || (view === "participation" && !row.sheet)
          ? "Confirmed WSDC 7849"
          : "Unconfirmed printed name match",
      registry_match:
        view === "participation"
          ? Boolean(row.registry)
          : view === "sheets"
            ? Boolean(row.registryMatch)
            : view === "registry",
      source_record: row.raw,
      corresponding_registry: row.registry?.raw ?? row.registryMatch?.raw ?? null,
      dataset_commit: data.release.commit,
    }));
    const keys = Object.keys(exported[0]);
    const csv =
      "\uFEFF" +
      [
        keys.map(csvCell).join(","),
        ...exported.map((r) => keys.map((key) => csvCell(r[key])).join(",")),
      ].join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `jesann-nail-${view}${$("year").value ? "-" + $("year").value : ""}.csv`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  setView("participation");
})();
