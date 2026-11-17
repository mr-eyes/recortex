"""An invented manuscript record for the tests of the mind page: octo-org/sample-project, a two-season field trial of a
winter rye cover crop and topsoil moisture. build() returns the fake repository's state, the project file and the
trusted line. Everything here is invented; people are roles, accounts are octocat and hubot.
"""

import datetime
import json

REPO = "octo-org/sample-project"
HUB_NUMBER = 1


def utc(at):
    """The UTC stamp GitHub would give a comment posted at the marker time `at`."""
    moment = datetime.datetime.fromisoformat(at)
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def zone(at):
    return "EST" if at.endswith("-05:00") else "EDT"


def heading_time(at):
    return at[:10] + " " + at[11:16] + " " + zone(at)


# ------------------------------------------------------------------ topic bodies

ROLE = ("This body is the manuscript text for the sections below: the current version only, ready to copy. The "
        "comments on this issue are its history (dated results, decisions, failures and corrections) and are never "
        "manuscript text.")

QUESTION = """# Why cover crops might keep topsoil moist

%s

## Question

Cover crops are grown between cash crops to protect the soil. Their effect on soil water is disputed: a living cover
draws water from the soil while it grows, but its residue shades the surface and slows evaporation once it is
terminated. Which of the two wins depends on rainfall, and most field trials report only season means, where the two
effects can cancel.

We ask whether a winter rye cover crop keeps the topsoil wetter during **dry spells**, the periods in which a crop
is most likely to suffer water stress. We measure volumetric water content $\\theta$ (m³/m³) every day in paired
plots over two growing seasons, and we compare the plots during dry spells as well as over the whole season.

Our hypothesis is that the residue effect dominates in dry spells, so that $\\Delta\\theta = \\theta_{cover} -
\\theta_{bare} > 0$ when it has not rained for several days, even if the season mean shows no difference.

## Sources

| Statement or number | Source |
|---|---|
| Plots and seasons | `data/plots.csv`, tag v0.4 |
""" % ROLE

DESIGN = """# Field design and sampling

%s

## Methods

### Plots

The trial ran on one field of silt loam in two growing seasons. The field was divided into 24 plots of 6 m by 12 m in
six blocks of four. In each block, two plots were sown with winter rye after harvest and two were left bare, at random.
The four plots in the low corner of the field were excluded from the moisture analysis because water stands on them
after heavy rain (see the calibration below), which leaves 20 of 24 plots, 10 with cover and 10 bare.

### Table: plots - Plots by block and treatment

Plots in the moisture analysis, by block and treatment, after the exclusion of the low corner.

| Block | Cover | Bare | Excluded |
|---|---|---|---|
| A | 2 | 2 | 0 |
| B | 2 | 2 | 0 |
| C | 2 | 2 | 0 |
| D | 2 | 2 | 0 |
| E | 2 | 2 | 0 |
| F | 0 | 0 | 4 |

### Sampling

Each plot carried one capacitance probe at three depths: **5 cm**, **15 cm** and **30 cm**. The probes logged every
hour and we used the daily mean at 06:00 local time. The steps for each probe were:

1. install the probe in a hand-augered hole and backfill with the original soil;
2. calibrate it against oven-dried cores taken beside it;
3. check the reading against a handheld probe once a month.

The probes were calibrated against 60 oven-dried cores (3 per plot in 20 plots); the root mean square error of the
calibrated readings was 0.027 m³/m³.

## Sources

| Statement or number | Source |
|---|---|
| 24 plots, 20 of 24 in the analysis | `data/plots.csv`, tag v0.4 |
| RMSE 0.027 m³/m³, 60 cores | `results/calibration.csv`, commit 7d41e0a |
| Table plots | data `data/plots.csv`; command `python scripts/plot_table.py`; tag v0.4 |
""" % ROLE

MODEL_METHODS_EXTRA = [
    "The model was fitted by restricted maximum likelihood. Time was measured in days from sowing and rescaled to "
    "weeks before fitting, which kept the correlation parameter away from the edge of its range and let the fit "
    "converge in every plot. We checked convergence by refitting from three different starting values and by "
    "comparing the log-likelihood of the three fits, which agreed to the fourth decimal in every case. The model "
    "includes rainfall on the day before the reading, because a reading taken the morning after a storm is high "
    "whatever the treatment, and leaving it out would make the residuals depend on the weather.",
    "The random intercept $u_p$ absorbs the differences between plots that do not change over the season: texture, "
    "slope, the depth of the topsoil and the position of the probe. We considered a random slope for rainfall as "
    "well, but its variance was estimated at zero in the first season and the fit with it did not converge in the "
    "second, so it was left out. Block enters as a fixed effect with five levels, because the blocks follow a slope "
    "across the field and we wanted their effect to be estimated, not assumed to be random.",
    "Readings from the three depths were modeled separately. The 5 cm reading is the one a germinating crop sees, "
    "and it is the one reported in the main text; the 15 cm and 30 cm readings are in the supplement. A joint model "
    "of the three depths with a correlation between depths was tried and gave the same treatment effects within "
    "their confidence intervals, at a much higher cost in fitting time, so we report the simpler separate models.",
    "Dry spells were identified from the field's own rain gauge, not from the regional weather station, because "
    "the station lies 14 km away and missed two local storms in the first season. A dry spell starts on the first "
    "day with less than 1 mm of rain after a day with more, and it counts as a dry spell when it lasts at least five "
    "days. Readings within dry spells were analysed with the same model, restricted to those days, with the day "
    "within the spell as an additional fixed effect.",
    "Confidence intervals for the treatment effect are Wald intervals from the model's standard errors. We compared "
    "them with intervals from a parametric bootstrap of 2,000 refits for the pooled model; the bootstrap intervals "
    "were wider by less than 0.001 m³/m³ at either end, so we report the Wald intervals. No adjustment for multiple "
    "comparisons was made for the three depths, because the 5 cm depth was named as the primary outcome before the "
    "second season began.",
    "Missing readings (a probe that stopped logging, or a reading outside the calibrated range) were left missing "
    "and not imputed. The model uses every reading that is present, and the correlation term handles the gaps "
    "because time is given explicitly. Across both seasons, 2.3 percent of probe-days were missing, and no plot "
    "lost more than 9 percent of its days.",
]
MODEL = """# A mixed model of daily soil moisture

%s

## Methods

### Model structure

We modeled the daily volumetric water content at 5 cm with a linear mixed model:

$$
\\theta_{pt} = \\beta_0 + \\beta_1\\,\\mathrm{cover}_p + \\beta_2\\,\\mathrm{rain}_{t-1} + \\beta_3\\,\\mathrm{block}_p + u_p + \\varepsilon_{pt}
$$

where $\\theta_{pt}$ is the water content of plot $p$ on day $t$, $u_p \\sim \\mathcal{N}(0, \\sigma_u^2)$ is a random
intercept per plot, and the residuals follow a first-order autoregressive process shared by all plots,
$\\varepsilon_{pt} = \\phi\\,\\varepsilon_{p,t-1} + \\eta_{pt}$ with $\\eta_{pt} \\sim \\mathcal{N}(0, \\sigma^2)$.
The treatment effect is $\\beta_1$, the difference in water content between cover and bare plots on the same day.

### Fitting

%s

## Results

With both seasons pooled, the cover crop raised the water content at 5 cm by 0.024 m³/m³ (95%% CI 0.013 to 0.035;
40 of 40 plot-seasons). The effect was larger in the first season than in the second.

<table>
  <thead>
    <tr><th rowspan="2">Season</th><th colspan="2">Effect of cover at 5 cm (m³/m³)</th><th rowspan="2">Plot-seasons</th></tr>
    <tr><th>Estimate</th><th>95%% CI</th></tr>
  </thead>
  <tbody>
    <tr><td>Season 1</td><td><b>0.031</b></td><td>0.018 to 0.044</td><td>20 of 20</td></tr>
    <tr><td>Season 2</td><td>0.019</td><td>0.006 to 0.032</td><td>20 of 20</td></tr>
    <tr><td>Pooled</td><td>0.024</td><td>0.013 to 0.035</td><td>40 of 40</td></tr>
  </tbody>
</table>

### Table: fit - Model fit by season

Fit statistics of the model above, by season, at 5 cm.

| Season | $\\hat\\phi$ | $\\hat\\sigma_u$ | Log-likelihood | Converged |
|---|---|---|---|---|
| Season 1 | 0.58 | 0.011 | 4,812 | 20 of 20 |
| Season 2 | 0.61 | 0.013 | 4,655 | 20 of 20 |

## Limitations

The model assumes the same autocorrelation in every plot. A model with one $\\phi$ per plot fitted better in the
first season but did not converge in the second, so the shared term is a compromise rather than a finding.

## Sources

| Statement or number | Source |
|---|---|
| 0.024 m³/m³ (0.013 to 0.035), 40 of 40 | `results/fit_pooled.csv`, commit 3f9c2ab |
| Season effects | `results/fit_by_season.csv`, commit 3f9c2ab |
| Table fit | data `results/fit_by_season.csv`; command `Rscript analysis/fit_model.R --season both`; commit 3f9c2ab |
""" % (ROLE, "\n\n".join(MODEL_METHODS_EXTRA))

MOISTURE = """# Cover crops keep the topsoil wetter in dry spells

%s

## Results

Over the whole season the difference between cover and bare plots was small (see the model). In dry spells it was
not. Across the 11 dry spells of the two seasons, the cover plots stayed above the wilting point of the soil on 9 of
11 spells, the bare plots on 4 of 11. The difference grew with the length of the spell, by about
$0.004\\,\\mathrm{m^3/m^3}$ per day without rain.

| Spell length | Spells | Cover above wilting point | Bare above wilting point |
|---|---|---|---|
| 5 to 7 days | 6 | 6 of 6 | 3 of 6 |
| 8 to 12 days | 4 | 3 of 4 | 1 of 4 |
| over 12 days | 1 | 0 of 1 | 0 of 1 |

### Figure: dryspell - Soil moisture through the longest dry spell

Daily water content at 5 cm in cover (green) and bare (brown) plots through the 14-day dry spell of the second
season, mean of 10 plots each, with the wilting point as a dashed line.

![Soil moisture through the longest dry spell](https://github.com/octo-org/sample-project/blob/v0.4/figures/dryspell.png?raw=true)

## Limitations

The wilting point was taken from the texture class of the field, not measured per plot. Soil texture was measured
in 12 of 20 plots only, so differences in texture between plots cannot be ruled out as a cause.

## Sources

| Statement or number | Source |
|---|---|
| 9 of 11 and 4 of 11 spells | `results/dry_spells.csv`, commit 3f9c2ab |
| 0.004 per day | `results/spell_slope.csv`, commit 3f9c2ab |
| Figure dryspell | data `results/dry_spells_daily.csv`; command `Rscript analysis/plot_dryspell.R`; tag v0.4 |
""" % ROLE

DISCUSSION = """# What the trial shows and what it does not

%s

## Limitations

The trial ran on one field of one soil type for two seasons. The effect we report is the effect of winter rye on a
silt loam; sandy soils hold less water, and the balance between transpiration by the living cover and shading by its
residue may differ there. The second season was wetter than the first, which may explain part of the smaller effect.

## Recommendations

A trial on a sandy soil, with texture measured in every plot, would show whether the dry-spell effect transfers. The
daily model we used needs at least one probe per plot logging every day; a weekly reading would miss most dry spells.

## Sources

| Statement or number | Source |
|---|---|
| Two seasons, one field | `data/plots.csv`, tag v0.4 |
""" % ROLE

SENSORS = ("This topic is not part of the manuscript: the low-cost probes drifted too much to be used, as the decision "
           "of 16 September records.")

TASK_REFIT = """## Goal

Refit the soil moisture model with both seasons and report the effect pooled and per season.

## Destination topic

#4: the result of this task is posted there as a dated comment when the task ends, and enters that topic's body only
once it is adopted.

## Done when

The pooled and per-season fits converge in every plot and their estimates are in `results/`.

## Plan

| Step | Owner | State |
|---|---|---|
| Pooled fit | Claude session | done |
| Per-season fit | Claude session | running |

## Result

Not finished. Opened 2026-10-09 10:00 EDT.
"""

TASK_REVIEWER = """## Goal

Answer reviewer 2, who asks whether soil texture rather than the cover crop explains the dry-spell difference.

## Destination topic

#5: the result of this task is posted there as a dated comment when the task ends.

## Done when

The response letter has a paragraph on texture backed by a number.

## Plan

| Step | Owner | State |
|---|---|---|
| Find what texture data exists | Claude session | done |
| Draft the answer | the first author | to do |

## Result

Not finished. Opened 2026-10-30 09:00 EDT.
"""

TASK_CALIBRATE = """## Goal

Calibrate the 24 capacitance probes against oven-dried cores.

## Destination topic

#3: the result of this task is posted there as a dated comment when the task ends.

## Done when

Every probe has a calibration line and an error against its cores.

## Plan

| Step | Owner | State |
|---|---|---|
| Take cores | the field technician | done |
| Fit calibration lines | Claude session | done |

## Result

Done: 20 of 24 probes calibrated; the four in the low corner were left out. See the result on #3.
"""

HUB = """# sample-project manuscript hub

## Deliverable text

The body of every issue with a section label is manuscript text; comments are history.

## Resume here

The current resume block is the newest "Resume block" comment on this issue.

## Project

A two-season field trial of winter rye as a cover crop and its effect on topsoil moisture. Times are Eastern, as
written.

## Deliverable sections

| Section | Issues |
|---|---|
| Question | #2 |
| Methods | #3, #4 |
| Results | #4, #5 |
| Limitations | #4, #5, #6 |
| Recommendations | #6 |
"""

# ------------------------------------------------------------------ memories

def fold_word(word, meaning, why, also=None, story=None, now=False):
    text = '\n<details>\n<summary>We %suse "%s" to mean %s</summary>\n\n**Why it matters here:** %s\n' % (
        "now " if now else "", word, meaning, why)
    if also:
        text += "\n**Also called:** %s\n" % also
    if story:
        text += "\n**The longer story:**\n\n%s\n" % story
    return text + "\n</details>\n"


def fold_thread(meaning, why):
    return "\n<details>\n<summary>This thread is about %s</summary>\n\n**Why we care:** %s\n\n</details>\n" % (
        meaning, why)


LABEL = {"result": "we found that", "decision": "we decided to", "failure": "what went wrong", "idea":
         "we had the idea to", "question": "we asked", "correction": "we corrected ourselves",
         "checkpoint": "we paused here"}


def memory(kind, at, title, plain, fields, links="", evidence=None, folds=""):
    marker = "<!-- ghim:event type=%s at=%s%s -->" % (kind, at, (" " + links) if links else "")
    out = [marker, "### %s: %s - %s" % (kind.capitalize(), heading_time(at), title), "",
           "**In plain words, %s:** %s" % (LABEL[kind], plain), ""]
    for name, value in fields:
        if name == "Exact words":
            out += ["**Exact words:**", "", "> " + value, ""]
        elif name == "Numbers":
            out += ["**Numbers:**", "", value, ""]
        else:
            out += ["**%s:** %s" % (name, value), ""]
    if evidence:
        out += ["**Evidence:**"] + ["- " + line for line in evidence] + [""]
    return "\n".join(out) + folds


def decision(at, title, plain, decision_text, by, words, where, effect, nxt, **kw):
    return memory("decision", at, title, plain, [("Decision", decision_text), ("Decided by", "%s, %s" % (
        by, heading_time(at))), ("Exact words", words), ("Where recorded", where), ("Effect", effect),
        ("Next step", nxt)], **kw)


def result(at, title, plain, ran, config, numbers, excl, cost, not_est, nxt, **kw):
    return memory("result", at, title, plain, [("What ran", ran), ("Configuration", config), ("Numbers", numbers),
                                               ("Exclusions and failures", excl), ("Cost", cost),
                                               ("Not established", not_est), ("Next step", nxt)], **kw)


def failure(at, title, plain, failed, ran, cause, impact, not_est, nxt, **kw):
    return memory("failure", at, title, plain, [("What failed", failed), ("What ran", ran), ("Cause", cause),
                                                ("Impact", impact), ("Not established", not_est),
                                                ("Next step", nxt)], **kw)


def idea(at, title, plain, why, take, nxt, **kw):
    return memory("idea", at, title, plain, [("Why it might help", why), ("What it would take", take),
                                             ("Next step", nxt)], **kw)


def question(at, title, plain, why, answer, nxt, **kw):
    return memory("question", at, title, plain, [("Why it matters", why), ("What would answer it", answer),
                                                 ("Next step", nxt)], **kw)


def checkpoint(at, title, plain, state, done, progress, nxt, open_q, **kw):
    return memory("checkpoint", at, title, plain, [("State", state), ("Done since the last checkpoint", done),
                                                   ("In progress", progress), ("Next actions", nxt),
                                                   ("Open questions", open_q)], **kw)


def correction(at, title, plain, corrects, was, is_, why, also, **kw):
    return memory("correction", at, title, plain, [("Corrects", corrects), ("Was", was), ("Is", is_), ("Why", why),
                                                   ("Also updated", also)], **kw)


def table(rows):
    head = "| Measure | Value | Denominator | Source path |\n|---|---|---|---|\n"
    return head + "\n".join("| %s | %s | %s | `%s` |" % row for row in rows)


LONG_CAUSE = (
    "The probes in the low corner sit in a shallow depression where water collects after any storm of more than "
    "20 mm. When the soil around a capacitance probe is saturated, a thin film of free water forms along the probe's "
    "prongs, and the probe reads that film as well as the soil, which pushes the reading above the soil's true water "
    "content. We confirmed this in three ways. First, the four probes read high only on the two to three days after "
    "a storm and agreed with their cores within the calibration error once the standing water was gone. Second, the "
    "field technician dug out one probe on the morning after the storm of 11 September and found the hole filled "
    "with water to 4 cm below the surface, while the neighboring plots in block E were dry at the same depth. Third, "
    "when we reinstalled that probe 20 cm higher on the slope, its readings followed the readings of block E within "
    "0.01 for the next week. Reinstalling all four probes was considered, but it would have moved them out of their "
    "plots, and the plots themselves still flood, so the plant-available water in them would still not be comparable "
    "with the rest of the field. The remaining guess, marked as a guess, is that the corner also has a higher clay "
    "content, which would make the flooding worse; texture was not measured there before the exclusion.")

MEMORIES = []


def add(cid, issue, body, user=None):
    MEMORIES.append((cid, issue, body, user))


# the question thread (#2)
add(9101, 2, decision(
    "2026-09-01T10:00-04:00", "frame the paper around dry spells",
    "frame the paper around what cover crops do in dry spells, not over the whole season",
    "The manuscript reports the effect of the cover crop in dry spells as its main outcome.",
    "the principal investigator",
    "Season means hide the one thing farmers ask about. Lead with the dry spells.",
    "Lab meeting, 2026-09-01.",
    "The Question section and the hypothesis are written around what happens in a dry spell.",
    "Draft the Question section, owner the first author",
    folds=fold_thread("the question the manuscript asks and how it is framed",
                      "The framing decides which numbers lead the abstract.")
    + fold_word("dry spell", "a run of at least five days with less than 1 mm of rain on each day, measured at "
                "the field's own gauge", "Every result in the paper is counted per dry spell.",
                also="spell")))

# the design thread (#3, #12)
add(9102, 3, idea(
    "2026-09-02T09:30-04:00", "three depths",
    "measure moisture at three depths in each plot instead of one",
    "A cover crop's roots take water from deeper than the residue protects, so one depth could miss half the story.",
    "Three probes per plot instead of one: 72 probes for 24 plots.",
    "Ask the principal investigator, owner the first author",
    folds=fold_thread("how the field plots are laid out and when, where and how deep moisture is measured",
                      "Every number in the paper comes from these plots and probes.")))
add(9103, 3, decision(
    "2026-09-03T14:00-04:00", "three depths, 5 cm primary",
    "measure at three depths and name the shallowest one the primary outcome",
    "Probes at 5, 15 and 30 cm in every plot; 5 cm is the primary outcome.",
    "the principal investigator",
    "Fine, three depths. But 5 cm is the one we report first, decide that now so nobody can say we picked it later.",
    "Email to the first author, 2026-09-03.",
    "The Methods name three depths and the 5 cm depth as primary.",
    "Order 72 probes, owner the field technician", links="because=9102"))
add(9104, 12, checkpoint(
    "2026-09-10T17:00-04:00", "calibration half done",
    "the probes are calibrated in most plots, and the low corner reads oddly after rain",
    "Calibration lines are fitted for 18 of 24 plots. The four probes in the low corner read high after the storm "
    "of 9 September.", "Cores taken in all 24 plots.", "Fitting the remaining 6 calibration lines.",
    "Finish the lines; look at the low corner, owner Claude session", "Whether the low corner can be used."))
add(9105, 12, failure(
    "2026-09-12T11:20-04:00", "low corner probes read high after rain",
    "the probes in the wet corner read far too high for days after each storm",
    "After the storm of 11 September the four probes in the low corner read 0.08 m³/m³ above their cores for "
    "three days.", "Calibration check against fresh cores, `scripts/calibrate.py --plots F1-F4`.", LONG_CAUSE,
    "4 of 24 plots give readings that cannot be compared with the rest after rain; this blocks #12.",
    "Whether the corner's soil differs in texture.", "Decide whether to drop the corner, owner the principal "
    "investigator",
    folds=fold_word("wet corner", "the four plots in the low northeast corner of the field, where water stands "
                    "after heavy rain", "Its probes read too high after rain, so its plots are left out.",
                    also="the low corner, block F",
                    story="The field slopes gently to the northeast. In the first season the corner flooded twice, "
                    "and the farmer who manages the field says it has always done so in wet autumns.")))
add(9106, 12, decision(
    "2026-09-14T09:00-04:00", "drop the wet corner",
    "leave the wet corner out of the moisture analysis and keep the other plots",
    "The four plots of block F are excluded from every moisture analysis; 20 of 24 plots remain.",
    "the principal investigator",
    "Drop them. Say so in the methods and say why. Do not try to correct the readings.",
    "Lab meeting, 2026-09-14.",
    "The analysis uses 20 of 24 plots, 10 cover and 10 bare; the Methods say why.",
    "Update the Methods and the plot table, owner the first author", links="because=9105",
    evidence=["table plots"]))
add(9107, 12, result(
    "2026-09-20T15:00-04:00", "calibration finished",
    "the calibrated probes agree closely with oven-dried cores in every plot we kept",
    "`python scripts/calibrate.py --all`, commit 7d41e0a.",
    "60 cores, 3 per plot, 20 plots.",
    table([("RMSE of calibrated readings", "0.021 m³/m³", "60 cores", "results/calibration.csv")]),
    "The 4 plots of the wet corner, as decided.", "Two days of the technician's time.",
    "Whether the calibration holds through the second season.", "Recheck in the spring, owner the field technician",
    links="because=9106"))
add(9108, 3, correction(
    "2026-10-02T10:30-04:00", "calibration error was larger",
    "the calibration error we reported was too small, because two cores were weighed before they were dry",
    "The calibration result of 20 September.", "RMSE 0.021 m³/m³ over 60 cores.",
    "RMSE 0.027 m³/m³ over 60 cores, `results/calibration.csv` at commit 7d41e0a.",
    "Two cores from plot C3 were weighed after 12 hours in the oven instead of 24; reweighed after 24 hours.",
    "The Methods of #3.", links="replaces=9107"))

# the sensors thread (#7, set aside)
add(9110, 7, idea(
    "2026-09-04T11:00-04:00", "low-cost sensor network",
    "build our own network of cheap moisture sensors to cover the whole field densely",
    "Ten times as many sensors for the price of the commercial probes would show moisture across the field, not "
    "only in plots.", "60 sensor kits, a logger and a week of building.", "Build 10 test units, owner Claude session",
    folds=fold_thread("whether a home-built network of cheap sensors could replace the commercial probes",
                      "It would have changed what the paper can say about the field as a whole.")))
add(9111, 7, failure(
    "2026-09-15T16:00-04:00", "cheap sensors drift",
    "the cheap sensors drifted so much within days that their readings were useless",
    "7 of 10 test units drifted by more than 0.05 m³/m³ within 10 days.", "Bench test in a soil column, 10 units.",
    "Not established; the leading guess is corrosion of the uncoated prongs.",
    "The sensor network cannot be used for the paper.", "Whether coated prongs would drift less.",
    "Decide, owner the principal investigator", links="checks=9110"))
add(9112, 7, decision(
    "2026-09-16T09:15-04:00", "commercial probes only",
    "use only the commercial probes and leave the sensor network out of the paper",
    "Only the commercial capacitance probes are used; the sensor network is not part of the manuscript.",
    "the principal investigator", "Not for this paper. Maybe a side project.", "Lab meeting, 2026-09-16.",
    "The topic leaves the manuscript.", "Close the topic, owner the first author",
    links="because=9111 instead=9110"))

# the model thread (#4, #8, #10)
add(9120, 4, idea(
    "2026-09-05T10:00-04:00", "a mixed model",
    "model daily moisture with a mixed model that has a random effect for each plot",
    "Plots differ in ways we did not measure; a random intercept absorbs that.",
    "A model in R and two days of work.", "Write the model, owner Claude session",
    folds=fold_thread("the statistical model that turns daily probe readings into the effect of the cover crop",
                      "Its estimate is the headline number of the paper.")))
add(9121, 4, question(
    "2026-09-06T09:00-04:00", "are days independent",
    "whether daily readings are so alike from one day to the next that a plain mixed model is wrong",
    "A model that treats correlated days as independent gives confidence intervals that are too narrow.",
    "The autocorrelation of the residuals at lag 1.", "Fit the plain model and look, owner Claude session",
    links="because=9120"))
add(9122, 4, result(
    "2026-09-18T14:00-04:00", "residuals are autocorrelated",
    "the days are strongly correlated, so the plain model's intervals are too narrow",
    "`Rscript analysis/fit_model.R --plain`, commit 51aa0c2.",
    "Season 1, 20 plots, 5 cm.",
    table([("Residual autocorrelation at lag 1", "0.62", "20 plots", "results/acf_plain.csv")]),
    "None", "Minutes.", "Whether one correlation fits every plot.", "Add a correlation term, owner Claude session",
    links="checks=9121"))
add(9123, 4, decision(
    "2026-09-19T10:00-04:00", "a lag term per plot",
    "add the lag term to the model, with its own strength in each plot",
    "The residuals follow a first-order autoregressive process, with one parameter per plot.",
    "the statistician", "Per-plot AR(1). The plots are too different for one shared phi.",
    "Video call with the statistician, 2026-09-19.", "The model in `analysis/fit_model.R` changes.",
    "Refit, owner Claude session", links="because=9122",
    evidence=["code analysis/fit_model.R#L40-L72 at 51aa0c2", "pull 10"],
    folds=fold_word("lag term", "the part of the model that lets each day's reading depend on the reading of the "
                    "day before in the same plot", "It is why our confidence intervals are honest.",
                    also="AR(1) term")))
add(9124, 4, failure(
    "2026-09-25T18:00-04:00", "the per-plot lag term does not converge",
    "the model with a lag term per plot would not finish fitting for several plots",
    "The fit did not converge for 6 of 20 plots; the optimizer stopped at the edge of the parameter range.",
    "`Rscript analysis/fit_model.R --ar per-plot`, commit 51aa0c2.",
    "Shown by the trace: phi ran to 0.999 in the 6 plots with the longest dry spells.",
    "No usable estimate from the per-plot model.", "Whether more data would fix it.",
    "Find a model that converges, owner Claude session", links="checks=9123"))
add(9125, 4, idea(
    "2026-09-26T09:00-04:00", "weekly means",
    "fit the model on weekly means instead of daily readings so that the lag term matters less",
    "Weekly means are far less correlated, and the model would converge.",
    "Change the input; an hour.", "Try it, owner Claude session", links="because=9124"))
add(9126, 4, decision(
    "2026-09-28T11:00-04:00", "keep daily data, rescale time",
    "keep the daily readings, rescale time to weeks and keep one lag term per plot",
    "Daily data stay; time is rescaled to weeks before fitting.",
    "the statistician",
    "Weekly means throw away the dry spells, which is the whole paper. Rescale time and try again.",
    "Email to the first author, 2026-09-28.", "The model input stays daily.", "Refit, owner Claude session",
    links="because=9124 instead=9125"))
add(9127, 4, result(
    "2026-10-03T16:00-04:00", "the model converges",
    "with time rescaled the model converges in every plot, and the cover crop raises moisture",
    "`Rscript analysis/fit_model.R --ar per-plot --weeks`, commit 8e02b7f.",
    "Season 1, 20 plots, 5 cm, the model $\\theta_{pt} = \\beta_0 + \\beta_1 \\mathrm{cover}_p + u_p + "
    "\\varepsilon_{pt}$ with AR(1) residuals per plot.",
    table([("Effect of cover", "0.031 m³/m³ (0.018 to 0.044)", "20 plots", "results/fit_season1.csv"),
           ("Plots converged", "20", "20 plots", "results/fit_season1.csv")]),
    "None", "Twenty minutes per fit.", "Whether the effect holds in the second season.",
    "Fit the second season, owner Claude session", links="checks=9126", evidence=["table fit"]))
add(9128, 8, checkpoint(
    "2026-10-10T17:30-04:00", "second season refit started",
    "the second season's data are in and the refit with both seasons has started",
    "Season 2 readings are cleaned. The per-plot model again fails to converge for 5 of 20 plots in season 2.",
    "Data cleaning of season 2.", "Trying a shared lag term.", "Compare shared and per-plot terms, owner Claude "
    "session", "Whether a shared lag term is acceptable to the statistician."))
add(9129, 8, decision(
    "2026-10-12T10:00-04:00", "one shared lag term",
    "use one lag term shared by all plots instead of one per plot",
    "The residuals follow one AR(1) process shared by all plots.", "the statistician",
    "Shared phi is fine. Say in the limitations that per-plot did not converge in season 2.",
    "Video call with the statistician, 2026-10-12.", "The Methods and the Limitations of #4 change.",
    "Refit both seasons, owner Claude session", links="replaces=9123",
    folds=fold_word("lag term", "the part of the model that lets each day's reading depend on the day before, "
                    "now with one strength shared by all plots", "The shared version is what the paper reports.",
                    also="AR(1) term", now=True)))
add(9130, 8, result(
    "2026-10-20T15:00-04:00", "both seasons pooled",
    "with both seasons the effect of the cover crop is smaller but still clearly above zero",
    "`Rscript analysis/fit_model.R --season both`, commit 3f9c2ab.",
    "Seasons 1 and 2, 20 plots each, 5 cm, one shared lag term; each plot-season counted once.",
    table([("Effect of cover, pooled", "0.024 m³/m³ (0.013 to 0.035)", "40 plot-seasons",
            "results/fit_pooled.csv"),
           ("Effect of cover, season 2", "0.019 m³/m³ (0.006 to 0.032)", "20 plots", "results/fit_by_season.csv")]),
    "None", "An hour.", "Why the second season's effect is smaller.", "Write the Results, owner the first author",
    links="replaces=9127", evidence=["table fit"],
    folds=fold_word("plot-season", "one plot in one season, the unit the pooled model counts",
                    "The pooled numbers are counted in plot-seasons, not plots.")))
add(9131, 4, question(
    "2026-10-22T09:30-04:00", "pooled or per season",
    "whether the abstract should give the pooled effect or the effect of each season",
    "The pooled number is smaller and safer; the first season's is the more striking.",
    "The principal investigator's choice, or a journal's guidance.", "Ask at the lab meeting, owner the first author",
    links="because=9130"))
add(9132, 8, checkpoint(
    "2026-11-12T16:45-05:00", "per-season fit running",
    "the pooled fit is in the text and the per-season fits are still running",
    "The pooled fit is in the body of #4. The per-season fits with the shared lag term are running.",
    "Pooled fit and its table.", "Per-season fits, about two hours left.",
    "Check the per-season fits; answer reviewer 2, owner Claude session", "Pooled or per season in the abstract."))

# the moisture thread (#5, #9)
add(9140, 5, result(
    "2026-10-21T14:00-04:00", "dry spells",
    "in dry spells the cover plots stayed moist far more often than the bare plots",
    "`Rscript analysis/dry_spells.R`, commit 3f9c2ab.", "11 dry spells over two seasons, 20 plots, 5 cm.",
    table([("Spells above wilting point, cover", "9", "11 spells", "results/dry_spells.csv"),
           ("Spells above wilting point, bare", "4", "11 spells", "results/dry_spells.csv")]),
    "None", "Minutes.", "Whether soil texture differs between the plots.", "Write the Results, owner the first "
    "author", links="because=9130", evidence=["figure dryspell"],
    folds=fold_thread("what the cover crop does to topsoil moisture during dry spells, the main result",
                      "It is the finding the title promises.")))
add(9141, 9, question(
    "2026-10-30T09:00-04:00", "texture",
    "whether differences in soil texture between plots, not the cover crop, explain the dry-spell result",
    "Reviewer 2 asks it directly; without an answer the main result is open to doubt.",
    "Clay fraction per plot as a covariate.", "Find the texture data, owner Claude session"))
add(9142, 9, idea(
    "2026-11-02T10:00-05:00", "clay as a covariate",
    "add the clay fraction of each plot to the model as a covariate",
    "If the effect survives the covariate, texture is not the explanation.",
    "Clay fraction for every plot.", "Check which plots have texture data, owner Claude session",
    links="because=9141"))
add(9143, 9, failure(
    "2026-11-05T15:30-05:00", "texture data incomplete",
    "texture was measured in too few plots to add it to the model without losing many plots",
    "Clay fraction exists for 12 of 20 plots; the other 8 were never sampled.",
    "A search of `data/` and the technician's notebooks.", "The samples were taken for a different project.",
    "The covariate model would drop 8 of 20 plots; this blocks #9.", "Whether the 8 plots can still be sampled.",
    "Ask the field technician, owner the first author", links="checks=9142"))
add(9144, 5, memory("decision", "2026-11-14T09:00-05:00", "an outsider's comment",
                    "say that texture is not the cause and move on",
                    [("Decision", "Texture is not the cause.")]), user="hubot")

# the discussion thread (#6)
add(9150, 6, idea(
    "2026-11-08T11:00-05:00", "compare with other trials",
    "compare our dry-spell effect with the other cover-crop trials that report daily moisture",
    "It would place the effect among other soils and climates.",
    "A literature search and a table; two days.", "None for now, until the reviewers have answered",
    folds=fold_thread("what the trial shows and does not show, and what should come next",
                      "Reviewers read this section to decide how far the result reaches.")))
add(9151, 6, question(
    "2026-11-09T10:00-05:00", "sandy soils",
    "whether the dry-spell effect would hold on a sandy soil",
    "It decides how far the Recommendations can go.", "A second trial on sand.",
    "Write it as a limitation for now, owner the first author"))

# loose memories (#11, a hand-opened question, and the hub)
add(9160, 11, decision(
    "2026-10-15T13:00-04:00", "a field-methods journal first",
    "send the manuscript to a field-methods journal before a general one",
    "The first submission goes to a field-methods journal.", "the principal investigator",
    "The model is the contribution. Send it where people care about the model.", "Lab meeting, 2026-10-15.",
    "The Methods stay long; the Introduction is short.", "Format for the journal, owner the first author"))
add(9161, 1, question(
    "2026-11-10T09:00-05:00", "preprint",
    "whether to post a preprint before the reviewers answer",
    "A preprint would let the field station cite the result this winter.", "The principal investigator's choice.",
    "Ask, owner the first author"))

RESUMES = [
    (9170, "2026-09-21T17:00-04:00", "Calibration is finished; the model is next. The wet corner is out.",
     [("Fit the plain mixed model", "Claude session", "#4"), ("Write the plot table", "the first author", "#3")],
     "none"),
    (9171, "2026-10-13T17:00-04:00", "The second season is in; the model has one shared lag term now.",
     [("Refit both seasons", "Claude session", "#8"), ("Draft the Results", "the first author", "#5")],
     "Do not change the primary outcome (#3) without the principal investigator."),
    (9172, "2026-11-13T17:30-05:00", "Answering the reviewers. The pooled model is in the text; the per-season fits "
     "and the texture question for reviewer 2 are open.",
     [("Check the per-season fits", "Claude session", "#8"), ("Ask the technician about the 8 unsampled plots",
                                                               "the first author", "#9"),
      ("Decide pooled or per season for the abstract", "the principal investigator", "#4")],
     "Do not send the response letter (#9) until the principal investigator has read it."),
]


def resume(at, focus, rows, holds):
    table_text = "| Action | Owner | Where |\n|---|---|---|\n" + "\n".join("| %s | %s | %s |" % row for row in rows)
    return ("### Resume block: %s\n\n<!-- ghim:resume:start -->\n**Last updated:** %s by the first author's Claude "
            "session\n**Current focus:** %s\n**Active issues:** #8, #9\n**Last decisions:** see the threads.\n"
            "**Next actions:**\n\n%s\n\n**Holds:** %s\n<!-- ghim:resume:end -->\n" % (
                heading_time(at), heading_time(at), focus, table_text, holds))


# ------------------------------------------------------------------ the state

def issue(title, body, labels, created, state="open", closed=None, parent=None, pull=False, user="octocat"):
    out = {"title": title, "body": body, "labels": labels, "state": state, "created_at": created,
           "updated_at": created, "user": user}
    if closed:
        out["closed_at"] = closed
    if parent:
        out["parent"] = parent
    if pull:
        out["pull_request"] = True
        out["merged_at"] = closed
    return out


ISSUES = {
    "1": issue("sample-project manuscript hub", HUB, ["hub"], "2026-08-31T14:00:00Z"),
    "2": issue("Question: cover crops and topsoil moisture", QUESTION, ["final-record", "rd:question",
                                                                       "topic:question"], "2026-08-31T14:05:00Z",
               parent=1),
    "3": issue("Methods: field design and sampling", DESIGN, ["final-record", "rd:methods", "topic:design"],
               "2026-08-31T14:06:00Z", parent=1),
    "4": issue("Methods and results: the moisture model", MODEL, ["final-record", "rd:methods", "rd:results",
                                                                 "rd:limitations", "topic:model"],
               "2026-08-31T14:07:00Z", parent=1),
    "5": issue("Results: dry spells", MOISTURE, ["final-record", "rd:results", "rd:limitations", "topic:moisture"],
               "2026-08-31T14:08:00Z", parent=1),
    "6": issue("Discussion: limits and next work", DISCUSSION, ["final-record", "rd:limitations", "rd:next",
                                                               "topic:discussion"], "2026-08-31T14:09:00Z", parent=1),
    "7": issue("Low-cost sensor network", SENSORS, ["topic:sensors"], "2026-08-31T14:10:00Z", state="closed",
               closed="2026-09-16T14:00:00Z", parent=1),
    "8": issue("Task: refit the model with the second season", TASK_REFIT, ["active", "topic:model"],
               "2026-10-09T14:00:00Z", parent=4),
    "9": issue("Task: answer reviewer 2 on soil texture", TASK_REVIEWER, ["active", "topic:moisture",
                                                                         "topic:design"], "2026-10-30T13:00:00Z",
               parent=5),
    "10": issue("Add the mixed model script", "Topic: #4", ["topic:model"], "2026-09-19T15:00:00Z",
                state="closed", closed="2026-09-20T12:00:00Z", pull=True),
    "11": issue("Which journal first?", "Should we aim for a field-methods journal or a general one?",
                ["type:question"], "2026-10-14T15:00:00Z"),
    "12": issue("Task: calibrate the probes", TASK_CALIBRATE, ["active", "topic:design"], "2026-09-08T13:00:00Z",
                state="closed", closed="2026-09-20T20:00:00Z", parent=3),
}

LABELS = sorted({label for item in ISSUES.values() for label in item["labels"]})


def build():
    """(state, config, trusted line): the fake repository, the project file and the trusted-repos line."""
    comments, latest = {}, {}
    issues = json.loads(json.dumps(ISSUES))
    for cid, number, text, user in MEMORIES:
        at = text.split(" at=", 1)[1].split(" ", 1)[0]
        created = utc(at)
        comments[str(cid)] = {"issue": number, "body": text, "created_at": created}
        if user:
            comments[str(cid)]["user"] = user
        latest[number] = max(latest.get(number, ""), created)
    for cid, at, focus, rows, holds in RESUMES:
        created = utc(at)
        comments[str(cid)] = {"issue": 1, "body": resume(at, focus, rows, holds), "created_at": created}
        latest[1] = max(latest.get(1, ""), created)
    for number, stamp in latest.items():
        issues[str(number)]["updated_at"] = max(issues[str(number)]["updated_at"], stamp)
    state = {"labels": [{"name": name, "color": "ededed", "description": ""} for name in LABELS],
             "issues": issues, "comments": comments, "next_issue": 13, "next_comment": 9200}
    return state, {"repo": REPO, "hub": HUB_NUMBER, "profiles": ["research"]}, "%s octocat\n" % REPO


# ------------------------------------------------------------------ the same record as a paper, with a plan (1.3.0)
# The record above is a report (the research profile). The paper is the same project under the manuscript profile,
# whose profile names a plan: the topics carry the manuscript's section labels, and the plan issue (#13) holds the
# plan comments. paper() returns (state, config, trusted) like build().

PAPER_LABELS = {"rd:question": "ms:introduction", "rd:methods": "ms:methods", "rd:results": "ms:results",
                "rd:limitations": "ms:limitations", "rd:next": "ms:discussion"}
PAPER_HEADINGS = {"## Question": "## Introduction", "## Recommendations": "## Discussion"}
PLAN_ISSUE = 13
ADOPTION = 9300
PLAN_LABEL = "plan"
PLAN_BODY = """# The plan of the paper

This body is a readable copy of the plan. The plan itself is the dated plan comments on this issue; where they differ, the comments are right.

Copy made: 2026-11-16 09:45 EST

## Message

None yet.

## Claims

None yet.

## Figures and tables

None yet.

## Discussion

None yet.

## Open gaps

None.
"""


def plan_memory(at, part, title, plain, section, topic, statement, needs, have, otherwise, nxt, ident=None, change=None,
                links=""):
    marker = "<!-- ghim:event type=plan at=%s%s -->" % (at, (" " + links) if links else "")
    out = [marker, "### Plan: %s - %s: %s" % (heading_time(at), part, title), "",
           "**In plain words, we plan to:** %s" % plain, "", "**Part:** %s" % part, "", "**Section:** %s" % section, "",
           "**Topic:** %s" % topic, ""]
    if ident:
        out += ["**Id:** %s" % ident, ""]
    out += ["**Statement:** %s" % statement, "", "**It needs:** %s" % needs, "", "**We have:** %s" % have, "",
            "**If it does not hold:** %s" % otherwise, ""]
    if change:
        out += ["**Change:** %s" % change, ""]
    out += ["**Next step:** %s" % nxt, ""]
    return "\n".join(out)


PLAN_MEMORIES = []


def add_plan(cid, body, user=None, issue=PLAN_ISSUE):
    PLAN_MEMORIES.append((cid, issue, body, user))


# the adoption decision and the parts, 16 November
add_plan(ADOPTION, decision(
    "2026-11-16T09:30-05:00", "adopt the plan", "adopt the plan for the paper as it was shown",
    "The plan for the paper is adopted.", "the first author", "Yes, adopt this plan.", "Chat, 2026-11-16.",
    "The session keeps the plan current from now on.", "Post the plan, owner Claude session"))
add_plan(9301, plan_memory(
    "2026-11-16T10:00-05:00", "message", "the one message", "tell one story: a rye cover keeps the topsoil wetter in dry "
    "spells", "All", "None yet",
    "A winter rye cover keeps the topsoil wetter in dry spells, in both seasons, though by less than the first season "
    "alone suggested.", "The dry-spell comparison and the pooled fit.", "The pooled fit and the dry-spell result.",
    "The paper reports what the pooled fit shows and drops the dry-spell framing.", "None for now, owner the first author",
    links="because=9300"))
add_plan(9302, plan_memory(
    "2026-11-16T10:10-05:00", "claim", "wetter topsoil in dry spells",
    "claim that a rye cover keeps the topsoil wetter during dry spells", "Results", "#5",
    "In dry spells the plots with a rye cover stayed above the wilting point on more spells than the bare plots.",
    "The dry-spell comparison in both seasons.", "The dry-spell result.", "The paper reports the season effect only.",
    "Answer the texture question, owner Claude session", links="because=9300,9140"))
add_plan(9303, plan_memory(
    "2026-11-16T10:15-05:00", "claim", "the pooled effect", "claim that the pooled effect of the cover is above zero",
    "Results", "#4", "Pooled over both seasons, the cover raises topsoil moisture.", "The pooled fit.",
    "The pooled fit.", "The paper reports each season alone.", "None for now, owner Claude session", links="because=9130"))
add_plan(9304, plan_memory(
    "2026-11-16T10:20-05:00", "claim", "texture does not explain it",
    "claim that soil texture does not explain the dry-spell effect", "Results", "#5",
    "Soil texture does not explain the dry-spell effect.", "The dry-spell comparison with texture as a covariate.",
    "Nothing yet.", "The paper says that texture cannot be ruled out.", "Find the texture data, owner the first author",
    links="because=9141"))
add_plan(9305, plan_memory(
    "2026-11-16T10:25-05:00", "figure", "the longest dry spell", "show the longest dry spell in one figure", "Results",
    "#5", "The cover plots stay above the wilting point through the longest dry spell.",
    "The daily readings of the longest spell.", "The daily readings.", "The figure moves to the supplement.",
    "None for now, owner Claude session", ident="dryspell", links="because=9302"))
add_plan(9306, plan_memory(
    "2026-11-16T10:30-05:00", "table", "dry spells by length", "tabulate the spells by length", "Results", "#5",
    "The number of spells above the wilting point, by length of the spell.", "The spell table.", "The spell counts.",
    "The table moves to the supplement.", "Make the table, owner Claude session", ident="spell-length",
    links="because=9302"))
add_plan(9307, plan_memory(
    "2026-11-16T10:35-05:00", "interpretation", "residue shading", "read the effect as shading by the residue",
    "Discussion", "#6", "The effect is most likely the shading of the surface by the residue.",
    "A comparison with plots whose residue was removed.", "Nothing yet.", "The paper names shading as one explanation "
    "among others.", "None for now, owner the first author", links="because=9302"))
add_plan(9308, plan_memory(
    "2026-11-16T10:36-05:00", "limitation", "one field", "state that the trial ran on one field", "Limitations", "#6",
    "The trial ran on one field of one soil type for two seasons.", "Nothing; it is a limit.", "The trial's design.",
    "None; it is stated.", "None for now, owner the first author", links="because=9143"))
add_plan(9309, plan_memory(
    "2026-11-16T10:37-05:00", "comparison", "other cover-crop trials", "compare the effect with other trials",
    "Discussion", "#6", "The effect is of the size other cover-crop trials report.", "The other trials' numbers.",
    "Nothing yet.", "The paper does not compare.", "Search the literature, owner the first author", links="because=9150"))
add_plan(9310, plan_memory(
    "2026-11-16T10:38-05:00", "implication", "sandy soils", "suggest a trial on sandy soil", "Discussion", "#6",
    "A trial on sandy soil would show how far the effect reaches.", "Nothing; it is a suggestion.",
    "The question about sandy soils.", "None.", "None for now, owner the first author", links="because=9151"))
add_plan(9311, plan_memory(
    "2026-11-16T10:40-05:00", "claim", "weekly means", "claim that weekly means would do as well as daily readings",
    "Results", "#4", "Weekly means give the same effect as daily readings.", "A fit on weekly means.", "Nothing yet.",
    "The paper uses daily readings.", "None for now, owner Claude session", links="because=9125"))
add_plan(9312, plan_memory(
    "2026-11-16T10:45-05:00", "claim", "the lag term", "claim that a lag term is needed", "Results", "#4",
    "The residuals are correlated from day to day, so the model needs a lag term.", "The residual autocorrelation.",
    "The autocorrelation of the plain model.", "The paper uses the plain model.", "None for now, owner Claude session",
    links="because=9122"))
# what happened afterwards
add_plan(9313, plan_memory(
    "2026-11-17T09:00-05:00", "claim", "texture, in the plots we can test",
    "claim that texture does not explain the dry-spell effect in the plots with texture data", "Results", "#5",
    "In the plots with texture data, texture does not explain the dry-spell effect.",
    "The covariate model on the plots with texture data.", "The texture data of some plots.",
    "The paper says that texture cannot be ruled out.", "Fit the covariate model, owner Claude session",
    change="narrows", links="replaces=9304 because=9143"))
add_plan(9314, failure(
    "2026-11-18T14:00-05:00", "the covariate model", "the covariate model on the plots with texture data would not fit",
    "The model with clay as a covariate did not converge.", "`Rscript analysis/clay.R`, commit 3f9c2ab.",
    "Too few plots with texture data.", "No answer to the texture question.", "Whether more plots can be sampled.",
    "Ask the technician, owner the first author", links="checks=9313"), issue=9)
add_plan(9315, decision(
    "2026-11-19T10:00-05:00", "drop the weekly claim", "drop the claim about weekly means",
    "The weekly-means claim is dropped from the plan.", "the first author", "Drop it, the dry spells are the paper.",
    "Chat, 2026-11-19.", "The part leaves the plan.", "Update the plan, owner Claude session", links="instead=9311"))
add_plan(9316, result(
    "2026-11-20T11:00-05:00", "a trimmed fit", "with the first week of each season left out the pooled effect is smaller",
    "`Rscript analysis/fit_model.R --season both --trim`, commit 3f9c2ab.",
    "Seasons 1 and 2, 20 plots each, the first week of each season left out.",
    table([("Effect of cover, pooled, trimmed", "0.009 m³/m³ (-0.004 to 0.022)", "40 plot-seasons",
            "results/fit_trim.csv")]),
    "None", "An hour.", "Whether the trimmed fit is the right one.", "Decide which fit to report, owner the first author",
    links="against=9303"), issue=4)
add_plan(9317, correction(
    "2026-11-21T09:00-05:00", "the autocorrelation", "we corrected the residual autocorrelation we reported",
    "Result 9122 on the model thread.", "0.62", "0.58", "The first value came from a run with the wrong season.",
    "None.", links="replaces=9122"), issue=4)


def text_memory(at, section, plain, changed, why, approved, kept="Nothing was changed by hand.",
                before="none", after="a" * 64, version="b" * 40, links=""):
    """A text comment (the kind of 1.3.0 that records a change of a topic body). `approved` is the field's whole text;
    before is "none" or 64 hex characters; version is 40 hex characters or the words after "Not kept: "."""
    marker = "<!-- ghim:event type=text at=%s%s -->" % (at, (" " + links) if links else "")
    hashes = "before sha256 `%s`; after sha256 `%s`" % (before, after)
    kept_version = "`%s`" % version if len(version) == 40 else "Not kept: %s" % version
    return "\n".join([marker, "### Text: %s - %s" % (heading_time(at), section), "",
                      "**In plain words, we changed the text to say that:** %s" % plain, "",
                      "**Section:** %s" % section, "", "**What changed:** %s" % changed, "", "**Why:** %s" % why, "",
                      "**Approved:** %s" % approved, "", "**Kept from the author:** %s" % kept, "",
                      "**Body:** %s" % hashes, "", "**Version:** %s" % kept_version, ""])


def proposal(at, section, changed, why, pull, base, text):
    return "\n".join(["<!-- ghim:event type=proposal at=%s -->" % at, "### Proposal: %s - %s" % (heading_time(at), section), "",
                      "**Section:** %s" % section, "", "**What would change:** %s" % changed, "", "**Why:** %s" % why, "",
                      "**Pull request:** #%d" % pull, "", "**Base:** sha256 `%s`" % base, "",
                      "**Text:** sha256 `%s`" % text, ""])


def paper(plan=True):
    """(state, config, trusted line) of the record as a paper; plan=False leaves the plan issue and its comments out."""
    state, config, trusted = build()
    for number, item in state["issues"].items():
        item["labels"] = [PAPER_LABELS.get(label, label) for label in item["labels"]]
        for old, new in PAPER_HEADINGS.items():
            item["body"] = item["body"].replace(old, new)
    state["date"] = "2026-12-01"  # what the fake creates is dated after the record, as GitHub's clock is
    names = sorted({label for item in state["issues"].values() for label in item["labels"]} | {PLAN_LABEL})
    state["labels"] = [{"name": name, "color": "ededed", "description": ""} for name in names]
    config = dict(config, profiles=["manuscript"])
    if plan:
        state["issues"][str(PLAN_ISSUE)] = issue("The plan of the paper", PLAN_BODY, [PLAN_LABEL],
                                                 "2026-11-16T14:30:00Z", parent=1)
        state["next_issue"] = PLAN_ISSUE + 1
        latest = {}
        for cid, number, text, user in PLAN_MEMORIES:
            at = text.split(" at=", 1)[1].split(" ", 1)[0]
            state["comments"][str(cid)] = {"issue": number, "body": text, "created_at": utc(at)}
            if user:
                state["comments"][str(cid)]["user"] = user
            latest[number] = max(latest.get(number, ""), utc(at))
        for number, stamp in latest.items():
            state["issues"][str(number)]["updated_at"] = max(state["issues"][str(number)]["updated_at"], stamp)
        config["plans"] = {"manuscript": PLAN_ISSUE}
    return state, config, trusted
